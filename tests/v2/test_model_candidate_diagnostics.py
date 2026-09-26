from __future__ import annotations

import pytest

from scripts.diagnose_model_candidates import (
    MAX_DIAGNOSTIC_ROWS,
    catalog_coverage,
    diagnose_entries,
    main,
    summarize_entries,
    summarize_candidate_evidence,
)
from src.dev_agent.resources.model_evidence import ModelEvidenceCatalog
from src.dev_agent.resources.model_runtime import (
    RUNTIME_NOT_PROBED,
    RUNTIME_UNAVAILABLE,
    RuntimeAdmissionSnapshot,
)


@pytest.fixture(scope="module")
def model_evidence() -> ModelEvidenceCatalog:
    return ModelEvidenceCatalog.load_default()


def test_diagnose_entries_accepts_the_bounded_full_inventory_limit(model_evidence):
    rows = diagnose_entries(model_evidence, limit=MAX_DIAGNOSTIC_ROWS)

    assert rows


def test_diagnose_entries_rejects_limits_above_the_bounded_inventory_cap(model_evidence):
    with pytest.raises(ValueError, match="1 to"):
        diagnose_entries(model_evidence, limit=MAX_DIAGNOSTIC_ROWS + 1)


def test_summarize_entries_returns_only_bounded_aggregate_gate_counts():
    rows = [
        {"provider_id": "gemini", "static_result": "ELIGIBLE", "result": "ELIGIBLE"},
        {"provider_id": "gemini", "static_result": "BENCHMARK_MISSING", "result": "BENCHMARK_MISSING"},
        {"provider_id": "cloudflare", "static_result": "ELIGIBLE", "result": "ELIGIBLE"},
    ]

    assert summarize_entries(rows) == {
        "row_count": 3,
        "eligible_count": 2,
        "runtime_eligible_count": 2,
        "static_eligible_count": 2,
        "runtime_unknown_count": 0,
        "runtime_not_probed_count": 0,
        "runtime_unavailable_count": 0,
        "result_counts": {"BENCHMARK_MISSING": 1, "ELIGIBLE": 2},
        "static_result_counts": {"BENCHMARK_MISSING": 1, "ELIGIBLE": 2},
        "provider_counts": {"cloudflare": 1, "gemini": 2},
    }


def test_summarize_entries_separates_static_candidates_from_runtime_unknown():
    summary = summarize_entries(
        [
            {
                "provider_id": "gemini",
                "static_result": "ELIGIBLE",
                "result": "RUNTIME_UNKNOWN",
            },
            {
                "provider_id": "gemini",
                "static_result": "BENCHMARK_MISSING",
                "result": "BENCHMARK_MISSING",
            },
        ]
    )

    assert summary["eligible_count"] == 0
    assert summary["static_eligible_count"] == 1
    assert summary["runtime_unknown_count"] == 1


def test_static_candidate_without_runtime_snapshot_is_not_probed(model_evidence):
    rows = diagnose_entries(model_evidence, limit=MAX_DIAGNOSTIC_ROWS)

    statically_eligible = [row for row in rows if row["static_result"] == "ELIGIBLE"]
    not_probed = [row for row in statically_eligible if row["result"] == RUNTIME_NOT_PROBED]
    assert not_probed
    assert {row["runtime"] for row in not_probed} == {RUNTIME_NOT_PROBED}


def test_explicit_runtime_snapshot_distinguishes_unavailable_from_not_probed(model_evidence):
    candidate = next(row for row in diagnose_entries(model_evidence, limit=MAX_DIAGNOSTIC_ROWS) if row["result"] == RUNTIME_NOT_PROBED)
    snapshot = RuntimeAdmissionSnapshot.from_document(
        {
            "schema_version": 1,
            "observations": [
                {
                    "provider_id": candidate["provider_id"],
                    "provider_binding_id": candidate["provider_binding_id"],
                    "model_id": candidate["model_id"],
                    "status": RUNTIME_UNAVAILABLE,
                    "observed_at": "2026-09-27T00:00:00+00:00",
                    "source": "existing-resource-authority",
                }
            ],
        }
    )
    rows = diagnose_entries(model_evidence, runtime_snapshot=snapshot, limit=MAX_DIAGNOSTIC_ROWS)
    observed = next(row for row in rows if row["provider_binding_id"] == candidate["provider_binding_id"] and row["model_id"] == candidate["model_id"])
    assert observed["result"] == RUNTIME_UNAVAILABLE
    assert observed["gate_reason"] == "runtime_unavailable"


def test_runtime_snapshot_rejects_duplicate_exact_identity():
    with pytest.raises(ValueError, match="duplicate"):
        RuntimeAdmissionSnapshot.from_document(
            {
                "schema_version": 1,
                "observations": [
                    {
                        "provider_id": "gemini",
                        "provider_binding_id": "gemini:worker:free-3",
                        "model_id": "gemini-3.8-flash",
                        "status": RUNTIME_UNAVAILABLE,
                        "observed_at": "2026-09-27T00:00:00+00:00",
                        "source": "fixture",
                    },
                    {
                        "provider_id": "gemini",
                        "provider_binding_id": "gemini:worker:free-3",
                        "model_id": "gemini-3.8-flash",
                        "status": RUNTIME_UNAVAILABLE,
                        "observed_at": "2026-09-27T00:00:01+00:00",
                        "source": "fixture",
                    },
                ],
            }
        )


def test_candidate_evidence_exposes_not_promoted_reasons():
    summary = summarize_candidate_evidence(
        {
            "host_discovery": {"candidate_entry_count": 1357, "refreshed_binding_count": 13},
            "canonical_admission_snapshot": {"candidate_not_merged": True, "runtime_eligible_count": 0},
            "generation": {"attempted": False, "paid_route_added": False},
        }
    )
    assert summary == {
        "status": "NOT_PROMOTED",
        "candidate_entry_count": 1357,
        "refreshed_binding_count": 13,
        "reasons": ["candidate_not_merged", "generation_not_attempted", "no_runtime_eligible_route", "paid_route_not_added"],
    }


def test_catalog_coverage_reports_stale_storage_separately(model_evidence):
    coverage = catalog_coverage(model_evidence)
    assert coverage["stored_catalog_count"] >= coverage["current_catalog_count"]
    assert "stale_or_expired_count" in coverage
    assert isinstance(coverage["stale_or_expired_provider_binding_counts"], dict)


def test_main_tabular_output_exposes_runtime_and_gate_reason(model_evidence, capsys):
    assert main(["--limit", "1"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) >= 2
    header = lines[0].split("\t")
    row = lines[1].split("\t")

    assert header == [
        "provider",
        "binding",
        "model",
        "discovery",
        "benchmark",
        "capability",
        "billing",
        "qualification",
        "runtime",
        "gate_reason",
        "result",
    ]
    assert len(row) == len(header)
    assert row[9] in {"runtime_not_probed", "runtime_unknown", "runtime_unavailable", "discovery_blocked", "benchmark_blocked", "capability_blocked", "billing_missing", "qualification_missing"}

