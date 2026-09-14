"""Pure, bounded work-position values for the existing coordination plane.

The types in this module annotate existing Task UUIDs and checkpoints.  They
do not select work, schedule Tasks, claim files, call providers, or control
processes.  Durable persistence remains owned by Commander and the existing
Coordination store/artifact boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .protocol import ArtifactReference
from .protocol_helpers import (
    CoordinationValidationError,
    ensure_json_safe,
    ensure_secret_free,
    validate_identifier,
    validate_relative_path,
    validate_string_sequence,
    validate_text,
)


MAX_ADDRESS_DEPTH = 16
MAX_ADDRESS_CHARS = 1024
MAX_INTERRUPT_DEPTH = 8
MAX_INTERRUPT_DEPTH_HARD = 64
MAX_ARTIFACT_REFS = 32
_NUMERIC_SEGMENT = re.compile(r"^[1-9][0-9]*$")
_LETTER_SEGMENT = re.compile(r"^[A-Z]$")


def _child_kind(value: Any) -> str:
    if not isinstance(value, str):
        raise CoordinationValidationError("child kind must be text")
    normalized = value.strip().lower()
    if normalized not in {"numeric", "letter"}:
        raise CoordinationValidationError("child kind must be numeric or letter")
    return normalized


def _address_segment(value: Any, name: str = "address segment") -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 64:
        raise CoordinationValidationError(f"{name} is invalid")
    if _NUMERIC_SEGMENT.fullmatch(value):
        return value
    if _LETTER_SEGMENT.fullmatch(value):
        return value
    raise CoordinationValidationError(
        f"{name} must be a positive integer or a single uppercase letter"
    )


@dataclass(frozen=True)
class WorkAddress:
    """Human-readable position for an existing Task or Step."""

    segments: tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.segments, (str, bytes)) or not isinstance(self.segments, Sequence):
            raise CoordinationValidationError("work address segments must be a sequence")
        segments = tuple(
            _address_segment(segment, f"work address segment {index}")
            for index, segment in enumerate(self.segments)
        )
        if not segments:
            raise CoordinationValidationError("work address must contain at least one segment")
        if len(segments) > MAX_ADDRESS_DEPTH:
            raise CoordinationValidationError("work address exceeds its depth bound")
        if len("-".join(segments)) > MAX_ADDRESS_CHARS:
            raise CoordinationValidationError("work address exceeds its size bound")
        object.__setattr__(self, "segments", segments)

    @classmethod
    def parse(cls, value: Any) -> "WorkAddress":
        if not isinstance(value, str) or not value or value != value.strip():
            raise CoordinationValidationError("work address must be non-empty text")
        if len(value) > MAX_ADDRESS_CHARS:
            raise CoordinationValidationError("work address exceeds its size bound")
        return cls(tuple(value.split("-")))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorkAddress":
        if not isinstance(value, Mapping) or set(value) != {"address"}:
            raise CoordinationValidationError("work address object must contain only address")
        ensure_json_safe(value, "work address")
        ensure_secret_free(value, "work address")
        return cls.parse(value["address"])

    def to_dict(self) -> dict[str, str]:
        return {"address": str(self)}

    @property
    def parent(self) -> "WorkAddress | None":
        if len(self.segments) == 1:
            return None
        return WorkAddress(self.segments[:-1])

    def next_child(
        self,
        existing: Sequence[str | "WorkAddress"],
        *,
        kind: str = "numeric",
    ) -> "WorkAddress":
        """Allocate a deterministic unused direct child address.

        Address allocation is only a collision check.  It does not imply that
        the resulting Task is ready or can run concurrently with its siblings.
        """

        if isinstance(existing, (str, bytes)) or not isinstance(existing, Sequence):
            raise CoordinationValidationError("existing addresses must be a sequence")
        normalized_kind = _child_kind(kind)
        occupied: set[str] = set()
        prefix = self.segments
        for index, item in enumerate(existing):
            parsed = item if isinstance(item, WorkAddress) else WorkAddress.parse(item)
            if parsed.segments[: len(prefix)] == prefix and len(parsed.segments) == len(prefix) + 1:
                occupied.add(parsed.segments[-1])

        if normalized_kind == "letter":
            for codepoint in range(ord("A"), ord("Z") + 1):
                segment = chr(codepoint)
                if segment not in occupied:
                    return WorkAddress(prefix + (segment,))
            raise CoordinationValidationError("no uppercase letter child address is available")

        candidate = 1
        while candidate <= 100_000:
            segment = str(candidate)
            if segment not in occupied:
                return WorkAddress(prefix + (segment,))
            candidate += 1
        raise CoordinationValidationError("no numeric child address is available")

    @classmethod
    def next_root(
        cls,
        existing: Sequence[str | "WorkAddress"],
        *,
        kind: str = "numeric",
    ) -> "WorkAddress":
        """Allocate a deterministic root/lane address without a parent.

        Commander plans do not always persist the external parent Task that
        owns their children.  Root allocation gives those plans a stable
        display address while leaving UUID, dependency, and ownership
        authority unchanged.  Any existing descendant reserves its first
        segment so an automatically-created root cannot split an existing
        address tree.
        """

        if isinstance(existing, (str, bytes)) or not isinstance(existing, Sequence):
            raise CoordinationValidationError("existing addresses must be a sequence")
        normalized_kind = _child_kind(kind)
        occupied: set[str] = set()
        for item in existing:
            parsed = item if isinstance(item, WorkAddress) else cls.parse(item)
            occupied.add(parsed.segments[0])

        if normalized_kind == "letter":
            for codepoint in range(ord("A"), ord("Z") + 1):
                segment = chr(codepoint)
                if segment not in occupied:
                    return cls((segment,))
            raise CoordinationValidationError("no uppercase letter root address is available")

        candidate = 1
        while candidate <= 100_000:
            segment = str(candidate)
            if segment not in occupied:
                return cls((segment,))
            candidate += 1
        raise CoordinationValidationError("no numeric root address is available")

    def __str__(self) -> str:
        return "-".join(self.segments)


class InterruptionMode(Enum):
    NOTE = "NOTE"
    PARALLEL = "PARALLEL"
    INTERRUPT = "INTERRUPT"
    CANCEL = "CANCEL"


@dataclass(frozen=True)
class ResumeCapsule:
    """Small restart/interruption checkpoint, without raw conversation."""

    work_address: WorkAddress
    status: str
    objective: str
    current_action: str
    completed: tuple[str, ...]
    next_action: str
    resume_from: str
    blocked_by: tuple[str, ...]
    owned_paths: tuple[str, ...]
    checkpoint_revision: str
    artifact_refs: tuple[ArtifactReference, ...] = ()
    node_type: str = "task"
    interrupt_stack: "InterruptStack | None" = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.work_address, WorkAddress):
            raise CoordinationValidationError("resume work_address must be a WorkAddress")
        status = validate_text(self.status, "resume status", max_chars=64)
        objective = validate_text(self.objective, "resume objective")
        current_action = validate_text(self.current_action, "resume current_action")
        next_action = validate_text(self.next_action, "resume next_action")
        resume_from = validate_text(self.resume_from, "resume resume_from")
        checkpoint_revision = validate_text(
            self.checkpoint_revision, "resume checkpoint_revision", max_chars=256
        )
        task_id = None if self.task_id is None else validate_identifier(self.task_id, "resume task_id")
        completed = validate_string_sequence(self.completed, "resume completed")
        blocked_by = validate_string_sequence(self.blocked_by, "resume blocked_by")
        if isinstance(self.owned_paths, (str, bytes)) or not isinstance(self.owned_paths, Sequence):
            raise CoordinationValidationError("resume owned_paths must be a sequence")
        if len(self.owned_paths) > 64:
            raise CoordinationValidationError("resume owned_paths has too many items")
        owned_paths = tuple(
            validate_relative_path(path, f"resume owned_paths[{index}]")
            for index, path in enumerate(self.owned_paths)
        )
        if not isinstance(self.artifact_refs, Sequence) or isinstance(self.artifact_refs, (str, bytes)):
            raise CoordinationValidationError("resume artifact_refs must be a sequence")
        if len(self.artifact_refs) > MAX_ARTIFACT_REFS:
            raise CoordinationValidationError("resume artifact_refs has too many items")
        artifact_refs: list[ArtifactReference] = []
        for index, reference in enumerate(self.artifact_refs):
            if isinstance(reference, ArtifactReference):
                artifact = reference
            elif isinstance(reference, Mapping):
                artifact = ArtifactReference.from_dict(reference)
            else:
                raise CoordinationValidationError(f"resume artifact_refs[{index}] is invalid")
            artifact_refs.append(artifact)
        if not isinstance(self.node_type, str) or self.node_type.strip().lower() not in {"task", "step"}:
            raise CoordinationValidationError("resume node_type must be task or step")
        node_type = self.node_type.strip().lower()
        if self.interrupt_stack is None:
            interrupt_stack = InterruptStack()
        elif isinstance(self.interrupt_stack, InterruptStack):
            interrupt_stack = self.interrupt_stack
        elif isinstance(self.interrupt_stack, Mapping):
            interrupt_stack = InterruptStack.from_dict(self.interrupt_stack)
        else:
            raise CoordinationValidationError("resume interrupt_stack must be an InterruptStack")
        normalized = {
            "work_address": str(self.work_address),
            "status": status,
            "objective": objective,
            "current_action": current_action,
            "completed": list(completed),
            "next_action": next_action,
            "resume_from": resume_from,
            "blocked_by": list(blocked_by),
            "owned_paths": list(owned_paths),
            "checkpoint_revision": checkpoint_revision,
            "artifact_refs": [item.to_dict() for item in artifact_refs],
            "node_type": node_type,
            "interrupt_stack": interrupt_stack.to_dict(),
        }
        if task_id is not None:
            normalized["task_id"] = task_id
        ensure_json_safe(normalized, "resume capsule")
        ensure_secret_free(normalized, "resume capsule")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "current_action", current_action)
        object.__setattr__(self, "completed", completed)
        object.__setattr__(self, "next_action", next_action)
        object.__setattr__(self, "resume_from", resume_from)
        object.__setattr__(self, "blocked_by", blocked_by)
        object.__setattr__(self, "owned_paths", owned_paths)
        object.__setattr__(self, "checkpoint_revision", checkpoint_revision)
        object.__setattr__(self, "artifact_refs", tuple(artifact_refs))
        object.__setattr__(self, "node_type", node_type)
        object.__setattr__(self, "interrupt_stack", interrupt_stack)
        object.__setattr__(self, "task_id", task_id)

    def to_interrupt_frame(self, *, task_id: str | None = None) -> "InterruptFrame":
        """Project this checkpoint into the bounded parent return frame."""

        resolved_task_id = task_id if task_id is not None else self.task_id
        if resolved_task_id is None:
            raise CoordinationValidationError(
                "resume task_id is required to create an interrupt frame"
            )
        return InterruptFrame(
            task_id=resolved_task_id,
            work_address=self.work_address,
            resume_from=self.resume_from,
            next_action=self.next_action,
            checkpoint_revision=self.checkpoint_revision,
        )

    def push_interrupt(self, frame: "InterruptFrame") -> "ResumeCapsule":
        """Return a suspended projection with one bounded parent frame pushed."""

        if not isinstance(frame, InterruptFrame):
            raise CoordinationValidationError("interrupt frame must be an InterruptFrame")
        return replace(
            self,
            status="SUSPENDED_BY_INTERRUPT",
            interrupt_stack=self.interrupt_stack.push(frame),
        )

    def pop_interrupt(self) -> tuple["InterruptFrame", "ResumeCapsule"]:
        """Pop the latest parent frame and restore its position fields.

        The existing Task/checkpoint identified by the returned frame remains
        authoritative for the parent's full state.  This value-layer helper
        only restores the bounded position/next-action projection; it performs
        no lookup, scheduling, or process operation.
        """

        frame, stack = self.interrupt_stack.pop()
        resumed = replace(
            self,
            task_id=frame.task_id,
            work_address=frame.work_address,
            status="RUNNING",
            current_action=frame.next_action,
            next_action=frame.next_action,
            resume_from=frame.resume_from,
            checkpoint_revision=frame.checkpoint_revision,
            interrupt_stack=stack,
        )
        return frame, resumed

    def to_dict(self) -> dict[str, Any]:
        value = {
            "work_address": str(self.work_address),
            "status": self.status,
            "objective": self.objective,
            "current_action": self.current_action,
            "completed": list(self.completed),
            "next_action": self.next_action,
            "resume_from": self.resume_from,
            "blocked_by": list(self.blocked_by),
            "owned_paths": list(self.owned_paths),
            "checkpoint_revision": self.checkpoint_revision,
            "artifact_refs": [item.to_dict() for item in self.artifact_refs],
            "node_type": self.node_type,
            "interrupt_stack": self.interrupt_stack.to_dict(),
        }
        if self.task_id is not None:
            value["task_id"] = self.task_id
        ensure_json_safe(value, "resume capsule")
        ensure_secret_free(value, "resume capsule")
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResumeCapsule":
        if not isinstance(value, Mapping):
            raise CoordinationValidationError("resume capsule must be an object")
        ensure_json_safe(value, "resume capsule")
        ensure_secret_free(value, "resume capsule")
        raw_address = value.get("work_address")
        if isinstance(raw_address, Mapping):
            address = WorkAddress.from_dict(raw_address)
        else:
            address = WorkAddress.parse(raw_address)
        required = (
            "status",
            "objective",
            "current_action",
            "next_action",
            "resume_from",
            "checkpoint_revision",
        )
        missing = [name for name in required if name not in value]
        if missing:
            raise CoordinationValidationError(f"resume capsule missing: {missing[0]}")
        return cls(
            work_address=address,
            status=value["status"],
            objective=value["objective"],
            current_action=value["current_action"],
            completed=value.get("completed", ()),
            next_action=value["next_action"],
            resume_from=value["resume_from"],
            blocked_by=value.get("blocked_by", ()),
            owned_paths=value.get("owned_paths", ()),
            checkpoint_revision=value["checkpoint_revision"],
            artifact_refs=value.get("artifact_refs", ()),
            node_type=value.get("node_type", "task"),
            interrupt_stack=value.get("interrupt_stack"),
            task_id=value.get("task_id"),
        )


@dataclass(frozen=True)
class InterruptFrame:
    """Parent checkpoint to restore after an interrupt child completes."""

    task_id: str
    work_address: WorkAddress
    resume_from: str
    next_action: str
    checkpoint_revision: str

    def __post_init__(self) -> None:
        task_id = validate_identifier(self.task_id, "interrupt task_id")
        if not isinstance(self.work_address, WorkAddress):
            raise CoordinationValidationError("interrupt work_address must be a WorkAddress")
        resume_from = validate_text(self.resume_from, "interrupt resume_from")
        next_action = validate_text(self.next_action, "interrupt next_action")
        checkpoint_revision = validate_text(
            self.checkpoint_revision, "interrupt checkpoint_revision", max_chars=256
        )
        value = {
            "task_id": task_id,
            "work_address": str(self.work_address),
            "resume_from": resume_from,
            "next_action": next_action,
            "checkpoint_revision": checkpoint_revision,
        }
        ensure_json_safe(value, "interrupt frame")
        ensure_secret_free(value, "interrupt frame")
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "resume_from", resume_from)
        object.__setattr__(self, "next_action", next_action)
        object.__setattr__(self, "checkpoint_revision", checkpoint_revision)

    def to_dict(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "work_address": str(self.work_address),
            "resume_from": self.resume_from,
            "next_action": self.next_action,
            "checkpoint_revision": self.checkpoint_revision,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "InterruptFrame":
        if not isinstance(value, Mapping):
            raise CoordinationValidationError("interrupt frame must be an object")
        return cls(
            task_id=value.get("task_id"),
            work_address=WorkAddress.parse(value.get("work_address")),
            resume_from=value.get("resume_from"),
            next_action=value.get("next_action"),
            checkpoint_revision=value.get("checkpoint_revision"),
        )


@dataclass(frozen=True)
class InterruptStack:
    """Immutable bounded LIFO stack of parent resume frames."""

    max_depth: int = MAX_INTERRUPT_DEPTH
    frames: tuple[InterruptFrame, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if isinstance(self.max_depth, bool) or not isinstance(self.max_depth, int):
            raise CoordinationValidationError("interrupt max_depth must be an integer")
        if not 0 < self.max_depth <= MAX_INTERRUPT_DEPTH_HARD:
            raise CoordinationValidationError("interrupt max_depth is outside its bound")
        if isinstance(self.frames, (str, bytes)) or not isinstance(self.frames, Sequence):
            raise CoordinationValidationError("interrupt frames must be a sequence")
        frames = tuple(self.frames)
        if len(frames) > self.max_depth:
            raise CoordinationValidationError("interrupt stack depth exceeded")
        if any(not isinstance(frame, InterruptFrame) for frame in frames):
            raise CoordinationValidationError("interrupt stack contains an invalid frame")
        object.__setattr__(self, "frames", frames)

    def push(self, frame: InterruptFrame) -> "InterruptStack":
        if not isinstance(frame, InterruptFrame):
            raise CoordinationValidationError("interrupt stack accepts InterruptFrame values")
        if len(self.frames) >= self.max_depth:
            raise CoordinationValidationError("interrupt stack depth exceeded")
        return InterruptStack(max_depth=self.max_depth, frames=self.frames + (frame,))

    def pop(self) -> tuple[InterruptFrame, "InterruptStack"]:
        if not self.frames:
            raise CoordinationValidationError("interrupt stack is empty")
        top = self.frames[-1]
        return top, InterruptStack(max_depth=self.max_depth, frames=self.frames[:-1])

    def to_dict(self) -> dict[str, Any]:
        value = {"max_depth": self.max_depth, "frames": [frame.to_dict() for frame in self.frames]}
        ensure_json_safe(value, "interrupt stack")
        ensure_secret_free(value, "interrupt stack")
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "InterruptStack":
        if not isinstance(value, Mapping):
            raise CoordinationValidationError("interrupt stack must be an object")
        frames = value.get("frames", ())
        if isinstance(frames, (str, bytes)) or not isinstance(frames, Sequence):
            raise CoordinationValidationError("interrupt frames must be a sequence")
        return cls(
            max_depth=value.get("max_depth", MAX_INTERRUPT_DEPTH),
            frames=tuple(InterruptFrame.from_dict(frame) for frame in frames),
        )


def classify_intervention(value: Any) -> InterruptionMode:
    """Normalize only an explicit mode; ambiguous input remains a NOTE."""

    if isinstance(value, InterruptionMode):
        return value
    if isinstance(value, str):
        normalized = value.strip().upper()
        for mode in InterruptionMode:
            if normalized == mode.value:
                return mode
    return InterruptionMode.NOTE


def allocate_work_address(
    parent: WorkAddress | str | None,
    existing: Sequence[str | WorkAddress],
    *,
    kind: str = "numeric",
) -> WorkAddress:
    """Allocate one address for a Host-owned task projection.

    This helper only allocates a display position.  The caller still owns
    persistence, dependencies, file ownership, leases, and queue actions.
    ``parent=None`` is used for a Commander plan whose external parent is not
    represented in the plan file.
    """

    if parent is None:
        return WorkAddress.next_root(existing, kind=kind)
    parsed_parent = parent if isinstance(parent, WorkAddress) else WorkAddress.parse(parent)
    return parsed_parent.next_child(existing, kind=kind)


__all__ = [
    "MAX_ADDRESS_DEPTH",
    "MAX_INTERRUPT_DEPTH",
    "CoordinationValidationError",
    "InterruptFrame",
    "InterruptStack",
    "InterruptionMode",
    "ResumeCapsule",
    "WorkAddress",
    "allocate_work_address",
    "classify_intervention",
]
