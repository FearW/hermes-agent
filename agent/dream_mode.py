"""Dream mode runtime for sleep-time maintenance.

Dream mode is the visible/manual surface for Hermes sleep mode.  It reuses
existing maintenance primitives instead of introducing another memory backend:
built-in memory compaction, capability lifecycle maintenance, and L4 archive
compaction.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from hermes_constants import get_hermes_home

from agent.sleep_mode import resolve_sleep_mode


STATE_FILE = "dream_state.json"


def _state_path() -> Path:
    return get_hermes_home() / STATE_FILE


def _now() -> float:
    return time.time()


def load_dream_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {
            "last_run_at": None,
            "last_duration_seconds": None,
            "last_result": None,
            "runs": 0,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {
            "last_run_at": None,
            "last_duration_seconds": None,
            "last_result": {"ok": False, "errors": ["dream state is unreadable"]},
            "runs": 0,
        }


def save_dream_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def dream_status(config: Dict[str, Any] | None) -> dict[str, Any]:
    sleep = resolve_sleep_mode(config or {})
    state = load_dream_state()
    return {
        "enabled": bool(sleep.get("enabled", True)),
        "profile": sleep.get("profile", "balanced"),
        "sleep_mode": sleep,
        "state": state,
        "actions": [
            "builtin_memory_compaction",
            "capability_lifecycle_maintenance",
            "l4_archive_compaction",
        ],
    }


def run_dream_cycle(config: Dict[str, Any] | None, *, reason: str = "manual") -> dict[str, Any]:
    sleep = resolve_sleep_mode(config or {})
    started = _now()
    result: dict[str, Any] = {
        "ok": True,
        "reason": reason,
        "profile": sleep.get("profile", "balanced"),
        "started_at": started,
        "actions": {},
        "errors": [],
    }

    if not sleep.get("enabled", True):
        result["ok"] = False
        result["skipped"] = "sleep mode is disabled"
        _persist_result(result, started)
        return result

    if sleep.get("background_review", True):
        try:
            from tools.memory_tool import compact_builtin_memory

            result["actions"]["builtin_memory"] = compact_builtin_memory()
        except Exception as exc:  # pragma: no cover - defensive maintenance guard
            result["ok"] = False
            result["errors"].append(f"builtin memory compaction failed: {exc}")

    try:
        from agent.capability_lifecycle import run_lifecycle_maintenance

        result["actions"]["capability_lifecycle"] = run_lifecycle_maintenance()
    except Exception as exc:  # pragma: no cover - defensive maintenance guard
        result["ok"] = False
        result["errors"].append(f"capability lifecycle failed: {exc}")

    if sleep.get("l4_compaction", True):
        try:
            from agent.l4_archive import compact_archive

            result["actions"]["l4_compaction"] = compact_archive()
        except Exception as exc:  # pragma: no cover - defensive maintenance guard
            result["ok"] = False
            result["errors"].append(f"L4 compaction failed: {exc}")

    _persist_result(result, started)
    return result


def _persist_result(result: dict[str, Any], started: float) -> None:
    finished = _now()
    result["finished_at"] = finished
    result["duration_seconds"] = round(finished - started, 3)
    state = load_dream_state()
    state["last_run_at"] = finished
    state["last_duration_seconds"] = result["duration_seconds"]
    state["last_result"] = result
    state["runs"] = int(state.get("runs") or 0) + 1
    save_dream_state(state)
