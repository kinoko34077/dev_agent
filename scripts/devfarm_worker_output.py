"""Pure parsing and bounded metric projection for a DevFarm Worker result."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from scripts.devfarm_contracts import (
    FULL_WORKER_OUTPUT_MODE,
    MINIMAL_WORKER_OUTPUT_MODE,
    LOCALIZED_FILE_EDIT_MODE,
    WORKER_OUTPUT_MODES,
)
from scripts.devfarm_errors import DevFarmError
from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.providers.base import ModelProvider, transport_failure_metadata


MINIMAL_WORKER_OUTPUT_KEYS = frozenset({"file_replacements"})
LOCALIZED_WORKER_OUTPUT_KEYS = frozenset({"file_edits"})


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
    if not isinstance(configured, str) or configured not in WORKER_OUTPUT_MODES:
        raise DevFarmError(
            f"output_contract.mode must be one of: {', '.join(sorted(WORKER_OUTPUT_MODES))}"
        )
    if local_ollama and "mode" not in output_contract:
        return MINIMAL_WORKER_OUTPUT_MODE
    return configured


def minimal_worker_output_schema(
    *,
    allowed_paths: Sequence[str] | None = None,
    line_array_only: bool = False,
) -> dict[str, Any]:
    """Return the strict provider schema for a Host-owned change proposal.

    When the Host knows the outbound paths, bind the replacement object to
    those exact keys.  This prevents a structured-output provider from
    treating unrelated metadata as a source path before the existing
    Host path validator gets a chance to reject it.
    """

    line_array_schema = {
        "type": "array",
        "items": {"type": "string", "pattern": "^[^\\r\\n]*$"},
    }
    replacement_value = line_array_schema if line_array_only else {
        "oneOf": [
            {"type": "string"},
            line_array_schema,
        ]
    }
    if allowed_paths is None:
        replacement_object: dict[str, Any] = {
            "type": "object",
            "minProperties": 1,
            "additionalProperties": replacement_value,
        }
    else:
        if isinstance(allowed_paths, (str, bytes)):
            raise DevFarmError("allowed_paths must be a sequence of paths")
        normalized_paths = tuple(
            dict.fromkeys(
                path.replace("\\", "/").strip()
                for path in allowed_paths
                if isinstance(path, str) and path.strip()
            )
        )
        if not normalized_paths:
            raise DevFarmError("allowed_paths must contain at least one path")
        replacement_object = {
            "type": "object",
            "minProperties": 1,
            "maxProperties": 1,
            "properties": {path: replacement_value for path in normalized_paths},
            "additionalProperties": False,
        }

    return {
        "type": "object",
        "properties": {
            "file_replacements": replacement_object,
        },
        "required": ["file_replacements"],
        "additionalProperties": False,
    }


def localized_worker_output_schema(*, allowed_paths: Sequence[str]) -> dict[str, Any]:
    """Return a strict, path-bound schema for line-anchored Host edits."""

    if isinstance(allowed_paths, (str, bytes)):
        raise DevFarmError("allowed_paths must be a sequence of paths")
    normalized_paths = tuple(
        dict.fromkeys(
            path.replace("\\", "/").strip()
            for path in allowed_paths
            if isinstance(path, str) and path.strip()
        )
    )
    if not normalized_paths:
        raise DevFarmError("allowed_paths must contain at least one path")
    line_schema = {
        "type": "array",
        "minItems": 1,
        "items": {"type": "string", "pattern": "^[^\\r\\n]*$"},
    }
    edit_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "enum": list(normalized_paths)},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
            "expected_lines": line_schema,
            "replacement_lines": line_schema,
        },
        "required": ["path", "start_line", "end_line", "expected_lines", "replacement_lines"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "file_edits": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": edit_schema,
            }
        },
        "required": ["file_edits"],
        "additionalProperties": False,
    }


def full_worker_output_schema(
    *,
    allowed_paths: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return the provider-facing schema for the legacy full Worker result.

    The full-result mode keeps its historical Host validation and optional
    metadata fields, but the fields required to build a durable result are
    now declared to providers that support structured output.  ``additionalProperties``
    remains enabled for compatibility; the Host still performs the final
    contract validation and never treats provider schema acceptance as proof
    of a valid patch.
    """

    line_array_schema = {
        "type": "array",
        "items": {"type": "string", "pattern": "^[^\\r\\n]*$"},
    }
    replacement_value: dict[str, Any] = {
        "oneOf": [
            {"type": "string"},
            line_array_schema,
        ]
    }
    if allowed_paths is None:
        replacement_object: dict[str, Any] = {
            "type": "object",
            "additionalProperties": replacement_value,
        }
    else:
        if isinstance(allowed_paths, (str, bytes)):
            raise DevFarmError("allowed_paths must be a sequence of paths")
        normalized_paths = tuple(
            dict.fromkeys(
                path.replace("\\", "/").strip()
                for path in allowed_paths
                if isinstance(path, str) and path.strip()
            )
        )
        if not normalized_paths:
            raise DevFarmError("allowed_paths must contain at least one path")
        replacement_object = {
            "type": "object",
            "properties": {path: replacement_value for path in normalized_paths},
            "additionalProperties": False,
            "maxProperties": 1,
        }

    return {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["pending", "running", "completed", "failed", "blocked_external"],
            },
            "changed_files": {"type": "array", "items": {"type": "string"}},
            "tests_run": {"type": "array", "items": {"type": "string"}},
            "tests_passed": {"type": "boolean"},
            "known_issues": {"type": "array", "items": {"type": "string"}},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "patch": {"type": "string"},
            "file_replacements": replacement_object,
            # These fields are model-owned annotations retained for the
            # existing artifact projection.  Their detailed shape remains
            # Host-local and is not used as acceptance evidence.
            "tests": {},
            "notes": {"type": "string"},
        },
        "required": [
            "status",
            "changed_files",
            "tests_run",
            "tests_passed",
            "known_issues",
            "assumptions",
        ],
        "additionalProperties": True,
    }


def worker_proposal_preflight_failure(
    reason: str,
    *,
    mode: str,
    finish_reason: str | None = None,
) -> dict[str, Any]:
    """Project a deterministic Worker rejection without retaining raw output.

    This projection is deliberately advisory metadata.  It tells the
    existing refinement boundary that the model output needs bounded
    correction before provider reassignment is considered; it does not select
    a provider, retry a request, or alter Host Verification authority.
    """

    if not isinstance(reason, str) or not reason.strip():
        raise DevFarmError("preflight failure reason must be non-empty text")
    if mode not in {FULL_WORKER_OUTPUT_MODE, MINIMAL_WORKER_OUTPUT_MODE, LOCALIZED_FILE_EDIT_MODE}:
        raise DevFarmError("preflight failure mode is unsupported")
    normalized = reason.casefold()
    normalized_finish_reason = (
        finish_reason.strip().casefold()
        if isinstance(finish_reason, str) and finish_reason.strip()
        else None
    )
    if "unsupported fields" in normalized or "additional field" in normalized:
        error_code = "WORKER_OUTPUT_ADDITIONAL_FIELD"
    elif (
        normalized_finish_reason in {"length", "max_tokens", "max_tokens_reached"}
        and ("json" in normalized or "json object" in normalized or "object" in normalized)
    ):
        # A length-terminated response is a distinct Host-observed failure:
        # replaying the same full contract is unlikely to help, while treating
        # it as an ordinary malformed JSON response loses the bounded output
        # signal needed for a safer reframe/localized correction.
        error_code = "WORKER_OUTPUT_TRUNCATED"
    elif "json" in normalized or "json object" in normalized:
        error_code = "WORKER_OUTPUT_INVALID_JSON"
    elif "must be" in normalized or "required" in normalized:
        error_code = "WORKER_OUTPUT_SCHEMA"
    elif "outside manifest" in normalized or "outside" in normalized and "allowed" in normalized:
        error_code = "WORKER_OUTPUT_PATH_SCOPE"
    elif (
        "newline" in normalized
        or "line" in normalized and "string" in normalized
        or "complete source lines" in normalized
    ):
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

    if mode not in {FULL_WORKER_OUTPUT_MODE, MINIMAL_WORKER_OUTPUT_MODE, LOCALIZED_FILE_EDIT_MODE}:
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

_FINISH_REASON_ALIASES = {
    "stop": "stop",
    "end_turn": "stop",
    "stop_sequence": "stop",
    "length": "length",
    "max_tokens": "length",
    "max_tokens_reached": "length",
    "tool_call": "tool_call",
    "tool_calls": "tool_call",
    "function_call": "tool_call",
    "malformed_function_call": "tool_call",
    "unexpected_tool_call": "tool_call",
    "content_filter": "content_filter",
    "safety": "content_filter",
    "recitation": "content_filter",
    "blocked": "content_filter",
}


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


def _safe_finish_reason(value: Any) -> str:
    """Project provider finish reasons to a small provider-neutral allowlist."""

    if not isinstance(value, str) or not value.strip():
        return "unknown"
    return _FINISH_REASON_ALIASES.get(value.strip().casefold(), "unknown")


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
        "finish_reason": (
            "not_observed"
            if response is None
            else _safe_finish_reason(getattr(response, "finish_reason", None))
        ),
        "attempt_count": 1,
        "host_verified": False,
        "host_verified_test_count": 0,
        "host_tests_passed": None,
        "result_accepted": None,
        "codex_correction_chars": None,
    }


__all__ = [
    "FULL_WORKER_OUTPUT_MODE",
    "LOCALIZED_FILE_EDIT_MODE",
    "LOCALIZED_WORKER_OUTPUT_KEYS",
    "MINIMAL_WORKER_OUTPUT_KEYS",
    "MINIMAL_WORKER_OUTPUT_MODE",
    "build_worker_metrics",
    "extract_json_object",
    "full_worker_output_schema",
    "localized_worker_output_schema",
    "minimal_worker_output_schema",
    "normalize_model_status",
    "safe_host_failure_metadata",
    "safe_usage",
    "worker_output_mode",
    "worker_proposal_preflight_failure",
    "worker_proposal_preflight_success",
]
