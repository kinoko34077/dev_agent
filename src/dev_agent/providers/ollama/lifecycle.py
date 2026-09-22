"""Bounded Ollama model inventory and lifecycle control."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import time
from typing import Any, Callable
from urllib.request import Request

from ...resources.provider_policy import validate_endpoint_authority
from ..openai_compatible.http import _read_bounded, urlopen_no_redirect


@dataclass(frozen=True)
class OllamaModelInfo:
    name: str
    size: int | None = None
    size_vram: int | None = None
    digest: str | None = None
    expires_at: str | None = None


class OllamaLifecycleError(RuntimeError):
    """A bounded model lifecycle operation failed."""

    def __init__(self, message: str, *, category: str) -> None:
        super().__init__(message)
        self.category = category


def normalize_keep_alive(value: str | int | float | None, *, default: str = "10m") -> str | int | float:
    candidate = default if value is None else value
    if isinstance(candidate, bool):
        raise ValueError("keep_alive must be a duration string or finite number")
    if isinstance(candidate, str):
        if not candidate.strip():
            raise ValueError("keep_alive must not be empty")
        return candidate.strip()
    if isinstance(candidate, (int, float)) and math.isfinite(float(candidate)):
        return candidate
    raise ValueError("keep_alive must be a duration string or finite number")


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_models(payload: object) -> tuple[OllamaModelInfo, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise OllamaLifecycleError("ollama lifecycle response has invalid model shape", category="provider_decode")
    models: list[OllamaModelInfo] = []
    for item in payload["models"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise OllamaLifecycleError("ollama lifecycle response has invalid model entry", category="provider_decode")
        models.append(
            OllamaModelInfo(
                name=item["name"].strip(),
                size=_optional_int(item.get("size")),
                size_vram=_optional_int(item.get("size_vram")),
                digest=_optional_text(item.get("digest")),
                expires_at=_optional_text(item.get("expires_at")),
            )
        )
    return tuple(models)


class OllamaModelManager:
    """Host-owned lifecycle manager for one loopback Ollama endpoint."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.25,
        think: bool | None = False,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        validate_endpoint_authority("ollama", base_url)
        if timeout_seconds <= 0 or not math.isfinite(float(timeout_seconds)):
            raise ValueError("timeout_seconds must be positive")
        if poll_interval_seconds < 0 or not math.isfinite(float(poll_interval_seconds)):
            raise ValueError("poll_interval_seconds must be non-negative")
        if think is not None and not isinstance(think, bool):
            raise ValueError("think must be a boolean or None")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.think = think
        self._clock = clock
        self._sleep = sleeper
        self._active_requests: dict[str, int] = {}

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> object:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        request = Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with urlopen_no_redirect(request, timeout=self.timeout_seconds) as response:
                return json.loads(_read_bounded(response).decode("utf-8"))
        except OllamaLifecycleError:
            raise
        except Exception as exc:
            raise OllamaLifecycleError("ollama lifecycle request failed", category="transport") from exc

    def _start_streaming_generation(self, model: str, keep_alive: str | int | float) -> None:
        """Start a bounded load without waiting for the full generation.

        Some thinking-capable local models materialize successfully but do not
        finish an empty non-streaming generation promptly.  Reading only the
        first bounded streaming chunk keeps lifecycle readiness separate from
        inference completion; /api/ps remains the source of truth for loaded
        state.
        """

        payload: dict[str, Any] = {
            "model": model,
            "prompt": "",
            "stream": True,
            "keep_alive": keep_alive,
            "options": {"num_predict": 1},
        }
        if self.think is not None:
            payload["think"] = self.think
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen_no_redirect(request, timeout=self.timeout_seconds) as response:
                line = response.readline(64 * 1024)
                if not line:
                    raise OllamaLifecycleError("ollama preload returned no response", category="load_failure")
                json.loads(line.decode("utf-8"))
        except OllamaLifecycleError:
            raise
        except Exception as exc:
            raise OllamaLifecycleError("ollama preload request failed", category="transport") from exc

    def list_installed(self) -> tuple[OllamaModelInfo, ...]:
        return _parse_models(self._request_json("GET", "/api/tags"))

    def list_loaded(self) -> tuple[OllamaModelInfo, ...]:
        return _parse_models(self._request_json("GET", "/api/ps"))

    def is_installed(self, model: str) -> bool:
        return any(item.name == model for item in self.list_installed())

    def is_loaded(self, model: str) -> bool:
        return any(item.name == model for item in self.list_loaded())

    def _wait_for_loaded(self, model: str, *, loaded: bool, deadline_seconds: float) -> None:
        if deadline_seconds <= 0 or not math.isfinite(float(deadline_seconds)):
            raise ValueError("deadline_seconds must be positive")
        deadline = self._clock() + deadline_seconds
        while True:
            if self.is_loaded(model) is loaded:
                return
            if self._clock() >= deadline:
                raise OllamaLifecycleError("ollama model lifecycle deadline exceeded", category="timeout")
            self._sleep(min(self.poll_interval_seconds, max(0.0, deadline - self._clock())))

    def ensure_loaded(self, model: str, *, keep_alive: str | int | float | None = None, deadline_seconds: float = 120.0) -> OllamaModelInfo:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        model = model.strip()
        keep_alive = normalize_keep_alive(keep_alive)
        if not self.is_installed(model):
            raise OllamaLifecycleError("ollama model is not installed", category="model_missing")
        loaded = next((item for item in self.list_loaded() if item.name == model), None)
        if loaded is None:
            self._start_streaming_generation(model, keep_alive)
            self._wait_for_loaded(model, loaded=True, deadline_seconds=deadline_seconds)
            loaded = next((item for item in self.list_loaded() if item.name == model), None)
        if loaded is None:
            raise OllamaLifecycleError("ollama model did not become loaded", category="load_failure")
        return loaded

    def acquire(self, model: str, *, keep_alive: str | int | float | None = None, deadline_seconds: float = 120.0) -> OllamaModelInfo:
        info = self.ensure_loaded(model, keep_alive=keep_alive, deadline_seconds=deadline_seconds)
        self._active_requests[model] = self._active_requests.get(model, 0) + 1
        return info

    def release(self, model: str) -> None:
        active = self._active_requests.get(model, 0)
        if active <= 0:
            raise OllamaLifecycleError("ollama request release has no active request", category="state")
        if active == 1:
            self._active_requests.pop(model, None)
        else:
            self._active_requests[model] = active - 1

    def active_requests(self, model: str) -> int:
        return self._active_requests.get(model, 0)

    def unload(self, model: str, *, deadline_seconds: float = 30.0) -> None:
        if self.active_requests(model) > 0:
            raise OllamaLifecycleError("cannot unload a model with active requests", category="active_requests")
        if not self.is_loaded(model):
            return
        self._request_json("POST", "/api/generate", {"model": model, "prompt": "", "stream": False, "keep_alive": 0})
        self._wait_for_loaded(model, loaded=False, deadline_seconds=deadline_seconds)

    def unload_idle(self, *, deadline_seconds: float = 30.0) -> tuple[str, ...]:
        unloaded: list[str] = []
        for item in self.list_loaded():
            if self.active_requests(item.name) == 0:
                self.unload(item.name, deadline_seconds=deadline_seconds)
                unloaded.append(item.name)
        return tuple(unloaded)


__all__ = ["OllamaLifecycleError", "OllamaModelInfo", "OllamaModelManager", "normalize_keep_alive"]
