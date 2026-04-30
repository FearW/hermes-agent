import yaml

from agent.capability_lifecycle import run_lifecycle_maintenance
from hermes_cli import workflow as wf


def test_lifecycle_manager_updates_workflow_and_skill_states(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(wf, "HERMES_HOME", home)
    monkeypatch.setattr(wf, "WORKFLOW_DIR", home / "workflows")
    monkeypatch.setattr(wf, "DEFS_DIR", home / "workflows" / "definitions")
    monkeypatch.setattr(wf, "RUNS_DIR", home / "workflows" / "runs")
    monkeypatch.setattr(wf, "WATCHERS_FILE", home / "workflows" / "watchers.json")
    monkeypatch.setattr(wf, "WATCH_STATE_DIR", home / "workflows" / "watch_state")
    wf._ensure_dirs()

    wf._save_workflow("auto-flow", {
        "name": "auto-flow",
        "prompt_template": "do thing",
        "inputs": {"paths": [], "globs": []},
        "outputs": {"format": "markdown", "write_to": None},
        "watch": {"enabled": False, "path": None, "patterns": ["*"], "recursive": False, "settle_seconds": 3},
        "managed": {"enabled": False, "schedule": None, "job_id": None},
        "metadata": {"auto_draft": True, "auto_match_count": 6},
    })

    skill_path = home / "skills" / "l4-auto" / "l4-test" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text("---\nname: l4-test\ncategory: devops\nlifecycle_state: draft\n---\n\n# l4-test\n", encoding="utf-8")

    import agent.capability_lifecycle as cl
    monkeypatch.setattr(cl, "WORKFLOW_DIR", home / "workflows" / "definitions")
    monkeypatch.setattr(cl, "WORKFLOW_OFFICIAL_DIR", home / "workflows" / "official")
    monkeypatch.setattr(cl, "SKILLS_ROOT", home / "skills")
    monkeypatch.setattr(cl, "skill_draft_path", lambda name: skill_path)

    class _FakeConn:
        def __init__(self):
            self.queries = []
        def execute(self, sql, params=None):
            self.queries.append((sql, params))
            class _Rows:
                def fetchall(self_inner):
                    return [{
                        "session_id": "sess-abc",
                        "linked_skill": "l4-test",
                        "category": "devops",
                        "priority": 4,
                        "confidence": 0.8,
                        "stale_flag": 0,
                        "conflict_flag": 0,
                    }]
            return _Rows()
        def commit(self):
            pass
        def close(self):
            pass

    monkeypatch.setattr(cl, "_connect", lambda: _FakeConn())

    result = run_lifecycle_maintenance()
    assert set(result.keys()) == {"promoted", "demoted", "pruned"}

    workflow_data = yaml.safe_load((home / "workflows" / "definitions" / "auto-flow.yaml").read_text())
    assert workflow_data["metadata"]["lifecycle_state"] == "promoted"
    assert (home / "workflows" / "official" / "auto-flow.yaml").exists()

    skill_text = skill_path.read_text(encoding="utf-8")
    assert "lifecycle_state: promoted" in skill_text
    assert (home / "skills" / "devops" / "l4-test" / "SKILL.md").exists()
