"""Pure parsing and bounded metric projection for a DevFarm Worker result."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from scripts.devfarm_errors import DevFarmError
from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.providers.base import ModelProvider, transport_failure_metadata


FULL_WORKER_OUTPUT_MODE = "full_result"
MINIMAL_WORKER_OUTPUT_MODE = "minimal_file_replacement"
MINIMAL_WORKER_OUTPUT_KEYS = frozenset({"file_replacements", "notes"})


def worker_output_mode(
    output_contract: Mapping[str, Any],
    *,
    local_ollama: bool = False,
) -> str:
    """Resolve the bounded Worker output mode from Host-owned configuration.

    Existing manifests retain the historical full-result contract.  Ollama
    remains on the minimal contract by default, while another provider may
    opt into the same Host-generated replacement path explicitly.  Unknown
    modes fail closed before a provider request is made.
    """

    if not isinstance(output_contract, Mapping):
        raise DevFarmError("output_contract must be an object")
    configured = output_contract.get("mode", FULL_WORKER_OUTPUT_MODE)
    if not isinstance(configured, str) or configured not in {
        FULL_WORKER_OUTPUT_MODE,
        MINIMAL_WORKER_OUTPUT_MODE,
    }:
        raise DevFarmError("output_contract.mode must be full_result or minimal_file_replacement")
    if local_ollama:
        return MINIMAL_WORKER_OUTPUT_MODE
    return configured


def minimal_worker_output_schema() -> dict[str, Any]:
    """Return the strict provider schema for a Host-owned change proposal."""

    return {
        "type": "object",
        "properties": {
            "file_replacements": {
                "type": "object",
                "minProperties": 1,
                "additionalProperties": {
                    "oneOf": [
                        {"type": "string"},
                        {
                            "type": "array",
                            "items": {"type": "string", "pattern": "^[^\\r\\n]*$"},
                        },
                    ]
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["file_replacements"],
        "additionalProperties": False,
    }


def worker_proposal_preflight_failure(
    reason: str,
    *,
    mode: str,
) -> dict[str, Any]:
    """Project a deterministic Worker rejection without retaining raw output.

    This projection is deliberately advisory metadata.  It tells the
    existing refinement boundary that the model output needs bounded
    correction before provider reassignment is considered; it does not select
    a provider, retry a request, or alter Host Verification authority.
    """

    if not isinstance(reason, str) or not reason.strip():
        raise DevFarmError("preflight failure reason must be non-empty text")
    if mode not in {FULL_WORKER_OUTPUT_MODE, MINIMAL_WORKER_OUTPUT_MODE}:
        raise DevFarmError("preflight failure mode is unsupported")
    normalized = reason.casefold()
    if "unsupported fields" in normalized or "additional field" in normalized:
        error_code = "WORKER_OUTPUT_ADDITIONAL_FIELD"
    elif "json" in normalized or "json object" in normalized:
        error_code = "WORKER_OUTPUT_INVALID_JSON"
    elif "must be" in normalized or "required" in normalized:
        error_code = "WORKER_OUTPUT_SCHEMA"
    elif "outside manifest" in normalized or "outside" in normalized and "allowed" in normalized:
        error_code = "WORKER_OUTPUT_PATH_SCOPE"
    elif "newline" in normalized or "line" in normalized and "string" in normalized:
        error_code = "WORKER_OUTPUT_LINE_SHAPE"
    elif "secret" in normalized:
        error_code = "WORKER_OUTPUT_SECRET_REJECTED"
    elif "unified diff" in normalized or "patch" in normalized:
        error_code = "WORKER_OUTPUT_PATCH_SYNTAX"
    else:
        error_code = "WORKER_OUTPUT_DETERMINISTIC_REJECTED"
    return {
        "status": "rejected",
        "stage": "worker_proposal_preflight",
        "mode": mode,
        "failure_class": "FORMAT_PATCH",
        "error_code": error_code,
        "fallback_eligible": False,
        "fallback_disposition": "defer_to_bounded_correction",
        "validator_ref": "worker:proposal_preflight",
    }


def worker_proposal_preflight_success(
    *,
    mode: str,
    canonicalization_rules: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Project an accepted deterministic preflight and its audit facts."""

    if mode not in {FULL_WORKER_OUTPUT_MODE, MINIMAL_WORKER_OUTPUT_MODE}:
        raise DevFarmError("preflight success mode is unsupported")
    rules = list(dict.fromkeys(item for item in canonicalization_rules if isinstance(item, str)))
    return {
        "status": "accepted",
        "stage": "worker_proposal_preflight",
        "mode": mode,
        "fallback_eligible": False,
        "fallback_disposition": "not_applicable",
        "validator_ref": "worker:proposal_preflight",
        "canonicalization": {
            "applied": bool(rules),
            "rules": rules,
            "source": "host",
            "preserves_source_content": True,
        },
    }


_MODEL_STATUS_ALIASES = {
    "success": "completed",
    "complete": "completed",
    "done": "completed",
    "ok": "completed",
    "error": "failed",
    "failure": "failed",
    "blocked": "blocked_external",
}
_USAGE_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "candidatesTokenCount",
        "promptTokenCount",
        "thoughtsTokenCount",
        "cost",
        "cost_usd",
        "cost_minor",
        "native_units",
        "unit",
        "quota_remaining",
        "quota_reset_at",
        "remaining",
        "reset_at",
        "latency_ms",
        "failure_count",
        "refinement_round",
        "reasoning_effort",
    }
)
_QUOTA_OBSERVATION_KEYS = frozenset(
    {
        "unit",
        "remaining",
        "reset_at",
        "observed_at",
        "source",
        "authority",
        "confidence",
        "stale_after_seconds",
        "estimated",
        "consumed",
    }
)
_HOST_FAILURE_CATEGORIES = frozenset(
    {
        "host_configuration",
        "host_runtime_failure",
        "provider_http",
        "provider_decode",
        "transport",
        "reconciliation_required",
    }
)


def safe_host_failure_metadata(error: BaseException) -> dict[str, str | int]:
    """Project only bounded child-runtime diagnostics into Worker metrics."""

    metadata: dict[str, str] = {}
    category = getattr(error, "host_failure_category", None)
    if isinstance(category, str) and category in _HOST_FAILURE_CATEGORIES:
        metadata["host_failure_category"] = category
    diagnostic_type = getattr(error, "host_failure_type", None)
    if (
        isinstance(diagnostic_type, str)
        and 0 < len(diagnostic_type) <= 64
        and all(part.isidentifier() for part in diagnostic_type.split("."))
    ):
        metadata["host_failure_type"] = diagnostic_type
    metadata.update(transport_failure_metadata(error))
    return metadata


def extract_json_object(text: str) -> Mapping[str, Any]:
    """Decode one bounded JSON object, tolerating a single Markdown fence."""

    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise DevFarmError("worker response did not contain a JSON object")
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise DevFarmError(f"worker response JSON is invalid: {exc}") from exc
    if not isinstance(value, Mapping):
        raise DevFarmError("worker response must be a JSON object")
    return value


def normalize_model_status(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DevFarmError("worker status must be a non-empty string")
    normalized = value.strip().lower()
    return _MODEL_STATUS_ALIASES.get(normalized, normalized)


def _safe_metric_value(value: Any) -> int | float | str | bool | None:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:512]
    return None


def safe_usage(usage: Any) -> dict[str, Any]:
    """Keep only provider-neutral usage/quota scalars in Worker artifacts."""

    if not isinstance(usage, Mapping):
        return {}
    normalized: dict[str, Any] = {}
    for key, value in usage.items():
        if key in _USAGE_KEYS:
            safe = _safe_metric_value(value)
            if safe is not None:
                normalized[str(key)] = safe
        elif key == "quota_observation" and isinstance(value, Mapping):
            observation: dict[str, Any] = {}
            for nested_key, nested_value in value.items():
                if nested_key not in _QUOTA_OBSERVATION_KEYS:
                    continue
                safe = _safe_metric_value(nested_value)
                if safe is not None:
                    observation[str(nested_key)] = safe
            if observation:
                normalized["quota_observation"] = observation
    return normalized


def build_worker_metrics(
    provider: ModelProvider,
    request: ModelRequest,
    *,
    response: Any = None,
    elapsed_ms: int = 0,
    task_type: str = "unspecified",
) -> dict[str, Any]:
    """Project bounded provider identity and usage without raw output."""

    provider_id = getattr(provider, "provider_id", None)
    if not isinstance(provider_id, str) or not provider_id.strip():
        provider_id = getattr(response, "provider", None)
    provider_id = provider_id.strip() if isinstance(provider_id, str) and provider_id.strip() else "unknown"

    binding = getattr(provider, "provider_binding_id", None) or provider_id
    binding = binding.strip() if isinstance(binding, str) and binding.strip() else provider_id
    model = getattr(provider, "model_id", None) or getattr(provider, "model", None) or getattr(response, "model", None)
    model = model.strip() if isinstance(model, str) and model.strip() else "unknown"
    tier = getattr(provider, "intelligence_tier", None)
    tier = getattr(tier, "value", tier)
    if not isinstance(tier, str) or not tier.strip():
        tier = None
    return {
        "provider_id": provider_id,
        "provider_binding_id": binding,
        "model_id": model,
        "intelligence_tier": tier.strip() if isinstance(tier, str) else None,
        "task_type": task_type,
        "request_id": request.request_id,
        "elapsed_ms": max(0, int(elapsed_ms)),
        "usage": safe_usage(getattr(response, "usage", {})),
        "attempt_count": 1,
        "host_verified": False,
        "host_verified_test_count": 0,
        "host_tests_passed": None,
        "result_accepted": None,
        "codex_correction_chars": None,
    }


__all__ = [
    "FULL_WORKER_OUTPUT_MODE",
    "MINIMAL_WORKER_OUTPUT_KEYS",
    "MINIMAL_WORKER_OUTPUT_MODE",
    "build_worker_metrics",
    "extract_json_object",
    "minimal_worker_output_schema",
    "normalize_model_status",
    "safe_host_failure_metadata",
    "safe_usage",
    "worker_output_mode",
    "worker_proposal_preflight_failure",
    "worker_proposal_preflight_success",
]
