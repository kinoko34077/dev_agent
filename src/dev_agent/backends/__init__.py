"""External AgentBackend contracts and future adapters."""

from .protocol import (
    AgentBackend,
    AgentBackendEvent,
    AgentBackendIdentity,
    AgentBackendRequest,
    AgentBackendResult,
    AgentBackendScope,
    AgentBackendSession,
    AgentBackendStatus,
)

__all__ = [
    "AgentBackend",
    "AgentBackendEvent",
    "AgentBackendIdentity",
    "AgentBackendRequest",
    "AgentBackendResult",
    "AgentBackendScope",
    "AgentBackendSession",
    "AgentBackendStatus",
]
