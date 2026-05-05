from unittest.mock import patch

from agent.dream_mode import dream_status, load_dream_state, run_dream_cycle


def test_dream_status_uses_sleep_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    status = dream_status({"sleep_mode": {"profile": "deep"}})

    assert status["enabled"] is True
    assert status["profile"] == "deep"
    assert "l4_archive_compaction" in status["actions"]
    # New: task_states is exposed even when empty (no prior runs).
    assert "task_states" in status


def test_dream_cycle_disabled_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    result = run_dream_cycle({"sleep_mode": {"enabled": False}}, reason="test")

    assert result["ok"] is False
    assert result["skipped"] == "sleep mode is disabled"
    state = load_dream_state()
    assert state["runs"] == 1
    assert state["last_result"]["reason"] == "test"


def test_dream_cycle_off_profile_is_still_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    result = run_dream_cycle({"sleep_mode": {"profile": "off", "enabled": True}}, reason="test")

    assert result["ok"] is False
    assert result["skipped"] == "sleep mode is disabled"


def test_dream_cycle_delegates_to_scheduler(tmp_path, monkeypatch):
    """When enabled, run_dream_cycle should invoke scheduler tasks and return
    a normalised summary including per-task ``changed`` counters."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    # Stub out the three dream-tagged actions so the test has no external
    # side-effects (FS compaction, capability yaml rewrites, etc.).
    with patch("tools.memory_tool.compact_builtin_memory", return_value={"memory_removed": 1}), \
         patch("agent.capability_lifecycle.run_lifecycle_maintenance", return_value={"promoted": 0, "demoted": 0, "pruned": 0}), \
         patch("agent.l4_archive.compact_archive", return_value={"deleted": 2, "remaining": 4}):
        result = run_dream_cycle({"sleep_mode": {"profile": "balanced"}}, reason="unit")

    assert result["ok"] is True
    assert "actions" in result
    action_names = set(result["actions"].keys())
    assert {"builtin_memory", "capability_lifecycle", "l4_compaction"} <= action_names
    # The wrapper should surface normalized "changed" counts.
    assert result["actions"]["builtin_memory"]["changed"] >= 1
    assert result["actions"]["l4_compaction"]["changed"] >= 2
    # Scheduler persists per-task state alongside the summary.
    state = load_dream_state()
    assert "tasks" in state
    assert "builtin_memory" in state["tasks"]
