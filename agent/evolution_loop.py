"""Minimal self-evolution loop helpers (v1).

This module records structured run outcomes and optional user feedback so
offline scripts can compute quality/cost/latency KPIs and compare strategies.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_constants import get_hermes_home


def _evolution_dir() -> Path:
    path = get_hermes_home() / "evolution"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def record_run_outcome(
    *,
    session_id: str,
    run_id: str,
    model: str,
    strategy_version: str,
    experiment_bucket: str,
    completed: bool,
    interrupted: bool,
    api_calls: int,
    retry_count: int,
    tool_turns: int,
    fallback_active: bool,
    latency_seconds: float,
    estimated_cost_usd: float,
    final_response_len: int,
    turn_exit_reason: str,
    timestamp_ms: Optional[int] = None,
) -> None:
    payload = {
        "type": "run_outcome",
        "timestamp_ms": int(timestamp_ms or (time.time() * 1000)),
        "session_id": session_id or "",
        "run_id": run_id,
        "model": model or "",
        "strategy_version": strategy_version or "default",
        "experiment_bucket": experiment_bucket or "control",
        "completed": bool(completed),
        "interrupted": bool(interrupted),
        "api_calls": int(api_calls or 0),
        "retry_count": int(retry_count or 0),
        "tool_turns": int(tool_turns or 0),
        "fallback_active": bool(fallback_active),
        "latency_seconds": float(latency_seconds or 0.0),
        "estimated_cost_usd": float(estimated_cost_usd or 0.0),
        "final_response_len": int(final_response_len or 0),
        "turn_exit_reason": turn_exit_reason or "unknown",
    }
    _append_jsonl(_evolution_dir() / "outcomes.jsonl", payload)


def record_user_feedback(
    *,
    session_id: str,
    run_id: str,
    score: int,
    tags: Optional[list[str]] = None,
    note: str = "",
    strategy_version: str = "",
    experiment_bucket: str = "",
    timestamp_ms: Optional[int] = None,
) -> None:
    payload = {
        "type": "user_feedback",
        "timestamp_ms": int(timestamp_ms or (time.time() * 1000)),
        "session_id": session_id or "",
        "run_id": run_id or "",
        "score": int(score),
        "tags": list(tags or []),
        "note": note or "",
        "strategy_version": strategy_version or "default",
        "experiment_bucket": experiment_bucket or "control",
    }
    _append_jsonl(_evolution_dir() / "feedback.jsonl", payload)


def record_background_event(
    *,
    session_id: str,
    event: str,
    detail: str = "",
    data: Optional[Dict[str, Any]] = None,
    timestamp_ms: Optional[int] = None,
) -> None:
    """Record a non-outcome background event (errors, scheduler cycles, etc.).

    Stored under ``evolution/background_events.jsonl`` so the evolution loop
    tooling can include these alongside run outcomes without polluting the
    strict ``run_outcome`` schema.
    """
    payload: Dict[str, Any] = {
        "type": "background_event",
        "timestamp_ms": int(timestamp_ms or (time.time() * 1000)),
        "session_id": session_id or "",
        "event": event or "",
        "detail": detail or "",
    }
    if data:
        payload["data"] = dict(data)
    _append_jsonl(_evolution_dir() / "background_events.jsonl", payload)
