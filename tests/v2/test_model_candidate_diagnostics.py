from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import scripts.diagnose_model_candidates as diagnostics
from scripts.diagnose_model_candidates import (
    MAX_DIAGNOSTIC_ROWS,
    catalog_coverage,
    diagnose_entries,
    main,
    summarize_entries,
    summarize_candidate_evidence,
)
from src.dev_agent.resources.model_admission import STATIC_MODEL_UNSUPPORTED
from src.dev_agent.resources.model_benchmarks import BenchmarkCatalog
from src.dev_agent.resources.model_capabilities import ModelCapabilityCatalog
from src.dev_agent.resources.model_catalog import ModelAliasCatalog, ModelCatalog
from src.dev_agent.resources.model_evidence import ModelEvidenceCatalog
from src.dev_agent.resources.model_runtime import (
    RUNTIME_NOT_PROBED,
    RUNTIME_UNAVAILABLE,
    RuntimeAdmissionSnapshot,
)


# Keep the deterministic diagnostic clock inside the current reviewed evidence
# window.  The canonical evidence refresh is intentionally time-bounded; a
# clock before the refresh's observed_at would make every row look expired and
# hide the runtime-not-probed cases these tests exercise.
DIAGNOSTIC_NOW = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)


class _CurrentQualification:
    """Keep diagnostic tests focused on runtime state, independent of expiry."""

    def resolve(self, *_args, **_kwargs):
        return object()


@pytest.fixture(scope="module")
def model_evidence() -> ModelEvidenceCatalog:
    return ModelEvidenceCatalog.load_default()


def test_diagnose_entries_accepts_the_bounded_full_inventory_limit(model_evidence):
    rows = diagnose_entries(model_evidence, limit=MAX_DIAGNOSTIC_ROWS, now=DIAGNOSTIC_NOW)

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
        "runtime_bootstrap_admitted_count": 0,
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


@pytest.mark.current_billing_catalog
def test_static_candidate_without_runtime_snapshot_is_not_probed(model_evidence):
    rows = diagnose_entries(
        model_evidence,
        qualification_resolver=_CurrentQualification(),
        limit=MAX_DIAGNOSTIC_ROWS,
        now=DIAGNOSTIC_NOW,
    )

    statically_eligible = [row for row in rows if row["static_result"] == "ELIGIBLE"]
    not_probed = [row for row in statically_eligible if row["result"] == RUNTIME_NOT_PROBED]
    assert not_probed
    assert {row["runtime"] for row in not_probed} == {RUNTIME_NOT_PROBED}


@pytest.mark.current_billing_catalog
def test_missing_benchmark_does_not_replace_runtime_not_probed_reason(model_evidence):
    rows = diagnose_entries(
        model_evidence,
        qualification_resolver=_CurrentQualification(),
        limit=MAX_DIAGNOSTIC_ROWS,
        now=DIAGNOSTIC_NOW,
    )

    row = next(
        row
        for row in rows
        if row["benchmark"] == "MISSING" and row["result"] == RUNTIME_NOT_PROBED
    )
    assert row["static_result"] == "ELIGIBLE"
    assert row["gate_reason"] == "runtime_not_probed"


def test_explicit_specialized_model_remains_the_diagnostic_gate(monkeypatch):
    evidence = ModelEvidenceCatalog(
        catalog=ModelCatalog.from_document(
            {
                "schema_version": 1,
                "entries": [
                    {
                        "provider_id": "gemini",
                        "provider_binding_id": "gemini:worker",
                        "model_id": "gemini-image-1",
                        "source": "fixture.models.list",
                        "observed_at": "2026-11-02T00:00:00+00:00",
                        "expires_at": "2026-11-09T00:00:00+00:00",
                        "metadata": {"supported_generation_methods": ["embedContent"]},
                    }
                ],
            }
        ),
        aliases=ModelAliasCatalog.from_document({"schema_version": 1, "entries": []}),
        benchmarks=BenchmarkCatalog.from_document(
            {"schema_version": 1, "tier_thresholds": {"L1": 0, "L2": 30, "L3": 60}, "entries": []}
        ),
        capabilities=ModelCapabilityCatalog.from_document({"schema_version": 1, "entries": []}),
    )

    class MissingQualification:
        def resolve(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr("scripts.diagnose_model_candidates.profile_for", lambda *_args: None)
    row = diagnose_entries(
        evidence,
        qualification_resolver=MissingQualification(),
        limit=1,
        now=DIAGNOSTIC_NOW,
    )[0]

    assert row["static_result"] == STATIC_MODEL_UNSUPPORTED
    assert row["gate_reason"] == "capability_blocked"


@pytest.mark.current_billing_catalog
def test_explicit_runtime_snapshot_distinguishes_unavailable_from_not_probed(model_evidence):
    candidate = next(
        row
        for row in diagnose_entries(
            model_evidence,
            qualification_resolver=_CurrentQualification(),
            limit=MAX_DIAGNOSTIC_ROWS,
            now=DIAGNOSTIC_NOW,
        )
        if row["result"] == RUNTIME_NOT_PROBED
    )
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
    rows = diagnose_entries(
        model_evidence,
        qualification_resolver=_CurrentQualification(),
        runtime_snapshot=snapshot,
        limit=MAX_DIAGNOSTIC_ROWS,
        now=DIAGNOSTIC_NOW,
    )
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
        "reasons": ["candidate_not_merged", "generation_not_attempted", "no_runtime_eligible_route"],
    }


def test_catalog_coverage_reports_stale_storage_separately(model_evidence):
    coverage = catalog_coverage(model_evidence, now=DIAGNOSTIC_NOW)
    assert coverage["stored_catalog_count"] >= coverage["current_catalog_count"]
    assert "stale_or_expired_count" in coverage
    assert isinstance(coverage["stale_or_expired_provider_binding_counts"], dict)


def test_catalog_sources_show_fresh_staged_rows_separately_from_expired_canonical_rows():
    now = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)
    canonical = ModelCatalog.from_document(
        {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": "cloudflare",
                    "provider_binding_id": "cloudflare:account",
                    "model_id": "@cf/zai-org/glm-4.7-flash",
                    "source": "models.list",
                    "observed_at": "2026-10-04T00:00:00+00:00",
                    "expires_at": "2026-10-11T00:00:00+00:00",
                }
            ],
        }
    )
    staged = ModelCatalog.from_document(
        {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": "cloudflare",
                    "provider_binding_id": "cloudflare:account",
                    "model_id": "@cf/zai-org/glm-4.7-flash",
                    "source": "models.list",
                    "observed_at": "2026-11-01T15:49:15+00:00",
                    "expires_at": "2026-11-08T15:49:15+00:00",
                }
            ],
        }
    )

    summary = diagnostics.summarize_catalog_sources(canonical, staged, now=now)

    assert summary == {
        "canonical": {"stored_count": 1, "current_count": 0, "stale_or_expired_count": 1},
        "staged_candidate": {
            "status": "NOT_PROMOTED",
            "stored_count": 1,
            "current_count": 1,
            "stale_or_expired_count": 0,
        },
    }


def test_main_tabular_output_exposes_runtime_and_gate_reason(model_evidence, capsys):
    assert main(["--limit", "1", "--now", DIAGNOSTIC_NOW.isoformat()]) == 0

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
    assert row[9] in {
        "runtime_not_probed",
        "runtime_unknown",
        "runtime_unavailable",
        "discovery_blocked",
        "capability_blocked",
        "billing_missing",
        "qualification_missing",
    }


def test_main_summary_exposes_runtime_admission_counts(capsys):
    assert main(["--all", "--summary", "--now", DIAGNOSTIC_NOW.isoformat()]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("runtime_eligible\t") for line in lines)
    assert any(line.startswith("runtime_bootstrap_admitted\t") for line in lines)
    assert any(line.startswith("runtime_not_probed\t") for line in lines)
    assert any(line.startswith("runtime_unavailable\t") for line in lines)


def test_main_summary_defaults_to_complete_current_catalog(capsys):
    assert main(["--summary", "--json", "--now", DIAGNOSTIC_NOW.isoformat()]) == 0

    payload = json.loads(capsys.readouterr().out)
    summary = payload["summary"]
    coverage = summary["catalog_coverage"]
    assert summary["row_count"] == coverage["current_catalog_count"]
    assert summary["diagnostic_truncated"] is False


def test_main_accepts_configured_pool_runtime_snapshot(tmp_path, capsys):
    evidence = tmp_path / "configured-pool.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_type": "runtime_admission_configured_pool_snapshot",
                "recorded_at": "2026-09-27T00:00:00+00:00",
                "source": {"authority": "existing runtime authority"},
                "runtime_snapshot": {
                    "observations": [
                        {
                            "provider_id": "gemini",
                            "provider_binding_id": "gemini:worker:free-3",
                            "model_id": "gemini-3.5-flash-lite",
                            "status": RUNTIME_UNAVAILABLE,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    assert main(
        [
            "--summary",
            "--json",
            "--runtime-evidence",
            str(evidence),
            "--provider",
            "gemini",
            "--binding",
            "gemini:worker:free-3",
            "--model",
            "gemini-3.5-flash-lite",
            "--now",
            DIAGNOSTIC_NOW.isoformat(),
        ]
    ) == 0

    output = capsys.readouterr().out
    assert '"runtime_unavailable_count": 1' in output


def test_main_accepts_multi_binding_runtime_snapshot(tmp_path, capsys):
    evidence = tmp_path / "multi-binding-runtime.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "evidence_type": "runtime_admission_multi_binding_snapshot",
                "recorded_at": "2026-09-28T00:00:00+00:00",
                "source": {"authority": "existing runtime authority", "provider_id": "gemini"},
                "runtime_snapshot": {
                    "observations": [
                        {
                            "provider_binding_id": "gemini:worker:free-3",
                            "model_id": "gemini-3.8-flash",
                            "status": RUNTIME_UNAVAILABLE,
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    assert main(
        [
            "--summary",
            "--json",
            "--runtime-evidence",
            str(evidence),
            "--provider",
            "gemini",
            "--binding",
            "gemini:worker:free-3",
            "--model",
            "gemini-3.8-flash",
            "--now",
            DIAGNOSTIC_NOW.isoformat(),
        ]
    ) == 0

    output = capsys.readouterr().out
    assert '"runtime_unavailable_count": 1' in output


@pytest.mark.current_billing_catalog
def test_summary_keeps_no_charge_proposal_candidates_visible_when_formal_evidence_is_expired(
    tmp_path,
    capsys,
):
    candidate_catalog = tmp_path / "staged-candidates.json"
    candidate_catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": [
                    {
                        "provider_id": "gemini",
                        "provider_binding_id": "gemini:worker:free-3",
                        "model_id": "gemini-3.6-flash",
                        "source": "staged.models.list",
                        "observed_at": "2026-11-02T09:00:00+00:00",
                        "expires_at": "2026-11-09T09:00:00+00:00",
                    },
                    {
                        "provider_id": "openrouter",
                        "provider_binding_id": "openrouter:account",
                        "model_id": "google/gemma-4-26b-a4b-it:free",
                        "source": "staged.models.list",
                        "observed_at": "2026-11-02T09:00:00+00:00",
                        "expires_at": "2026-11-09T09:00:00+00:00",
                    },
                    {
                        "provider_id": "ollama_cloud",
                        "provider_binding_id": "ollama_cloud:account",
                        "model_id": "deepseek-v4.1-flash",
                        "source": "staged.models.list",
                        "observed_at": "2026-11-02T09:00:00+00:00",
                        "expires_at": "2026-11-09T09:00:00+00:00",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert main(
        [
            "--summary",
            "--json",
            "--candidate-catalog",
            str(candidate_catalog),
            "--now",
            "2026-11-02T10:00:00+00:00",
        ]
    ) == 0

    summary = json.loads(capsys.readouterr().out)["summary"]
    assert summary["eligible_count"] == 0
    proposal = summary["proposal_only_candidates"]
    assert proposal["inventory_source"] == "staged_plus_canonical_advisory_not_promoted"
    assert proposal["binding_model_row_count"] >= 2
    assert proposal["current_binding_model_row_count"] >= 2
    assert {"gemini", "openrouter"} <= set(proposal["provider_counts"])
    assert "ollama_cloud" not in proposal["provider_counts"]
    assert proposal["scope"] == "text_generation_rows_with_exact_current_no_charge_proposal_admission"
    assert proposal["requires_host_verification"] is True
    assert proposal["formal_runtime_eligibility_asserted"] is False


@pytest.mark.current_billing_catalog
def test_stale_discovery_identity_remains_advisory_only_when_exact_billing_is_current():
    canonical = ModelCatalog.from_document(
        {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": "groq",
                    "provider_binding_id": "groq:account",
                    "model_id": "llama-3.3-70b-versatile",
                    "source": "prior.models.list",
                    "observed_at": "2026-10-01T00:00:00+00:00",
                    "expires_at": "2026-10-08T00:00:00+00:00",
                }
            ],
        }
    )
    staged = ModelCatalog.from_document(
        {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": "gemini",
                    "provider_binding_id": "gemini:worker:free-3",
                    "model_id": "gemini-3.6-flash",
                    "source": "staged.models.list",
                    "observed_at": "2026-11-02T09:00:00+00:00",
                    "expires_at": "2026-11-09T09:00:00+00:00",
                }
            ],
        }
    )

    summary = diagnostics.summarize_proposal_only_candidates(
        canonical,
        staged,
        now=datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc),
    )

    assert summary["binding_model_row_count"] == 2
    assert summary["current_binding_model_row_count"] == 1
    assert summary["stale_advisory_binding_model_row_count"] == 1
    assert summary["provider_counts"] == {"gemini": 1, "groq": 1}
    assert summary["stale_provider_counts"] == {"groq": 1}
    assert summary["formal_runtime_eligibility_asserted"] is False


@pytest.mark.current_billing_catalog
def test_vercel_conflicting_free_listing_remains_visible_as_billing_unknown():
    staged = ModelCatalog.from_document(
        {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": "vercel",
                    "provider_binding_id": "vercel:account",
                    "model_id": "inclusionai/ling-3.1-flash-1019",
                    "source": "staged.models.list",
                    "observed_at": "2026-11-02T09:00:00+00:00",
                    "expires_at": "2026-11-09T09:00:00+00:00",
                },
                {
                    "provider_id": "vercel",
                    "provider_binding_id": "vercel:account",
                    "model_id": "google/gemini-3.8-flash",
                    "source": "staged.models.list",
                    "observed_at": "2026-11-02T09:00:00+00:00",
                    "expires_at": "2026-11-09T09:00:00+00:00",
                },
            ],
        }
    )

    summary = diagnostics.summarize_proposal_only_candidates(
        ModelCatalog.from_document({"schema_version": 1, "entries": []}),
        staged,
        now=datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc),
    )

    assert summary["binding_model_row_count"] == 0
    assert summary["provider_counts"] == {}
    assert summary["billing_unknown_binding_model_row_count"] == 1
    assert summary["billing_unknown_provider_counts"] == {"vercel": 1}
    assert summary["billing_expired_or_not_no_charge_binding_model_row_count"] == 1
    assert summary["billing_expired_or_not_no_charge_provider_counts"] == {"vercel": 1}
    assert summary["formal_runtime_eligibility_asserted"] is False


@pytest.mark.current_billing_catalog
def test_local_ollama_inventory_is_reported_separately_from_remote_proposal_routes():
    local = ModelCatalog.from_document(
        {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": "ollama",
                    "provider_binding_id": "ollama:local",
                    "model_id": "qwen3.5:9b",
                    "source": "staged.models.list",
                    "observed_at": "2026-11-02T09:00:00+00:00",
                    "expires_at": "2026-11-09T09:00:00+00:00",
                }
            ],
        }
    )

    summary = diagnostics.summarize_proposal_only_candidates(
        ModelCatalog.from_document({"schema_version": 1, "entries": []}),
        local,
        now=datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc),
    )

    assert summary["binding_model_row_count"] == 0
    assert summary["local_route_binding_model_row_count"] == 1
    assert summary["local_route_provider_counts"] == {"ollama": 1}
    assert summary["inactive_provider_binding_model_row_count"] == 0
