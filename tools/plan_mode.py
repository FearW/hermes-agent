"""Central classification of which tools a Plan-mode advisor may call.

Plan mode is Hermes' analogue of Claude Code's Opus Plan mode: the advisor
investigates the request using read-only tools, produces a plan, and waits
for the user to approve or reject before the executor runs any destructive
action.

The classification lives in one place (this module) rather than being
scattered across 30+ tool files.  Each name below maps to a registered
tool in :mod:`tools.registry`.  Tools not listed here default to
``read_only=False`` — so new tools are denied by default (fail-safe).

Call :func:`apply_classification` once after all tools have registered
(done by :mod:`model_tools`).
"""
from __future__ import annotations

import logging
from typing import FrozenSet

from tools.registry import registry

logger = logging.getLogger(__name__)


# Tools the planner is allowed to invoke.  Observation-only, no side effects.
PLANNER_READ_ONLY_TOOLS: FrozenSet[str] = frozenset({
    # file / search
    "read_file",
    "search_files",
    "session_search",

    # web
    "web_search",
    "web_extract",

    # skills inventory (view only; skill_manage mutates, so excluded)
    "skills_list",
    "skill_view",

    # vision (analyze, does not generate)
    "vision_analyze",

    # browser observation (navigation/click/type are destructive)
    "browser_snapshot",
    "browser_console",
    "browser_get_images",
    "browser_vision",

    # home assistant state (call_service is destructive)
    "ha_get_state",
    "ha_list_entities",
    "ha_list_services",

    # rl inspection (start/stop/edit are destructive)
    "rl_check_status",
    "rl_get_current_config",
    "rl_get_results",
    "rl_list_environments",
    "rl_list_runs",

    # mcp-minimax read APIs
    "mcp_minimax_list_prompts",
    "mcp_minimax_get_prompt",
    "mcp_minimax_list_resources",
    "mcp_minimax_read_resource",
    "mcp_minimax_web_search",
    "mcp_minimax_understand_image",

    # user-interaction tools that don't change state
    "clarify",

    # the agent's own scratchpad — writes stay within the conversation
    "memory",
    "todo",
})


def apply_classification() -> int:
    """Flag :data:`PLANNER_READ_ONLY_TOOLS` on the registry.

    Returns the number of tools successfully flagged.  Names that do not
    correspond to a registered tool are logged at debug level (typical
    cause: the owning toolset is not active in this runtime).
    """
    hit = registry.mark_read_only(PLANNER_READ_ONLY_TOOLS)
    missed = sorted(PLANNER_READ_ONLY_TOOLS - registry.read_only_names())
    if missed:
        logger.debug(
            "plan_mode: %d/%d tool(s) not present in registry: %s",
            len(missed), len(PLANNER_READ_ONLY_TOOLS), ", ".join(missed),
        )
    return hit


def planner_allowed_tools() -> FrozenSet[str]:
    """Current snapshot of planner-allowed tool names (after classification)."""
    return frozenset(registry.read_only_names())
