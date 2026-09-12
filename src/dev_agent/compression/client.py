"""Strict HTTP client for an independent fixed compression service."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from .integrity import inspect_information_retention
from .protocol import COMPRESSION_PROFILE, CompressionResult, CompressionService


class CompressionHttpError(RuntimeError):
    pass


def _text(value: Any, name: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompressionHttpError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise CompressionHttpError(f"{name} is too long")
    return value


def parse_compression_response(value: Mapping[str, Any], *, expected_input: str) -> CompressionResult:
    if not isinstance(value, Mapping):
        raise CompressionHttpError("compression response must be an object")
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
        raise CompressionHttpError(str(exc)) from exc
    expected_input_sha = hashlib.sha256(expected_input.encode("utf-8")).hexdigest()
    expected_output_sha = hashlib.sha256(result.compressed_text.encode("utf-8")).hexdigest()
    if result.input_sha256 != expected_input_sha:
        raise CompressionHttpError("input_sha256 does not match request payload")
    if result.output_sha256 != expected_output_sha:
        raise CompressionHttpError("output_sha256 does not match compressed_text")
    if result.input_chars != len(expected_input) or result.output_chars != len(result.compressed_text):
        raise CompressionHttpError("compression character counts do not match payloads")
    if result.profile != COMPRESSION_PROFILE:
        raise CompressionHttpError("unsupported compression profile")
    return result


class HttpCompressionService(CompressionService):
    """Client for the fixed `/v1/compress` service contract.

    It accepts no caller-supplied prompt, provider options, tools, or model
    parameters. The service endpoint is an explicit composition setting.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 30.0,
        max_input_chars: int = 1_000_000,
        max_response_bytes: int = 2_000_000,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("compression endpoint must be an absolute HTTP(S) URL without query or fragment")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if isinstance(max_input_chars, bool) or not isinstance(max_input_chars, int) or max_input_chars <= 0:
            raise ValueError("max_input_chars must be positive")
        if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int) or max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._endpoint = endpoint
        self._timeout_seconds = float(timeout_seconds)
        self._max_input_chars = max_input_chars
        self._max_response_bytes = max_response_bytes
        self._opener = opener

    def compress(self, text: str, *, profile: str = COMPRESSION_PROFILE) -> CompressionResult:
        if not isinstance(text, str):
            raise TypeError("compression payload must be text")
        if not text:
            raise ValueError("compression payload must not be empty")
        if len(text) > self._max_input_chars:
            raise CompressionHttpError("compression payload exceeds max_input_chars")
        if profile != COMPRESSION_PROFILE:
            raise CompressionHttpError("unsupported compression profile")
        body = json.dumps({"text": text, "profile": profile}, ensure_ascii=False).encode("utf-8")
        request = Request(self._endpoint, data=body, method="POST", headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                raw = response.read(self._max_response_bytes + 1)
        except (OSError, URLError) as exc:
            raise CompressionHttpError("compression service request failed") from exc
        if len(raw) > self._max_response_bytes:
            raise CompressionHttpError("compression service response exceeds limit")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompressionHttpError("compression service returned invalid JSON") from exc
        return parse_compression_response(value, expected_input=text)


def _payload_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class CompressionIntegrityError(CompressionHttpError):
    pass


def compress_handoff_payload(
    envelope,
    service: CompressionService,
    *,
    max_uncompressed_chars: int,
    strict_integrity: bool = False,
):
    """Compress only a Handoff payload after a Control Plane threshold check."""

    from ..handoff import HandoffEnvelope, PayloadMode, validate_handoff

    validated = validate_handoff(envelope)
    if isinstance(max_uncompressed_chars, bool) or not isinstance(max_uncompressed_chars, int) or max_uncompressed_chars <= 0:
        raise ValueError("max_uncompressed_chars must be positive")
    if validated.payload is None:
        return validated
    original = _payload_text(validated.payload)
    if len(original) <= max_uncompressed_chars:
        return validated
    result = service.compress(original)
    retention = inspect_information_retention(original, result.compressed_text)
    warnings = tuple(result.warnings) + tuple(f"{item.kind}:{item.value}" for item in retention)
    if strict_integrity and retention:
        raise CompressionIntegrityError("information retention check failed")
    metadata = dict(validated.metadata)
    if warnings:
        metadata["compression_warnings"] = list(warnings)
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
    "HttpCompressionService",
    "compress_handoff_payload",
    "parse_compression_response",
]
