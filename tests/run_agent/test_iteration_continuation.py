from run_agent import AIAgent, IterationBudget


def _make_agent(decision, *, task_mode="heavy", enabled_toolsets=None):
    agent = object.__new__(AIAgent)
    agent.max_iterations = 60
    agent.max_iterations_with_approval = 150
    agent.iteration_extension_step = 30
    agent.iteration_budget = IterationBudget(60)
    agent.quiet_mode = True
    agent._emit_status = lambda *_args, **_kwargs: None
    agent._safe_print = lambda *_args, **_kwargs: None
    agent.task_mode = task_mode
    agent.enabled_toolsets = ["all"] if enabled_toolsets is None else enabled_toolsets
    agent._continuation_policy = {
        "enabled": True,
        "auto_task_modes": ["heavy"],
        "legacy_toolset_fallback": True,
        "manual_fallback": False,
        "require_tool_activity": False,
    }
    agent._turn_used_tools = True
    agent.continuation_callback = lambda payload: decision(payload)
    return agent


def test_iteration_continuation_approval_extends_budget():
    agent = _make_agent(lambda _payload: "approve")

    approved = agent._maybe_extend_iteration_budget(60)

    assert approved is True
    assert agent.max_iterations == 90
    assert agent.iteration_budget.max_total == 90


def test_iteration_continuation_denial_keeps_budget():
    agent = _make_agent(lambda _payload: "deny", task_mode="light", enabled_toolsets=[])

    approved = agent._maybe_extend_iteration_budget(60)

    assert approved is False
    assert agent.max_iterations == 60
    assert agent.iteration_budget.max_total == 60


def test_iteration_continuation_auto_extends_for_heavy_mode():
    called = {"count": 0}

    def _decision(_payload):
        called["count"] += 1
        return "deny"

    agent = _make_agent(_decision, task_mode="heavy")

    approved = agent._maybe_extend_iteration_budget(60)

    assert approved is True
    assert called["count"] == 0
    assert agent.max_iterations == 90
    assert agent.iteration_budget.max_total == 90


def test_iteration_continuation_light_mode_does_not_auto_extend():
    called = {"count": 0}

    def _decision(_payload):
        called["count"] += 1
        return "deny"

    agent = _make_agent(_decision, task_mode="light", enabled_toolsets=[])

    approved = agent._maybe_extend_iteration_budget(60)

    assert approved is False
    assert called["count"] == 0
    assert agent.max_iterations == 60
    assert agent.iteration_budget.max_total == 60


def test_iteration_continuation_light_mode_stays_off_even_with_legacy_fallback():
    agent = _make_agent(lambda _payload: "approve", task_mode="light", enabled_toolsets=["web"])

    approved = agent._maybe_extend_iteration_budget(60)

    assert approved is False
    assert agent.max_iterations == 60


def test_iteration_continuation_policy_can_disable_auto_extend():
    agent = _make_agent(lambda _payload: "deny", task_mode="heavy")
    agent._continuation_policy = {
        "enabled": False,
        "auto_task_modes": ["heavy"],
        "legacy_toolset_fallback": True,
        "manual_fallback": False,
        "require_tool_activity": False,
    }

    approved = agent._maybe_extend_iteration_budget(60)

    assert approved is False
    assert agent.max_iterations == 60
    assert agent.iteration_budget.max_total == 60


def test_iteration_continuation_can_require_tool_activity():
    agent = _make_agent(lambda _payload: "deny", task_mode="heavy")
    agent._continuation_policy = {
        "enabled": True,
        "auto_task_modes": ["heavy"],
        "legacy_toolset_fallback": True,
        "manual_fallback": False,
        "require_tool_activity": True,
    }
    agent._turn_used_tools = False

    approved = agent._maybe_extend_iteration_budget(60)

    assert approved is False
    assert agent.max_iterations == 60
    assert agent.iteration_budget.max_total == 60


def test_iteration_continuation_manual_fallback_requires_callback():
    agent = _make_agent(lambda _payload: "approve", task_mode="light", enabled_toolsets=[])
    agent._continuation_policy = {
        "enabled": True,
        "auto_task_modes": [],
        "legacy_toolset_fallback": False,
        "manual_fallback": True,
        "require_tool_activity": False,
    }

    approved = agent._maybe_extend_iteration_budget(60)

    assert approved is True
    assert agent.max_iterations == 90


def test_iteration_continuation_auto_extend_stops_at_hard_cap():
    agent = _make_agent(lambda _payload: "deny", task_mode="heavy")

    assert agent._maybe_extend_iteration_budget(60) is True
    assert agent.max_iterations == 90
    assert agent._maybe_extend_iteration_budget(90) is True
    assert agent.max_iterations == 120
    assert agent._maybe_extend_iteration_budget(120) is True
    assert agent.max_iterations == 150
    assert agent._maybe_extend_iteration_budget(150) is False
    assert agent.iteration_budget.max_total == 150
