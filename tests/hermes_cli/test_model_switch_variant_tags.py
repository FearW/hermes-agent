"""CPA-only model switching preserves the user's model string."""

import pytest
from unittest.mock import patch

from hermes_cli.model_switch import switch_model


_MOCK_VALIDATION = {"accepted": True, "persist": True, "recognized": True, "message": None}


def _run_switch(raw_input: str, current_provider: str = "cliproxyapi") -> str:
    with patch(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        return_value={"api_key": "test", "base_url": "http://127.0.0.1:8080/v1", "api_mode": "chat_completions"},
    ), patch("hermes_cli.models.validate_requested_model", return_value=_MOCK_VALIDATION), \
         patch("hermes_cli.model_switch.get_model_info", return_value=None), \
         patch("hermes_cli.model_switch.get_model_capabilities", return_value=None):
        result = switch_model(
            raw_input=raw_input,
            current_provider=current_provider,
            current_model="anthropic/claude-sonnet-4.6",
        )
        assert result.success, f"switch_model failed: {result.error_message}"
        assert result.target_provider == "cliproxyapi"
        return result.new_model


class TestCpaModelStringPreservation:
    @pytest.mark.parametrize("model", [
        "nvidia/nemotron-3-super-120b-a12b:free",
        "anthropic/claude-sonnet-4.6:extended",
        "meta-llama/llama-4-maverick:fast",
        "nvidia:nemotron-3-super-120b-a12b",
        "nvidia:nemotron-3-super-120b-a12b:free",
        "claude-sonnet-4.6",
        "anthropic/claude-sonnet-4.6",
    ])
    def test_model_string_passes_through_unchanged(self, model):
        assert _run_switch(model) == model

    def test_previous_non_cpa_provider_is_replaced_by_cpa(self):
        with patch(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            return_value={"api_key": "test", "base_url": "http://127.0.0.1:8080/v1", "api_mode": "chat_completions"},
        ), patch("hermes_cli.models.validate_requested_model", return_value=_MOCK_VALIDATION), \
             patch("hermes_cli.model_switch.get_model_info", return_value=None), \
             patch("hermes_cli.model_switch.get_model_capabilities", return_value=None):
            result = switch_model(
                raw_input="qwen3-coder",
                current_provider="openrouter",
                current_model="anthropic/claude-sonnet-4.6",
            )

        assert result.success is True
        assert result.target_provider == "cliproxyapi"
        assert result.provider_changed is True
