"""Run bounded, read-only semantic-coherence checks for the v2 authorities.

This is a diagnostic projection, not a router, scheduler, lifecycle store, or
Gate authority.  It composes the existing canonical contracts so a new wait,
route, adapter, or projection cannot silently introduce a second meaning.
The optional model funnel input is loaded from reviewed local snapshots only;
the command never contacts a Provider or mutates admission state.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import replace
import json
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.domain.execution import (  # noqa: E402
    CanonicalExecutionBinding,
    ExecutionLifecycleStage,
    ExecutionRequirement,
    FieldPresence,
    PresenceState,
    UnsupportedCapabilityError,
    devfarm_lifecycle_stage,
    operation_lifecycle_stage,
)
from src.dev_agent.domain.protocol import IntelligenceTier, ModelRequest, ProtocolError, RiskLevel, Task, TaskStatus, TaskType  # noqa: E402
from src.dev_agent.domain.wait import WAIT_CONDITION_REGISTRY, WaitReplayPolicy  # noqa: E402
from src.dev_agent.providers.base import ProviderError  # noqa: E402
from src.dev_agent.providers.cloudflare.provider import CloudflareWorkersAIHttpProvider  # noqa: E402
from src.dev_agent.providers.gemini.decoder import decode_generate_content  # noqa: E402
from src.dev_agent.intelligence.policy import TaskIntelligencePolicy  # noqa: E402
from src.dev_agent.intelligence.role_manifest import builtin_role_manifests  # noqa: E402
from src.dev_agent.resources.model_evidence import ModelEvidenceCatalog  # noqa: E402
from src.dev_agent.resources.model_funnel import FunnelReport, build_funnel_report  # noqa: E402
from src.dev_agent.resources.model_runtime import RuntimeAdmissionSnapshot  # noqa: E402
from src.dev_agent.resources.qualification import QualificationResolver  # noqa: E402
from src.dev_agent.resources.tier_authority import production_tier_authority  # noqa: E402


def _result(name: str, *, violations: list[str] | None = None, **details: Any) -> dict[str, Any]:
    bounded = list(violations or [])[:32]
    value: dict[str, Any] = {
        "name": name,
        "status": "FAIL" if bounded else "PASS",
        "violations": bounded,
    }
    value.update(details)
    return value


def check_wait_wake_coverage() -> dict[str, Any]:
    """Ensure every durable deferred status has one typed wake authority."""

    deferred = {
        status
        for status in TaskStatus
        if status.name.startswith("WAITING_") or status.name.startswith("BLOCKED_")
    }
    registered = set(WAIT_CONDITION_REGISTRY)
    missing = sorted((status.value for status in deferred - registered))
    extra = sorted((status.value for status in registered - deferred))
    unbound: list[str] = []
    for status, entry in WAIT_CONDITION_REGISTRY.items():
        if not entry.kind.value or not entry.wake_authority or not entry.wake_predicate:
            unbound.append(status.value)
        if status is TaskStatus.WAITING_RECONCILIATION and entry.replay_policy is not WaitReplayPolicy.NO_EXTERNAL_REPLAY:
            unbound.append(f"{status.value}:replay_policy")
    return _result(
        "wait_wake_coverage",
        violations=[
            *(f"missing:{value}" for value in missing),
            *(f"unexpected:{value}" for value in extra),
            *(f"unbound:{value}" for value in unbound),
        ],
        deferred_statuses=sorted(status.value for status in deferred),
        registered_statuses=sorted(status.value for status in registered),
        missing_statuses=missing,
        extra_entries=extra,
        unbound_entries=unbound,
    )


def check_funnel_invariants(report: FunnelReport) -> dict[str, Any]:
    """Validate formal-supply and role-scoped exact-route projections."""

    if not isinstance(report, FunnelReport):
        raise TypeError("report must be a FunnelReport")
    violations: list[str] = []
    identities: set[tuple[str, str, str]] = set()
    formal_rows = []
    for row in report.rows:
        if row.identity in identities:
            violations.append(f"duplicate_identity:{row.identity!r}")
        identities.add(row.identity)
        if row.formal_supply:
            formal_rows.append(row)
            if row.discovery_status != "CURRENT":
                violations.append(f"formal_supply_discovery:{row.identity!r}")
            if row.static_result != "ELIGIBLE":
                violations.append(f"formal_supply_static:{row.identity!r}")
            if row.qualification_status != "CURRENT_HIGH_CONFIDENCE":
                violations.append(f"formal_supply_qualification:{row.identity!r}")
            if row.billing_status != "CURRENT":
                violations.append(f"formal_supply_billing:{row.identity!r}")
            if row.runtime_status != "RUNTIME_ELIGIBLE":
                violations.append(f"formal_supply_runtime:{row.identity!r}")
    if report.coverage.get("independent_route_basis") not in {None, "provider_id+provider_binding_id; quota domains are not inferred"}:
        violations.append("unexpected_route_independence_basis")

    roles = sorted({role for row in report.rows for role in row.task_fit})
    role_supply: dict[str, dict[str, Any]] = {}
    for role in roles:
        selected = [row for row in formal_rows if role in row.task_fit]
        routes = {(row.provider_id, row.provider_binding_id) for row in selected}
        projected = report.supply_for(role=role)
        if projected["independent_route_count"] != len(routes):
            violations.append(f"role_route_count:{role}")
        role_supply[role] = {
            "formal_supply_count": len(selected),
            "independent_route_count": len(routes),
            "basis": projected["independence_basis"],
        }

    return _result(
        "model_funnel_invariants",
        violations=violations,
        formal_supply_count=len(formal_rows),
        independent_route_count=len({(row.provider_id, row.provider_binding_id) for row in formal_rows}),
        role_supply=role_supply,
    )


def _check_field_presence() -> dict[str, Any]:
    unspecified = FieldPresence.unspecified()
    value = FieldPresence.value(42)
    explicit_none = FieldPresence.explicit_none()
    violations: list[str] = []
    if {unspecified.state, value.state, explicit_none.state} != {
        PresenceState.UNSPECIFIED,
        PresenceState.VALUE,
        PresenceState.EXPLICIT_NONE,
    }:
        violations.append("presence_states_collapsed")
    requirement = ExecutionRequirement(
        role="implementer",
        minimum_intelligence_tier=IntelligenceTier.L1,
        risk=RiskLevel.NORMAL,
        required_capabilities=("text",),
        feature_requirements={"seed": value, "thinking": explicit_none},
    )
    try:
        requirement.require_supported_capabilities({"text"})
    except UnsupportedCapabilityError as exc:
        if exc.code != "UNSUPPORTED_CAPABILITY" or exc.capabilities != ("seed",):
            violations.append("unsupported_capability_not_typed")
    else:
        violations.append("unsupported_capability_was_silently_dropped")
    return _result(
        "field_presence_and_capability",
        violations=violations,
        states=[unspecified.state.value, value.state.value, explicit_none.state.value],
        explicit_none_is_requirement=False,
    )


def check_lifecycle_projection() -> dict[str, Any]:
    """Check shared projection pairs and fail-closed contradiction handling."""

    pairs = (
        (TaskStatus.READY, "READY"),
        (TaskStatus.RUNNING, "DISPATCHED"),
        (TaskStatus.BLOCKED_QUOTA, "BLOCKED"),
        (TaskStatus.FAILED, "REJECTED"),
    )
    violations = [
        f"projection:{operation.value}!={devfarm}"
        for operation, devfarm in pairs
        if operation_lifecycle_stage(operation) is not devfarm_lifecycle_stage(devfarm)
    ]
    binding = CanonicalExecutionBinding.for_child(
        logical_execution_id="coherence-execution",
        proposal_id="coherence-proposal",
        child_key="worker",
        executor_kind="devfarm_worker",
    ).with_observation(stage=ExecutionLifecycleStage.INTEGRATED, attempt_id="attempt-1")
    contradiction_rejected = False
    try:
        binding.merge_observation(binding.with_stage(ExecutionLifecycleStage.ATTEMPT_ACTIVE))
    except ProtocolError:
        contradiction_rejected = True
    if not contradiction_rejected:
        violations.append("lifecycle_contradiction_accepted")
    return _result(
        "canonical_lifecycle_projection",
        violations=violations,
        projection_pairs_checked=len(pairs),
        contradiction_rejected=contradiction_rejected,
    )


def check_provider_normalization() -> dict[str, Any]:
    """Verify materially different Provider envelopes enter one canonical result."""

    request = ModelRequest(
        messages=[{"role": "user", "content": "bounded semantic coherence fixture"}],
        requested_capabilities=["text"],
    )
    gemini = decode_generate_content(
        {"candidates": [{"content": {"parts": [{"text": "ready"}]}, "finishReason": "STOP"}]},
        model="gemini-test",
        request_id=request.request_id,
    )
    cloudflare = CloudflareWorkersAIHttpProvider._decode(
        {
            "success": True,
            "result": {
                "model": "cloudflare-test",
                "choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}],
            },
        },
        request,
        model="cloudflare-test",
    )
    violations: list[str] = []
    if gemini.text_segments != cloudflare.text_segments:
        violations.append("text_semantics_differ")
    if gemini.finish_reason.lower() != cloudflare.finish_reason.lower():
        violations.append("finish_reason_semantics_differ")
    return _result(
        "provider_protocol_normalization",
        violations=violations,
        providers_checked=["cloudflare", "gemini"],
        canonical_fields=["text_segments", "finish_reason", "provider", "model"],
    )


def check_provider_decode_diagnostics() -> dict[str, Any]:
    """Ensure malformed Provider envelopes yield bounded shape facts only."""

    required = {
        "decoder_branch",
        "http_status",
        "content_type",
        "top_level_keys",
        "value_kinds",
        "nested_paths",
        "size_bucket",
        "schema_fingerprint",
    }
    violations: list[str] = []
    request = ModelRequest(messages=[{"role": "user", "content": "bounded diagnostic fixture"}])
    errors: list[tuple[str, ProviderError]] = []
    try:
        decode_generate_content(
            {"candidates": [], "private": {"value": "redacted-secret-fixture"}},
            model="gemini-test",
            request_id=request.request_id,
        )
    except ProviderError as error:
        errors.append(("gemini", error))
    try:
        CloudflareWorkersAIHttpProvider._decode(
            {"success": True, "result": None, "private": {"value": "redacted-secret-fixture"}},
            request,
            model="cloudflare-test",
        )
    except ProviderError as error:
        errors.append(("cloudflare", error))
    for provider, error in errors:
        diagnostics = error.decode_diagnostics
        if not isinstance(diagnostics, Mapping):
            violations.append(f"missing:{provider}")
            continue
        missing = sorted(required - set(diagnostics))
        violations.extend(f"{provider}:missing:{key}" for key in missing)
        encoded = json.dumps(diagnostics, ensure_ascii=False, sort_keys=True)
        if "redacted-secret-fixture" in encoded:
            violations.append(f"{provider}:raw_payload_retained")
        if len(encoded) > 6_000:
            violations.append(f"{provider}:diagnostic_unbounded")
    if {provider for provider, _ in errors} != {"gemini", "cloudflare"}:
        violations.append("provider_decode_branch_not_observed")
    return _result(
        "provider_decode_structure",
        violations=violations,
        providers_checked=["cloudflare", "gemini"],
        raw_payload_retained=False,
        diagnostic_fields=sorted(required),
    )


def check_role_preflight_dispatch_equivalence() -> dict[str, Any]:
    """Compare role preflight with the canonical request used for dispatch."""

    role_task_types = {
        "planner": TaskType.REASONING,
        "implementer": TaskType.WORKER,
        "reviewer": TaskType.REASONING,
    }
    manifests = builtin_role_manifests()
    policy = TaskIntelligencePolicy()
    violations: list[str] = []
    checked: list[str] = []
    for role in sorted(role_task_types):
        task = Task(
            objective=f"coherence {role} task",
            task_type=role_task_types[role],
            required_capabilities=[],
            metadata={"execution_role": role},
        )
        decision = policy.decide(task)
        try:
            admission = manifests[role].validate_task(task, decision)
        except Exception as exc:
            violations.append(f"preflight:{role}:{type(exc).__name__}")
            continue
        request = ModelRequest(
            task_id=task.task_id,
            messages=[{"role": "user", "content": "bounded role projection"}],
            metadata={
                "execution_role": role,
                "task_type": task.task_type.value,
                "minimum_intelligence_tier": decision.minimum_tier.value,
                "maximum_intelligence_tier": decision.maximum_tier.value,
                "risk": task.risk.value,
            },
        )
        requirement = ExecutionRequirement.from_model_request(request)
        if requirement.role != admission.role_id:
            violations.append(f"role:{role}")
        if requirement.task_type.value != admission.task_type:
            violations.append(f"task_type:{role}")
        if requirement.minimum_intelligence_tier is not admission.intelligence_tier:
            violations.append(f"tier:{role}")
        if requirement.risk is not admission.risk or requirement.sensitivity != admission.sensitivity:
            violations.append(f"classification:{role}")
        checked.append(role)
    return _result(
        "role_preflight_dispatch_equivalence",
        violations=violations,
        roles_checked=checked,
        canonical_projection="ExecutionRequirement",
        provider_selection_included=False,
    )


def check_tier_authority_conflicts() -> dict[str, Any]:
    """Ensure conflicting exact-route tier evidence is rejected."""

    decision = production_tier_authority.resolve(
        provider_id="gemini",
        provider_binding_id="gemini:account-a",
        model_id="gemini-test",
        qualification=SimpleNamespace(intelligence_tier="L1"),
        model_admission=SimpleNamespace(intelligence_tier="L2"),
    )
    conflict_rejected = not decision.eligible and decision.reason == "TIER_EVIDENCE_CONFLICT"
    return _result(
        "exact_route_tier_authority",
        violations=[] if conflict_rejected else ["tier_evidence_conflict_not_rejected"],
        conflict_rejected=conflict_rejected,
        decision_reason=decision.reason,
        exact_identity=["gemini", "gemini:account-a", "gemini-test"],
    )


_STATE_HEAD_LABELS = {
    "accepted_head": "Accepted remote head at latest integrated implementation/evidence",
    "implementation_head": "Latest implementation baseline",
}
_GATE_VALUE_PATHS = {
    "D9_DOGFOOD": ("phase7_dogfood", "status"),
    "D9_PRODUCTION_DEPLOYMENT": ("phase7_production_deployment", "status"),
    "PHASE8_PREPARATION": ("phase8_preparation", "status"),
    "PHASE8_LIVE_ACTIVATION": ("phase8_preparation", "live_activation"),
}


def _git_text(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={root.as_posix()}", *args],
            cwd=root,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip()


def _state_head_from_text(text: str, label: str) -> str | None:
    for line in text.splitlines()[:80]:
        if label not in line:
            continue
        match = re.search(r"`([0-9a-f]{7,40})`", line, flags=re.IGNORECASE)
        if match:
            return match.group(1).lower()
    return None


def _state_projection_status(
    *,
    current_head: str,
    accepted_head: str | None,
    implementation_head: str | None,
    gate_values: Mapping[str, str],
    projected_gate_values: Mapping[str, str],
    ancestor_checks: Mapping[str, bool | None],
) -> dict[str, Any]:
    """Evaluate a prose projection without making it a second authority."""

    violations: list[str] = []
    if not re.fullmatch(r"[0-9a-f]{40}", current_head, flags=re.IGNORECASE):
        violations.append("current_head_invalid")
    for name, value in (
        ("accepted_head", accepted_head),
        ("implementation_head", implementation_head),
    ):
        if not value:
            violations.append(f"{name}_missing")
        elif not re.fullmatch(r"[0-9a-f]{7,40}", value, flags=re.IGNORECASE):
            violations.append(f"{name}_invalid")
        elif ancestor_checks.get(name) is False:
            violations.append(f"{name}_not_ancestor")
    for key, value in gate_values.items():
        if projected_gate_values.get(key) != value:
            violations.append(f"gate_projection:{key}")
    return _result(
        "state_projection_consistency",
        violations=violations,
        gate_authority="spec/v2/GATE_STATUS.json",
        current_head=current_head,
        accepted_head=accepted_head,
        implementation_head=implementation_head,
        projected_gate_values=dict(projected_gate_values),
        gate_values=dict(gate_values),
        ancestor_verification=(
            "LIMITED_SHALLOW_HISTORY"
            if any(value is None for value in ancestor_checks.values())
            else "COMPLETE"
        ),
        sync_required=current_head.lower() not in {
            value.lower() for value in (accepted_head, implementation_head) if value
        },
    )


def check_state_projection_consistency(root: Path | None = None) -> dict[str, Any]:
    """Detect stale/invalid Current State markers against machine-owned state."""

    repository_root = root or ROOT
    current_head = _git_text(repository_root, "rev-parse", "HEAD") or ""
    state_path = repository_root / "docs" / "CURRENT_STATE.md"
    gate_path = repository_root / "spec" / "v2" / "GATE_STATUS.json"
    try:
        state_text = state_path.read_text(encoding="utf-8")
        gate_document = json.loads(gate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _result(
            "state_projection_consistency",
            violations=["state_projection_source_unreadable"],
            gate_authority="spec/v2/GATE_STATUS.json",
            sync_required=True,
        )

    accepted_head = _state_head_from_text(state_text, _STATE_HEAD_LABELS["accepted_head"])
    implementation_head = _state_head_from_text(state_text, _STATE_HEAD_LABELS["implementation_head"])
    tracks = gate_document.get("development_tracks", {})
    gate_values: dict[str, str] = {}
    for gate, (track, field) in _GATE_VALUE_PATHS.items():
        value = tracks.get(track, {}).get(field) if isinstance(tracks, Mapping) else None
        if isinstance(value, str):
            gate_values[gate] = value

    projected_gate_values: dict[str, str] = {}
    for gate in _GATE_VALUE_PATHS:
        match = re.search(rf"{re.escape(gate)}=([A-Z_]+)", state_text[:12_000])
        if match:
            projected_gate_values[gate] = match.group(1)

    ancestor_checks: dict[str, bool | None] = {}
    for name, value in (("accepted_head", accepted_head), ("implementation_head", implementation_head)):
        if not value or not current_head:
            ancestor_checks[name] = False
            continue
        try:
            result = subprocess.run(
                [
                    "git",
                    "-c",
                    f"safe.directory={repository_root.as_posix()}",
                    "merge-base",
                    "--is-ancestor",
                    value,
                    current_head,
                ],
                cwd=repository_root,
                capture_output=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            ancestor_checks[name] = None
            continue
        if result.returncode == 0:
            ancestor_checks[name] = True
        elif result.returncode == 1:
            ancestor_checks[name] = False
        elif _git_text(repository_root, "rev-parse", "--is-shallow-repository") == "true":
            ancestor_checks[name] = None
        else:
            ancestor_checks[name] = False
    return _state_projection_status(
        current_head=current_head,
        accepted_head=accepted_head,
        implementation_head=implementation_head,
        gate_values=gate_values,
        projected_gate_values=projected_gate_values,
        ancestor_checks=ancestor_checks,
    )


def check_report(report: Mapping[str, Any]) -> tuple[str, ...]:
    """Return bounded check names that failed in a generated report."""

    checks = report.get("checks") if isinstance(report, Mapping) else None
    if not isinstance(checks, list):
        return ("report_checks_missing",)
    return tuple(
        str(item.get("name"))
        for item in checks
        if isinstance(item, Mapping) and item.get("status") != "PASS"
    )[:32]


def build_report(*, funnel_report: FunnelReport | None = None) -> dict[str, Any]:
    """Build a bounded machine-readable report without external effects."""

    if funnel_report is not None and not isinstance(funnel_report, FunnelReport):
        raise TypeError("funnel_report must be a FunnelReport or None")
    checks = [
        check_wait_wake_coverage(),
        _check_field_presence(),
        check_lifecycle_projection(),
        check_provider_normalization(),
        check_provider_decode_diagnostics(),
        check_role_preflight_dispatch_equivalence(),
        check_tier_authority_conflicts(),
        check_state_projection_consistency(),
    ]
    if funnel_report is not None:
        checks.append(check_funnel_invariants(funnel_report))
    failures = check_report({"checks": checks})
    return {
        "schema_version": 1,
        "status": "FAIL" if failures else "PASS",
        "checks": checks,
        "failed_checks": list(failures),
        "network_calls": False,
        "gate_impact": "UNCHANGED",
        "gate_authority": "spec/v2/GATE_STATUS.json",
        "funnel_included": funnel_report is not None,
    }


def _load_local_funnel() -> FunnelReport:
    return build_funnel_report(
        ModelEvidenceCatalog.load_default(),
        runtime_snapshot=RuntimeAdmissionSnapshot.empty(),
        qualification_resolver=QualificationResolver(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-model-funnel",
        action="store_true",
        help="include the reviewed local model-evidence funnel; never contacts a Provider",
    )
    args = parser.parse_args(argv)
    try:
        report = build_report(funnel_report=_load_local_funnel() if args.with_model_funnel else None)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "category": type(exc).__name__}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
