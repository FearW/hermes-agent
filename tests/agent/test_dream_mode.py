from agent.dream_mode import dream_status, load_dream_state, run_dream_cycle


def test_dream_status_uses_sleep_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    status = dream_status({"sleep_mode": {"profile": "deep"}})

    assert status["enabled"] is True
    assert status["profile"] == "deep"
    assert "l4_archive_compaction" in status["actions"]


def test_dream_cycle_disabled_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    result = run_dream_cycle({"sleep_mode": {"enabled": False}}, reason="test")

    assert result["ok"] is False
    assert result["skipped"] == "sleep mode is disabled"
    state = load_dream_state()
    assert state["runs"] == 1
    assert state["last_result"]["reason"] == "test"
