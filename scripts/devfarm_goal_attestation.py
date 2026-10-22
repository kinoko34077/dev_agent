"""Read-only M2 advisory provenance for persisted Commander parent plans.

Not a trusted model verdict, task review_decision, or execution authorization.
Source bodies and packet bodies are deliberately not persisted.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

# All unknown normalized fields contribute to identity by default. Each
# excluded lifecycle field must be separately reviewed when schema changes.
_ROOT_LIFECYCLE = frozenset({
    "status", "plan_revision", "created_at", "updated_at", "results",
    "review_decisions", "supervisor", "goal_attestation",
})
_TASK_LIFECYCLE = frozenset({
    "status", "attempt_count", "last_attempt_id", "stale_result_attempt_id",
    "result_ref", "block_reason", "last_result_status", "last_error",
    "integration_note", "manifest_history", "target_ref",
    "integration_revision", "source_attempt_id", "verified_patch_digest",
    "dispatch_id", "dispatch_started_at", "dispatch_deadline_at",
    "dispatch_owner_pid", "dispatch_recovery",
})
_BINDING_IDENTITY = ("logical_execution_id", "proposal_id", "child_key", "executor_kind")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ATTESTATION_KEYS = frozenset({
    "schema_version", "state", "advisory_only", "proposal_sha256",
    "definition_sha256", "source_refs", "packet_sha256", "assessor_ref",
})


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _check_sha(value: object) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ValueError("invalid SHA-256 digest")
    return value


def _check_ref(value: object) -> str:
    if (not isinstance(value, str) or not 0 < len(value) <= 500
            or value != value.strip() or any(ch in value for ch in "\r\n\0")):
        raise ValueError("invalid source/reviewer reference")
    return value


def definition_projection(plan: Mapping) -> dict:
    """Derive a stable identity from an actual normalized plan (not raw input)."""
    if not isinstance(plan, Mapping) or not isinstance(plan.get("tasks"), list) or not plan["tasks"]:
        raise ValueError("normalized plan must contain tasks")
    projected = {key: value for key, value in plan.items()
                 if key not in _ROOT_LIFECYCLE and key != "tasks"}
    tasks = []
    for task in plan["tasks"]:
        if not isinstance(task, Mapping):
            raise ValueError("invalid normalized task")
        stable = {key: value for key, value in task.items()
                  if key not in _TASK_LIFECYCLE and key != "canonical_execution"}
        binding = task.get("canonical_execution")
        if binding is not None:
            if not isinstance(binding, Mapping):
                raise ValueError("invalid canonical execution")
            stable["canonical_execution_identity"] = {
                key: binding[key] for key in _BINDING_IDENTITY if key in binding
            }
        tasks.append(stable)
    projected["tasks"] = tasks
    return projected


def definition_sha256(plan: Mapping) -> str:
    return _digest(definition_projection(plan))


def not_recorded() -> dict:
    return {"schema_version": 1, "state": "not_recorded", "advisory_only": True}


def normalize_attestation(raw: object) -> dict:
    """Validate bounded, non-authoritative metadata; reject embedded sources."""
    if raw == not_recorded():
        return not_recorded()
    if not isinstance(raw, Mapping) or set(raw) != _ATTESTATION_KEYS:
        raise ValueError("invalid advisory attestation keys")
    if (type(raw["schema_version"]) is not int or raw["schema_version"] != 1
            or raw["state"] != "reviewed_advisory" or raw["advisory_only"] is not True):
        raise ValueError("invalid advisory attestation state")
    for key in ("proposal_sha256", "definition_sha256", "packet_sha256"):
        _check_sha(raw[key])
    _check_ref(raw["assessor_ref"])
    sources = raw["source_refs"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 8:
        raise ValueError("invalid source-ref count")
    seen = set()
    copied = []
    for item in sources:
        if not isinstance(item, Mapping) or set(item) != {"ref", "sha256"}:
            raise ValueError("source body/extra fields forbidden")
        ref = _check_ref(item["ref"])
        if ref in seen:
            raise ValueError("duplicated source reference")
        seen.add(ref)
        copied.append({"ref": ref, "sha256": _check_sha(item["sha256"])})
    return {**{k: raw[k] for k in _ATTESTATION_KEYS if k != "source_refs"},
            "source_refs": copied}


def reviewed_attestation(plan: Mapping, assessment: Mapping, packet: Mapping) -> dict:
    """Caller must independently verify ALIGNED against the original full plan."""
    if assessment.get("disposition") != "ALIGNED" or assessment.get("advisory_only") is not True:
        raise ValueError("cannot attest a non-ALIGNED proposal")
    record = {
        "schema_version": 1,
        "state": "reviewed_advisory",
        "advisory_only": True,
        "proposal_sha256": assessment["proposal_sha256"],
        "definition_sha256": definition_sha256(plan),
        "source_refs": assessment["source_refs"],
        "packet_sha256": _digest(packet),
        # This is deliberately not authenticated identity evidence.
        "assessor_ref": "unverified:caller_assessed",
    }
    return normalize_attestation(record)


def applicability(plan: Mapping, record: object) -> str:
    """Derived display-only state; never used to grant Worker/merge permission."""
    if record is None:
        return "legacy_unknown"
    try:
        attested = normalize_attestation(record)
        if attested == not_recorded():
            return "not_recorded"
        return ("matches_initial_definition"
                if definition_sha256(plan) == attested["definition_sha256"]
                else "stale_definition")
    except (ValueError, KeyError, TypeError):
        return "invalid"
