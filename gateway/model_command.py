"""`/model` slash-command handler — edit config.yaml from a chat message.

Mirrors the plan_mode_runtime design: pure functions, returns a synthetic
agent-result dict so the gateway can post it as a reply without invoking
the LLM. No messaging I/O.

Subcommands:
    /model show
    /model main <alias> <model>
    /model plan <alias> <model>
    /model plan off

Aliases mirror ``switch_model.py`` on the operator laptop. The config.yaml
edits are string-level to preserve comments and key order.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

CONFIG_PATH = get_hermes_home() / "config.yaml"

COMMAND_PREFIXES = ("/model", "/模型")

# Provider alias profiles are defined by the user in ``config.yaml`` under
# ``model_aliases:``. Keeping them out of source so the code is provider-
# agnostic and shareable. Each alias maps to a dict with fields:
# ``provider, base_url, api_mode, api_key_env, context_length``.

REQUIRED_PROFILE_FIELDS = (
    "provider", "base_url", "api_mode", "api_key_env", "context_length",
)

DEFAULT_CONTEXT_LENGTH = 128000


def _profile_from_custom_entry(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Derive a /model profile from a ``custom_providers:`` list entry.

    ``name`` / ``base_url`` are mandatory in custom_providers (hermes config
    validator enforces this). ``api_mode`` / ``api_key_env`` / ``context_length``
    are pulled from the entry when present; sensible fallbacks otherwise.
    The env-var name defaults to ``{NAME.upper()}_API_KEY`` — override by
    setting ``api_key_env`` in the entry (or in ``model_aliases``).
    """
    name = entry.get("name")
    base_url = entry.get("base_url")
    if not isinstance(name, str) or not isinstance(base_url, str):
        return None
    return {
        "provider": f"custom:{name}",
        "base_url": base_url,
        "api_mode": entry.get("api_mode") or "chat_completions",
        "api_key_env": entry.get("api_key_env") or f"{name.upper()}_API_KEY",
        "context_length": entry.get("context_length") or DEFAULT_CONTEXT_LENGTH,
    }


def _parse_profiles(src: str) -> Dict[str, Dict[str, Any]]:
    """Build alias→profile map from ``custom_providers`` (auto) and
    ``model_aliases`` (explicit).  Explicit wins on conflict.
    """
    try:
        data = yaml.safe_load(src) or {}
    except yaml.YAMLError as e:
        logger.warning("config.yaml yaml parse failed: %s", e)
        return {}

    out: Dict[str, Dict[str, Any]] = {}

    cps = data.get("custom_providers") if isinstance(data, dict) else None
    if isinstance(cps, list):
        for entry in cps:
            if not isinstance(entry, dict):
                continue
            profile = _profile_from_custom_entry(entry)
            if profile is None:
                continue
            out[str(entry["name"]).lower()] = profile

    explicit = data.get("model_aliases") if isinstance(data, dict) else None
    if isinstance(explicit, dict):
        for name, profile in explicit.items():
            if not isinstance(profile, dict):
                continue
            if not all(k in profile for k in REQUIRED_PROFILE_FIELDS):
                logger.debug("model_aliases.%s missing required fields", name)
                continue
            out[str(name).lower()] = profile

    return out


def parse_model_command(message_text: Optional[str]) -> Optional[str]:
    """If message starts with /model or /模型, return the body (possibly empty)."""
    if not message_text:
        return None
    t = message_text.lstrip()
    low = t.lower()
    for pref in COMMAND_PREFIXES:
        if low.startswith(pref.lower()):
            tail = t[len(pref):]
            if tail == "" or tail[0].isspace():
                return tail.strip()
    return None


MAIN_BLOCK_RE = re.compile(r"^model:\n(?:[ \t]+.*\n)+", re.MULTILINE)
PLAN_BLOCK_RE = re.compile(r"^plan_mode:\n(?:[ \t]+.*\n)+", re.MULTILINE)
PLAN_MODEL_SUB_RE = re.compile(r"^  model:\n(?:[ \t]{4,}.*\n)+", re.MULTILINE)


def _render_main(model: str, profile: dict) -> str:
    return (
        "model:\n"
        f"  default: {model}\n"
        f"  provider: {profile['provider']}\n"
        f"  base_url: {profile['base_url']}\n"
        f"  api_mode: {profile['api_mode']}\n"
        f"  api_key_env: {profile['api_key_env']}\n"
        f"  context_length: {profile['context_length']}\n"
    )


def _render_plan_sub(model: str, profile: dict) -> str:
    return (
        "  model:\n"
        f"    default: {model}\n"
        f"    provider: {profile['provider']}\n"
        f"    base_url: {profile['base_url']}\n"
        f"    api_mode: {profile['api_mode']}\n"
        f"    api_key_env: {profile['api_key_env']}\n"
        f"    context_length: {profile['context_length']}\n"
    )


def _swap_main(src: str, new_block: str) -> str:
    if not MAIN_BLOCK_RE.search(src):
        raise RuntimeError("main model: block not found")
    return MAIN_BLOCK_RE.sub(new_block, src, count=1)


def _swap_plan(src: str, new_sub: Optional[str]) -> str:
    m = PLAN_BLOCK_RE.search(src)
    if not m:
        raise RuntimeError("plan_mode: block not found")
    block = m.group(0)
    if PLAN_MODEL_SUB_RE.search(block):
        if new_sub is None:
            updated = PLAN_MODEL_SUB_RE.sub("", block, count=1)
        else:
            updated = PLAN_MODEL_SUB_RE.sub(new_sub, block, count=1)
    else:
        if new_sub is None:
            return src
        if not block.endswith("\n"):
            block += "\n"
        updated = block + new_sub
    return src[: m.start()] + updated + src[m.end():]


def _stub_reply(text: str) -> Dict[str, Any]:
    return {
        "final_response": text,
        "messages": [],
        "api_calls": 0,
        "tools": [],
        "history_offset": 0,
        "last_prompt_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "model": "",
        "completed": True,
        "_model_command_synthetic": True,
    }


HELP_TMPL = (
    "用法:\n"
    "  /model show\n"
    "  /model main <alias> <model>\n"
    "  /model plan <alias> <model>\n"
    "  /model plan off\n"
    "aliases: {aliases}"
)


def _help(profiles: Dict[str, Dict[str, Any]]) -> str:
    names = ", ".join(profiles) if profiles else "(空 — 在 config.yaml 的 model_aliases 段添加)"
    return HELP_TMPL.format(aliases=names)


def _write_backup_and_save(old_src: str, new_src: str) -> None:
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = CONFIG_PATH.with_name(CONFIG_PATH.name + f".bak_cmd_{ts}")
    try:
        backup.write_text(old_src)
    except Exception as e:
        logger.warning("config backup failed: %s", e)
    CONFIG_PATH.write_text(new_src)


def handle_model_command(body: str) -> Dict[str, Any]:
    try:
        src = CONFIG_PATH.read_text()
    except Exception as e:
        return _stub_reply(f"读取 config 失败: {e}")

    profiles = _parse_profiles(src)
    toks = body.split()
    if not toks:
        return _stub_reply(_help(profiles))

    sub = toks[0].lower()

    if sub == "show":
        m = MAIN_BLOCK_RE.search(src)
        pm = PLAN_BLOCK_RE.search(src)
        msg = "[主模型]\n" + (m.group(0).rstrip() if m else "(missing)")
        msg += "\n\n[plan_mode]\n" + (pm.group(0).rstrip() if pm else "(missing)")
        return _stub_reply(msg)

    if sub == "main":
        if len(toks) != 3:
            return _stub_reply("用法: /model main <alias> <model>")
        alias, model = toks[1].lower(), toks[2]
        profile = profiles.get(alias)
        if not profile:
            return _stub_reply(
                f"未知 alias {alias!r}; 可选: {', '.join(profiles) or '(空)'}"
            )
        try:
            new_src = _swap_main(src, _render_main(model, profile))
        except Exception as e:
            return _stub_reply(f"编辑失败: {e}")
        _write_backup_and_save(src, new_src)
        return _stub_reply(
            f"✓ 主模型已切为 {profile['provider']} / {model}\n"
            "下条消息起生效（无需重启）"
        )

    if sub == "plan":
        if len(toks) == 2 and toks[1].lower() == "off":
            try:
                new_src = _swap_plan(src, None)
            except Exception as e:
                return _stub_reply(f"编辑失败: {e}")
            if new_src == src:
                return _stub_reply("plan 模式本就未设独立模型")
            _write_backup_and_save(src, new_src)
            return _stub_reply("✓ plan 独立模型已移除（fallback 到主模型）")
        if len(toks) == 3:
            alias, model = toks[1].lower(), toks[2]
            profile = profiles.get(alias)
            if not profile:
                return _stub_reply(
                    f"未知 alias {alias!r}; 可选: {', '.join(profiles) or '(空)'}"
                )
            try:
                new_src = _swap_plan(src, _render_plan_sub(model, profile))
            except Exception as e:
                return _stub_reply(f"编辑失败: {e}")
            _write_backup_and_save(src, new_src)
            return _stub_reply(
                f"✓ plan 模式模型已切为 {profile['provider']} / {model}\n"
                "下次 /plan 生效"
            )
        return _stub_reply("用法: /model plan <alias> <model> | /model plan off")

    return _stub_reply(f"未知子命令 {sub!r}\n\n" + _help(profiles))
