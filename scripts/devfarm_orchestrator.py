"""Lightweight development-farm orchestration.

The farm is a development aid, not the production scheduler or an AgentBackend.
Remote proposal calls may overlap in one bounded host process, while Git
worktree creation and host verification remain behind a separate, smaller
concurrency budget.  No proposal is merged automatically.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import threading
from typing import Any, Iterator, Mapping, Sequence

from scripts.devfarm import DevFarmError, validate_manifest
from scripts.devfarm_worker import apply_and_verify, run_worker
from src.dev_agent.providers.base import ModelProvider


class ConcurrencyLimitError(DevFarmError):
    """A requested host or remote slot is intentionally disabled."""


class ConcurrencyGovernor:
    """Thread-safe bounded slot counter with observable peak usage."""

    def __init__(self, max_inflight: int, *, name: str) -> None:
        if isinstance(max_inflight, bool) or not isinstance(max_inflight, int) or max_inflight < 0:
            raise ValueError("max_inflight must be a non-negative integer")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        self.name = name.strip()
        self.max_inflight = max_inflight
        self._semaphore = threading.BoundedSemaphore(max_inflight) if max_inflight else None
        self._lock = threading.Lock()
        self._active = 0
        self._peak = 0

    @contextmanager
    def slot(self) -> Iterator[None]:
        if self._semaphore is None:
            raise ConcurrencyLimitError(f"concurrency slot is disabled: {self.name}")
        self._semaphore.acquire()
        with self._lock:
            self._active += 1
            self._peak = max(self._peak, self._active)
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
            self._semaphore.release()

    def snapshot(self) -> dict[str, int | str]:
        with self._lock:
            return {
                "name": self.name,
                "max_inflight": self.max_inflight,
                "active": self._active,
                "peak": self._peak,
            }


class RemoteConcurrencyGovernor(ConcurrencyGovernor):
    """Bound remote inference waits independently from local verification."""

    def __init__(self, max_inflight: int = 4) -> None:
        super().__init__(max_inflight, name="remote_inference")


class HostConcurrencyGovernor:
    """Small local-resource budget for the verification side of the farm."""

    def __init__(
        self,
        *,
        worktree_verification_slots: int = 1,
        pytest_slots: int = 1,
        heavy_subprocess_slots: int = 1,
        local_model_slots: int = 0,
    ) -> None:
        self._slots = {
            "worktree_verification": ConcurrencyGovernor(worktree_verification_slots, name="host.worktree_verification"),
            "pytest": ConcurrencyGovernor(pytest_slots, name="host.pytest"),
            "heavy_subprocess": ConcurrencyGovernor(heavy_subprocess_slots, name="host.heavy_subprocess"),
            "local_model": ConcurrencyGovernor(local_model_slots, name="host.local_model"),
        }

    def slot(self, resource: str = "worktree_verification"):
        try:
            return self._slots[resource].slot()
        except KeyError as exc:
            raise ValueError(f"unknown host concurrency resource: {resource}") from exc

    def snapshot(self) -> dict[str, dict[str, int | str]]:
        return {name: slot.snapshot() for name, slot in self._slots.items()}


@dataclass(frozen=True)
class WorkerAssignment:
    """One manifest/provider pair owned by the orchestrator."""

    manifest_path: Path
    provider: ModelProvider

    def __post_init__(self) -> None:
        if not isinstance(self.manifest_path, Path):
            object.__setattr__(self, "manifest_path", Path(self.manifest_path))
        if not isinstance(self.provider, ModelProvider):
            raise TypeError("provider must implement ModelProvider")


@dataclass(frozen=True)
class DevFarmRun:
    """Proposal and optional host-verification artifacts from one run."""

    proposals: tuple[dict[str, Any], ...]
    verifications: tuple[dict[str, Any], ...]
    remote_governor: Mapping[str, Any]
    host_governor: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposals": list(self.proposals),
            "verifications": list(self.verifications),
            "remote_governor": dict(self.remote_governor),
            "host_governor": dict(self.host_governor),
        }


class DevFarmOrchestrator:
    """Run remote proposals in parallel and host verification in a small pool."""

    def __init__(
        self,
        *,
        remote_governor: RemoteConcurrencyGovernor | None = None,
        host_governor: HostConcurrencyGovernor | None = None,
    ) -> None:
        self.remote_governor = remote_governor or RemoteConcurrencyGovernor()
        self.host_governor = host_governor or HostConcurrencyGovernor()

    @staticmethod
    def _normalize_assignments(assignments: Sequence[WorkerAssignment | tuple[str | Path, ModelProvider]]) -> list[WorkerAssignment]:
        normalized: list[WorkerAssignment] = []
        task_ids: set[str] = set()
        for value in assignments:
            assignment = value if isinstance(value, WorkerAssignment) else WorkerAssignment(Path(value[0]), value[1])
            try:
                raw = json.loads(assignment.manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise DevFarmError(f"could not read worker manifest: {assignment.manifest_path}") from exc
            manifest = validate_manifest(raw)
            task_id = manifest["task_id"]
            if task_id in task_ids:
                raise DevFarmError(f"duplicate worker task assignment: {task_id}")
            task_ids.add(task_id)
            normalized.append(WorkerAssignment(assignment.manifest_path, assignment.provider))
        return normalized

    def _propose(self, root: Path, assignment: WorkerAssignment) -> dict[str, Any]:
        with self.remote_governor.slot():
            return run_worker(root, assignment.manifest_path, provider=assignment.provider)

    def propose(
        self,
        root: str | Path,
        assignments: Sequence[WorkerAssignment | tuple[str | Path, ModelProvider]],
    ) -> list[dict[str, Any]]:
        """Generate remote proposals without creating any worker worktree."""

        root = Path(root).resolve()
        normalized = self._normalize_assignments(assignments)
        if not normalized:
            return []
        with ThreadPoolExecutor(max_workers=self.remote_governor.max_inflight) as pool:
            futures = [pool.submit(self._propose, root, assignment) for assignment in normalized]
            return [future.result() for future in futures]

    def _verify(self, root: Path, manifest_path: Path) -> dict[str, Any]:
        with self.host_governor.slot("worktree_verification"):
            return apply_and_verify(root, manifest_path)

    def verify(self, root: str | Path, manifest_paths: Sequence[str | Path]) -> list[dict[str, Any]]:
        """Verify completed proposals behind the host worktree budget."""

        root = Path(root).resolve()
        paths = [Path(path) for path in manifest_paths]
        if not paths:
            return []
        limit = self.host_governor.snapshot()["worktree_verification"]["max_inflight"]
        if not isinstance(limit, int) or limit <= 0:
            raise ConcurrencyLimitError("host worktree verification is disabled")
        with ThreadPoolExecutor(max_workers=limit) as pool:
            futures = [pool.submit(self._verify, root, path) for path in paths]
            return [future.result() for future in futures]

    def run(
        self,
        root: str | Path,
        assignments: Sequence[WorkerAssignment | tuple[str | Path, ModelProvider]],
    ) -> DevFarmRun:
        """Run proposal stage, then verify only proposals eligible for review."""

        normalized = self._normalize_assignments(assignments)
        proposals = self.propose(root, normalized)
        verify_paths = [
            assignment.manifest_path
            for assignment, result in zip(normalized, proposals)
            if result.get("status") == "completed" and bool(result.get("changed_files"))
        ]
        verifications = self.verify(root, verify_paths)
        return DevFarmRun(
            proposals=tuple(proposals),
            verifications=tuple(verifications),
            remote_governor=self.remote_governor.snapshot(),
            host_governor=self.host_governor.snapshot(),
        )


__all__ = [
    "ConcurrencyGovernor",
    "ConcurrencyLimitError",
    "DevFarmOrchestrator",
    "DevFarmRun",
    "HostConcurrencyGovernor",
    "RemoteConcurrencyGovernor",
    "WorkerAssignment",
]
