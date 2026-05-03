"""Tests for CPA-only fallback model activation."""

from unittest.mock import MagicMock, patch

import pytest

import run_agent
from run_agent import AIAgent


@pytest.fixture(autouse=True)
def _no_fallback_wait(monkeypatch):
    """Avoid sleeps in fallback/recovery tests."""
    import time as _time

    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(run_agent, "jittered_backoff", lambda *a, **k: 0.0)


def _make_tool_defs(*names: str) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"{name} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in names
    ]


def _make_agent(fallback_model=None):
    """Create a lightweight AIAgent with only fallback state populated."""
    agent = AIAgent.__new__(AIAgent)
    agent.api_key = "cpa-primary-key"
    agent.base_url = "http://127.0.0.1:8080/v1"
    agent.provider = "cliproxyapi"
    agent.api_mode = "chat_completions"
    agent.model = "gpt-5(8192)"
    agent.client = MagicMock()
    agent.client.api_key = "cpa-primary-key"
    agent.client.base_url = "http://127.0.0.1:8080/v1"
    agent.context_compressor = None
    agent._fallback_chain = agent._normalize_fallback_chain(fallback_model)
    agent._fallback_index = 0
    agent._fallback_model = agent._fallback_chain[0] if agent._fallback_chain else None
    agent._fallback_activated = False
    agent._primary_runtime = {"provider": "cliproxyapi"}
    agent._transport_cache = {}
    agent._config_context_length = None
    agent._rate_limited_until = 0
    agent._emit_status = lambda msg: None
    return agent


class TestTryActivateFallback:
    def test_returns_false_when_not_configured(self):
        agent = _make_agent(fallback_model=None)

        assert agent._try_activate_fallback() is False
        assert agent._fallback_activated is False

    def test_returns_false_for_empty_config(self):
        agent = _make_agent(fallback_model={"provider": "openrouter", "model": ""})

        assert agent._try_activate_fallback() is False

    def test_accepts_missing_provider_as_cpa_model(self):
        agent = _make_agent(fallback_model={"model": "gpt-5-mini"})

        assert agent._fallback_model == {
            "provider": "cliproxyapi",
            "model": "gpt-5-mini",
            "api_mode": "chat_completions",
        }
        assert agent._try_activate_fallback() is True
        assert agent.model == "gpt-5-mini"
        assert agent.provider == "cliproxyapi"

    def test_ignores_legacy_provider_fields(self):
        agent = _make_agent(
            fallback_model={"provider": "openrouter", "model": "anthropic/claude-sonnet-4"},
        )

        with patch("agent.auxiliary_client.resolve_provider_client") as mock_resolve:
            result = agent._try_activate_fallback()

        assert result is True
        mock_resolve.assert_not_called()
        assert agent._fallback_activated is True
        assert agent.model == "anthropic/claude-sonnet-4"
        assert agent.provider == "cliproxyapi"
        assert agent.api_mode == "chat_completions"
        assert agent.base_url == "http://127.0.0.1:8080/v1"
        assert agent.api_key == "cpa-primary-key"
        assert agent.client.api_key == "cpa-primary-key"

    def test_string_fallback_is_a_cpa_model(self):
        agent = _make_agent(fallback_model="gpt-5-mini")

        assert agent._fallback_model == {"provider": "cliproxyapi", "model": "gpt-5-mini"}
        assert agent._try_activate_fallback() is True
        assert agent.model == "gpt-5-mini"
        assert agent.provider == "cliproxyapi"

    def test_context_length_is_preserved(self):
        agent = _make_agent(
            fallback_model={
                "provider": "zai",
                "model": "glm-5",
                "context_length": 262144,
            },
        )

        assert agent._try_activate_fallback() is True
        assert agent.model == "glm-5"
        assert agent.provider == "cliproxyapi"
        assert agent._config_context_length == 262144

    def test_single_fallback_only_fires_once(self):
        agent = _make_agent(fallback_model={"provider": "openrouter", "model": "model-a"})

        assert agent._try_activate_fallback() is True
        assert agent._try_activate_fallback() is False

    def test_fallback_chain_advances_by_model(self):
        agent = _make_agent(
            fallback_model=[
                {"provider": "openrouter", "model": "model-a"},
                {"provider": "anthropic", "model": "model-b"},
            ],
        )

        assert agent._try_activate_fallback() is True
        assert agent.model == "model-a"
        assert agent.provider == "cliproxyapi"
        assert agent._fallback_index == 1

        assert agent._try_activate_fallback() is True
        assert agent.model == "model-b"
        assert agent.provider == "cliproxyapi"
        assert agent._fallback_index == 2

        assert agent._try_activate_fallback() is False


class TestFallbackInit:
    def test_fallback_stored_when_configured(self):
        agent = _make_agent(
            fallback_model={"provider": "openrouter", "model": "anthropic/claude-sonnet-4"},
        )

        assert agent._fallback_model is not None
        assert agent._fallback_model["provider"] == "cliproxyapi"
        assert agent._fallback_model["model"] == "anthropic/claude-sonnet-4"
        assert agent._fallback_activated is False

    def test_fallback_none_when_not_configured(self):
        agent = _make_agent(fallback_model=None)

        assert agent._fallback_model is None
        assert agent._fallback_activated is False

    def test_fallback_none_for_unsupported_type(self):
        agent = _make_agent(fallback_model=123)

        assert agent._fallback_model is None