import json
from types import SimpleNamespace

import pytest

import scripts.devfarm_reviewer_shadow as reviewer_shadow
from src.dev_agent.intelligence.reviewer_adapter import ReviewProposal
from src.dev_agent.resources.control import DispatchDenied


def test_reviewer_pool_selector_uses_shared_resource_boundary():
    values = {
        "GEMINI_API_KEY_3": "secret-value",
        "GEMINI_MODEL_3": "gemini-review-model",
    }

    bindings = reviewer_shadow.resolve_provider_pool(
        pool_json=None,
        use_configured_pool=True,
        env=values.get,
    )

    assert bindings is not None
    selected = next(item for item in bindings if item.binding_id == "gemini:worker:free-3")
    assert selected.model == "gemini-review-model"
    assert "secret-value" not in repr(selected)


def test_reviewer_pool_selector_rejects_invalid_pool():
    with pytest.raises(reviewer_shadow.ReviewAdapterError, match="invalid binding object"):
        reviewer_shadow.resolve_provider_pool(pool_json="[1]", use_configured_pool=False)


def test_reviewer_shadow_cli_classifies_dispatch_denied_without_raw_output(monkeypatch, capsys):
    def denied(**_kwargs):
        raise DispatchDenied("no_route", "no eligible resource")

    monkeypatch.setattr(reviewer_shadow, "run_shadow", denied)

    assert reviewer_shadow.main(["--run-id", "run-1", "--task-id", "task-1"]) == 2

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "status": "blocked_external",
        "category": "DispatchDenied",
        "message": "no eligible resource",
    }


def test_reviewer_proposal_only_does_not_require_codex_decision(monkeypatch):
    packet = {
        "task_id": "task-d7",
        "attempt_id": "attempt-d7",
        "status": "HOST_VERIFIED",
        "changed_files": ["docs/change.md"],
        "verification_summary": {"host_verified": True},
        "known_issues": [],
        "acceptance": ["the focused check passes"],
        "artifact_refs": [{"kind": "verification", "path": ".devfarm/verification/d7.json"}],
    }

    class FakeRunner:
        def __init__(self, _root, _run_id):
            pass

        def review_packet(self, _task_id):
            return packet

    proposal = ReviewProposal(
        task_id="task-d7",
        attempt_id="attempt-d7",
        decision="APPROVE_INTEGRATION",
        evidence_refs=(packet["artifact_refs"][0],),
        rationale="The verified bounded packet is suitable for a proposal-only shadow review.",
    )

    class FakeAdapter:
        def __init__(self, _provider, *, allow_unknown_quota, intelligence_tier):
            assert allow_unknown_quota is True
            assert intelligence_tier == "L2"

        def propose(self, value):
            assert value == packet
            return proposal

    selected = SimpleNamespace(
        provider_id="gemini",
        provider_binding_id="gemini:worker:free-3",
        model_id="gemini-3.6-flash",
        outcome="succeeded",
    )

    class FakePool:
        dispatcher = SimpleNamespace(audits=[selected])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(reviewer_shadow, "CodexSupervisedCommanderRun", FakeRunner)
    monkeypatch.setattr(reviewer_shadow, "ModelReviewAdapter", FakeAdapter)
    admission_calls = {}

    def admit(bindings, **_kwargs):
        admission_calls["bindings"] = bindings
        return ("admitted",)

    monkeypatch.setattr(reviewer_shadow, "admit_resource_pool", admit)
    monkeypatch.setattr(reviewer_shadow, "compose_resource_pool", lambda *_args, **_kwargs: FakePool())

    output = reviewer_shadow.run_proposal_only(
        root=".",
        run_id="run-d7",
        task_id="task-d7",
        provider_id="gemini",
        binding_id="gemini:worker:free-3",
        model_id="gemini-3.6-flash",
        api_key_env="GEMINI_API_KEY_3",
        quota_domain="gemini:project:982142111392",
        timeout_seconds=45.0,
        allow_unknown_quota=True,
        provider_pool=("pool-a", "pool-b"),
    )

    assert output["status"] == "live_shadow_proposal_only"
    assert admission_calls["bindings"] == ("pool-a", "pool-b")
    assert output["proposal"] == proposal.to_dict()
    assert "codex_decision" not in output
    assert output["authority"] == {
        "reviewer_mode": "shadow",
        "proposal_only": True,
        "final_authority": ["codexless_policy", "host_policy"],
        "integration_performed_by_shadow": False,
    }
