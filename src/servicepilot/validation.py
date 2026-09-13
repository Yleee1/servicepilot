from __future__ import annotations

import re
from typing import Any

from .schemas import ToolDefinition


class ToolValidationError(ValueError):
    pass


def validate_arguments(definition: ToolDefinition, arguments: dict[str, Any]) -> None:
    schema = definition.parameters
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    missing = required - arguments.keys()
    if missing:
        raise ToolValidationError(f"missing required arguments: {sorted(missing)}")

    if schema.get("additionalProperties") is False:
        unknown = arguments.keys() - properties.keys()
        if unknown:
            raise ToolValidationError(f"unknown arguments: {sorted(unknown)}")

    python_types = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
    for name, value in arguments.items():
        rule = properties.get(name)
        if not rule:
            continue
        expected = python_types.get(rule.get("type"))
        if expected and (not isinstance(value, expected) or isinstance(value, bool) and expected != bool):
            raise ToolValidationError(f"argument {name} has invalid type")
        if "enum" in rule and value not in rule["enum"]:
            raise ToolValidationError(f"argument {name} must be one of {rule['enum']}")
        if isinstance(value, str) and rule.get("minLength", 0) > len(value):
            raise ToolValidationError(f"argument {name} is too short")
        if isinstance(value, str) and "pattern" in rule and not re.fullmatch(rule["pattern"], value):
            raise ToolValidationError(f"argument {name} has invalid format")
