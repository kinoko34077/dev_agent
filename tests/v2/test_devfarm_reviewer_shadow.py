import json

import scripts.devfarm_reviewer_shadow as reviewer_shadow
from src.dev_agent.resources.control import DispatchDenied


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
