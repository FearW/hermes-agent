"""Helpers for optional cheap-vs-strong model routing."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from utils import is_truthy_value

_COMPLEX_KEYWORDS = {
    "debug",
    "debugging",
    "implement",
    "implementation",
    "refactor",
    "patch",
    "traceback",
    "stacktrace",
    "exception",
    "error",
    "analyze",
    "analysis",
    "investigate",
    "architecture",
    "design",
    "compare",
    "benchmark",
    "optimize",
    "optimise",
    "review",
    "terminal",
    "shell",
    "tool",
    "tools",
    "pytest",
    "test",
    "tests",
    "plan",
    "planning",
    "delegate",
    "subagent",
    "cron",
    "docker",
    "kubernetes",
}

_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)

_GENERAL_KEYWORDS = {
    "build", "create", "write", "draft", "summarize", "summarise",
    "translate", "explain", "research", "compare", "plan", "steps",
    "outline", "improve", "edit", "revise", "整理", "总结", "翻译",
    "解释", "方案", "建议",
}

_COMPLEX_TASK_KEYWORDS = {
    "debug", "debugging", "implement", "implementation", "refactor", "patch",
    "traceback", "stacktrace", "exception", "error", "analyze", "analysis",
    "investigate", "architecture", "design", "benchmark", "optimize", "optimise",
    "review", "terminal", "shell", "tool", "tools", "pytest", "test", "tests",
    "delegate", "subagent", "cron", "docker", "kubernetes", "deploy", "migration",
    "rollback", "incident", "production", "fix", "hotfix", "cli", "gateway",
    "代码", "修复", "重构", "排查", "报错", "异常", "实现", "部署", "上线",
    "工具", "终端", "脚本", "备份", "恢复", "复盘", "优化", "可行性分析",
    "落地", "自动", "外部程序", "15分钟", "超时", "回滚",
}

_GENERAL_HINT_TOKENS = {"总结", "翻译", "解释", "方案", "建议", "整理"}
_COMPLEX_HINT_TOKENS = {
    "代码", "修复", "重构", "排查", "报错", "异常", "实现", "部署", "上线",
    "工具", "终端", "脚本", "备份", "恢复", "复盘", "优化", "可行性分析",
    "落地", "自动", "外部程序", "15分钟", "超时", "回滚",
}
_DEFAULT_ROUTE_MODES = {"simple": "light", "general": "light", "complex": "heavy"}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    return is_truthy_value(value, default=default)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        out: List[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    return []


def _merged_keyword_set(base: set[str], extra_value: Any) -> set[str]:
    extras = {item.lower() for item in _coerce_str_list(extra_value)}
    return set(base) | extras


def _contains_any(text: str, tokens: Any) -> bool:
    return any(token and token in text for token in _coerce_str_list(tokens))


def _resolve_route_modes(routing_config: Optional[Dict[str, Any]]) -> Dict[str, str]:
    cfg = routing_config or {}
    configured = cfg.get("route_modes")
    if not isinstance(configured, dict):
        return dict(_DEFAULT_ROUTE_MODES)

    resolved = dict(_DEFAULT_ROUTE_MODES)
    for key in ("simple", "general", "complex"):
        value = str(configured.get(key) or "").strip().lower()
        if value in {"light", "heavy", "inherit"}:
            resolved[key] = value
    return resolved


def _primary_route(primary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "model": primary.get("model"),
        "runtime": {
            "api_key": primary.get("api_key"),
            "base_url": primary.get("base_url"),
            "provider": primary.get("provider"),
            "api_mode": primary.get("api_mode"),
            "command": primary.get("command"),
            "args": list(primary.get("args") or []),
            "credential_pool": primary.get("credential_pool"),
        },
        "label": None,
        "signature": (
            primary.get("model"),
            primary.get("provider"),
            primary.get("base_url"),
            primary.get("api_mode"),
            primary.get("command"),
            tuple(primary.get("args") or ()),
        ),
    }


def _classify_turn_complexity(
    user_message: str, routing_config: Optional[Dict[str, Any]]
) -> str:
    """Classify a turn into simple / general / complex using strict rules."""
    cfg = routing_config or {}
    text = (user_message or "").strip()
    if not text:
        return "general"

    max_chars = _coerce_int(cfg.get("max_simple_chars"), 160)
    max_words = _coerce_int(cfg.get("max_simple_words"), 28)
    max_lines = _coerce_int(cfg.get("max_simple_lines"), 2)
    min_complex_chars = _coerce_int(cfg.get("min_complex_chars"), max(max_chars * 2, 320))
    min_complex_words = _coerce_int(cfg.get("min_complex_words"), max(max_words * 2, 60))
    min_complex_lines = _coerce_int(cfg.get("min_complex_lines"), 6)
    min_complex_sentences = _coerce_int(cfg.get("min_complex_sentences"), 4)
    min_complex_chars_with_many_sentences = _coerce_int(
        cfg.get("min_complex_chars_with_many_sentences"),
        180,
    )
    lowered = text.lower()
    words = {token.strip(".,:;!?()[]{}\"'`") for token in lowered.split()}
    general_keywords = _merged_keyword_set(_GENERAL_KEYWORDS, cfg.get("general_keywords"))
    complex_keywords = _merged_keyword_set(_COMPLEX_TASK_KEYWORDS, cfg.get("complex_keywords"))

    line_count = text.count("\n") + 1
    char_count = len(text)
    word_count = len([w for w in lowered.split() if w.strip()])
    has_code_block = "```" in text or "`" in text
    has_url = bool(_URL_RE.search(text))
    has_general_keywords = bool(words & general_keywords) or _contains_any(
        text,
        list(_GENERAL_HINT_TOKENS) + _coerce_str_list(cfg.get("force_general_contains")),
    )
    has_complex_keywords = bool(words & complex_keywords) or _contains_any(
        text,
        list(_COMPLEX_HINT_TOKENS) + _coerce_str_list(cfg.get("force_heavy_contains")),
    )
    has_question_chain = text.count("?") >= 2 or text.count("？") >= 2
    has_many_sentences = sum(text.count(mark) for mark in ".!?。！？") >= min_complex_sentences
    has_path_like = "/" in text or "\\" in text
    has_constraint_chain = _contains_any(
        text,
        [
            "并且", "如果", "若是", "要是", "然后", "成功了", "失败", "完成",
            "自动恢复", "不断", "每次",
        ] + _coerce_str_list(cfg.get("constraint_contains")),
    ) and char_count >= _coerce_int(cfg.get("min_constraint_chars"), 120)
    has_timeout_or_recovery = _contains_any(
        text,
        ["15分钟", "超时", "恢复", "备份", "回滚", "拉起"]
        + _coerce_str_list(cfg.get("timeout_or_recovery_contains")),
    )
    if _contains_any(text, cfg.get("force_light_contains")):
        return "simple"
    if _contains_any(text, cfg.get("force_heavy_contains")):
        return "complex"

    if (
        char_count <= max_chars
        and word_count <= max_words
        and line_count <= max_lines
        and not has_code_block
        and not has_url
        and not has_general_keywords
        and not has_complex_keywords
        and not has_question_chain
        and not has_many_sentences
        and not has_path_like
    ):
        return "simple"

    if (
        char_count >= min_complex_chars
        or word_count >= min_complex_words
        or line_count >= min_complex_lines
        or has_code_block
        or has_url
        or has_complex_keywords
        or has_question_chain
        or has_path_like
        or has_constraint_chain
        or has_timeout_or_recovery
        or (char_count >= min_complex_chars_with_many_sentences and has_many_sentences)
    ):
        return "complex"

    return "general"


def _looks_simple_turn(
    user_message: str, routing_config: Optional[Dict[str, Any]]
) -> bool:
    return _classify_turn_complexity(user_message, routing_config) == "simple"


def _build_runtime_from_primary(
    primary: Dict[str, Any], model_cfg: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    provider = (
        str(model_cfg.get("provider") or primary.get("provider") or "").strip().lower()
    )
    model = str(model_cfg.get("model") or "").strip()
    if not provider or not model:
        return None

    if provider == str(primary.get("provider") or "").strip().lower() and str(
        primary.get("base_url") or ""
    ) == str(model_cfg.get("base_url") or primary.get("base_url") or ""):
        from hermes_cli.auth import has_usable_secret
        _fp_api_key = primary.get("api_key")
        _fp_key_env = str(model_cfg.get("api_key_env") or "").strip()
        if _fp_key_env:
            _env_val = os.getenv(_fp_key_env) or None
            if _env_val and has_usable_secret(_env_val):
                _fp_api_key = _env_val
        elif not has_usable_secret(_fp_api_key or ""):
            if model_cfg.get("api_key"):
                _fp_api_key = str(model_cfg.get("api_key"))
        return {
            "model": model,
            "runtime": {
                "api_key": _fp_api_key,
                "base_url": model_cfg.get("base_url") or primary.get("base_url"),
                "provider": provider,
                "api_mode": model_cfg.get("api_mode") or primary.get("api_mode"),
                "command": primary.get("command"),
                "args": list(primary.get("args") or []),
                "credential_pool": primary.get("credential_pool"),
            },
        }

    from hermes_cli.runtime_provider import resolve_runtime_provider

    explicit_api_key = None
    api_key_env = str(model_cfg.get("api_key_env") or "").strip()
    if api_key_env:
        explicit_api_key = os.getenv(api_key_env) or None
    elif model_cfg.get("api_key"):
        explicit_api_key = str(model_cfg.get("api_key"))

    runtime = resolve_runtime_provider(
        requested=provider,
        explicit_api_key=explicit_api_key,
        explicit_base_url=model_cfg.get("base_url"),
    )
    return {
        "model": model,
        "runtime": {
            "api_key": runtime.get("api_key"),
            "base_url": runtime.get("base_url"),
            "provider": runtime.get("provider"),
            "api_mode": model_cfg.get("api_mode") or runtime.get("api_mode"),
            "command": runtime.get("command"),
            "args": list(runtime.get("args") or []),
            "credential_pool": runtime.get("credential_pool"),
        },
    }


def _build_signature(model: str, runtime: Dict[str, Any]) -> tuple:
    return (
        model,
        runtime.get("provider"),
        runtime.get("base_url"),
        runtime.get("api_mode"),
        runtime.get("command"),
        tuple(runtime.get("args") or ()),
    )


def _route_task_mode(
    routing_config: Optional[Dict[str, Any]],
    turn_complexity: Optional[str],
) -> str:
    """Return ``light`` / ``heavy`` / ``inherit`` for this turn.

    Mode mapping is configurable via ``smart_model_routing.route_modes``.
    """
    cfg = routing_config or {}
    if not _coerce_bool(cfg.get("enabled"), False):
        return "inherit"
    modes = _resolve_route_modes(cfg)
    return modes.get(str(turn_complexity or "general").strip().lower(), "light")


def resolve_turn_toolsets(
    route: Optional[Dict[str, Any]],
    default_toolsets: Optional[List[str]],
) -> Optional[List[str]]:
    """Resolve the effective toolset list for a routed turn.

    ``light``  => no tools at all (basic Q&A only)
    ``heavy``  => full tool + MCP + skills access
    ``inherit`` => keep the caller's configured toolsets unchanged
    """
    mode = str((route or {}).get("task_mode") or "inherit").strip().lower()
    if mode == "light":
        return []
    if mode == "heavy":
        return ["all"]
    if default_toolsets is None:
        return None
    return list(default_toolsets)


def choose_cheap_model_route(
    user_message: str, routing_config: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Return the configured cheap-model route when a message looks simple.

    Conservative by design: if the message has signs of code/tool/debugging/
    long-form work, keep the primary model.
    """
    cfg = routing_config or {}
    if not _coerce_bool(cfg.get("enabled"), False):
        return None

    cheap_model = cfg.get("cheap_model") or {}
    if not isinstance(cheap_model, dict):
        return None
    provider = str(cheap_model.get("provider") or "").strip().lower()
    model = str(cheap_model.get("model") or "").strip()
    if not provider or not model:
        return None

    if not _looks_simple_turn(user_message, cfg):
        return None

    route = dict(cheap_model)
    route["provider"] = provider
    route["model"] = model
    route["routing_reason"] = "simple_turn"
    return route


def choose_executor_route(
    user_message: str,
    routing_config: Optional[Dict[str, Any]],
    primary: Dict[str, Any],
    executor_override: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    cfg = routing_config or {}
    if not _coerce_bool(cfg.get("enabled"), False):
        return None

    executor_cfg = dict(cfg.get("executor_model") or {})
    if not isinstance(executor_cfg, dict):
        return None

    if isinstance(executor_override, dict):
        for key in (
            "model",
            "provider",
            "base_url",
            "api_key",
            "api_key_env",
            "api_mode",
        ):
            value = executor_override.get(key)
            if value is not None:
                executor_cfg[key] = value

    executor_route = _build_runtime_from_primary(primary, executor_cfg)
    if not executor_route:
        return None

    complexity = _classify_turn_complexity(user_message, cfg)
    route = {
        "model": executor_route["model"],
        "runtime": executor_route["runtime"],
        "label": f"{complexity} → {executor_route['model']} ({executor_route['runtime'].get('provider')})",
        "signature": _build_signature(
            executor_route["model"], executor_route["runtime"]
        ),
        "routing_reason": complexity,
        "turn_complexity": complexity,
    }
    return route


def resolve_turn_route(
    user_message: str,
    routing_config: Optional[Dict[str, Any]],
    primary: Dict[str, Any],
    executor_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve the effective model/runtime for one turn.

    Returns a dict with model/runtime/signature/label fields.
    """
    cfg = routing_config or {}
    turn_complexity = (
        _classify_turn_complexity(user_message, cfg)
        if _coerce_bool(cfg.get("enabled"), False)
        else None
    )
    strategy = str(cfg.get("strategy") or "cheap_model").strip().lower()
    if strategy == "executor":
        route = choose_executor_route(
            user_message,
            cfg,
            primary,
            executor_override=executor_override,
        )
        if route:
            route.setdefault(
                "label",
                f"{route.get('turn_complexity') or 'general'} → {route.get('model')} ({route.get('runtime', {}).get('provider')})",
            )
            route.setdefault(
                "signature",
                _build_signature(route.get("model"), route.get("runtime") or {}),
            )
            route.setdefault("routing_reason", route.get("turn_complexity") or "general")
            route.setdefault("turn_complexity", route.get("routing_reason") or "general")
            turn_complexity = route.get("turn_complexity") or turn_complexity
            result = route
        else:
            result = _primary_route(primary)
    else:
        route = choose_cheap_model_route(user_message, cfg)
        if not route:
            result = _primary_route(primary)
        else:
            from hermes_cli.runtime_provider import resolve_runtime_provider

            explicit_api_key = None
            api_key_env = str(route.get("api_key_env") or "").strip()
            if api_key_env:
                explicit_api_key = os.getenv(api_key_env) or None

            try:
                runtime = resolve_runtime_provider(
                    requested=route.get("provider"),
                    explicit_api_key=explicit_api_key,
                    explicit_base_url=route.get("base_url"),
                )
            except Exception:
                result = _primary_route(primary)
            else:
                result = {
                    "model": route.get("model"),
                    "runtime": {
                        "api_key": runtime.get("api_key"),
                        "base_url": runtime.get("base_url"),
                        "provider": runtime.get("provider"),
                        "api_mode": runtime.get("api_mode"),
                        "command": runtime.get("command"),
                        "args": list(runtime.get("args") or []),
                        "credential_pool": runtime.get("credential_pool"),
                    },
                    "label": f"smart route → {route.get('model')} ({runtime.get('provider')})",
                    "signature": _build_signature(route.get("model"), runtime),
                }
                result["routing_reason"] = route.get("routing_reason")
                result["turn_complexity"] = (
                    turn_complexity
                    or route.get("turn_complexity")
                    or route.get("routing_reason")
                )
                turn_complexity = result.get("turn_complexity") or turn_complexity

    if turn_complexity and not result.get("turn_complexity"):
        result["turn_complexity"] = turn_complexity
    task_mode = _route_task_mode(cfg, result.get("turn_complexity") or turn_complexity)
    result["task_mode"] = task_mode
    signature = tuple(result.get("signature") or _build_signature(result.get("model"), result.get("runtime") or {}))
    if not signature or signature[-1] != task_mode:
        result["signature"] = signature + (task_mode,)
    return result
