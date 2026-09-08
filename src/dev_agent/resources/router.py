"""Deterministic provider/resource selection."""

from __future__ import annotations

from dataclasses import dataclass, field
import time

from .ledger import ResourceLedger


class NoRoute(RuntimeError):
    pass


@dataclass(frozen=True)
class RouteRequest:
    capabilities: set[str] = field(default_factory=set)
    sensitivity: str = "normal"
    allowed_providers: set[str] | None = None
    max_cost_minor: int | None = None
    max_latency_ms: int | None = None
    excluded_resource_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class RouteSelection:
    resource_id: str
    provider_id: str
    native_unit: str
    estimated_cost_minor: int | None
    price_currency: str | None


_SENSITIVITY = {"public": 0, "normal": 1, "internal": 2, "sensitive": 3}


class ResourceRouter:
    def __init__(self, ledger: ResourceLedger) -> None:
        self.ledger = ledger

    def choose(self, request: RouteRequest) -> RouteSelection:
        if request.sensitivity not in _SENSITIVITY:
            raise ValueError("invalid sensitivity")
        candidates = []
        for resource in self.ledger.list_resources():
            if resource["resource_id"] in request.excluded_resource_ids:
                continue
            if request.allowed_providers is not None and resource["provider_id"] not in request.allowed_providers:
                continue
            if not request.capabilities.issubset(set(resource["capabilities"])):
                continue
            if _SENSITIVITY.get(resource["sensitivity"], -1) < _SENSITIVITY[request.sensitivity]:
                continue
            if resource["health"] not in {"healthy", "degraded"} or resource["available"] <= 0:
                continue
            if resource["circuit_open_until"] > time.time():
                continue
            if request.max_cost_minor is not None and resource["cost_minor"] is not None and resource["cost_minor"] > request.max_cost_minor:
                continue
            if request.max_cost_minor is not None and resource["cost_minor"] is None:
                continue
            # Minimize exposure first, then cost, then stable resource id.
            candidates.append((_SENSITIVITY[resource["sensitivity"]], resource["cost_minor"] if resource["cost_minor"] is not None else 10**18, resource["resource_id"], resource))
        if not candidates:
            raise NoRoute("no eligible resource")
        _, _, _, chosen = min(candidates)
        return RouteSelection(chosen["resource_id"], chosen["provider_id"], chosen["native_unit"], chosen["cost_minor"], chosen["price_currency"])
