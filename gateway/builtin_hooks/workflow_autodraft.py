"""Built-in workflow autodraft hook.

When a session ends, inspect the transcript and save a draft workflow for
obviously reusable task patterns. This is intentionally conservative and
never interrupts the user flow.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("hooks.workflow-autodraft")


def _should_capture(messages: list[dict]) -> bool:
    user_messages = [str(m.get("content") or "").strip() for m in messages if isinstance(m, dict) and m.get("role") == "user"]
    user_messages = [m for m in user_messages if m]
    if len(user_messages) < 2:
        return False
    text = "\n".join(user_messages).lower()
    signals = ["/", "watch ", "save ", "write ", "export ", "summarize", "report", "daily", "every ", "*."]
    return sum(1 for token in signals if token in text) >= 2


async def handle(event_type: str, context: dict) -> None:
    if event_type != "session:end":
        return

    session_id = str(context.get("session_id") or "").strip()
    if not session_id:
        return

    try:
        from gateway.session import SessionStore
        from gateway.config import load_gateway_config
        from hermes_constants import get_hermes_home
        from hermes_cli import workflow as workflow_mod

        store = SessionStore(get_hermes_home() / "sessions", load_gateway_config())
        messages = store.load_transcript(session_id)
        if not _should_capture(messages):
            return

        workflow_name = f"auto-{session_id[-8:]}"
        path = workflow_mod._workflow_path(workflow_name)
        if path.exists():
            return

        draft = workflow_mod._build_workflow_from_session(workflow_name, session_id)
        draft.setdefault("metadata", {})
        draft["metadata"]["auto_draft"] = True
        draft["metadata"]["auto_draft_source"] = "session:end"
        workflow_mod._save_workflow(workflow_name, draft)
        try:
            from agent.l4_archive import link_workflow_candidate
            link_workflow_candidate(session_id, workflow_name)
        except Exception:
            pass
        logger.info("workflow-autodraft saved %s from session %s", workflow_name, session_id)
    except Exception as exc:
        logger.debug("workflow-autodraft skipped for session %s: %s", session_id, exc)
