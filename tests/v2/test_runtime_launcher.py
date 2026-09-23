from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "start-dev-agent.bat"


def test_launcher_is_repo_relative_and_does_not_register_os_startup():
    content = LAUNCHER.read_text(encoding="utf-8")

    assert "%~dp0" in content
    assert 'pushd "%DEV_AGENT_ROOT%"' in content
    assert "python -m scripts.devfarm_runtime_coordinator serve %*" in content
    assert "popd" in content
    assert "exit /b %DEV_AGENT_EXIT%" in content
    assert "schtasks" not in content.lower()
    assert "reg add" not in content.lower()
    assert "git pull" not in content.lower()


def test_launcher_runs_from_another_working_directory_and_preserves_runtime_boundary(tmp_path):
    working_directory = tmp_path / "operator cwd with spaces"
    data_directory = tmp_path / "runtime state with spaces"
    working_directory.mkdir()
    result = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "call",
            str(LAUNCHER),
            "--max-cycles",
            "1",
            "--idle-sleep-seconds",
            "0.01",
            "--data-dir",
            str(data_directory),
            "--instance-id",
            "launcher-test",
        ],
        cwd=working_directory,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert '"mode": "serve"' in result.stdout
    assert '"status": "CYCLE_LIMIT"' in result.stdout
    assert (data_directory / "state.sqlite3").exists()
