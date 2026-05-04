from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from gateway.config import GatewayConfig
from gateway.session import SessionEntry, SessionStore


def test_update_session_throttles_index_writes(tmp_path):
    store = SessionStore(tmp_path / "sessions", GatewayConfig())
    store._index_save_min_interval_secs = 3600.0

    with store._lock:
        store._loaded = True
        store._entries["k1"] = SessionEntry(
            session_key="k1",
            session_id="s1",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

    with patch.object(store, "_save", wraps=store._save) as save_spy:
        store.update_session("k1")
        store.update_session("k1")

    assert save_spy.call_count == 1


def test_model_signature_uses_mtime_cache(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    config_path = hermes_home / "config.yaml"
    config_path.write_text("model: openrouter/test-model\n", encoding="utf-8")

    store = SessionStore(tmp_path / "sessions", GatewayConfig())

    with patch("gateway.session.open", wraps=open) as open_spy:
        sig1 = store._build_model_signature()
        sig2 = store._build_model_signature()

    assert sig1 == sig2
    config_open_calls = [
        c for c in open_spy.call_args_list
        if c.args and Path(c.args[0]) == config_path
    ]
    assert len(config_open_calls) == 1


def test_pending_index_save_flushed_when_due(tmp_path):
    store = SessionStore(tmp_path / "sessions", GatewayConfig())
    store._index_save_min_interval_secs = 0.0

    with store._lock:
        store._loaded = True
        store._entries["k1"] = SessionEntry(
            session_key="k1",
            session_id="s1",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

    store.update_session("k1")
    store._index_save_min_interval_secs = 3600.0
    store.update_session("k1")
    assert store._index_save_pending is True

    store._index_save_min_interval_secs = 0.0
    _ = store.list_sessions()
    assert store._index_save_pending is False

