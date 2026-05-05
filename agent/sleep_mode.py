"""Sleep mode orchestration for background learning and maintenance.

This module does not implement new memory backends. It provides one small
control layer over existing Hermes mechanisms: memory review, skill review,
external-memory sync/prefetch, and L4 gateway maintenance.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any, Dict

logger = logging.getLogger(__name__)


_PROFILES: dict[str, dict[str, Any]] = {
    "off": {
        "enabled": False,
        "memory_review_interval": 0,
        "skill_review_interval": 0,
        "memory_token_threshold": 0,
        "background_review": False,
        "external_memory_sync": False,
        "l4_periodic_archive": False,
        "l4_compaction": False,
        "maintenance_interval_seconds": 0,
        "l4_interval_seconds": 0,
        "idle_before_maintenance_seconds": 0,
        "report_actions": False,
    },
    "light": {
        "enabled": True,
        "memory_review_interval": 12,
        "skill_review_interval": 20,
        "memory_token_threshold": 12000,
        "background_review": True,
        "external_memory_sync": True,
        "l4_periodic_archive": True,
        "l4_compaction": True,
        "maintenance_interval_seconds": 21600,
        "l4_interval_seconds": 7200,
        "idle_before_maintenance_seconds": 1800,
        "report_actions": True,
    },
    "balanced": {
        "enabled": True,
        "memory_review_interval": 8,
        "skill_review_interval": 12,
        "memory_token_threshold": 8000,
        "background_review": True,
        "external_memory_sync": True,
        "l4_periodic_archive": True,
        "l4_compaction": True,
        "maintenance_interval_seconds": 14400,
        "l4_interval_seconds": 5400,
        "idle_before_maintenance_seconds": 1800,
        "report_actions": True,
    },
    "deep": {
        "enabled": True,
        "memory_review_interval": 4,
        "skill_review_interval": 8,
        "memory_token_threshold": 5000,
        "background_review": True,
        "external_memory_sync": True,
        "l4_periodic_archive": True,
        "l4_compaction": True,
        "maintenance_interval_seconds": 7200,
        "l4_interval_seconds": 3600,
        "idle_before_maintenance_seconds": 1800,
        "report_actions": True,
    },
}


# Single source of truth for every field resolve_sleep_mode accepts. Keeping
# this explicit (instead of relying on "key in resolved") lets us surface
# typos in user configs instead of silently dropping them.
_INT_KEYS: tuple[str, ...] = (
    "memory_review_interval",
    "skill_review_interval",
    "memory_token_threshold",
    "maintenance_interval_seconds",
    "l4_interval_seconds",
    "idle_before_maintenance_seconds",
)

_BOOL_KEYS: tuple[str, ...] = (
    "enabled",
    "background_review",
    "external_memory_sync",
    "l4_periodic_archive",
    "l4_compaction",
    "report_actions",
)

_KNOWN_KEYS: frozenset[str] = frozenset(("profile", *_INT_KEYS, *_BOOL_KEYS))


def resolve_sleep_mode(config: Dict[str, Any] | None) -> dict[str, Any]:
    """Return effective sleep-mode settings from config.yaml.

    Unknown profiles fall back to ``balanced`` (with a warning). Explicit keys
    in ``sleep_mode`` override the selected profile; unknown keys are logged
    and ignored instead of being silently dropped.
    """
    config = config or {}
    raw = config.get("sleep_mode") or {}
    if not isinstance(raw, dict):
        logger.warning(
            "sleep_mode config is not a mapping (got %s); using balanced defaults",
            type(raw).__name__,
        )
        raw = {}

    requested_profile = str(raw.get("profile") or "balanced").lower()
    profile = requested_profile
    if profile not in _PROFILES:
        logger.warning(
            "Unknown sleep_mode profile %r; falling back to 'balanced'",
            requested_profile,
        )
        profile = "balanced"

    resolved = deepcopy(_PROFILES[profile])
    resolved["profile"] = profile

    for key, value in raw.items():
        if key == "profile":
            continue
        if key not in _KNOWN_KEYS:
            logger.warning(
                "Ignoring unknown sleep_mode field %r (valid: %s)",
                key,
                ", ".join(sorted(_KNOWN_KEYS)),
            )
            continue
        resolved[key] = value

    # The explicit "off" profile is authoritative and must stay disabled even
    # if an older config entry still carries enabled=true.
    if profile == "off":
        resolved.update(_PROFILES["off"])
        resolved["profile"] = "off"
    elif not bool(resolved.get("enabled", True)):
        resolved.update(_PROFILES["off"])
        resolved["profile"] = profile if profile == "off" else f"{profile}:disabled"

    for key in _INT_KEYS:
        raw_value = resolved.get(key, 0)
        try:
            resolved[key] = max(0, int(raw_value))
        except (TypeError, ValueError):
            logger.warning(
                "sleep_mode.%s has non-integer value %r; using 0", key, raw_value
            )
            resolved[key] = 0

    for key in _BOOL_KEYS:
        if key == "enabled":
            # enabled has already been normalised above (off/disabled paths).
            resolved[key] = bool(resolved.get(key, False))
            continue
        resolved[key] = bool(resolved.get(key, False))

    return resolved


def apply_sleep_mode_to_maintenance_config(config: Dict[str, Any] | None) -> dict[str, Any]:
    """Return maintenance config with sleep-mode cadence applied."""
    config = config or {}
    maintenance = dict(config.get("maintenance") or {})
    sleep = resolve_sleep_mode(config)

    if not sleep.get("enabled", True):
        maintenance["enabled"] = False
        maintenance["retention_loop"] = False
        maintenance["l4_periodic_archive"] = False
        maintenance["l4_compaction"] = False
        return maintenance

    maintenance.setdefault("enabled", True)
    maintenance["l4_periodic_archive"] = bool(sleep.get("l4_periodic_archive", True))
    maintenance["l4_compaction"] = bool(sleep.get("l4_compaction", True))
    if sleep.get("maintenance_interval_seconds", 0):
        maintenance["interval_seconds"] = sleep["maintenance_interval_seconds"]
    if sleep.get("l4_interval_seconds", 0):
        maintenance["l4_interval_seconds"] = sleep["l4_interval_seconds"]
    maintenance["idle_before_maintenance_seconds"] = int(
        sleep.get("idle_before_maintenance_seconds", 0) or 0
    )
    return maintenance
