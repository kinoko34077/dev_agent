"""Bounded local self-update composition over existing runtime primitives.

This module is deliberately a small Host-owned composition boundary.  It
does not start a scheduler, copy state, mutate the active checkout, or retry
an uncertain process effect.  Candidate releases are materialized by the
existing revision-pinned store, started through the existing rolling service,
and failed revisions are recorded beside release metadata so the same bad
revision is not automatically retried.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable

from .rolling import RollingDecision, RollingRestartResult, RollingRestartService
from .runtime_release import RevisionPinnedRuntimeStore, RuntimeRelease, RuntimeReleaseError
from .runtime_rollback import RollbackDecision, RuntimeRollbackResult, RuntimeRollbackService
from .guardian_process import LaunchProfile


class SelfUpdateError(RuntimeError):
    """A self-update journal or trusted-source boundary is invalid."""


class SelfUpdateDecision(str, Enum):
    PROMOTED = "PROMOTED"
    CANDIDATE_ALREADY_FAILED = "CANDIDATE_ALREADY_FAILED"
    CANDIDATE_HEALTH_FAILED = "CANDIDATE_HEALTH_FAILED"
    RELEASE_UNAVAILABLE = "RELEASE_UNAVAILABLE"
    TRUST_REJECTED = "TRUST_REJECTED"
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    RELEASE_PROFILE_MISMATCH = "RELEASE_PROFILE_MISMATCH"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


@dataclass(frozen=True)
class SelfUpdateJournal:
    """Bounded durable projection of candidate outcomes."""

    active_revision: str | None = None
    failed_revisions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "active_revision": self.active_revision,
            "failed_revisions": list(self.failed_revisions),
        }


@dataclass(frozen=True)
class SelfUpdateResult:
    decision: SelfUpdateDecision
    candidate_revision: str
    active_revision: str | None
    release: RuntimeRelease | None = None
    rolling: RollingRestartResult | None = None
    rollback: RuntimeRollbackResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "candidate_revision": self.candidate_revision,
            "active_revision": self.active_revision,
            "release": self.release.to_dict() if self.release is not None else None,
            "rolling": self.rolling.to_dict() if self.rolling is not None else None,
            "rollback": self.rollback.to_dict() if self.rollback is not None else None,
        }


class SelfUpdateService:
    """Use pinned release, rolling, and rollback services for one update."""

    _JOURNAL_NAME = "self-update.json"

    def __init__(
        self,
        release_store: RevisionPinnedRuntimeStore,
        rolling_service: RollingRestartService,
        *,
        trusted_ref: str = "v2/bootstrap",
    ) -> None:
        if not isinstance(release_store, RevisionPinnedRuntimeStore):
            raise TypeError("release_store must be a RevisionPinnedRuntimeStore")
        if not isinstance(rolling_service, RollingRestartService):
            raise TypeError("rolling_service must be a RollingRestartService")
        if not isinstance(trusted_ref, str) or not trusted_ref.strip():
            raise ValueError("trusted_ref must be non-empty text")
        self.release_store = release_store
        self.rolling_service = rolling_service
        self.trusted_ref = trusted_ref.strip()
        self._journal_path = release_store.metadata_root / self._JOURNAL_NAME

    def journal(self) -> SelfUpdateJournal:
        if not self._journal_path.exists():
            return SelfUpdateJournal()
        try:
            document = json.loads(self._journal_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SelfUpdateError("self-update journal is unreadable") from exc
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise SelfUpdateError("self-update journal schema is unsupported")
        active = document.get("active_revision")
        if active is not None and (not isinstance(active, str) or not active.strip()):
            raise SelfUpdateError("self-update active revision is invalid")
        failed = document.get("failed_revisions", [])
        if not isinstance(failed, list) or len(failed) > 64 or any(not isinstance(item, str) or not item.strip() for item in failed):
            raise SelfUpdateError("self-update failed revision list is invalid")
        return SelfUpdateJournal(active_revision=active, failed_revisions=tuple(dict.fromkeys(failed)))

    def promote(
        self,
        active_profile: LaunchProfile,
        candidate_profile: LaunchProfile,
        *,
        health_check: Callable[[Any], bool],
        preflight: Callable[[], bool] | None = None,
    ) -> SelfUpdateResult:
        """Promote one trusted candidate, with no retry on uncertain effects."""

        self._validate_profiles(active_profile, candidate_profile)
        current = self.journal()
        candidate_revision = candidate_profile.revision
        if candidate_revision in current.failed_revisions:
            return SelfUpdateResult(
                decision=SelfUpdateDecision.CANDIDATE_ALREADY_FAILED,
                candidate_revision=candidate_revision,
                active_revision=current.active_revision or active_profile.revision,
            )
        if not self._is_trusted(candidate_revision):
            return SelfUpdateResult(
                decision=SelfUpdateDecision.TRUST_REJECTED,
                candidate_revision=candidate_revision,
                active_revision=current.active_revision or active_profile.revision,
            )
        if preflight is not None:
            try:
                preflight_passed = bool(preflight())
            except Exception:
                preflight_passed = False
            if not preflight_passed:
                return SelfUpdateResult(
                    decision=SelfUpdateDecision.PREFLIGHT_FAILED,
                    candidate_revision=candidate_revision,
                    active_revision=current.active_revision or active_profile.revision,
                )
        try:
            release = self.release_store.materialize(candidate_revision)
        except RuntimeReleaseError:
            return SelfUpdateResult(
                decision=SelfUpdateDecision.RELEASE_UNAVAILABLE,
                candidate_revision=candidate_revision,
                active_revision=current.active_revision or active_profile.revision,
            )
        if candidate_profile.runtime_root.resolve() != release.runtime_root.resolve():
            return SelfUpdateResult(
                decision=SelfUpdateDecision.RELEASE_PROFILE_MISMATCH,
                candidate_revision=candidate_revision,
                active_revision=current.active_revision or active_profile.revision,
                release=release,
            )
        rolling = self.rolling_service.roll(active_profile, candidate_profile, health_check=health_check)
        if rolling.decision is RollingDecision.COMPLETED:
            updated = SelfUpdateJournal(
                active_revision=candidate_revision,
                failed_revisions=current.failed_revisions,
            )
            self._write_journal(updated)
            return SelfUpdateResult(
                decision=SelfUpdateDecision.PROMOTED,
                candidate_revision=candidate_revision,
                active_revision=candidate_revision,
                release=release,
                rolling=rolling,
            )
        if rolling.decision is RollingDecision.NEW_HEALTH_FAILED:
            failed = tuple(dict.fromkeys((*current.failed_revisions, candidate_revision)))[-64:]
            updated = SelfUpdateJournal(active_revision=current.active_revision or active_profile.revision, failed_revisions=failed)
            self._write_journal(updated)
            return SelfUpdateResult(
                decision=SelfUpdateDecision.CANDIDATE_HEALTH_FAILED,
                candidate_revision=candidate_revision,
                active_revision=updated.active_revision,
                release=release,
                rolling=rolling,
            )
        return SelfUpdateResult(
            decision=SelfUpdateDecision.RECONCILIATION_REQUIRED,
            candidate_revision=candidate_revision,
            active_revision=current.active_revision or active_profile.revision,
            release=release,
            rolling=rolling,
        )

    def rollback(
        self,
        current_profile: LaunchProfile,
        lkg_profile: LaunchProfile,
        *,
        health_check: Callable[[Any], bool],
    ) -> SelfUpdateResult:
        """Roll back through the existing RuntimeRollbackService once."""

        self._validate_profiles(current_profile, lkg_profile, require_forward_generation=False)
        if not self._is_trusted(lkg_profile.revision):
            return SelfUpdateResult(
                decision=SelfUpdateDecision.TRUST_REJECTED,
                candidate_revision=lkg_profile.revision,
                active_revision=current_profile.revision,
            )
        rollback = RuntimeRollbackService(self.release_store, self.rolling_service).rollback(
            current_profile,
            lkg_profile,
            health_check=health_check,
        )
        if rollback.decision is RollbackDecision.COMPLETED:
            self._write_journal(SelfUpdateJournal(active_revision=lkg_profile.revision, failed_revisions=self.journal().failed_revisions))
            return SelfUpdateResult(
                decision=SelfUpdateDecision.ROLLED_BACK,
                candidate_revision=lkg_profile.revision,
                active_revision=lkg_profile.revision,
                release=rollback.release,
                rollback=rollback,
            )
        if rollback.decision in {
            RollbackDecision.NEW_START_UNKNOWN,
            RollbackDecision.NEW_STOP_UNKNOWN,
            RollbackDecision.OLD_STOP_UNKNOWN,
        }:
            decision = SelfUpdateDecision.RECONCILIATION_REQUIRED
        else:
            decision = SelfUpdateDecision.ROLLBACK_FAILED
        return SelfUpdateResult(
            decision=decision,
            candidate_revision=lkg_profile.revision,
            active_revision=current_profile.revision,
            release=rollback.release,
            rollback=rollback,
        )

    @staticmethod
    def _validate_profiles(
        active: LaunchProfile,
        candidate: LaunchProfile,
        *,
        require_forward_generation: bool = True,
    ) -> None:
        if not isinstance(active, LaunchProfile) or not isinstance(candidate, LaunchProfile):
            raise TypeError("profiles must be LaunchProfile instances")
        if active.role != candidate.role:
            raise ValueError("self-update requires one runtime role")
        if require_forward_generation and candidate.generation <= active.generation:
            raise ValueError("candidate generation must be greater than active generation")

    def _write_journal(self, journal: SelfUpdateJournal) -> None:
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._journal_path.parent,
                prefix=f".{self._journal_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(journal.to_dict(), temporary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self._journal_path)
        except OSError as exc:
            raise SelfUpdateError("self-update journal could not be persisted") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def _is_trusted(self, revision: str) -> bool:
        try:
            return self.release_store.revision_reachable_from(revision, self.trusted_ref)
        except RuntimeReleaseError:
            return False


__all__ = ["SelfUpdateDecision", "SelfUpdateError", "SelfUpdateJournal", "SelfUpdateResult", "SelfUpdateService"]
