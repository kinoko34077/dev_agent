from __future__ import annotations

import pytest

from scripts.diagnose_model_candidates import (
    MAX_DIAGNOSTIC_ROWS,
    diagnose_entries,
    main,
    summarize_entries,
)
from src.dev_agent.resources.model_evidence import ModelEvidenceCatalog


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
        {"provider_id": "gemini", "result": "ELIGIBLE"},
        {"provider_id": "gemini", "result": "BENCHMARK_MISSING"},
        {"provider_id": "cloudflare", "result": "ELIGIBLE"},
    ]

    assert summarize_entries(rows) == {
        "row_count": 3,
        "eligible_count": 2,
        "result_counts": {"BENCHMARK_MISSING": 1, "ELIGIBLE": 2},
        "provider_counts": {"cloudflare": 1, "gemini": 2},
    }


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
    assert row[9] in {"runtime_unknown", "discovery_blocked", "benchmark_blocked", "capability_blocked", "billing_missing", "qualification_missing"}
