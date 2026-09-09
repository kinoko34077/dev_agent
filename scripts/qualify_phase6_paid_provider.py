"""Run an explicitly authorized paid-provider Phase 6 qualification.

This command is intentionally fail-closed.  It never sends a request unless
both ``--allow-billing`` and the exact confirmation phrase are supplied.  The
operator must also provide a protected budget configuration outside the Agent
workspace.  A provider response without an observed ``cost_minor`` is not
treated as a successful qualification: the reservation remains unknown and
the report tells the operator to reconcile it before retrying.
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
from src.dev_agent.providers.gemini import GeminiHttpProvider
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from src.dev_agent.tools.registry import ToolRegistry
from src.dev_agent.tools.runtime import ToolRuntime
from src.dev_agent.runtime.controller import Controller


CONFIRMATION = "I_UNDERSTAND_THIS_MAY_CHARGE"


class PaidQualificationBlocked(RuntimeError):
    """The qualification could not produce proof safe for promotion."""


def require_billing_authorization(*, allow_billing: bool, confirmation: str) -> None:
    if not allow_billing:
        raise PaidQualificationBlocked("paid qualification requires --allow-billing")
    if confirmation != CONFIRMATION:
        raise PaidQualificationBlocked(f"paid qualification requires --confirm {CONFIRMATION}")


def qualify(
    *,
    budget_config: str | Path,
    model: str,
    worst_case_minor: int,
    prompt: str,
    max_output_tokens: int,
    timeout_seconds: float,
    allow_billing: bool,
    confirmation: str,
    agent_root: str | Path = ROOT,
) -> dict[str, object]:
    require_billing_authorization(allow_billing=allow_billing, confirmation=confirmation)
    if isinstance(worst_case_minor, bool) or not isinstance(worst_case_minor, int) or worst_case_minor <= 0:
        raise ValueError("worst_case_minor must be a positive integer")
    if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens <= 0:
        raise ValueError("max_output_tokens must be a positive integer")
    if not prompt.strip():
        raise ValueError("prompt is required")

    with TemporaryDirectory(prefix="dev-agent-phase6-paid-") as directory:
        root = Path(directory)
        ledger = ResourceLedger(root / "resources.sqlite3")
        try:
            BudgetAuthority.configure_from_protected_file(ledger, budget_config, agent_root=agent_root)
            config = ledger.budget_config()
            currency = str(config["currency"])
            ledger.register_resource(
                "paid-gemini",
                provider_id="gemini",
                native_unit="request",
                capacity=1,
                capabilities=["text"],
                sensitivity="normal",
                cost_minor=worst_case_minor,
                price_currency=currency,
            )
            ledger.observe("paid-gemini", available=1, health="healthy", confidence=1.0)
            governor = BudgetGovernor(ledger)
            control = ResourceControlPlane(ResourceRouter(ledger), governor)
            dispatcher = ProviderDispatcher(
                ProviderRegistry([
                    GeminiHttpProvider(model=model, timeout_seconds=timeout_seconds),
                ]),
                control,
            )
            task = Task(
                objective=prompt,
                limits={"max_steps": 2, "max_model_calls": 1, "max_tool_calls": 0, "max_output_tokens": max_output_tokens},
            )
            with SQLiteStateStore(root / "state.sqlite3") as store:
                result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(task)
                snapshot = store.snapshot()
                audits = store.list_provider_audits(task_id=task.task_id)
                intent_rows = [
                    dict(row)
                    for row in store.connection.execute(
                        "SELECT idempotency_key, status, result_payload FROM effect_intents ORDER BY idempotency_key"
                    ).fetchall()
                ]
            reservation_rows = [
                dict(row)
                for row in ledger.connection.execute(
                    "SELECT reservation_id, status, estimated_minor, actual_minor, currency FROM budget_reservations"
                ).fetchall()
            ]
            report: dict[str, object] = {
                "schema_version": 1,
                "qualification": "phase6_paid_provider_dispatch",
                "provider": "gemini",
                "model": model,
                "task_id": task.task_id,
                "status": result.status.value,
                "provider_audits": audits,
                "effect_intents": [
                    {"idempotency_key": row["idempotency_key"], "status": row["status"]}
                    for row in intent_rows
                ],
                "budget_reservations": reservation_rows,
                "budget": governor.snapshot(),
                "cost_observation": "provider_response.usage.cost_minor",
                "scope": "isolated temporary SQLite state; real paid Provider request",
            }
            if result.status is not TaskStatus.COMPLETED:
                if result.status is TaskStatus.WAITING_RECONCILIATION:
                    report["status"] = "blocked_missing_or_ambiguous_cost"
                    report["reconciliation_required"] = True
                raise PaidQualificationBlocked(json.dumps(report, ensure_ascii=False, sort_keys=True))
            if not audits or audits[-1].get("outcome") != "succeeded":
                raise PaidQualificationBlocked("paid qualification completed without a durable succeeded audit")
            return report
        finally:
            ledger.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget-config", type=Path, required=True, help="operator-owned JSON path outside the Agent workspace")
    parser.add_argument("--model", default="gemini-2.5-flash")
    parser.add_argument("--worst-case-minor", type=int, required=True)
    parser.add_argument("--prompt", default="Reply with the single word: qualification.")
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--agent-root", type=Path, default=ROOT)
    parser.add_argument("--allow-billing", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    try:
        report = qualify(
            budget_config=args.budget_config,
            model=args.model,
            worst_case_minor=args.worst_case_minor,
            prompt=args.prompt,
            max_output_tokens=args.max_output_tokens,
            timeout_seconds=args.timeout_seconds,
            allow_billing=args.allow_billing,
            confirmation=args.confirm,
            agent_root=args.agent_root,
        )
    except PaidQualificationBlocked as exc:
        try:
            detail = json.loads(str(exc))
        except json.JSONDecodeError:
            detail = {"status": "blocked", "category": "billing_authorization", "message": str(exc)}
        print(json.dumps(detail, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({"status": "failed", "category": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
