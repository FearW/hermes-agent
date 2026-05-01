"""Compatibility tests for terminal output limit helper."""


def test_get_max_bytes_defaults_to_budget_constant(monkeypatch):
    from tools.budget_config import DEFAULT_RESULT_SIZE_CHARS
    from tools.tool_output_limits import get_max_bytes

    monkeypatch.delenv("HERMES_TOOL_OUTPUT_MAX_BYTES", raising=False)
    monkeypatch.delenv("HERMES_TOOL_OUTPUT_MAX_CHARS", raising=False)

    assert get_max_bytes() == DEFAULT_RESULT_SIZE_CHARS


def test_get_max_bytes_honors_env(monkeypatch):
    from tools.tool_output_limits import get_max_bytes

    monkeypatch.setenv("HERMES_TOOL_OUTPUT_MAX_BYTES", "12345")

    assert get_max_bytes() == 12345


def test_get_max_bytes_ignores_invalid_env(monkeypatch):
    from tools.tool_output_limits import get_max_bytes

    monkeypatch.setenv("HERMES_TOOL_OUTPUT_MAX_BYTES", "not-a-number")


    assert get_max_bytes(default=4321) == 4321
