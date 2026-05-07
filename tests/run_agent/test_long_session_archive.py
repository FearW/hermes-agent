import pytest
from unittest.mock import MagicMock, patch

from run_agent import AIAgent


def _make_agent(platform="telegram"):
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            session_id=f"{platform}-long-session",
            platform=platform,
        )
    agent.client = MagicMock()
    return agent


@pytest.mark.skip("AIAgent._maybe_archive_long_session not yet implemented")
def test_maybe_archive_long_session_trims_messaging_history(monkeypatch):
    agent = _make_agent("telegram")
    archived = []

    def fake_summarize(session_id, source, messages):
        return {"summary": f"User intent: {messages[0]['content']}"}

    def fake_archive(session_id, source, messages):
        archived.append((session_id, source, list(messages)))

    monkeypatch.setattr("agent.l4_archive.summarize_messages", fake_summarize)
    monkeypatch.setattr("agent.l4_archive.archive_session_summary", fake_archive)
    agent._last_token_observation = {"history_tokens": 40_001}
    messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i}"}
        for i in range(210)
    ]

    result = agent._maybe_archive_long_session(messages)

    assert result is messages
    assert len(messages) == 64
    assert archived
    assert archived[0][0].startswith("telegram-long-session#prefix-")
    assert archived[0][1] == "telegram"
    assert len(archived[0][2]) == 146
    assert "Earlier in this session" in messages[0]["content"]
    assert agent._rewrite_session_db_on_next_flush is True
    assert agent._long_session_archived_this_turn is True


@pytest.mark.skip("AIAgent._maybe_archive_long_session not yet implemented")
def test_maybe_archive_long_session_skips_non_messaging():
    agent = _make_agent("cron")
    agent._last_token_observation = {"history_tokens": 80_000}
    messages = [{"role": "user", "content": str(i)} for i in range(250)]

    agent._maybe_archive_long_session(messages)

    assert len(messages) == 250
    assert agent._long_session_archived_this_turn is False


@pytest.mark.skip("AIAgent._maybe_archive_long_session not yet implemented")
def test_flush_rewrites_db_after_long_session_archive():
    agent = _make_agent("weixin")
    db = MagicMock()
    agent._session_db = db
    agent._rewrite_session_db_on_next_flush = True
    agent._last_flushed_db_idx = 250
    messages = [{"role": "user", "content": "breadcrumb"}]

    agent._flush_messages_to_session_db(messages)

    db.clear_messages.assert_called_once_with(agent.session_id)
    db.append_message.assert_called_once()
    assert agent._last_flushed_db_idx == 1
    assert agent._rewrite_session_db_on_next_flush is False
