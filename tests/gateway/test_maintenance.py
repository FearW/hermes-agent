import asyncio

from gateway.maintenance import retention_loop


class _FakeStore:
    def __init__(self):
        self.calls = []

    def archive_and_prune_expired_transcripts(self, older_than_days=14):
        self.calls.append(older_than_days)
        raise asyncio.CancelledError()


def test_retention_loop_calls_store():
    store = _FakeStore()

    async def _run():
        try:
            await retention_loop(store, older_than_days=14, initial_delay=0, interval_seconds=3600)
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    assert store.calls == [14]


def test_l4_compaction_keeps_high_value_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    import agent.l4_archive as l4

    monkeypatch.setattr(l4, "ARCHIVE_DIR", tmp_path / ".hermes" / "memory_l4")
    monkeypatch.setattr(l4, "ARCHIVE_PATH", l4.ARCHIVE_DIR / "archive.json")
    monkeypatch.setattr(l4, "ARCHIVE_DB_PATH", l4.ARCHIVE_DIR / "archive.db")

    old_low = {
        "session_id": "old-low",
        "source": "test",
        "archived_at": "2000-01-01 00:00:00",
        "message_count": 5,
        "tool_names": [],
        "summary": "old low value",
        "user_intents": [],
        "task_type": "general",
        "category": "general",
        "priority": 1,
        "confidence": 0.5,
        "project_tag": "global",
        "project_root": "",
        "outcome": "",
        "unresolved": False,
        "important_files": [],
        "important_commands": [],
        "workflow_candidate": "",
        "skill_candidate": "",
        "linked_workflow": "",
        "linked_skill": "",
        "conflict_flag": False,
        "stale_flag": False,
        "superseded_by": "",
    }
    old_high = dict(old_low, session_id="old-high", priority=5, summary="important")

    conn = l4._connect()
    try:
        l4._insert_entry(conn, old_low)
        l4._insert_entry(conn, old_high)
        conn.commit()
    finally:
        conn.close()

    result = l4.compact_archive(max_entries=10, max_age_days=1, keep_priority_at_least=4)

    assert result["deleted"] == 1
    assert l4.is_session_archived("old-low") is False
    assert l4.is_session_archived("old-high") is True
