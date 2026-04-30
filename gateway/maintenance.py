from __future__ import annotations

import asyncio
import logging

from agent.capability_lifecycle import run_lifecycle_maintenance

logger = logging.getLogger("gateway.maintenance")


async def retention_loop(session_store, *, older_than_days: int = 14, initial_delay: int = 300, interval_seconds: int = 21600) -> None:
    await asyncio.sleep(initial_delay)
    while True:
        try:
            pruned = session_store.archive_and_prune_expired_transcripts(older_than_days=older_than_days)
            if pruned:
                logger.info("Retention maintenance archived/pruned %d expired sessions", pruned)
            lifecycle = run_lifecycle_maintenance()
            if any(lifecycle.values()):
                logger.info("Lifecycle maintenance updated states: %s", lifecycle)
        except Exception as exc:
            logger.debug("Retention maintenance cycle failed: %s", exc)
        await asyncio.sleep(interval_seconds)


async def l4_periodic_archive_loop(session_store, *, initial_delay: int = 120, interval_seconds: int = 7200) -> None:
    """Periodically archive sessions into L4 (every 2h, sessions idle 30+ min)."""
    await asyncio.sleep(initial_delay)
    while True:
        try:
            from agent.l4_archive import (
                is_session_archived, summarize_messages, _connect, _insert_entry, _migrate_legacy_json_if_needed
            )
            import datetime
            archived_count = 0
            for entry in session_store.list_sessions():
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
