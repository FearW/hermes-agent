"""CPA-only behavior for the shared /model switch pipeline."""

from hermes_cli.model_switch import list_authenticated_providers, switch_model
from hermes_cli.providers import resolve_provider_full


_MOCK_VALIDATION = {
    "accepted": True,
    "persist": True,
    "recognized": True,
    "message": None,
}


def test_list_authenticated_providers_ignores_custom_providers():
    providers = list_authenticated_providers(
        current_provider="openai-codex",
        current_model="qwen3-coder",
        user_providers={"openrouter": {"api_key": "sk-or"}},
        custom_providers=[
            {
                "name": "Local (127.0.0.1:4141)",
                "base_url": "http://127.0.0.1:4141/v1",
                "model": "rotator-openrouter-coding",
            }
        ],
        max_models=50,
    )

    assert [p["slug"] for p in providers] == ["cliproxyapi"]
    assert providers[0]["models"] == ["qwen3-coder"]
    assert providers[0]["source"] == "cpa"


def test_switch_model_rejects_explicit_named_custom_provider(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **kwargs: {
            "api_key": "cpa-key",
            "base_url": "http://127.0.0.1:8080/v1",
            "api_mode": "chat_completions",
        },
    )
    monkeypatch.setattr("hermes_cli.models.validate_requested_model", lambda *a, **k: _MOCK_VALIDATION)

    result = switch_model(
        raw_input="rotator-openrouter-coding",
        current_provider="cliproxyapi",
        current_model="gpt-5.4",
        explicit_provider="custom:local-(127.0.0.1:4141)",
        custom_providers=[
            {
                "name": "Local (127.0.0.1:4141)",
                "base_url": "http://127.0.0.1:4141/v1",
                "model": "rotator-openrouter-coding",
            }
        ],
    )

    assert result.success is False
    assert "CPA/CLIProxyAPI" in result.error_message


def test_switch_model_always_targets_cpa(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **kwargs: {
            "api_key": "cpa-key",
            "base_url": "http://127.0.0.1:8080/v1",
            "api_mode": "chat_completions",
        },
    )
    monkeypatch.setattr("hermes_cli.models.validate_requested_model", lambda *a, **k: _MOCK_VALIDATION)
    monkeypatch.setattr("hermes_cli.model_switch.get_model_info", lambda *a, **k: None)
    monkeypatch.setattr("hermes_cli.model_switch.get_model_capabilities", lambda *a, **k: None)

    result = switch_model(
        raw_input="qwen3-coder",
        current_provider="openai-codex",
        current_model="gpt-5.4",
        current_base_url="https://chatgpt.com/backend-api/codex",
        current_api_key="",
        user_providers={"openrouter": {"api_key": "sk-or"}},
        custom_providers=[{"name": "Local", "base_url": "http://127.0.0.1:4141/v1"}],
    )

    assert result.success is True
    assert result.target_provider == "cliproxyapi"
    assert result.provider_label == "CLIProxyAPI / CPA"
    assert result.new_model == "qwen3-coder"
    assert result.base_url == "http://127.0.0.1:8080/v1"
    assert result.api_key == "cpa-key"
    assert result.provider_changed is True


def test_resolve_provider_full_still_finds_named_custom_provider():
    """Provider config parsing still exists for CPA WebUI/import tooling."""
    resolved = resolve_provider_full(
        "custom:local-(127.0.0.1:4141)",
        user_providers={},
        custom_providers=[
            {
                "name": "Local (127.0.0.1:4141)",
                "base_url": "http://127.0.0.1:4141/v1",
            }
        ],
    )

    assert resolved is not None
    assert resolved.id == "custom:local-(127.0.0.1:4141)"
    assert resolved.name == "Local (127.0.0.1:4141)"
    assert resolved.base_url == "http://127.0.0.1:4141/v1"
    assert resolved.source == "user-config"
