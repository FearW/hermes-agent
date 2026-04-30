from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from hermes_constants import get_hermes_home

SKILL_DRAFT_ROOT = get_hermes_home() / "skills" / "l4-auto"


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return text or "skill-draft"


def skill_draft_path(name: str) -> Path:
    return SKILL_DRAFT_ROOT / _slugify(name) / "SKILL.md"


def _bullet_block(title: str, items: List[str]) -> List[str]:
    if not items:
        return []
    lines = ["", f"## {title}"]
    lines.extend(f"- {item}" for item in items if str(item).strip())
    return lines


def _frontmatter(entry: Dict[str, Any], name: str) -> str:
    category = str(entry.get("category") or "general")
    priority = int(entry.get("priority") or 0)
    confidence = float(entry.get("confidence") or 0.0)
    session_id = str(entry.get("session_id") or "")
    lifecycle_state = str(entry.get("skill_state") or "draft")
    return (
        "---\n"
        f"name: {name}\n"
        f"category: {category}\n"
        f"priority: {priority}\n"
        f"confidence: {confidence:.2f}\n"
        "auto_draft: true\n"
        f"lifecycle_state: {lifecycle_state}\n"
        f"source_session_id: {session_id}\n"
        "---\n\n"
    )


def render_skill_draft(entry: Dict[str, Any]) -> str:
    session_id = entry.get("session_id") or "unknown-session"
    summary = str(entry.get("summary") or "").strip()
    outcome = str(entry.get("outcome") or "").strip()
    files = [str(item) for item in (entry.get("important_files") or [])]
    commands = [str(item) for item in (entry.get("important_commands") or [])]
    user_intents = [str(item) for item in (entry.get("user_intents") or [])]
    task_type = str(entry.get("task_type") or "general")
    unresolved = bool(entry.get("unresolved"))
    tool_names = [str(item) for item in (entry.get("tool_names") or [])]
    category = str(entry.get("category") or "general")
    priority = int(entry.get("priority") or 0)
    confidence = float(entry.get("confidence") or 0.0)

    title = f"l4-{session_id[-8:]}"
    lines: List[str] = [
        f"# {title}",
        "",
        "## Purpose",
        f"Reusable auto-drafted skill derived from L4 archival memory for a `{task_type}` pattern in category `{category}`.",
        "",
        f"Priority: {priority}/5  |  Confidence: {confidence:.2f}",
        "",
        "## When To Use",
        f"Use this skill when the task resembles the archived session `{session_id}` or matches the same troubleshooting / execution pattern.",
        "",
        "## Core Pattern",
        summary or "No summary available.",
    ]

    if user_intents:
        lines += _bullet_block("Typical User Requests", user_intents)
    if outcome:
        lines += ["", "## Expected Outcome", outcome]
    if files:
        lines += _bullet_block("Important Files", files)
    if commands:
        lines += ["", "## Useful Commands"]
        lines.extend(f"- `{item}`" for item in commands)
    if tool_names:
        lines += _bullet_block("Likely Tools", tool_names)

    lines += [
        "",
        "## Recommended Workflow",
        "1. Re-read the user request and map it to the archived pattern.",
        "2. Inspect the important files before making changes.",
        "3. Reuse or adapt the useful commands instead of improvising from scratch.",
        "4. Validate the final result and summarize what changed.",
    ]

    if unresolved:
        lines += [
            "",
            "## Open Questions",
            "- This archived pattern was marked as potentially unresolved. Re-check assumptions before finalizing.",
        ]

    lines += [
        "",
        "## Maintenance Notes",
        "- This is an auto-generated draft. Refine naming, commands, and edge cases before relying on it as a polished skill.",
        "- If this skill gets reused successfully multiple times, promote it out of `l4-auto` into a permanent category.",
    ]
    return _frontmatter(entry, title) + "\n".join(lines) + "\n"


def create_skill_draft_from_l4(entry: Dict[str, Any]) -> str:
    session_id = str(entry.get("session_id") or "")
    name = f"l4-{session_id[-8:]}"
    path = skill_draft_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_skill_draft(entry), encoding="utf-8")
    return name
