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
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.devfarm_planner_shadow import (  # noqa: E402 - explicit script boundary
    _build_provider,
    _single_binding,
    admit_planner_pool,
)
from scripts.devfarm_supervisor import CodexSupervisedCommanderRun  # noqa: E402
from src.dev_agent.intelligence.reviewer_adapter import (  # noqa: E402
    ModelReviewAdapter,
    ReviewAdapterError,
    compare_review_proposal,
)
from src.dev_agent.providers.base import ProviderError  # noqa: E402
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderPoolExhausted, ProviderRegistry  # noqa: E402
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy  # noqa: E402
from src.dev_agent.resources.billing_catalog import profile_for  # noqa: E402
from src.dev_agent.resources.control import DispatchDenied  # noqa: E402
from src.dev_agent.resources.ledger import ResourceLedger  # noqa: E402
from src.dev_agent.resources.model_evidence import ModelEvidenceCatalog  # noqa: E402
from src.dev_agent.resources.qualification import QualificationResolver  # noqa: E402
from src.dev_agent.resources.router import ResourceRouter  # noqa: E402
from src.dev_agent.resources.control import ResourceControlPlane  # noqa: E402


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
    resolver = QualificationResolver()
    evidence = ModelEvidenceCatalog.load_default()
    binding = _single_binding(
        provider_id=provider_id,
        binding_id=binding_id,
        model_id=model_id,
        api_key_env=api_key_env,
        quota_domain=quota_domain,
        timeout_seconds=timeout_seconds,
    )
    admitted = admit_planner_pool(
        (binding,),
        resolver=resolver,
        model_admission_resolver=evidence.resolver,
        model_catalog=evidence.catalog,
    )
    if not admitted:
        raise ReviewAdapterError("exact current high-confidence L2 reviewer resource is not admitted")

    with TemporaryDirectory(prefix="dev-agent-reviewer-shadow-") as directory:
        ledger = ResourceLedger(Path(directory) / "resources.sqlite3")
        try:
            concrete = []
            for candidate, qualification, profile in admitted:
                admission = evidence.resolver.resolve(
                    candidate.provider_id,
                    candidate.credential_binding_id,
                    candidate.model,
                )
                if admission is None:
                    raise ReviewAdapterError("reviewer model evidence expired after admission")
                concrete.append(_build_provider(binding=candidate))
                resource_id = f"reviewer-shadow:{candidate.binding_id}"
                ledger.register_resource(
                    resource_id,
                    provider_id=candidate.provider_id,
                    provider_binding_id=candidate.binding_id,
                    native_unit="request",
                    capacity=1,
                    capabilities=sorted(set(qualification.routing_capabilities) & set(admission.capabilities)),
                    sensitivity="normal",
                    cost_minor=profile.cost_minor,
                    price_currency=profile.price_currency,
                    quota_domain=candidate.quota_domain,
                    intelligence_tier=admission.intelligence_tier,
                    metadata={
                        "provider_binding_id": candidate.binding_id,
                        "qualification_binding_id": candidate.credential_binding_id,
                        "model_id": candidate.model,
                        "intelligence_tier": admission.intelligence_tier,
                        "privacy_profile": "remote_cloud",
                        "qualification_required": True,
                        "billing_authority": "trusted_catalog",
                        "billing_mode": profile.billing_mode,
                        "overage_policy": profile.overage_policy,
                        "no_charge_guaranteed": profile.no_charge_guaranteed,
                        "billing_expires_at": profile.expires_at,
                        "allowance_amount": profile.allowance_amount,
                        "allowance_currency": profile.allowance_currency,
                        "allowance_period": profile.allowance_period,
                    },
                )
                ledger.observe(resource_id, available=1, health="healthy", concurrency_limit=1)
            policy = BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0)
            BudgetAuthority.configure(ledger, policy)
            dispatcher = ProviderDispatcher(
                ProviderRegistry(concrete),
                ResourceControlPlane(
                    ResourceRouter(
                        ledger,
                        qualification_resolver=resolver,
                        model_admission_resolver=evidence.resolver,
                    ),
                    BudgetGovernor(ledger, policy),
                ),
            )
            proposal = ModelReviewAdapter(
                dispatcher,
                allow_unknown_quota=allow_unknown_quota,
            ).propose(packet)
            comparison = compare_review_proposal(proposal, codex_decision["decision"], packet)
            selected = next((entry for entry in reversed(dispatcher.audits) if entry.outcome == "succeeded"), None)
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
                    "provider": selected.provider_id if selected is not None else None,
                    "binding": selected.provider_binding_id if selected is not None else None,
                    "model": selected.model_id if selected is not None else None,
                    "intelligence_tier": "L2",
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
                "dispatch_audits": [
                    {
                        "provider": entry.provider_id,
                        "binding": entry.provider_binding_id,
                        "model": entry.model_id,
                        "outcome": entry.outcome,
                    }
                    for entry in dispatcher.audits
                ],
                "allow_unknown_quota": allow_unknown_quota,
            }
        finally:
            ledger.close()


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
    args = parser.parse_args(argv)
    try:
        output = run_shadow(
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
