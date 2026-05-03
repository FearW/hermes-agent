"""Tests that _try_activate_fallback updates the context compressor."""

from unittest.mock import MagicMock, patch

from run_agent import AIAgent
from agent.context_compressor import ContextCompressor


def _make_agent_with_compressor() -> AIAgent:
    """Build a minimal AIAgent with a context_compressor, skipping __init__."""
    agent = AIAgent.__new__(AIAgent)

    agent.model = "primary-model"
    agent.provider = "cliproxyapi"
    agent.base_url = "http://127.0.0.1:8080/v1"
    agent.api_key = "sk-primary"
    agent.api_mode = "chat_completions"
    agent.client = MagicMock()
    agent.quiet_mode = True

    agent._fallback_activated = False
    agent._fallback_model = {
        "provider": "cliproxyapi",
        "model": "gpt-4o",
    }
    agent._fallback_chain = [agent._fallback_model]
    agent._fallback_index = 0
    agent._primary_runtime = {"provider": "cliproxyapi"}
    agent._transport_cache = {}
    agent._config_context_length = None
    agent._rate_limited_until = 0

    agent.context_compressor = ContextCompressor(
        model="primary-model",
        threshold_percent=0.50,
        base_url="http://127.0.0.1:8080/v1",
        api_key="sk-primary",
        provider="cliproxyapi",
        quiet_mode=True,
    )

    return agent


@patch("agent.model_metadata.get_model_context_length", return_value=128_000)
def test_compressor_updated_on_fallback(mock_ctx_len):
    """After fallback activation, the compressor must reflect the fallback model."""
    agent = _make_agent_with_compressor()

    assert agent.context_compressor.model == "primary-model"
    agent._emit_status = lambda msg: None

    with patch("agent.auxiliary_client.resolve_provider_client") as mock_resolve:
        result = agent._try_activate_fallback()

    assert result is True
    assert agent._fallback_activated is True
    mock_resolve.assert_not_called()

    c = agent.context_compressor
    assert c.model == "gpt-4o"
    assert c.base_url == "http://127.0.0.1:8080/v1"
    assert c.api_key == "sk-primary"
    assert c.provider == "cliproxyapi"
    assert c.context_length == 128_000
    assert c.threshold_tokens == int(128_000 * c.threshold_percent)


@patch("agent.model_metadata.get_model_context_length", return_value=128_000)
def test_compressor_not_present_does_not_crash(mock_ctx_len):
    """If the agent has no compressor, fallback should still succeed."""
    agent = _make_agent_with_compressor()
    agent.context_compressor = None
    agent._emit_status = lambda msg: None

    with patch("agent.auxiliary_client.resolve_provider_client") as mock_resolve:
        result = agent._try_activate_fallback()

    assert result is True
    mock_resolve.assert_not_called()