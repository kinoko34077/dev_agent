"""Bounded rollback composition for revision-pinned Guardian runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .rolling import RollingDecision, RollingRestartResult, RollingRestartService
from .runtime_release import RevisionPinnedRuntimeStore, RuntimeRelease, RuntimeReleaseError
from .guardian_process import LaunchProfile


class RollbackDecision(str, Enum):
    COMPLETED = "COMPLETED"
    RELEASE_UNAVAILABLE = "RELEASE_UNAVAILABLE"
    RELEASE_PROFILE_MISMATCH = "RELEASE_PROFILE_MISMATCH"
    NEW_START_UNKNOWN = "NEW_START_UNKNOWN"
    NEW_HEALTH_FAILED = "NEW_HEALTH_FAILED"
    NEW_STOP_UNKNOWN = "NEW_STOP_UNKNOWN"
    OLD_STOP_UNKNOWN = "OLD_STOP_UNKNOWN"


@dataclass(frozen=True)
class RuntimeRollbackResult:
    decision: RollbackDecision
    release: RuntimeRelease | None
    rolling: RollingRestartResult | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "release": self.release.to_dict() if self.release is not None else None,
            "rolling": self.rolling.to_dict() if self.rolling is not None else None,
        }


class RuntimeRollbackService:
    """Materialize a known-good commit and delegate replacement to Guardian."""

    def __init__(
        self,
        release_store: RevisionPinnedRuntimeStore,
        rolling_service: RollingRestartService,
    ) -> None:
        if not isinstance(release_store, RevisionPinnedRuntimeStore):
            raise TypeError("release_store must be a RevisionPinnedRuntimeStore")
        if not isinstance(rolling_service, RollingRestartService):
            raise TypeError("rolling_service must be a RollingRestartService")
        self.release_store = release_store
        self.rolling_service = rolling_service

    def rollback(self, old_profile: LaunchProfile, new_profile: LaunchProfile, *, health_check) -> RuntimeRollbackResult:
        """Perform one pinned rollback attempt; never retry an uncertain effect."""

        try:
            release = self.release_store.materialize(new_profile.revision)
        except RuntimeReleaseError:
            return RuntimeRollbackResult(
                decision=RollbackDecision.RELEASE_UNAVAILABLE,
                release=None,
                rolling=None,
            )
        if new_profile.runtime_root.resolve() != release.runtime_root.resolve():
            return RuntimeRollbackResult(
                decision=RollbackDecision.RELEASE_PROFILE_MISMATCH,
                release=release,
                rolling=None,
            )

        rolling = self.rolling_service.roll(
            old_profile,
            new_profile,
            health_check=health_check,
        )
        return RuntimeRollbackResult(
            decision=RollbackDecision(rolling.decision.value),
            release=release,
            rolling=rolling,
        )


__all__ = ["RollbackDecision", "RuntimeRollbackResult", "RuntimeRollbackService"]
