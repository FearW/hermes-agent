from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Dict

import yaml

from agent.l4_archive import ARCHIVE_DB_PATH
from agent.l4_skill_drafts import SKILL_DRAFT_ROOT
from hermes_constants import get_hermes_home

HERMES_HOME = get_hermes_home().resolve()
WORKFLOW_DRAFTS = HERMES_HOME / "workflows" / "definitions"
WORKFLOW_OFFICIAL = HERMES_HOME / "workflows" / "official"
SKILLS_ROOT = HERMES_HOME / "skills"


def _read_yaml(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _frontmatter(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text.startswith("---\n"):
        return {}
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return {}
    data = yaml.safe_load(parts[0][4:]) or {}
    return data if isinstance(data, dict) else {}


def _workflow_stats() -> Dict[str, Any]:
    drafts = list(WORKFLOW_DRAFTS.glob("*.yaml"))
    official = list(WORKFLOW_OFFICIAL.glob("*.yaml"))
    states = Counter()
    for path in drafts:
        metadata = (_read_yaml(path).get("metadata") or {})
        states[str(metadata.get("lifecycle_state") or "unknown")] += 1
    return {"draft_count": len(drafts), "official_count": len(official), "states": dict(states)}


def _skill_stats() -> Dict[str, Any]:
    drafts = list(SKILL_DRAFT_ROOT.rglob("SKILL.md")) if SKILL_DRAFT_ROOT.exists() else []
    official = [p for p in SKILLS_ROOT.rglob("SKILL.md") if "l4-auto" not in str(p)]
    states = Counter()
    for path in drafts:
        fm = _frontmatter(path)
        states[str(fm.get("lifecycle_state") or "unknown")] += 1
    return {"draft_count": len(drafts), "official_count": len(official), "states": dict(states)}


def _l4_stats() -> Dict[str, Any]:
    if not ARCHIVE_DB_PATH.exists():
        return {"count": 0, "categories": {}, "projects": {}, "priority": {}}
    conn = sqlite3.connect(str(ARCHIVE_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(l4_archive)").fetchall()}
        total = conn.execute("SELECT COUNT(*) AS c FROM l4_archive").fetchone()["c"]
        categories = Counter()
        projects = Counter()
        priorities = Counter()
        if {"category", "project_tag", "priority"}.issubset(cols):
            rows = conn.execute("SELECT category, project_tag, priority FROM l4_archive").fetchall()
            for row in rows:
                categories[str(row["category"] or "general")] += 1
                projects[str(row["project_tag"] or "global")] += 1
                priorities[str(row["priority"] or 0)] += 1
        return {
            "count": int(total or 0),
            "categories": dict(categories),
            "projects": dict(projects),
            "priority": dict(priorities),
        }
    finally:
        conn.close()


def build_capabilities_dashboard() -> str:
    workflow = _workflow_stats()
    skill = _skill_stats()
    l4 = _l4_stats()
    lines = [
        "# Hermes Capabilities Dashboard",
        "",
        "## L4 Memory",
        f"- Total entries: {l4['count']}",
        f"- Categories: {json.dumps(l4['categories'], ensure_ascii=False)}",
        f"- Projects: {json.dumps(l4['projects'], ensure_ascii=False)}",
        f"- Priority: {json.dumps(l4['priority'], ensure_ascii=False)}",
        "",
        "## Workflows",
        f"- Drafts: {workflow['draft_count']}",
        f"- Official: {workflow['official_count']}",
        f"- Lifecycle states: {json.dumps(workflow['states'], ensure_ascii=False)}",
        "",
        "## Skills",
        f"- Drafts: {skill['draft_count']}",
        f"- Official: {skill['official_count']}",
        f"- Lifecycle states: {json.dumps(skill['states'], ensure_ascii=False)}",
    ]
    return "\n".join(lines)
