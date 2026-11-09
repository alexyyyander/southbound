"""Version-one decompilation wire contracts and dependency-free validation.

Schemas use only the JSON Schema keywords implemented by validate(). Keep new
keywords and their runtime validation together: advertised schemas are enforced
on both caller input and backend output.
"""

from __future__ import annotations

import re
from typing import Any


def obj(properties: dict, required: tuple[str, ...] | None = None) -> dict:
    return {
        "type": "object", "properties": properties,
        "required": list(properties if required is None else required),
        "additionalProperties": False,
    }


def text(**constraints: Any) -> dict:
    return {"type": "string", "minLength": 1, **constraints}


def array(items: dict) -> dict:
    return {"type": "array", "items": items}


ID = text(maxLength=256)
ADDRESS = text(pattern=r"^0x[0-9a-fA-F]+$", maxLength=256)
FUNCTION = obj({"address": ADDRESS, "name": text()}, ("address",))
EVIDENCE = obj({
    "source": text(), "operation": text(), "locator": text(),
    "sha256": text(pattern=r"^[0-9a-fA-F]{64}$"),
}, ("source", "operation", "locator"))
ERROR_CODES = (
    "INVALID_ARGUMENT", "BACKEND_UNAVAILABLE", "BACKEND_CONTRACT_ERROR",
    "BACKEND_ERROR", "TARGET_NOT_FOUND", "SESSION_NOT_FOUND", "FUNCTION_NOT_FOUND",
    "AMBIGUOUS_FUNCTION", "UNSUPPORTED", "ACCESS_DENIED", "BUSY", "TIMEOUT", "CANCELLED",
)
ERROR_SCHEMA = obj({
    "ok": {"type": "boolean", "enum": [False]},
    "error": obj({
        "code": text(enum=list(ERROR_CODES)), "message": text(), "retryable": {"type": "boolean"},
    }),
})
SCOPE = {"session_id": ID, "target_id": ID}
SELECTOR = {"oneOf": [obj({"address": ADDRESS}), obj({"name": text(maxLength=1024)})]}
PAGE = {
    "query": text(maxLength=4096),
    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
    "cursor": text(maxLength=4096),
}


def success_schema(data: dict) -> dict:
    return obj({
        "ok": {"type": "boolean", "enum": [True]},
        **SCOPE, "data": data, "evidence": array(EVIDENCE),
        "completeness": text(enum=["complete", "partial", "unknown"]),
        "limitations": array(text()),
    })


# Separate operations keep input and result responsibilities small and explicit.
OPERATIONS = {
    "open_target": (
        "Open a local binary for static analysis; never execute the target.",
        obj({"path": text(pattern=r"^(?:/|[A-Za-z]:[\\/])", maxLength=4096)}),
        obj({"path": text(), "state": text(enum=["ready", "analyzing"])}),
    ),
    "target_overview": (
        "Read the current target's format, architecture and inventory counts.",
        obj(SCOPE),
        obj({"format": text(), "architecture": text(),
             "function_count": {"type": "integer", "minimum": 0},
             "string_count": {"type": "integer", "minimum": 0}}, ()),
    ),
    "search_functions": (
        "Search function names by case-insensitive literal substring; no regex.",
        obj({**SCOPE, **PAGE}, ("session_id", "target_id", "query")),
        obj({"items": array(FUNCTION), "next_cursor": text()}, ("items",)),
    ),
    "search_strings": (
        "Search decoded strings by case-insensitive literal substring; no regex.",
        obj({**SCOPE, **PAGE}, ("session_id", "target_id", "query")),
        obj({"items": array(obj({"address": ADDRESS, "value": {"type": "string"}})),
             "next_cursor": text()}, ("items",)),
    ),
    "analyze_function": (
        "Analyze one function selected by exact name or hexadecimal virtual address.",
        obj({**SCOPE, "selector": SELECTOR}),
        obj({"function": FUNCTION, "pseudocode": {"type": "string"},
             "assembly": {"type": "string"}, "calls": array(FUNCTION)}, ("function",)),
    ),
    "analysis_status": (
        "Read target analysis state; this interface creates no scheduled tasks.",
        obj(SCOPE),
        obj({"state": text(enum=["ready", "analyzing", "failed", "closed"]),
             "message": text()}, ("state",)),
    ),
    "close_target": (
        "Release this target and its backend-owned resources; idempotent within the session.",
        obj(SCOPE), obj({"closed": {"type": "boolean", "enum": [True]}}),
    ),
}
TOOLS = [
    {"name": "decompile_" + name, "description": description,
     "inputSchema": inputs, "outputSchema": {"oneOf": [success_schema(outputs), ERROR_SCHEMA]}}
    for name, (description, inputs, outputs) in OPERATIONS.items()
]


def validate(value: Any, schema: dict, path: str = "$") -> None:
    """Validate the deliberately small schema vocabulary above without coercion."""
    if "oneOf" in schema:
        matches = 0
        for alternative in schema["oneOf"]:
            try:
                validate(value, alternative, path)
                matches += 1
            except ValueError:
                pass
        if matches != 1:
            raise ValueError(f"{path}: expected exactly one permitted shape")
        return
    kind = schema["type"]
    valid = {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: type(value) is int,
        "boolean": lambda: type(value) is bool,
    }[kind]()
    if not valid:
        raise ValueError(f"{path}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: unsupported value")
    if kind == "object":
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{path}: keys must be strings")
        if set(schema["required"]) - value.keys():
            raise ValueError(f"{path}: missing required fields")
        if value.keys() - schema["properties"].keys():
            raise ValueError(f"{path}: unknown fields")
        for key, item in value.items():
            validate(item, schema["properties"][key], f"{path}.{key}")
    elif kind == "array":
        for index, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{index}]")
    elif kind == "string":
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 2**63):
            raise ValueError(f"{path}: invalid string length")
        if schema.get("minLength", 0) and not value.strip():
            raise ValueError(f"{path}: blank string")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ValueError(f"{path}: invalid string format")
    elif kind == "integer":
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            raise ValueError(f"{path}: integer out of range")
