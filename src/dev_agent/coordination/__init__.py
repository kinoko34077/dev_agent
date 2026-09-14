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

__all__ = [
    "ArtifactReference",
    "CoordinationValidationError",
    "HandoffNote",
    "MailboxMessage",
    "MailboxStatus",
    "MessageKind",
    "PeerRecord",
    "PeerStatus",
]
