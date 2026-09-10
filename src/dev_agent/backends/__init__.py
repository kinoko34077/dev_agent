"""External AgentBackend contracts and the Control Plane dispatch boundary."""

from .dispatcher import (
    AgentBackendDispatchError,
    AgentBackendDispatchIdentity,
    AgentBackendDispatcher,
    BackendAdmission,
    BackendDispatchUncertain,
)

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
    "AgentBackendDispatchError",
    "AgentBackendDispatchIdentity",
    "AgentBackendDispatcher",
    "BackendAdmission",
    "AgentBackendEvent",
    "AgentBackendIdentity",
    "AgentBackendRequest",
    "AgentBackendResult",
    "AgentBackendScope",
    "AgentBackendSession",
    "AgentBackendStatus",
    "BackendDispatchUncertain",
]
