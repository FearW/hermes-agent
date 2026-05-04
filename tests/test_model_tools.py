"""Tests for model_tools.py — function call dispatch, agent-loop interception, legacy toolsets."""

import importlib
import json
import sys
from unittest.mock import call, patch

import pytest

from tools.registry import registry

from model_tools import (
    handle_function_call,
    get_all_tool_names,
    get_toolset_for_tool,
    _AGENT_LOOP_TOOLS,
    _LEGACY_TOOLSET_MAP,
    TOOL_TO_TOOLSET_MAP,
)


def test_dynamic_tool_discovery_is_explicitly_lazy(monkeypatch):
    sys.modules.pop("model_tools", None)
    monkeypatch.setattr("tools.mcp_tool.discover_mcp_tools", lambda: (_ for _ in ()).throw(AssertionError("eager mcp discovery")))
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: (_ for _ in ()).throw(AssertionError("eager plugin discovery")))

    module = importlib.import_module("model_tools")

    assert module._dynamic_discovery_done is False
    module._dynamic_discovery_done = True


def test_dynamic_tool_discovery_updates_existing_mapping_references(monkeypatch):
    import model_tools as module

    tool_name = "test_dynamic_reference_tool"
    toolset_name = "test_dynamic_reference"
    old_map = module.TOOL_TO_TOOLSET_MAP

    def register_dynamic_tool():
        registry.register(
            name=tool_name,
            toolset=toolset_name,
            schema={
                "description": "Dynamic test tool",
                "parameters": {"type": "object", "properties": {}},
            },
            handler=lambda args, **kwargs: "{}",
        )

    monkeypatch.setattr("tools.mcp_tool.discover_mcp_tools", register_dynamic_tool)
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda: None)
    monkeypatch.setattr("tools.plan_mode.apply_classification", lambda: None)
    monkeypatch.setattr(module, "_dynamic_discovery_done", False)

    try:
        module._ensure_dynamic_tool_discovery()

        assert old_map is module.TOOL_TO_TOOLSET_MAP
        assert old_map[tool_name] == toolset_name
    finally:
        registry.deregister(tool_name)
        module.TOOL_TO_TOOLSET_MAP.clear()
        module.TOOL_TO_TOOLSET_MAP.update(registry.get_tool_to_toolset_map())
        module.TOOLSET_REQUIREMENTS.clear()
        module.TOOLSET_REQUIREMENTS.update(registry.get_toolset_requirements())


@pytest.mark.parametrize(
    ("failing_target", "expected_fragment"),
    [
        ("tools.mcp_tool.discover_mcp_tools", "MCP tool discovery failed"),
        ("hermes_cli.plugins.discover_plugins", "Plugin discovery failed"),
        ("tools.plan_mode.apply_classification", "Plan-mode classification failed"),
    ],
)
def test_dynamic_tool_discovery_logs_and_keeps_maps_consistent_on_partial_failures(
    monkeypatch, failing_target, expected_fragment
):
    import model_tools as module
    original_done = module._dynamic_discovery_done

    mcp_called = 0
    plugins_called = 0
    classify_called = 0

    def _ok_mcp():
        nonlocal mcp_called
        mcp_called += 1

    def _ok_plugins():
        nonlocal plugins_called
        plugins_called += 1

    def _ok_classify():
        nonlocal classify_called
        classify_called += 1

    monkeypatch.setattr("tools.mcp_tool.discover_mcp_tools", _ok_mcp)
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", _ok_plugins)
    monkeypatch.setattr("tools.plan_mode.apply_classification", _ok_classify)
    monkeypatch.setattr(module, "_dynamic_discovery_done", False)

    if failing_target == "tools.mcp_tool.discover_mcp_tools":
        monkeypatch.setattr(
            failing_target, lambda: (_ for _ in ()).throw(RuntimeError("boom-mcp"))
        )
    elif failing_target == "hermes_cli.plugins.discover_plugins":
        monkeypatch.setattr(
            failing_target,
            lambda: (_ for _ in ()).throw(RuntimeError("boom-plugins")),
        )
    else:
        monkeypatch.setattr(
            failing_target,
            lambda: (_ for _ in ()).throw(RuntimeError("boom-classification")),
        )

    try:
        with patch.object(module.logger, "debug") as debug_log:
            module._ensure_dynamic_tool_discovery()

        assert module._dynamic_discovery_done is True
        assert module.TOOL_TO_TOOLSET_MAP == registry.get_tool_to_toolset_map()
        assert module.TOOLSET_REQUIREMENTS == registry.get_toolset_requirements()
        assert any(expected_fragment in str(args[0]) for args, _ in debug_log.call_args_list)
        assert mcp_called + plugins_called + classify_called == 2
    finally:
        monkeypatch.setattr(module, "_dynamic_discovery_done", original_done)


def test_quiet_toolset_resolution_is_cached(monkeypatch):
    import model_tools as module

    calls = []

    def fake_resolve_toolset(name):
        calls.append(name)
        return ["read_file"]

    monkeypatch.setattr(module, "_dynamic_discovery_done", True)
    monkeypatch.setattr(module, "validate_toolset", lambda name: True)
    monkeypatch.setattr(module, "resolve_toolset", fake_resolve_toolset)
    monkeypatch.setattr(module.registry, "get_definitions", lambda names, quiet=False: [])

    module._toolset_resolution_cache.clear()

    try:
        module.get_tool_definitions(enabled_toolsets=["filesystem"], quiet_mode=True)
        module.get_tool_definitions(enabled_toolsets=["filesystem"], quiet_mode=True)

        assert calls == ["filesystem"]
    finally:
        module._toolset_resolution_cache.clear()


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "INF", "-inf", "+inf"])
def test_coerce_number_does_not_convert_nonfinite_values(raw):
    import model_tools as module
    assert module._coerce_number(raw) == raw


# =========================================================================
# handle_function_call
# =========================================================================

class TestHandleFunctionCall:
    def test_agent_loop_tool_returns_error(self):
        for tool_name in _AGENT_LOOP_TOOLS:
            result = json.loads(handle_function_call(tool_name, {}))
            assert "error" in result
            assert "agent loop" in result["error"].lower()

    def test_unknown_tool_returns_error(self):
        result = json.loads(handle_function_call("totally_fake_tool_xyz", {}))
        assert "error" in result
        assert "totally_fake_tool_xyz" in result["error"]

    def test_exception_returns_json_error(self):
        # Even if something goes wrong, should return valid JSON
        result = handle_function_call("web_search", None)  # None args may cause issues
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        assert "error" in parsed
        assert len(parsed["error"]) > 0
        assert "error" in parsed["error"].lower() or "failed" in parsed["error"].lower()

    def test_tool_hooks_receive_session_and_tool_call_ids(self):
        with (
            patch("model_tools.registry.dispatch", return_value='{"ok":true}'),
            patch("hermes_cli.plugins.invoke_hook") as mock_invoke_hook,
        ):
            result = handle_function_call(
                "web_search",
                {"q": "test"},
                task_id="task-1",
                tool_call_id="call-1",
                session_id="session-1",
            )

        assert result == '{"ok":true}'
        assert mock_invoke_hook.call_args_list == [
            call(
                "pre_tool_call",
                tool_name="web_search",
                args={"q": "test"},
                task_id="task-1",
                session_id="session-1",
                tool_call_id="call-1",
            ),
            call(
                "post_tool_call",
                tool_name="web_search",
                args={"q": "test"},
                result='{"ok":true}',
                task_id="task-1",
                session_id="session-1",
                tool_call_id="call-1",
            ),
        ]

    def test_skip_pre_tool_call_hook_is_accepted(self):
        with (
            patch("model_tools.registry.dispatch", return_value='{"ok":true}'),
            patch("hermes_cli.plugins.invoke_hook") as mock_invoke_hook,
        ):
            result = handle_function_call(
                "web_search",
                {"q": "test"},
                task_id="task-1",
                tool_call_id="call-1",
                session_id="session-1",
                skip_pre_tool_call_hook=True,
            )

        assert result == '{"ok":true}'
        assert mock_invoke_hook.call_args_list == [
            call(
                "post_tool_call",
                tool_name="web_search",
                args={"q": "test"},
                result='{"ok":true}',
                task_id="task-1",
                session_id="session-1",
                tool_call_id="call-1",
            ),
        ]


# =========================================================================
# Agent loop tools
# =========================================================================

class TestAgentLoopTools:
    def test_expected_tools_in_set(self):
        assert "todo" in _AGENT_LOOP_TOOLS
        assert "memory" in _AGENT_LOOP_TOOLS
        assert "session_search" in _AGENT_LOOP_TOOLS
        assert "delegate_task" in _AGENT_LOOP_TOOLS

    def test_no_regular_tools_in_set(self):
        assert "web_search" not in _AGENT_LOOP_TOOLS
        assert "terminal" not in _AGENT_LOOP_TOOLS


# =========================================================================
# Legacy toolset map
# =========================================================================

class TestLegacyToolsetMap:
    def test_expected_legacy_names(self):
        expected = [
            "web_tools", "terminal_tools", "vision_tools", "moa_tools",
            "image_tools", "skills_tools", "browser_tools", "cronjob_tools",
            "rl_tools", "file_tools", "tts_tools",
        ]
        for name in expected:
            assert name in _LEGACY_TOOLSET_MAP, f"Missing legacy toolset: {name}"

    def test_values_are_lists_of_strings(self):
        for name, tools in _LEGACY_TOOLSET_MAP.items():
            assert isinstance(tools, list), f"{name} is not a list"
            for tool in tools:
                assert isinstance(tool, str), f"{name} contains non-string: {tool}"


# =========================================================================
# Backward-compat wrappers
# =========================================================================

class TestBackwardCompat:
    def test_get_all_tool_names_returns_list(self):
        names = get_all_tool_names()
        assert isinstance(names, list)
        assert len(names) > 0
        # Should contain well-known tools
        assert "web_search" in names
        assert "terminal" in names

    def test_get_toolset_for_tool(self):
        result = get_toolset_for_tool("web_search")
        assert result is not None
        assert isinstance(result, str)

    def test_get_toolset_for_unknown_tool(self):
        result = get_toolset_for_tool("totally_nonexistent_tool")
        assert result is None

    def test_tool_to_toolset_map(self):
        assert isinstance(TOOL_TO_TOOLSET_MAP, dict)
        assert len(TOOL_TO_TOOLSET_MAP) > 0
