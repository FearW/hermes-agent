from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Any

import yaml

from agent.l4_archive import _connect
from agent.l4_skill_drafts import skill_draft_path
from hermes_constants import get_hermes_home

WORKFLOW_DIR = get_hermes_home() / "workflows" / "definitions"
WORKFLOW_OFFICIAL_DIR = get_hermes_home() / "workflows" / "official"
SKILLS_ROOT = get_hermes_home() / "skills"

PROMOTE_MATCH_THRESHOLD = 5
DEMOTE_MATCH_THRESHOLD = 1
PRUNE_MATCH_THRESHOLD = 0


def _workflow_paths():
    return sorted(WORKFLOW_DIR.glob("*.yaml"))


def _parse_skill_frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return {}, text
    fm = yaml.safe_load(parts[0][4:]) or {}
    return fm if isinstance(fm, dict) else {}, parts[1]


def _write_skill_frontmatter(path: Path, metadata: dict, body: str) -> None:
    payload = "---\n" + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=False) + "---\n\n" + body.lstrip("\n")
    path.write_text(payload, encoding="utf-8")


def _classify_workflow_state(metadata: dict) -> str:
    matches = int(metadata.get("auto_match_count", 0) or 0)
    recommended = bool(metadata.get("recommended"))
    if recommended or matches >= PROMOTE_MATCH_THRESHOLD:
        return "promoted"
    if matches <= PRUNE_MATCH_THRESHOLD:
        return "candidate"
    if matches <= DEMOTE_MATCH_THRESHOLD:
        return "demoted"
    return "recommended"


def _classify_skill_state(entry: dict) -> str:
    priority = int(entry.get("priority", 0) or 0)
    confidence = float(entry.get("confidence", 0.0) or 0.0)
    if entry.get("stale_flag") or entry.get("conflict_flag"):
        return "demoted"
    if priority >= 4 and confidence >= 0.7:
        return "promoted"
    if priority >= 2:
        return "recommended"
    return "candidate"


def _promote_workflow_asset(path: Path, data: dict) -> None:
    WORKFLOW_OFFICIAL_DIR.mkdir(parents=True, exist_ok=True)
    target = WORKFLOW_OFFICIAL_DIR / path.name
    data.setdefault("metadata", {})["promoted_from"] = str(path)
    target.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=False), encoding="utf-8")


def _promote_skill_asset(path: Path, metadata: dict, body: str) -> Path:
    category = metadata.get("category") or "general"
    name = metadata.get("name") or path.parent.name
    target = SKILLS_ROOT / category / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata["promoted_from"] = str(path)
    _write_skill_frontmatter(target, metadata, body)
    return target


def run_lifecycle_maintenance() -> Dict[str, int]:
    promoted = demoted = pruned = 0

    for path in _workflow_paths():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        metadata = data.setdefault("metadata", {})
        if not metadata.get("auto_draft"):
            continue
        state = _classify_workflow_state(metadata)
        metadata["lifecycle_state"] = state
        if state == "promoted":
            metadata["recommended"] = True
            _promote_workflow_asset(path, data)
            promoted += 1
        elif state == "demoted":
            metadata["recommended"] = False
            demoted += 1
        elif state == "candidate" and int(metadata.get("auto_match_count", 0) or 0) == 0:
            metadata["prunable"] = True
            pruned += 1
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=False), encoding="utf-8")

    conn = _connect()
    try:
        rows = conn.execute("SELECT session_id, linked_skill, category, priority, confidence, stale_flag, conflict_flag FROM l4_archive WHERE linked_skill != ''").fetchall()
        for row in rows:
            entry = dict(row)
            state = _classify_skill_state(entry)
            skill_name = entry["linked_skill"]
            path = skill_draft_path(skill_name)
            if not path.exists():
                continue
            metadata, body = _parse_skill_frontmatter(path)
            metadata["lifecycle_state"] = state
            if state == "promoted":
                metadata["recommended"] = True
                _promote_skill_asset(path, metadata, body)
                promoted += 1
            elif state == "demoted":
                metadata["recommended"] = False
                demoted += 1
            _write_skill_frontmatter(path, metadata, body)

            conn.execute(
                "UPDATE l4_archive SET superseded_by = CASE WHEN ? = 'demoted' AND superseded_by = '' THEN linked_skill ELSE superseded_by END WHERE session_id = ?",
                (state, entry["session_id"]),
            )
        conn.commit()
    finally:
        conn.close()

    return {"promoted": promoted, "demoted": demoted, "pruned": pruned}
