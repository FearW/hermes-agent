"""Credential source removal registry.

Each seeded credential source needs a matching removal step so
``hermes auth remove`` is sticky.  Removing only the pool entry is not enough:
the next ``load_pool()`` can re-seed from auth.json, ~/.hermes/.env, or an
external CLI credential file unless the external source is cleaned or
suppressed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, List, Optional


@dataclass(frozen=True)
class RemovalResult:
    cleaned: List[str] = field(default_factory=list)
    hints: List[str] = field(default_factory=list)
    suppress: bool = True


@dataclass(frozen=True)
class RemovalStep:
    description: str
    matches: Callable[[str, str], bool]
    remove_fn: Callable[[str, object], RemovalResult]


def _source(entry: object) -> str:
    return str(getattr(entry, "source", "") or "")


def _remove_provider_state(provider: str) -> bool:
    from hermes_cli.auth import _auth_store_lock, _load_auth_store, _save_auth_store

    with _auth_store_lock():
        auth_store = _load_auth_store()
        providers = auth_store.get("providers")
        changed = False
        if isinstance(providers, dict) and provider in providers:
            providers.pop(provider, None)
            changed = True
        if auth_store.get("active_provider") == provider:
            auth_store["active_provider"] = None
            changed = True
        if changed:
            _save_auth_store(auth_store)
        return changed


def _remove_env(provider: str, entry: object) -> RemovalResult:
    source = _source(entry)
    env_var = source.split(":", 1)[1] if source.startswith("env:") else ""
    if not env_var:
        return RemovalResult()

    had_process_value = env_var in os.environ
    from hermes_cli.config.env import remove_env_value

    removed_from_dotenv = remove_env_value(env_var)
    cleaned: List[str] = []
    hints: List[str] = []
    if removed_from_dotenv:
        cleaned.append(f"Cleared {env_var} from .env")
    elif had_process_value:
        hints.append(
            f"{env_var} is still set in your shell environment; unset it there to stop future sessions from seeing it."
        )
    return RemovalResult(cleaned=cleaned, hints=hints, suppress=True)


def _remove_copilot(provider: str, entry: object) -> RemovalResult:
    from hermes_cli.auth import suppress_credential_source

    for source in (
        "gh_cli",
        "env:COPILOT_GITHUB_TOKEN",
        "env:GH_TOKEN",
        "env:GITHUB_TOKEN",
    ):
        suppress_credential_source(provider, source)
    return RemovalResult(suppress=False)


def _remove_provider_state_source(provider: str, entry: object) -> RemovalResult:
    cleaned = []
    if _remove_provider_state(provider):
        cleaned.append(f"Cleared auth.json providers.{provider}")
    return RemovalResult(cleaned=cleaned, suppress=True)


def _remove_codex(provider: str, entry: object) -> RemovalResult:
    from hermes_cli.auth import suppress_credential_source

    cleaned = []
    if _remove_provider_state(provider):
        cleaned.append("Cleared auth.json providers.openai-codex")
    suppress_credential_source(provider, "device_code")
    return RemovalResult(cleaned=cleaned, suppress=False)


def _remove_anthropic_external(provider: str, entry: object) -> RemovalResult:
    source = _source(entry)
    cleaned: List[str] = []
    if source in {"hermes_pkce", "manual:hermes_pkce"}:
        oauth_path = _hermes_oauth_path()
        if oauth_path.exists():
            try:
                oauth_path.unlink()
                cleaned.append("Removed ~/.hermes/.anthropic_oauth.json")
            except OSError:
                pass
    return RemovalResult(cleaned=cleaned, suppress=True)


def _remove_qwen(provider: str, entry: object) -> RemovalResult:
    source = _source(entry)
    from hermes_cli.auth import suppress_credential_source

    if source == "manual:qwen_cli":
        suppress_credential_source(provider, "qwen-cli")
        return RemovalResult(suppress=False)
    return RemovalResult(suppress=True)


def _remove_minimax(provider: str, entry: object) -> RemovalResult:
    from hermes_cli.auth import suppress_credential_source

    cleaned = []
    if _remove_provider_state(provider):
        cleaned.append("Cleared auth.json providers.minimax-oauth")
    if _source(entry) == "manual:minimax_oauth":
        suppress_credential_source(provider, "oauth")
        return RemovalResult(cleaned=cleaned, suppress=False)
    return RemovalResult(cleaned=cleaned, suppress=True)


def _remove_custom_config(provider: str, entry: object) -> RemovalResult:
    return RemovalResult(
        hints=[
            "This credential also exists in config.yaml custom_providers; remove the api_key field there if you do not want it re-enabled."
        ],
        suppress=True,
    )


def _hermes_oauth_path() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / ".anthropic_oauth.json"


def _provider_is(provider: str, *names: str) -> bool:
    return provider in names


def _source_is(source: str, *names: str) -> bool:
    return source in names


def _source_starts(source: str, prefix: str) -> bool:
    return source.startswith(prefix)


_REGISTRY: List[RemovalStep] = [
    RemovalStep(
        "gh auth token / COPILOT_GITHUB_TOKEN / GH_TOKEN",
        lambda provider, source: _provider_is(provider, "copilot")
        and _source_is(source, "gh_cli", "env:COPILOT_GITHUB_TOKEN", "env:GH_TOKEN", "env:GITHUB_TOKEN"),
        _remove_copilot,
    ),
    RemovalStep(
        "~/.claude/.credentials.json",
        lambda provider, source: _provider_is(provider, "anthropic") and _source_is(source, "claude_code"),
        _remove_anthropic_external,
    ),
    RemovalStep(
        "~/.hermes/.anthropic_oauth.json",
        lambda provider, source: _provider_is(provider, "anthropic")
        and _source_is(source, "hermes_pkce", "manual:hermes_pkce"),
        _remove_anthropic_external,
    ),
    RemovalStep(
        "auth.json providers.nous",
        lambda provider, source: _provider_is(provider, "nous") and _source_is(source, "device_code"),
        _remove_provider_state_source,
    ),
    RemovalStep(
        "auth.json providers.openai-codex + ~/.codex/auth.json",
        lambda provider, source: _provider_is(provider, "openai-codex")
        and _source_is(source, "device_code", "manual:device_code"),
        _remove_codex,
    ),
    RemovalStep(
        "auth.json providers.minimax-oauth",
        lambda provider, source: _provider_is(provider, "minimax-oauth")
        and _source_is(source, "oauth", "manual:minimax_oauth"),
        _remove_minimax,
    ),
    RemovalStep(
        "~/.qwen/oauth_creds.json",
        lambda provider, source: _provider_is(provider, "qwen-oauth")
        and _source_is(source, "qwen-cli", "manual:qwen_cli"),
        _remove_qwen,
    ),
    RemovalStep(
        "Custom provider config.yaml api_key field",
        lambda provider, source: provider.startswith("custom:")
        and _source_is(source, "model_config") or _source_starts(source, "config:"),
        _remove_custom_config,
    ),
    RemovalStep(
        "Any env-seeded credential (XAI_API_KEY, DEEPSEEK_API_KEY, etc.)",
        lambda provider, source: _source_starts(source, "env:"),
        _remove_env,
    ),
]


def find_removal_step(provider: str, source: str) -> Optional[RemovalStep]:
    normalized_provider = (provider or "").strip().lower()
    normalized_source = (source or "").strip()
    for step in _REGISTRY:
        if step.matches(normalized_provider, normalized_source):
            return step
    return None


__all__ = ["RemovalResult", "RemovalStep", "_REGISTRY", "find_removal_step"]
