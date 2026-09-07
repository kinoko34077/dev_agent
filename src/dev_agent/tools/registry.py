"""Explicit tool registry; model output never contains an implementation ref."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    required_arguments: frozenset[str] = field(default_factory=frozenset)
    side_effect_level: str = "none"
    enabled: bool = True


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.name.strip() or spec.name in self._tools:
            raise ValueError(f"invalid or duplicate tool: {spec.name!r}")
        self._tools[spec.name] = spec

    def resolve(self, name: str) -> ToolSpec | None:
        spec = self._tools.get(name)
        return spec if spec and spec.enabled else None

    def names(self) -> list[str]:
        return sorted(name for name, spec in self._tools.items() if spec.enabled)
