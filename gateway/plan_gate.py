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

_PLANNER_SYSTEM = (
    "\u4f60\u662f Hermes \u7684\u8f7b\u91cf\u89c4\u5212\u5668\u3002\u8bf7\u628a\u7528\u6237\u8bf7\u6c42\u62c6\u6210\u5b89\u5168\u3001\u53ef\u6267\u884c\u3001\u53ef\u9a8c\u8bc1\u7684\u4e2d\u6587\u8ba1\u5212\u3002\n\n"
    "\u89c4\u5219\uff1a\n"
    "- \u9ed8\u8ba4\u8f93\u51fa\u4e2d\u6587\uff1b\u53ea\u6709\u7528\u6237\u8bf7\u6c42\u660e\u786e\u5168\u82f1\u6587\u65f6\u624d\u53ef\u4ee5\u8f93\u51fa\u82f1\u6587\u3002\n"
    "- \u6700\u591a 5 \u6b65\uff0c\u907f\u514d\u5197\u957f\u89e3\u91ca\uff0c\u53ea\u8fd4\u56de JSON\u3002\n"
    "- \u4e0d\u8981\u6267\u884c\u4efb\u52a1\uff0c\u4e0d\u8981\u8c03\u7528 shell\uff0c\u4e0d\u8981\u8bbf\u95ee\u5916\u90e8 API\uff0c\u53ea\u505a\u89c4\u5212\u3002\n"
    "- \u4e0d\u8981\u8f93\u51fa\u65e5\u8bed\u3001\u5fb7\u8bed\u3001\u6cd5\u8bed\u3001\u897f\u73ed\u7259\u8bed\u6216\u5176\u4ed6\u8bed\u8a00\u3002\n"
    "- \u5982\u679c\u4efb\u52a1\u5f88\u7b80\u5355\uff0c\u4e5f\u8981\u7ed9\u51fa\u6700\u5c0f\u8ba1\u5212\u3002\n\n"
    "\u53ea\u8fd4\u56de JSON\uff0c\u683c\u5f0f\u5fc5\u987b\u5b8c\u5168\u5982\u4e0b\uff1a\n\n"
    "{\n"
    "  \"summary\": \"\u7b80\u8981\u6982\u62ec\u4efb\u52a1\",\n"
    "  \"steps\": [\"\u6b65\u9aa4 1\", \"\u6b65\u9aa4 2\", \"...\"],\n"
    "  \"risk_flags\": [\"\u9700\u8981\u6ce8\u610f\u7684\u98ce\u9669\", \"...\"]\n"
    "}\n\n"
    "\u8981\u6c42\uff1a\n"
    "- steps \u5fc5\u987b\u662f\u9762\u5411\u843d\u5730\u7684\u77ed\u53e5\uff0c\u5efa\u8bae 2-6 \u4e2a\u6c49\u5b57\u8d77\u6b65\u7684\u52a8\u4f5c\u3002\n"
    "- risk_flags \u53ea\u5199\u771f\u6b63\u9700\u8981\u6ce8\u610f\u7684\u4e8b\uff0c\u4f8b\u5982 shell \u5199\u5165\u3001\u5220\u9664\u3001\u8bbf\u95ee API \u6216\u53ef\u80fd\u7834\u574f\u7528\u6237\u9b54\u6539\u7684\u64cd\u4f5c\u3002\n"
    "- JSON \u4e4b\u5916\u4e0d\u8981\u8f93\u51fa\u4efb\u4f55 Markdown \u6216\u89e3\u91ca\u3002\n\n"
    "\u73b0\u5728\u8bf7\u6839\u636e\u7528\u6237\u8bf7\u6c42\u8f93\u51fa\u8ba1\u5212 JSON\u3002"
)

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
