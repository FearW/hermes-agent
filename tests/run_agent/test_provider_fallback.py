"""Tests for ordered CPA fallback model chains."""

from unittest.mock import MagicMock, patch

from run_agent import AIAgent, _pool_may_recover_from_rate_limit


def _make_agent(fallback_model=None):
    """Create a lightweight AIAgent with only fallback state populated."""
    agent = AIAgent.__new__(AIAgent)
    agent.api_key = "cpa-primary-key"
    agent.base_url = "http://127.0.0.1:8080/v1"
    agent.provider = "cliproxyapi"
    agent.api_mode = "chat_completions"
    agent.model = "gpt-5(8192)"
    agent.client = MagicMock()
    agent.client.base_url = "http://127.0.0.1:8080/v1"
    agent.client.api_key = "cpa-primary-key"
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


class TestFallbackChainInit:
    def test_no_fallback(self):
        agent = _make_agent(fallback_model=None)

        assert agent._fallback_chain == []
        assert agent._fallback_index == 0
        assert agent._fallback_model is None

    def test_single_dict_normalized_to_cpa(self):
        agent = _make_agent(fallback_model={"provider": "openai", "model": "gpt-4o"})

        assert agent._fallback_chain == [
            {"provider": "cliproxyapi", "model": "gpt-4o", "api_mode": "chat_completions"}
        ]
        assert agent._fallback_model == agent._fallback_chain[0]

    def test_list_of_models_ignores_providers(self):
        agent = _make_agent(
            fallback_model=[
                {"provider": "openai", "model": "gpt-4o"},
                {"provider": "zai", "model": "glm-4.7"},
            ],
        )

        assert agent._fallback_chain == [
            {"provider": "cliproxyapi", "model": "gpt-4o", "api_mode": "chat_completions"},
            {"provider": "cliproxyapi", "model": "glm-4.7", "api_mode": "chat_completions"},
        ]
        assert agent._fallback_model == agent._fallback_chain[0]

    def test_invalid_entries_filtered(self):
        agent = _make_agent(
            fallback_model=[
                {"provider": "openai", "model": "gpt-4o"},
                {"provider": "", "model": "glm-4.7"},
                {"provider": "zai"},
                "not-a-dict",
                123,
            ],
        )

        assert agent._fallback_chain == [
            {"provider": "cliproxyapi", "model": "gpt-4o", "api_mode": "chat_completions"},
            {"provider": "cliproxyapi", "model": "glm-4.7", "api_mode": "chat_completions"},
            {"provider": "cliproxyapi", "model": "not-a-dict"},
        ]

    def test_empty_list(self):
        agent = _make_agent(fallback_model=[])

        assert agent._fallback_chain == []
        assert agent._fallback_model is None

    def test_dict_without_provider_is_valid_cpa_model(self):
        agent = _make_agent(fallback_model={"model": "gpt-4o"})

        assert agent._fallback_chain == [
            {"provider": "cliproxyapi", "model": "gpt-4o", "api_mode": "chat_completions"}
        ]


class TestFallbackChainAdvancement:
    def test_exhausted_returns_false(self):
        agent = _make_agent(fallback_model=None)

        assert agent._try_activate_fallback() is False

    def test_cpa_fallback_switches_only_model(self):
        agent = _make_agent(fallback_model="gpt-5-mini")
        agent.model = "gpt-5(8192)"

        with patch("agent.auxiliary_client.resolve_provider_client") as mock_resolve:
            assert agent._try_activate_fallback() is True

        mock_resolve.assert_not_called()
        assert agent.model == "gpt-5-mini"
        assert agent.provider == "cliproxyapi"
        assert agent.base_url == "http://127.0.0.1:8080/v1"
        assert agent.api_mode == "chat_completions"
        assert agent.client.api_key == "cpa-primary-key"

    def test_advances_index(self):
        agent = _make_agent(
            fallback_model=[
                {"provider": "openai", "model": "gpt-4o"},
                {"provider": "zai", "model": "glm-4.7"},
            ],
        )

        assert agent._try_activate_fallback() is True
        assert agent._fallback_index == 1
        assert agent.model == "gpt-4o"
        assert agent.provider == "cliproxyapi"
        assert agent._fallback_activated is True

    def test_second_fallback_works(self):
        agent = _make_agent(
            fallback_model=[
                {"provider": "openai", "model": "gpt-4o"},
                {"provider": "zai", "model": "glm-4.7"},
            ],
        )

        assert agent._try_activate_fallback() is True
        assert agent.model == "gpt-4o"
        assert agent._try_activate_fallback() is True
        assert agent.model == "glm-4.7"
        assert agent.provider == "cliproxyapi"
        assert agent._fallback_index == 2

    def test_all_exhausted_returns_false(self):
        agent = _make_agent(fallback_model=[{"provider": "openai", "model": "gpt-4o"}])

        assert agent._try_activate_fallback() is True
        assert agent._try_activate_fallback() is False


# ── Pool-rotation vs fallback gating (#11314) ────────────────────────────


def _pool(n_entries: int, has_available: bool = True):
    """Make a minimal credential-pool stand-in for rotation-room checks."""
    pool = MagicMock()
    pool.entries.return_value = [MagicMock() for _ in range(n_entries)]
    pool.has_available.return_value = has_available
    return pool


class TestPoolRotationRoom:
    def test_none_pool_returns_false(self):
        assert _pool_may_recover_from_rate_limit(None) is False

    def test_single_credential_returns_false(self):
        assert _pool_may_recover_from_rate_limit(_pool(1)) is False

    def test_single_credential_in_cooldown_returns_false(self):
        assert _pool_may_recover_from_rate_limit(_pool(1, has_available=False)) is False

    def test_two_credentials_available_returns_true(self):
        assert _pool_may_recover_from_rate_limit(_pool(2)) is True

    def test_multiple_credentials_all_in_cooldown_returns_false(self):
        assert _pool_may_recover_from_rate_limit(_pool(3, has_available=False)) is False

    def test_many_credentials_available_returns_true(self):
        assert _pool_may_recover_from_rate_limit(_pool(10)) is True