"""Tests for CPA-only gateway runtime credential resolution."""

from unittest.mock import patch

import pytest


class TestResolveRuntimeAgentKwargs:
    """_resolve_runtime_agent_kwargs resolves CPA only."""

    def test_resolves_cpa_runtime_once(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gateway.run._hermes_home", tmp_path)

        calls = {"n": 0}

        def _mock_resolve(**kwargs):
            calls["n"] += 1
            assert kwargs.get("requested") == "cliproxyapi"
            return {
                "api_key": "cpa-key",
                "base_url": "http://127.0.0.1:8080/v1",
                "provider": "cliproxyapi",
                "api_mode": "chat_completions",
                "command": None,
                "args": None,
                "credential_pool": None,
            }

        with patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            side_effect=_mock_resolve,
        ):
            from gateway.run import _resolve_runtime_agent_kwargs

            result = _resolve_runtime_agent_kwargs()

        assert calls["n"] == 1
        assert result["provider"] == "cliproxyapi"
        assert result["api_key"] == "cpa-key"
        assert result["base_url"] == "http://127.0.0.1:8080/v1"
        assert result["api_mode"] == "chat_completions"

    def test_auth_error_does_not_try_provider_fallback(self, tmp_path, monkeypatch):
        from hermes_cli.auth import AuthError

        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "model:\n  provider: cliproxyapi\n"
            "fallback_model:\n  model: meta-llama/llama-4-maverick\n"
        )
        monkeypatch.setattr("gateway.run._hermes_home", tmp_path)

        with patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            side_effect=AuthError("CPA token refresh failed"),
        ) as mock_resolve:
            from gateway.run import _resolve_runtime_agent_kwargs

            with pytest.raises(RuntimeError):
                _resolve_runtime_agent_kwargs()

        mock_resolve.assert_called_once()
        assert mock_resolve.call_args.kwargs["requested"] == "cliproxyapi"