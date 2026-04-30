"""Runtime helpers for Plan mode — config accessors + inbound-reply handling.

Separates the logic that can live outside ``gateway/run.py`` so the
run.py patch stays small.  Pure functions + :mod:`gateway.plan_store`
calls only; no messaging I/O.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from gateway.plan_store import (
    APPROVED, EXECUTED, FAILED, PENDING, REJECTED, TIMEOUT,
    PlanArtifact, get_store,
)
from gateway.plan_gate import (
    APPROVE, REJECT, UNCLEAR, classify_reply,
    build_executor_prompt_with_plan,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_SEC = 600.0  # 10 minutes

# Prefixes that trigger planner mode regardless of complexity — the user's
# explicit "please plan this" signal, modelled on Claude Code's plan mode.
PLAN_COMMAND_PREFIXES = ("/plan", "/规划", "/计划")


def plan_mode_config(user_config: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the ``plan_mode:`` block from config.yaml.

    Returns a dict with defaults filled in.  Missing or malformed config
    yields ``enabled=False`` so existing users are unaffected.  Plan mode
    is only ever entered via an explicit ``/plan`` command, so there is
    no auto-trigger configuration.
    """
    raw = user_config.get("plan_mode") if isinstance(user_config, dict) else None
    if not isinstance(raw, dict):
        raw = {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "timeout_sec": float(raw.get("timeout_sec", DEFAULT_TIMEOUT_SEC)),
        "model": raw.get("model") if isinstance(raw.get("model"), dict) else None,
    }


def plan_mode_enabled(user_config: Dict[str, Any]) -> bool:
    return plan_mode_config(user_config)["enabled"]


def plan_mode_model_override(
    user_config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Return plan-mode model override as runtime kwargs, or None.

    The ``plan_mode.model`` block mirrors the top-level ``model:`` schema
    (default/provider/base_url/api_mode/api_key_env/context_length).
    When absent or missing required fields, returns None so the planner
    falls back to the primary model.
    """
    m = plan_mode_config(user_config).get("model")
    if not isinstance(m, dict):
        return None
    name = m.get("default") or m.get("model")
    provider = m.get("provider")
    if not name or not provider:
        return None
    return {
        "model": name,
        "provider": provider,
        "base_url": m.get("base_url"),
        "api_mode": m.get("api_mode"),
        "api_key_env": m.get("api_key_env"),
        "context_length": m.get("context_length"),
    }


def parse_plan_command(message_text: str) -> Optional[str]:
    """If ``message_text`` starts with ``/plan`` (or Chinese alias), return
    the remainder (stripped).  Otherwise return ``None``.

    Matches prefixes case-insensitively followed by whitespace, end-of-string,
    or newline.  Bare ``/plan`` with no body returns empty string so the caller
    can ask the user what to plan.
    """
    if not message_text:
        return None
    t = message_text.lstrip()
    lower = t.lower()
    for pref in PLAN_COMMAND_PREFIXES:
        if lower.startswith(pref):
            tail = t[len(pref):]
            # Require whitespace/EOS after prefix so "/planner" doesn't match.
            if tail == "" or tail[0].isspace():
                return tail.strip()
    return None


# ---------------------------------------------------------------------------
# Inbound reply interception
# ---------------------------------------------------------------------------

# Stub result template — matches the shape ``_run_agent`` returns so the
# gateway's post-processing (media scan, send, logging) still works.
def _stub_result(final_response: str) -> Dict[str, Any]:
    return {
        "final_response": final_response,
        "messages": [],
        "api_calls": 0,
        "tools": [],
        "history_offset": 0,
        "last_prompt_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "model": "",
        "completed": True,
        "_plan_mode_synthetic": True,
    }


# Action constants returned by :func:`handle_inbound_reply`.
ACT_RUN_EXECUTOR = "run_executor"
ACT_RETURN_RESULT = "return_result"


def handle_inbound_reply(
    *,
    user_config: Dict[str, Any],
    session_key: str,
    message_text: str,
    timeout_sec: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Check if this inbound message is a reply to a pending plan.

    Returns ``None`` if there is no pending plan for ``session_key`` OR
    plan mode is disabled — caller should proceed normally.

    Otherwise returns a dict::

        {
          "action": "run_executor" | "return_result",
          "new_message": str,          # present when action == run_executor
          "result": dict,              # present when action == return_result
          "plan_id": str,
        }
    """
    if not plan_mode_enabled(user_config):
        return None

    store = get_store()

    # Expire stale pending plans first (cheap no-op if none).
    to = timeout_sec
    if to is None:
        to = plan_mode_config(user_config)["timeout_sec"]
    try:
        store.expire_stale(to)
    except Exception as e:
        logger.debug("plan_store.expire_stale failed: %s", e)

    pending = store.pending_for_session(session_key)
    if pending is None:
        return None

    verdict = classify_reply(message_text)
    if verdict == APPROVE:
        store.set_status(pending.plan_id, APPROVED, reason="user approved")
        new_msg = build_executor_prompt_with_plan(
            pending.user_message, pending.summary, pending.steps,
        )
        return {
            "action": ACT_RUN_EXECUTOR,
            "new_message": new_msg,
            "plan_id": pending.plan_id,
        }
    if verdict == REJECT:
        store.set_status(pending.plan_id, REJECTED, reason="user rejected")
        return {
            "action": ACT_RETURN_RESULT,
            "result": _stub_result("❌ Plan cancelled."),
            "plan_id": pending.plan_id,
        }
    # UNCLEAR — remind the user, leave plan pending.
    return {
        "action": ACT_RETURN_RESULT,
        "result": _stub_result(
            "⏳ There is a pending plan awaiting your decision.\n"
            "Reply ✅ to approve or ❌ to cancel."
        ),
        "plan_id": pending.plan_id,
    }


def mark_executor_outcome(plan_id: str, *, success: bool, reason: str = "") -> None:
    """Flip the plan's status after the executor finishes running."""
    if not plan_id:
        return
    try:
        get_store().set_status(
            plan_id,
            EXECUTED if success else FAILED,
            reason=reason or ("executed ok" if success else "executor error"),
        )
    except Exception as e:
        logger.debug("plan_store.set_status(%s) failed: %s", plan_id, e)


# ---------------------------------------------------------------------------
# Planner-phase helper used inside _run_agent
# ---------------------------------------------------------------------------

def record_new_plan(
    *,
    session_key: str,
    platform: str,
    chat_id: str,
    user_message: str,
    summary: str,
    steps,
    risk_flags,
) -> PlanArtifact:
    """Persist a freshly-produced plan and return it."""
    plan = PlanArtifact.new(
        session_key=session_key,
        platform=platform,
        chat_id=chat_id,
        user_message=user_message,
        summary=summary,
        steps=steps,
        risk_flags=risk_flags,
    )
    get_store().insert(plan)
    return plan
