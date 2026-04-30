import re
from pathlib import Path


def test_main_referenced_hermes_cli_modules_exist():
    main_path = Path("hermes_cli/main.py")
    text = main_path.read_text(encoding="utf-8")
    matches = re.findall(
        r"from hermes_cli\.([A-Za-z_][A-Za-z0-9_]*) import|import hermes_cli\.([A-Za-z_][A-Za-z0-9_]*)",
        text,
    )
    modules = sorted({left or right for left, right in matches})

    missing = []
    for module in modules:
        module_file = Path("hermes_cli") / f"{module}.py"
        package_init = Path("hermes_cli") / module / "__init__.py"
        if not module_file.exists() and not package_init.exists():
            missing.append(module)

    assert missing == []


def test_main_imports_without_missing_cli_modules():
    import hermes_cli.main as main_mod

    assert callable(main_mod.main)
