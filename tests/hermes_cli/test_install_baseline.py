from pathlib import Path


INSTALL_FILES = [
    Path("scripts/install.sh"),
    Path("scripts/install.ps1"),
    Path("scripts/install.cmd"),
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_installers_install_this_fork_not_upstream():
    for path in INSTALL_FILES:
        text = _read(path)
        assert "FearW/hermes-agent" in text
        assert "raw.githubusercontent.com/NousResearch/hermes-agent" not in text
        assert "https://github.com/NousResearch/hermes-agent.git" not in text
        assert "git@github.com:NousResearch/hermes-agent.git" not in text


def test_installers_default_to_lightweight_base_install():
    shell_text = _read(Path("scripts/install.sh"))
    ps_text = _read(Path("scripts/install.ps1"))

    assert 'pip install -e "."' in shell_text
    assert '& $UvCmd pip install -e "."' in ps_text
    assert 'pip install -e ".[all]"' not in shell_text
    assert 'pip install -e ".[all]"' not in ps_text


def test_windows_installer_has_no_broken_escaped_quotes():
    text = _read(Path("scripts/install.ps1"))

    assert '\\"' not in text
    assert "Hermes Agent Installer - FearW Fork" in text
    assert "Installation Complete!" in text


def test_update_commands_follow_this_fork():
    for path in [Path("hermes_cli/main.py"), Path("hermes_cli/commands/update.py")]:
        text = _read(path)
        assert "https://github.com/FearW/hermes-agent/archive/refs/heads/" in text
        assert 'OFFICIAL_REPO_URL = "https://github.com/FearW/hermes-agent.git"' in text
        assert "https://github.com/NousResearch/hermes-agent/archive/refs/heads/" not in text
