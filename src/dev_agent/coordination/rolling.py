"""Guardian-owned rolling restart composition.

This module does not start a second scheduler or process authority.  It only
uses the existing :class:`GuardianProcessExecutor` to enforce the safe
ordering for replacing one static process generation with another.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .guardian_process import (
    GuardianProcessExecutionError,
    GuardianProcessExecutor,
    LaunchProfile,
    ProcessHandle,
)


class RollingDecision(str, Enum):
    COMPLETED = "COMPLETED"
    NEW_START_UNKNOWN = "NEW_START_UNKNOWN"
    NEW_HEALTH_FAILED = "NEW_HEALTH_FAILED"
    NEW_STOP_UNKNOWN = "NEW_STOP_UNKNOWN"
    OLD_STOP_UNKNOWN = "OLD_STOP_UNKNOWN"


@dataclass(frozen=True)
class RollingRestartResult:
    decision: RollingDecision
    new_handle: ProcessHandle | None
    old_stopped: bool
    reconciliation_required: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "new_handle": self.new_handle.to_dict() if self.new_handle is not None else None,
            "old_stopped": self.old_stopped,
            "reconciliation_required": self.reconciliation_required,
        }


class RollingRestartService:
    """Perform one bounded rolling replacement through the Guardian executor."""

    def __init__(self, executor: GuardianProcessExecutor) -> None:
        if not isinstance(executor, GuardianProcessExecutor):
            raise TypeError("executor must be a GuardianProcessExecutor")
        self.executor = executor

    def roll(
        self,
        old_profile: LaunchProfile,
        new_profile: LaunchProfile,
        *,
        health_check: Callable[[ProcessHandle], bool],
    ) -> RollingRestartResult:
        if not callable(health_check):
            raise TypeError("health_check must be callable")
        if old_profile.role != new_profile.role:
            raise ValueError("rolling restart requires the same role")
        if new_profile.generation <= old_profile.generation:
            raise ValueError("new generation must be greater than the old generation")

        self.executor.bound_profile(old_profile)
        self.executor.bound_profile(new_profile)

        try:
            new_handle = self.executor.start_profile(new_profile)
        except GuardianProcessExecutionError:
            return RollingRestartResult(
                decision=RollingDecision.NEW_START_UNKNOWN,
                new_handle=None,
                old_stopped=False,
                reconciliation_required=True,
            )

        try:
            healthy = health_check(new_handle)
        except Exception:
            healthy = False

        if not healthy:
            try:
                self.executor.stop_profile(new_profile)
            except GuardianProcessExecutionError:
                return RollingRestartResult(
                    decision=RollingDecision.NEW_STOP_UNKNOWN,
                    new_handle=new_handle,
                    old_stopped=False,
                    reconciliation_required=True,
                )
            return RollingRestartResult(
                decision=RollingDecision.NEW_HEALTH_FAILED,
                new_handle=new_handle,
                old_stopped=False,
                reconciliation_required=False,
            )

        try:
            self.executor.stop_profile(old_profile)
        except GuardianProcessExecutionError:
            return RollingRestartResult(
                decision=RollingDecision.OLD_STOP_UNKNOWN,
                new_handle=new_handle,
                old_stopped=False,
                reconciliation_required=True,
            )
        return RollingRestartResult(
            decision=RollingDecision.COMPLETED,
            new_handle=new_handle,
            old_stopped=True,
            reconciliation_required=False,
        )


__all__ = ["RollingDecision", "RollingRestartResult", "RollingRestartService"]
