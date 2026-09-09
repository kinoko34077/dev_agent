"""Injected-transport Gemini adapter.

The transport may be backed by the Google SDK in a deployment, but the SDK
object is never returned to the Kernel. Offline tests use a plain callable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import os
from copy import deepcopy
import re
from threading import RLock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ...domain.protocol import ModelRequest, ModelResponse
from ..base import ModelProvider, ProviderError
from .decoder import decode_generate_content
from .transcript import GeminiTranscriptStore, function_call_count
from ..normalize import normalize_response


class GeminiProvider(ModelProvider):
    provider_id = "gemini"

    def __init__(self, transport: Callable[[dict[str, Any]], ModelResponse | Mapping[str, Any]], *, model: str = "gemini") -> None:
        self.transport = transport
        self.model = model

    def request(self, request: ModelRequest) -> ModelResponse:
        try:
            raw = self.transport(request.to_dict())
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"gemini transport failed: {exc}", category="transport", retryable=True) from exc
        return normalize_response(raw, provider=self.provider_id, default_model=self.model)


class GeminiHttpProvider(ModelProvider):
    """Minimal REST adapter for Gemini ``generateContent``."""

    provider_id = "gemini"

    def __init__(self, *, model: str, api_key: str | None = None, base_url: str = "https://generativelanguage.googleapis.com/v1beta", timeout_seconds: float = 30.0) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transcripts = GeminiTranscriptStore()
        self._transcript_lock = RLock()

    def _key(self) -> str:
        key = self.api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ProviderError("gemini authentication failed: GEMINI_API_KEY is not configured", category="authentication", retryable=False)
        return key

    @staticmethod
    def _safe_error_detail(error: HTTPError) -> str | None:
        """Extract a bounded, secret-scrubbed diagnostic from Google's error body."""

        try:
            raw = error.read(4096)
        except (AttributeError, KeyError, OSError, ValueError):
            return None
        if not raw:
            return None
        decoded = raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(decoded)
        except json.JSONDecodeError:
            payload = decoded
        if isinstance(payload, Mapping):
            nested = payload.get("error")
            values = nested if isinstance(nested, Mapping) else payload
            detail = ": ".join(
                str(values.get(key)).strip()
                for key in ("message", "status", "code")
                if isinstance(values.get(key), (str, int)) and str(values.get(key)).strip()
            )
        elif isinstance(payload, str):
            detail = payload.strip()
        else:
            detail = ""
        if not detail:
            return None
        detail = " ".join(detail.split())
        detail = re.sub(r"(?i)\b(api[_ -]?key|token|authorization|password|secret)\b\s*[:=]\s*[^\s,;}]+", r"\1=[REDACTED]", detail)
        detail = re.sub(r"(?i)\b(?:AIza|sk|gsk|sn)[-_][a-z0-9_-]+\b", "[REDACTED]", detail)
        return detail[:512]

    def _task_key(self, request: ModelRequest) -> str:
        return self._transcripts.key(request.task_id, request.request_id)

    @staticmethod
    def _function_response(result: Any) -> dict[str, Any]:
        response: dict[str, Any] = {
            "call_id": result.call_id,
            "provider_call_id": result.provider_call_id,
            "status": result.status.value,
            "result": result.structured_result,
            "error": result.error,
        }
        function_response: dict[str, Any] = {
            "name": result.tool_name or "unknown",
            "response": response,
        }
        if result.provider_call_id:
            # Gemini's REST contract uses this id to associate the response
            # with the exact functionCall part.
            function_response["id"] = result.provider_call_id
        return function_response

    def _contents(self, request: ModelRequest) -> list[dict[str, Any]]:
        task_key = self._task_key(request)
        transcript = self._transcripts.history(task_key=task_key)
        has_kernel_tool_turn = any(
            item.get("role") == "assistant" and item.get("tool_calls")
            for item in request.messages
        )
        if has_kernel_tool_turn and not transcript:
            raise ProviderError(
                "gemini provider transcript is unavailable for a tool-result turn",
                category="provider_context",
                retryable=False,
            )

        # Controller state contains a provider-neutral assistant tool-call
        # marker.  Replace those markers with the exact Gemini model parts
        # held by the adapter so thoughtSignature and part ordering survive.
        contents: list[dict[str, Any]] = []
        for item in request.messages:
            if item.get("role") == "assistant" and item.get("tool_calls"):
                continue
            contents.append({"role": "model" if item["role"] == "assistant" else "user", "parts": [{"text": item["content"]}]})

        if request.tool_results and transcript:
            self._transcripts.mark_replayed(task_key=task_key, parts=tuple(part for model_parts in transcript for part in model_parts))
            result_index = 0
            for model_parts in transcript:
                contents.append({"role": "model", "parts": deepcopy(list(model_parts))})
                expected = function_call_count(model_parts)
                if expected:
                    batch = request.tool_results[result_index : result_index + expected]
                    if len(batch) != expected:
                        raise ProviderError(
                            "gemini tool-result count does not match the preserved function-call turn",
                            category="provider_context",
                            retryable=False,
                        )
                    contents.append({"role": "user", "parts": [{"functionResponse": self._function_response(result)} for result in batch]})
                    result_index += expected
            if result_index != len(request.tool_results):
                raise ProviderError(
                    "gemini tool results contain an unmatched function response",
                    category="provider_context",
                    retryable=False,
                )
        elif request.tool_results:
            # Keep backwards-compatible construction for callers that provide
            # a raw FunctionResponse without a preceding model turn.  A real
            # Gemini 3 function-call continuation is rejected above when the
            # Controller's assistant tool marker is present.
            contents.append({"role": "user", "parts": [{"functionResponse": self._function_response(result)} for result in request.tool_results]})
        return contents

    def transcript_diagnostics(self, task_id: str) -> dict[str, int]:
        """Return count-only transcript evidence for an operator artifact."""

        return self._transcripts.diagnostics(task_key=task_id)

    def _thinking_config(self, request: ModelRequest) -> dict[str, Any] | None:
        metadata = request.metadata
        effort = metadata.get("thinking_effort")
        if effort is not None:
            if not isinstance(effort, str) or effort not in {"minimal", "low", "medium", "high"}:
                raise ProviderError("gemini thinking_effort is invalid", category="provider_context", retryable=False)
            if self.model.startswith("gemini-3"):
                return {"thinkingConfig": {"thinkingLevel": effort}}
            # Gemini 2.5 uses a numeric thinking budget, not thinkingLevel.
            budget = metadata.get("gemini_thinking_budget")
            if budget is None:
                return None
            if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
                raise ProviderError("gemini_thinking_budget must be a non-negative integer", category="provider_context", retryable=False)
            return {"thinkingConfig": {"thinkingBudget": budget}}
        budget = metadata.get("gemini_thinking_budget")
        if budget is None:
            return None
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            raise ProviderError("gemini_thinking_budget must be a non-negative integer", category="provider_context", retryable=False)
        return {"thinkingConfig": {"thinkingBudget": budget}}

    def _payload(self, request: ModelRequest) -> dict[str, Any]:
        payload = {"contents": self._contents(request), "generationConfig": {"maxOutputTokens": request.max_output_tokens}}
        thinking_config = self._thinking_config(request)
        if thinking_config is not None:
            payload["generationConfig"].update(thinking_config)
        if request.tool_definitions:
            payload["tools"] = [{"functionDeclarations": request.tool_definitions}]
        return payload

    def request(self, request: ModelRequest) -> ModelResponse:
        key = self._key()
        url = f"{self.base_url}/models/{quote(self.model, safe='')}:generateContent"
        body = json.dumps(self._payload(request), ensure_ascii=False).encode("utf-8")
        http_request = Request(url, data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key}, method="POST")
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            category = "rate_limit" if exc.code == 429 else "authentication" if exc.code == 401 else "authorization" if exc.code == 403 else "provider_http"
            detail = self._safe_error_detail(exc)
            suffix = f": {detail}" if detail else ""
            raise ProviderError(f"gemini {category}: HTTP {exc.code}{suffix}", category=category, retryable=category in {"rate_limit"}, http_status=exc.code) from exc
        except (URLError, OSError, json.JSONDecodeError) as exc:
            raise ProviderError(f"gemini transport failed: {exc}", category="transport", retryable=True) from exc
        response = decode_generate_content(raw, model=self.model, request_id=request.request_id)
        try:
            parts = tuple(
                deepcopy(part)
                for part in raw["candidates"][0]["content"]["parts"]
                if isinstance(part, Mapping)
            )
        except (KeyError, IndexError, TypeError):
            parts = ()
        if parts:
            with self._transcript_lock:
                self._transcripts.record(task_key=self._task_key(request), request_id=request.request_id, parts=parts)
        return response
