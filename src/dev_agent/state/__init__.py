"""Durable state interfaces with lazy compatibility exports."""

from importlib import import_module


_LAZY_EXPORTS = {
    "JsonStateStore": (".json_store", "JsonStateStore"),
    "SQLiteStateStore": (".sqlite_store", "SQLiteStateStore"),
    "StateStore": (".store", "StateStore"),
    "TaskStateView": (".views", "TaskStateView"),
    "EventStore": (".views", "EventStore"),
    "EffectIntentStore": (".views", "EffectIntentStore"),
    "ProviderAuditStore": (".views", "ProviderAuditStore"),
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
