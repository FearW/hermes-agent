import time

from gateway.config import GatewayConfig
from gateway.session import SessionStore
from hermes_state import SessionDB
from agent import l4_archive


def test_l4_archive_writes_summary(tmp_path, monkeypatch):
    archive_dir = tmp_path / "memory_l4"
    archive_json = archive_dir / "archive.json"
    archive_db = archive_dir / "archive.db"
    monkeypatch.setattr(l4_archive, "ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(l4_archive, "ARCHIVE_PATH", archive_json)
    monkeypatch.setattr(l4_archive, "ARCHIVE_DB_PATH", archive_db)

    l4_archive.archive_session_summary(
        "sess-1",
        "telegram",
        [
            {"role": "user", "content": "Summarize /tmp/a.txt"},
            {"role": "assistant", "content": "Done"},
        ],
    )
    data = l4_archive.search_archive("summarize a.txt")
    assert data[0]["session_id"] == "sess-1"
    assert archive_db.exists()


def test_session_store_disables_legacy_jsonl_writes(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    cfg = GatewayConfig()
    store = SessionStore(sessions_dir, cfg)
    store.append_to_transcript("sess-jsonl-off", {"role": "user", "content": "hello"}, skip_db=True)
    assert not (sessions_dir / "sess-jsonl-off.jsonl").exists()


def test_archive_and_prune_expired_transcripts(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    cfg = GatewayConfig()
    store = SessionStore(sessions_dir, cfg)
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path)
    store._db = db

    archive_dir = tmp_path / "memory_l4"
    archive_json = archive_dir / "archive.json"
    archive_db = archive_dir / "archive.db"
    monkeypatch.setattr(l4_archive, "ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(l4_archive, "ARCHIVE_PATH", archive_json)
    monkeypatch.setattr(l4_archive, "ARCHIVE_DB_PATH", archive_db)

    sid = "old-session"
    db.create_session(session_id=sid, source="telegram", user_id="u1")
    db.append_message(sid, role="user", content="Watch /root/inbox/*.txt")
    db.append_message(sid, role="assistant", content="ok")
    db.end_session(sid, "done")
    with db._lock:
        db._conn.execute(
            "UPDATE sessions SET started_at = ?, ended_at = ? WHERE id = ?",
            (time.time() - 40 * 86400, time.time() - 39 * 86400, sid),
        )
        db._conn.commit()

    legacy = sessions_dir / f"{sid}.jsonl"
    legacy.write_text("{\"role\":\"user\",\"content\":\"legacy\"}\n", encoding="utf-8")

    pruned = store.archive_and_prune_expired_transcripts(older_than_days=14)
    assert pruned == 1
    assert not legacy.exists()
    assert db.get_session(sid) is None
    archive_data = l4_archive.search_archive("watch inbox txt")
    assert archive_data[0]["session_id"] == sid
