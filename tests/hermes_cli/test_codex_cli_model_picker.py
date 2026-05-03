"""CPA-only regression tests for the /model provider list.

The old /model picker discovered Codex/Anthropic credentials and exposed them
as selectable providers. Provider selection is now disabled; the list contains
only CLIProxyAPI / CPA and does not migrate external provider credentials.
"""

import base64
import json
import time


def _make_fake_jwt(expiry_offset: int = 3600) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
    exp = int(time.time()) + expiry_offset
    payload_bytes = json.dumps({"exp": exp, "sub": "test"}).encode()
    payload = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    return f"{header}.{payload}.fakesig"


def test_model_picker_lists_only_cpa_when_codex_cli_tokens_exist(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    (hermes_home / "auth.json").write_text(json.dumps({"version": 2, "providers": {}}))
    (codex_home / "auth.json").write_text(json.dumps({
        "tokens": {
            "access_token": _make_fake_jwt(),
            "refresh_token": "fake-refresh-token",
        }
    }))

    from hermes_cli.model_switch import list_authenticated_providers

    providers = list_authenticated_providers(current_provider="openai-codex", max_models=10)

    assert [p["slug"] for p in providers] == ["cliproxyapi"]
    assert providers[0]["name"] == "CLIProxyAPI / CPA"

    store = json.loads((hermes_home / "auth.json").read_text())
    assert store.get("providers", {}) == {}


def test_model_picker_lists_only_cpa_when_anthropic_credentials_exist(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (hermes_home / "auth.json").write_text(json.dumps({"version": 2, "providers": {}}))
    (claude_dir / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": _make_fake_jwt(),
            "refreshToken": "fake-refresh",
            "expiresAt": int(time.time() * 1000) + 3_600_000,
        }
    }))

    from hermes_cli.model_switch import list_authenticated_providers

    providers = list_authenticated_providers(current_provider="anthropic", max_models=10)

    assert [p["slug"] for p in providers] == ["cliproxyapi"]
    assert providers[0]["is_current"] is False


def test_model_picker_marks_cpa_current_and_includes_current_model():
    from hermes_cli.model_switch import list_authenticated_providers

    providers = list_authenticated_providers(
        current_provider="cliproxyapi",
        current_base_url="http://127.0.0.1:8080/v1",
        current_model="qwen3-coder",
        max_models=10,
    )

    assert providers == [{
        "slug": "cliproxyapi",
        "name": "CLIProxyAPI / CPA",
        "is_current": True,
        "is_user_defined": False,
        "models": ["qwen3-coder"],
        "total_models": 1,
        "source": "cpa",
        "api_url": "http://127.0.0.1:8080/v1",
    }]
