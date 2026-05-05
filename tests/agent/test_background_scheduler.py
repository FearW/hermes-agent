"""Tests for the unified BackgroundScheduler."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent.background_scheduler import (
    AlwaysReadyProbe,
    BackgroundScheduler,
    BackgroundTask,
    IdleProbe,
    build_default_tasks,
    build_scheduler,
)


def _make_task(
    name: str,
    *,
    changed: int = 0,
    tags: tuple[str, ...] = ("test",),
    base_cadence_s: int = 60,
    idle_required_s: int = 0,
    side_effect: Exception | None = None,
) -> tuple[BackgroundTask, dict]:
    """Helper: build a task whose action is scripted by the returned counter."""
    counter = {"calls": 0, "changed": changed, "last_result": None}

    def _action():
        counter["calls"] += 1
        if side_effect is not None:
            raise side_effect
        result = {"changed": counter["changed"], "name": name}
        counter["last_result"] = result
        return result

    task = BackgroundTask(
        name=name,
        action=_action,
        base_cadence_s=base_cadence_s,
        idle_required_s=idle_required_s,
        tags=tags,
    )
    return task, counter


def _scheduler(tmp_path: Path, tasks: list[BackgroundTask]) -> BackgroundScheduler:
    return BackgroundScheduler(
        sleep_cfg={"enabled": True, "profile": "balanced"},
        tasks=tasks,
        state_path=tmp_path / "dream_state.json",
    )


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------


def test_run_once_executes_tasks_in_order(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    order: list[str] = []

    def _make(name: str):
        def _action():
            order.append(name)
            return {"changed": 1}

        return BackgroundTask(name=name, action=_action, base_cadence_s=60, tags=("test",))

    sched = _scheduler(tmp_path, [_make("a"), _make("b"), _make("c")])
    summary = sched.run_once()

    assert order == ["a", "b", "c"]
    assert summary["ok"] is True
    assert set(summary["actions"].keys()) == {"a", "b", "c"}
    assert summary["task_states"]["a"]["last_changed"] == 1


def test_run_once_filters_by_tag(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    memory_task, memory_counter = _make_task("memory", changed=1, tags=("memory",))
    l4_task, l4_counter = _make_task("l4", changed=2, tags=("l4",))

    sched = _scheduler(tmp_path, [memory_task, l4_task])
    summary = sched.run_once(tags=("memory",))

    assert memory_counter["calls"] == 1
    assert l4_counter["calls"] == 0
    assert set(summary["actions"].keys()) == {"memory"}


def test_run_once_filters_by_task_name(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    a_task, a_counter = _make_task("alpha", changed=1)
    b_task, b_counter = _make_task("beta", changed=1)

    sched = _scheduler(tmp_path, [a_task, b_task])
    summary = sched.run_once(task_names=("beta",))

    assert a_counter["calls"] == 0
    assert b_counter["calls"] == 1
    assert set(summary["actions"].keys()) == {"beta"}


def test_run_once_isolates_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    boom_task, _ = _make_task("boom", side_effect=RuntimeError("kaboom"))
    ok_task, ok_counter = _make_task("ok", changed=1)

    sched = _scheduler(tmp_path, [boom_task, ok_task])
    summary = sched.run_once()

    assert summary["ok"] is False
    assert any("boom" in err for err in summary["errors"])
    assert ok_counter["calls"] == 1  # later task still ran


# ---------------------------------------------------------------------------
# Backoff behaviour
# ---------------------------------------------------------------------------


def test_backoff_doubles_cadence_after_empty_streak(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task, _ = _make_task("idle", changed=0, base_cadence_s=100)
    sched = _scheduler(tmp_path, [task])

    for _ in range(3):
        sched.run_once()

    state = sched.task_states()["idle"]
    # After 3 empty cycles backoff engages (doubles at least once).
    assert state["current_cadence_s"] >= 200
    assert state["consecutive_empty"] >= 3


def test_backoff_caps_at_four_times_base(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task, _ = _make_task("idle", changed=0, base_cadence_s=100)
    sched = _scheduler(tmp_path, [task])

    for _ in range(10):
        sched.run_once()

    state = sched.task_states()["idle"]
    assert state["current_cadence_s"] <= 400


def test_backoff_resets_when_work_appears(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    counter = {"calls": 0}

    def _action():
        counter["calls"] += 1
        # Empty for first 3 cycles, then productive.
        return {"changed": 0 if counter["calls"] <= 3 else 5}

    task = BackgroundTask(
        name="flip", action=_action, base_cadence_s=100, tags=("test",)
    )
    sched = _scheduler(tmp_path, [task])

    for _ in range(3):
        sched.run_once()
    mid = sched.task_states()["flip"]["current_cadence_s"]
    assert mid > 100  # doubled at least once

    sched.run_once()  # this returns changed=5
    after = sched.task_states()["flip"]
    assert after["current_cadence_s"] == 100
    assert after["consecutive_empty"] == 0


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def test_state_persists_across_instances(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    state_path = tmp_path / "dream_state.json"

    task, _ = _make_task("persist", changed=0, base_cadence_s=100)
    sched_a = BackgroundScheduler(
        sleep_cfg={"enabled": True}, tasks=[task], state_path=state_path
    )
    for _ in range(4):
        sched_a.run_once()

    # new scheduler with fresh lambda but same persistent state
    task_b, _ = _make_task("persist", changed=0, base_cadence_s=100)
    sched_b = BackgroundScheduler(
        sleep_cfg={"enabled": True}, tasks=[task_b], state_path=state_path
    )
    restored = sched_b.task_states()["persist"]
    assert restored["consecutive_empty"] >= 3
    assert restored["current_cadence_s"] >= 200


def test_run_once_writes_task_states_to_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    task, _ = _make_task("writer", changed=1)
    sched = _scheduler(tmp_path, [task])
    sched.run_once()

    state_path = tmp_path / "dream_state.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert "tasks" in data
    assert "writer" in data["tasks"]
    assert data["tasks"]["writer"]["last_changed"] == 1


# ---------------------------------------------------------------------------
# run_forever
# ---------------------------------------------------------------------------


class _StubProbe(IdleProbe):
    def __init__(self, value: float) -> None:
        self.value = value

    def seconds_since_activity(self) -> float:
        return self.value


def test_run_forever_skips_tasks_when_idle_threshold_unmet(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    counter = {"calls": 0}

    def _action():
        counter["calls"] += 1
        return {"changed": 1}

    task = BackgroundTask(
        name="gated",
        action=_action,
        base_cadence_s=1,
        idle_required_s=3600,
        tags=("gated",),
    )
    sched = _scheduler(tmp_path, [task])

    async def _runner():
        probe = _StubProbe(60)  # only 60s idle — far below 3600s threshold
        stop_event = asyncio.Event()

        async def _stop_after(delay: float):
            await asyncio.sleep(delay)
            stop_event.set()

        # Make the scheduler think the task is due immediately.
        sched._runtime["gated"].next_run_at = 0
        await asyncio.gather(
            sched.run_forever(probe, stop_event, initial_delay=0, poll_interval=0.05),
            _stop_after(0.15),
        )

    asyncio.run(_runner())
    assert counter["calls"] == 0


def test_run_forever_runs_tasks_once_idle_threshold_met(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    counter = {"calls": 0}

    def _action():
        counter["calls"] += 1
        return {"changed": 1}

    task = BackgroundTask(
        name="go",
        action=_action,
        base_cadence_s=3600,  # long cadence so it fires once then sleeps
        idle_required_s=30,
        tags=("go",),
    )
    sched = _scheduler(tmp_path, [task])

    async def _runner():
        probe = _StubProbe(120)  # plenty idle
        stop_event = asyncio.Event()
        sched._runtime["go"].next_run_at = 0

        async def _stop_after(delay: float):
            await asyncio.sleep(delay)
            stop_event.set()

        await asyncio.gather(
            sched.run_forever(probe, stop_event, initial_delay=0, poll_interval=0.05),
            _stop_after(0.25),
        )

    asyncio.run(_runner())
    assert counter["calls"] >= 1


# ---------------------------------------------------------------------------
# build_default_tasks / build_scheduler
# ---------------------------------------------------------------------------


def test_build_default_tasks_respects_disabled_features():
    sleep_cfg = {
        "enabled": True,
        "profile": "balanced",
        "background_review": False,
        "l4_compaction": False,
        "l4_periodic_archive": False,
        "maintenance_interval_seconds": 3600,
        "l4_interval_seconds": 1800,
        "idle_before_maintenance_seconds": 0,
    }
    tasks = build_default_tasks(sleep_cfg, session_store=None)
    names = {t.name for t in tasks}
    assert "builtin_memory" not in names  # disabled by background_review
    assert "l4_compaction" not in names  # disabled
    assert "retention_archive" not in names  # no session_store
    assert "capability_lifecycle" in names


def test_build_default_tasks_with_session_store_adds_gateway_tasks():
    class _FakeStore:
        def list_sessions(self):
            return []

    sleep_cfg = {
        "enabled": True,
        "profile": "balanced",
        "background_review": True,
        "l4_compaction": True,
        "l4_periodic_archive": True,
        "maintenance_interval_seconds": 3600,
        "l4_interval_seconds": 1800,
        "idle_before_maintenance_seconds": 1800,
    }
    tasks = build_default_tasks(sleep_cfg, session_store=_FakeStore())
    names = {t.name for t in tasks}
    assert {"builtin_memory", "capability_lifecycle", "l4_compaction",
            "retention_archive", "l4_periodic_archive"} <= names


def test_build_scheduler_wires_sleep_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    sched = build_scheduler(
        {"sleep_mode": {"profile": "light"}}, state_path=tmp_path / "dream.json"
    )
    assert sched.sleep_cfg["profile"] == "light"
    assert sched.tasks  # at least one task registered


# ---------------------------------------------------------------------------
# AlwaysReadyProbe
# ---------------------------------------------------------------------------


def test_always_ready_probe_reports_infinite_idle():
    probe = AlwaysReadyProbe()
    assert probe.seconds_since_activity() == float("inf")
