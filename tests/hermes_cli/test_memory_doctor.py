from argparse import Namespace

from hermes_cli.memory_doctor import analyze_memory_health, run_memory_doctor
from tools.memory_tool import ENTRY_DELIMITER


def test_analyze_memory_health_reports_slimming_candidates(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    mem_dir = home / "memories"
    mem_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("hermes_cli.memory_doctor.get_hermes_home", lambda: home)

    (mem_dir / "MEMORY.md").write_text(
        ENTRY_DELIMITER.join([
            "Project hermes-agent uses uv",
            "Project hermes-agent uses uv for installs",
        ]),
        encoding="utf-8",
    )
    (mem_dir / "USER.md").write_text("User prefers concise Chinese", encoding="utf-8")

    health = analyze_memory_health()

    assert health["memory"].entries == 2
    assert health["memory"].compacted_entries == 1
    assert health["memory"].status == "can slim"
    assert health["needs_action"] is True


def test_run_memory_doctor_compact_writes_compacted_memory(tmp_path, monkeypatch, capsys):
    home = tmp_path / ".hermes"
    mem_dir = home / "memories"
    mem_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("hermes_cli.memory_doctor.get_hermes_home", lambda: home)
    monkeypatch.setattr("hermes_cli.memory_doctor.display_hermes_home", lambda: str(home))

    memory_file = mem_dir / "MEMORY.md"
    memory_file.write_text(
        ENTRY_DELIMITER.join([
            "Project hermes-agent uses uv",
            "Project hermes-agent uses uv for installs",
        ]),
        encoding="utf-8",
    )

    run_memory_doctor(Namespace(compact=True))

    out = capsys.readouterr().out
    assert "Memory Health" in out
    assert "已执行安全瘦身" in out
    assert len(memory_file.read_text(encoding="utf-8").split(ENTRY_DELIMITER)) == 1
