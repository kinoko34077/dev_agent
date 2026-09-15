from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

import scripts.devfarm_guardian as guardian_module
from scripts.devfarm_repository import read_json
from scripts.devfarm_guardian import (
    GuardianOperatorError,
    guardian_health,
    guardian_registration,
    guardian_unregistration,
    guardian_run_once,
    guardian_serve,
)


def test_guardian_uses_shared_repository_json_reader():
    assert guardian_module.read_json is read_json


def _config(tmp_path):
    path = tmp_path / "guardian.json"
    path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "profile_id": "agent-1",
                        "role": "agent",
                        "generation": 1,
                        "revision": "rev-a",
                        "executable": sys.executable,
                        "arguments": ["-c", "pass"],
                        "runtime_root": str(tmp_path),
                        "environment_profile": "minimal",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_guardian_health_is_bounded_and_does_not_claim_os_registration(tmp_path):
    result = guardian_health(_config(tmp_path))

    assert result["status"] == "READY"
    assert result["profile_count"] == 1
    assert result["process_authority"] == "static_profile_only"
    assert result["os_registration"] == "NOT_CONFIGURED"
    assert result["network"] == "not_used"


def test_guardian_run_once_reconciles_without_starting_a_process(tmp_path):
    result = guardian_run_once(_config(tmp_path), tmp_path / "runtime")

    assert result["mode"] == "run_once"
    assert result["reconciled_action_count"] == 0
    assert result["bounded"] is True


def test_guardian_serve_uses_bounded_polling_without_creating_a_scheduler(tmp_path):
    sleeps = []

    result = guardian_serve(
        _config(tmp_path),
        tmp_path / "runtime",
        poll_seconds=6.0,
        max_cycles=3,
        sleep_fn=sleeps.append,
    )

    assert result["mode"] == "serve"
    assert result["cycles"] == 3
    assert result["poll_seconds"] == 6.0
    assert sleeps == [6.0, 6.0]
    assert result["task_scheduler"] == "not_owned_by_guardian"


def test_guardian_registration_is_static_dry_run_by_default(tmp_path):
    result = guardian_registration(_config(tmp_path), tmp_path / "runtime")

    assert result["status"] == "DRY_RUN"
    assert result["registration"] == "NOT_APPLIED"
    assert result["task_name"] == "DevAgentGuardian"
    assert result["command"][1] == str(guardian_module.Path(guardian_module.__file__).resolve())
    assert result["command"][2] == "serve"
    assert "--poll-seconds" in result["command"]
    assert result["arbitrary_command"] is False
    assert result["recovery"]["disable_command"] == [
        "schtasks.exe",
        "/Change",
        "/TN",
        "DevAgentGuardian",
        "/DISABLE",
    ]
    assert result["recovery"]["unregister_command"] == [
        "schtasks.exe",
        "/Delete",
        "/TN",
        "DevAgentGuardian",
        "/F",
    ]


def test_guardian_unregistration_is_static_dry_run_by_default():
    result = guardian_unregistration()

    assert result["status"] == "DRY_RUN"
    assert result["registration"] == "UNREGISTER_NOT_APPLIED"
    assert result["command"] == ["schtasks.exe", "/Delete", "/TN", "DevAgentGuardian", "/F"]
    assert result["mutation_performed"] is False
    assert result["arbitrary_command"] is False


def test_guardian_unregistration_apply_uses_bounded_static_command(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return guardian_module.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(guardian_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(guardian_module.subprocess, "run", fake_run)

    result = guardian_unregistration(apply=True)

    assert result["status"] == "APPLIED"
    assert result["registration"] == "UNREGISTERED"
    assert result["mutation_performed"] is True
    assert calls[0][0] == ["schtasks.exe", "/Delete", "/TN", "DevAgentGuardian", "/F"]
    assert calls[0][1]["timeout"] == 30


def test_guardian_registration_apply_uses_bounded_static_command(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return guardian_module.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(guardian_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(guardian_module.subprocess, "run", fake_run)

    result = guardian_registration(_config(tmp_path), tmp_path / "runtime", apply=True)

    assert result["status"] == "APPLIED"
    assert result["registration"] == "CONFIGURED"
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:4] == ["schtasks.exe", "/Create", "/TN", "DevAgentGuardian"]
    assert kwargs["timeout"] == 30
    assert kwargs["check"] is False


def test_guardian_registration_apply_closes_timeout_without_raw_process_output(tmp_path, monkeypatch):
    def fake_run(_command, **_kwargs):
        raise guardian_module.subprocess.TimeoutExpired("schtasks.exe", 30, output="secret", stderr="secret")

    monkeypatch.setattr(guardian_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(guardian_module.subprocess, "run", fake_run)

    with pytest.raises(GuardianOperatorError, match="timed out"):
        guardian_registration(_config(tmp_path), tmp_path / "runtime", apply=True)


def test_guardian_os_registration_status_reports_configured_without_task_output(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return guardian_module.subprocess.CompletedProcess(
            command,
            0,
            stdout="TaskName: \\DevAgentGuardian\nSecretPath: hidden",
            stderr="",
        )

    monkeypatch.setattr(guardian_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(guardian_module.subprocess, "run", fake_run)

    result = guardian_module.guardian_os_registration_status(_config(tmp_path))

    assert result["status"] == "CONFIGURED"
    assert result["registration"] == "CONFIGURED"
    assert result["query_performed"] is True
    assert result["mutation_performed"] is False
    assert result["query_command_static"] is True
    assert "SecretPath" not in json.dumps(result)
    assert calls[0][0] == ["schtasks.exe", "/Query", "/TN", "DevAgentGuardian", "/FO", "LIST"]
    assert calls[0][1]["timeout"] == 10


def test_guardian_os_registration_status_reports_missing_task_without_raw_output(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        return guardian_module.subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="ERROR: The system cannot find the file specified.",
        )

    monkeypatch.setattr(guardian_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(guardian_module.subprocess, "run", fake_run)

    result = guardian_module.guardian_os_registration_status(_config(tmp_path))

    assert result["status"] == "NOT_CONFIGURED"
    assert result["registration"] == "NOT_CONFIGURED"
    assert result["observed_task_state"] == "absent"
    assert "specified" not in json.dumps(result)


def test_guardian_os_registration_status_can_query_without_runtime_config(monkeypatch):
    def fake_run(command, **kwargs):
        return guardian_module.subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="ERROR: The system cannot find the file specified.",
        )

    monkeypatch.setattr(guardian_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(guardian_module.subprocess, "run", fake_run)

    result = guardian_module.guardian_os_registration_status()

    assert result["status"] == "NOT_CONFIGURED"
    assert result["registration"] == "NOT_CONFIGURED"
    assert result["config_status"] == "NOT_PROVIDED"
    assert result["profile_count"] is None
    assert result["query_performed"] is True
    assert result["mutation_performed"] is False


@pytest.mark.parametrize(
    ("exception", "reason"),
    [
        (FileNotFoundError("schtasks.exe"), "query_unavailable"),
        (guardian_module.subprocess.TimeoutExpired("schtasks.exe", 10), "query_timeout"),
    ],
)
def test_guardian_os_registration_status_is_bounded_when_query_unavailable(
    tmp_path, monkeypatch, exception, reason
):
    def fake_run(_command, **_kwargs):
        raise exception

    monkeypatch.setattr(guardian_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(guardian_module.subprocess, "run", fake_run)

    result = guardian_module.guardian_os_registration_status(_config(tmp_path))

    assert result["status"] == "NOT_VERIFIED"
    assert result["registration"] == "NOT_VERIFIED"
    assert result["reason"] == reason
    assert result["mutation_performed"] is False


def test_guardian_os_registration_status_does_not_claim_windows_registration_elsewhere(tmp_path):
    result = guardian_module.guardian_os_registration_status(_config(tmp_path))

    if guardian_module.os.name != "nt":
        assert result["status"] == "NOT_APPLICABLE"
        assert result["registration"] == "NOT_VERIFIED"
        assert result["query_performed"] is False
        assert result["mutation_performed"] is False


def test_guardian_config_rejects_arbitrary_command_field(tmp_path):
    path = _config(tmp_path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["command"] = "powershell -c whoami"
    path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(GuardianOperatorError, match="unknown Guardian configuration field"):
        guardian_health(path)
