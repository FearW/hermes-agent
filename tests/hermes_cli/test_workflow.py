import argparse

from hermes_cli import workflow as wf


def _patch_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(wf, "HERMES_HOME", home)
    monkeypatch.setattr(wf, "WORKFLOW_DIR", home / "workflows")
    monkeypatch.setattr(wf, "DEFS_DIR", home / "workflows" / "definitions")
    monkeypatch.setattr(wf, "RUNS_DIR", home / "workflows" / "runs")
    monkeypatch.setattr(wf, "WATCHERS_FILE", home / "workflows" / "watchers.json")
    monkeypatch.setattr(wf, "WATCH_STATE_DIR", home / "workflows" / "watch_state")
    wf._ensure_dirs()
    return home


def test_workflow_create_and_load(tmp_path, monkeypatch):
    _patch_home(tmp_path, monkeypatch)

    class Args:
        name = "daily-report"
        prompt = "Summarize {{input_1}}"
        description = "Daily report workflow"
        input = ["/tmp/input.txt"]
        glob = None
        write_to = "reports/out.md"
        output_format = "markdown"
        skills = ["news"]
        provider = "minimax-cn"
        model = "MiniMax-M2.7"
        deliver = "local"

    assert wf.workflow_create(Args()) == 0
    path, data = wf._load_workflow("daily-report")
    assert path.exists()
    assert data["prompt_template"] == "Summarize {{input_1}}"
    assert data["outputs"]["write_to"] == "reports/out.md"
    assert data["skills"] == ["news"]


def test_workflow_capture_infers_paths_outputs_and_watch(monkeypatch):
    monkeypatch.setattr(
        wf,
        "_session_messages",
        lambda session_id: [
            {"role": "user", "content": "Watch /root/inbox/*.txt and summarize each new file."},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "Write the result to reports/daily.md"},
        ],
    )
    workflow = wf._build_workflow_from_session("capture-test", "sess_1")
    assert workflow["metadata"]["source_session_id"] == "sess_1"
    assert "{{input_paths}}" in workflow["prompt_template"]
    assert workflow["inputs"]["globs"] == ["/root/inbox/*.txt"]
    assert workflow["outputs"]["write_to"] == "reports/daily.md"
    assert workflow["watch"]["enabled"] is True
    assert workflow["watch"]["path"] == "/root/inbox"
    assert workflow["watch"]["patterns"] == ["*.txt"]
    assert workflow["metadata"]["variables"]


def test_workflow_watch_set_persists_config(tmp_path, monkeypatch):
    home = _patch_home(tmp_path, monkeypatch)
    wf._save_workflow("watch-me", {
        "name": "watch-me",
        "prompt_template": "Process {{input_1}}",
        "inputs": {"paths": [], "globs": []},
        "outputs": {"format": "markdown", "write_to": None},
        "watch": {"enabled": False, "path": None, "patterns": ["*"], "recursive": False, "settle_seconds": 3},
        "managed": {"enabled": False, "schedule": None, "job_id": None},
    })

    class Args:
        name = "watch-me"
        path = str((home / "incoming").resolve())
        pattern = ["*.txt"]
        recursive = True
        settle_seconds = 5

    assert wf.workflow_watch_set(Args()) == 0
    watchers = wf._read_watchers()
    assert watchers[0]["workflow"] == "watch-me"
    assert watchers[0]["patterns"] == ["*.txt"]
    assert watchers[0]["recursive"] is True


def test_build_workflow_parser_registers_command():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    wf.build_workflow_parser(subparsers)
    args = parser.parse_args(["workflow", "list"])
    assert args.command == "workflow"
    assert args.workflow_action == "list"
