from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hooks.l4-autoarchive")


async def _llm_summarize(session_id: str, messages: List[Dict[str, Any]]) -> Optional[str]:
    """Generate LLM summary. Returns None if unavailable."""
    try:
        from hermes_cli.config import load_config
        config = load_config()
        model_cfg = config.get("model", {})
        base_url = model_cfg.get("base_url", "")
        api_key_env = model_cfg.get("api_key_env", "OPENAI_API_KEY")
        model_name = model_cfg.get("default", "")
        if not base_url or not model_name:
            return None
        import os
        api_key = os.environ.get(api_key_env) or os.environ.get("OPENAI_API_KEY") or ""
        if not api_key:
            return None
        from openai import AsyncOpenAI
        openai_client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        lines = []
        char_budget = 4000
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "")
            content = str(msg.get("content") or "").strip()
            if not content or role not in ("user", "assistant"):
                continue
            snippet = content[:300] + ("..." if len(content) > 300 else "")
            line = f"[{role.upper()}] {snippet}"
            if sum(len(l) for l in lines) + len(line) > char_budget:
                break
            lines.append(line)
        if not lines:
            return None
        transcript_text = "\n".join(lines)
        prompt = (
            "You are a memory archivist. Summarize this conversation in 3-5 sentences.\n"
            "Cover: what the user wanted, what was done, key decisions/findings, outcome, unresolved items.\n"
            "Be factual and concise. Use the same language as the conversation.\n\n"
            f"CONVERSATION:\n{transcript_text}\n\nSUMMARY:"
        )
        resp = await asyncio.wait_for(
            openai_client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.3,
            ),
            timeout=15.0,
        )
        summary = resp.choices[0].message.content.strip() if resp.choices else ""
        return summary or None
    except Exception as exc:
        logger.debug("LLM summarize failed for session %s: %s", session_id, exc)
        return None


async def handle(event_type: str, context: dict) -> None:
    if event_type != "session:end":
        return

    session_id = str(context.get("session_id") or "").strip()
    session_key = str(context.get("session_key") or "").strip()

    if not session_id and not session_key:
        logger.debug("l4-autoarchive: no session_id or session_key in context")
        return

    try:
        from gateway.session import SessionStore
        from gateway.config import load_gateway_config
        from hermes_constants import get_hermes_home
        from agent.l4_archive import (
            is_session_archived, summarize_messages, _connect,
            _insert_entry, _migrate_legacy_json_if_needed, search_archive,
        )

        store = SessionStore(get_hermes_home() / "sessions", load_gateway_config())

        # Resolve session_id from session_key if needed
        if not session_id and session_key:
            for entry in store.list_sessions():
                eid = getattr(entry, "session_id", "")
                ekey = getattr(entry, "session_key", "")
                if eid == session_key or ekey == session_key:
                    session_id = eid
                    break
            if not session_id:
                logger.debug("l4-autoarchive: cannot resolve session_id from key %s", session_key)
                return

        if is_session_archived(session_id):
            logger.debug("l4-autoarchive: session %s already archived", session_id)
            return

        messages = store.load_transcript(session_id)
        if not messages or len(messages) < 3:
            logger.debug(
                "l4-autoarchive: session %s too short (%d msgs), skipping",
                session_id, len(messages) if messages else 0,
            )
            return

        source = str(context.get("platform") or "unknown")
        entry = summarize_messages(session_id, source, messages)

        llm_summary = await _llm_summarize(session_id, messages)
        if llm_summary:
            entry["summary"] = llm_summary
            entry["confidence"] = min(entry.get("confidence", 0.5) + 0.2, 1.0)

        conn = _connect()
        try:
            _migrate_legacy_json_if_needed(conn)
            _insert_entry(conn, entry)
            conn.commit()
        finally:
            conn.close()

        logger.info(
            "l4-autoarchive: archived session %s [%s] priority=%d msgs=%d llm=%s",
            session_id, source, entry.get("priority", 0),
            len(messages), "yes" if llm_summary else "no",
        )

        try:
            hits = search_archive(session_id, limit=1)
            if hits and hits[0].get("skill_candidate") and not hits[0].get("linked_skill"):
                from agent.l4_skill_drafts import create_skill_draft_from_l4
                skill_name = create_skill_draft_from_l4(hits[0])
                if skill_name:
                    conn2 = _connect()
                    try:
                        conn2.execute(
                            "UPDATE l4_archive SET linked_skill = ?, skill_candidate = ? WHERE session_id = ?",
                            (skill_name, skill_name, session_id),
                        )
                        conn2.commit()
                    finally:
                        conn2.close()
        except Exception as exc:
            logger.debug("l4-autoarchive skill draft failed: %s", exc)

    except Exception as exc:
        logger.warning("l4-autoarchive failed for session %s: %s", session_id or session_key, exc)
