"""Anthropic prompt caching (stable prefix strategy).

Reduces input token costs by ~75% on multi-turn conversations by caching
the conversation prefix. Uses up to 4 cache_control breakpoints (Anthropic max):
  1. Tools array (optional, caches the whole tools schema)
  2. System prompt (stable across all turns)
  3-4. Last 1-2 cacheable, stable non-system messages (rolling window)

Tools-level caching matters for long sessions that get context-compressed
mid-run: after compression the system prompt rebuilds but tools usually
don't, so a dedicated tools breakpoint keeps the (typically 5-15K token)
tools schema cached even when the system cache is invalidated.

Pure functions -- no class state, no AIAgent dependency.
"""

import copy
from typing import Any, Dict, List, Optional


def _apply_cache_marker(msg: dict, cache_marker: dict, native_anthropic: bool = False) -> None:
    """Add cache_control to a single message, handling all format variations.

    Mutates ``msg`` in place. Only the specific fields we touch need to
    survive the call -- ``msg`` itself can be a shallow copy of the caller's
    dict, but any container we mutate (the ``content`` list, the last content
    block) must be local so we don't bleed cache_control into the caller's
    persistent history.
    """
    role = msg.get("role", "")
    content = msg.get("content")

    if role == "tool":
        if native_anthropic:
            msg["cache_control"] = cache_marker
        return

    if content is None or content == "":
        msg["cache_control"] = cache_marker
        return

    if isinstance(content, str):
        msg["content"] = [
            {"type": "text", "text": content, "cache_control": cache_marker}
        ]
        return

    if isinstance(content, list) and content:
        # Re-bind the content list and its last block so upstream persistent
        # history is never mutated. Non-last blocks stay shared-by-reference
        # (they aren't touched here).
        new_content = list(content)
        last = new_content[-1]
        if isinstance(last, dict):
            new_content[-1] = {**last, "cache_control": cache_marker}
            msg["content"] = new_content


def _cache_marker(cache_ttl: str) -> Dict[str, Any]:
    """Return a fresh ephemeral cache_control marker dict."""
    marker: Dict[str, Any] = {"type": "ephemeral"}
    if cache_ttl == "1h":
        marker["ttl"] = "1h"
    return marker


def split_system_for_cache(
    active_system: str,
    ephemeral_system: Optional[str],
    cache_ttl: str = "5m",
) -> Optional[List[Dict[str, Any]]]:
    """Return a system content list with cache_control on the stable block.

    When both *active_system* and *ephemeral_system* are present, returns
    ``[{stable text + cache_control}, {ephemeral text}]``. The cache marker
    sits on the stable block, so cache_read covers the stable prefix and
    ephemeral changes don't invalidate the system cache.

    Returns ``None`` when ``ephemeral_system`` is empty — fall back to the
    legacy single-string path so we keep one breakpoint free for messages
    (no benefit from splitting when there's nothing dynamic to isolate).
    """
    if not ephemeral_system:
        return None
    if not active_system:
        return None
    return [
        {
            "type": "text",
            "text": active_system,
            "cache_control": _cache_marker(cache_ttl),
        },
        {"type": "text", "text": ephemeral_system},
    ]


def apply_tool_cache_control(
    tools: Optional[List[Dict[str, Any]]],
    cache_ttl: str = "5m",
) -> Optional[List[Dict[str, Any]]]:
    """Return a new tools list with ``cache_control`` on the last entry.

    The tools schema is the stablest part of the prompt prefix: it changes
    only when toolsets are explicitly enabled/disabled.  Placing a
    breakpoint on the last tool creates a dedicated cache hit for the
    entire tools array that survives system-prompt rebuilds
    (e.g. context compression). Returns ``None`` unchanged when *tools*
    is falsy.

    The caller's list + tool dicts are NOT mutated: we shallow-copy both
    the list and the last tool entry. The rest of the entries stay
    shared-by-reference.
    """
    if not tools:
        return tools
    out = list(tools)
    last = out[-1]
    if not isinstance(last, dict):
        return out
    out[-1] = {**last, "cache_control": _cache_marker(cache_ttl)}
    return out


def apply_anthropic_cache_control(
    api_messages: List[Dict[str, Any]],
    cache_ttl: str = "5m",
    native_anthropic: bool = False,
    volatile_message_indices: set[int] | None = None,
    reserve_tools_breakpoint: bool = False,
    skip_system_marker: bool = False,
) -> List[Dict[str, Any]]:
    """Apply cache_control breakpoints to messages for Anthropic models.

    Places breakpoints on: (optional tools breakpoint reserved by the caller)
    + system prompt + last N cacheable, stable non-system messages. Volatile
    messages are skipped so dynamic per-call context (memory recall, plugin
    injections, prefills) does not consume a breakpoint that will miss on
    the next turn.

    Args:
        reserve_tools_breakpoint: When True, reserves 1 of the 4 Anthropic
            cache_control slots for the tools array (applied separately via
            ``apply_tool_cache_control``), leaving 3 for system + last 2
            messages. When False, falls back to the legacy system_and_3
            layout (system + last 3 messages).
        skip_system_marker: When True, do not apply a cache_control marker
            to the system message — the caller has already placed one
            (typically on a stable block of a split [stable, ephemeral]
            system layout). Still reserves the breakpoint budget slot.

    Returns:
        A shallow copy of *api_messages* with cache_control injected on
        the messages selected. Message dicts we actually mutate are
        themselves shallow-copied first so the caller's persistent
        conversation history is never touched.
    """
    if not api_messages:
        return []

    # Shallow-copy the list so we can swap out specific dicts without
    # disturbing the caller. Messages we don't touch stay shared-by-ref.
    messages = list(api_messages)
    marker = _cache_marker(cache_ttl)
    breakpoints_used = 1 if reserve_tools_breakpoint else 0

    if messages[0].get("role") == "system":
        if not skip_system_marker:
            messages[0] = dict(messages[0])
            _apply_cache_marker(messages[0], marker, native_anthropic=native_anthropic)
        breakpoints_used += 1

    remaining = max(0, 4 - breakpoints_used)
    volatile_message_indices = volatile_message_indices or set()
    non_sys: list[int] = []
    for i in range(len(messages)):
        if i in volatile_message_indices:
            continue
        role = messages[i].get("role")
        if role == "system":
            continue
        if role == "tool" and not native_anthropic:
            continue
        non_sys.append(i)
    for idx in non_sys[-remaining:] if remaining else ():
        messages[idx] = dict(messages[idx])
        _apply_cache_marker(messages[idx], marker, native_anthropic=native_anthropic)

    return messages
