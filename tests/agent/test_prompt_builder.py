"""Tests for agent/prompt_builder.py  context scanning, truncation, skills index."""

import builtins
import importlib
import logging
import sys

import pytest

from agent.prompt_builder import (
    _scan_context_content,
    _truncate_content,
    _parse_skill_file,
    _skill_should_show,
    _find_hermes_md,
    _find_git_root,
    _strip_yaml_frontmatter,
    build_skills_system_prompt,
    build_nous_subscription_prompt,
    build_context_files_prompt,
    build_environment_hints,
    CONTEXT_FILE_MAX_CHARS,
    DEFAULT_AGENT_IDENTITY,
    TOOL_USE_ENFORCEMENT_GUIDANCE,
    TOOL_USE_ENFORCEMENT_MODELS,
    OPENAI_MODEL_EXECUTION_GUIDANCE,
    MEMORY_GUIDANCE,
    SESSION_SEARCH_GUIDANCE,
    PLATFORM_HINTS,
    WSL_ENVIRONMENT_HINT,
)
from hermes_cli.nous_subscription import NousFeatureState, NousSubscriptionFeatures
from agent.user_profile import build_user_profile_prompt, PROFILE_PATH


class TestGuidanceConstants:
    def test_memory_guidance_discourages_task_logs(self):
        assert "durable facts" in MEMORY_GUIDANCE
        assert "Do NOT save task progress" in MEMORY_GUIDANCE
        assert "session_search" in MEMORY_GUIDANCE

    def test_session_search_guidance_mentions_l4(self):
        assert "L4 archival memory" in SESSION_SEARCH_GUIDANCE


def test_user_profile_prompt_builds(tmp_path, monkeypatch):
    monkeypatch.setattr('agent.user_profile.PROFILE_PATH', tmp_path / 'profile.json')
    (tmp_path / 'profile.json').write_text('{"name":"User","communication":{"language":"zh-CN","style":"direct"},"preferences":["concise"]}', encoding='utf-8')
    prompt = build_user_profile_prompt()
    assert '## User Profile' in prompt
    assert 'User' in prompt
    assert 'concise' in prompt
