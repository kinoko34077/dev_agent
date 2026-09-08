"""Explicit tool registry; model output never contains an implementation ref."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    required_arguments: frozenset[str] = field(default_factory=frozenset)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    side_effect_level: str = "none"
    path_argument: str | None = None
    path_operation: str | None = None
    timeout_seconds: float = 30.0
    max_argument_bytes: int = 65536
    max_result_bytes: int = 131072
    enabled: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        for name in ("max_argument_bytes", "max_result_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.input_schema, dict):
            raise ValueError("input_schema must be an object")
        if not isinstance(self.output_schema, dict):
            raise ValueError("output_schema must be an object")
        self._validate_schema_subset(self.input_schema)
        self._validate_schema_subset(self.output_schema)

    @staticmethod
    def _validate_schema_subset(schema: dict[str, Any]) -> None:
        supported = {"type", "properties", "required", "additionalProperties", "enum", "minimum", "maximum", "items", "minItems", "maxItems", "minLength", "maxLength"}
        unknown = set(schema) - supported
        if unknown:
            raise ValueError(f"unsupported schema keywords: {', '.join(sorted(unknown))}")
        for child in schema.get("properties", {}).values():
            if isinstance(child, dict):
                ToolSpec._validate_schema_subset(child)
        if isinstance(schema.get("items"), dict):
            ToolSpec._validate_schema_subset(schema["items"])

    def provider_definition(self) -> dict[str, Any]:
        schema = dict(self.input_schema)
        if self.required_arguments:
            schema.setdefault("required", sorted(self.required_arguments))
        schema.setdefault("type", "object")
        return {"name": self.name, "description": self.description, "parameters": schema}


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

    def definitions(self) -> list[dict[str, Any]]:
        return [self._tools[name].provider_definition() for name in self.names()]
