"""Core config load/save functions extracted from hermes_cli.config."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, Any, Optional

import yaml

# Resolved from hermes_cli.config at import time (import-at-end pattern)
from hermes_cli.config import (
    get_config_path,
    DEFAULT_CONFIG,
    ensure_hermes_home,
    is_managed,
    managed_error,
    _secure_file,
)


def _current_config_path() -> Path:
    """Resolve config path dynamically so tests/runtime overrides are honored."""
    import hermes_cli.config as config_pkg

    return config_pkg.get_config_path()


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, preserving nested defaults.

    Keys in *override* take precedence. If both values are dicts the merge
    recurses, so a user who overrides only ``tts.elevenlabs.voice_id`` will
    keep the default ``tts.elevenlabs.model_id`` intact.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _expand_env_vars(obj):
    """Recursively expand ``${VAR}`` references in config values.

    Only string values are processed; dict keys, numbers, booleans, and
    None are left untouched.  Unresolved references (variable not in
    ``os.environ``) are kept verbatim so callers can detect them.
    """
    if isinstance(obj, str):
        return re.sub(
            r"\${([^}]+)}",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            obj,
        )
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


def _normalize_root_model_keys(config: Dict[str, Any]) -> Dict[str, Any]:
    """Move stale root-level provider/base_url into model section.

    Some users (or older code) placed ``provider:`` and ``base_url:`` at the
    config root instead of inside ``model:``.  These root-level keys are only
    used as a fallback when the corresponding ``model.*`` key is empty — they
    never override an existing ``model.provider`` or ``model.base_url``.
    After migration the root-level keys are removed so they can't cause
    confusion on subsequent loads.
    """
    # Only act if there are root-level keys to migrate
    has_root = any(config.get(k) for k in ("provider", "base_url"))
    if not has_root:
        return config

    config = dict(config)
    model = config.get("model")
    if not isinstance(model, dict):
        model = {"default": model} if model else {}
        config["model"] = model

    for key in ("provider", "base_url"):
        root_val = config.get(key)
        if root_val and not model.get(key):
            model[key] = root_val
        config.pop(key, None)

    return config


def _normalize_max_turns_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize legacy root-level max_turns into agent.max_turns."""
    config = dict(config)
    agent_config = dict(config.get("agent") or {})

    if "max_turns" in config and "max_turns" not in agent_config:
        agent_config["max_turns"] = config["max_turns"]

    if "max_turns" not in agent_config:
        agent_config["max_turns"] = DEFAULT_CONFIG["agent"]["max_turns"]

    config["agent"] = agent_config
    config.pop("max_turns", None)
    return config


def read_raw_config() -> Dict[str, Any]:
    """Read ~/.hermes/config.yaml as-is, without merging defaults or migrating.

    Returns the raw YAML dict, or ``{}`` if the file doesn't exist or can't
    be parsed.  Use this for lightweight config reads where you just need a
    single value and don't want the overhead of ``load_config()``'s deep-merge
    + migration pipeline.
    """
    try:
        config_path = _current_config_path()
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception as exc:
        logging.getLogger(__name__).debug("Failed to read raw config: %s", exc)
    return {}


def load_config() -> Dict[str, Any]:
    """Load configuration from ~/.hermes/config.yaml."""
    import copy

    ensure_hermes_home()
    config_path = _current_config_path()

    config = copy.deepcopy(DEFAULT_CONFIG)

    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}

            if "max_turns" in user_config:
                agent_user_config = dict(user_config.get("agent") or {})
                if agent_user_config.get("max_turns") is None:
                    agent_user_config["max_turns"] = user_config["max_turns"]
                user_config["agent"] = agent_user_config
                user_config.pop("max_turns", None)

            config = _deep_merge(config, user_config)
        except Exception as e:
            print(f"Warning: Failed to load config: {e}")

    return _expand_env_vars(
        _normalize_root_model_keys(_normalize_max_turns_config(config))
    )


_SECURITY_COMMENT = """
# -- Security ----------------------------------------------------------
# API keys, tokens, and passwords are redacted from tool output by default.
# Set to false to see full values (useful for debugging auth issues).
# tirith pre-exec scanning is enabled by default when the tirith binary
# is available. Configure via security.tirith_* keys or env vars
# (TIRITH_ENABLED, TIRITH_BIN, TIRITH_TIMEOUT, TIRITH_FAIL_OPEN).
#
# security:
#   redact_secrets: false
#   tirith_enabled: true
#   tirith_path: "tirith"
#   tirith_timeout: 5
#   tirith_fail_open: true
"""

_FALLBACK_COMMENT = """
# -- Fallback Model ----------------------------------------------------
# Automatic provider failover when primary is unavailable.
# Uncomment and configure to enable. Triggers on rate limits (429),
# overload (529), service errors (503), or connection failures.
#
# Supported providers:
#   openrouter   (OPENROUTER_API_KEY)  - routes to any model
#   openai-codex (OAuth - hermes auth) - OpenAI Codex
#   nous         (OAuth - hermes auth) - Nous Portal
#   zai          (ZAI_API_KEY)         - Z.AI / GLM
#   kimi-coding  (KIMI_API_KEY)        - Kimi / Moonshot
#   minimax      (MINIMAX_API_KEY)     - MiniMax
#   minimax-cn   (MINIMAX_CN_API_KEY)  - MiniMax (China)
#
# For custom OpenAI-compatible endpoints, add base_url and api_key_env.
#
# fallback_model:
#   provider: openrouter
#   model: anthropic/claude-sonnet-4
#
# -- Smart Model Routing -----------------------------------------------
# Optional cheap-vs-strong routing for simple turns.
# Keeps the primary model for complex work, but can route short/simple
# messages to a cheaper model across providers.
#
# smart_model_routing:
#   enabled: true
#   max_simple_chars: 160
#   max_simple_words: 28
#   max_simple_lines: 2
#   min_complex_chars: 320
#   min_complex_words: 60
#   force_light_contains: ["现在几点", "今天天气"]
#   force_heavy_contains: ["debug", "patch", "terminal", "MCP"]
#   route_modes:
#     simple: light
#     general: light
#     complex: heavy
#   cheap_model:
#     provider: openrouter
#     model: google/gemini-2.5-flash
"""


_COMMENTED_SECTIONS = """
# -- Security ----------------------------------------------------------
# API keys, tokens, and passwords are redacted from tool output by default.
# Set to false to see full values (useful for debugging auth issues).
#
# security:
#   redact_secrets: false

# -- Fallback Model ----------------------------------------------------
# Automatic provider failover when primary is unavailable.
# Uncomment and configure to enable. Triggers on rate limits (429),
# overload (529), service errors (503), or connection failures.
#
# Supported providers:
#   openrouter   (OPENROUTER_API_KEY)  - routes to any model
#   openai-codex (OAuth - hermes auth) - OpenAI Codex
#   nous         (OAuth - hermes auth) - Nous Portal
#   zai          (ZAI_API_KEY)         - Z.AI / GLM
#   kimi-coding  (KIMI_API_KEY)        - Kimi / Moonshot
#   minimax      (MINIMAX_API_KEY)     - MiniMax
#   minimax-cn   (MINIMAX_CN_API_KEY)  - MiniMax (China)
#
# For custom OpenAI-compatible endpoints, add base_url and api_key_env.
#
# fallback_model:
#   provider: openrouter
#   model: anthropic/claude-sonnet-4
#
# -- Smart Model Routing -----------------------------------------------
# Optional cheap-vs-strong routing for simple turns.
# Keeps the primary model for complex work, but can route short/simple
# messages to a cheaper model across providers.
#
# smart_model_routing:
#   enabled: true
#   max_simple_chars: 160
#   max_simple_words: 28
#   max_simple_lines: 2
#   min_complex_chars: 320
#   min_complex_words: 60
#   force_light_contains: ["现在几点", "今天天气"]
#   force_heavy_contains: ["debug", "patch", "terminal", "MCP"]
#   route_modes:
#     simple: light
#     general: light
#     complex: heavy
#   cheap_model:
#     provider: openrouter
#     model: google/gemini-2.5-flash
"""


def save_config(config: Dict[str, Any]):
    """Save configuration to ~/.hermes/config.yaml."""
    if is_managed():
        managed_error("save configuration")
        return
    from utils import atomic_yaml_write

    ensure_hermes_home()
    config_path = get_config_path()
    normalized = _normalize_root_model_keys(_normalize_max_turns_config(config))

    # Build optional commented-out sections for features that are off by
    # default or only relevant when explicitly configured.
    parts = []
    sec = normalized.get("security", {})
    if not sec or sec.get("redact_secrets") is None:
        parts.append(_SECURITY_COMMENT)
    fb = normalized.get("fallback_model", {})
    if not fb or (isinstance(fb, dict) and not fb.get("model")):
        parts.append(_FALLBACK_COMMENT)

    atomic_yaml_write(
        config_path,
        normalized,
        extra_content="".join(parts) if parts else None,
    )
    _secure_file(config_path)


# Env file management -- extracted to config/env.py
from hermes_cli.config.env import (
    load_env,
    _sanitize_env_lines,
    sanitize_env_file,
    save_env_value,
    remove_env_value,
    save_anthropic_oauth_token,
    use_anthropic_claude_code_credentials,
    save_anthropic_api_key,
    save_env_value_secure,
    get_env_value,
)
