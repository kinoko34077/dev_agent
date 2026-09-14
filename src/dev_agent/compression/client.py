"""Strict HTTP client for an independent fixed compression service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from enum import Enum
import hashlib
import json
import os
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import urlparse

from .integrity import inspect_information_retention
from .protocol import (
    COMPRESSION_API_TOKEN_ENV,
    COMPRESSION_PROFILE,
    DEFAULT_COMPRESSION_ENDPOINT,
    DEFAULT_COMPRESSION_PROVIDER_CONTEXT_LIMIT_CHARS,
    DEFAULT_COMPRESSION_PUBLIC_MAX_INPUT_CHARS,
    DEFAULT_COMPRESSION_THRESHOLD_CHARS,
    CompressionResult,
    CompressionService,
)


class CompressionFailureCategory(str, Enum):
    """Bounded, non-secret classification for a compression call failure."""

    AUTHENTICATION = "authentication_failure"
    RATE_LIMITED = "rate_limited"
    PROVIDER_ERROR = "provider_error"
    TRANSPORT = "transport_failure"
    INVALID_RESPONSE = "invalid_response"
    HTTP_ERROR = "http_error"
    CONFIGURATION = "configuration_error"


class CompressionHttpError(RuntimeError):
    """A bounded compression transport/contract failure.

    The exception intentionally retains only a safe category and status code;
    response bodies and credentials never become diagnostic state.
    """

    def __init__(
        self,
        message: str,
        *,
        category: str = "compression_error",
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.http_status = http_status


def _text(value: Any, name: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompressionHttpError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise CompressionHttpError(f"{name} is too long")
    return value


def parse_compression_response(value: Mapping[str, Any], *, expected_input: str) -> CompressionResult:
    if not isinstance(value, Mapping):
        raise CompressionHttpError(
            "compression response must be an object",
            category=CompressionFailureCategory.INVALID_RESPONSE.value,
        )
    try:
        result = CompressionResult(
            compressed_text=_text(value.get("compressed_text"), "compressed_text", max_length=1_000_000),
            profile=_text(value.get("profile"), "profile"),
            prompt_version=_text(value.get("prompt_version"), "prompt_version"),
            model=_text(value.get("model"), "model"),
            input_chars=value.get("input_chars"),
            output_chars=value.get("output_chars"),
            input_sha256=_text(value.get("input_sha256"), "input_sha256", max_length=64),
            output_sha256=_text(value.get("output_sha256"), "output_sha256", max_length=64),
            warnings=tuple(value.get("warnings", ())),
        )
    except (TypeError, ValueError) as exc:
        raise CompressionHttpError(
            str(exc),
            category=CompressionFailureCategory.INVALID_RESPONSE.value,
        ) from exc
    expected_input_sha = hashlib.sha256(expected_input.encode("utf-8")).hexdigest()
    expected_output_sha = hashlib.sha256(result.compressed_text.encode("utf-8")).hexdigest()
    if result.input_sha256 != expected_input_sha:
        raise CompressionHttpError(
            "input_sha256 does not match request payload",
            category=CompressionFailureCategory.INVALID_RESPONSE.value,
        )
    if result.output_sha256 != expected_output_sha:
        raise CompressionHttpError(
            "output_sha256 does not match compressed_text",
            category=CompressionFailureCategory.INVALID_RESPONSE.value,
        )
    if result.input_chars != len(expected_input) or result.output_chars != len(result.compressed_text):
        raise CompressionHttpError(
            "compression character counts do not match payloads",
            category=CompressionFailureCategory.INVALID_RESPONSE.value,
        )
    if result.profile != COMPRESSION_PROFILE:
        raise CompressionHttpError(
            "unsupported compression profile",
            category=CompressionFailureCategory.INVALID_RESPONSE.value,
        )
    return result


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler)


def _open_no_redirect(request: Request, *, timeout: float):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _http_failure_category(status: int) -> str:
    if status in {401, 403}:
        return CompressionFailureCategory.AUTHENTICATION.value
    if status == 429:
        return CompressionFailureCategory.RATE_LIMITED.value
    if 500 <= status <= 599:
        return CompressionFailureCategory.PROVIDER_ERROR.value
    return CompressionFailureCategory.HTTP_ERROR.value


class HttpCompressionService(CompressionService):
    """Client for the fixed `/v1/compress` service contract.

    It accepts no caller-supplied prompt, provider options, tools, or model
    parameters. The service endpoint is an explicit composition setting.
    """

    def __init__(
        self,
        endpoint: str = DEFAULT_COMPRESSION_ENDPOINT,
        *,
        api_token: str | None = None,
        timeout_seconds: float = 30.0,
        max_input_chars: int = DEFAULT_COMPRESSION_PUBLIC_MAX_INPUT_CHARS,
        provider_context_limit_chars: int = DEFAULT_COMPRESSION_PROVIDER_CONTEXT_LIMIT_CHARS,
        max_response_bytes: int = 2_000_000,
        opener: Callable[..., Any] = _open_no_redirect,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("compression endpoint must be an absolute HTTP(S) URL without query or fragment")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if isinstance(max_input_chars, bool) or not isinstance(max_input_chars, int) or max_input_chars <= 0:
            raise ValueError("max_input_chars must be positive")
        if (
            isinstance(provider_context_limit_chars, bool)
            or not isinstance(provider_context_limit_chars, int)
            or provider_context_limit_chars <= 0
        ):
            raise ValueError("provider_context_limit_chars must be positive")
        if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int) or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if api_token is not None:
            if not isinstance(api_token, str) or not api_token.strip() or len(api_token) > 4096:
                raise ValueError("api_token must be a bounded non-empty string")
            api_token = api_token.strip()
            if any(ord(character) < 32 or ord(character) == 127 for character in api_token):
                raise ValueError("api_token must not contain control characters")
        self._endpoint = endpoint
        self._api_token = api_token
        self._timeout_seconds = float(timeout_seconds)
        self._max_input_chars = max_input_chars
        self._provider_context_limit_chars = provider_context_limit_chars
        self._max_response_bytes = max_response_bytes
        self._opener = opener

    @classmethod
    def from_environment(
        cls,
        *,
        endpoint: str = DEFAULT_COMPRESSION_ENDPOINT,
        timeout_seconds: float = 30.0,
        max_input_chars: int = DEFAULT_COMPRESSION_PUBLIC_MAX_INPUT_CHARS,
        provider_context_limit_chars: int = DEFAULT_COMPRESSION_PROVIDER_CONTEXT_LIMIT_CHARS,
        max_response_bytes: int = 2_000_000,
        opener: Callable[..., Any] = _open_no_redirect,
    ) -> "HttpCompressionService":
        """Build the explicitly configured client from one fixed secret name.

        Environment access happens only when this factory is called; importing
        the package never reads credentials or performs network I/O.
        """

        token = os.environ.get(COMPRESSION_API_TOKEN_ENV)
        if token is None or not token.strip():
            raise CompressionHttpError(
                "compression API token is not configured",
                category=CompressionFailureCategory.AUTHENTICATION.value,
            )
        return cls(
            endpoint,
            api_token=token,
            timeout_seconds=timeout_seconds,
            max_input_chars=max_input_chars,
            provider_context_limit_chars=provider_context_limit_chars,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )

    def compress(self, text: str, *, profile: str = COMPRESSION_PROFILE) -> CompressionResult:
        if not isinstance(text, str):
            raise TypeError("compression payload must be text")
        if not text:
            raise ValueError("compression payload must not be empty")
        if len(text) > self._max_input_chars:
            raise CompressionHttpError(
                "compression payload exceeds max_input_chars",
                category=CompressionFailureCategory.CONFIGURATION.value,
            )
        if len(text) > self._provider_context_limit_chars:
            raise CompressionHttpError(
                "compression payload exceeds provider_context_limit_chars",
                category=CompressionFailureCategory.CONFIGURATION.value,
            )
        if profile != COMPRESSION_PROFILE:
            raise CompressionHttpError(
                "unsupported compression profile",
                category=CompressionFailureCategory.CONFIGURATION.value,
            )
        body = json.dumps({"text": text, "profile": profile}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_token is not None:
            headers["Authorization"] = f"Bearer {self._api_token}"
        request = Request(self._endpoint, data=body, method="POST", headers=headers)
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                status = response.getcode() if callable(getattr(response, "getcode", None)) else getattr(response, "status", None)
                if isinstance(status, int) and not 200 <= status < 300:
                    raise CompressionHttpError(
                        "compression service returned an HTTP error",
                        category=_http_failure_category(status),
                        http_status=status,
                    )
                raw = response.read(self._max_response_bytes + 1)
        except HTTPError as exc:
            raise CompressionHttpError(
                "compression service returned an HTTP error",
                category=_http_failure_category(exc.code),
                http_status=exc.code,
            ) from None
        except CompressionHttpError:
            raise
        except (OSError, URLError) as exc:
            raise CompressionHttpError(
                "compression service request failed",
                category=CompressionFailureCategory.TRANSPORT.value,
            ) from exc
        if len(raw) > self._max_response_bytes:
            raise CompressionHttpError(
                "compression service response exceeds limit",
                category=CompressionFailureCategory.INVALID_RESPONSE.value,
            )
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompressionHttpError(
                "compression service returned invalid JSON",
                category=CompressionFailureCategory.INVALID_RESPONSE.value,
            ) from exc
        return parse_compression_response(value, expected_input=text)


def _payload_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class CompressionIntegrityError(CompressionHttpError):
    pass


def compress_handoff_payload(
    envelope,
    service: CompressionService | None,
    *,
    max_uncompressed_chars: int = DEFAULT_COMPRESSION_THRESHOLD_CHARS,
    strict_integrity: bool = False,
    fallback_to_original: bool = True,
):
    """Compress only a Handoff payload after a Control Plane threshold check."""

    from ..handoff import HandoffEnvelope, PayloadMode, validate_handoff

    validated = validate_handoff(envelope)
    if isinstance(max_uncompressed_chars, bool) or not isinstance(max_uncompressed_chars, int) or max_uncompressed_chars <= 0:
        raise ValueError("max_uncompressed_chars must be positive")
    if not isinstance(fallback_to_original, bool):
        raise TypeError("fallback_to_original must be a boolean")
    if validated.payload is None or validated.payload_mode != PayloadMode.ORIGINAL.value:
        return validated
    original = _payload_text(validated.payload)
    if len(original) <= max_uncompressed_chars:
        return validated
    if service is None:
        if not fallback_to_original:
            raise CompressionHttpError(
                "compression service unavailable",
                category=CompressionFailureCategory.CONFIGURATION.value,
            )
        return validated
    try:
        result = service.compress(original, profile=COMPRESSION_PROFILE)
    except CompressionHttpError as exc:
        if not fallback_to_original:
            raise
        metadata = dict(validated.metadata)
        metadata["compression"] = {
            "status": "fallback_original",
            "failure_category": exc.category,
            "http_status": exc.http_status,
        }
        return replace(validated, metadata=metadata)
    retention = inspect_information_retention(original, result.compressed_text)
    warnings = tuple(result.warnings) + tuple(f"{item.kind}:{item.value}" for item in retention)
    if strict_integrity and retention:
        raise CompressionIntegrityError("information retention check failed")
    metadata = dict(validated.metadata)
    if warnings:
        metadata["compression_warnings"] = list(warnings)
    metadata["compression_provenance"] = {
        "profile": result.profile,
        "prompt_version": result.prompt_version,
        "model": result.model,
        "input_chars": result.input_chars,
        "output_chars": result.output_chars,
        "input_sha256": result.input_sha256,
        "output_sha256": result.output_sha256,
        "warnings": list(warnings),
    }
    return HandoffEnvelope(
        handoff_id=validated.handoff_id,
        kind=validated.kind,
        subject=validated.subject,
        instruction=validated.instruction,
        source_role=validated.source_role,
        target_role=validated.target_role,
        conditions=validated.conditions,
        cautions=validated.cautions,
        requirements=validated.requirements,
        directive=validated.directive,
        payload=result.compressed_text,
        payload_mode=PayloadMode.COMPRESSED.value,
        payload_reference=validated.payload_reference,
        original_reference=validated.original_reference,
        original_sha256=result.input_sha256,
        compression_profile=result.profile,
        compression_prompt_version=result.prompt_version,
        compression_model=result.model,
        metadata=metadata,
    )


__all__ = [
    "CompressionHttpError",
    "CompressionIntegrityError",
    "CompressionFailureCategory",
    "HttpCompressionService",
    "compress_handoff_payload",
    "parse_compression_response",
]
