"""Dream mode runtime for sleep-time maintenance.

Dream mode is the visible/manual surface for Hermes sleep mode.  It delegates
every action to ``agent.background_scheduler.BackgroundScheduler`` so that
the CLI ``/dream`` command, the Web UI "run now" button, and the gateway
maintenance loop all share one code path.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from hermes_constants import get_hermes_home
from utils import atomic_json_write

from agent.background_scheduler import (
    STATE_FILE,
    AlwaysReadyProbe,
    build_scheduler,
    record_skipped_run,
)
from agent.sleep_mode import resolve_sleep_mode


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
    atomic_json_write(_state_path(), state)


def dream_status(config: Dict[str, Any] | None) -> dict[str, Any]:
    sleep = resolve_sleep_mode(config or {})
    state = load_dream_state()
    return {
        "enabled": bool(sleep.get("enabled", True)),
        "profile": sleep.get("profile", "balanced"),
        "sleep_mode": sleep,
        "state": state,
        "task_states": state.get("tasks") or {},
        "actions": [
            "builtin_memory_compaction",
            "capability_lifecycle_maintenance",
            "l4_archive_compaction",
        ],
    }


def run_dream_cycle(
    config: Dict[str, Any] | None,
    *,
    reason: str = "manual",
    tags: tuple[str, ...] | None = ("dream",),
) -> dict[str, Any]:
    """Run one maintenance cycle on behalf of the user.

    Delegates to ``BackgroundScheduler.run_once``. Disabled sleep mode is
    still recorded (so the UI can show a "skipped: disabled" card).
    """
    sleep = resolve_sleep_mode(config or {})
    started = _now()

    if not sleep.get("enabled", True):
        result: dict[str, Any] = {
            "ok": False,
            "reason": reason,
            "profile": sleep.get("profile", "balanced"),
            "started_at": started,
            "actions": {},
            "errors": [],
            "skipped": "sleep mode is disabled",
        }
        _persist_skipped_result(result, started)
        return result

    scheduler = build_scheduler(config or {}, sleep_cfg=sleep)
    summary = scheduler.run_once(tags=tags)
    summary["reason"] = reason
    summary["profile"] = sleep.get("profile", "balanced")
    return summary


def _persist_skipped_result(result: dict[str, Any], started: float) -> None:
    """Persist a 'skipped' dream run — delegates to the shared writer in
    ``agent.background_scheduler.record_skipped_run`` so this path uses
    the same atomic write + schema as a real BackgroundScheduler cycle."""
    finished = _now()
    result["finished_at"] = finished
    result["duration_seconds"] = round(finished - started, 3)
    record_skipped_run(result, state_path=_state_path())
