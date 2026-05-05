"""Gateway maintenance shim.

The actual task runner lives in :mod:`agent.background_scheduler`. This
module exposes ``start_scheduler()`` — the gateway's single entry point —
plus thin backward-compatible shims for the legacy ``retention_loop`` and
``l4_periodic_archive_loop`` functions so out-of-tree imports don't break.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from typing import Any

from agent.background_scheduler import (
    BackgroundScheduler,
    BackgroundTask,
    SessionStoreIdleProbe,
    _builtin_memory_action,
    _capability_lifecycle_action,
    _l4_compaction_action,
    _l4_periodic_archive_action,
    _retention_action,
    build_scheduler,
)
from agent.capability_lifecycle import run_lifecycle_maintenance  # re-export for tests

logger = logging.getLogger("gateway.maintenance")

__all__ = [
    "start_scheduler",
    "retention_loop",
    "l4_periodic_archive_loop",
    "run_lifecycle_maintenance",
]


def _seconds_since_latest_activity(session_store) -> float:
    """Legacy helper retained so existing imports don't break."""
    return SessionStoreIdleProbe(session_store).seconds_since_activity()


def start_scheduler(
    session_store: Any,
    *,
    config: dict[str, Any] | None = None,
    retention_days: int = 14,
    initial_delay: int = 120,
    poll_interval: int = 60,
    l4_max_entries: int = 2000,
    l4_max_age_days: int = 180,
    l4_keep_priority_at_least: int = 4,
    l4_max_sessions_per_cycle: int = 50,
    stop_event: asyncio.Event | None = None,
) -> tuple[asyncio.Task, BackgroundScheduler]:
    """Start the unified gateway maintenance scheduler as an asyncio task.

    Returns ``(task, scheduler)`` so the gateway can cancel + inspect state.
    """
    scheduler = build_scheduler(
        config,
        session_store=session_store,
        retention_days=retention_days,
        l4_max_entries=l4_max_entries,
        l4_max_age_days=l4_max_age_days,
        l4_keep_priority_at_least=l4_keep_priority_at_least,
        l4_max_sessions_per_cycle=l4_max_sessions_per_cycle,
    )
    probe = SessionStoreIdleProbe(session_store)
    task = asyncio.create_task(
        scheduler.run_forever(
            probe,
            stop_event=stop_event,
            initial_delay=initial_delay,
            poll_interval=poll_interval,
        ),
        name="gateway-background-scheduler",
    )
    logger.info("Started unified background scheduler with %d task(s)", len(scheduler.tasks))
    return task, scheduler


# ---------------------------------------------------------------------------
# Deprecated thin shims — retained for backward compatibility.
#
# Both shims delegate to ``BackgroundScheduler.run_forever`` with a tailored
# task list, so idle-gating, error isolation, adaptive backoff, and atomic
# state persistence all match the unified runtime. The legacy parameter
# surface (``initial_delay``, ``interval_seconds``, etc.) is preserved for
# out-of-tree callers.
# ---------------------------------------------------------------------------


async def retention_loop(
    session_store,
    *,
    older_than_days: int = 14,
    initial_delay: int = 300,
    interval_seconds: int = 21600,
    l4_compaction: bool = True,
    l4_max_entries: int = 2000,
    l4_max_age_days: int = 180,
    l4_keep_priority_at_least: int = 4,
    idle_before_maintenance_seconds: int = 0,
) -> None:
    """Deprecated. Prefer :func:`start_scheduler`.

    Thin shim: runs retention + builtin memory + lifecycle + (optional) L4
    compaction on the unified ``BackgroundScheduler``. Idle gating and error
    isolation are provided by the scheduler.
    """
    warnings.warn(
        "gateway.maintenance.retention_loop is deprecated; use "
        "gateway.maintenance.start_scheduler instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    cadence = max(60, int(interval_seconds))
    tasks: list[BackgroundTask] = [
        BackgroundTask(
            name="retention_archive",
            action=lambda: _retention_action(session_store, older_than_days),
            base_cadence_s=cadence,
            idle_required_s=max(0, int(idle_before_maintenance_seconds)),
            tags=("retention", "gateway"),
        ),
        BackgroundTask(
            name="builtin_memory",
            action=_builtin_memory_action,
            base_cadence_s=cadence,
            tags=("memory",),
        ),
        BackgroundTask(
            name="capability_lifecycle",
            action=_capability_lifecycle_action,
            base_cadence_s=cadence,
            tags=("lifecycle",),
        ),
    ]
    if l4_compaction:
        tasks.append(
            BackgroundTask(
                name="l4_compaction",
                action=lambda: _l4_compaction_action(
                    max_entries=l4_max_entries,
                    max_age_days=l4_max_age_days,
                    keep_priority_at_least=l4_keep_priority_at_least,
                ),
                base_cadence_s=cadence,
                tags=("l4",),
            )
        )
    scheduler = BackgroundScheduler({"enabled": True}, tasks)
    # Legacy retention_loop fired on the very first iteration after
    # initial_delay. Reset the scheduler's staggered next_run_at seeds so
    # out-of-tree callers relying on "first cycle runs immediately"
    # keep working.
    for rt in scheduler._runtime.values():
        rt.next_run_at = 0.0
    probe = SessionStoreIdleProbe(session_store)
    await scheduler.run_forever(
        probe, initial_delay=max(0, int(initial_delay)), poll_interval=60
    )


async def l4_periodic_archive_loop(
    session_store,
    *,
    initial_delay: int = 120,
    interval_seconds: int = 7200,
    max_sessions_per_cycle: int = 50,
    idle_before_maintenance_seconds: int = 0,
) -> None:
    """Deprecated. Prefer :func:`start_scheduler`.

    Thin shim: runs the L4 periodic archive on the unified scheduler with
    the same knobs the original loop accepted.
    """
    warnings.warn(
        "gateway.maintenance.l4_periodic_archive_loop is deprecated; use "
        "gateway.maintenance.start_scheduler instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    cadence = max(60, int(interval_seconds))
    tasks: list[BackgroundTask] = [
        BackgroundTask(
            name="l4_periodic_archive",
            action=lambda: _l4_periodic_archive_action(
                session_store,
                max_sessions_per_cycle=max_sessions_per_cycle,
            ),
            base_cadence_s=cadence,
            idle_required_s=max(0, int(idle_before_maintenance_seconds)),
            tags=("l4", "gateway"),
        ),
    ]
    scheduler = BackgroundScheduler({"enabled": True}, tasks)
    # Legacy loop fired on the very first iteration after initial_delay.
    for rt in scheduler._runtime.values():
        rt.next_run_at = 0.0
    probe = SessionStoreIdleProbe(session_store)
    await scheduler.run_forever(
        probe, initial_delay=max(0, int(initial_delay)), poll_interval=60
    )
