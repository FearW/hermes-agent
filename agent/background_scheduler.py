"""Unified background task scheduler for Hermes sleep-mode maintenance.

``BackgroundScheduler`` is the single runner for every recurring maintenance
action Hermes performs while the user is idle or has just finished a turn:
built-in memory compaction, capability-lifecycle maintenance, L4 archive
compaction, gateway retention, and L4 periodic archival.

Two execution surfaces share the same task set:

* ``run_once(tags=...)`` — synchronous, used by ``agent.dream_mode`` and the
  ``/dream`` slash command. Runs every matching task sequentially.
* ``await run_forever(idle_probe, stop_event)`` — async, used by
  ``gateway.run`` as a single maintenance loop (replacing the old
  ``retention_loop`` + ``l4_periodic_archive_loop`` duo).

Per-task adaptive backoff:
    Each task action returns ``{"changed": int, ...}``. When a task reports
    zero changes several cycles in a row its cadence doubles, capped at
    ``4x`` its base cadence. Any cycle with work done resets the cadence.

State persists to ``$HERMES_HOME/dream_state.json`` and is shared with
``agent.dream_mode.load_dream_state`` for backward-compatibility.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from hermes_constants import get_hermes_home
from utils import atomic_json_write

from agent.sleep_mode import resolve_sleep_mode

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task + scheduler dataclasses
# ---------------------------------------------------------------------------

ActionResult = dict[str, Any]
ActionFn = Callable[[], ActionResult]


@dataclass
class BackgroundTask:
    """Definition of a single scheduled maintenance task.

    ``action`` must return a dict with at least ``{"changed": int}``. Extra
    fields are persisted verbatim under ``last_result``. Errors raised by
    ``action`` are caught by the scheduler and surfaced via ``errors``.
    """

    name: str
    action: ActionFn
    base_cadence_s: int
    jitter_s: int = 0
    idle_required_s: int = 0
    tags: tuple[str, ...] = ()
    requires: Callable[[], bool] | None = None
    max_backoff_multiplier: int = 4


@dataclass
class _TaskRuntime:
    """Mutable per-task runtime state tracked by the scheduler."""

    current_cadence_s: int
    consecutive_empty: int = 0
    next_run_at: float = 0.0
    last_run_at: float | None = None
    last_changed: int = 0


STATE_FILE = "dream_state.json"

DEFAULT_EMPTY_STREAK_BEFORE_BACKOFF = 3


# ---------------------------------------------------------------------------
# Idle probes
# ---------------------------------------------------------------------------


class IdleProbe:
    """Abstract idle probe — tells the scheduler how long since activity."""

    def seconds_since_activity(self) -> float:  # pragma: no cover - interface
        raise NotImplementedError


class AlwaysReadyProbe(IdleProbe):
    """Probe that always reports "just idled enough" — used for run_once."""

    def seconds_since_activity(self) -> float:
        return float("inf")


class SessionStoreIdleProbe(IdleProbe):
    """Gateway probe: latest ``SessionStore`` activity timestamp.

    Caches the resolved latest-activity timestamp for a short TTL so the
    scheduler's 60 s poll loop doesn't re-query the session DB every time
    for a value that only changes when a user replies. The idle seconds
    themselves are re-derived from ``now`` on each call (cheap), only the
    DB walk is cached.
    """

    _DEFAULT_CACHE_TTL_S = 20.0

    def __init__(
        self,
        session_store: Any,
        *,
        cache_ttl_seconds: float = _DEFAULT_CACHE_TTL_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = session_store
        self._cache_ttl = max(0.0, float(cache_ttl_seconds))
        self._clock = clock
        self._cached_latest: datetime.datetime | None = None
        self._cached_at: float = 0.0

    def _latest_activity(self) -> datetime.datetime | None:
        now_mono = self._clock()
        if (
            self._cache_ttl > 0
            and self._cached_at
            and (now_mono - self._cached_at) < self._cache_ttl
        ):
            return self._cached_latest

        store = self._store
        if store is None:
            return None
        try:
            entries = list(store.list_sessions())
        except Exception:
            return None

        latest: datetime.datetime | None = None
        for entry in entries:
            updated = getattr(entry, "updated_at", None)
            if not isinstance(updated, datetime.datetime):
                continue
            if latest is None or updated > latest:
                latest = updated

        self._cached_latest = latest
        self._cached_at = now_mono
        return latest

    def seconds_since_activity(self) -> float:
        if self._store is None:
            return float("inf")
        latest = self._latest_activity()
        if latest is None:
            return float("inf")

        if latest.tzinfo is None:
            now = datetime.datetime.now()
        else:
            now = datetime.datetime.now(latest.tzinfo)
        return max(0.0, (now - latest).total_seconds())

    def invalidate_cache(self) -> None:
        """Drop the cached activity timestamp (forces next call to requery)."""
        self._cached_latest = None
        self._cached_at = 0.0


# ---------------------------------------------------------------------------
# State-file helpers (shared between BackgroundScheduler + dream_mode)
# ---------------------------------------------------------------------------


def _atomic_write_state(state_path: Path, state: dict[str, Any]) -> None:
    """Atomic write to ``dream_state.json`` shared by all writers.

    Centralised so any future schema additions only need to touch one
    helper rather than living separately in ``BackgroundScheduler._write_state``
    and ``dream_mode.save_dream_state``.
    """
    try:
        atomic_json_write(state_path, state)
    except Exception as exc:  # pragma: no cover - state is best-effort
        logger.debug("Failed to persist scheduler state to %s: %s", state_path, exc)


def record_skipped_run(
    result: dict[str, Any],
    *,
    state_path: Path | None = None,
) -> None:
    """Persist a skipped/no-op dream cycle without instantiating a scheduler.

    Used by ``dream_mode._persist_skipped_result`` when sleep mode is
    disabled — keeps the manual ``/dream`` skipped-card path on the same
    atomic-write codepath as a real cycle.
    """
    if state_path is None:
        state_path = get_hermes_home() / STATE_FILE
    if state_path.exists():
        try:
            existing = json.loads(state_path.read_text(encoding="utf-8"))
            state = existing if isinstance(existing, dict) else {}
        except Exception:
            state = {}
    else:
        state = {}
    state["runs"] = int(state.get("runs") or 0) + 1
    state["last_run_at"] = float(result.get("finished_at") or time.time())
    state["last_duration_seconds"] = float(result.get("duration_seconds") or 0.0)
    state["last_result"] = result
    _atomic_write_state(state_path, state)


# ---------------------------------------------------------------------------
# Default task factory
# ---------------------------------------------------------------------------


def _normalise_result(raw: Any) -> ActionResult:
    """Coerce a maintenance action's return value into scheduler shape.

    Accepts the historical return shapes of ``compact_builtin_memory``,
    ``run_lifecycle_maintenance``, ``compact_archive``, and
    ``archive_and_prune_expired_transcripts`` so tasks can point at them
    directly without adapters.
    """
    if isinstance(raw, dict):
        result = dict(raw)
        if "changed" in result:
            try:
                result["changed"] = int(result["changed"])
            except (TypeError, ValueError):
                result["changed"] = 0
            return result
        # heuristics for common shapes
        changed = 0
        for key in ("memory_removed", "user_removed", "deleted", "archived", "promoted", "demoted", "pruned"):
            try:
                changed += max(0, int(result.get(key, 0) or 0))
            except (TypeError, ValueError):
                continue
        result["changed"] = changed
        return result
    if isinstance(raw, int):
        return {"changed": max(0, raw)}
    return {"changed": 0, "detail": raw}


def _builtin_memory_action() -> ActionResult:
    from tools.memory_tool import compact_builtin_memory

    return _normalise_result(compact_builtin_memory())


def _capability_lifecycle_action() -> ActionResult:
    from agent.capability_lifecycle import run_lifecycle_maintenance

    return _normalise_result(run_lifecycle_maintenance())


def _l4_compaction_action(
    *,
    max_entries: int = 2000,
    max_age_days: int = 180,
    keep_priority_at_least: int = 4,
) -> ActionResult:
    from agent.l4_archive import compact_archive

    return _normalise_result(
        compact_archive(
            max_entries=max_entries,
            max_age_days=max_age_days,
            keep_priority_at_least=keep_priority_at_least,
        )
    )


def _retention_action(session_store: Any, older_than_days: int) -> ActionResult:
    try:
        pruned = session_store.archive_and_prune_expired_transcripts(
            older_than_days=older_than_days
        )
    except Exception as exc:
        return {"changed": 0, "error": str(exc)}
    return {"changed": int(pruned or 0), "pruned": int(pruned or 0)}


def _l4_periodic_archive_action(
    session_store: Any,
    *,
    max_sessions_per_cycle: int,
    session_idle_floor_s: int = 1800,
) -> ActionResult:
    from agent.l4_archive import (
        _connect,
        _insert_entry,
        _migrate_legacy_json_if_needed,
        is_session_archived,
        summarize_messages,
    )

    archived_count = 0
    try:
        entries = list(session_store.list_sessions())
    except Exception as exc:
        return {"changed": 0, "error": str(exc)}

    for entry in entries:
        if max_sessions_per_cycle > 0 and archived_count >= max_sessions_per_cycle:
            break
        sid = getattr(entry, "session_id", "")
        if not sid or is_session_archived(sid):
            continue
        updated = getattr(entry, "updated_at", None)
        if isinstance(updated, datetime.datetime):
            if (datetime.datetime.now() - updated).total_seconds() < session_idle_floor_s:
                continue
        try:
            messages = session_store.load_transcript(sid)
        except Exception:
            continue
        if not messages or len(messages) < 5:
            continue
        src = getattr(entry, "platform", "unknown") or "unknown"
        src = src.value if hasattr(src, "value") else str(src)
        arch_entry = summarize_messages(sid, src, messages)
        conn = _connect()
        try:
            _migrate_legacy_json_if_needed(conn)
            _insert_entry(conn, arch_entry)
            conn.commit()
            archived_count += 1
        except Exception:
            continue
        finally:
            conn.close()

    return {"changed": archived_count, "archived": archived_count}


def build_default_tasks(
    sleep_cfg: dict[str, Any],
    *,
    session_store: Any | None = None,
    retention_days: int = 14,
    l4_max_entries: int = 2000,
    l4_max_age_days: int = 180,
    l4_keep_priority_at_least: int = 4,
    l4_max_sessions_per_cycle: int = 50,
) -> list[BackgroundTask]:
    """Assemble the canonical maintenance task list from resolved sleep_cfg.

    Callers that don't need gateway-scoped tasks (for example
    ``agent.dream_mode.run_dream_cycle``) can pass ``session_store=None``;
    the retention + L4 archive tasks are simply skipped.
    """
    maintenance_cadence = max(
        60, int(sleep_cfg.get("maintenance_interval_seconds", 14400) or 14400)
    )
    l4_cadence = max(60, int(sleep_cfg.get("l4_interval_seconds", 5400) or 5400))
    idle_required = int(sleep_cfg.get("idle_before_maintenance_seconds", 0) or 0)

    tasks: list[BackgroundTask] = []

    if sleep_cfg.get("background_review", True):
        tasks.append(
            BackgroundTask(
                name="builtin_memory",
                action=_builtin_memory_action,
                base_cadence_s=maintenance_cadence,
                jitter_s=min(300, maintenance_cadence // 20 or 30),
                idle_required_s=0,
                tags=("memory", "dream"),
            )
        )

    tasks.append(
        BackgroundTask(
            name="capability_lifecycle",
            action=_capability_lifecycle_action,
            base_cadence_s=maintenance_cadence,
            jitter_s=min(300, maintenance_cadence // 20 or 30),
            idle_required_s=0,
            tags=("lifecycle", "dream"),
        )
    )

    if sleep_cfg.get("l4_compaction", True):
        tasks.append(
            BackgroundTask(
                name="l4_compaction",
                action=lambda: _l4_compaction_action(
                    max_entries=l4_max_entries,
                    max_age_days=l4_max_age_days,
                    keep_priority_at_least=l4_keep_priority_at_least,
                ),
                base_cadence_s=maintenance_cadence,
                jitter_s=min(300, maintenance_cadence // 20 or 30),
                idle_required_s=0,
                tags=("l4", "dream"),
            )
        )

    if session_store is not None:
        tasks.append(
            BackgroundTask(
                name="retention_archive",
                action=lambda: _retention_action(session_store, retention_days),
                base_cadence_s=maintenance_cadence,
                jitter_s=min(300, maintenance_cadence // 20 or 30),
                idle_required_s=idle_required,
                tags=("retention", "gateway"),
            )
        )
        if sleep_cfg.get("l4_periodic_archive", True):
            tasks.append(
                BackgroundTask(
                    name="l4_periodic_archive",
                    action=lambda: _l4_periodic_archive_action(
                        session_store,
                        max_sessions_per_cycle=l4_max_sessions_per_cycle,
                    ),
                    base_cadence_s=l4_cadence,
                    jitter_s=min(300, l4_cadence // 20 or 30),
                    idle_required_s=idle_required,
                    tags=("l4", "gateway"),
                )
            )

    return tasks


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class BackgroundScheduler:
    """Shared runner for dream + gateway maintenance tasks.

    Not thread-safe across scheduler instances, but a single instance IS safe
    to use from concurrent sync/async callers — each task carries its own
    lock so dream (sync) and gateway (async) can't trample on the same
    action if both happen to run the same task name.
    """

    def __init__(
        self,
        sleep_cfg: dict[str, Any],
        tasks: Sequence[BackgroundTask],
        *,
        state_path: Path | None = None,
        empty_streak_before_backoff: int = DEFAULT_EMPTY_STREAK_BEFORE_BACKOFF,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.sleep_cfg = sleep_cfg
        self.tasks: list[BackgroundTask] = list(tasks)
        self._state_path = state_path or (get_hermes_home() / STATE_FILE)
        self._empty_streak_before_backoff = max(1, int(empty_streak_before_backoff))
        self._clock = clock
        self._task_locks: dict[str, threading.Lock] = {
            t.name: threading.Lock() for t in self.tasks
        }
        self._runtime: dict[str, _TaskRuntime] = {
            t.name: _TaskRuntime(current_cadence_s=t.base_cadence_s) for t in self.tasks
        }
        self._hydrate_runtime_from_disk()
        # Seed first-run times for never-run tasks so ``run_forever`` can fire
        # them shortly after startup, staggered to avoid one big tick.
        now = self._clock()
        for i, task in enumerate(self.tasks):
            rt = self._runtime[task.name]
            if rt.last_run_at is None and rt.next_run_at == 0.0:
                rt.next_run_at = now + min(task.base_cadence_s, 60 + i * 10)

    # -- State persistence --------------------------------------------------

    def _hydrate_runtime_from_disk(self) -> None:
        state = self._read_state()
        tasks_state = state.get("tasks") or {}
        if not isinstance(tasks_state, dict):
            return
        for name, rt in self._runtime.items():
            row = tasks_state.get(name)
            if not isinstance(row, dict):
                continue
            try:
                rt.current_cadence_s = int(
                    row.get("current_cadence_s") or rt.current_cadence_s
                )
            except (TypeError, ValueError):
                pass
            try:
                rt.consecutive_empty = int(row.get("consecutive_empty") or 0)
            except (TypeError, ValueError):
                rt.consecutive_empty = 0
            try:
                rt.last_changed = int(row.get("last_changed") or 0)
            except (TypeError, ValueError):
                rt.last_changed = 0
            last_run = row.get("last_run_at")
            if isinstance(last_run, (int, float)):
                rt.last_run_at = float(last_run)

    def _read_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_state(self, result: dict[str, Any] | None = None) -> None:
        state = self._read_state()
        now = self._clock()
        state.setdefault("runs", 0)
        if result is not None:
            state["runs"] = int(state.get("runs") or 0) + 1
            state["last_run_at"] = now
            state["last_duration_seconds"] = float(result.get("duration_seconds") or 0.0)
            state["last_result"] = result
        state["tasks"] = {
            name: {
                "current_cadence_s": rt.current_cadence_s,
                "consecutive_empty": rt.consecutive_empty,
                "last_run_at": rt.last_run_at,
                "last_changed": rt.last_changed,
                "next_run_at": rt.next_run_at,
            }
            for name, rt in self._runtime.items()
        }
        _atomic_write_state(self._state_path, state)

    # -- Public snapshot ----------------------------------------------------

    def task_states(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "current_cadence_s": rt.current_cadence_s,
                "consecutive_empty": rt.consecutive_empty,
                "last_run_at": rt.last_run_at,
                "last_changed": rt.last_changed,
                "next_run_at": rt.next_run_at,
            }
            for name, rt in self._runtime.items()
        }

    # -- Cadence / backoff --------------------------------------------------

    def _apply_backoff(self, task: BackgroundTask, changed: int) -> None:
        rt = self._runtime[task.name]
        if changed > 0:
            rt.consecutive_empty = 0
            rt.current_cadence_s = task.base_cadence_s
            return
        rt.consecutive_empty += 1
        if rt.consecutive_empty < self._empty_streak_before_backoff:
            return
        max_cadence = task.base_cadence_s * max(1, task.max_backoff_multiplier)
        rt.current_cadence_s = min(max_cadence, max(task.base_cadence_s, rt.current_cadence_s * 2))

    def _next_due_at(self, task: BackgroundTask) -> float:
        rt = self._runtime[task.name]
        jitter = 0.0
        if task.jitter_s > 0:
            jitter = random.uniform(0, float(task.jitter_s))
        return self._clock() + rt.current_cadence_s + jitter

    # -- Execution: synchronous one-shot ------------------------------------

    def _execute_task(self, task: BackgroundTask) -> dict[str, Any]:
        lock = self._task_locks[task.name]
        if not lock.acquire(blocking=False):
            return {"skipped": "already_running"}
        try:
            if task.requires is not None:
                try:
                    if not task.requires():
                        return {"skipped": "requirements_unmet"}
                except Exception as exc:
                    logger.debug("Task %s requirements check failed: %s", task.name, exc)
                    return {"skipped": "requirements_error", "error": str(exc)}
            started = self._clock()
            try:
                raw = task.action()
                result = _normalise_result(raw)
            except Exception as exc:
                logger.warning("Background task %s failed: %s", task.name, exc)
                result = {"changed": 0, "error": str(exc)}
            duration = self._clock() - started
            result["duration_seconds"] = round(duration, 3)
            result.setdefault("changed", 0)
            changed = int(result.get("changed") or 0)
            rt = self._runtime[task.name]
            rt.last_run_at = started
            rt.last_changed = changed
            self._apply_backoff(task, changed)
            rt.next_run_at = self._next_due_at(task)
            return result
        finally:
            lock.release()

    def run_once(
        self,
        *,
        tags: Iterable[str] | None = None,
        task_names: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Run matching tasks once, sequentially.

        ``tags`` filters by ``BackgroundTask.tags`` intersection; ``task_names``
        filters by exact name. When both are ``None`` every registered task
        runs. Returns a dict usable as dream_mode's historical payload.
        """
        started = self._clock()
        tag_set = set(tags) if tags else None
        name_set = set(task_names) if task_names else None

        actions: dict[str, Any] = {}
        errors: list[str] = []
        ok = True

        for task in self.tasks:
            if tag_set and not (set(task.tags) & tag_set):
                continue
            if name_set and task.name not in name_set:
                continue
            result = self._execute_task(task)
            actions[task.name] = result
            if "error" in result:
                ok = False
                errors.append(f"{task.name}: {result['error']}")

        finished = self._clock()
        summary = {
            "ok": ok,
            "started_at": started,
            "finished_at": finished,
            "duration_seconds": round(finished - started, 3),
            "actions": actions,
            "errors": errors,
            "task_states": self.task_states(),
        }
        self._write_state(summary)
        return summary

    # -- Execution: async steady-state --------------------------------------

    async def run_forever(
        self,
        idle_probe: IdleProbe,
        stop_event: asyncio.Event | None = None,
        *,
        initial_delay: int = 0,
        poll_interval: int = 60,
    ) -> None:
        """Async loop: run each task on its own cadence.

        Respects ``idle_before_maintenance_seconds``: a task whose
        ``idle_required_s > 0`` is deferred until ``idle_probe`` reports at
        least that many seconds since the last session activity.
        """
        if initial_delay > 0:
            try:
                await asyncio.sleep(initial_delay)
            except asyncio.CancelledError:
                return

        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    return
                now = self._clock()
                due_tasks = [t for t in self.tasks if self._runtime[t.name].next_run_at <= now]
                if not due_tasks:
                    await self._sleep_with_stop(poll_interval, stop_event)
                    continue

                idle_seconds: float | None = None
                # Track whether any task actually mutated runtime state so we
                # only hit disk when there's something new to persist.
                state_dirty = False
                for task in due_tasks:
                    if task.idle_required_s > 0:
                        if idle_seconds is None:
                            try:
                                idle_seconds = idle_probe.seconds_since_activity()
                            except Exception:
                                idle_seconds = float("inf")
                        if idle_seconds < task.idle_required_s:
                            rt = self._runtime[task.name]
                            retry_delay = max(
                                30,
                                min(300, task.idle_required_s // 6 or 30),
                            )
                            rt.next_run_at = now + retry_delay
                            state_dirty = True
                            logger.debug(
                                "Task %s waiting for idle threshold: %.0fs / %.0fs",
                                task.name,
                                idle_seconds,
                                task.idle_required_s,
                            )
                            continue
                    # run on a worker thread so blocking DB work doesn't stall the loop
                    try:
                        result = await asyncio.to_thread(self._execute_task, task)
                    except Exception as exc:
                        logger.warning("Scheduler dispatch failed for %s: %s", task.name, exc)
                        continue
                    # ``_execute_task`` mutates last_run_at + next_run_at only
                    # when the action actually ran (or raised). "skipped"
                    # sentinels (already_running / requirements_unmet /
                    # requirements_error) return before touching runtime, so
                    # there's nothing new to persist for them.
                    if not result.get("skipped"):
                        state_dirty = True
                    changed = int(result.get("changed") or 0)
                    if changed:
                        logger.info(
                            "Background task %s changed %d item(s) in %.2fs",
                            task.name,
                            changed,
                            result.get("duration_seconds") or 0.0,
                        )

                if state_dirty:
                    self._write_state()
                await self._sleep_with_stop(poll_interval, stop_event)
        except asyncio.CancelledError:
            raise

    async def _sleep_with_stop(
        self, seconds: float, stop_event: asyncio.Event | None
    ) -> None:
        if seconds <= 0:
            return
        if stop_event is None:
            await asyncio.sleep(seconds)
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass


# ---------------------------------------------------------------------------
# Convenience factories
# ---------------------------------------------------------------------------


def build_scheduler(
    config: dict[str, Any] | None = None,
    *,
    session_store: Any | None = None,
    retention_days: int = 14,
    l4_max_entries: int = 2000,
    l4_max_age_days: int = 180,
    l4_keep_priority_at_least: int = 4,
    l4_max_sessions_per_cycle: int = 50,
    state_path: Path | None = None,
    sleep_cfg: dict[str, Any] | None = None,
) -> BackgroundScheduler:
    """Resolve sleep-mode from config and return a scheduler with default tasks.

    *sleep_cfg* lets callers reuse an already-resolved sleep config — used by
    ``dream_mode.run_dream_cycle`` to avoid re-resolving (which deepcopies a
    profile template) once per call.
    """
    if sleep_cfg is None:
        sleep_cfg = resolve_sleep_mode(config or {})
    tasks = build_default_tasks(
        sleep_cfg,
        session_store=session_store,
        retention_days=retention_days,
        l4_max_entries=l4_max_entries,
        l4_max_age_days=l4_max_age_days,
        l4_keep_priority_at_least=l4_keep_priority_at_least,
        l4_max_sessions_per_cycle=l4_max_sessions_per_cycle,
    )
    return BackgroundScheduler(sleep_cfg, tasks, state_path=state_path)
