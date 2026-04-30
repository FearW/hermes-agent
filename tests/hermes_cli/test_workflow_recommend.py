from hermes_cli import workflow as wf
from hermes_cli import workflow_recommend as wr


def test_recommend_workflows_for_text_matches_saved_definition(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(wf, "HERMES_HOME", home)
    monkeypatch.setattr(wf, "WORKFLOW_DIR", home / "workflows")
    monkeypatch.setattr(wf, "DEFS_DIR", home / "workflows" / "definitions")
    monkeypatch.setattr(wf, "RUNS_DIR", home / "workflows" / "runs")
    monkeypatch.setattr(wf, "WATCHERS_FILE", home / "workflows" / "watchers.json")
    monkeypatch.setattr(wf, "WATCH_STATE_DIR", home / "workflows" / "watch_state")
    monkeypatch.setattr(wr, "DEFS_DIR", home / "workflows" / "definitions")
    wf._ensure_dirs()

    wf._save_workflow("daily-weather", {
        "name": "daily-weather",
        "description": "Watch weather files and summarize daily report",
        "prompt_template": "Watch {{input_paths}} and summarize weather report",
        "inputs": {"paths": [], "globs": ["/root/inbox/*.txt"]},
        "outputs": {"format": "markdown", "write_to": "reports/weather.md"},
        "watch": {"enabled": True, "path": "/root/inbox", "patterns": ["*.txt"], "recursive": False, "settle_seconds": 3},
        "managed": {"enabled": False, "schedule": None, "job_id": None},
        "metadata": {"auto_draft": True, "category": "productivity", "priority": 4},
    })

    matches = wr.recommend_workflows_for_text("Please summarize the weather report from txt files")
    assert matches
    assert matches[0]["name"] == "daily-weather"

    note = wr.build_workflow_recommendation_note("summarize weather report txt")
    assert "daily-weather" in note
    assert "Category: productivity" in note
    assert "Priority: 4/5" in note

    _, updated = wf._load_workflow("daily-weather")
    assert updated["metadata"]["auto_match_count"] >= 1
