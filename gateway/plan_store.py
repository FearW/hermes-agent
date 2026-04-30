"""Persistent store for Plan-mode approval artifacts.

Each turn that enters the plan-gate produces a :class:`PlanArtifact`.  The
artifact is persisted to ``state.db`` so that (a) the user's ✅/❌ reply
survives a gateway restart and (b) concurrent inbound messages on the same
session can see whether a plan is pending.

Schema lives in its own table (``plan_artifacts``) and is created
idempotently on first use — no hermes_state migration bump required.

Lifecycle::

    pending  ──user ✅──▶  approved  ──executor done──▶  executed
       │                         │
       │                         └──executor error──▶  failed
       │
       ├──user ❌──▶  rejected
       └──timeout──▶  timeout
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

from hermes_constants import get_hermes_home


_STEP_PREFIX_RE = re.compile(r"^\s*(?:\d+[.)-]|[-*•])\s+")


def _strip_step_prefix(step: str) -> str:
    """Remove an existing numbered/bulleted prefix so render can add its own."""
    return _STEP_PREFIX_RE.sub("", step, count=1).strip()


PlanStatus = str  # pending | approved | rejected | timeout | executed | failed

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
TIMEOUT = "timeout"
EXECUTED = "executed"
FAILED = "failed"

TERMINAL_STATUSES = frozenset({REJECTED, TIMEOUT, EXECUTED, FAILED})


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS plan_artifacts (
    plan_id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    user_message TEXT NOT NULL,
    summary TEXT NOT NULL,
    steps TEXT NOT NULL,
    risk_flags TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    decided_at REAL,
    decision_reason TEXT,
    executed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_plans_session_status
    ON plan_artifacts(session_key, status);
CREATE INDEX IF NOT EXISTS idx_plans_created
    ON plan_artifacts(created_at DESC);
"""


@dataclass
class PlanArtifact:
    """A single plan awaiting (or having received) user approval."""

    plan_id: str
    session_key: str
    platform: str
    chat_id: str
    user_message: str
    summary: str
    steps: List[str]
    risk_flags: List[str]
    status: PlanStatus = PENDING
    created_at: float = field(default_factory=time.time)
    decided_at: Optional[float] = None
    decision_reason: Optional[str] = None
    executed_at: Optional[float] = None

    @classmethod
    def new(
        cls,
        *,
        session_key: str,
        platform: str,
        chat_id: str,
        user_message: str,
        summary: str,
        steps: List[str],
        risk_flags: Optional[List[str]] = None,
    ) -> "PlanArtifact":
        return cls(
            plan_id=uuid.uuid4().hex[:16],
            session_key=session_key,
            platform=platform,
            chat_id=chat_id,
            user_message=user_message,
            summary=summary,
            steps=list(steps),
            risk_flags=list(risk_flags or []),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def render_markdown(self) -> str:
        """Human-facing plan card text (used by messaging platforms)."""
        lines = ["📋 **Plan**", ""]
        if self.summary:
            lines.append(self.summary)
            lines.append("")
        if self.steps:
            lines.append("**Steps:**")
            for i, step in enumerate(self.steps, 1):
                lines.append(f"{i}. {_strip_step_prefix(step)}")
            lines.append("")
        if self.risk_flags:
            lines.append("**Risks:** " + " · ".join(self.risk_flags))
            lines.append("")
        lines.append("Reply ✅ to approve, ❌ to cancel.")
        return "\n".join(lines)


class PlanStore:
    """Thread-safe store backed by ``state.db``."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = Path(db_path) if db_path else (get_hermes_home() / "state.db")
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_schema()

    # --- connection management ------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                isolation_level=None,  # autocommit; we batch via BEGIN/COMMIT
                timeout=30.0,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._conn = conn
        return self._conn

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.executescript(_SCHEMA_SQL)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # --- CRUD -----------------------------------------------------------

    def insert(self, plan: PlanArtifact) -> None:
        with self._lock:
            self._connect().execute(
                "INSERT INTO plan_artifacts "
                "(plan_id, session_key, platform, chat_id, user_message, summary, "
                " steps, risk_flags, status, created_at, decided_at, decision_reason, "
                " executed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    plan.plan_id, plan.session_key, plan.platform, plan.chat_id,
                    plan.user_message, plan.summary,
                    json.dumps(plan.steps, ensure_ascii=False),
                    json.dumps(plan.risk_flags, ensure_ascii=False),
                    plan.status, plan.created_at,
                    plan.decided_at, plan.decision_reason, plan.executed_at,
                ),
            )

    def get(self, plan_id: str) -> Optional[PlanArtifact]:
        with self._lock:
            row = self._connect().execute(
                "SELECT * FROM plan_artifacts WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        return self._row_to_plan(row) if row else None

    def pending_for_session(self, session_key: str) -> Optional[PlanArtifact]:
        with self._lock:
            row = self._connect().execute(
                "SELECT * FROM plan_artifacts "
                "WHERE session_key = ? AND status = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (session_key, PENDING),
            ).fetchone()
        return self._row_to_plan(row) if row else None

    def set_status(
        self,
        plan_id: str,
        status: PlanStatus,
        *,
        reason: Optional[str] = None,
    ) -> bool:
        """Transition a plan's status.  Returns True if a row was updated."""
        now = time.time()
        field_name = "executed_at" if status == EXECUTED else "decided_at"
        with self._lock:
            cur = self._connect().execute(
                f"UPDATE plan_artifacts SET status = ?, {field_name} = ?, "
                "decision_reason = COALESCE(?, decision_reason) "
                "WHERE plan_id = ?",
                (status, now, reason, plan_id),
            )
            return cur.rowcount > 0

    def expire_stale(self, older_than_sec: float) -> int:
        """Mark pending plans older than ``older_than_sec`` as timed-out."""
        cutoff = time.time() - older_than_sec
        with self._lock:
            cur = self._connect().execute(
                "UPDATE plan_artifacts "
                "SET status = ?, decided_at = ?, "
                "    decision_reason = COALESCE(decision_reason, 'timeout') "
                "WHERE status = ? AND created_at < ?",
                (TIMEOUT, time.time(), PENDING, cutoff),
            )
            return cur.rowcount

    def recent(self, limit: int = 20) -> List[PlanArtifact]:
        with self._lock:
            rows = self._connect().execute(
                "SELECT * FROM plan_artifacts "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_plan(r) for r in rows]

    # --- helpers --------------------------------------------------------

    @staticmethod
    def _row_to_plan(row: sqlite3.Row) -> PlanArtifact:
        return PlanArtifact(
            plan_id=row["plan_id"],
            session_key=row["session_key"],
            platform=row["platform"],
            chat_id=row["chat_id"],
            user_message=row["user_message"],
            summary=row["summary"],
            steps=json.loads(row["steps"] or "[]"),
            risk_flags=json.loads(row["risk_flags"] or "[]"),
            status=row["status"],
            created_at=row["created_at"],
            decided_at=row["decided_at"],
            decision_reason=row["decision_reason"],
            executed_at=row["executed_at"],
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[PlanStore] = None
_store_lock = threading.Lock()


def get_store() -> PlanStore:
    """Return the process-wide :class:`PlanStore` singleton."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = PlanStore()
    return _store
