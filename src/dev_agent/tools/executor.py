"""Bounded tool execution primitives and process containment."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
from time import monotonic
from typing import Any

from .registry import ToolSpec


class ToolTimedOut(Exception):
    pass


class ToolCancelled(Exception):
    pass


class ToolResponseDecodeError(Exception):
    pass


class ToolProcessError(Exception):
    pass


def handler_reference(spec: ToolSpec) -> str | None:
    if spec.handler_ref:
        return spec.handler_ref
    module = getattr(spec.handler, "__module__", None)
    qualname = getattr(spec.handler, "__qualname__", None)
    if not module or not qualname or "<locals>" in qualname:
        return None
    return f"{module}:{qualname}"


def terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate the worker and descendants, then leave reaping to caller."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        # CREATE_NEW_PROCESS_GROUP alone does not guarantee descendant
        # termination on Windows; taskkill's /T closes the whole tree.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, 9)
        except (ProcessLookupError, PermissionError):
            pass
    if process.poll() is None:
        process.kill()


def run_subprocess(handler_ref: str, arguments: dict[str, Any], timeout_seconds: float, cancel_event: Event | None) -> dict[str, Any]:
    worker = Path(__file__).with_name("process_worker.py")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        [sys.executable, str(worker), handler_ref],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(Path.cwd()),
        start_new_session=os.name != "nt",
        creationflags=creationflags,
    )
    payload = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    result_box: dict[str, Any] = {}

    def communicate() -> None:
        try:
            result_box["output"] = process.communicate(input=payload)
        except BaseException as exc:  # pragma: no cover - OS-level failures vary
            result_box["exception"] = exc
        finally:
            # Record when the worker actually finished. A busy CI runner can
            # report a short-lived worker just after the parent deadline.
            result_box["finished_at"] = monotonic()

    reader = Thread(target=communicate, name="dev-agent-tool-worker-io", daemon=True)
    reader.start()
    deadline = monotonic() + timeout_seconds
    while reader.is_alive():
        if cancel_event is not None and cancel_event.is_set():
            terminate_process_tree(process)
            reader.join(timeout=2.0)
            raise ToolCancelled()
        remaining = deadline - monotonic()
        if remaining <= 0:
            terminate_process_tree(process)
            reader.join(timeout=2.0)
            raise ToolTimedOut()
        reader.join(timeout=min(0.05, remaining))
    reader.join()
    if result_box.get("finished_at", monotonic()) > deadline:
        raise ToolTimedOut()
    if "exception" in result_box:
        raise ToolProcessError("isolated tool process communication failed") from result_box["exception"]
    stdout, _stderr = result_box.get("output", ("", ""))
    if process.returncode != 0 and not stdout.strip():
        raise ToolProcessError("isolated tool process failed")
    try:
        packet = json.loads(stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ToolResponseDecodeError("isolated tool returned malformed JSON") from exc
    if not isinstance(packet, dict) or packet.get("ok") is not True:
        detail = packet.get("error", {}) if isinstance(packet, dict) else {}
        message = detail.get("message", "isolated tool failed") if isinstance(detail, dict) else "isolated tool failed"
        raise ToolProcessError(str(message))
    value = packet.get("result")
    if not isinstance(value, dict):
        raise ToolResponseDecodeError("isolated tool result must be an object")
    return value


def run_in_process(handler, arguments: dict[str, Any], timeout_seconds: float, cancel_event: Event | None) -> dict[str, Any]:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(handler, arguments)
    deadline = monotonic() + timeout_seconds
    try:
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                future.cancel()  # best effort only; running Python threads cannot be killed
                raise ToolTimedOut()
            try:
                value = future.result(timeout=min(0.05, remaining))
                # A handler may finish concurrently with the deadline. Do not
                # accept a value observed after the runtime budget.
                if monotonic() >= deadline:
                    raise ToolTimedOut()
                return value
            except FutureTimeoutError:
                if cancel_event is not None and cancel_event.is_set():
                    future.cancel()
                    raise ToolCancelled()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


class ToolExecutor:
    """Execute one registered handler in its declared isolation boundary."""

    def execute(self, spec: ToolSpec, arguments: dict[str, Any], cancel_event: Event | None = None) -> dict[str, Any]:
        if spec.requires_subprocess:
            reference = handler_reference(spec)
            if reference is None:
                raise ValueError("isolated tools require an importable top-level handler")
            return run_subprocess(reference, arguments, spec.timeout_seconds, cancel_event)
        return run_in_process(spec.handler, arguments, spec.timeout_seconds, cancel_event)


__all__ = [
    "ToolCancelled",
    "ToolExecutor",
    "ToolProcessError",
    "ToolResponseDecodeError",
    "ToolTimedOut",
    "handler_reference",
    "run_in_process",
    "run_subprocess",
    "terminate_process_tree",
]
