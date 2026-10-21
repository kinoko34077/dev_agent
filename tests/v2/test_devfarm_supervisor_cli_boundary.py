import inspect
from types import SimpleNamespace

from scripts.devfarm_supervisor import main as compatibility_main
from scripts.devfarm_supervisor_cli import main as cli_main
import scripts.devfarm_supervisor_cli as supervisor_cli


def test_supervisor_cli_entrypoint_is_owned_by_dedicated_cli_module():
    assert cli_main.__module__ == "scripts.devfarm_supervisor_cli"
    assert "devfarm_supervisor_cli" in inspect.getsource(compatibility_main)


def test_supervisor_cli_keeps_local_trial_opt_in_default_off_and_forwards_it(monkeypatch, tmp_path):
    calls = []
    provider_calls = []

    class Runner:
        def __init__(self, *_args):
            pass

        def advance(self, **kwargs):
            calls.append(("resume", kwargs))
            return SimpleNamespace(to_dict=lambda: {"status": "ACTIVE"})

        def run_until_intervention(self, **kwargs):
            calls.append(("run", kwargs))
            return SimpleNamespace(to_dict=lambda: {"status": "ACTIVE"})

    monkeypatch.setattr(supervisor_cli, "CodexSupervisedCommanderRun", Runner)

    def capture_providers(*_args, **kwargs):
        provider_calls.append(kwargs.get("local_trial", False))
        return {}

    monkeypatch.setattr(supervisor_cli, "providers_for_resume", capture_providers)

    assert cli_main(["resume", "run-1", "--root", str(tmp_path)]) == 0
    assert cli_main(["resume", "run-1", "--root", str(tmp_path), "--local-trial"]) == 0
    assert cli_main(["run", "run-1", "--root", str(tmp_path), "--local-trial"]) == 0

    assert [(kind, kwargs["local_trial"]) for kind, kwargs in calls] == [
        ("resume", False),
        ("resume", True),
        ("run", True),
    ]
    assert provider_calls == [False, True, True]
