"""Moonshot/Kimi schema compatibility helpers.

Moonshot-family OpenAI-compatible endpoints reject a few permissive JSON Schema
shapes that other providers accept.  Keep sanitization conservative: only remove
known-problematic keywords and recurse through nested schema containers.
"""

from __future__ import annotations

import copy
from typing import Any


_MOONSHOT_MODEL_MARKERS = (
    "moonshot",
    "kimi-",
    "kimi_",
    "k1.",
    "k2.",
    "moonshotai/",
)

_UNSUPPORTED_SCHEMA_KEYS = {
    "$schema",
    "$id",
    "examples",
    "default",
    "unevaluatedProperties",
    "dependentSchemas",
    "dependentRequired",
}


def is_moonshot_model(model: Any) -> bool:
    """Return True when a model slug belongs to the Moonshot/Kimi family."""
    normalized = str(model or "").strip().lower()
    if not normalized:
        return False
    if "/" in normalized:
        vendor, slug = normalized.split("/", 1)
        if vendor in {"moonshot", "moonshotai", "kimi"}:
            return True
        normalized = slug
    return normalized.startswith(_MOONSHOT_MODEL_MARKERS)


def _sanitize_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_sanitize_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        if key in _UNSUPPORTED_SCHEMA_KEYS:
            continue
        if key == "additionalProperties" and item is True:
            continue
        cleaned[key] = _sanitize_schema(item)
    return cleaned


def sanitize_moonshot_tools(tools: list[dict] | None) -> list[dict] | None:
    """Return a Moonshot-safe copy of OpenAI tool definitions."""
    if not tools:
        return tools
    sanitized = copy.deepcopy(tools)
    return _sanitize_schema(sanitized)
