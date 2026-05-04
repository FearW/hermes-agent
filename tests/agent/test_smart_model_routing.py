from agent.smart_model_routing import choose_cheap_model_route, resolve_turn_toolsets


_BASE_CONFIG = {
    "enabled": True,
    "cheap_model": {
        "provider": "openrouter",
        "model": "google/gemini-2.5-flash",
    },
}


def test_returns_none_when_disabled():
    cfg = {**_BASE_CONFIG, "enabled": False}
    assert choose_cheap_model_route("what time is it in tokyo?", cfg) is None


def test_routes_short_simple_prompt():
    result = choose_cheap_model_route("what time is it in tokyo?", _BASE_CONFIG)
    assert result is not None
    assert result["provider"] == "openrouter"
    assert result["model"] == "google/gemini-2.5-flash"
    assert result["routing_reason"] == "simple_turn"


def test_skips_long_prompt():
    prompt = "please summarize this carefully " * 20
    assert choose_cheap_model_route(prompt, _BASE_CONFIG) is None


def test_skips_code_like_prompt():
    prompt = "debug this traceback: ```python\nraise ValueError('bad')\n```"
    assert choose_cheap_model_route(prompt, _BASE_CONFIG) is None


def test_skips_tool_heavy_prompt_keywords():
    prompt = "implement a patch for this docker error"
    assert choose_cheap_model_route(prompt, _BASE_CONFIG) is None


def test_resolve_turn_route_falls_back_to_primary_when_route_runtime_cannot_be_resolved(monkeypatch):
    from agent.smart_model_routing import resolve_turn_route

    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("bad route")),
    )
    result = resolve_turn_route(
        "what time is it in tokyo?",
        _BASE_CONFIG,
        {
            "model": "anthropic/claude-sonnet-4",
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions",
            "api_key": "sk-primary",
        },
    )
    assert result["model"] == "anthropic/claude-sonnet-4"
    assert result["runtime"]["provider"] == "openrouter"
    assert result["label"] is None


def test_resolve_turn_route_marks_simple_turn_as_light(monkeypatch):
    from agent.smart_model_routing import resolve_turn_route

    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **kwargs: {
            "api_key": "sk-cheap",
            "base_url": "https://openrouter.ai/api/v1",
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
            "credential_pool": None,
        },
    )
    result = resolve_turn_route(
        "what time is it in tokyo?",
        _BASE_CONFIG,
        {
            "model": "anthropic/claude-sonnet-4",
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions",
            "api_key": "sk-primary",
        },
    )

    assert result["turn_complexity"] == "simple"
    assert result["task_mode"] == "light"
    assert result["signature"][-1] == "light"


def test_resolve_turn_route_marks_non_simple_primary_turn_as_heavy():
    from agent.smart_model_routing import resolve_turn_route

    result = resolve_turn_route(
        "implement a patch for this docker error",
        _BASE_CONFIG,
        {
            "model": "anthropic/claude-sonnet-4",
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions",
            "api_key": "sk-primary",
        },
    )

    assert result["model"] == "anthropic/claude-sonnet-4"
    assert result["turn_complexity"] == "complex"
    assert result["task_mode"] == "heavy"
    assert result["signature"][-1] == "heavy"


def test_resolve_turn_route_marks_general_turn_as_light():
    from agent.smart_model_routing import resolve_turn_route

    result = resolve_turn_route(
        "please summarize this note for me",
        _BASE_CONFIG,
        {
            "model": "anthropic/claude-sonnet-4",
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions",
            "api_key": "sk-primary",
        },
    )

    assert result["turn_complexity"] == "general"
    assert result["task_mode"] == "light"
    assert result["signature"][-1] == "light"


def test_resolve_turn_toolsets_light_turn_disables_all_tools():
    assert resolve_turn_toolsets({"task_mode": "light"}, ["core", "web"]) == []


def test_resolve_turn_toolsets_heavy_turn_grants_all_tools():
    assert resolve_turn_toolsets({"task_mode": "heavy"}, ["core"]) == ["all"]


def test_force_light_contains_overrides_other_complexity_signals(monkeypatch):
    from agent.smart_model_routing import resolve_turn_route

    cfg = {
        **_BASE_CONFIG,
        "force_light_contains": ["safe-shortcut"],
    }
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **kwargs: {
            "api_key": "sk-cheap",
            "base_url": "https://openrouter.ai/api/v1",
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
            "credential_pool": None,
        },
    )

    result = resolve_turn_route(
        "safe-shortcut debug this traceback and patch it",
        cfg,
        {
            "model": "anthropic/claude-sonnet-4",
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions",
            "api_key": "sk-primary",
        },
    )

    assert result["turn_complexity"] == "simple"
    assert result["task_mode"] == "light"


def test_force_heavy_contains_escalates_general_turn():
    from agent.smart_model_routing import resolve_turn_route

    cfg = {
        **_BASE_CONFIG,
        "force_heavy_contains": ["prod-hotfix"],
    }
    result = resolve_turn_route(
        "prod-hotfix please summarize the current issue",
        cfg,
        {
            "model": "anthropic/claude-sonnet-4",
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions",
            "api_key": "sk-primary",
        },
    )

    assert result["turn_complexity"] == "complex"
    assert result["task_mode"] == "heavy"


def test_route_modes_can_promote_general_turns_to_heavy():
    from agent.smart_model_routing import resolve_turn_route

    cfg = {
        **_BASE_CONFIG,
        "route_modes": {
            "simple": "light",
            "general": "heavy",
            "complex": "heavy",
        },
    }
    result = resolve_turn_route(
        "please summarize this note for me",
        cfg,
        {
            "model": "anthropic/claude-sonnet-4",
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_mode": "chat_completions",
            "api_key": "sk-primary",
        },
    )

    assert result["turn_complexity"] == "general"
    assert result["task_mode"] == "heavy"
