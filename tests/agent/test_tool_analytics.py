"""Tests for agent.tool_analytics module."""

import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "analytics"
    db_dir.mkdir()
    db_path = db_dir / "tool_usage.db"
    monkeypatch.setattr("agent.tool_analytics._db_path", lambda: db_path)
    monkeypatch.setattr("agent.tool_analytics._local", type("_L", (), {"conn": None})())
    yield db_path


def _record(tool_name, **kw):
    from agent.tool_analytics import record_invocation
    record_invocation(tool_name, **kw)


def test_record_and_query_summary():
    from agent.tool_analytics import query_summary

    _record("web_search", toolset="web", success=True, duration_ms=150)
    _record("web_search", toolset="web", success=True, duration_ms=200)
    _record("web_search", toolset="web", success=False, duration_ms=50, error_type="TimeoutError")
    _record("terminal", toolset="sandbox", success=True, duration_ms=3000)

    rows = query_summary(period="all")
    assert len(rows) == 2
    web_row = next(r for r in rows if r["tool_name"] == "web_search")
    assert web_row["total_calls"] == 3
    assert web_row["successes"] == 2
    assert web_row["failures"] == 1
    term_row = next(r for r in rows if r["tool_name"] == "terminal")
    assert term_row["total_calls"] == 1


def test_query_summary_filter_tool():
    from agent.tool_analytics import query_summary

    _record("web_search", success=True, duration_ms=100)
    _record("terminal", success=True, duration_ms=200)

    rows = query_summary(period="all", tool_name="web_search")
    assert len(rows) == 1
    assert rows[0]["tool_name"] == "web_search"


def test_query_timeline():
    from agent.tool_analytics import query_timeline

    _record("web_search", success=True, duration_ms=100)
    _record("terminal", success=True, duration_ms=200)

    rows = query_timeline(period="all", bucket="day")
    assert len(rows) >= 1
    assert "total_calls" in rows[0]


def test_query_top_errors():
    from agent.tool_analytics import query_top_errors

    _record("web_search", success=False, duration_ms=50, error_type="TimeoutError")
    _record("web_search", success=False, duration_ms=30, error_type="ConnectionError")
    _record("terminal", success=False, duration_ms=10, error_type="RuntimeError")

    rows = query_top_errors(period="all")
    assert len(rows) == 3
    names = [r["tool_name"] for r in rows]
    assert "web_search" in names
    assert "terminal" in names


def test_purge_old_data(tmp_path):
    from agent.tool_analytics import purge_old_data, _get_conn

    _record("old_tool", success=True, duration_ms=100)

    conn = _get_conn()
    conn.execute(
        "UPDATE tool_invocations SET ts = datetime('now', '-100 days') WHERE tool_name = 'old_tool'"
    )
    conn.commit()

    _record("new_tool", success=True, duration_ms=100)

    removed = purge_old_data(max_days=90)
    assert removed == 1

    rows = conn.execute("SELECT tool_name FROM tool_invocations").fetchall()
    names = [r["tool_name"] for r in rows]
    assert "old_tool" not in names
    assert "new_tool" in names


def test_record_invocation_no_crash_on_db_error(monkeypatch):
    from agent.tool_analytics import record_invocation

    monkeypatch.setattr("agent.tool_analytics._db_path", lambda: Path("/nonexistent/path/db.sqlite"))
    monkeypatch.setattr("agent.tool_analytics._local", type("_L", (), {"conn": None})())

    record_invocation("test_tool", success=True, duration_ms=100)


def test_cli_cmd_analytics_summary(tmp_path, capsys):
    from hermes_cli.analytics_cmd import cmd_analytics

    _record("web_search", toolset="web", success=True, duration_ms=150)

    args = type("A", (), {
        "analytics_command": "summary",
        "period": "all",
        "tool_name": None,
        "limit": 20,
        "format": "table",
    })()
    cmd_analytics(args)

    captured = capsys.readouterr()
    assert "web_search" in captured.out
    assert "📊" in captured.out


def test_cli_cmd_analytics_json(tmp_path, capsys):
    from hermes_cli.analytics_cmd import cmd_analytics

    _record("terminal", toolset="sandbox", success=True, duration_ms=500)

    args = type("A", (), {
        "analytics_command": "summary",
        "period": "all",
        "tool_name": None,
        "limit": 20,
        "format": "json",
    })()
    cmd_analytics(args)

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, list)
    assert data[0]["tool_name"] == "terminal"
