import json

from hermes_cli import main as hermes_main


def _write_lock(path, packages):
    path.write_text(json.dumps({"packages": packages}), encoding="utf-8")


def test_web_ui_npm_install_needed_when_node_modules_was_cleaned(tmp_path):
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    (web_dir / "package.json").write_text("{}", encoding="utf-8")
    _write_lock(
        web_dir / "package-lock.json",
        {
            "": {"name": "web"},
            "node_modules/vite": {"version": "7.3.1"},
        },
    )

    assert hermes_main._web_ui_npm_install_needed(web_dir) is True


def test_npm_package_lock_matches_when_hidden_lock_matches(tmp_path):
    web_dir = tmp_path / "web"
    node_modules = web_dir / "node_modules"
    node_modules.mkdir(parents=True)
    packages = {
        "": {"name": "web"},
        "node_modules/vite": {"version": "7.3.1"},
    }
    _write_lock(web_dir / "package-lock.json", packages)
    _write_lock(node_modules / ".package-lock.json", packages)

    assert hermes_main._npm_package_lock_matches(web_dir) is True


def test_npm_package_lock_mismatch_when_required_native_file_missing(tmp_path):
    web_dir = tmp_path / "web"
    node_modules = web_dir / "node_modules"
    (node_modules / "vite").mkdir(parents=True)
    (node_modules / "vite" / "package.json").write_text("{}", encoding="utf-8")
    packages = {
        "": {"name": "web"},
        "node_modules/vite": {"version": "7.3.1"},
    }
    _write_lock(web_dir / "package-lock.json", packages)
    _write_lock(node_modules / ".package-lock.json", packages)

    assert (
        hermes_main._npm_package_lock_matches(
            web_dir,
            required_files=(node_modules / "@rollup" / "missing" / "package.json",),
        )
        is False
    )


def test_tui_need_npm_install_when_ink_missing(tmp_path):
    root = tmp_path / "ui-tui"
    root.mkdir()

    assert hermes_main._tui_need_npm_install(root) is True


def test_tui_need_npm_install_false_when_lock_matches(tmp_path):
    root = tmp_path / "ui-tui"
    node_modules = root / "node_modules"
    ink_package = node_modules / "@hermes" / "ink" / "package.json"
    ink_package.parent.mkdir(parents=True)
    ink_package.write_text("{}", encoding="utf-8")
    packages = {
        "": {"name": "ui-tui"},
        "node_modules/@hermes/ink": {"version": "0.0.0"},
    }
    _write_lock(root / "package-lock.json", packages)
    _write_lock(node_modules / ".package-lock.json", packages)

    assert hermes_main._tui_need_npm_install(root) is False
