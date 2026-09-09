"""Run an opt-in live ProviderDispatcher qualification in an isolated store.

This probe is intentionally separate from the deterministic test suite.  It
exercises the Phase 6 path with a real local Ollama endpoint while retaining
the resulting state, audit, intent, and budget summaries in one JSON record.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.domain.protocol import Task, TaskStatus
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
from src.dev_agent.providers.ollama import OllamaProvider
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.runtime.controller import Controller, RuntimeFailure
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from src.dev_agent.tools.registry import ToolRegistry, ToolSpec
from src.dev_agent.tools.runtime import ToolRuntime


def qualify(*, model: str, base_url: str, timeout_seconds: float) -> dict:
    with TemporaryDirectory(prefix="dev-agent-phase6-provider-") as directory:
        root = Path(directory)
        resource_path = root / "resources.sqlite3"
        state_path = root / "state.sqlite3"
        ledger = ResourceLedger(resource_path)
        try:
            ledger.register_resource(
                "ollama-local",
                provider_id="ollama",
                native_unit="request",
                capacity=1,
                capabilities=["text", "tool_call"],
                sensitivity="normal",
                cost_minor=0,
                price_currency="JPY",
            )
            ledger.observe("ollama-local", available=1, health="healthy", confidence=1.0)
            policy = BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=10)
            # Exercise the deployment-facing configuration boundary even in
            # this isolated qualification.  The temporary operator file is
            # outside the repository root; production must replace it with an
            # ACL/Secret-Store owned path.
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
            provider = ProviderDispatcher(
                ProviderRegistry([OllamaProvider(model=model, base_url=base_url, timeout_seconds=timeout_seconds)]),
                control,
            )
            tools = ToolRegistry()
            tools.register(
                ToolSpec(
                    name="echo",
                    description="Return the supplied value unchanged.",
                    required_arguments=frozenset({"value"}),
                    handler=lambda arguments: {"echo": arguments["value"]},
                )
            )
            task = Task(
                objective=(
                    "Use the echo tool exactly once with value 'phase6-live'. "
                    "After the tool returns, briefly confirm the result."
                ),
                limits={"max_steps": 3, "max_model_calls": 3, "max_tool_calls": 2, "max_output_tokens": 512},
            )
            with SQLiteStateStore(state_path) as store:
                result = Controller(provider, ToolRuntime(tools), store).run(task)
                snapshot = store.snapshot()
                audits = store.list_provider_audits(task_id=task.task_id)
                intents = [dict(row) for row in store.connection.execute("SELECT idempotency_key, status FROM effect_intents ORDER BY idempotency_key").fetchall()]
            event_types = [event["event_type"] for event in snapshot["events"] if event.get("task_id") == task.task_id]
            output = {
                "provider": "ollama",
                "model": model,
                "base_url": base_url,
                "task_id": task.task_id,
                "status": result.status.value,
                "event_types": event_types,
                "tool_result_count": len(snapshot["tool_results"]),
                "provider_audits": audits,
                "effect_intents": intents,
                "budget": governor.snapshot(),
            }
            if result.status != TaskStatus.COMPLETED:
                raise RuntimeFailure(f"live qualification ended in {result.status.value}")
            required = {"model.requested", "model.responded", "tool.completed", "task.completed"}
            missing = sorted(required - set(event_types))
            if missing:
                raise RuntimeFailure(f"live qualification missing events: {', '.join(missing)}")
            return output
        finally:
            ledger.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(qualify(model=args.model, base_url=args.base_url, timeout_seconds=args.timeout_seconds), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"status": "blocked_or_failed", "category": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
