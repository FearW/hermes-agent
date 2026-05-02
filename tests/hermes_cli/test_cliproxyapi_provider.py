"""Tests for CLIProxyAPI / CPA runtime provider support."""

import json
import urllib.request

from hermes_cli.auth import PROVIDER_REGISTRY, resolve_provider
import pytest

from hermes_cli.runtime_provider import LegacyProviderDisabledError, resolve_runtime_provider


def test_cliproxyapi_provider_registered():
    pconfig = PROVIDER_REGISTRY["cliproxyapi"]
    assert pconfig.name == "CLIProxyAPI"
    assert pconfig.auth_type == "api_key"
    assert pconfig.inference_base_url == "http://127.0.0.1:8080/v1"
    assert pconfig.api_key_env_vars == ("CLIPROXY_API_KEY", "CPA_API_KEY", "OPENAI_API_KEY")
    assert pconfig.base_url_env_var == "CLIPROXY_BASE_URL"


def test_cpa_alias_provider_registered():
    pconfig = PROVIDER_REGISTRY["cpa"]
    assert pconfig.name == "CLIProxyAPI"
    assert pconfig.inference_base_url == "http://127.0.0.1:8080/v1"


def test_resolve_provider_accepts_cpa_names():
    assert resolve_provider("cliproxyapi") == "cliproxyapi"
    assert resolve_provider("cpa") == "cpa"


def test_runtime_provider_defaults_to_local_cpa(monkeypatch):
    for key in ("CLIPROXY_BASE_URL", "CPA_BASE_URL", "CLIPROXY_API_KEY", "CPA_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    runtime = resolve_runtime_provider(requested="cpa")

    assert runtime["provider"] == "cliproxyapi"
    assert runtime["requested_provider"] == "cpa"
    assert runtime["api_mode"] == "chat_completions"
    assert runtime["base_url"] == "http://127.0.0.1:8080/v1"
    assert runtime["api_key"] == "no-key-required"


def test_legacy_provider_runtime_is_disabled(monkeypatch):
    monkeypatch.delenv("CLIPROXY_BASE_URL", raising=False)
    monkeypatch.delenv("CPA_BASE_URL", raising=False)

    with pytest.raises(LegacyProviderDisabledError):
        resolve_runtime_provider(requested="openrouter")


def test_default_config_is_cpa_first():
    from hermes_cli.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["model"]["provider"] == "cliproxyapi"
    assert DEFAULT_CONFIG["model"]["default"] == "gpt-5(8192)"
    assert DEFAULT_CONFIG["model"]["base_url"] == "http://127.0.0.1:8080/v1"


def test_empty_legacy_model_config_falls_back_to_cpa(monkeypatch, tmp_path):
    from hermes_cli.config import save_config


    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for key in ("CLIPROXY_BASE_URL", "CPA_BASE_URL", "CLIPROXY_API_KEY", "CPA_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    save_config({"model": ""})

    runtime = resolve_runtime_provider()

    assert runtime["provider"] == "cliproxyapi"
    assert runtime["base_url"] == "http://127.0.0.1:8080/v1"
    assert runtime["api_key"] == "no-key-required"


def test_runtime_provider_uses_cpa_env_over_default(monkeypatch):
    monkeypatch.setenv("CPA_BASE_URL", "http://localhost:9000/v1")
    monkeypatch.setenv("CPA_API_KEY", "cpa-test-key")
    monkeypatch.delenv("CLIPROXY_BASE_URL", raising=False)
    monkeypatch.delenv("CLIPROXY_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    runtime = resolve_runtime_provider(requested="cliproxyapi")

    assert runtime["provider"] == "cliproxyapi"
    assert runtime["requested_provider"] == "cliproxyapi"
    assert runtime["base_url"] == "http://localhost:9000/v1"
    assert runtime["api_key"] == "cpa-test-key"


def test_api_key_provider_status_uses_cpa_base_url_alias(monkeypatch):
    from hermes_cli.auth import get_api_key_provider_status

    monkeypatch.setenv("CPA_BASE_URL", "http://localhost:9100/v1")
    monkeypatch.setenv("CPA_API_KEY", "cpa-test-key")
    monkeypatch.delenv("CLIPROXY_BASE_URL", raising=False)
    monkeypatch.delenv("CLIPROXY_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    status = get_api_key_provider_status("cliproxyapi")

    assert status["base_url"] == "http://localhost:9100/v1"
    assert status["logged_in"] is True

def test_cliproxyapi_is_visible_in_model_picker():
    from hermes_cli.models import CANONICAL_PROVIDERS, provider_model_ids

    provider_ids = [provider.slug for provider in CANONICAL_PROVIDERS]
    assert "cliproxyapi" in provider_ids
    assert provider_model_ids("cliproxyapi")[0] == "gpt-5(8192)"


def test_cliproxyapi_model_suffix_is_preserved():
    from hermes_cli.models import provider_model_ids

    assert "gpt-5(8192)" in provider_model_ids("cliproxyapi")


def test_cpa_management_url_strips_v1_path():
    from hermes_cli.main import _cpa_management_url

    assert _cpa_management_url("http://127.0.0.1:8080/v1", "openai-compatibility") == (
        "http://127.0.0.1:8080/openai-compatibility"
    )


def test_cpa_put_provider_config_sends_payload(monkeypatch):
    from hermes_cli import main as main_mod

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    ok, message = main_mod._cpa_put_provider_config(
        "http://localhost:8080/v1",
        "cpa-key",
        "codex-api-key",
        [{"api-key": "upstream-key"}],
    )

    assert ok is True
    assert message == "已写入 CPA"
    assert captured["url"] == "http://localhost:8080/codex-api-key"
    assert captured["method"] == "PUT"
    assert captured["auth"] == "Bearer cpa-key"
    assert captured["body"] == [{"api-key": "upstream-key"}]
