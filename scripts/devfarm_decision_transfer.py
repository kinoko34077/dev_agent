"""Bounded, advisory cross-incident diagnosis transfer for devfarm Commander.

This is NOT an inference engine, a source fetcher, an approval/dispatch gate, or
a replacement for goal-alignment. Trusted workers must verify source references
and structure the episode; this module rejects malformed evidence and checks
conditional applicability and repeated probes before a new development choice.
It never reads secrets, invokes providers, changes repository state, or promotes
a candidate judgment rule to verified status.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any


class DecisionTransferError(ValueError):
    """Invalid or unsupported bounded decision evidence packet."""


_TYPES = frozenset({"user_report", "direct_observation", "code_inspection", "agent_report"})
_STAGES = frozenset({"producer", "consumer", "boundary"})
_STATES = frozenset({"present", "missing", "unknown"})
_MECHANISM = "producer_consumer_scope_mismatch"
_MAX_OBSERVATIONS = 32
_MAX_ATTEMPTS = 32


def _obj(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DecisionTransferError(f"{name} must be an object")
    return value


def _text(value: Any, name: str, max_length: int = 500) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise DecisionTransferError(f"{name} must be a bounded non-empty string")
    return value.strip()


def _list(value: Any, name: str, limit: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > limit:
        raise DecisionTransferError(f"{name} must be a bounded list")
    return value


def _ref(value: Any, name: str) -> str:
    ref = _text(value, name)
    # A locator is not an authenticated read. Only GitHub issue links are
    # supported in this initial public fixture contract; the caller must read
    # and verify the actual Issue and any later corrections independently.
    if not ref.startswith("https://github.com/") or "/issues/" not in ref:
        raise DecisionTransferError(f"{name} must be an explicit GitHub Issue URL")
    return ref


def _observations(value: Any, name: str) -> list[dict[str, str]]:
    items = _list(value, name, _MAX_OBSERVATIONS)
    if not items:
        raise DecisionTransferError(f"{name} cannot be empty")
    found: set[str] = set()
    result = []
    for index, raw in enumerate(items):
        obs = _obj(raw, f"{name}[{index}]")
        if set(obs) != {"id", "stage", "state", "evidence_type", "source_ref"}:
            raise DecisionTransferError("observation requires exactly id, stage, state, evidence_type, source_ref")
        oid = _text(obs["id"], "observation.id", 100)
        if oid in found:
            raise DecisionTransferError("duplicate observation id")
        found.add(oid)
        if obs["stage"] not in _STAGES or obs["state"] not in _STATES or obs["evidence_type"] not in _TYPES:
            raise DecisionTransferError("invalid observation stage/state/evidence_type")
        result.append({"id": oid, "stage": obs["stage"], "state": obs["state"],
                       "evidence_type": obs["evidence_type"],
                       "source_ref": _ref(obs["source_ref"], "observation.source_ref")})
    return result


def _attempts(value: Any, name: str, observation_ids: set[str]) -> tuple[list[dict[str, str]], int]:
    attempts = []
    repeated = 0
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(_list(value, name, _MAX_ATTEMPTS)):
        item = _obj(raw, f"{name}[{index}]")
        if set(item) != {"probe", "revision", "result_observation_id"}:
            raise DecisionTransferError("attempt requires exactly probe, revision, result_observation_id")
        probe = _text(item["probe"], "attempt.probe", 100)
        revision = _text(item["revision"], "attempt.revision", 100)
        oid = _text(item["result_observation_id"], "attempt.result_observation_id", 100)
        if oid not in observation_ids:
            raise DecisionTransferError("attempt must reference a recorded observation")
        key = (probe, revision, oid)
        if key in seen:
            repeated += 1
        seen.add(key)
        attempts.append({"probe": probe, "revision": revision, "result_observation_id": oid})
    return attempts, repeated


def _stage_state(observations: list[dict[str, str]], stage: str) -> tuple[str, str]:
    relevant = [o for o in observations if o["stage"] == stage
                and o["evidence_type"] in {"user_report", "direct_observation"}]
    states = {o["state"] for o in relevant if o["state"] != "unknown"}
    if len(states) > 1:
        return "conflict", "conflicting_source_claims"
    if not states:
        return "unknown", "not_observed"
    state = next(iter(states))
    kinds = {o["evidence_type"] for o in relevant if o["state"] == state}
    return state, "observed" if "direct_observation" in kinds else "reported"


def evaluate_decision_transfer(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Assess a conditional diagnostic *probe*, not a diagnosis or user outcome.

    A source-structured origin can offer a candidate rule. The target must be
    independently described; a separate issue reference prevents literal
    replay of the training incident from counting as transfer.
    """
    payload = _obj(packet, "packet")
    if set(payload) != {"schema_version", "origin", "target"} or payload["schema_version"] != 1:
        raise DecisionTransferError("unsupported decision-transfer packet")
    origin = _obj(payload["origin"], "origin")
    target = _obj(payload["target"], "target")
    if set(origin) != {"ref", "goal", "success_condition", "observations",
                       "attempts", "cause", "correction_ref"}:
        raise DecisionTransferError("origin fields incomplete or unexpected")
    if set(target) != {"ref", "observations", "attempts", "fixed_constraints"}:
        raise DecisionTransferError("target fields incomplete or unexpected")
    origin_ref = _ref(origin["ref"], "origin.ref")
    target_ref = _ref(target["ref"], "target.ref")
    if origin_ref == target_ref:
        raise DecisionTransferError("same incident cannot be its own transfer case")
    _text(origin["goal"], "origin.goal")
    _text(origin["success_condition"], "origin.success_condition")
    _ref(origin["correction_ref"], "origin.correction_ref")
    cause = _obj(origin["cause"], "origin.cause")
    if set(cause) != {"mechanism", "status", "source_ref"}:
        raise DecisionTransferError("cause fields incomplete or unexpected")
    if cause["mechanism"] != _MECHANISM or cause["status"] not in {"reported", "source_supported"}:
        raise DecisionTransferError("unsupported cause mechanism/status")
    _ref(cause["source_ref"], "origin.cause.source_ref")
    origin_obs = _observations(origin["observations"], "origin.observations")
    _, origin_repeat = _attempts(origin["attempts"], "origin.attempts",
                                {o["id"] for o in origin_obs})
    origin_producer, _ = _stage_state(origin_obs, "producer")
    origin_consumer, _ = _stage_state(origin_obs, "consumer")
    if origin_producer != "present" or origin_consumer != "missing":
        raise DecisionTransferError("source incident does not substantiate claimed condition")

    target_obs = _observations(target["observations"], "target.observations")
    _, target_repeat = _attempts(target["attempts"], "target.attempts",
                                {o["id"] for o in target_obs})
    fixed = _list(target["fixed_constraints"], "target.fixed_constraints", 16)
    for clause in fixed:
        _text(clause, "fixed constraint")
    producer, producer_level = _stage_state(target_obs, "producer")
    consumer, consumer_level = _stage_state(target_obs, "consumer")

    if "conflict" in {producer, consumer}:
        disposition = "NEEDS_EVIDENCE"
        probe = "reconcile_conflicting_observations"
    elif producer == "missing" or consumer == "present":
        disposition = "NOT_APPLICABLE"
        probe = None
    elif producer == "present" and consumer == "missing":
        if producer_level == consumer_level == "observed":
            disposition, probe = "PROBE_BOUNDARY", "trace_producer_to_consumer_scope"
        else:
            disposition, probe = "NEEDS_EVIDENCE", "measure_producer_and_consumer_in_same_revision"
    else:
        disposition, probe = "NEEDS_EVIDENCE", "establish_both_boundary_endpoints"

    return {
        "schema_version": 1,
        "advisory_only": True,
        "rule_id": "candidate:producer-consumer-scope",
        "rule_status": "candidate",
        "rule_origin_ref": origin_ref,
        "cause_status": cause["status"],
        "target_ref": target_ref,
        "disposition": disposition,
        "next_probe": probe,
        "avoid_same_probe_without_new_revision": target_repeat > 0,
        "origin_redundant_attempts": origin_repeat,
        "target_redundant_attempts": target_repeat,
        "producer_evidence": producer_level,
        "consumer_evidence": consumer_level,
        "fixed_constraints_preserved": fixed,
        "authority": "no_dispatch_no_merge_no_permission_no_root_cause_certification",
    }
