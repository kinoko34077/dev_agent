"""Immutable read data passed to resource routing decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class RoutingSnapshot:
    """One consistent resource/quota read used by a routing decision."""

    resources: tuple[dict[str, Any], ...]
    quota_observations_by_domain: Mapping[str, tuple[dict[str, Any], ...]]
