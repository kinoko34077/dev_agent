from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.check_semantic_coherence import (
    build_report,
    check_funnel_invariants,
    check_lifecycle_projection,
    check_provider_normalization,
    check_wait_wake_coverage,
)
from src.dev_agent.resources.model_funnel import FunnelCandidate, FunnelReport
from src.dev_agent.resources.model_runtime import RUNTIME_BOOTSTRAP_ADMITTED, RUNTIME_ELIGIBLE


def _candidate(*, runtime_status: str = RUNTIME_ELIGIBLE, formal_supply: bool = True, task_fit=None):
    return FunnelCandidate(
        provider_id="provider-a",
        provider_binding_id="provider-a:free",
        model_id="model-a",
        discovery_status="CURRENT",
        refresh_state="CURRENT",
        static_result="ELIGIBLE",
        static_gap=None,
        alias_status="PASS",
        benchmark_status="PASS",
        capability_status="PASS",
        canonical_model_id="canonical-a",
        benchmark_tier="L1",
        task_fit={"implementer": 0.9} if task_fit is None else task_fit,
        qualification_status="CURRENT_HIGH_CONFIDENCE",
        qualification_confidence="high",
        qualification_tier="L1",
        qualification_expires_at="2026-10-10T00:00:00+00:00",
        billing_status="CURRENT",
        runtime_status=runtime_status,
        formal_supply=formal_supply,
        candidate_reason=None,
    )


def _report(*rows: FunnelCandidate) -> FunnelReport:
    return FunnelReport(
        rows=tuple(rows),
        qualification_candidates=(),
        coverage={"overall": {"formal_supply_count": sum(row.formal_supply for row in rows)}},
        observed_at="2026-10-04T00:00:00+00:00",
    )


def test_wait_wake_coverage_detects_every_deferred_status():
    result = check_wait_wake_coverage()

    assert result["status"] == "PASS"
    assert result["missing_statuses"] == []
    assert result["unbound_entries"] == []


def test_wait_wake_coverage_fails_when_a_registry_entry_is_removed(monkeypatch):
    import scripts.check_semantic_coherence as coherence

    monkeypatch.delitem(coherence.WAIT_CONDITION_REGISTRY, next(iter(coherence.WAIT_CONDITION_REGISTRY)))

    result = coherence.check_wait_wake_coverage()

    assert result["status"] == "FAIL"
    assert result["missing_statuses"]


def test_funnel_invariants_require_runtime_eligible_for_formal_supply():
    valid = check_funnel_invariants(_report(_candidate()))
    bootstrap = check_funnel_invariants(
        _report(
            _candidate(
                runtime_status=RUNTIME_BOOTSTRAP_ADMITTED,
                formal_supply=True,
            )
        )
    )

    assert valid["status"] == "PASS"
    assert bootstrap["status"] == "FAIL"
    assert any(item.startswith("formal_supply_runtime:") for item in bootstrap["violations"])


def test_funnel_role_supply_uses_exact_role_and_route_identity():
    report = _report(
        _candidate(),
        replace(
            _candidate(),
            provider_binding_id="provider-a:other",
            model_id="model-b",
            task_fit={"reviewer": 0.8},
        ),
    )

    result = check_funnel_invariants(report)

    assert result["status"] == "PASS"
    assert result["role_supply"]["implementer"]["independent_route_count"] == 1
    assert result["role_supply"]["reviewer"]["independent_route_count"] == 1


def test_canonical_lifecycle_projection_rejects_contradictory_observation():
    result = check_lifecycle_projection()

    assert result["status"] == "PASS"
    assert result["contradiction_rejected"] is True
    assert result["projection_pairs_checked"] >= 3


def test_provider_adapters_normalize_equivalent_semantics_without_raw_payload():
    result = check_provider_normalization()

    assert result["status"] == "PASS"
    assert result["providers_checked"] == ["cloudflare", "gemini"]
    assert "raw" not in result
    assert "content" not in result


def test_build_report_is_bounded_and_marks_gate_as_unchanged():
    report = build_report(funnel_report=_report(_candidate()))

    assert report["status"] == "PASS"
    assert report["network_calls"] is False
    assert report["gate_impact"] == "UNCHANGED"
    assert {item["status"] for item in report["checks"]} == {"PASS"}
    assert all(len(str(item)) < 2000 for item in report["checks"])


def test_build_report_fails_closed_for_invalid_funnel_input():
    with pytest.raises(TypeError):
        build_report(funnel_report={})
