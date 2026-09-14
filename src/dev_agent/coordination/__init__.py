"""Process-coordination contracts and durable peer/mailbox services."""

from .protocol import (
    ArtifactReference,
    CoordinationValidationError,
    HandoffNote,
    MailboxMessage,
    MailboxStatus,
    MessageKind,
    PeerRecord,
    PeerStatus,
)
from .work import (
    InterruptFrame,
    InterruptStack,
    InterruptionMode,
    ResumeCapsule,
    WorkAddress,
    classify_intervention,
)

__all__ = [
    "ArtifactReference",
    "CoordinationValidationError",
    "HandoffNote",
    "MailboxMessage",
    "MailboxStatus",
    "MessageKind",
    "PeerRecord",
    "PeerStatus",
    "InterruptFrame",
    "InterruptStack",
    "InterruptionMode",
    "ResumeCapsule",
    "WorkAddress",
    "classify_intervention",
]
