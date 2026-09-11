"""Durable queue and worker boundaries with lazy compatibility exports."""

from importlib import import_module


_LAZY_EXPORTS = {
    "DurableQueue": (".queue", "DurableQueue"),
    "MaintenanceMode": (".queue", "MaintenanceMode"),
    "QueueEmpty": (".queue", "QueueEmpty"),
    "QueueItem": (".queue", "QueueItem"),
    "StaleLease": (".queue", "StaleLease"),
    "QuotaProbeResult": (".quota", "QuotaProbeResult"),
    "QuotaProbeStatus": (".quota", "QuotaProbeStatus"),
    "QuotaRequalificationCoordinator": (".quota", "QuotaRequalificationCoordinator"),
    "QuotaWakeScheduler": (".quota", "QuotaWakeScheduler"),
    "WorkerRunner": (".worker", "WorkerRunner"),
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
