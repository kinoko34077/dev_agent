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
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.domain.protocol import RiskLevel, Task, TaskStatus, TaskType
from src.dev_agent.intelligence.planner import RootPlanningValidator
from src.dev_agent.intelligence.planner_adapter import ModelPlanningAdapter, PlanningResponseError
from src.dev_agent.operation import OperationProviderBinding
from src.dev_agent.providers.base import ProviderError, transport_failure_metadata
from src.dev_agent.providers.dispatch import ProviderPoolExhausted
from src.dev_agent.providers.host_dispatch import route_through_host
from src.dev_agent.resources.billing_catalog import profile_for
from src.dev_agent.resources.control import DispatchDenied
from src.dev_agent.resources.model_admission import ModelAdmissionResolver
from src.dev_agent.resources.model_evidence import ModelEvidenceCatalog
from src.dev_agent.resources.qualification import QualificationResolver
from src.dev_agent.resources.router import ResourceRouter
from scripts.devfarm_resource_pool import (
    ResourcePoolError,
    admit_resource_pool,
    build_provider,
    compose_resource_pool,
    make_binding,
    resolve_provider_pool as resolve_shared_provider_pool,
)
from scripts.devfarm_host_dispatch import create_host_process_executor


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


def _bounded_request_id(value: object) -> str | None:
    """Return only a canonical UUID for bounded failure correlation."""

    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _build_provider(*, binding: OperationProviderBinding):
    """Compatibility seam for isolated tests; runtime composition is shared."""

    return build_provider(binding=binding)


def _single_binding(
    *,
    provider_id: str,
    binding_id: str,
    model_id: str,
    api_key_env: str,
    quota_domain: str,
    timeout_seconds: float,
) -> OperationProviderBinding:
    return make_binding(
        provider_id=provider_id,
        binding_id=binding_id,
        model_id=model_id,
        api_key_env=api_key_env,
        quota_domain=quota_domain,
        timeout_seconds=timeout_seconds,
    )


def admit_planner_pool(
    bindings: tuple[OperationProviderBinding, ...] | list[OperationProviderBinding],
    *,
    resolver: QualificationResolver,
    model_admission_resolver: ModelAdmissionResolver | None = None,
    model_catalog=None,
    expand_discovered_models: bool = False,
    now: datetime | None = None,
) -> tuple[tuple[OperationProviderBinding, object, object], ...]:
    """Compatibility wrapper for the role-neutral development admission API."""

    try:
        return admit_resource_pool(
            bindings,
            resolver=resolver,
            required_tier="L2",
            no_charge_required=True,
            model_admission_resolver=model_admission_resolver,
            model_catalog=model_catalog,
            expand_discovered_models=expand_discovered_models,
            now=now,
            profile_resolver=profile_for,
        )
    except ResourcePoolError as exc:
        raise PlannerShadowBlocked(str(exc)) from exc


def _shadow_context(*, repository: str, branch: str) -> dict[str, str]:
    return {
        "repository": repository,
        "branch": branch,
        "source": "live_planner_shadow",
        "authority": "host_validation_required",
    }


def resolve_provider_pool(
    *,
    pool_json: str | None,
    use_configured_pool: bool,
    env=None,
) -> tuple[OperationProviderBinding, ...] | None:
    """Resolve one explicit Planner pool source without activating resources.

    The normal singleton invocation remains unchanged.  A configured pool is
    available only through an explicit flag, while qualification, billing,
    privacy, quota, and health admission still happen in ``run_shadow``.
    """

    try:
        kwargs = {"pool_json": pool_json, "use_configured_pool": use_configured_pool}
        if env is not None:
            kwargs["env"] = env
        return resolve_shared_provider_pool(**kwargs)
    except ResourcePoolError as exc:
        raise PlannerShadowInputError(str(exc)) from exc


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
    model_admission_resolver: ModelAdmissionResolver | None = None,
    model_catalog=None,
    expand_discovered_models: bool = False,
    execution_boundary: str = "in_process",
    max_output_tokens: int = 1_024,
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
    admitted = admit_planner_pool(
        bindings,
        resolver=resolver,
        model_admission_resolver=model_admission_resolver,
        model_catalog=model_catalog,
        expand_discovered_models=expand_discovered_models,
    )
    if not admitted:
        raise PlannerShadowBlocked("no exact current high-confidence L2 planner resource is admitted")
    if execution_boundary not in {"in_process", "host_process"}:
        raise PlannerShadowInputError("execution_boundary must be in_process or host_process")

    def _effective_tier(binding: OperationProviderBinding, qualification: object) -> str | None:
        if model_admission_resolver is not None:
            admission = model_admission_resolver.resolve(binding.provider_id, binding.credential_binding_id, binding.model)
            return None if admission is None else admission.intelligence_tier
        return getattr(qualification, "intelligence_tier", None)

    with compose_resource_pool(
        admitted,
        resolver=resolver,
        model_admission_resolver=model_admission_resolver,
        resource_id_prefix="planner-shadow",
        provider_builder=_build_provider,
        router_factory=ResourceRouter,
    ) as resource_pool:
        ledger = resource_pool.ledger
        dispatcher = resource_pool.dispatcher
        planner_provider = dispatcher
        if execution_boundary == "host_process":
            planner_provider = route_through_host(
                dispatcher,
                create_host_process_executor(
                    ROOT / ".devfarm" / "host-dispatch",
                    timeout_seconds=timeout_seconds,
                ),
            )
        try:
            proposal = ModelPlanningAdapter(
                planner_provider,
                max_output_tokens=max_output_tokens,
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
            result = {
                "status": "pool_exhausted",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "eligible_pool": [
                    {
                        "provider": binding.provider_id,
                        "binding": binding.binding_id,
                        "model": binding.model,
                        "quota_domain": binding.quota_domain,
                        "intelligence_tier": _effective_tier(binding, qualification),
                    }
                    for binding, qualification, _profile in admitted
                ],
                "attempts": list(exc.attempts),
                "host_validation": "not_run",
                "allow_unknown_quota": allow_unknown_quota,
            }
            request_id = _bounded_request_id(getattr(exc, "request_id", None))
            if request_id is not None:
                result["request_id"] = request_id
            return result
        except ProviderError as exc:
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
            result = {
                "status": "pool_exhausted",
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "eligible_pool": [
                    {
                        "provider": binding.provider_id,
                        "binding": binding.binding_id,
                        "model": binding.model,
                        "quota_domain": binding.quota_domain,
                        "intelligence_tier": _effective_tier(binding, qualification),
                    }
                    for binding, qualification, _profile in admitted
                ],
                "attempts": attempts,
                "host_validation": "not_run",
                "allow_unknown_quota": allow_unknown_quota,
            }
            request_id = _bounded_request_id(getattr(exc, "request_id", None))
            if request_id is not None:
                result["request_id"] = request_id
            return result
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
                    "intelligence_tier": _effective_tier(binding, qualification),
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
        "--max-output-tokens",
        type=int,
        default=1_024,
        help="bounded Planner response ceiling; increase only for larger validated proposals",
    )
    parser.add_argument(
        "--pool-json",
        help=(
            "explicit JSON array of non-secret OperationProviderBinding objects; "
            "when supplied, exact current high-confidence L2 entries are admitted and routed as a pool"
        ),
    )
    parser.add_argument(
        "--configured-pool",
        action="store_true",
        help="explicitly use configured non-secret Provider bindings as the Planner pool",
    )
    parser.add_argument(
        "--allow-unknown-quota",
        action="store_true",
        help="explicitly use the bounded local admission path when provider quota telemetry is absent",
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
    args = parser.parse_args(argv)
    parent_task_id = args.parent_task_id or str(uuid4())
    try:
        # Normal live invocation always uses the reviewed static evidence
        # snapshot.  It is loaded here, never at module import time, and does
        # not call a discovery/benchmark service during planner dispatch.
        model_evidence = ModelEvidenceCatalog.load_default()
        model_admission_resolver = model_evidence.resolver
        provider_pool = resolve_provider_pool(
            pool_json=args.pool_json,
            use_configured_pool=args.configured_pool,
        )
        output = run_shadow(
            objective=args.objective,
            parent_task_id=parent_task_id,
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
            model_admission_resolver=model_admission_resolver,
            model_catalog=model_evidence.catalog,
            expand_discovered_models=args.expand_discovered_models,
            execution_boundary=args.execution_boundary,
            max_output_tokens=args.max_output_tokens,
        )
        code = 0 if output.get("status") == "live_shadow_validated" else 2
    except PlannerShadowInputError as exc:
        output = {"status": "invalid_input", "category": type(exc).__name__, "message": str(exc)}
        code = 2
    except PlanningResponseError as exc:
        message = str(exc).lower()
        output = {
            "status": "failed",
            "category": "model_output_invalid",
            "adapter_error": type(exc).__name__,
            "provider_response_observed": getattr(exc, "provider_response_observed", False) is True,
            "response_contract": "invalid_json" if "json" in message else "invalid_proposal",
            "reconciliation_required": False,
        }
        request_id = _bounded_request_id(getattr(exc, "request_id", None))
        if request_id is not None:
            output["request_id"] = request_id
        code = 2
    except (PlannerShadowBlocked, DispatchDenied, ProviderError) as exc:
        output = {
            "status": "blocked_external",
            "category": getattr(exc, "host_failure_category", getattr(exc, "category", type(exc).__name__)),
            "error_type": type(exc).__name__,
            "reconciliation_required": getattr(exc, "requires_reconciliation", False),
        }
        host_failure_type = getattr(exc, "host_failure_type", None)
        if isinstance(host_failure_type, str):
            output["host_failure_type"] = host_failure_type
        transport_failure_category = getattr(exc, "transport_failure_category", None)
        if isinstance(transport_failure_category, str):
            output["transport_failure_category"] = transport_failure_category
        output.update(transport_failure_metadata(exc))
        request_id = _bounded_request_id(getattr(exc, "request_id", None))
        if request_id is not None:
            output["request_id"] = request_id
        code = 2
    except Exception as exc:
        # An unexpected failure may have happened before or after the
        # concrete Provider boundary.  Keep the outer projection fail-closed
        # and require reconciliation rather than exposing exception text or
        # guessing that the request was harmless.
        output = {
            "status": "failed",
            "category": "planner_shadow_failure",
            "error_type": type(exc).__name__,
            "reconciliation_required": True,
        }
        code = 2
    output.setdefault("parent_task_id", parent_task_id)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
