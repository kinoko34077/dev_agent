"""Pure, bounded metadata for finite refinement convergence.

The value types in this module deliberately do not own dispatch, persistence,
approval, integration, or external-effect recovery.  They give existing Host
boundaries one canonical way to describe an attempt, its sanitized failure
signature, and the finite convergence state associated with it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import re
from typing import Any

from ..coordination.protocol_helpers import ensure_json_safe, ensure_secret_free


class ConvergenceState(str, Enum):
    """Bounded lifecycle states for one attempt chain."""

    FAST_PATH = "FAST_PATH"
    REFINEMENT = "REFINEMENT"
    PROGRESS = "PROGRESS"
    NO_PROGRESS = "NO_PROGRESS"
    STUCK = "STUCK"
    NON_CONVERGING = "NON_CONVERGING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"


class ConvergenceStopReason(str, Enum):
    """Finite reasons a convergence chain may stop."""

    FAST_PATH = "FAST_PATH"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    SAME_SIGNATURE_LIMIT = "SAME_SIGNATURE_LIMIT"
    NON_CONVERGING = "NON_CONVERGING"
    EXTERNAL_RECONCILIATION = "EXTERNAL_RECONCILIATION"
    AUTHORITY_REQUIRED = "AUTHORITY_REQUIRED"
    VALIDATION_FAILED = "VALIDATION_FAILED"


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_REFS = 64
_MAX_ROUND = 64
_MAX_CONVERGENCE_BYTES = 32 * 1024


def _raw_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _token(value: Any, name: str) -> str:
    value = _raw_value(value)
    # Run the repository-wide secret detector before the syntax check so a
    # secret-shaped diagnostic is rejected as a secret, not merely as prose.
    ensure_secret_free({"value": value}, name)
    if not isinstance(value, str) or not _TOKEN.fullmatch(value.strip()):
        raise ValueError(f"{name} must be a bounded token")
    return value.strip().lower()


def _optional_token(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _token(value, name)


def _token_sequence(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a sequence")
    if len(value) > _MAX_REFS:
        raise ValueError(f"{name} contains too many items")
    return tuple(sorted({_token(item, f"{name}[]") for item in value}))


def _optional_digest(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _DIGEST.fullmatch(value.strip().lower()):
        raise ValueError(f"{name} must be a SHA-256 failure signature")
    return value.strip().lower()


@dataclass(frozen=True)
class FailureFingerprint:
    """Canonical, secret-free observation used to compare two failures."""

    failure_class: str
    validator_refs: tuple[str, ...] = field(default_factory=tuple)
    response_contract: str | None = None
    test_ids: tuple[str, ...] = field(default_factory=tuple)
    patch_category: str | None = None
    error_code: str | None = None
    failure_signature: str = field(init=False)

    def __post_init__(self) -> None:
        failure_class = _token(self.failure_class, "failure_class")
        validator_refs = _token_sequence(self.validator_refs, "validator_refs")
        test_ids = _token_sequence(self.test_ids, "test_ids")
        response_contract = _optional_token(self.response_contract, "response_contract")
        patch_category = _optional_token(self.patch_category, "patch_category")
        error_code = _optional_token(self.error_code, "error_code")

        object.__setattr__(self, "failure_class", failure_class)
        object.__setattr__(self, "validator_refs", validator_refs)
        object.__setattr__(self, "test_ids", test_ids)
        object.__setattr__(self, "response_contract", response_contract)
        object.__setattr__(self, "patch_category", patch_category)
        object.__setattr__(self, "error_code", error_code)

        canonical = self._canonical_payload()
        encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        signature = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        object.__setattr__(self, "failure_signature", signature)
        encoded_value = self.to_dict()
        ensure_json_safe(encoded_value, "failure fingerprint")
        ensure_secret_free(encoded_value, "failure fingerprint")

    @classmethod
    def from_observation(
        cls,
        *,
        failure_class: Any,
        validator_refs: Sequence[Any] = (),
        response_contract: Any = None,
        test_ids: Sequence[Any] = (),
        patch_category: Any = None,
        error_code: Any = None,
    ) -> "FailureFingerprint":
        """Build a fingerprint from bounded diagnostic categories only."""

        return cls(
            failure_class=failure_class,
            validator_refs=tuple(validator_refs),
            response_contract=response_contract,
            test_ids=tuple(test_ids),
            patch_category=patch_category,
            error_code=error_code,
        )

    def _canonical_payload(self) -> dict[str, Any]:
        return {
            "failure_class": self.failure_class,
            "validator_refs": list(self.validator_refs),
            "response_contract": self.response_contract,
            "test_ids": list(self.test_ids),
            "patch_category": self.patch_category,
            "error_code": self.error_code,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._canonical_payload(),
            "failure_signature": self.failure_signature,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureFingerprint":
        if not isinstance(value, Mapping):
            raise ValueError("failure fingerprint must be an object")
        allowed = {
            "failure_class",
            "validator_refs",
            "response_contract",
            "test_ids",
            "patch_category",
            "error_code",
            "failure_signature",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown failure fingerprint field: {sorted(unknown)[0]}")
        fingerprint = cls.from_observation(
            failure_class=value.get("failure_class"),
            validator_refs=value.get("validator_refs", ()),
            response_contract=value.get("response_contract"),
            test_ids=value.get("test_ids", ()),
            patch_category=value.get("patch_category"),
            error_code=value.get("error_code"),
        )
        supplied = _optional_digest(value.get("failure_signature"), "failure_signature")
        if supplied != fingerprint.failure_signature:
            raise ValueError("failure_signature does not match normalized observation")
        return fingerprint


@dataclass(frozen=True)
class ConvergenceMetadata:
    """Attempt-scoped convergence metadata carried by existing artifacts."""

    refinement_round: int
    source_attempt_id: str | None
    fresh_attempt_id: str | None
    failure_class: str | None
    failure_signature: str | None
    correction_actor: str | None
    previous_model_identity: str | None
    current_model_identity: str
    validator_refs: tuple[str, ...]
    convergence_state: ConvergenceState
    stop_reason: ConvergenceStopReason | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.refinement_round, bool)
            or not isinstance(self.refinement_round, int)
            or not 0 <= self.refinement_round <= _MAX_ROUND
        ):
            raise ValueError("refinement_round must be an integer between 0 and 64")

        source_attempt_id = _optional_token(self.source_attempt_id, "source_attempt_id")
        fresh_attempt_id = _optional_token(self.fresh_attempt_id, "fresh_attempt_id")
        if fresh_attempt_id is not None and source_attempt_id is None:
            raise ValueError("fresh_attempt_id requires source_attempt_id")
        if source_attempt_id is not None and source_attempt_id == fresh_attempt_id:
            raise ValueError("source and fresh attempt IDs must be distinct")

        failure_class = _optional_token(self.failure_class, "failure_class")
        failure_signature = _optional_digest(self.failure_signature, "failure_signature")
        if (failure_class is None) != (failure_signature is None):
            raise ValueError("failure_class and failure_signature must be provided together")

        correction_actor = _optional_token(self.correction_actor, "correction_actor")
        previous_model_identity = _optional_token(
            self.previous_model_identity,
            "previous_model_identity",
        )
        current_model_identity = _token(self.current_model_identity, "current_model_identity")
        validator_refs = _token_sequence(self.validator_refs, "validator_refs")

        try:
            state = (
                self.convergence_state
                if isinstance(self.convergence_state, ConvergenceState)
                else ConvergenceState(self.convergence_state)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("convergence_state is unsupported") from exc
        try:
            stop_reason = (
                self.stop_reason
                if self.stop_reason is None or isinstance(self.stop_reason, ConvergenceStopReason)
                else ConvergenceStopReason(self.stop_reason)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("stop_reason is unsupported") from exc

        if state is ConvergenceState.FAST_PATH:
            if (
                self.refinement_round != 0
                or source_attempt_id is not None
                or fresh_attempt_id is not None
                or failure_class is not None
                or failure_signature is not None
                or correction_actor is not None
                or stop_reason is not ConvergenceStopReason.FAST_PATH
            ):
                raise ValueError("FAST_PATH metadata must describe a zero-round success")
        if state is ConvergenceState.REFINEMENT:
            if source_attempt_id is None or failure_class is None or failure_signature is None:
                raise ValueError("REFINEMENT metadata requires source attempt and failure signature")
        if state is ConvergenceState.NON_CONVERGING and stop_reason is None:
            raise ValueError("NON_CONVERGING metadata requires stop_reason")
        if stop_reason is ConvergenceStopReason.FAST_PATH and state is not ConvergenceState.FAST_PATH:
            raise ValueError("FAST_PATH stop_reason requires FAST_PATH state")

        object.__setattr__(self, "source_attempt_id", source_attempt_id)
        object.__setattr__(self, "fresh_attempt_id", fresh_attempt_id)
        object.__setattr__(self, "failure_class", failure_class)
        object.__setattr__(self, "failure_signature", failure_signature)
        object.__setattr__(self, "correction_actor", correction_actor)
        object.__setattr__(self, "previous_model_identity", previous_model_identity)
        object.__setattr__(self, "current_model_identity", current_model_identity)
        object.__setattr__(self, "validator_refs", validator_refs)
        object.__setattr__(self, "convergence_state", state)
        object.__setattr__(self, "stop_reason", stop_reason)

        encoded = self.to_dict()
        ensure_json_safe(encoded, "convergence metadata")
        ensure_secret_free(encoded, "convergence metadata")
        if len(json.dumps(encoded, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > _MAX_CONVERGENCE_BYTES:
            raise ValueError("convergence metadata exceeds its size bound")

    @classmethod
    def fast_path(cls, *, current_model_identity: str) -> "ConvergenceMetadata":
        return cls(
            refinement_round=0,
            source_attempt_id=None,
            fresh_attempt_id=None,
            failure_class=None,
            failure_signature=None,
            correction_actor=None,
            previous_model_identity=None,
            current_model_identity=current_model_identity,
            validator_refs=(),
            convergence_state=ConvergenceState.FAST_PATH,
            stop_reason=ConvergenceStopReason.FAST_PATH,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "refinement_round": self.refinement_round,
            "source_attempt_id": self.source_attempt_id,
            "fresh_attempt_id": self.fresh_attempt_id,
            "failure_class": self.failure_class,
            "failure_signature": self.failure_signature,
            "correction_actor": self.correction_actor,
            "previous_model_identity": self.previous_model_identity,
            "current_model_identity": self.current_model_identity,
            "validator_refs": list(self.validator_refs),
            "convergence_state": self.convergence_state.value,
            "stop_reason": self.stop_reason.value if self.stop_reason is not None else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConvergenceMetadata":
        if not isinstance(value, Mapping):
            raise ValueError("convergence metadata must be an object")
        allowed = {
            "refinement_round",
            "source_attempt_id",
            "fresh_attempt_id",
            "failure_class",
            "failure_signature",
            "correction_actor",
            "previous_model_identity",
            "current_model_identity",
            "validator_refs",
            "convergence_state",
            "stop_reason",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown convergence metadata field: {sorted(unknown)[0]}")
        return cls(
            refinement_round=value.get("refinement_round"),
            source_attempt_id=value.get("source_attempt_id"),
            fresh_attempt_id=value.get("fresh_attempt_id"),
            failure_class=value.get("failure_class"),
            failure_signature=value.get("failure_signature"),
            correction_actor=value.get("correction_actor"),
            previous_model_identity=value.get("previous_model_identity"),
            current_model_identity=value.get("current_model_identity"),
            validator_refs=value.get("validator_refs", ()),
            convergence_state=value.get("convergence_state"),
            stop_reason=value.get("stop_reason"),
        )


def _signature_value(value: Any, name: str) -> str:
    if isinstance(value, FailureFingerprint):
        return value.failure_signature
    if isinstance(value, ConvergenceMetadata):
        signature = value.failure_signature
    else:
        signature = value
    normalized = _optional_digest(signature, name)
    if normalized is None:
        raise ValueError(f"{name} has no failure signature")
    return normalized


def same_failure_signature(left: Any, right: Any) -> bool:
    """Compare only validated digests, never raw diagnostic prose."""

    return _signature_value(left, "left") == _signature_value(right, "right")


__all__ = [
    "ConvergenceMetadata",
    "ConvergenceState",
    "ConvergenceStopReason",
    "FailureFingerprint",
    "same_failure_signature",
]
