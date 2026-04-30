"""Plan-mode orchestration: planner prompt, reply classification, gate logic.

Hermes' Plan mode aligns with Claude Code's Opus Plan mode:

1. For turns classified as COMPLEX, a *planner* agent runs with **only
   read-only tools** (see :mod:`tools.plan_mode`).
2. The planner emits a structured :class:`PlanArtifact` (JSON, parsed
   tolerantly).
3. The artifact is pushed to the originating messaging platform and
   persisted in :mod:`gateway.plan_store`.
4. Execution of any destructive tool is **gated** until the user replies
   ✅ (approve) or ❌ (reject).

This module is pure logic — no gateway wiring, no messaging send calls.
``gateway/run.py`` integrates the pieces.
"""
from __future__ import annotations

import json
import logging
import re
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reply classification
# ---------------------------------------------------------------------------

APPROVE_TOKENS = {
    "✅", "approve", "approved", "yes", "y", "ok", "okay", "go", "ship",
    "确认", "同意", "批准", "好", "好的", "行", "可以", "通过", "继续",
}

REJECT_TOKENS = {
    "❌", "reject", "rejected", "no", "n", "cancel", "cancelled", "stop", "abort",
    "取消", "否", "不", "不要", "拒绝", "作废", "算了",
}

APPROVE = "approve"
REJECT = "reject"
UNCLEAR = "unclear"


def classify_reply(text: str) -> str:
    """Return one of ``approve``, ``reject``, ``unclear``.

    Matching is case-insensitive and substring-tolerant — short replies
    like ``"yes"`` or ``"取消"`` work; longer sentences are still matched
    if a keyword is present as a whole word.  If both an approve and a
    reject token are found, returns ``unclear`` (safer to re-ask).
    """
    if not text:
        return UNCLEAR
    t = text.strip().lower()
    if not t:
        return UNCLEAR

    # Fast path: exact match to a single emoji/word.
    if t in APPROVE_TOKENS:
        return APPROVE
    if t in REJECT_TOKENS:
        return REJECT

    # Slower path: word-boundary scan.  Use both ASCII and CJK scans since
    # CJK keywords have no word boundary.
    has_approve = any(tok in t for tok in ("✅",) )
    has_reject = any(tok in t for tok in ("❌",) )

    # Word-boundary match for ASCII tokens
    for tok in APPROVE_TOKENS:
        if tok.isascii() and re.search(rf"\b{re.escape(tok)}\b", t):
            has_approve = True
            break
    for tok in REJECT_TOKENS:
        if tok.isascii() and re.search(rf"\b{re.escape(tok)}\b", t):
            has_reject = True
            break

    # Substring match for CJK tokens (no word boundaries)
    if not has_approve:
        has_approve = any(tok in text for tok in APPROVE_TOKENS if not tok.isascii())
    if not has_reject:
        has_reject = any(tok in text for tok in REJECT_TOKENS if not tok.isascii())

    if has_approve and not has_reject:
        return APPROVE
    if has_reject and not has_approve:
        return REJECT
    return UNCLEAR


# ---------------------------------------------------------------------------
# Planner prompt + output parsing
# ---------------------------------------------------------------------------

_PLANNER_SYSTEM = """You are the PLANNER for Hermes. Your job is to read the user's request, \
optionally do ONE brief investigation with a read-only tool, and emit a \
concrete execution plan for the executor model to carry out.

HARD LIMITS:
- You may call AT MOST 1 read-only tool. Often ZERO is fine — trust your
  knowledge of the codebase and plan from intuition when the request is
  clear.
- You have no more than 5 total turns before the runtime aborts. After one
  investigation, STOP tool-calling and produce JSON immediately.
- You must NOT call any destructive tool (write, shell, send_message,
  etc.); those are not available to you. Do not answer the user directly.

Output EXACTLY one JSON object, nothing else, with this shape:

{
  "summary": "one-sentence description of what we will do",
  "steps": ["step 1", "step 2", "..."],
  "risk_flags": ["short human-readable risk tag", "..."]
}

Rules:
- ``steps`` MUST be an ordered list of concrete, verifiable actions the
  executor will take. Prefer 2–6 steps. Do NOT number the steps inside the
  string — the ordering is implicit from list position.
- ``risk_flags`` lists anything that could surprise the user: writes to
  filesystem, shell execution, outgoing messages, external API mutations,
  irreversible actions, cost. Empty list is fine if none apply.
- No prose before or after the JSON. No markdown fences.

If you catch yourself about to call a second tool, STOP and emit JSON.
"""


def build_planner_system_prompt() -> str:
    """System prompt injected into the planner agent."""
    return _PLANNER_SYSTEM


def build_planner_user_prompt(user_message: str) -> str:
    """Wrap the raw user request for the planner."""
    return (
        "USER REQUEST:\n"
        f"{user_message}\n\n"
        "Investigate briefly (if needed) and emit the JSON plan."
    )


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_planner_output(raw_text: str) -> Optional[Tuple[str, List[str], List[str]]]:
    """Parse planner output into ``(summary, steps, risk_flags)``.

    Returns ``None`` if parsing fails.  Tolerant of markdown fences and
    leading/trailing prose (models sometimes disobey the JSON-only rule).
    """
    if not raw_text:
        return None

    text = raw_text.strip()
    # Strip common markdown fence wrappers.
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        obj = json.loads(text)
    except Exception:
        # Fall back to first {...} match.
        m = _JSON_OBJECT_RE.search(text)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return None

    if not isinstance(obj, dict):
        return None
    summary = str(obj.get("summary") or "").strip()
    steps_raw = obj.get("steps") or []
    risks_raw = obj.get("risk_flags") or []
    if not isinstance(steps_raw, list) or not isinstance(risks_raw, list):
        return None
    steps = [str(s).strip() for s in steps_raw if str(s).strip()]
    risks = [str(r).strip() for r in risks_raw if str(r).strip()]
    if not summary and not steps:
        return None
    return summary, steps, risks


# ---------------------------------------------------------------------------
# Post-plan executor prompt
# ---------------------------------------------------------------------------

def build_executor_prompt_with_plan(
    user_message: str, summary: str, steps: List[str]
) -> str:
    """Prompt fragment the executor sees after user approval."""
    plan_lines = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    return (
        "The user has APPROVED the following plan. Execute it step by step, "
        "adapting only when a step becomes impossible; announce deviations.\n\n"
        f"PLAN SUMMARY: {summary}\n\n"
        "STEPS:\n"
        f"{plan_lines}\n\n"
        "ORIGINAL USER REQUEST:\n"
        f"{user_message}"
    )
