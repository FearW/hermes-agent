from agent import l4_archive
from agent.l4_skill_drafts import create_skill_draft_from_l4, skill_draft_path


def test_search_archive_matches_summary(tmp_path, monkeypatch):
    archive_dir = tmp_path / "memory_l4"
    archive_json = archive_dir / "archive.json"
    archive_db = archive_dir / "archive.db"
    monkeypatch.setattr(l4_archive, "ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(l4_archive, "ARCHIVE_PATH", archive_json)
    monkeypatch.setattr(l4_archive, "ARCHIVE_DB_PATH", archive_db)

    l4_archive.archive_session_summary(
        "sess-2",
        "telegram",
        [
            {"role": "user", "content": "Fix docker networking bug in /srv/app/docker-compose.yml"},
            {"role": "assistant", "content": "Adjusted bridge settings"},
        ],
    )
    matches = l4_archive.search_archive("docker networking compose", limit=2)
    assert matches
    assert matches[0]["session_id"] == "sess-2"
    assert matches[0]["task_type"] == "troubleshooting"
    assert matches[0]["category"] == "devops"
    assert matches[0]["priority"] >= 1
    assert matches[0]["confidence"] >= 0.5
    assert "/srv/app/docker-compose.yml" in matches[0]["important_files"]
    assert archive_db.exists()
    assert l4_archive.RETENTION_DAYS == 14

    l4_archive.link_workflow_candidate("sess-2", "auto-sess-2")
    linked = l4_archive.search_archive("auto-sess-2", limit=2)
    assert linked[0]["linked_workflow"] == "auto-sess-2"

    skill_name = create_skill_draft_from_l4(linked[0])
    assert skill_name.startswith("l4-")
    path = skill_draft_path(skill_name)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "## Purpose" in content
    assert "## Recommended Workflow" in content
    assert "Priority:" in content
    assert "category `devops`" in content
