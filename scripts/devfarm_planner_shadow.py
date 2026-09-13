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
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.domain.protocol import RiskLevel, Task, TaskStatus, TaskType
from src.dev_agent.intelligence.planner import RootPlanningValidator
from src.dev_agent.intelligence.planner_adapter import ModelPlanningAdapter
from src.dev_agent.operation import OperationProviderBinding
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderPoolExhausted, ProviderRegistry
from src.dev_agent.providers.factory import ProviderDefinition, ProviderFactory
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.billing_catalog import profile_for
from src.dev_agent.resources.control import DispatchDenied, ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.qualification import QualificationResolver
from src.dev_agent.resources.router import ResourceRouter


class PlannerShadowBlocked(RuntimeError):
    """The exact L2 shadow route is not currently admitted."""


class PlannerShadowInputError(ValueError):
    """The shadow command received an invalid local identity input."""


def validate_parent_task_id(value: object) -> str:
    """Validate the existing UUID task identity before provider composition."""

    if not isinstance(value, str) or not value.strip():
        raise PlannerShadowInputError("parent_task_id must be a UUID string")
    normalized = value.strip()
    try:
        UUID(normalized)
    except (ValueError, AttributeError, TypeError) as exc:
        raise PlannerShadowInputError("parent_task_id must be a UUID string") from exc
    return normalized


def _build_provider(*, binding: OperationProviderBinding):
    return ProviderFactory().create(
        ProviderDefinition(
            provider_id=binding.provider_id,
            model=binding.model,
            provider_binding_id=binding.binding_id,
            credential_id=binding.credential_id,
            api_key_env=binding.api_key_env,
            project_id=binding.project_id,
            base_url=binding.base_url,
            timeout_seconds=binding.timeout_seconds,
            intelligence_tier=binding.intelligence_tier,
        )
    )


def _single_binding(
    *,
    provider_id: str,
    binding_id: str,
    model_id: str,
    api_key_env: str,
    quota_domain: str,
    timeout_seconds: float,
) -> OperationProviderBinding:
    return OperationProviderBinding(
        provider_id=provider_id,
        model=model_id,
        provider_binding_id=binding_id,
        api_key_env=api_key_env,
        quota_domain=quota_domain,
        timeout_seconds=timeout_seconds,
    )


def admit_planner_pool(
    bindings: tuple[OperationProviderBinding, ...] | list[OperationProviderBinding],
    *,
    resolver: QualificationResolver,
) -> tuple[tuple[OperationProviderBinding, object, object], ...]:
    """Return only exact, current, high-confidence L2 planner candidates.

    The model name never supplies a tier.  Each identity must resolve through
    the same qualification and billing catalogs used by ResourceRouter.
    """

    admitted: list[tuple[OperationProviderBinding, object, object]] = []
    for binding in bindings:
        qualification = resolver.resolve(binding.provider_id, binding.binding_id, binding.model, min_confidence="high")
        if qualification is None or qualification.intelligence_tier != "L2":
            continue
        profile = profile_for(binding.provider_id, binding.binding_id, binding.model)
        if profile is None or not profile.no_charge_guaranteed:
            continue
        if not binding.quota_domain:
            continue
        admitted.append((binding, qualification, profile))
    return tuple(admitted)


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
    provider_pool: tuple[OperationProviderBinding, ...] | list[OperationProviderBinding] | None = None,
) -> dict[str, object]:
    parent_task_id = validate_parent_task_id(parent_task_id)
    resolver = QualificationResolver()
    bindings = tuple(provider_pool) if provider_pool is not None else (
        _single_binding(
            provider_id=provider_id,
            binding_id=binding_id,
            model_id=model_id,
            api_key_env=api_key_env,
            quota_domain=quota_domain,
            timeout_seconds=timeout_seconds,
        ),
    )
    admitted = admit_planner_pool(bindings, resolver=resolver)
    if not admitted:
        raise PlannerShadowBlocked("no exact current high-confidence L2 planner resource is admitted")

    with TemporaryDirectory(prefix="dev-agent-planner-shadow-") as directory:
        ledger = ResourceLedger(Path(directory) / "resources.sqlite3")
        try:
            concrete = []
            for binding, qualification, profile in admitted:
                concrete.append(_build_provider(binding=binding))
                resource_id = f"planner-shadow:{binding.binding_id}"
                ledger.register_resource(
                    resource_id,
                    provider_id=binding.provider_id,
                    provider_binding_id=binding.binding_id,
                    native_unit="request",
                    capacity=1,
                    capabilities=sorted(qualification.routing_capabilities),
                    sensitivity="normal",
                    cost_minor=profile.cost_minor,
                    price_currency=profile.price_currency,
                    quota_domain=binding.quota_domain,
                    intelligence_tier=qualification.intelligence_tier,
                    metadata={
                        "provider_binding_id": binding.binding_id,
                        "model_id": binding.model,
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
                ledger.observe(resource_id, available=1, health="healthy", concurrency_limit=1)
            policy = BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0)
            BudgetAuthority.configure(ledger, policy)
            control = ResourceControlPlane(
                ResourceRouter(ledger, qualification_resolver=resolver),
                BudgetGovernor(ledger, policy),
            )
            dispatcher = ProviderDispatcher(ProviderRegistry(concrete), control)
            try:
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
            except ProviderPoolExhausted as exc:
                return {
                    "status": "pool_exhausted",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "eligible_pool": [
                        {
                            "provider": binding.provider_id,
                            "binding": binding.binding_id,
                            "model": binding.model,
                            "quota_domain": binding.quota_domain,
                            "intelligence_tier": qualification.intelligence_tier,
                        }
                        for binding, qualification, _profile in admitted
                    ],
                    "attempts": list(exc.attempts),
                    "host_validation": "not_run",
                    "allow_unknown_quota": allow_unknown_quota,
                }
            except ProviderError as exc:
                # A singleton or otherwise bounded pool can surface its last
                # confirmed failover-safe error directly for compatibility.
                # Convert that result into the same structured pool outcome;
                # unknown/reconciliation errors still escape unchanged.
                if not exc.failover_safe or exc.requires_reconciliation:
                    raise
                attempts = [
                    {
                        "provider_id": entry.provider_id,
                        "binding_id": entry.provider_binding_id or entry.provider_id,
                        "model_id": entry.model_id,
                        "category": entry.outcome,
                        "failover_safe": True,
                        "reconciliation_required": False,
                    }
                    for entry in dispatcher.audits
                    if entry.outcome != "succeeded"
                ]
                if not attempts:
                    raise
                return {
                    "status": "pool_exhausted",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "eligible_pool": [
                        {
                            "provider": binding.provider_id,
                            "binding": binding.binding_id,
                            "model": binding.model,
                            "quota_domain": binding.quota_domain,
                            "intelligence_tier": qualification.intelligence_tier,
                        }
                        for binding, qualification, _profile in admitted
                    ],
                    "attempts": attempts,
                    "host_validation": "not_run",
                    "allow_unknown_quota": allow_unknown_quota,
                }
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
            quota_observations = [
                observation
                for binding, _qualification, _profile in admitted
                for observation in ledger.list_quota_observations(quota_domain=binding.quota_domain)
            ]
            successful = [entry for entry in dispatcher.audits if entry.outcome == "succeeded"]
            selected = successful[-1] if successful else None
            selected_provider = selected.provider_id if selected is not None else None
            selected_binding = selected.provider_binding_id if selected is not None else None
            selected_model = selected.model_id if selected is not None else None
            proposal_digest = hashlib.sha256(
                json.dumps(proposal.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            return {
                "status": "live_shadow_validated",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "provider": selected_provider,
                "binding": selected_binding,
                "model": selected_model,
                "intelligence_tier": "L2",
                "qualification_confidence": "high",
                "parent_task_id": parent_task_id,
                "proposal_id": proposal.proposal_id,
                "child_count": len(accepted),
                "child_keys": [child.child_key for child in accepted],
                "child_owners": [child.suggested_owner for child in accepted],
                "quota_status": "unknown_not_reported" if not quota_observations else "observed",
                "allow_unknown_quota": allow_unknown_quota,
                "host_validation": "passed",
                "eligible_pool": [
                    {
                        "provider": binding.provider_id,
                        "binding": binding.binding_id,
                        "model": binding.model,
                        "quota_domain": binding.quota_domain,
                        "intelligence_tier": qualification.intelligence_tier,
                    }
                    for binding, qualification, _profile in admitted
                ],
                "dispatch_audits": [
                    {
                        "provider": entry.provider_id,
                        "binding": entry.provider_binding_id,
                        "model": entry.model_id,
                        "outcome": entry.outcome,
                    }
                    for entry in dispatcher.audits
                ],
                "proposal_sha256": proposal_digest,
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
        "--pool-json",
        help=(
            "explicit JSON array of non-secret OperationProviderBinding objects; "
            "when supplied, exact current high-confidence L2 entries are admitted and routed as a pool"
        ),
    )
    parser.add_argument(
        "--allow-unknown-quota",
        action="store_true",
        help="explicitly use the bounded local admission path when provider quota telemetry is absent",
    )
    args = parser.parse_args(argv)
    try:
        provider_pool = None
        if args.pool_json is not None:
            try:
                raw_pool = json.loads(args.pool_json)
            except json.JSONDecodeError as exc:
                raise PlannerShadowInputError("pool-json must be valid JSON") from exc
            if not isinstance(raw_pool, list) or not raw_pool:
                raise PlannerShadowInputError("pool-json must be a non-empty JSON array")
            try:
                provider_pool = tuple(OperationProviderBinding(**dict(entry)) for entry in raw_pool)
            except (TypeError, ValueError) as exc:
                raise PlannerShadowInputError(f"pool-json contains an invalid binding: {exc}") from exc
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
            provider_pool=provider_pool,
        )
        code = 0 if output.get("status") == "live_shadow_validated" else 2
    except PlannerShadowInputError as exc:
        output = {"status": "invalid_input", "category": type(exc).__name__, "message": str(exc)}
        code = 2
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
