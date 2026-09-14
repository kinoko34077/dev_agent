"""Public Host Verification primitives shared by DevFarm adapters."""

from __future__ import annotations

import math
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import threading
from typing import Any

from scripts.devfarm import DevFarmError, is_protected_path
from scripts.devfarm_artifacts import MAX_TEST_OUTPUT_CHARS, bounded_test_output
from src.dev_agent.security.audit import AuditRecorder


def is_within(root: Path, candidate: Path) -> bool:
    """Return whether a resolved candidate remains below the resolved root."""

    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


class HostVerificationRunner:
    """Run allowlisted verification commands with bounded host containment."""

    _SAFE_ENVIRONMENT_KEYS = frozenset(
        {
            "COMSPEC",
            "LANG",
            "LC_ALL",
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
        }
    )

    def __init__(self, *, timeout_seconds: float = 120.0, max_output_bytes: int = MAX_TEST_OUTPUT_CHARS) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_bytes = max_output_bytes

    def _environment(self, home: Path) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in self._SAFE_ENVIRONMENT_KEYS
        }
        home_value = home.as_posix()
        environment.update(
            {
                "HOME": home_value,
                "USERPROFILE": home_value,
                "HOMEDRIVE": home.drive,
                "HOMEPATH": home_value[len(home.drive) :] if home.drive else home_value,
                "DEV_AGENT_HOST_VERIFICATION": "1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONNOUSERSITE": "1",
            }
        )
        return environment

    def _terminate_tree(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
            )
            return
        try:
            import signal

            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()

    def run(self, command: list[str], *, cwd: Path) -> dict[str, Any]:
        if not isinstance(command, list) or not command or not all(isinstance(token, str) and token for token in command):
            raise ValueError("command must be a non-empty token list")
        workspace = cwd.resolve()
        with tempfile.TemporaryDirectory(prefix="devfarm-host-home-") as home_dir:
            home = Path(home_dir)
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                command,
                cwd=workspace,
                env=self._environment(home),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
            stdout = bytearray()
            stderr = bytearray()
            output_state = {"stdout_truncated": False, "stderr_truncated": False}

            def capture(stream, buffer: bytearray, key: str) -> None:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        return
                    remaining = self.max_output_bytes - len(buffer)
                    if remaining > 0:
                        buffer.extend(chunk[:remaining])
                    if len(chunk) > max(remaining, 0):
                        output_state[key] = True

            threads = [
                threading.Thread(target=capture, args=(process.stdout, stdout, "stdout_truncated"), daemon=True),
                threading.Thread(target=capture, args=(process.stderr, stderr, "stderr_truncated"), daemon=True),
            ]
            for thread in threads:
                thread.start()
            timed_out = False
            try:
                return_code = process.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate_tree(process)
                try:
                    return_code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    return_code = process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)
            return {
                "returncode": None if timed_out else return_code,
                "stdout": AuditRecorder.sanitize_payload({"text": bytes(stdout).decode("utf-8", errors="replace")})["text"],
                "stderr": AuditRecorder.sanitize_payload({"text": bytes(stderr).decode("utf-8", errors="replace")})["text"],
                "timed_out": timed_out,
                "output_truncated": output_state["stdout_truncated"] or output_state["stderr_truncated"],
                "containment": {
                    "environment": "sanitized_allowlist",
                    "home": "temporary",
                    "process_tree": "terminated_on_timeout",
                    "network": "not_isolated",
                    "sandbox": "not_provided",
                },
            }


def validate_host_test_targets(workspace: Path, tokens: list[str]) -> None:
    """Resolve every structured test target inside the verification worktree."""

    for token in tokens[3:]:
        if token in {"-q", "-x"} or token.startswith("--maxfail="):
            continue
        target = token.split("::", 1)[0].replace("\\", "/")
        parsed = PurePosixPath(target)
        if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts) or is_protected_path(target):
            raise DevFarmError(f"host test target is outside the allowed worktree scope: {target}")
        current = workspace
        for part in parsed.parts:
            current = current / part
            if current.is_symlink():
                raise DevFarmError(f"host test target symlink is not allowed: {target}")
        resolved = (workspace / target).resolve()
        if not is_within(workspace, resolved):
            raise DevFarmError(f"host test target resolves outside the worktree: {target}")


def target_is_independent(workspace: Path, target: str, changed_set: frozenset[str]) -> bool:
    """Return whether a test target includes at least one worker-independent file."""

    path = (workspace / target).resolve()
    if path.is_dir():
        for child in path.rglob("*"):
            if not child.is_file():
                continue
            try:
                rel = child.relative_to(workspace).as_posix()
            except ValueError:
                continue
            if rel not in changed_set:
                return True
        return False
    return target not in changed_set


__all__ = [
    "HostVerificationRunner",
    "bounded_test_output",
    "is_within",
    "target_is_independent",
    "validate_host_test_targets",
]
