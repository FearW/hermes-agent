"""LM Studio reasoning parameter helpers.

LM Studio exposes model-specific reasoning effort values via
``capabilities.reasoning.allowed_options``.  Keep this helper tiny and safe so
chat-completions transport registration never depends on optional runtime
probing succeeding.
"""

from __future__ import annotations

from typing import Any, Iterable


_EFFORT_ALIASES = {
    "none": None,
    "off": None,
    "disabled": None,
    "minimal": "low",
    "xhigh": "high",
}


def _normalize_allowed_options(options: Any) -> list[str]:
    if not isinstance(options, Iterable) or isinstance(options, (str, bytes, dict)):
        return []
    normalized: list[str] = []
    for option in options:
        value = str(option or "").strip().lower()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def resolve_lmstudio_effort(reasoning_config: dict | None, allowed_options: Any = None) -> str | None:
    """Return the safest LM Studio ``reasoning_effort`` value.

    ``None`` means Hermes should omit the field.  When LM Studio publishes a
    constrained option list, clamp Hermes' wider effort vocabulary to a value
    the model actually accepts.
    """
    if isinstance(reasoning_config, dict):
        if reasoning_config.get("enabled") is False:
            return None
        requested = str(reasoning_config.get("effort") or "medium").strip().lower()
    else:
        requested = "medium"

    requested = _EFFORT_ALIASES.get(requested, requested)
    if requested is None:
        return None

    allowed = _normalize_allowed_options(allowed_options)
    if not allowed:
        return requested if requested in {"low", "medium", "high"} else "medium"
    if requested in allowed:
        return requested
    for candidate in ("medium", "high", "low"):
        if candidate in allowed:
            return candidate
    return allowed[0] if allowed else None
