from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from scripts.diagnose_model_candidates import main
from src.dev_agent.resources.model_evidence import ModelEvidenceCatalog
from src.dev_agent.resources.model_funnel import build_funnel_report
from src.dev_agent.resources.model_runtime import (
    RUNTIME_BOOTSTRAP_ADMITTED,
    RUNTIME_ELIGIBLE,
    RUNTIME_UNKNOWN,
    RuntimeAdmissionSnapshot,
)
from src.dev_agent.resources.qualification import QualificationResolver


NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


def _evidence() -> ModelEvidenceCatalog:
    return ModelEvidenceCatalog(
        catalog=ModelEvidenceCatalog.load_default().catalog.from_document(
            {
                "schema_version": 1,
                "entries": [
                    {
                        "provider_id": "fixture",
                        "provider_binding_id": "fixture:binding-a",
                        "model_id": "model-a",
                        "source": "fixture.discovery",
                        "observed_at": "2026-10-01T00:00:00+00:00",
                        "expires_at": "2026-10-10T00:00:00+00:00",
                    },
                    {
                        "provider_id": "fixture",
                        "provider_binding_id": "fixture:binding-b",
                        "model_id": "model-a",
                        "source": "fixture.discovery",
                        "observed_at": "2026-10-01T00:00:00+00:00",
                        "expires_at": "2026-10-10T00:00:00+00:00",
                    },
                    {
                        "provider_id": "fixture",
                        "provider_binding_id": "fixture:binding-c",
                        "model_id": "model-b",
                        "source": "fixture.discovery",
                        "observed_at": "2026-10-01T00:00:00+00:00",
                        "expires_at": "2026-10-10T00:00:00+00:00",
                    },
                    {
                        "provider_id": "fixture",
                        "provider_binding_id": "fixture:expired",
                        "model_id": "model-a",
                        "source": "fixture.discovery",
                        "observed_at": "2026-09-01T00:00:00+00:00",
                        "expires_at": "2026-10-01T00:00:00+00:00",
                    },
                ],
            }
        ),
        aliases=ModelEvidenceCatalog.load_default().aliases.from_document(
            {
                "schema_version": 1,
                "entries": [
                    {"provider_id": "fixture", "model_id": "model-a", "canonical_model_id": "fixture/model-a"},
                    {"provider_id": "fixture", "model_id": "model-b", "canonical_model_id": "fixture/model-b"},
                ],
            }
        ),
        benchmarks=ModelEvidenceCatalog.load_default().benchmarks.from_document(
            {
                "schema_version": 1,
                "tier_thresholds": {"L1": 0, "L2": 60, "L3": 90},
                "entries": [
                    {
                        "canonical_model_id": "fixture/model-a",
                        "benchmark": "fixture",
                        "benchmark_version": "1",
                        "model_version": "a",
                        "source": "fixture.benchmark",
                        "observed_at": "2026-10-01T00:00:00+00:00",
                        "expires_at": "2026-10-20T00:00:00+00:00",
                        "raw_score": 70,
                        "normalized_score": 70,
                        "confidence": "high",
                        "task_fit": {"planning": 80, "coding": 70},
                    },
                    {
                        "canonical_model_id": "fixture/model-b",
                        "benchmark": "fixture",
                        "benchmark_version": "1",
                        "model_version": "b",
                        "source": "fixture.benchmark",
                        "observed_at": "2026-09-01T00:00:00+00:00",
                        "expires_at": "2026-10-01T00:00:00+00:00",
                        "raw_score": 70,
                        "normalized_score": 70,
                        "confidence": "high",
                        "task_fit": {"planning": 70},
                    },
                ],
            }
        ),
        capabilities=ModelEvidenceCatalog.load_default().capabilities.from_document(
            {
                "schema_version": 1,
                "entries": [
                    {
                        "provider_id": "fixture",
                        "model_id": "model-a",
                        "source": "fixture.capability",
                        "observed_at": "2026-10-01T00:00:00+00:00",
                        "expires_at": "2026-10-20T00:00:00+00:00",
                        "capabilities": ["text"],
                    },
                    {
                        "provider_id": "fixture",
                        "model_id": "model-b",
                        "source": "fixture.capability",
                        "observed_at": "2026-10-01T00:00:00+00:00",
                        "expires_at": "2026-10-20T00:00:00+00:00",
                        "capabilities": ["text"],
                    },
                ],
            }
        ),
    )


def _qualification() -> QualificationResolver:
    return QualificationResolver(
        entries=[
            {
                "provider": "fixture",
                "provider_binding_id": "fixture:binding-a",
                "model": "model-a",
                "intelligence_tier": "L2",
                "tested_at": "2026-10-01T00:00:00+00:00",
                "expires_at": "2026-10-06T00:00:00+00:00",
                "confidence": "high",
                "capabilities": ["text"],
            },
            {
                "provider": "fixture",
                "provider_binding_id": "fixture:binding-b",
                "model": "model-a",
                "intelligence_tier": "L2",
                "tested_at": "2026-10-01T00:00:00+00:00",
                "expires_at": "2026-10-20T00:00:00+00:00",
                "confidence": "low",
                "capabilities": ["text"],
            },
        ]
    )


def _runtime() -> RuntimeAdmissionSnapshot:
    return RuntimeAdmissionSnapshot.from_document(
        {
            "schema_version": 1,
            "observations": [
                {
                    "provider_id": "fixture",
                    "provider_binding_id": "fixture:binding-a",
                    "model_id": "model-a",
                    "status": RUNTIME_BOOTSTRAP_ADMITTED,
                    "observed_at": "2026-10-04T11:00:00+00:00",
                    "source": "fixture.runtime",
                },
                {
                    "provider_id": "fixture",
                    "provider_binding_id": "fixture:binding-b",
                    "model_id": "model-a",
                    "status": RUNTIME_ELIGIBLE,
                    "observed_at": "2026-10-04T11:00:00+00:00",
                    "source": "fixture.runtime",
                },
                {
                    "provider_id": "fixture",
                    "provider_binding_id": "fixture:binding-c",
                    "model_id": "model-b",
                    "status": RUNTIME_UNKNOWN,
                    "observed_at": "2026-10-04T11:00:00+00:00",
                    "source": "fixture.runtime",
                },
            ],
        }
    )


def test_funnel_keeps_exact_identity_and_separates_runtime_bootstrap_from_formal_supply(monkeypatch):
    monkeypatch.setattr("src.dev_agent.resources.model_funnel.profile_for", lambda *_: None)

    report = build_funnel_report(_evidence(), qualification_resolver=_qualification(), runtime_snapshot=_runtime(), now=NOW)

    rows = {row.identity: row for row in report.rows}
    assert rows[("fixture", "fixture:binding-a", "model-a")].runtime_status == RUNTIME_BOOTSTRAP_ADMITTED
    assert rows[("fixture", "fixture:binding-a", "model-a")].formal_supply is False
    assert rows[("fixture", "fixture:binding-a", "model-a")].refresh_state == "EXPIRING"
    assert rows[("fixture", "fixture:binding-c", "model-b")].static_result == "BENCHMARK_MISSING"
    assert rows[("fixture", "fixture:expired", "model-a")].discovery_status == "EXPIRED"
    assert report.supply_for(provider_id="fixture", tier="L2")["status"] == "INSUFFICIENT"
    assert report.supply_for(provider_id="fixture", tier="L2")["independent_route_count"] == 0


def test_funnel_counts_only_explicit_runtime_eligible_routes_and_exposes_role_metrics(monkeypatch):
    monkeypatch.setattr(
        "src.dev_agent.resources.model_funnel.profile_for",
        lambda provider, binding, model: object() if binding == "fixture:binding-b" else None,
    )
    resolver = _qualification()
    # Upgrade only the exact binding used for the formal-supply assertion.
    resolver = QualificationResolver(
        entries=[
            {
                **dict(resolver.catalog.lookup("fixture", "fixture:binding-b", "model-a")),
                "confidence": "high",
            },
            dict(resolver.catalog.lookup("fixture", "fixture:binding-a", "model-a")),
        ]
    )

    report = build_funnel_report(_evidence(), qualification_resolver=resolver, runtime_snapshot=_runtime(), now=NOW)

    assert report.coverage["providers"]["fixture"]["runtime_eligible_count"] == 1
    assert report.coverage["providers"]["fixture"]["runtime_bootstrap_admitted_count"] == 1
    assert report.coverage["providers"]["fixture"]["alias_coverage_count"] == 3
    assert report.coverage["providers"]["fixture"]["benchmark_coverage_count"] == 2
    assert report.coverage["providers"]["fixture"]["capability_coverage_count"] == 3
    assert report.coverage["roles"]["planning"]["runtime_eligible_count"] == 1
    assert report.coverage["tiers"]["L2"]["formal_supply_count"] == 1
    assert report.supply_for(provider_id="fixture", role="planning", tier="L2")["status"] == "INSUFFICIENT"
    assert report.supply_for(provider_id="fixture", role="planning", tier="L2", min_independent_routes=1)["status"] == "SUFFICIENT"


def test_qualification_candidates_are_bounded_deterministic_and_do_not_probe_or_promote(monkeypatch):
    monkeypatch.setattr("src.dev_agent.resources.model_funnel.profile_for", lambda *_: None)

    report = build_funnel_report(
        _evidence(),
        qualification_resolver=_qualification(),
        runtime_snapshot=_runtime(),
        now=NOW,
        candidate_limit=1,
    )

    assert len(report.qualification_candidates) == 1
    assert report.qualification_candidates[0].identity == ("fixture", "fixture:binding-a", "model-a")
    assert report.qualification_candidates[0].formal_supply is False
    assert report.qualification_candidates[0].candidate_reason in {
        "runtime_bootstrap_admitted",
        "qualification_expiring",
        "billing_missing",
    }


def test_diagnostic_summary_exposes_funnel_projection_without_network(monkeypatch, capsys):
    # The default snapshot is enough to verify the CLI contract; this test
    # ensures the funnel is exposed through the existing diagnostic boundary.
    assert main(["--summary", "--json", "--now", NOW.isoformat()]) == 0

    payload = json.loads(capsys.readouterr().out)
    funnel = payload["summary"]["funnel"]
    assert "coverage" in funnel
    assert "providers" in funnel["coverage"]
    assert isinstance(funnel["qualification_candidates"], list)
