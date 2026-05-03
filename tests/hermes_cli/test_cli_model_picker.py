"""CPA-only tests for /model picker behavior."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class _FakeBuffer:
    def __init__(self, text="draft text"):
        self.text = text
        self.cursor_position = len(text)
        self.reset_calls = []

    def reset(self, append_to_history=False):
        self.reset_calls.append(append_to_history)
        self.text = ""
        self.cursor_position = 0


def _make_modal_cli():
    from cli import HermesCLI

    cli = HermesCLI.__new__(HermesCLI)
    cli.model = "gpt-5.4"
    cli.provider = "cliproxyapi"
    cli.requested_provider = "cliproxyapi"
    cli.base_url = "http://127.0.0.1:8080/v1"
    cli.api_key = ""
    cli.api_mode = "chat_completions"
    cli._explicit_api_key = ""
    cli._explicit_base_url = ""
    cli._pending_model_switch_note = None
    cli._model_picker_state = None
    cli._modal_input_snapshot = None
    cli._status_bar_visible = True
    cli._invalidate = MagicMock()
    cli.agent = None
    cli.config = {}
    cli.console = MagicMock()
    cli._app = SimpleNamespace(
        current_buffer=_FakeBuffer(),
        invalidate=MagicMock(),
    )
    return cli


def test_prompt_text_input_uses_run_in_terminal_when_app_active():
    from cli import HermesCLI

    cli = _make_modal_cli()

    with (
        patch("prompt_toolkit.application.run_in_terminal", side_effect=lambda fn: fn()) as run_mock,
        patch("builtins.input", return_value="manual-value"),
    ):
        result = HermesCLI._prompt_text_input(cli, "Enter value: ")

    assert result == "manual-value"
    run_mock.assert_called_once()
    assert cli._status_bar_visible is True


def test_should_handle_model_command_inline_uses_command_name_resolution():
    from cli import HermesCLI

    cli = _make_modal_cli()

    with patch("hermes_cli.commands.resolve_command", return_value=SimpleNamespace(name="model")):
        assert HermesCLI._should_handle_model_command_inline(cli, "/model") is True

    with patch("hermes_cli.commands.resolve_command", return_value=SimpleNamespace(name="help")):
        assert HermesCLI._should_handle_model_command_inline(cli, "/model") is False

    assert HermesCLI._should_handle_model_command_inline(cli, "/model", has_images=True) is False


def test_open_model_picker_uses_only_cpa_models_and_captures_draft():
    from cli import HermesCLI

    cli = _make_modal_cli()
    providers = [
        {"slug": "openrouter", "name": "OpenRouter", "models": ["anthropic/claude-opus-4.6"]},
        {"slug": "cliproxyapi", "name": "CLIProxyAPI / CPA", "models": ["gpt-5.4", "qwen3-coder"]},
    ]

    HermesCLI._open_model_picker(cli, providers, cli.model, cli.provider)

    assert cli._model_picker_state is not None
    assert cli._model_picker_state["stage"] == "model"
    assert cli._model_picker_state["providers"][0]["slug"] == "cliproxyapi"
    assert cli._model_picker_state["model_list"] == ["gpt-5.4", "qwen3-coder"]
    assert cli._modal_input_snapshot == {"text": "draft text", "cursor_position": len("draft text")}
    assert cli._app.current_buffer.text == ""


def test_model_picker_selection_switches_cpa_model_and_restores_draft():
    from cli import HermesCLI

    cli = _make_modal_cli()
    HermesCLI._open_model_picker(
        cli,
        [{"slug": "cliproxyapi", "name": "CLIProxyAPI / CPA", "models": ["gpt-5.4", "qwen3-coder"]}],
        cli.model,
        cli.provider,
    )
    cli._model_picker_state["selected"] = 1

    switch_result = SimpleNamespace(
        success=True,
        error_message=None,
        new_model="qwen3-coder",
        target_provider="cliproxyapi",
        api_key="sk-cpa",
        base_url="http://127.0.0.1:8080/v1",
        api_mode="chat_completions",
        provider_label="CLIProxyAPI / CPA",
        model_info=None,
        warning_message=None,
        provider_changed=False,
    )

    with (
        patch("hermes_cli.model_switch.switch_model", return_value=switch_result) as switch_mock,
        patch("cli._cprint"),
        patch("cli.save_config_value"),
    ):
        HermesCLI._handle_model_picker_selection(cli)

    assert cli._model_picker_state is None
    assert cli.model == "qwen3-coder"
    assert cli.provider == "cliproxyapi"
    assert cli.requested_provider == "cliproxyapi"
    assert cli._app.current_buffer.text == "draft text"
    switch_mock.assert_called_once()
    assert "explicit_provider" not in switch_mock.call_args.kwargs


def test_model_picker_manual_entry_switches_without_provider():
    from cli import HermesCLI

    cli = _make_modal_cli()
    HermesCLI._open_model_picker(
        cli,
        [{"slug": "cliproxyapi", "name": "CLIProxyAPI / CPA", "models": []}],
        cli.model,
        cli.provider,
    )
    cli._model_picker_state["selected"] = len(cli._model_picker_state["model_list"])
    cli._prompt_text_input = MagicMock(return_value="manual-model")

    switch_result = SimpleNamespace(
        success=True,
        error_message=None,
        new_model="manual-model",
        target_provider="cliproxyapi",
        api_key="",
        base_url="http://127.0.0.1:8080/v1",
        api_mode="chat_completions",
        provider_label="CLIProxyAPI / CPA",
        model_info=None,
        warning_message=None,
        provider_changed=False,
    )

    with (
        patch("hermes_cli.model_switch.switch_model", return_value=switch_result) as switch_mock,
        patch("cli._cprint"),
        patch("cli.save_config_value"),
    ):
        HermesCLI._handle_model_picker_selection(cli)

    assert cli.model == "manual-model"
    assert switch_mock.call_args.kwargs["raw_input"] == "manual-model"
    assert "explicit_provider" not in switch_mock.call_args.kwargs
