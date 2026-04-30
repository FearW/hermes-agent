"""Tests for gateway session management."""

import pytest
from gateway.config import Platform, HomeChannel, GatewayConfig, PlatformConfig
from gateway.session import SessionSource, build_session_context, build_session_context_prompt


def test_build_session_context_prompt_contains_project_fields(monkeypatch):
    config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token='fake')})
    source = SessionSource(platform=Platform.TELEGRAM, chat_id='111', chat_name='Home Chat', chat_type='dm')
    monkeypatch.setenv('TERMINAL_CWD', '/srv/projects/hermes-app')
    ctx = build_session_context(source, config)
    prompt = build_session_context_prompt(ctx)
    assert '**Project Tag:** hermes-app' in prompt
    assert '**Project Root:** /srv/projects/hermes-app' in prompt
