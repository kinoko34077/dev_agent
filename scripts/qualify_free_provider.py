"""Run an opt-in live qualification for a free cloud Provider.

The probe uses the canonical Controller -> ProviderDispatcher ->
ProviderRegistry -> ResourceControlPlane path in a temporary SQLite
environment.  It never writes credentials or raw provider payloads to the
repository.  Missing credentials and unsuccessful live checks remain
``blocked_external``/``failed``; they are never converted into Gate evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.domain.protocol import ModelRequest, Task, TaskStatus
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
from src.dev_agent.providers.factory import ProviderDefinition, ProviderFactory
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.billing_catalog import profile_for
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.runtime.controller import Controller
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from src.dev_agent.tools.registry import ToolRegistry, ToolSpec
from src.dev_agent.tools.runtime import ToolRuntime


class FreeProviderQualificationBlocked(RuntimeError):
    """The requested binding/model has no trusted no-charge qualification."""


def _provider(name: str, model: str, timeout_seconds: float, *, binding_id: str | None = None, api_key_env: str | None = None):
    return ProviderFactory().create(
        ProviderDefinition(
            provider_id=name,
            model=model,
            timeout_seconds=timeout_seconds,
            provider_binding_id=binding_id or f"{name}:qualification",
            api_key_env=api_key_env,
        )
    )


def _has_routable_quota_headroom(observation: object) -> bool:
    """Only authoritative remaining/limit telemetry gates a quota domain."""
    if not isinstance(observation, dict):
        return False
    return any(observation.get(field) is not None for field in ("limit", "remaining", "request_remaining", "token_remaining", "daily_remaining"))


def _trusted_free_profile(provider_name: str, model: str, binding_id: str | None = None):
    binding_id = binding_id or f"{provider_name}:qualification"
    profile = profile_for(provider_name, binding_id, model)
    if profile is None or not profile.no_charge_guaranteed:
        raise FreeProviderQualificationBlocked(
            f"provider/model is not in the trusted free catalog: {provider_name}/{binding_id}/{model}"
        )
    return profile


def qualify(*, provider_name: str, model: str, timeout_seconds: float, provider_binding_id: str | None = None, api_key_env: str | None = None, quota_domain: str | None = None) -> dict:
    # Resolve billing before the first network request.  A qualification
    # command must not use the provider name as a proxy for a free tier, since
    # a provider may expose both free and billable models or accounts.
    binding_id = provider_binding_id or f"{provider_name}:qualification"
    profile = _trusted_free_profile(provider_name, model, binding_id)
    with TemporaryDirectory(prefix="dev-agent-free-provider-") as directory:
        root = Path(directory)
        ledger = ResourceLedger(root / "resources.sqlite3")
        try:
            resource_id = f"{provider_name}-free"
            concrete = _provider(provider_name, model, timeout_seconds, binding_id=binding_id, api_key_env=api_key_env)
            # A quota-aware Router correctly refuses a domain with no fresh
            # observation.  Bootstrap the observation from one real, simple
            # provider response; no quota number is fabricated.  The actual
            # tool-call qualification below still uses the canonical
            # Dispatcher path.
            preflight = concrete.request(
                ModelRequest(messages=[{"role": "user", "content": "Reply with the single word ready."}])
            )
            preflight_quota = preflight.usage.get("quota_observation") if isinstance(preflight.usage, dict) else None
            observed_quota_domain = quota_domain or (f"{provider_name}-account" if _has_routable_quota_headroom(preflight_quota) else None)
            ledger.register_resource(
                resource_id,
                provider_id=provider_name,
                native_unit="request",
                capacity=1,
                capabilities=["text", "tool_call"],
                sensitivity="normal",
                cost_minor=profile.cost_minor,
                price_currency=profile.price_currency,
                quota_domain=observed_quota_domain,
                provider_binding_id=getattr(concrete, "provider_binding_id", provider_name),
                    metadata={
                        "provider_binding_id": getattr(concrete, "provider_binding_id", provider_name),
                        "model_id": model,
                        "billing_authority": "trusted_catalog",
                        "billing_mode": profile.billing_mode,
                        "overage_policy": profile.overage_policy,
                        "no_charge_guaranteed": profile.no_charge_guaranteed,
                    },
            )
            ledger.observe(resource_id, available=1, health="healthy", confidence=1.0, concurrency_limit=1)
            if observed_quota_domain is not None:
                ledger.ingest_quota_observation(resource_id, preflight.usage, source="live-preflight")
            # The free qualification has no charge-bearing reservation, but
            # it still exercises the normal budget persistence boundary.
            policy = BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=10)
            protected_config = root / "operator-budget.json"
            protected_config.write_text(
                json.dumps({
                    "hard_cap_minor": policy.hard_cap_minor,
                    "recovery_reserve_minor": policy.recovery_reserve_minor,
                    "currency": policy.currency,
                }),
                encoding="utf-8",
            )
            BudgetAuthority.configure_from_protected_file(ledger, protected_config, agent_root=ROOT)
            governor = BudgetGovernor(ledger, policy)
            control = ResourceControlPlane(ResourceRouter(ledger), governor)
            provider = ProviderDispatcher(ProviderRegistry([concrete]), control)
            tools = ToolRegistry()
            tools.register(
                ToolSpec(
                    name="echo",
                    description="Return the supplied value unchanged.",
                    required_arguments=frozenset({"value"}),
                    input_schema={
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                    },
                    handler=lambda arguments: {"echo": arguments["value"]},
                )
            )
            task = Task(
                objective=(
                    "Call the echo tool exactly once with value 'free-provider-live'. "
                    "After the tool result is returned, provide a short final confirmation."
                ),
                required_capabilities=["text", "tool_call"],
                limits={"max_steps": 3, "max_model_calls": 3, "max_tool_calls": 2, "max_output_tokens": 256},
            )
            with SQLiteStateStore(root / "state.sqlite3") as store:
                result = Controller(provider, ToolRuntime(tools), store).run(task)
                snapshot = store.snapshot()
                audits = store.list_provider_audits(task_id=task.task_id)
                intents = [dict(row) for row in store.connection.execute("SELECT idempotency_key, status FROM effect_intents ORDER BY idempotency_key").fetchall()]
            event_types = [event["event_type"] for event in snapshot["events"] if event.get("task_id") == task.task_id]
            tool_results = [item for item in snapshot["tool_results"].values() if item.get("call_id")]
            quota = ledger.get_quota_observation(resource_id)
            transcript = concrete.transcript_diagnostics(task.task_id) if callable(getattr(concrete, "transcript_diagnostics", None)) else None
            output = {
                "status": result.status.value,
                "provider": provider_name,
                "model": model,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "event_types": event_types,
                "tool_result_count": len(tool_results),
                "provider_audit_count": len(audits),
                "effect_intents": intents,
                "quota_observation": quota,
                "quota_status": (
                    "unknown_not_reported"
                    if not quota
                    else "estimated"
                    if quota.get("authority") == "estimated"
                    else "observed"
                ),
                "billing_authority": "trusted_catalog",
                "gemini_transcript": transcript,
                "budget": governor.snapshot(),
            }
            if result.status != TaskStatus.COMPLETED:
                raise RuntimeError(f"qualification ended in {result.status.value}")
            required = {"model.requested", "model.responded", "tool.completed", "task.completed"}
            missing = sorted(required - set(event_types))
            if missing:
                raise RuntimeError(f"qualification missing events: {', '.join(missing)}")
            if len(tool_results) != 1:
                raise RuntimeError(f"qualification expected one tool result, got {len(tool_results)}")
            if provider_name == "gemini" and model.startswith("gemini-3"):
                if not transcript or transcript["thought_signatures_received"] < 1 or transcript["thought_signatures_replayed"] < 1:
                    raise RuntimeError("qualification missing Gemini 3 thought signature roundtrip")
            return output
        finally:
            ledger.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("gemini", "groq", "cloudflare", "mistral", "openrouter", "ollama_cloud", "vercel"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--binding", dest="provider_binding_id")
    parser.add_argument("--api-key-env", dest="api_key_env", help="credential environment variable name; the value is never stored")
    parser.add_argument("--quota-domain", dest="quota_domain")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--evidence-path", type=Path)
    args = parser.parse_args(argv)
    try:
        output = qualify(provider_name=args.provider, model=args.model, timeout_seconds=args.timeout_seconds, provider_binding_id=args.provider_binding_id, api_key_env=args.api_key_env, quota_domain=args.quota_domain)
        code = 0
    except FreeProviderQualificationBlocked as exc:
        output = {"status": "blocked_external", "category": "untrusted_billing", "message": str(exc)}
        code = 2
    except ProviderError as exc:
        output = {"status": "blocked_external" if exc.category == "authentication" else "failed", "category": exc.category, "message": str(exc)}
        code = 2
    except Exception as exc:
        output = {"status": "failed", "category": type(exc).__name__, "message": str(exc)}
        code = 2
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    print(rendered)
    if args.evidence_path is not None:
        destination = args.evidence_path.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(main())
