"""Run one opt-in, proposal-only Free L2 Reviewer shadow request.

This utility reads the existing compact ReviewPacket and durable Codex
ReviewDecision from a Commander plan, routes one bounded review request
through the existing qualified ResourceRouter/ProviderDispatcher, and emits a
comparison record.  It never writes a ReviewDecision, integrates a patch, or
grants the Free L2 model authority.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.devfarm_resource_pool import (  # noqa: E402 - explicit script boundary
    ResourcePoolError,
    admit_resource_pool,
    compose_resource_pool,
    make_binding,
    resolve_provider_pool as resolve_shared_provider_pool,
)
from scripts.devfarm_host_dispatch import create_host_process_executor  # noqa: E402
from scripts.devfarm_supervisor import CodexSupervisedCommanderRun  # noqa: E402
from src.dev_agent.intelligence.reviewer_adapter import (  # noqa: E402
    ModelReviewAdapter,
    ReviewAdapterError,
    compare_review_proposal,
)
from src.dev_agent.providers.base import ProviderError  # noqa: E402
from src.dev_agent.providers.dispatch import ProviderPoolExhausted  # noqa: E402
from src.dev_agent.providers.host_dispatch import route_through_host  # noqa: E402
from src.dev_agent.resources.model_evidence import ModelEvidenceCatalog  # noqa: E402
from src.dev_agent.resources.control import DispatchDenied  # noqa: E402
from src.dev_agent.resources.qualification import QualificationResolver  # noqa: E402
from src.dev_agent.operation import OperationProviderBinding  # noqa: E402


def resolve_provider_pool(*, pool_json: str | None, use_configured_pool: bool, env=None):
    """Resolve an explicit Reviewer pool through the shared Host boundary."""

    try:
        kwargs = {"pool_json": pool_json, "use_configured_pool": use_configured_pool}
        if env is not None:
            kwargs["env"] = env
        return resolve_shared_provider_pool(**kwargs)
    except ResourcePoolError as exc:
        raise ReviewAdapterError(str(exc)) from exc


def _reviewer_bindings(
    *,
    provider_pool: tuple[OperationProviderBinding, ...] | list[OperationProviderBinding] | None,
    provider_id: str,
    binding_id: str,
    model_id: str,
    api_key_env: str,
    quota_domain: str,
    timeout_seconds: float,
) -> tuple[OperationProviderBinding, ...]:
    if provider_pool is not None:
        return tuple(provider_pool)
    return (
        make_binding(
            provider_id=provider_id,
            binding_id=binding_id,
            model_id=model_id,
            api_key_env=api_key_env,
            quota_domain=quota_domain,
            timeout_seconds=timeout_seconds,
        ),
    )


def _admit_reviewer_resources(
    *,
    provider_pool: tuple[OperationProviderBinding, ...] | list[OperationProviderBinding] | None,
    provider_id: str,
    binding_id: str,
    model_id: str,
    api_key_env: str,
    quota_domain: str,
    timeout_seconds: float,
    required_tier: str = "L2",
    model_admission_resolver=None,
    model_catalog=None,
    expand_discovered_models: bool = False,
):
    resolver = QualificationResolver()
    evidence = ModelEvidenceCatalog.load_default()
    admission_resolver = model_admission_resolver if model_admission_resolver is not None else evidence.resolver
    catalog = model_catalog if model_catalog is not None else evidence.catalog
    bindings = _reviewer_bindings(
        provider_pool=provider_pool,
        provider_id=provider_id,
        binding_id=binding_id,
        model_id=model_id,
        api_key_env=api_key_env,
        quota_domain=quota_domain,
        timeout_seconds=timeout_seconds,
    )
    admitted = admit_resource_pool(
        bindings,
        resolver=resolver,
        required_tier=required_tier,
        no_charge_required=True,
        model_admission_resolver=admission_resolver,
        model_catalog=catalog,
        expand_discovered_models=expand_discovered_models,
    )
    if not admitted:
        raise ReviewAdapterError(f"exact current {required_tier} reviewer resource is not admitted")
    return resolver, admission_resolver, admitted


def _request_reviewer_proposal(
    *,
    root: str | Path,
    packet: dict[str, object],
    admitted,
    resolver: QualificationResolver,
    model_admission_resolver,
    timeout_seconds: float,
    allow_unknown_quota: bool,
    execution_boundary: str,
):
    if execution_boundary not in {"in_process", "host_process"}:
        raise ReviewAdapterError("execution_boundary must be in_process or host_process")
    with compose_resource_pool(
        admitted,
        resolver=resolver,
        model_admission_resolver=model_admission_resolver,
        resource_id_prefix="reviewer-shadow",
    ) as resource_pool:
        reviewer_provider = resource_pool.dispatcher
        if execution_boundary == "host_process":
            reviewer_provider = route_through_host(
                resource_pool.dispatcher,
                create_host_process_executor(
                    Path(root).resolve() / ".devfarm" / "host-dispatch",
                    timeout_seconds=timeout_seconds,
                ),
            )
        proposal = ModelReviewAdapter(
            reviewer_provider,
            allow_unknown_quota=allow_unknown_quota,
        ).propose(packet)
        selected = next(
            (entry for entry in reversed(resource_pool.dispatcher.audits) if entry.outcome == "succeeded"),
            None,
        )
        audits = [
            {
                "provider": entry.provider_id,
                "binding": entry.provider_binding_id,
                "model": entry.model_id,
                "outcome": entry.outcome,
            }
            for entry in resource_pool.dispatcher.audits
        ]
        selected_identity = None if selected is None else {
            "provider": selected.provider_id,
            "binding": selected.provider_binding_id,
            "model": selected.model_id,
        }
    return proposal, selected_identity, audits


def run_shadow(
    *,
    root: str | Path,
    run_id: str,
    task_id: str,
    provider_id: str,
    binding_id: str,
    model_id: str,
    api_key_env: str,
    quota_domain: str,
    timeout_seconds: float,
    allow_unknown_quota: bool,
    required_tier: str = "L2",
    execution_boundary: str = "in_process",
    provider_pool: tuple[OperationProviderBinding, ...] | list[OperationProviderBinding] | None = None,
    model_admission_resolver=None,
    model_catalog=None,
    expand_discovered_models: bool = False,
) -> dict[str, object]:
    runner = CodexSupervisedCommanderRun(root, run_id)
    plan = runner.plan()
    task = next((item for item in plan["tasks"] if item["task_id"] == task_id), None)
    if task is None:
        raise ReviewAdapterError("Commander task was not found")
    packet = next(
        (
            item
            for item in plan.get("supervisor", {}).get("review_packets", [])
            if item.get("task_id") == task_id
        ),
        None,
    )
    if not isinstance(packet, dict):
        raise ReviewAdapterError("compact ReviewPacket is not available")
    attempt_id = packet.get("attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise ReviewAdapterError("ReviewPacket has no attempt identity")
    decisions = [
        item
        for item in plan.get("review_decisions", [])
        if item.get("task_id") == task_id and item.get("attempt_id") == attempt_id
    ]
    if not decisions:
        raise ReviewAdapterError("durable Codex ReviewDecision is not available")
    codex_decision = decisions[-1]
    admission_kwargs = {
        "provider_pool": provider_pool,
        "provider_id": provider_id,
        "binding_id": binding_id,
        "model_id": model_id,
        "api_key_env": api_key_env,
        "quota_domain": quota_domain,
        "timeout_seconds": timeout_seconds,
        "model_admission_resolver": model_admission_resolver,
        "model_catalog": model_catalog,
        "expand_discovered_models": expand_discovered_models,
    }
    if required_tier != "L2":
        admission_kwargs["required_tier"] = required_tier
    resolver, admission_resolver, admitted = _admit_reviewer_resources(**admission_kwargs)
    proposal, selected, audits = _request_reviewer_proposal(
        root=root,
        packet=packet,
        admitted=admitted,
        resolver=resolver,
        model_admission_resolver=admission_resolver,
        timeout_seconds=timeout_seconds,
        allow_unknown_quota=allow_unknown_quota,
        execution_boundary=execution_boundary,
    )
    comparison = compare_review_proposal(proposal, codex_decision["decision"], packet)
    proposal_digest = hashlib.sha256(
        json.dumps(proposal.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    packet_digest = hashlib.sha256(
        json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "status": "live_shadow_validated",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "packet_sha256": packet_digest,
        "reviewer": {
            **(selected or {"provider": None, "binding": None, "model": None}),
            "intelligence_tier": required_tier,
        },
        "proposal_sha256": proposal_digest,
        "proposal": proposal.to_dict(),
        "codex_decision": {
            "decision_id": codex_decision.get("decision_id"),
            "decision": codex_decision.get("decision"),
            "reviewer_role": codex_decision.get("reviewer_role"),
        },
        "comparison": comparison.to_dict(),
        "host_verification": packet.get("verification_summary", {}),
        "dispatch_audits": audits,
        "allow_unknown_quota": allow_unknown_quota,
    }


def run_proposal_only(
    *,
    root: str | Path,
    run_id: str,
    task_id: str,
    provider_id: str,
    binding_id: str,
    model_id: str,
    api_key_env: str,
    quota_domain: str,
    timeout_seconds: float,
    allow_unknown_quota: bool,
    required_tier: str = "L2",
    execution_boundary: str = "in_process",
    provider_pool: tuple[OperationProviderBinding, ...] | list[OperationProviderBinding] | None = None,
    model_admission_resolver=None,
    model_catalog=None,
    expand_discovered_models: bool = False,
) -> dict[str, object]:
    """Request one Free L2 proposal without requiring a Codex decision.

    This is the D7 input boundary.  It reads the current Host-built packet,
    uses the same qualified Resource composition as the comparison path, and
    returns a proposal-only record.  It never writes a review decision,
    changes Task state, or performs integration.
    """

    runner = CodexSupervisedCommanderRun(root, run_id)
    packet = runner.review_packet(task_id)
    attempt_id = packet.get("attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise ReviewAdapterError("ReviewPacket has no attempt identity")
    admission_kwargs = {
        "provider_pool": provider_pool,
        "provider_id": provider_id,
        "binding_id": binding_id,
        "model_id": model_id,
        "api_key_env": api_key_env,
        "quota_domain": quota_domain,
        "timeout_seconds": timeout_seconds,
        "model_admission_resolver": model_admission_resolver,
        "model_catalog": model_catalog,
        "expand_discovered_models": expand_discovered_models,
    }
    if required_tier != "L2":
        admission_kwargs["required_tier"] = required_tier
    resolver, admission_resolver, admitted = _admit_reviewer_resources(**admission_kwargs)
    proposal, selected, audits = _request_reviewer_proposal(
        root=root,
        packet=packet,
        admitted=admitted,
        resolver=resolver,
        model_admission_resolver=admission_resolver,
        timeout_seconds=timeout_seconds,
        allow_unknown_quota=allow_unknown_quota,
        execution_boundary=execution_boundary,
    )
    proposal_digest = hashlib.sha256(
        json.dumps(proposal.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    packet_digest = hashlib.sha256(
        json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "status": "live_shadow_proposal_only",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "packet_sha256": packet_digest,
        "reviewer": {
            **(selected or {"provider": None, "binding": None, "model": None}),
            "intelligence_tier": required_tier,
        },
        "proposal_sha256": proposal_digest,
        "proposal": proposal.to_dict(),
        "host_verification": packet.get("verification_summary", {}),
        "authority": {
            "reviewer_mode": "shadow",
            "proposal_only": True,
            "final_authority": ["codexless_policy", "host_policy"],
            "integration_performed_by_shadow": False,
        },
        "dispatch_audits": audits,
        "allow_unknown_quota": allow_unknown_quota,
        "raw_worker_conversation_recorded": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--provider", default="gemini")
    parser.add_argument("--binding", default="gemini:worker:free-3")
    parser.add_argument("--model", default="gemini-3.6-flash")
    parser.add_argument("--api-key-env", default="GEMINI_API_KEY_3")
    parser.add_argument("--quota-domain", default="gemini:project:982142111392")
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument("--allow-unknown-quota", action="store_true")
    parser.add_argument(
        "--pool-json",
        help="explicit JSON array of non-secret OperationProviderBinding objects",
    )
    parser.add_argument(
        "--configured-pool",
        action="store_true",
        help="explicitly use configured non-secret Provider bindings as the Reviewer pool",
    )
    parser.add_argument(
        "--expand-discovered-models",
        action="store_true",
        help="explicitly materialize current ordinary text models from each credential binding",
    )
    parser.add_argument(
        "--execution-boundary",
        choices=("host_process", "in_process"),
        default="host_process",
        help="where the selected concrete Provider call runs; live operation defaults to the Host process",
    )
    parser.add_argument(
        "--proposal-only",
        action="store_true",
        help="emit a D7 Free L2 proposal without requiring a durable Codex decision",
    )
    args = parser.parse_args(argv)
    try:
        runner = run_proposal_only if args.proposal_only else run_shadow
        model_evidence = ModelEvidenceCatalog.load_default()
        provider_pool = resolve_provider_pool(
            pool_json=args.pool_json,
            use_configured_pool=args.configured_pool,
        )
        output = runner(
            root=args.root,
            run_id=args.run_id,
            task_id=args.task_id,
            provider_id=args.provider,
            binding_id=args.binding,
            model_id=args.model,
            api_key_env=args.api_key_env,
            quota_domain=args.quota_domain,
            timeout_seconds=args.timeout_seconds,
            allow_unknown_quota=args.allow_unknown_quota,
            execution_boundary=args.execution_boundary,
            provider_pool=provider_pool,
            model_admission_resolver=model_evidence.resolver,
            model_catalog=model_evidence.catalog,
            expand_discovered_models=args.expand_discovered_models,
        )
        code = 0
    except (ReviewAdapterError, DispatchDenied, ProviderPoolExhausted, ProviderError) as exc:
        output = {"status": "blocked_external", "category": type(exc).__name__, "message": str(exc)[:400]}
        code = 2
    except Exception as exc:
        output = {"status": "failed", "category": type(exc).__name__, "message": str(exc)[:400]}
        code = 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
