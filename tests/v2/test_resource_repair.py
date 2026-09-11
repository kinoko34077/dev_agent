from datetime import datetime, timezone

from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.budget import BudgetAuthority, BudgetPolicy
from src.dev_agent.resources.qualification import QualificationResolver
from src.dev_agent.resources.repair import apply_resource_repairs, plan_resource_repairs
from recovery.validate_resources import legacy_resource_metadata, validate_resource_ledger


MODEL = "@cf/meta/llama-3.1-8b-instruct"


def _resolver(*, expires_at: str = "2026-10-01T00:00:00+00:00") -> QualificationResolver:
    return QualificationResolver(
        entries=[
            {
                "provider": "cloudflare",
                "provider_binding_id": "cloudflare",
                "model": MODEL,
                "intelligence_tier": "L1",
                "tested_at": "2026-09-01T00:00:00+00:00",
                "expires_at": expires_at,
                "confidence": "high",
                "capabilities": [
                    "text",
                    "model_generated_tool_call",
                    "tool_result_roundtrip",
                    "final_response",
                ],
            }
        ]
    )


def _legacy_resource(ledger: ResourceLedger) -> None:
    ledger.register_resource(
        "cloudflare",
        provider_id="cloudflare",
        provider_binding_id="cloudflare",
        native_unit="request",
        capacity=1,
        capabilities=["text"],
        sensitivity="normal",
        cost_minor=0,
        price_currency="JPY",
        quota_domain="account-1",
        metadata={"provider_binding_id": "cloudflare", "model_id": MODEL},
    )
    ledger.observe("cloudflare", available=1, health="healthy", confidence=0.8)


def test_legacy_resource_is_reported_without_startup_style_mutation(tmp_path):
    path = tmp_path / "resources.sqlite3"
    with ResourceLedger(path) as ledger:
        _legacy_resource(ledger)
        BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0))
        before = ledger.get_resource("cloudflare")

        plan = plan_resource_repairs(
            ledger,
            _resolver(),
            now=datetime(2026, 9, 11, tzinfo=timezone.utc),
        )

        assert len(plan) == 1
        assert plan[0].status == "repairable"
        assert plan[0].before["capabilities"] == ["text"]
        assert "tool_call" in plan[0].after["capabilities"]
        assert ledger.get_resource("cloudflare") == before
        assert legacy_resource_metadata(path) == ["cloudflare"]
        valid, detail = validate_resource_ledger(path)
        assert valid is False
        assert "legacy resource metadata" in detail


def test_explicit_resource_repair_updates_projection_and_records_before_after_audit(tmp_path):
    path = tmp_path / "resources.sqlite3"
    with ResourceLedger(path) as ledger:
        _legacy_resource(ledger)
        result = apply_resource_repairs(
            ledger,
            _resolver(),
            operator_ref="operator:test",
            now=datetime(2026, 9, 11, tzinfo=timezone.utc),
        )

        assert result[0].status == "repaired"
        resource = ledger.get_resource("cloudflare")
        assert resource["capabilities"] == ("text", "tool_call")
        assert resource["metadata"]["qualification_required"] is True
        assert resource["metadata"]["billing_authority"] == "trusted_catalog"
        assert resource["metadata"]["privacy_profile"] == "remote_cloud"
        assert resource["metadata"]["intelligence_tier"] == "L1"
        assert resource["available"] == 1
        assert resource["health"] == "healthy"

        audit = ledger.list_resource_repairs(resource_id="cloudflare")
        assert len(audit) == 1
        assert audit[0]["operator_ref"] == "operator:test"
        assert audit[0]["before"]["capabilities"] == ["text"]
        assert audit[0]["after"]["capabilities"] == ["text", "tool_call"]


def test_resource_repair_fails_closed_for_expired_or_unidentified_resource(tmp_path):
    path = tmp_path / "resources.sqlite3"
    with ResourceLedger(path) as ledger:
        _legacy_resource(ledger)
        ledger.register_resource(
            "missing-identity",
            provider_id="cloudflare",
            native_unit="request",
            capacity=1,
            capabilities=["text"],
        )
        plans = plan_resource_repairs(
            ledger,
            _resolver(expires_at="2026-09-01T00:00:00+00:00"),
            now=datetime(2026, 9, 11, tzinfo=timezone.utc),
        )

        by_id = {item.resource_id: item for item in plans}
        assert by_id["cloudflare"].status == "blocked"
        assert "expired" in by_id["cloudflare"].reason
        assert by_id["missing-identity"].status == "blocked"
        assert "model identity" in by_id["missing-identity"].reason


def test_resource_cli_migrate_requires_explicit_apply_and_operator_audit(tmp_path, capsys):
    from src.dev_agent.cli import main

    resource_path = tmp_path / "resources.sqlite3"
    with ResourceLedger(resource_path) as ledger:
        _legacy_resource(ledger)

    assert main(["resource", "migrate", "--data-dir", str(tmp_path)]) == 0
    assert "\"applied\": false" in capsys.readouterr().out

    assert main(
        [
            "resource",
            "migrate",
            "--data-dir",
            str(tmp_path),
            "--apply",
            "--operator-ref",
            "operator:cli",
        ]
    ) == 0
    assert "\"applied\": true" in capsys.readouterr().out
    with ResourceLedger(resource_path) as ledger:
        assert ledger.list_resource_repairs(resource_id="cloudflare")[0]["operator_ref"] == "operator:cli"
    assert legacy_resource_metadata(resource_path) == []
