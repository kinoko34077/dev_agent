"""Run one opt-in, proposal-only Free L2 Planner shadow request.

This development utility composes the existing QualificationResolver,
Billing catalog, ResourceRouter, BudgetAuthority, ProviderDispatcher, and
ModelPlanningAdapter.  It never creates Tasks, writes a Commander plan, or
dispatches implementation work.  The provider response is accepted only as
a typed proposal; Host validation remains a separate authority boundary.

The default Gemini binding has no provider quota telemetry in the current
qualification evidence.  ``--allow-unknown-quota`` is therefore explicit and
uses only the existing bounded local admission path; it does not invent quota
headroom or turn a paid/unknown billing profile into a free route.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.domain.protocol import RiskLevel, Task, TaskStatus, TaskType
from src.dev_agent.intelligence.planner import RootPlanningValidator
from src.dev_agent.intelligence.planner_adapter import ModelPlanningAdapter
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
from src.dev_agent.providers.factory import ProviderDefinition, ProviderFactory
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.billing_catalog import profile_for
from src.dev_agent.resources.control import DispatchDenied, ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.qualification import QualificationResolver
from src.dev_agent.resources.router import ResourceRouter


class PlannerShadowBlocked(RuntimeError):
    """The exact L2 shadow route is not currently admitted."""


def _build_provider(*, provider_id: str, binding_id: str, model_id: str, api_key_env: str, timeout_seconds: float):
    return ProviderFactory().create(
        ProviderDefinition(
            provider_id=provider_id,
            model=model_id,
            provider_binding_id=binding_id,
            api_key_env=api_key_env,
            timeout_seconds=timeout_seconds,
        )
    )


def _shadow_context(*, repository: str, branch: str) -> dict[str, str]:
    return {
        "repository": repository,
        "branch": branch,
        "source": "live_planner_shadow",
        "authority": "host_validation_required",
    }


def run_shadow(
    *,
    objective: str,
    parent_task_id: str,
    provider_id: str,
    binding_id: str,
    model_id: str,
    api_key_env: str,
    quota_domain: str,
    timeout_seconds: float,
    allow_unknown_quota: bool,
    repository: str,
    branch: str,
) -> dict[str, object]:
    resolver = QualificationResolver()
    qualification = resolver.resolve(provider_id, binding_id, model_id, min_confidence="high")
    if qualification is None or qualification.intelligence_tier != "L2":
        raise PlannerShadowBlocked("exact current high-confidence L2 qualification is unavailable")
    profile = profile_for(provider_id, binding_id, model_id)
    if profile is None or not profile.no_charge_guaranteed:
        raise PlannerShadowBlocked("exact binding/model lacks a current trusted no-charge guarantee")

    with TemporaryDirectory(prefix="dev-agent-planner-shadow-") as directory:
        ledger = ResourceLedger(Path(directory) / "resources.sqlite3")
        try:
            concrete = _build_provider(
                provider_id=provider_id,
                binding_id=binding_id,
                model_id=model_id,
                api_key_env=api_key_env,
                timeout_seconds=timeout_seconds,
            )
            ledger.register_resource(
                "planner-shadow-resource",
                provider_id=provider_id,
                provider_binding_id=binding_id,
                native_unit="request",
                capacity=1,
                capabilities=sorted(qualification.routing_capabilities),
                sensitivity="normal",
                cost_minor=profile.cost_minor,
                price_currency=profile.price_currency,
                quota_domain=quota_domain,
                intelligence_tier=qualification.intelligence_tier,
                metadata={
                    "provider_binding_id": binding_id,
                    "model_id": model_id,
                    "intelligence_tier": qualification.intelligence_tier,
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
            ledger.observe("planner-shadow-resource", available=1, health="healthy", concurrency_limit=1)
            policy = BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0)
            BudgetAuthority.configure(ledger, policy)
            control = ResourceControlPlane(
                ResourceRouter(ledger, qualification_resolver=resolver),
                BudgetGovernor(ledger, policy),
            )
            dispatcher = ProviderDispatcher(ProviderRegistry([concrete]), control)
            proposal = ModelPlanningAdapter(
                dispatcher,
                max_output_tokens=1_024,
                cost_ceiling=0.0,
                allow_unknown_quota=allow_unknown_quota,
            ).propose(
                parent_task_id=parent_task_id,
                objective=objective,
                sensitivity="normal",
                required_intelligence_tier="L2",
                context_references=_shadow_context(repository=repository, branch=branch),
            )
            # Validate the model result with the existing host-only planner
            # validator, but do not persist or apply it.
            parent = Task(
                task_id=parent_task_id,
                objective=objective,
                status=TaskStatus.READY,
                task_type=TaskType.REASONING,
                risk=RiskLevel.NORMAL,
                sensitivity="normal",
            )
            accepted = RootPlanningValidator.validate(parent, proposal)
            quota_observations = ledger.list_quota_observations(quota_domain=quota_domain)
            return {
                "status": "live_shadow_validated",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "provider": provider_id,
                "binding": binding_id,
                "model": model_id,
                "intelligence_tier": qualification.intelligence_tier,
                "qualification_confidence": qualification.confidence,
                "parent_task_id": parent_task_id,
                "proposal_id": proposal.proposal_id,
                "child_count": len(accepted),
                "child_keys": [child.child_key for child in accepted],
                "child_owners": [child.suggested_owner for child in accepted],
                "quota_status": "unknown_not_reported" if not quota_observations else "observed",
                "allow_unknown_quota": allow_unknown_quota,
                "host_validation": "passed",
                "proposal": proposal.to_dict(),
            }
        finally:
            ledger.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--parent-task-id")
    parser.add_argument("--provider", default="gemini")
    parser.add_argument("--binding", default="gemini:core")
    parser.add_argument("--model", default="gemini-3.8-flash")
    parser.add_argument("--api-key-env", default="GEMINI_API_KEY")
    parser.add_argument("--quota-domain", default="gemini-core-account")
    parser.add_argument("--repository", default="kinoko34077/dev_agent")
    parser.add_argument("--branch", default="v2/bootstrap")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--allow-unknown-quota",
        action="store_true",
        help="explicitly use the bounded local admission path when provider quota telemetry is absent",
    )
    args = parser.parse_args(argv)
    try:
        output = run_shadow(
            objective=args.objective,
            parent_task_id=args.parent_task_id or str(uuid4()),
            provider_id=args.provider,
            binding_id=args.binding,
            model_id=args.model,
            api_key_env=args.api_key_env,
            quota_domain=args.quota_domain,
            timeout_seconds=args.timeout_seconds,
            allow_unknown_quota=args.allow_unknown_quota,
            repository=args.repository,
            branch=args.branch,
        )
        code = 0
    except (PlannerShadowBlocked, DispatchDenied, ProviderError) as exc:
        output = {"status": "blocked_external", "category": type(exc).__name__, "message": str(exc)}
        code = 2
    except Exception as exc:
        output = {"status": "failed", "category": type(exc).__name__, "message": str(exc)}
        code = 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
