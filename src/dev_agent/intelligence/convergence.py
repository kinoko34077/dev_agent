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
    RECURRENT_CYCLE = "RECURRENT_CYCLE"
    NON_CONVERGING = "NON_CONVERGING"
    EXTERNAL_RECONCILIATION = "EXTERNAL_RECONCILIATION"
    AUTHORITY_REQUIRED = "AUTHORITY_REQUIRED"
    VALIDATION_FAILED = "VALIDATION_FAILED"


class ValidationRung(str, Enum):
    """Ordered validation stages for one immutable attempt."""

    V0 = "V0"
    V1 = "V1"
    V2 = "V2"
    V3 = "V3"
    V4 = "V4"
    V5 = "V5"
    V6 = "V6"


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,255}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_REFS = 64
_MAX_ROUND = 64
_MAX_CONVERGENCE_BYTES = 32 * 1024
_VALIDATION_ORDER = (
    ValidationRung.V0,
    ValidationRung.V1,
    ValidationRung.V2,
    ValidationRung.V3,
    ValidationRung.V4,
    ValidationRung.V5,
    ValidationRung.V6,
)
_MAX_FAILURE_SPEC_CHARS = 2_000
_MAX_FAILURE_SPEC_ITEMS = 32
_NON_ACTIONABLE_DIRECTIVES = frozenset({
    "improve",
    "fix",
    "fix appropriately",
    "handle the error",
    "make it work",
})


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


def _bounded_failure_text(value: Any, name: str) -> str:
    ensure_secret_free({"value": value}, name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    normalized = value.strip()
    if len(normalized) > _MAX_FAILURE_SPEC_CHARS:
        raise ValueError(f"{name} exceeds its input limit")
    if "\x00" in normalized:
        raise ValueError(f"{name} must not contain NUL bytes")
    return normalized


def _bounded_failure_items(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a sequence")
    if len(value) > _MAX_FAILURE_SPEC_ITEMS:
        raise ValueError(f"{name} contains too many items")
    result = tuple(_bounded_failure_text(item, f"{name}[]") for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must not contain duplicates")
    return result


@dataclass(frozen=True)
class ConcreteFailureSpec:
    """Host-derived, bounded facts that make one failure actionable."""

    failure_class: str
    stage: str
    location: str
    observed: str
    expected: str
    problem: str
    required_correction: str
    must_preserve: tuple[str, ...] = field(default_factory=tuple)
    forbidden_changes: tuple[str, ...] = field(default_factory=tuple)
    acceptance_checks: tuple[str, ...] = field(default_factory=tuple)
    validator_refs: tuple[str, ...] = field(default_factory=tuple)
    failure_signature: str = field(init=False)

    def __post_init__(self) -> None:
        failure_class = _token(self.failure_class, "failure_class")
        stage = _token(self.stage, "stage")
        location = _bounded_failure_text(self.location, "location")
        observed = _bounded_failure_text(self.observed, "observed")
        expected = _bounded_failure_text(self.expected, "expected")
        problem = _bounded_failure_text(self.problem, "problem")
        required_correction = _bounded_failure_text(self.required_correction, "required_correction")
        must_preserve = _bounded_failure_items(self.must_preserve, "must_preserve")
        forbidden_changes = _bounded_failure_items(self.forbidden_changes, "forbidden_changes")
        acceptance_checks = _bounded_failure_items(self.acceptance_checks, "acceptance_checks")
        validator_refs = _token_sequence(self.validator_refs, "validator_refs")
        object.__setattr__(self, "failure_class", failure_class)
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "observed", observed)
        object.__setattr__(self, "expected", expected)
        object.__setattr__(self, "problem", problem)
        object.__setattr__(self, "required_correction", required_correction)
        object.__setattr__(self, "must_preserve", must_preserve)
        object.__setattr__(self, "forbidden_changes", forbidden_changes)
        object.__setattr__(self, "acceptance_checks", acceptance_checks)
        object.__setattr__(self, "validator_refs", validator_refs)
        encoded = json.dumps(self._canonical_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        object.__setattr__(self, "failure_signature", hashlib.sha256(encoded.encode("utf-8")).hexdigest())
        ensure_json_safe(self.to_dict(), "concrete failure spec")
        ensure_secret_free(self.to_dict(), "concrete failure spec")

    def _canonical_payload(self) -> dict[str, Any]:
        return {
            "failure_class": self.failure_class,
            "stage": self.stage,
            "location": self.location,
            "observed": self.observed,
            "expected": self.expected,
            "problem": self.problem,
            "required_correction": self.required_correction,
            "must_preserve": list(self.must_preserve),
            "forbidden_changes": list(self.forbidden_changes),
            "acceptance_checks": list(self.acceptance_checks),
            "validator_refs": list(self.validator_refs),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._canonical_payload(), "failure_signature": self.failure_signature}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConcreteFailureSpec":
        if not isinstance(value, Mapping):
            raise ValueError("concrete failure spec must be an object")
        allowed = {
            "failure_class", "stage", "location", "observed", "expected", "problem",
            "required_correction", "must_preserve", "forbidden_changes", "acceptance_checks",
            "validator_refs", "failure_signature",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown concrete failure spec field: {sorted(unknown)[0]}")
        spec = cls(
            failure_class=value.get("failure_class"),
            stage=value.get("stage"),
            location=value.get("location"),
            observed=value.get("observed"),
            expected=value.get("expected"),
            problem=value.get("problem"),
            required_correction=value.get("required_correction"),
            must_preserve=tuple(value.get("must_preserve", ())),
            forbidden_changes=tuple(value.get("forbidden_changes", ())),
            acceptance_checks=tuple(value.get("acceptance_checks", ())),
            validator_refs=tuple(value.get("validator_refs", ())),
        )
        supplied = _optional_digest(value.get("failure_signature"), "failure_signature")
        if supplied != spec.failure_signature:
            raise ValueError("failure_signature does not match concrete failure spec")
        return spec


@dataclass(frozen=True)
class RepairDirective:
    """A concrete, bounded instruction derived from a Host failure spec."""

    repair_target: str
    previous_problem: str
    required_action: str
    must_preserve: tuple[str, ...] = field(default_factory=tuple)
    forbidden: tuple[str, ...] = field(default_factory=tuple)
    completion_condition: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        target = _bounded_failure_text(self.repair_target, "repair_target")
        previous_problem = _bounded_failure_text(self.previous_problem, "previous_problem")
        required_action = _bounded_failure_text(self.required_action, "required_action")
        if required_action.casefold() in _NON_ACTIONABLE_DIRECTIVES:
            raise ValueError("required_action must be actionable")
        preserve = _bounded_failure_items(self.must_preserve, "must_preserve")
        forbidden = _bounded_failure_items(self.forbidden, "forbidden")
        completion = _bounded_failure_items(self.completion_condition, "completion_condition")
        if not completion:
            raise ValueError("completion_condition must not be empty")
        object.__setattr__(self, "repair_target", target)
        object.__setattr__(self, "previous_problem", previous_problem)
        object.__setattr__(self, "required_action", required_action)
        object.__setattr__(self, "must_preserve", preserve)
        object.__setattr__(self, "forbidden", forbidden)
        object.__setattr__(self, "completion_condition", completion)
        ensure_json_safe(self.to_dict(), "repair directive")
        ensure_secret_free(self.to_dict(), "repair directive")

    @property
    def directive_hash(self) -> str:
        """Return a deterministic identity for this bounded correction."""

        encoded = json.dumps(
            self._canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _canonical_payload(self) -> dict[str, Any]:
        return {
            "repair_target": self.repair_target,
            "previous_problem": self.previous_problem,
            "required_action": self.required_action,
            "must_preserve": list(self.must_preserve),
            "forbidden": list(self.forbidden),
            "completion_condition": list(self.completion_condition),
        }

    @classmethod
    def from_failure_spec(cls, spec: ConcreteFailureSpec) -> "RepairDirective":
        if not isinstance(spec, ConcreteFailureSpec):
            raise TypeError("spec must be ConcreteFailureSpec")
        return cls(
            repair_target=spec.location,
            previous_problem=spec.problem,
            required_action=spec.required_correction,
            must_preserve=spec.must_preserve,
            forbidden=spec.forbidden_changes,
            completion_condition=spec.acceptance_checks,
        )

    def to_dict(self) -> dict[str, Any]:
        return {**self._canonical_payload(), "directive_hash": self.directive_hash}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RepairDirective":
        if not isinstance(value, Mapping):
            raise ValueError("repair directive must be an object")
        allowed = {
            "repair_target", "previous_problem", "required_action", "must_preserve",
            "forbidden", "completion_condition", "directive_hash",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown repair directive field: {sorted(unknown)[0]}")
        directive = cls(
            repair_target=value.get("repair_target"),
            previous_problem=value.get("previous_problem"),
            required_action=value.get("required_action"),
            must_preserve=tuple(value.get("must_preserve", ())),
            forbidden=tuple(value.get("forbidden", ())),
            completion_condition=tuple(value.get("completion_condition", ())),
        )
        supplied = _optional_digest(value.get("directive_hash"), "directive_hash")
        if supplied is not None and supplied != directive.directive_hash:
            raise ValueError("directive_hash does not match repair directive")
        return directive


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
    failure_spec: ConcreteFailureSpec | None = None
    repair_directive: RepairDirective | None = None

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
        if self.failure_spec is not None and not isinstance(self.failure_spec, ConcreteFailureSpec):
            raise ValueError("failure_spec must be ConcreteFailureSpec or None")
        if self.repair_directive is not None and not isinstance(self.repair_directive, RepairDirective):
            raise ValueError("repair_directive must be RepairDirective or None")
        if self.repair_directive is not None and self.failure_spec is None:
            raise ValueError("repair_directive requires failure_spec")
        # ``failure_signature`` identifies the coarse recurrence fingerprint,
        # while ConcreteFailureSpec carries its own digest for the detailed
        # actionable facts. They intentionally use different canonical
        # payloads; retaining both lets a later model receive concrete repair
        # information without changing the bounded recurrence identity.

        if state is ConvergenceState.FAST_PATH:
            if (
                self.refinement_round != 0
                or source_attempt_id is not None
                or fresh_attempt_id is not None
                or failure_class is not None
                or failure_signature is not None
                or correction_actor is not None
                or stop_reason is not ConvergenceStopReason.FAST_PATH
                or self.failure_spec is not None
                or self.repair_directive is not None
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
    def fast_path(
        cls,
        *,
        current_model_identity: str,
        validator_refs: Sequence[Any] = (),
    ) -> "ConvergenceMetadata":
        return cls(
            refinement_round=0,
            source_attempt_id=None,
            fresh_attempt_id=None,
            failure_class=None,
            failure_signature=None,
            correction_actor=None,
            previous_model_identity=None,
            current_model_identity=current_model_identity,
            validator_refs=tuple(validator_refs),
            convergence_state=ConvergenceState.FAST_PATH,
            stop_reason=ConvergenceStopReason.FAST_PATH,
        )

    @classmethod
    def from_validation(
        cls,
        *,
        passed: bool,
        refinement_round: int,
        current_model_identity: str,
        validator_refs: Sequence[Any] = (),
        source_attempt_id: str | None = None,
        fresh_attempt_id: str | None = None,
        failure: FailureFingerprint | None = None,
        failure_spec: ConcreteFailureSpec | None = None,
        repair_directive: RepairDirective | None = None,
        correction_actor: str | None = None,
        previous_model_identity: str | None = None,
    ) -> "ConvergenceMetadata":
        """Project one validation result onto the fast/refinement paths."""

        if not isinstance(passed, bool):
            raise ValueError("passed must be a boolean")
        if passed and failure is not None:
            raise ValueError("a passing validation cannot carry a failure")
        if not passed and not isinstance(failure, FailureFingerprint):
            raise ValueError("a failed validation requires a FailureFingerprint")
        if passed and (failure_spec is not None or repair_directive is not None):
            raise ValueError("a passing validation cannot carry failure details")
        if repair_directive is not None and failure_spec is None:
            raise ValueError("repair_directive requires failure_spec")
        if failure_spec is not None and repair_directive is None:
            repair_directive = RepairDirective.from_failure_spec(failure_spec)
        if passed and refinement_round == 0:
            return cls.fast_path(
                current_model_identity=current_model_identity,
                validator_refs=validator_refs,
            )
        return cls(
            refinement_round=refinement_round,
            source_attempt_id=source_attempt_id,
            fresh_attempt_id=fresh_attempt_id,
            failure_class=failure.failure_class if failure is not None else None,
            failure_signature=failure.failure_signature if failure is not None else None,
            correction_actor=correction_actor,
            previous_model_identity=previous_model_identity,
            current_model_identity=current_model_identity,
            validator_refs=tuple(validator_refs),
            convergence_state=ConvergenceState.REFINEMENT if not passed else ConvergenceState.COMPLETED,
            stop_reason=None,
            failure_spec=failure_spec,
            repair_directive=repair_directive,
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
            "failure_spec": self.failure_spec.to_dict() if self.failure_spec is not None else None,
            "repair_directive": self.repair_directive.to_dict() if self.repair_directive is not None else None,
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
            "failure_spec",
            "repair_directive",
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
            failure_spec=(
                None
                if value.get("failure_spec") is None
                else ConcreteFailureSpec.from_dict(value.get("failure_spec"))
            ),
            repair_directive=(
                None
                if value.get("repair_directive") is None
                else RepairDirective.from_dict(value.get("repair_directive"))
            ),
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


@dataclass(frozen=True)
class ValidationObservation:
    """One bounded result from the ordered validation ladder."""

    rung: ValidationRung
    passed: bool
    validator_refs: tuple[str, ...] = field(default_factory=tuple)
    failure: FailureFingerprint | None = None

    def __post_init__(self) -> None:
        try:
            rung = self.rung if isinstance(self.rung, ValidationRung) else ValidationRung(self.rung)
        except (TypeError, ValueError) as exc:
            raise ValueError("rung is unsupported") from exc
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be a boolean")
        refs = _token_sequence(self.validator_refs, "validator_refs")
        if self.passed and self.failure is not None:
            raise ValueError("a passing validation cannot carry a failure")
        if not self.passed and not isinstance(self.failure, FailureFingerprint):
            raise ValueError("a failed validation requires a FailureFingerprint")
        object.__setattr__(self, "rung", rung)
        object.__setattr__(self, "validator_refs", refs)
        encoded = self.to_dict()
        ensure_json_safe(encoded, "validation observation")
        ensure_secret_free(encoded, "validation observation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rung": self.rung.value,
            "passed": self.passed,
            "validator_refs": list(self.validator_refs),
            "failure": self.failure.to_dict() if self.failure is not None else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidationObservation":
        if not isinstance(value, Mapping):
            raise ValueError("validation observation must be an object")
        allowed = {"rung", "passed", "validator_refs", "failure"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown validation observation field: {sorted(unknown)[0]}")
        failure_value = value.get("failure")
        failure = None if failure_value is None else FailureFingerprint.from_dict(failure_value)
        return cls(
            rung=value.get("rung"),
            passed=value.get("passed"),
            validator_refs=value.get("validator_refs", ()),
            failure=failure,
        )


@dataclass(frozen=True)
class ValidationLadder:
    """Prefix of V0..V6; a failure short-circuits all later rungs."""

    observations: tuple[ValidationObservation, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if isinstance(self.observations, (str, bytes)) or not isinstance(self.observations, Sequence):
            raise ValueError("observations must be a sequence")
        observations = tuple(self.observations)
        if len(observations) > len(_VALIDATION_ORDER) or any(
            not isinstance(item, ValidationObservation) for item in observations
        ):
            raise ValueError("observations must be an ordered validation prefix")
        for index, observation in enumerate(observations):
            if observation.rung is not _VALIDATION_ORDER[index]:
                raise ValueError("validation observations must start at V0 and remain ordered")
            if not observation.passed and index != len(observations) - 1:
                raise ValueError("validation failure must be the final observed rung")
        object.__setattr__(self, "observations", observations)
        encoded = self.to_dict()
        ensure_json_safe(encoded, "validation ladder")
        ensure_secret_free(encoded, "validation ladder")

    @property
    def first_failure(self) -> ValidationObservation | None:
        if self.observations and not self.observations[-1].passed:
            return self.observations[-1]
        return None

    @property
    def next_rung(self) -> ValidationRung | None:
        if self.first_failure is not None:
            return ValidationRung.V0
        if len(self.observations) == len(_VALIDATION_ORDER):
            return None
        return _VALIDATION_ORDER[len(self.observations)]

    def record(self, observation: ValidationObservation) -> "ValidationLadder":
        if not isinstance(observation, ValidationObservation):
            raise TypeError("observation must be ValidationObservation")
        if self.first_failure is not None:
            raise ValueError("a failed ladder must restart at V0 for a fresh attempt")
        return ValidationLadder(observations=(*self.observations, observation))

    def restart_after_correction(self) -> "ValidationLadder":
        if self.first_failure is None:
            raise ValueError("only a failed ladder can restart after correction")
        return ValidationLadder()

    def to_dict(self) -> dict[str, Any]:
        return {"observations": [item.to_dict() for item in self.observations]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidationLadder":
        if not isinstance(value, Mapping):
            raise ValueError("validation ladder must be an object")
        allowed = {"observations"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown validation ladder field: {sorted(unknown)[0]}")
        observations = value.get("observations", ())
        if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
            raise ValueError("observations must be a sequence")
        return cls(observations=tuple(ValidationObservation.from_dict(item) for item in observations))


@dataclass(frozen=True)
class ConvergenceObservation:
    """Sanitized state used to compare two adjacent refinement attempts."""

    failure_count: int
    validation_rung: ValidationRung | None
    failure_signature: str | None
    refinement_round: int

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("failure_count", self.failure_count, 64),
            ("refinement_round", self.refinement_round, _MAX_ROUND),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
                raise ValueError(f"{name} must be an integer between 0 and {maximum}")
        rung = self.validation_rung
        if rung is not None:
            try:
                rung = rung if isinstance(rung, ValidationRung) else ValidationRung(rung)
            except (TypeError, ValueError) as exc:
                raise ValueError("validation_rung is unsupported") from exc
        signature = _optional_digest(self.failure_signature, "failure_signature")
        if self.failure_count == 0 and signature is not None:
            raise ValueError("zero failures cannot carry a failure signature")
        if self.failure_count > 0 and signature is None:
            raise ValueError("a failed observation requires a failure signature")
        object.__setattr__(self, "validation_rung", rung)
        object.__setattr__(self, "failure_signature", signature)
        encoded = self.to_dict()
        ensure_json_safe(encoded, "convergence observation")
        ensure_secret_free(encoded, "convergence observation")

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_count": self.failure_count,
            "validation_rung": self.validation_rung.value if self.validation_rung is not None else None,
            "failure_signature": self.failure_signature,
            "refinement_round": self.refinement_round,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConvergenceObservation":
        if not isinstance(value, Mapping):
            raise ValueError("convergence observation must be an object")
        allowed = {"failure_count", "validation_rung", "failure_signature", "refinement_round"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown convergence observation field: {sorted(unknown)[0]}")
        return cls(
            failure_count=value.get("failure_count"),
            validation_rung=value.get("validation_rung"),
            failure_signature=value.get("failure_signature"),
            refinement_round=value.get("refinement_round"),
        )


@dataclass(frozen=True)
class ConvergenceAssessment:
    """Bounded, serializable comparison result for adjacent attempts."""

    previous: ConvergenceObservation
    current: ConvergenceObservation
    state: ConvergenceState
    failure_count_delta: int
    validation_advanced: bool
    signature_resolved: bool
    failure_changed: bool
    reasons: tuple[str, ...]
    stop_reason: ConvergenceStopReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.previous, ConvergenceObservation) or not isinstance(self.current, ConvergenceObservation):
            raise ValueError("assessment observations must be ConvergenceObservation values")
        try:
            state = self.state if isinstance(self.state, ConvergenceState) else ConvergenceState(self.state)
        except (TypeError, ValueError) as exc:
            raise ValueError("assessment state is unsupported") from exc
        if isinstance(self.failure_count_delta, bool) or not isinstance(self.failure_count_delta, int):
            raise ValueError("failure_count_delta must be an integer")
        for name, value in (
            ("validation_advanced", self.validation_advanced),
            ("signature_resolved", self.signature_resolved),
            ("failure_changed", self.failure_changed),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean")
        reasons = _token_sequence(self.reasons, "reasons")
        if state is ConvergenceState.NON_CONVERGING and self.stop_reason is None:
            raise ValueError("NON_CONVERGING assessment requires stop_reason")
        if state is not ConvergenceState.NON_CONVERGING and self.stop_reason is not None:
            raise ValueError("stop_reason is only valid for NON_CONVERGING assessment")
        try:
            stop_reason = (
                self.stop_reason
                if self.stop_reason is None or isinstance(self.stop_reason, ConvergenceStopReason)
                else ConvergenceStopReason(self.stop_reason)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("assessment stop_reason is unsupported") from exc
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "stop_reason", stop_reason)
        encoded = self.to_dict()
        ensure_json_safe(encoded, "convergence assessment")
        ensure_secret_free(encoded, "convergence assessment")

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous": self.previous.to_dict(),
            "current": self.current.to_dict(),
            "state": self.state.value,
            "failure_count_delta": self.failure_count_delta,
            "validation_advanced": self.validation_advanced,
            "signature_resolved": self.signature_resolved,
            "failure_changed": self.failure_changed,
            "reasons": list(self.reasons),
            "stop_reason": self.stop_reason.value if self.stop_reason is not None else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConvergenceAssessment":
        if not isinstance(value, Mapping):
            raise ValueError("convergence assessment must be an object")
        allowed = {
            "previous",
            "current",
            "state",
            "failure_count_delta",
            "validation_advanced",
            "signature_resolved",
            "failure_changed",
            "reasons",
            "stop_reason",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown convergence assessment field: {sorted(unknown)[0]}")
        reasons = value.get("reasons", ())
        if isinstance(reasons, (str, bytes)) or not isinstance(reasons, Sequence):
            raise ValueError("assessment reasons must be a sequence")
        return cls(
            previous=ConvergenceObservation.from_dict(value.get("previous")),
            current=ConvergenceObservation.from_dict(value.get("current")),
            state=value.get("state"),
            failure_count_delta=value.get("failure_count_delta"),
            validation_advanced=value.get("validation_advanced"),
            signature_resolved=value.get("signature_resolved"),
            failure_changed=value.get("failure_changed"),
            reasons=tuple(reasons),
            stop_reason=value.get("stop_reason"),
        )


def _validation_position(rung: ValidationRung | None) -> int:
    return -1 if rung is None else _VALIDATION_ORDER.index(rung)


def assess_convergence(
    previous: ConvergenceObservation,
    current: ConvergenceObservation,
    *,
    same_signature_count: int = 1,
    same_signature_limit: int = 2,
    max_refinement_rounds: int = 4,
    history: Sequence[ConvergenceObservation] = (),
) -> ConvergenceAssessment:
    """Compare adjacent attempts without dispatching or owning persistence."""

    if not isinstance(previous, ConvergenceObservation) or not isinstance(current, ConvergenceObservation):
        raise TypeError("previous and current must be ConvergenceObservation values")
    for name, value, minimum in (
        ("same_signature_count", same_signature_count, 1),
        ("same_signature_limit", same_signature_limit, 1),
        ("max_refinement_rounds", max_refinement_rounds, 1),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")
    if same_signature_count > same_signature_limit:
        raise ValueError("same_signature_count cannot exceed same_signature_limit")
    if max_refinement_rounds > _MAX_ROUND:
        raise ValueError("max_refinement_rounds exceeds the convergence bound")
    if isinstance(history, (str, bytes)) or not isinstance(history, Sequence):
        raise TypeError("history must be a sequence of ConvergenceObservation values")
    if len(history) > 4:
        raise ValueError("history is limited to the most recent four observations")
    if any(not isinstance(item, ConvergenceObservation) for item in history):
        raise TypeError("history must contain ConvergenceObservation values")

    failure_count_delta = current.failure_count - previous.failure_count
    validation_advanced = _validation_position(current.validation_rung) > _validation_position(previous.validation_rung)
    signature_resolved = previous.failure_signature is not None and current.failure_signature is None
    failure_changed = (
        previous.failure_signature is not None
        and current.failure_signature is not None
        and previous.failure_signature != current.failure_signature
    )
    same_signature = (
        previous.failure_signature is not None
        and previous.failure_signature == current.failure_signature
    )
    reasons: list[str] = []
    if failure_count_delta < 0:
        reasons.append("failure_count_reduced")
    if validation_advanced:
        reasons.append("validation_rung_advanced")
    if signature_resolved:
        reasons.append("failure_signature_resolved")
    observations = (*history, previous, current)
    signatures = tuple(
        item.failure_signature for item in observations if item.failure_signature is not None
    )
    recurrent_cycle = False
    for period in (2, 3):
        if len(signatures) < period + 1:
            continue
        window_length = min(len(signatures), period * 2)
        window = signatures[-window_length:]
        if all(window[index] == window[index - period] for index in range(period, len(window))):
            recurrent_cycle = True
            break

    bounded_progress = bool(reasons)
    if bounded_progress:
        state = ConvergenceState.PROGRESS
        stop_reason = None
    elif recurrent_cycle:
        state = ConvergenceState.NON_CONVERGING
        stop_reason = ConvergenceStopReason.RECURRENT_CYCLE
        reasons.append("recurrent_failure_cycle")
    elif current.refinement_round >= max_refinement_rounds:
        state = ConvergenceState.NON_CONVERGING
        stop_reason = ConvergenceStopReason.BUDGET_EXHAUSTED
        reasons.append("refinement_budget_exhausted")
    elif same_signature and same_signature_count >= same_signature_limit:
        state = ConvergenceState.NON_CONVERGING
        stop_reason = ConvergenceStopReason.SAME_SIGNATURE_LIMIT
        reasons.append("same_failure_signature_limit")
    elif same_signature:
        state = ConvergenceState.STUCK
        stop_reason = None
        reasons.append("same_failure_signature")
    else:
        state = ConvergenceState.NO_PROGRESS
        stop_reason = None
        if not recurrent_cycle:
            reasons.append("no_bounded_progress")

    return ConvergenceAssessment(
        previous=previous,
        current=current,
        state=state,
        failure_count_delta=failure_count_delta,
        validation_advanced=validation_advanced,
        signature_resolved=signature_resolved,
        failure_changed=failure_changed,
        reasons=tuple(reasons),
        stop_reason=stop_reason,
    )


__all__ = [
    "ConcreteFailureSpec",
    "ConvergenceAssessment",
    "ConvergenceMetadata",
    "ConvergenceObservation",
    "ConvergenceState",
    "ConvergenceStopReason",
    "FailureFingerprint",
    "RepairDirective",
    "ValidationLadder",
    "ValidationObservation",
    "ValidationRung",
    "assess_convergence",
    "same_failure_signature",
]
