"""External AgentBackend boundaries with lazy compatibility exports."""

from importlib import import_module


_LAZY_EXPORTS = {
    "AgentBackend": (".protocol", "AgentBackend"),
    "AgentBackendEvent": (".protocol", "AgentBackendEvent"),
    "AgentBackendIdentity": (".protocol", "AgentBackendIdentity"),
    "AgentBackendRequest": (".protocol", "AgentBackendRequest"),
    "AgentBackendResult": (".protocol", "AgentBackendResult"),
    "AgentBackendScope": (".protocol", "AgentBackendScope"),
    "AgentBackendSession": (".protocol", "AgentBackendSession"),
    "AgentBackendStatus": (".protocol", "AgentBackendStatus"),
    "AgentBackendDispatchError": (".dispatcher", "AgentBackendDispatchError"),
    "AgentBackendDispatchIdentity": (".dispatcher", "AgentBackendDispatchIdentity"),
    "AgentBackendDispatcher": (".dispatcher", "AgentBackendDispatcher"),
    "BackendAdmission": (".dispatcher", "BackendAdmission"),
    "BackendDispatchUncertain": (".dispatcher", "BackendDispatchUncertain"),
    "CodexExecBackend": (".codex_exec", "CodexExecBackend"),
    "CodexExecBackendError": (".codex_exec", "CodexExecBackendError"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


__all__ = list(_LAZY_EXPORTS)
