from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List

from hermes_constants import get_hermes_home

ARCHIVE_DIR = get_hermes_home() / "memory_l4"
ARCHIVE_PATH = ARCHIVE_DIR / "archive.json"
ARCHIVE_DB_PATH = ARCHIVE_DIR / "archive.db"
RETENTION_DAYS = 14
DEFAULT_MAX_ARCHIVE_ENTRIES = 2000
DEFAULT_MAX_ARCHIVE_AGE_DAYS = 180
DEFAULT_KEEP_PRIORITY_AT_LEAST = 4


def _ensure_archive_dir() -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def _connect() -> sqlite3.Connection:
    _ensure_archive_dir()
    conn = sqlite3.connect(str(ARCHIVE_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS l4_archive ("
        "session_id TEXT PRIMARY KEY, source TEXT, archived_at TEXT, message_count INTEGER, "
        "tool_names TEXT, summary TEXT, user_intents TEXT, task_type TEXT, category TEXT, "
        "priority INTEGER, confidence REAL, project_tag TEXT, project_root TEXT, outcome TEXT, "
        "unresolved INTEGER, important_files TEXT, important_commands TEXT, workflow_candidate TEXT, "
        "skill_candidate TEXT, linked_workflow TEXT, linked_skill TEXT, conflict_flag INTEGER, "
        "stale_flag INTEGER, superseded_by TEXT)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS l4_archive_fts USING fts5("
        "session_id UNINDEXED, source, summary, user_intents, tool_names, important_files, important_commands, category, project_tag, content='')"
    )
    return conn


def _insert_entry(conn: sqlite3.Connection, entry: Dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO l4_archive(session_id, source, archived_at, message_count, tool_names, summary, user_intents, task_type, category, priority, confidence, project_tag, project_root, outcome, unresolved, important_files, important_commands, workflow_candidate, skill_candidate, linked_workflow, linked_skill, conflict_flag, stale_flag, superseded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entry["session_id"], entry.get("source"), entry.get("archived_at"), int(entry.get("message_count") or 0),
            json.dumps(entry.get("tool_names") or []), entry.get("summary") or "", json.dumps(entry.get("user_intents") or []),
            entry.get("task_type") or "general", entry.get("category") or "general", int(entry.get("priority") or 0),
            float(entry.get("confidence") or 0.0), entry.get("project_tag") or "global", entry.get("project_root") or "",
            entry.get("outcome") or "", 1 if entry.get("unresolved") else 0,
            json.dumps(entry.get("important_files") or []), json.dumps(entry.get("important_commands") or []),
            entry.get("workflow_candidate") or "", entry.get("skill_candidate") or "", entry.get("linked_workflow") or "",
            entry.get("linked_skill") or "", 1 if entry.get("conflict_flag") else 0, 1 if entry.get("stale_flag") else 0,
            entry.get("superseded_by") or "",
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO l4_archive_fts(rowid, session_id, source, summary, user_intents, tool_names, important_files, important_commands, category, project_tag) VALUES ((SELECT rowid FROM l4_archive WHERE session_id = ?), ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entry["session_id"], entry["session_id"], entry.get("source") or "", entry.get("summary") or "",
            "\n".join(entry.get("user_intents") or []), " ".join(entry.get("tool_names") or []),
            "\n".join(entry.get("important_files") or []), "\n".join(entry.get("important_commands") or []),
            entry.get("category") or "general", entry.get("project_tag") or "global",
        ),
    )


def _row_to_entry(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "session_id": row["session_id"], "source": row["source"], "archived_at": row["archived_at"],
        "message_count": int(row["message_count"] or 0), "tool_names": json.loads(row["tool_names"] or "[]"),
        "summary": row["summary"] or "", "user_intents": json.loads(row["user_intents"] or "[]"),
        "task_type": row["task_type"] or "general", "category": row["category"] or "general",
        "priority": int(row["priority"] or 0), "confidence": float(row["confidence"] or 0.0),
        "project_tag": row["project_tag"] or "global", "project_root": row["project_root"] or "",
        "outcome": row["outcome"] or "", "unresolved": bool(row["unresolved"] or 0),
        "important_files": json.loads(row["important_files"] or "[]"), "important_commands": json.loads(row["important_commands"] or "[]"),
        "workflow_candidate": row["workflow_candidate"] or "", "skill_candidate": row["skill_candidate"] or "",
        "linked_workflow": row["linked_workflow"] or "", "linked_skill": row["linked_skill"] or "",
        "conflict_flag": bool(row["conflict_flag"] or 0), "stale_flag": bool(row["stale_flag"] or 0),
        "superseded_by": row["superseded_by"] or "",
    }


def _migrate_legacy_json_if_needed(conn: sqlite3.Connection) -> None:
    if not ARCHIVE_PATH.exists():
        return
    row = conn.execute("SELECT COUNT(*) AS c FROM l4_archive").fetchone()
    if row and int(row["c"] or 0) > 0:
        return
    try:
        data = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(data, list):
        return
    for entry in data:
        if isinstance(entry, dict) and entry.get("session_id"):
            entry.setdefault("task_type", "general")
            entry.setdefault("category", "general")
            entry.setdefault("priority", 0)
            entry.setdefault("confidence", 0.5)
            entry.setdefault("project_tag", "global")
            entry.setdefault("project_root", "")
            entry.setdefault("outcome", "")
            entry.setdefault("unresolved", False)
            entry.setdefault("important_files", [])
            entry.setdefault("important_commands", [])
            entry.setdefault("workflow_candidate", "")
            entry.setdefault("skill_candidate", "")
            entry.setdefault("linked_workflow", "")
            entry.setdefault("linked_skill", "")
            entry.setdefault("conflict_flag", False)
            entry.setdefault("stale_flag", False)
            entry.setdefault("superseded_by", "")
            _insert_entry(conn, entry)
    conn.commit()


def _load_archive() -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        _migrate_legacy_json_if_needed(conn)
        rows = conn.execute("SELECT * FROM l4_archive ORDER BY priority DESC, archived_at DESC").fetchall()
        return [_row_to_entry(row) for row in rows]
    finally:
        conn.close()


def _extract_files(messages: List[Dict[str, Any]]) -> List[str]:
    files = []
    path_re = re.compile(r"(?P<path>(?:~?/|/)?[A-Za-z0-9._-]+(?:/[A-Za-z0-9._*?-]+)+)")
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = str(msg.get("content") or "")
        for match in path_re.finditer(content):
            candidate = match.group("path").strip().strip("`\"'(),")
            if candidate and candidate not in files:
                files.append(candidate)
    return files[:10]


def _extract_commands(messages: List[Dict[str, Any]]) -> List[str]:
    commands = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            if fn.get("name") == "bash":
                raw = str(fn.get("arguments") or "").strip()
                if raw and raw not in commands:
                    commands.append(raw[:300])
    return commands[:10]


def _infer_task_type(user_messages: List[str]) -> str:
    text = "\n".join(user_messages).lower()
    if any(token in text for token in ["watch ", "every ", "daily", "report"]):
        return "workflow"
    if any(token in text for token in ["fix ", "error", "bug", "stack trace"]):
        return "troubleshooting"
    if any(token in text for token in ["write ", "summarize", "export", "generate"]):
        return "content"
    return "general"


def _infer_category(task_type: str, files: List[str], commands: List[str]) -> str:
    joined = "\n".join(files + commands).lower()
    if "docker" in joined or "kubectl" in joined or "systemctl" in joined:
        return "devops"
    if any(ext in joined for ext in [".py", ".js", ".ts", ".go", ".rs"]):
        return "software-development"
    if any(ext in joined for ext in [".md", ".docx", ".txt", ".csv"]):
        return "productivity"
    if task_type == "troubleshooting":
        return "devops"
    if task_type == "content":
        return "productivity"
    if task_type == "workflow":
        return "autonomous-ai-agents"
    return "general"


def _infer_priority(task_type: str, unresolved: bool, commands: List[str], files: List[str]) -> int:
    score = 1
    if task_type in {"workflow", "troubleshooting"}:
        score += 2
    if unresolved:
        score += 2
    if commands:
        score += 1
    if len(files) >= 2:
        score += 1
    return min(score, 5)


def _infer_project(messages: List[Dict[str, Any]], files: List[str]) -> tuple[str, str]:
    cwd = os.getenv("TERMINAL_CWD", "").strip()
    if cwd:
        return Path(cwd).name or "global", cwd
    text = "\n".join(str(m.get("content") or "") for m in messages if isinstance(m, dict))
    root_match = re.search(r"(?:workspace|project|repo|repository)\s*[:=]\s*([^\n]+)", text, re.IGNORECASE)
    if root_match:
        root = root_match.group(1).strip()
        return Path(root).name or "global", root
    if files:
        root = str(Path(files[0]).parent)
        return Path(root).name or "global", root
    return "global", ""


def _apply_governance_flags(entry: Dict[str, Any]) -> Dict[str, Any]:
    summary = str(entry.get("summary") or "").lower()
    outcome = str(entry.get("outcome") or "").lower()
    entry["conflict_flag"] = any(token in summary for token in ["but later", "however later", "conflict", "contradict"])
    entry["stale_flag"] = any(token in outcome for token in ["deprecated", "legacy", "obsolete", "stale"])
    entry.setdefault("superseded_by", "")
    return entry


def summarize_messages(session_id: str, source: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    user_messages: List[str] = []
    assistant_messages: List[str] = []
    tool_names: List[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        content = str(msg.get("content") or "").strip()
        if role == "user" and content:
            user_messages.append(content)
        elif role == "assistant" and content:
            assistant_messages.append(content)
        tool_name = msg.get("tool_name")
        if tool_name and tool_name not in tool_names:
            tool_names.append(str(tool_name))
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                name = fn.get("name")
                if name and name not in tool_names:
                    tool_names.append(str(name))

    preview: List[str] = []
    if user_messages:
        preview.append("User intent: " + user_messages[0][:500])
    if len(user_messages) > 1:
        preview.append("Follow-up: " + user_messages[1][:500])
    if assistant_messages:
        preview.append("Outcome: " + assistant_messages[-1][:500])

    task_type = _infer_task_type(user_messages)
    outcome = assistant_messages[-1][:500] if assistant_messages else ""
    unresolved = any(token in outcome.lower() for token in ["todo", "follow up", "unresolved", "not fixed", "failed"]) if outcome else False
    important_files = _extract_files(messages)
    important_commands = _extract_commands(messages)
    category = _infer_category(task_type, important_files, important_commands)
    priority = _infer_priority(task_type, unresolved, important_commands, important_files)
    confidence = 0.8 if task_type in {"workflow", "troubleshooting", "content"} else 0.5
    project_tag, project_root = _infer_project(messages, important_files)
    workflow_candidate = session_id if task_type in {"workflow", "content"} else ""
    skill_candidate = session_id if task_type == "troubleshooting" else ""

    entry = {
        "session_id": session_id,
        "source": source,
        "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "message_count": len(messages),
        "tool_names": tool_names,
        "summary": "\n".join(preview).strip(),
        "user_intents": user_messages[:5],
        "task_type": task_type,
        "category": category,
        "priority": priority,
        "confidence": confidence,
        "project_tag": project_tag,
        "project_root": project_root,
        "outcome": outcome,
        "unresolved": unresolved,
        "important_files": important_files,
        "important_commands": important_commands,
        "workflow_candidate": workflow_candidate,
        "skill_candidate": skill_candidate,
        "linked_workflow": "",
        "linked_skill": "",
        "conflict_flag": False,
        "stale_flag": False,
        "superseded_by": "",
    }
    return _apply_governance_flags(entry)



def is_session_archived(session_id: str) -> bool:
    conn = _connect()
    try:
        row = conn.execute("SELECT 1 FROM l4_archive WHERE session_id = ?", (session_id,)).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        conn.close()


def get_archived_session_ids() -> set:
    conn = _connect()
    try:
        rows = conn.execute("SELECT session_id FROM l4_archive").fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()
    finally:
        conn.close()


def archive_session_summary(session_id: str, source: str, messages: List[Dict[str, Any]]) -> None:
    entry = summarize_messages(session_id, source, messages)
    conn = _connect()
    try:
        _migrate_legacy_json_if_needed(conn)
        _insert_entry(conn, entry)
        conn.commit()
    finally:
        conn.close()


def compact_archive(
    *,
    max_entries: int = DEFAULT_MAX_ARCHIVE_ENTRIES,
    max_age_days: int = DEFAULT_MAX_ARCHIVE_AGE_DAYS,
    keep_priority_at_least: int = DEFAULT_KEEP_PRIORITY_AT_LEAST,
) -> Dict[str, int]:
    """Prune low-value L4 rows with cheap SQLite rules.

    High-priority, linked, unresolved, conflict, and workflow/skill candidate rows are kept.
    This keeps long-running gateways fast without using an LLM or deleting important memory.
    """
    max_entries = max(0, int(max_entries or 0))
    max_age_days = max(0, int(max_age_days or 0))
    keep_priority_at_least = max(0, int(keep_priority_at_least or 0))
    if max_entries == 0 and max_age_days == 0:
        return {"deleted": 0, "remaining": 0}

    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - max_age_days * 86400)) if max_age_days else ""
    conn = _connect()
    try:
        _migrate_legacy_json_if_needed(conn)
        deleted_ids: set[str] = set()

        protected = (
            "priority >= ? OR unresolved = 1 OR conflict_flag = 1 OR "
            "COALESCE(linked_workflow, '') != '' OR COALESCE(linked_skill, '') != '' OR "
            "COALESCE(workflow_candidate, '') != '' OR COALESCE(skill_candidate, '') != ''"
        )
        if max_age_days:
            rows = conn.execute(
                f"SELECT session_id FROM l4_archive WHERE archived_at < ? AND NOT ({protected})",
                (cutoff, keep_priority_at_least),
            ).fetchall()
            deleted_ids.update(str(row[0]) for row in rows)

        if max_entries:
            rows = conn.execute(
                f"""
                SELECT session_id FROM l4_archive
                WHERE NOT ({protected})
                ORDER BY priority ASC, archived_at ASC
                LIMIT MAX((SELECT COUNT(*) FROM l4_archive) - ?, 0)
                """,
                (keep_priority_at_least, max_entries),
            ).fetchall()
            deleted_ids.update(str(row[0]) for row in rows)

        if deleted_ids:
            placeholders = ",".join("?" for _ in deleted_ids)
            conn.execute(f"DELETE FROM l4_archive WHERE session_id IN ({placeholders})", tuple(deleted_ids))
            conn.execute("DROP TABLE IF EXISTS l4_archive_fts")
            conn.execute(
                "CREATE VIRTUAL TABLE l4_archive_fts USING fts5("
                "session_id UNINDEXED, source, summary, user_intents, tool_names, important_files, important_commands, category, project_tag, content='')"
            )
            for row in conn.execute("SELECT rowid, * FROM l4_archive").fetchall():
                entry = _row_to_entry(row)
                conn.execute(
                    "INSERT INTO l4_archive_fts(rowid, session_id, source, summary, user_intents, tool_names, important_files, important_commands, category, project_tag) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(row["rowid"]), entry["session_id"], entry.get("source") or "", entry.get("summary") or "",
                        "\n".join(entry.get("user_intents") or []), " ".join(entry.get("tool_names") or []),
                        "\n".join(entry.get("important_files") or []), "\n".join(entry.get("important_commands") or []),
                        entry.get("category") or "general", entry.get("project_tag") or "global",
                    ),
                )
            conn.execute("PRAGMA optimize")
            conn.commit()

        remaining = int(conn.execute("SELECT COUNT(*) FROM l4_archive").fetchone()[0])
        return {"deleted": len(deleted_ids), "remaining": remaining}
    finally:
        conn.close()


def link_workflow_candidate(session_id: str, workflow_name: str) -> None:
    conn = _connect()
    try:
        _migrate_legacy_json_if_needed(conn)
        conn.execute(
            "UPDATE l4_archive SET linked_workflow = ?, workflow_candidate = ? WHERE session_id = ?",
            (workflow_name, workflow_name, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def search_archive(query: str, limit: int = 3, project_tag: str | None = None) -> List[Dict[str, Any]]:
    query = str(query or "").strip()
    if not query:
        return []
    terms = [t for t in re.findall(r"[a-zA-Z0-9_*.\-/]+", query.lower()) if len(t) >= 2]
    if not terms:
        return []
    conn = _connect()
    try:
        sql = (
            "SELECT a.* FROM l4_archive_fts f JOIN l4_archive a ON a.rowid = f.rowid "
            "WHERE l4_archive_fts MATCH ? "
            + ("AND a.project_tag = ? " if project_tag else "")
            + "ORDER BY a.priority DESC, a.archived_at DESC LIMIT ?"
        )
        params = [" OR ".join(terms)]
        if project_tag:
            params.append(project_tag)
        params.append(limit)
        rows = conn.execute(sql, tuple(params)).fetchall()
        results = [_row_to_entry(row) for row in rows]
        if results:
            return results
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()

    entries = _load_archive()
    if project_tag:
        entries = [e for e in entries if e.get("project_tag") == project_tag]
    scored = []
    for entry in entries:
        haystack = "\n".join([
            str(entry.get("source") or ""),
            str(entry.get("summary") or ""),
            " ".join(entry.get("tool_names") or []),
            "\n".join(entry.get("user_intents") or []),
            "\n".join(entry.get("important_files") or []),
            "\n".join(entry.get("important_commands") or []),
            str(entry.get("task_type") or ""),
            str(entry.get("category") or ""),
            str(entry.get("project_tag") or ""),
            str(entry.get("outcome") or ""),
            str(entry.get("linked_workflow") or ""),
        ]).lower()
        score = int(entry.get("priority") or 0)
        if entry.get("stale_flag"):
            score -= 2
        if entry.get("conflict_flag"):
            score -= 2
        for term in terms:
            if term in haystack:
                score += 3 if len(term) > 4 else 1
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda item: (-item[0], -int(item[1].get("priority") or 0), item[1].get("archived_at", "")))
    return [item[1] for item in scored[:limit]]
