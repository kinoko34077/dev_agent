"""Small deterministic JSON-Schema subset validator for tool boundaries."""

from __future__ import annotations

from typing import Any


class SchemaValidationError(ValueError):
    pass


def validate(value: Any, schema: dict[str, Any], *, path: str = "$", root: Any = None) -> None:
    if not isinstance(schema, dict):
        raise SchemaValidationError(f"{path}: schema must be an object")
    if root is None:
        root = value
    if "$ref" in schema:
        raise SchemaValidationError(f"{path}: $ref is not supported")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path}: value is not in enum")
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise SchemaValidationError(f"{path}: expected object")
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise SchemaValidationError(f"{path}: missing required properties: {', '.join(missing)}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise SchemaValidationError(f"{path}: additional properties: {', '.join(extras)}")
        for key, child_schema in properties.items():
            if key in value:
                validate(value[key], child_schema, path=f"{path}.{key}", root=root)
    elif expected == "array":
        if not isinstance(value, list):
            raise SchemaValidationError(f"{path}: expected array")
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaValidationError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaValidationError(f"{path}: too many items")
        if "items" in schema:
            for index, item in enumerate(value):
                validate(item, schema["items"], path=f"{path}[{index}]", root=root)
    elif expected == "string":
        if not isinstance(value, str):
            raise SchemaValidationError(f"{path}: expected string")
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaValidationError(f"{path}: string too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaValidationError(f"{path}: string too long")
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaValidationError(f"{path}: expected integer")
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaValidationError(f"{path}: expected number")
    elif expected == "boolean" and not isinstance(value, bool):
        raise SchemaValidationError(f"{path}: expected boolean")
    elif expected is not None:
        raise SchemaValidationError(f"{path}: unsupported type {expected}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaValidationError(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaValidationError(f"{path}: above maximum")
