from __future__ import annotations

import asyncio
import datetime
import logging

from agent.capability_lifecycle import run_lifecycle_maintenance

logger = logging.getLogger("gateway.maintenance")


def _seconds_since_latest_activity(session_store) -> float:
    """Return seconds since the newest session activity, or inf if unknown."""
    try:
        entries = list(session_store.list_sessions())
    except Exception:
        return float("inf")

    latest: datetime.datetime | None = None
    for entry in entries:
        updated = getattr(entry, "updated_at", None)
        if not isinstance(updated, datetime.datetime):
            continue
        if latest is None or updated > latest:
            latest = updated

    if latest is None:
        return float("inf")

    if latest.tzinfo is None:
        now = datetime.datetime.now()
    else:
        now = datetime.datetime.now(latest.tzinfo)
    return max(0.0, (now - latest).total_seconds())


def _idle_retry_delay(interval_seconds: int, idle_before_maintenance_seconds: int) -> int:
    if interval_seconds <= 0:
        return 60
    if idle_before_maintenance_seconds <= 0:
        return interval_seconds
    return min(interval_seconds, max(30, min(300, idle_before_maintenance_seconds // 6 or 30)))


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
    await asyncio.sleep(initial_delay)
    while True:
        if idle_before_maintenance_seconds > 0:
            idle_seconds = _seconds_since_latest_activity(session_store)
            if idle_seconds < idle_before_maintenance_seconds:
                logger.debug(
                    "Retention maintenance waiting for idle threshold: %.0fs / %.0fs",
                    idle_seconds,
                    idle_before_maintenance_seconds,
                )
                await asyncio.sleep(
                    _idle_retry_delay(interval_seconds, idle_before_maintenance_seconds)
                )
                continue
        try:
            pruned = session_store.archive_and_prune_expired_transcripts(older_than_days=older_than_days)
            if pruned:
                logger.info("Retention maintenance archived/pruned %d expired sessions", pruned)
            lifecycle = run_lifecycle_maintenance()
            if any(lifecycle.values()):
                logger.info("Lifecycle maintenance updated states: %s", lifecycle)
            from tools.memory_tool import compact_builtin_memory

            builtin = compact_builtin_memory()
            if builtin.get("memory_removed") or builtin.get("user_removed"):
                logger.info("Built-in memory auto-compacted: %s", builtin)
            if l4_compaction:
                from agent.l4_archive import compact_archive

                compacted = compact_archive(
                    max_entries=l4_max_entries,
                    max_age_days=l4_max_age_days,
                    keep_priority_at_least=l4_keep_priority_at_least,
                )
                if compacted.get("deleted"):
                    logger.info("L4 compaction deleted %(deleted)d row(s), %(remaining)d remaining", compacted)
        except Exception as exc:
            logger.debug("Retention maintenance cycle failed: %s", exc)
        await asyncio.sleep(interval_seconds)


async def l4_periodic_archive_loop(
    session_store,
    *,
    initial_delay: int = 120,
    interval_seconds: int = 7200,
    max_sessions_per_cycle: int = 50,
    idle_before_maintenance_seconds: int = 0,
) -> None:
    """Periodically archive sessions into L4 (every 2h, sessions idle 30+ min)."""
    await asyncio.sleep(initial_delay)
    while True:
        if idle_before_maintenance_seconds > 0:
            idle_seconds = _seconds_since_latest_activity(session_store)
            if idle_seconds < idle_before_maintenance_seconds:
                logger.debug(
                    "L4 periodic archive waiting for idle threshold: %.0fs / %.0fs",
                    idle_seconds,
                    idle_before_maintenance_seconds,
                )
                await asyncio.sleep(
                    _idle_retry_delay(interval_seconds, idle_before_maintenance_seconds)
                )
                continue
        try:
            from agent.l4_archive import (
                is_session_archived, summarize_messages, _connect, _insert_entry, _migrate_legacy_json_if_needed
            )
            import datetime
            archived_count = 0
            for entry in session_store.list_sessions():
                if max_sessions_per_cycle > 0 and archived_count >= max_sessions_per_cycle:
                    break
                sid = getattr(entry, "session_id", "")
                if not sid or is_session_archived(sid):
                    continue
                updated = getattr(entry, "updated_at", None)
                if isinstance(updated, datetime.datetime):
                    if (datetime.datetime.now() - updated).total_seconds() < 1800:
                        continue
                messages = session_store.load_transcript(sid)
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
                finally:
                    conn.close()
            if archived_count:
                logger.info("L4 periodic archive: archived %d session(s)", archived_count)
        except Exception as exc:
            logger.debug("L4 periodic archive cycle failed: %s", exc)
        await asyncio.sleep(interval_seconds)
