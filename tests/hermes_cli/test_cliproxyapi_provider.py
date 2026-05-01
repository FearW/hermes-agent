"""Tests for CLIProxyAPI / CPA runtime provider support."""

from hermes_cli.auth import PROVIDER_REGISTRY, resolve_provider
from hermes_cli.runtime_provider import resolve_runtime_provider


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

def test_cliproxyapi_is_visible_in_model_picker():
    from hermes_cli.models import CANONICAL_PROVIDERS, provider_model_ids

    provider_ids = [provider.slug for provider in CANONICAL_PROVIDERS]
    assert "cliproxyapi" in provider_ids
    assert provider_model_ids("cliproxyapi")[0] == "gpt-5(8192)"


def test_cliproxyapi_model_suffix_is_preserved():
    from hermes_cli.models import provider_model_ids

    assert "gpt-5(8192)" in provider_model_ids("cliproxyapi")
