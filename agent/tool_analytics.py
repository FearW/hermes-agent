"""Lightweight SQLite-backed tool-usage analytics for Hermes Agent.

Every tool invocation is recorded as a single row.  Aggregation queries
power the ``hermes analytics`` CLI command.

Storage location: ``~/.hermes/analytics/tool_usage.db``
"""

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_DB_DIR_NAME = "analytics"
_DB_NAME = "tool_usage.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_invocations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    tool_name   TEXT    NOT NULL,
    toolset     TEXT    NOT NULL DEFAULT '',
    success     INTEGER NOT NULL DEFAULT 1,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    model       TEXT    NOT NULL DEFAULT '',
    platform    TEXT    NOT NULL DEFAULT '',
    session_id  TEXT    NOT NULL DEFAULT '',
    error_type  TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_inv_ts        ON tool_invocations (ts);
CREATE INDEX IF NOT EXISTS idx_inv_tool_name ON tool_invocations (tool_name);
CREATE INDEX IF NOT EXISTS idx_inv_platform  ON tool_invocations (platform);
"""


def _db_path() -> Path:
    return Path(get_hermes_home()) / _DB_DIR_NAME / _DB_NAME


_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            return conn
        except sqlite3.Error:
            try:
                conn.close()
            except Exception:
                pass
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _local.conn = conn
    return conn


def record_invocation(
    tool_name: str,
    *,
    toolset: str = "",
    success: bool = True,
    duration_ms: float = 0.0,
    model: str = "",
    platform: str = "",
    session_id: str = "",
    error_type: str = "",
) -> None:
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO tool_invocations "
            "(ts, tool_name, toolset, success, duration_ms, model, platform, session_id, error_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                tool_name,
                toolset,
                1 if success else 0,
                int(duration_ms),
                model,
                platform,
                session_id,
                error_type,
            ),
        )
        conn.commit()
    except Exception:
        logger.debug("analytics record failed", exc_info=True)


def query_summary(
    *,
    period: str = "day",
    tool_name: Optional[str] = None,
    platform: Optional[str] = None,
    model: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    period_hours = {"hour": 1, "day": 24, "week": 168, "month": 720, "all": 999999}
    hours = period_hours.get(period, 24)

    where_parts = ["julianday('now') - julianday(ts) <= ?"]
    params: list = [hours / 24.0]

    if tool_name:
        where_parts.append("tool_name = ?")
        params.append(tool_name)
    if platform:
        where_parts.append("platform = ?")
        params.append(platform)
    if model:
        where_parts.append("model = ?")
        params.append(model)

    where_clause = " AND ".join(where_parts)

    sql = f"""
        SELECT
            tool_name,
            COUNT(*)                                          AS total_calls,
            SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END)     AS successes,
            SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END)     AS failures,
            ROUND(AVG(duration_ms), 1)                        AS avg_ms,
            COALESCE(MIN(duration_ms), 0)                     AS min_ms,
            COALESCE(MAX(duration_ms), 0)                     AS max_ms,
            COALESCE(
                CAST(SUBSTR(
                    GROUP_CONCAT(duration_ms ORDER BY duration_ms),
                    1,
                    INSTR(GROUP_CONCAT(duration_ms ORDER BY duration_ms) || ',', ',')
                ) AS INTEGER),
                0
            )                                                 AS p50_ms
        FROM tool_invocations
        WHERE {where_clause}
        GROUP BY tool_name
        ORDER BY total_calls DESC
        LIMIT ?
    """
    params.append(limit)

    try:
        conn = _get_conn()
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.debug("analytics query failed", exc_info=True)
        return []


def query_timeline(
    *,
    period: str = "day",
    tool_name: Optional[str] = None,
    bucket: str = "hour",
) -> List[Dict[str, Any]]:
    period_hours = {"hour": 1, "day": 24, "week": 168, "month": 720, "all": 999999}
    hours = period_hours.get(period, 24)

    bucket_fmt = {"hour": "%Y-%m-%d %H:00", "day": "%Y-%m-%d", "week": "%Y-W%W"}
    fmt = bucket_fmt.get(bucket, "%Y-%m-%d %H:00")

    where_parts = ["julianday('now') - julianday(ts) <= ?"]
    params: list = [hours / 24.0]
    if tool_name:
        where_parts.append("tool_name = ?")
        params.append(tool_name)

    where_clause = " AND ".join(where_parts)

    sql = f"""
        SELECT
            strftime('{fmt}', ts) AS bucket,
            COUNT(*)                                          AS total_calls,
            SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END)     AS successes,
            SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END)     AS failures,
            ROUND(AVG(duration_ms), 1)                        AS avg_ms
        FROM tool_invocations
        WHERE {where_clause}
        GROUP BY bucket
        ORDER BY bucket
    """

    try:
        conn = _get_conn()
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.debug("analytics timeline query failed", exc_info=True)
        return []


def query_top_errors(
    *,
    period: str = "day",
    limit: int = 10,
) -> List[Dict[str, Any]]:
    period_hours = {"hour": 1, "day": 24, "week": 168, "month": 720, "all": 999999}
    hours = period_hours.get(period, 24)

    sql = """
        SELECT tool_name, error_type, COUNT(*) AS count
        FROM tool_invocations
        WHERE success = 0
          AND julianday('now') - julianday(ts) <= ?
        GROUP BY tool_name, error_type
        ORDER BY count DESC
        LIMIT ?
    """

    try:
        conn = _get_conn()
        rows = conn.execute(sql, [hours / 24.0, limit]).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        logger.debug("analytics top_errors query failed", exc_info=True)
        return []


def purge_old_data(max_days: int = 90) -> int:
    try:
        conn = _get_conn()
        cur = conn.execute(
            "DELETE FROM tool_invocations WHERE julianday('now') - julianday(ts) > ?",
            [max_days],
        )
        conn.commit()
        return cur.rowcount
    except Exception:
        logger.debug("analytics purge failed", exc_info=True)
        return 0
