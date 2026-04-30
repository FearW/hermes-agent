"""Model and provider selection commands for Hermes CLI."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


def _require_tty(command_name: str) -> None:
    if not sys.stdin.isatty():
        msg = (
            f"Error: 'hermes {command_name}' requires an interactive terminal."
            " It cannot be run through a pipe or non-interactive subprocess."
            " Run it directly in your terminal instead."
        )
        print(msg, file=sys.stderr)
        sys.exit(1)


def cmd_model(args):
    """Select default model — starts with provider selection, then model picker."""
    _require_tty("model")
    select_provider_and_model(args=args)


def select_provider_and_model(args=None):
    """Core provider selection + model picking logic.

    Shared by ``cmd_model`` (``hermes model``) and the setup wizard
    (``setup_model_provider`` in setup.py).  Handles the full flow:
    provider picker, credential prompting, model selection, and config
    persistence.
    """
    from hermes_cli.auth import (
        resolve_provider, AuthError, format_auth_error,
    )
    from hermes_cli.config import load_config, get_env_value

    config = load_config()
    current_model = config.get("model")
    if isinstance(current_model, dict):
        current_model = current_model.get("default", "")
    current_model = current_model or "(not set)"

    # Read effective provider the same way the CLI does at startup:
    # config.yaml model.provider > env var > auto-detect
    import os
    config_provider = None
    model_cfg = config.get("model")
    if isinstance(model_cfg, dict):
        config_provider = model_cfg.get("provider")

    effective_provider = (
        config_provider
        or os.getenv("HERMES_INFERENCE_PROVIDER")
        or "auto"
    )
    try:
        active = resolve_provider(effective_provider)
    except AuthError as exc:
        warning = format_auth_error(exc)
        print(f"Warning: {warning} Falling back to auto provider detection.")
        try:
            active = resolve_provider("auto")
        except AuthError:
            active = None  # no provider yet; default to first in list

    # Detect custom endpoint
    if active == "openrouter" and get_env_value("OPENAI_BASE_URL"):
        active = "custom"

    provider_labels = {
        "openrouter": "OpenRouter",
        "nous": "Nous Portal",
        "openai-codex": "OpenAI Codex",
        "qwen-oauth": "Qwen OAuth",
        "copilot-acp": "GitHub Copilot ACP",
        "copilot": "GitHub Copilot",
        "anthropic": "Anthropic",
        "gemini": "Google AI Studio",
        "zai": "Z.AI / GLM",
        "kimi-coding": "Kimi / Moonshot",
        "minimax": "MiniMax",
        "minimax-cn": "MiniMax (China)",
        "opencode-zen": "OpenCode Zen",
        "opencode-go": "OpenCode Go",
        "ai-gateway": "AI Gateway",
        "kilocode": "Kilo Code",
        "alibaba": "Alibaba Cloud (DashScope)",
        "huggingface": "Hugging Face",
        "xiaomi": "Xiaomi MiMo",
        "custom": "Custom endpoint",
    }
    active_label = provider_labels.get(active, active) if active else "none"

    print()
    print(f"  Current model:    {current_model}")
    print(f"  Active provider:  {active_label}")
    print()

    # Step 1: Provider selection — top providers shown first, rest behind "More..."
    top_providers = [
        ("nous", "Nous Portal (Nous Research subscription)"),
        ("openrouter", "OpenRouter (100+ models, pay-per-use)"),
        ("anthropic", "Anthropic (Claude models — API key or Claude Code)"),
        ("openai-codex", "OpenAI Codex"),
        ("qwen-oauth", "Qwen OAuth (reuses local Qwen CLI login)"),
        ("copilot", "GitHub Copilot (uses GITHUB_TOKEN or gh auth token)"),
        ("huggingface", "Hugging Face Inference Providers (20+ open models)"),
    ]

    extended_providers = [
        ("copilot-acp", "GitHub Copilot ACP (spawns `copilot --acp --stdio`)"),
        ("gemini", "Google AI Studio (Gemini models — OpenAI-compatible endpoint)"),
        ("zai", "Z.AI / GLM (Zhipu AI direct API)"),
        ("kimi-coding", "Kimi / Moonshot (Moonshot AI direct API)"),
        ("minimax", "MiniMax (global direct API)"),
        ("minimax-cn", "MiniMax China (domestic direct API)"),
        ("kilocode", "Kilo Code (Kilo Gateway API)"),
        ("opencode-zen", "OpenCode Zen (35+ curated models, pay-as-you-go)"),
        ("opencode-go", "OpenCode Go (open models, $10/month subscription)"),
        ("ai-gateway", "AI Gateway (Vercel — 200+ models, pay-per-use)"),
        ("alibaba", "Alibaba Cloud / DashScope Coding (Qwen + multi-provider)"),
        ("xiaomi", "Xiaomi MiMo (MiMo-V2 models — pro, omni, flash)"),
    ]

    def _named_custom_provider_map(cfg) -> dict[str, dict[str, str]]:
        custom_providers_cfg = cfg.get("custom_providers") or []
        custom_provider_map = {}
        if not isinstance(custom_providers_cfg, list):
            return custom_provider_map
        for entry in custom_providers_cfg:
            if not isinstance(entry, dict):
                continue
            name = (entry.get("name") or "").strip()
            base_url = (entry.get("base_url") or "").strip()
            if not name or not base_url:
                continue
            key = "custom:" + name.lower().replace(" ", "-")
            custom_provider_map[key] = {
                "name": name,
                "base_url": base_url,
                "api_key": entry.get("api_key", ""),
                "model": entry.get("model", ""),
                "api_mode": entry.get("api_mode", ""),
            }
        return custom_provider_map

    # Add user-defined custom providers from config.yaml
    _custom_provider_map = _named_custom_provider_map(config)  # key → {name, base_url, api_key}
    for key, provider_info in _custom_provider_map.items():
        name = provider_info["name"]
        base_url = provider_info["base_url"]
        short_url = base_url.replace("https://", "").replace("http://", "").rstrip("/")
        saved_model = provider_info.get("model", "")
        model_hint = f" — {saved_model}" if saved_model else ""
        top_providers.append((key, f"{name} ({short_url}){model_hint}"))

    top_keys = {k for k, _ in top_providers}
    extended_keys = {k for k, _ in extended_providers}

    # If the active provider is in the extended list, promote it into top
    if active and active in extended_keys:
        promoted = [(k, l) for k, l in extended_providers if k == active]
        extended_providers = [(k, l) for k, l in extended_providers if k != active]
        top_providers = promoted + top_providers
        top_keys.add(active)

    # Build the primary menu
    ordered = []
    default_idx = 0
    for key, label in top_providers:
        if active and key == active:
            ordered.append((key, f"{label}  ← currently active"))
            default_idx = len(ordered) - 1
        else:
            ordered.append((key, label))

    ordered.append(("more", "More providers..."))
    ordered.append(("cancel", "Cancel"))

    provider_idx = _prompt_provider_choice(
        [label for _, label in ordered], default=default_idx,
    )
    if provider_idx is None or ordered[provider_idx][0] == "cancel":
        print("No change.")
        return

    selected_provider = ordered[provider_idx][0]

    # "More providers..." — show the extended list
    if selected_provider == "more":
        ext_ordered = list(extended_providers)
        ext_ordered.append(("custom", "Custom endpoint (enter URL manually)"))
        if _custom_provider_map:
            ext_ordered.append(("remove-custom", "Remove a saved custom provider"))
        ext_ordered.append(("cancel", "Cancel"))

        ext_idx = _prompt_provider_choice(
            [label for _, label in ext_ordered], default=0,
        )
        if ext_idx is None or ext_ordered[ext_idx][0] == "cancel":
            print("No change.")
            return
        selected_provider = ext_ordered[ext_idx][0]

    # Step 2: Provider-specific setup + model selection
    if selected_provider == "openrouter":
        _model_flow_openrouter(config, current_model)
    elif selected_provider == "nous":
        _model_flow_nous(config, current_model, args=args)
    elif selected_provider == "openai-codex":
        _model_flow_openai_codex(config, current_model)
    elif selected_provider == "qwen-oauth":
        _model_flow_qwen_oauth(config, current_model)
    elif selected_provider == "copilot-acp":
        _model_flow_copilot_acp(config, current_model)
    elif selected_provider == "copilot":
        _model_flow_copilot(config, current_model)
    elif selected_provider == "custom":
        _model_flow_custom(config)
    elif selected_provider.startswith("custom:"):
        provider_info = _named_custom_provider_map(load_config()).get(selected_provider)
        if provider_info is None:
            print(
                "Warning: the selected saved custom provider is no longer available. "
                "It may have been removed from config.yaml. No change."
            )
            return
        _model_flow_named_custom(config, provider_info)
    elif selected_provider == "remove-custom":
        _remove_custom_provider(config)
    elif selected_provider == "anthropic":
        _model_flow_anthropic(config, current_model)
    elif selected_provider == "kimi-coding":
        _model_flow_kimi(config, current_model)
    elif selected_provider in ("gemini", "zai", "minimax", "minimax-cn", "kilocode", "opencode-zen", "opencode-go", "ai-gateway", "alibaba", "huggingface", "xiaomi"):
        _model_flow_api_key_provider(config, selected_provider, current_model)

    # ── Post-switch cleanup: clear stale OPENAI_BASE_URL ──────────────
    # When the user switches to a named provider (anything except "custom"),
    # a leftover OPENAI_BASE_URL in ~/.hermes/.env can poison auxiliary
    # clients that use provider:auto. Clear it proactively.  (#5161)
    if selected_provider not in ("custom", "cancel", "remove-custom") \
            and not selected_provider.startswith("custom:"):
        _clear_stale_openai_base_url()


def _clear_stale_openai_base_url():
    """Remove OPENAI_BASE_URL from ~/.hermes/.env if the active provider is not 'custom'.

    After a provider switch, a leftover OPENAI_BASE_URL causes auxiliary
    clients (compression, vision, delegation) with provider:auto to route
    requests to the old custom endpoint instead of the newly selected
    provider.  See issue #5161.
    """
    from hermes_cli.config import get_env_value, save_env_value, load_config

    cfg = load_config()
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, dict):
        provider = (model_cfg.get("provider") or "").strip().lower()
    else:
        provider = ""

    if provider == "custom" or not provider:
        return  # custom provider legitimately uses OPENAI_BASE_URL

    stale_url = get_env_value("OPENAI_BASE_URL")
    if stale_url:
        save_env_value("OPENAI_BASE_URL", "")
        print(f"Cleared stale OPENAI_BASE_URL from .env (was: {stale_url[:40]}...)"
              if len(stale_url) > 40
              else f"Cleared stale OPENAI_BASE_URL from .env (was: {stale_url})")


def _prompt_provider_choice(choices, *, default=0):
    """Show provider selection menu with curses arrow-key navigation.

    Falls back to a numbered list when curses is unavailable (e.g. piped
    stdin, non-TTY environments).  Returns the selected index, or None
    if the user cancels.
    """
    try:
        from hermes_cli.setup import _curses_prompt_choice
        idx = _curses_prompt_choice("Select provider:", choices, default)
        if idx >= 0:
            print()
            return idx
    except Exception:
        pass

    # Fallback: numbered list
    print("Select provider:")
    for i, c in enumerate(choices, 1):
        marker = "→" if i - 1 == default else " "
        print(f"  {marker} {i}. {c}")
    print()
    while True:
        try:
            val = input(f"Choice [1-{len(choices)}] ({default + 1}): ").strip()
            if not val:
                return default
            idx = int(val) - 1
            if 0 <= idx < len(choices):
                return idx
            print(f"Please enter 1-{len(choices)}")
        except ValueError:
            print("Please enter a number")
        except (KeyboardInterrupt, EOFError):
            print()
            return None


def _model_flow_openrouter(config, current_model=""):
    """OpenRouter provider: ensure API key, then pick model."""
    from hermes_cli.auth import _prompt_model_selection, _save_model_choice, deactivate_provider
    from hermes_cli.config import get_env_value, save_env_value

    api_key = get_env_value("OPENROUTER_API_KEY")
    if not api_key:
        print("No OpenRouter API key configured.")
        print("Get one at: https://openrouter.ai/keys")
        print()
        try:
            import getpass
            key = getpass.getpass("OpenRouter API key (or Enter to cancel): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return
        if not key:
            print("Cancelled.")
            return
        save_env_value("OPENROUTER_API_KEY", key)
        print("API key saved.")
        print()

    from hermes_cli.models import model_ids, get_pricing_for_provider
    openrouter_models = model_ids(force_refresh=True)

    # Fetch live pricing (non-blocking — returns empty dict on failure)
    pricing = get_pricing_for_provider("openrouter", force_refresh=True)

    selected = _prompt_model_selection(openrouter_models, current_model=current_model, pricing=pricing)
    if selected:
        _save_model_choice(selected)

        # Update config provider and deactivate any OAuth provider
        from hermes_cli.config import load_config, save_config
        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = "openrouter"
        model["base_url"] = OPENROUTER_BASE_URL
        model["api_mode"] = "chat_completions"
        save_config(cfg)
        deactivate_provider()
        print(f"Default model set to: {selected} (via OpenRouter)")
    else:
        print("No change.")


def _model_flow_nous(config, current_model="", args=None):
    """Nous Portal provider: ensure logged in, then pick model."""
    from hermes_cli.auth import (
        get_provider_auth_state, _prompt_model_selection, _save_model_choice,
        _update_config_for_provider, resolve_nous_runtime_credentials,
        AuthError, format_auth_error,
        _login_nous, PROVIDER_REGISTRY,
    )
    from hermes_cli.config import get_env_value, save_config, save_env_value
    from hermes_cli.nous_subscription import (
        apply_nous_provider_defaults,
        get_nous_subscription_explainer_lines,
    )
    import argparse

    state = get_provider_auth_state("nous")
    if not state or not state.get("access_token"):
        print("Not logged into Nous Portal. Starting login...")
        print()
        try:
            mock_args = argparse.Namespace(
                portal_url=getattr(args, "portal_url", None),
                inference_url=getattr(args, "inference_url", None),
                client_id=getattr(args, "client_id", None),
                scope=getattr(args, "scope", None),
                no_browser=bool(getattr(args, "no_browser", False)),
                timeout=getattr(args, "timeout", None) or 15.0,
                ca_bundle=getattr(args, "ca_bundle", None),
                insecure=bool(getattr(args, "insecure", False)),
            )
            _login_nous(mock_args, PROVIDER_REGISTRY["nous"])
            print()
            for line in get_nous_subscription_explainer_lines():
                print(line)
        except SystemExit:
            print("Login cancelled or failed.")
            return
        except Exception as exc:
            print(f"Login failed: {exc}")
            return
        # login_nous already handles model selection + config update
        return

    # Already logged in — use curated model list (same as OpenRouter defaults).
    # The live /models endpoint returns hundreds of models; the curated list
    # shows only agentic models users recognize from OpenRouter.
    from hermes_cli.models import (
        _PROVIDER_MODELS, get_pricing_for_provider, filter_nous_free_models,
        check_nous_free_tier, partition_nous_models_by_tier,
    )
    model_ids = _PROVIDER_MODELS.get("nous", [])
    if not model_ids:
        print("No curated models available for Nous Portal.")
        return

    # Verify credentials are still valid (catches expired sessions early)
    try:
        creds = resolve_nous_runtime_credentials(min_key_ttl_seconds=5 * 60)
    except Exception as exc:
        relogin = isinstance(exc, AuthError) and exc.relogin_required
        msg = format_auth_error(exc) if isinstance(exc, AuthError) else str(exc)
        if relogin:
            print(f"Session expired: {msg}")
            print("Re-authenticating with Nous Portal...\n")
            try:
                mock_args = argparse.Namespace(
                    portal_url=None, inference_url=None, client_id=None,
                    scope=None, no_browser=False, timeout=15.0,
                    ca_bundle=None, insecure=False,
                )
                _login_nous(mock_args, PROVIDER_REGISTRY["nous"])
            except Exception as login_exc:
                print(f"Re-login failed: {login_exc}")
            return
        print(f"Could not verify credentials: {msg}")
        return

    # Fetch live pricing (non-blocking — returns empty dict on failure)
    pricing = get_pricing_for_provider("nous")

    # Check if user is on free tier
    free_tier = check_nous_free_tier()

    # For both tiers: apply the allowlist filter first (removes non-allowlisted
    # free models and allowlist models that aren't actually free).
    # Then for free users: partition remaining models into selectable/unavailable.
    model_ids = filter_nous_free_models(model_ids, pricing)
    unavailable_models: list[str] = []
    if free_tier:
        model_ids, unavailable_models = partition_nous_models_by_tier(model_ids, pricing, free_tier=True)

    if not model_ids and not unavailable_models:
        print("No models available for Nous Portal after filtering.")
        return

    # Resolve portal URL for upgrade links (may differ on staging)
    _nous_portal_url = ""
    try:
        _nous_state = get_provider_auth_state("nous")
        if _nous_state:
            _nous_portal_url = _nous_state.get("portal_base_url", "")
    except Exception:
        pass

    if free_tier and not model_ids:
        print("No free models currently available.")
        if unavailable_models:
            from hermes_cli.auth import DEFAULT_NOUS_PORTAL_URL
            _url = (_nous_portal_url or DEFAULT_NOUS_PORTAL_URL).rstrip("/")
            print(f"Upgrade at {_url} to access paid models.")
        return

    print(f"Showing {len(model_ids)} curated models — use \"Enter custom model name\" for others.")

    selected = _prompt_model_selection(
        model_ids, current_model=current_model, pricing=pricing,
        unavailable_models=unavailable_models, portal_url=_nous_portal_url,
    )
    if selected:
        _save_model_choice(selected)
        # Reactivate Nous as the provider and update config
        inference_url = creds.get("base_url", "")
        _update_config_for_provider("nous", inference_url)
        current_model_cfg = config.get("model")
        if isinstance(current_model_cfg, dict):
            model_cfg = dict(current_model_cfg)
        elif isinstance(current_model_cfg, str) and current_model_cfg.strip():
            model_cfg = {"default": current_model_cfg.strip()}
        else:
            model_cfg = {}
        model_cfg["provider"] = "nous"
        model_cfg["default"] = selected
        if inference_url and inference_url.strip():
            model_cfg["base_url"] = inference_url.rstrip("/")
        else:
            model_cfg.pop("base_url", None)
        config["model"] = model_cfg
        # Clear any custom endpoint that might conflict
        if get_env_value("OPENAI_BASE_URL"):
            save_env_value("OPENAI_BASE_URL", "")
            save_env_value("OPENAI_API_KEY", "")
        changed_defaults = apply_nous_provider_defaults(config)
        save_config(config)
        print(f"Default model set to: {selected} (via Nous Portal)")
        if "tts" in changed_defaults:
            print("TTS provider set to: OpenAI TTS via your Nous subscription")
        else:
            current_tts = str(config.get("tts", {}).get("provider") or "edge")
            if current_tts.lower() not in {"", "edge"}:
                print(f"Keeping your existing TTS provider: {current_tts}")
        print()
        for line in get_nous_subscription_explainer_lines():
            print(line)
    else:
        print("No change.")


def _model_flow_openai_codex(config, current_model=""):
    """OpenAI Codex provider: ensure logged in, then pick model."""
    from hermes_cli.auth import (
        get_codex_auth_status, _prompt_model_selection, _save_model_choice,
        _update_config_for_provider, _login_openai_codex,
        PROVIDER_REGISTRY, DEFAULT_CODEX_BASE_URL,
    )
    from hermes_cli.codex_models import get_codex_model_ids
    import argparse

    status = get_codex_auth_status()
    if not status.get("logged_in"):
        print("Not logged into OpenAI Codex. Starting login...")
        print()
        try:
            mock_args = argparse.Namespace()
            _login_openai_codex(mock_args, PROVIDER_REGISTRY["openai-codex"])
        except SystemExit:
            print("Login cancelled or failed.")
            return
        except Exception as exc:
            print(f"Login failed: {exc}")
            return

    _codex_token = None
    # Prefer credential pool (where `hermes auth` stores device_code tokens),
    # fall back to legacy provider state.
    try:
        _codex_status = get_codex_auth_status()
        if _codex_status.get("logged_in"):
            _codex_token = _codex_status.get("api_key")
    except Exception:
        pass
    if not _codex_token:
        try:
            from hermes_cli.auth import resolve_codex_runtime_credentials
            _codex_creds = resolve_codex_runtime_credentials()
            _codex_token = _codex_creds.get("api_key")
        except Exception:
            pass

    codex_models = get_codex_model_ids(access_token=_codex_token)

    selected = _prompt_model_selection(codex_models, current_model=current_model)
    if selected:
        _save_model_choice(selected)
        _update_config_for_provider("openai-codex", DEFAULT_CODEX_BASE_URL)
        print(f"Default model set to: {selected} (via OpenAI Codex)")
    else:
        print("No change.")



_DEFAULT_QWEN_PORTAL_MODELS = [
    "qwen3-coder-plus",
    "qwen3-coder",
]


def _model_flow_qwen_oauth(_config, current_model=""):
    """Qwen OAuth provider: reuse local Qwen CLI login, then pick model."""
    from hermes_cli.auth import (
        get_qwen_auth_status,
        resolve_qwen_runtime_credentials,
        _prompt_model_selection,
        _save_model_choice,
        _update_config_for_provider,
        DEFAULT_QWEN_BASE_URL,
    )
    from hermes_cli.models import fetch_api_models

    status = get_qwen_auth_status()
    if not status.get("logged_in"):
        print("Not logged into Qwen CLI OAuth.")
        print("Run: qwen auth qwen-oauth")
        auth_file = status.get("auth_file")
        if auth_file:
            print(f"Expected credentials file: {auth_file}")
        if status.get("error"):
            print(f"Error: {status.get('error')}")
        return

    # Try live model discovery, fall back to curated list.
    models = None
    try:
        creds = resolve_qwen_runtime_credentials(refresh_if_expiring=True)
        models = fetch_api_models(creds["api_key"], creds["base_url"])
    except Exception:
        pass
    if not models:
        models = list(_DEFAULT_QWEN_PORTAL_MODELS)

    default = current_model or (models[0] if models else "qwen3-coder-plus")
    selected = _prompt_model_selection(models, current_model=default)
    if selected:
        _save_model_choice(selected)
        _update_config_for_provider("qwen-oauth", DEFAULT_QWEN_BASE_URL)
        print(f"Default model set to: {selected} (via Qwen OAuth)")
    else:
        print("No change.")



def _model_flow_custom(config):
    """Custom endpoint: collect URL, API key, and model name.

    Automatically saves the endpoint to ``custom_providers`` in config.yaml
    so it appears in the provider menu on subsequent runs.
    """
    from hermes_cli.auth import _save_model_choice, deactivate_provider
    from hermes_cli.config import get_env_value, load_config, save_config

    current_url = get_env_value("OPENAI_BASE_URL") or ""
    current_key = get_env_value("OPENAI_API_KEY") or ""

    print("Custom OpenAI-compatible endpoint configuration:")
    if current_url:
        print(f"  Current URL: {current_url}")
    if current_key:
        print(f"  Current key: {current_key[:8]}...")
    print()

    try:
        base_url = input(f"API base URL [{current_url or 'e.g. https://api.example.com/v1'}]: ").strip()
        import getpass
        api_key = getpass.getpass(f"API key [{current_key[:8] + '...' if current_key else 'optional'}]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return

    if not base_url and not current_url:
        print("No URL provided. Cancelled.")
        return

    # Validate URL format
    effective_url = base_url or current_url
    if not effective_url.startswith(("http://", "https://")):
        print(f"Invalid URL: {effective_url} (must start with http:// or https://)")
        return

    effective_key = api_key or current_key

    from hermes_cli.models import probe_api_models

    probe = probe_api_models(effective_key, effective_url)
    if probe.get("used_fallback") and probe.get("resolved_base_url"):
        print(
            f"Warning: endpoint verification worked at {probe['resolved_base_url']}/models, "
            f"not the exact URL you entered. Saving the working base URL instead."
        )
        effective_url = probe["resolved_base_url"]
        if base_url:
            base_url = effective_url
    elif probe.get("models") is not None:
        print(
            f"Verified endpoint via {probe.get('probed_url')} "
            f"({len(probe.get('models') or [])} model(s) visible)"
        )
    else:
        print(
            f"Warning: could not verify this endpoint via {probe.get('probed_url')}. "
            f"Hermes will still save it."
        )
        if probe.get("suggested_base_url"):
            suggested = probe["suggested_base_url"]
            if suggested.endswith("/v1"):
                print(f"  If this server expects /v1 in the path, try base URL: {suggested}")
            else:
                print(f"  If /v1 should not be in the base URL, try: {suggested}")

    # Select model — use probe results when available, fall back to manual input
    model_name = ""
    detected_models = probe.get("models") or []
    try:
        if len(detected_models) == 1:
            print(f"  Detected model: {detected_models[0]}")
            confirm = input("  Use this model? [Y/n]: ").strip().lower()
            if confirm in ("", "y", "yes"):
                model_name = detected_models[0]
            else:
                model_name = input("Model name (e.g. gpt-4, llama-3-70b): ").strip()
        elif len(detected_models) > 1:
            print("  Available models:")
            for i, m in enumerate(detected_models, 1):
                print(f"    {i}. {m}")
            pick = input(f"  Select model [1-{len(detected_models)}] or type name: ").strip()
            if pick.isdigit() and 1 <= int(pick) <= len(detected_models):
                model_name = detected_models[int(pick) - 1]
            elif pick:
                model_name = pick
        else:
            model_name = input("Model name (e.g. gpt-4, llama-3-70b): ").strip()

        context_length_str = input("Context length in tokens [leave blank for auto-detect]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return

    context_length = None
    if context_length_str:
        try:
            context_length = int(context_length_str.replace(",", "").replace("k", "000").replace("K", "000"))
            if context_length <= 0:
                context_length = None
        except ValueError:
            print(f"Invalid context length: {context_length_str} — will auto-detect.")
            context_length = None

    if model_name:
        _save_model_choice(model_name)

        # Update config and deactivate any OAuth provider
        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = "custom"
        model["base_url"] = effective_url
        if effective_key:
            model["api_key"] = effective_key
        model.pop("api_mode", None)  # let runtime auto-detect from URL
        save_config(cfg)
        deactivate_provider()

        # Sync the caller's config dict so the setup wizard's final
        # save_config(config) preserves our model settings.  Without
        # this, the wizard overwrites model.provider/base_url with
        # the stale values from its own config dict (#4172).
        config["model"] = dict(model)

        print(f"Default model set to: {model_name} (via {effective_url})")
    else:
        if base_url or api_key:
            # Even without a model name, persist the custom endpoint on the
            # caller's config dict so the setup wizard doesn't lose it.
            _caller_model = config.get("model")
            if not isinstance(_caller_model, dict):
                _caller_model = {"default": _caller_model} if _caller_model else {}
            _caller_model["provider"] = "custom"
            _caller_model["base_url"] = effective_url
            if effective_key:
                _caller_model["api_key"] = effective_key
            _caller_model.pop("api_mode", None)
            config["model"] = _caller_model
            print("Endpoint saved. Use `/model` in chat or `hermes model` to set a model.")

    # Auto-save to custom_providers so it appears in the menu next time
    _save_custom_provider(effective_url, effective_key, model_name or "", context_length=context_length)


def _save_custom_provider(base_url, api_key="", model="", context_length=None):
    """Save a custom endpoint to custom_providers in config.yaml.

    Deduplicates by base_url — if the URL already exists, updates the
    model name and context_length but doesn't add a duplicate entry.
    Auto-generates a display name from the URL hostname.
    """
    from hermes_cli.config import load_config, save_config

    cfg = load_config()
    providers = cfg.get("custom_providers") or []
    if not isinstance(providers, list):
        providers = []

    # Check if this URL is already saved — update model/context_length if so
    for entry in providers:
        if isinstance(entry, dict) and entry.get("base_url", "").rstrip("/") == base_url.rstrip("/"):
            changed = False
            if model and entry.get("model") != model:
                entry["model"] = model
                changed = True
            if model and context_length:
                models_cfg = entry.get("models", {})
                if not isinstance(models_cfg, dict):
                    models_cfg = {}
                models_cfg[model] = {"context_length": context_length}
                entry["models"] = models_cfg
                changed = True
            if changed:
                cfg["custom_providers"] = providers
                save_config(cfg)
            return  # already saved, updated if needed

    # Auto-generate a name from the URL
    import re
    clean = base_url.replace("https://", "").replace("http://", "").rstrip("/")
    # Remove /v1 suffix for cleaner names
    clean = re.sub(r"/v1/?$", "", clean)
    # Use hostname:port as the name
    name = clean.split("/")[0]
    # Capitalize for readability
    if "localhost" in name or "127.0.0.1" in name:
        name = f"Local ({name})"
    elif "runpod" in name.lower():
        name = f"RunPod ({name})"
    else:
        name = name.capitalize()

    entry = {"name": name, "base_url": base_url}
    if api_key:
        entry["api_key"] = api_key
    if model:
        entry["model"] = model
    if model and context_length:
        entry["models"] = {model: {"context_length": context_length}}

    providers.append(entry)
    cfg["custom_providers"] = providers
    save_config(cfg)
    print(f"  💾 Saved to custom providers as \"{name}\" (edit in config.yaml)")


def _remove_custom_provider(config):
    """Let the user remove a saved custom provider from config.yaml."""
    from hermes_cli.config import load_config, save_config

    cfg = load_config()
    providers = cfg.get("custom_providers") or []
    if not isinstance(providers, list) or not providers:
        print("No custom providers configured.")
        return

    print("Remove a custom provider:\n")

    choices = []
    for entry in providers:
        if isinstance(entry, dict):
            name = entry.get("name", "unnamed")
            url = entry.get("base_url", "")
            short_url = url.replace("https://", "").replace("http://", "").rstrip("/")
            choices.append(f"{name} ({short_url})")
        else:
            choices.append(str(entry))
    choices.append("Cancel")

    try:
        from simple_term_menu import TerminalMenu
        menu = TerminalMenu(
            [f"  {c}" for c in choices], cursor_index=0,
            menu_cursor="-> ", menu_cursor_style=("fg_red", "bold"),
            menu_highlight_style=("fg_red",),
            cycle_cursor=True, clear_screen=False,
            title="Select provider to remove:",
        )
        idx = menu.show()
        from hermes_cli.curses_ui import flush_stdin
        flush_stdin()
        print()
    except (ImportError, NotImplementedError, OSError, subprocess.SubprocessError):
        for i, c in enumerate(choices, 1):
            print(f"  {i}. {c}")
        print()
        try:
            val = input(f"Choice [1-{len(choices)}]: ").strip()
            idx = int(val) - 1 if val else None
        except (ValueError, KeyboardInterrupt, EOFError):
            idx = None

    if idx is None or idx >= len(providers):
        print("No change.")
        return

    removed = providers.pop(idx)
    cfg["custom_providers"] = providers
    save_config(cfg)
    removed_name = removed.get("name", "unnamed") if isinstance(removed, dict) else str(removed)
    print(f"✅ Removed \"{removed_name}\" from custom providers.")


def _model_flow_named_custom(config, provider_info):
    """Handle a named custom provider from config.yaml custom_providers list.

    Always probes the endpoint's /models API to let the user pick a model.
    If a model was previously saved, it is pre-selected in the menu.
    Falls back to the saved model if probing fails.
    """
    from hermes_cli.auth import _save_model_choice, deactivate_provider
    from hermes_cli.config import load_config, save_config
    from hermes_cli.models import fetch_api_models

    name = provider_info["name"]
    base_url = provider_info["base_url"]
    api_key = provider_info.get("api_key", "")
    saved_model = provider_info.get("model", "")

    print(f"  Provider: {name}")
    print(f"  URL:      {base_url}")
    if saved_model:
        print(f"  Current:  {saved_model}")
    print()

    print("Fetching available models...")
    models = fetch_api_models(api_key, base_url, timeout=8.0)

    if models:
        default_idx = 0
        if saved_model and saved_model in models:
            default_idx = models.index(saved_model)

        print(f"Found {len(models)} model(s):\n")
        try:
            from simple_term_menu import TerminalMenu
            menu_items = [
                f"  {m} (current)" if m == saved_model else f"  {m}"
                for m in models
            ] + ["  Cancel"]
            menu = TerminalMenu(
                menu_items, cursor_index=default_idx,
                menu_cursor="-> ", menu_cursor_style=("fg_green", "bold"),
                menu_highlight_style=("fg_green",),
                cycle_cursor=True, clear_screen=False,
                title=f"Select model from {name}:",
            )
            idx = menu.show()
            from hermes_cli.curses_ui import flush_stdin
            flush_stdin()
            print()
            if idx is None or idx >= len(models):
                print("Cancelled.")
                return
            model_name = models[idx]
        except (ImportError, NotImplementedError, OSError, subprocess.SubprocessError):
            for i, m in enumerate(models, 1):
                suffix = " (current)" if m == saved_model else ""
                print(f"  {i}. {m}{suffix}")
            print(f"  {len(models) + 1}. Cancel")
            print()
            try:
                val = input(f"Choice [1-{len(models) + 1}]: ").strip()
                if not val:
                    print("Cancelled.")
                    return
                idx = int(val) - 1
                if idx < 0 or idx >= len(models):
                    print("Cancelled.")
                    return
                model_name = models[idx]
            except (ValueError, KeyboardInterrupt, EOFError):
                print("\nCancelled.")
                return
    elif saved_model:
        print("Could not fetch models from endpoint.")
        try:
            model_name = input(f"Model name [{saved_model}]: ").strip() or saved_model
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return
    else:
        print("Could not fetch models from endpoint. Enter model name manually.")
        try:
            model_name = input("Model name: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return
        if not model_name:
            print("No model specified. Cancelled.")
            return

    # Activate and save the model to the custom_providers entry
    _save_model_choice(model_name)

    cfg = load_config()
    model = cfg.get("model")
    if not isinstance(model, dict):
        model = {"default": model} if model else {}
        cfg["model"] = model
    model["provider"] = "custom"
    model["base_url"] = base_url
    if api_key:
        model["api_key"] = api_key
    # Apply api_mode from custom_providers entry, or clear stale value
    custom_api_mode = provider_info.get("api_mode", "")
    if custom_api_mode:
        model["api_mode"] = custom_api_mode
    else:
        model.pop("api_mode", None)  # let runtime auto-detect from URL
    save_config(cfg)
    deactivate_provider()

    # Save model name to the custom_providers entry for next time
    _save_custom_provider(base_url, api_key, model_name)

    print(f"\n✅ Model set to: {model_name}")
    print(f"   Provider: {name} ({base_url})")


# Curated model lists for direct API-key providers — single source in models.py
from hermes_cli.models import _PROVIDER_MODELS


def _current_reasoning_effort(config) -> str:
    agent_cfg = config.get("agent")
    if isinstance(agent_cfg, dict):
        return str(agent_cfg.get("reasoning_effort") or "").strip().lower()
    return ""


def _set_reasoning_effort(config, effort: str) -> None:
    agent_cfg = config.get("agent")
    if not isinstance(agent_cfg, dict):
        agent_cfg = {}
        config["agent"] = agent_cfg
    agent_cfg["reasoning_effort"] = effort


def _prompt_reasoning_effort_selection(efforts, current_effort=""):
    """Prompt for a reasoning effort. Returns effort, 'none', or None to keep current."""
    deduped = list(dict.fromkeys(str(effort).strip().lower() for effort in efforts if str(effort).strip()))
    canonical_order = ("minimal", "low", "medium", "high", "xhigh")
    ordered = [effort for effort in canonical_order if effort in deduped]
    ordered.extend(effort for effort in deduped if effort not in canonical_order)
    if not ordered:
        return None

    def _label(effort):
        if effort == current_effort:
            return f"{effort}  ← currently in use"
        return effort

    disable_label = "Disable reasoning"
    skip_label = "Skip (keep current)"

    if current_effort == "none":
        default_idx = len(ordered)
    elif current_effort in ordered:
        default_idx = ordered.index(current_effort)
    elif "medium" in ordered:
        default_idx = ordered.index("medium")
    else:
        default_idx = 0

    try:
        from simple_term_menu import TerminalMenu

        choices = [f"  {_label(effort)}" for effort in ordered]
        choices.append(f"  {disable_label}")
        choices.append(f"  {skip_label}")
        menu = TerminalMenu(
            choices,
            cursor_index=default_idx,
            menu_cursor="-> ",
            menu_cursor_style=("fg_green", "bold"),
            menu_highlight_style=("fg_green",),
            cycle_cursor=True,
            clear_screen=False,
            title="Select reasoning effort:",
        )
        idx = menu.show()
        from hermes_cli.curses_ui import flush_stdin
        flush_stdin()
        if idx is None:
            return None
        print()
        if idx < len(ordered):
            return ordered[idx]
        if idx == len(ordered):
            return "none"
        return None
    except (ImportError, NotImplementedError, OSError, subprocess.SubprocessError):
        pass

    print("Select reasoning effort:")
    for i, effort in enumerate(ordered, 1):
        print(f"  {i}. {_label(effort)}")
    n = len(ordered)
    print(f"  {n + 1}. {disable_label}")
    print(f"  {n + 2}. {skip_label}")
    print()

    while True:
        try:
            choice = input(f"Choice [1-{n + 2}] (default: keep current): ").strip()
            if not choice:
                return None
            idx = int(choice)
            if 1 <= idx <= n:
                return ordered[idx - 1]
            if idx == n + 1:
                return "none"
            if idx == n + 2:
                return None
            print(f"Please enter 1-{n + 2}")
        except ValueError:
            print("Please enter a number")
        except (KeyboardInterrupt, EOFError):
            return None


def _model_flow_copilot(config, current_model=""):
    """GitHub Copilot flow using env vars, gh CLI, or OAuth device code."""
    from hermes_cli.auth import (
        PROVIDER_REGISTRY,
        _prompt_model_selection,
        _save_model_choice,
        deactivate_provider,
        resolve_api_key_provider_credentials,
    )
    from hermes_cli.config import save_env_value, load_config, save_config
    from hermes_cli.models import (
        fetch_api_models,
        fetch_github_model_catalog,
        github_model_reasoning_efforts,
        copilot_model_api_mode,
        normalize_copilot_model_id,
    )

    provider_id = "copilot"
    pconfig = PROVIDER_REGISTRY[provider_id]

    creds = resolve_api_key_provider_credentials(provider_id)
    api_key = creds.get("api_key", "")
    source = creds.get("source", "")

    if not api_key:
        print("No GitHub token configured for GitHub Copilot.")
        print()
        print("  Supported token types:")
        print("    → OAuth token (gho_*)          via `copilot login` or device code flow")
        print("    → Fine-grained PAT (github_pat_*)  with Copilot Requests permission")
        print("    → GitHub App token (ghu_*)     via environment variable")
        print("    ✗ Classic PAT (ghp_*)          NOT supported by Copilot API")
        print()
        print("  Options:")
        print("    1. Login with GitHub (OAuth device code flow)")
        print("    2. Enter a token manually")
        print("    3. Cancel")
        print()
        try:
            choice = input("  Choice [1-3]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return

        if choice == "1":
            try:
                from hermes_cli.copilot_auth import copilot_device_code_login
                token = copilot_device_code_login()
                if token:
                    save_env_value("COPILOT_GITHUB_TOKEN", token)
                    print("  Copilot token saved.")
                    print()
                else:
                    print("  Login cancelled or failed.")
                    return
            except Exception as exc:
                print(f"  Login failed: {exc}")
                return
        elif choice == "2":
            try:
                import getpass
                new_key = getpass.getpass("  Token (COPILOT_GITHUB_TOKEN): ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                return
            if not new_key:
                print("  Cancelled.")
                return
            # Validate token type
            try:
                from hermes_cli.copilot_auth import validate_copilot_token
                valid, msg = validate_copilot_token(new_key)
                if not valid:
                    print(f"  ✗ {msg}")
                    return
            except ImportError:
                pass
            save_env_value("COPILOT_GITHUB_TOKEN", new_key)
            print("  Token saved.")
            print()
        else:
            print("  Cancelled.")
            return

        creds = resolve_api_key_provider_credentials(provider_id)
        api_key = creds.get("api_key", "")
        source = creds.get("source", "")
    else:
        if source in ("GITHUB_TOKEN", "GH_TOKEN"):
            print(f"  GitHub token: REDACTED ✓ ({source})")
        elif source == "gh auth token":
            print("  GitHub token: REDACTED (from `gh auth token`)")
        else:
            print("  GitHub token: REDACTED")
        print()

    effective_base = pconfig.inference_base_url

    catalog = fetch_github_model_catalog(api_key)
    live_models = [item.get("id", "") for item in catalog if item.get("id")] if catalog else fetch_api_models(api_key, effective_base)
    normalized_current_model = normalize_copilot_model_id(
        current_model,
        catalog=catalog,
        api_key=api_key,
    ) or current_model
    if live_models:
        model_list = [model_id for model_id in live_models if model_id]
        print(f"  Found {len(model_list)} model(s) from GitHub Copilot")
    else:
        model_list = _PROVIDER_MODELS.get(provider_id, [])
        if model_list:
            print("  ⚠ Could not auto-detect models from GitHub Copilot — showing defaults.")
            print('    Use "Enter custom model name" if you do not see your model.')

    if model_list:
        selected = _prompt_model_selection(model_list, current_model=normalized_current_model)
    else:
        try:
            selected = input("Model name: ").strip()
        except (KeyboardInterrupt, EOFError):
            selected = None

    if selected:
        selected = normalize_copilot_model_id(
            selected,
            catalog=catalog,
            api_key=api_key,
        ) or selected
        initial_cfg = load_config()
        current_effort = _current_reasoning_effort(initial_cfg)
        reasoning_efforts = github_model_reasoning_efforts(
            selected,
            catalog=catalog,
            api_key=api_key,
        )
        selected_effort = None
        if reasoning_efforts:
            print(f"  {selected} supports reasoning controls.")
            selected_effort = _prompt_reasoning_effort_selection(
                reasoning_efforts, current_effort=current_effort
            )

        _save_model_choice(selected)

        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = provider_id
        model["base_url"] = effective_base
        model["api_mode"] = copilot_model_api_mode(
            selected,
            catalog=catalog,
            api_key=api_key,
        )
        if selected_effort is not None:
            _set_reasoning_effort(cfg, selected_effort)
        save_config(cfg)
        deactivate_provider()

        print(f"Default model set to: {selected} (via {pconfig.name})")
        if reasoning_efforts:
            if selected_effort == "none":
                print("Reasoning disabled for this model.")
            elif selected_effort:
                print(f"Reasoning effort set to: {selected_effort}")
    else:
        print("No change.")


def _model_flow_copilot_acp(config, current_model=""):
    """GitHub Copilot ACP flow using the local Copilot CLI."""
    from hermes_cli.auth import (
        PROVIDER_REGISTRY,
        _prompt_model_selection,
        _save_model_choice,
        deactivate_provider,
        get_external_process_provider_status,
        resolve_api_key_provider_credentials,
        resolve_external_process_provider_credentials,
    )
    from hermes_cli.models import (
        fetch_github_model_catalog,
        normalize_copilot_model_id,
    )
    from hermes_cli.config import load_config, save_config

    del config

    provider_id = "copilot-acp"
    pconfig = PROVIDER_REGISTRY[provider_id]

    status = get_external_process_provider_status(provider_id)
    resolved_command = status.get("resolved_command") or status.get("command") or "copilot"
    effective_base = status.get("base_url") or pconfig.inference_base_url

    print("  GitHub Copilot ACP delegates Hermes turns to `copilot --acp`.")
    print("  Hermes currently starts its own ACP subprocess for each request.")
    print("  Hermes uses your selected model as a hint for the Copilot ACP session.")
    print(f"  Command: {resolved_command}")
    print(f"  Backend marker: {effective_base}")
    print()

    try:
        creds = resolve_external_process_provider_credentials(provider_id)
    except Exception as exc:
        print(f"  ⚠ {exc}")
        print("  Set HERMES_COPILOT_ACP_COMMAND or COPILOT_CLI_PATH if Copilot CLI is installed elsewhere.")
        return

    effective_base = creds.get("base_url") or effective_base

    catalog_api_key = ""
    try:
        catalog_creds = resolve_api_key_provider_credentials("copilot")
        catalog_api_key = catalog_creds.get("api_key", "")
    except Exception:
        pass

    catalog = fetch_github_model_catalog(catalog_api_key)
    normalized_current_model = normalize_copilot_model_id(
        current_model,
        catalog=catalog,
        api_key=catalog_api_key,
    ) or current_model

    if catalog:
        model_list = [item.get("id", "") for item in catalog if item.get("id")]
        print(f"  Found {len(model_list)} model(s) from GitHub Copilot")
    else:
        model_list = _PROVIDER_MODELS.get("copilot", [])
        if model_list:
            print("  ⚠ Could not auto-detect models from GitHub Copilot — showing defaults.")
            print('    Use "Enter custom model name" if you do not see your model.')

    if model_list:
        selected = _prompt_model_selection(
            model_list,
            current_model=normalized_current_model,
        )
    else:
        try:
            selected = input("Model name: ").strip()
        except (KeyboardInterrupt, EOFError):
            selected = None

    if not selected:
        print("No change.")
        return

    selected = normalize_copilot_model_id(
        selected,
        catalog=catalog,
        api_key=catalog_api_key,
    ) or selected
    _save_model_choice(selected)

    cfg = load_config()
    model = cfg.get("model")
    if not isinstance(model, dict):
        model = {"default": model} if model else {}
        cfg["model"] = model
    model["provider"] = provider_id
    model["base_url"] = effective_base
    model["api_mode"] = "chat_completions"
    save_config(cfg)
    deactivate_provider()

    print(f"Default model set to: {selected} (via {pconfig.name})")


def _model_flow_kimi(config, current_model=""):
    """Kimi / Moonshot model selection with automatic endpoint routing.

    - sk-kimi-* keys   → api.kimi.com/coding/v1  (Kimi Coding Plan)
    - Other keys        → api.moonshot.ai/v1      (legacy Moonshot)

    No manual base URL prompt — endpoint is determined by key prefix.
    """
    from hermes_cli.auth import (
        PROVIDER_REGISTRY, KIMI_CODE_BASE_URL, _prompt_model_selection,
        _save_model_choice, deactivate_provider,
    )
    from hermes_cli.config import get_env_value, save_env_value, load_config, save_config

    provider_id = "kimi-coding"
    pconfig = PROVIDER_REGISTRY[provider_id]
    key_env = pconfig.api_key_env_vars[0] if pconfig.api_key_env_vars else ""
    base_url_env = pconfig.base_url_env_var or ""

    # Step 1: Check / prompt for API key
    existing_key = ""
    for ev in pconfig.api_key_env_vars:
        existing_key = get_env_value(ev) or os.getenv(ev, "")
        if existing_key:
            break

    if not existing_key:
        print(f"No {pconfig.name} API key configured.")
        if key_env:
            try:
                import getpass
                new_key = getpass.getpass(f"{key_env} (or Enter to cancel): ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                return
            if not new_key:
                print("Cancelled.")
                return
            save_env_value(key_env, new_key)
            existing_key = new_key
            print("API key saved.")
            print()
    else:
        print(f"  {pconfig.name} API key: {existing_key[:8]}... ✓")
        print()

    # Step 2: Auto-detect endpoint from key prefix
    is_coding_plan = existing_key.startswith("sk-kimi-")
    if is_coding_plan:
        effective_base = KIMI_CODE_BASE_URL
        print(f"  Detected Kimi Coding Plan key → {effective_base}")
    else:
        effective_base = pconfig.inference_base_url
        print(f"  Using Moonshot endpoint → {effective_base}")
    # Clear any manual base URL override so auto-detection works at runtime
    if base_url_env and get_env_value(base_url_env):
        save_env_value(base_url_env, "")
    print()

    # Step 3: Model selection — show appropriate models for the endpoint
    if is_coding_plan:
        # Coding Plan models (kimi-for-coding first)
        model_list = [
            "kimi-for-coding",
            "kimi-k2.5",
            "kimi-k2-thinking",
            "kimi-k2-thinking-turbo",
        ]
    else:
        # Legacy Moonshot models (excludes Coding Plan-only models)
        model_list = _PROVIDER_MODELS.get("moonshot", [])

    if model_list:
        selected = _prompt_model_selection(model_list, current_model=current_model)
    else:
        try:
            selected = input("Enter model name: ").strip()
        except (KeyboardInterrupt, EOFError):
            selected = None

    if selected:
        _save_model_choice(selected)

        # Update config with provider and base URL
        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = provider_id
        model["base_url"] = effective_base
        model.pop("api_mode", None)  # let runtime auto-detect from URL
        save_config(cfg)
        deactivate_provider()

        endpoint_label = "Kimi Coding" if is_coding_plan else "Moonshot"
        print(f"Default model set to: {selected} (via {endpoint_label})")
    else:
        print("No change.")


def _model_flow_api_key_provider(config, provider_id, current_model=""):
    """Generic flow for API-key providers (z.ai, MiniMax, OpenCode, etc.)."""
    from hermes_cli.auth import (
        PROVIDER_REGISTRY, _prompt_model_selection, _save_model_choice,
        deactivate_provider,
    )
    from hermes_cli.config import get_env_value, save_env_value, load_config, save_config
    from hermes_cli.models import fetch_api_models, opencode_model_api_mode, normalize_opencode_model_id

    pconfig = PROVIDER_REGISTRY[provider_id]
    key_env = pconfig.api_key_env_vars[0] if pconfig.api_key_env_vars else ""
    base_url_env = pconfig.base_url_env_var or ""

    # Check / prompt for API key
    existing_key = ""
    for ev in pconfig.api_key_env_vars:
        existing_key = get_env_value(ev) or os.getenv(ev, "")
        if existing_key:
            break

    if not existing_key:
        print(f"No {pconfig.name} API key configured.")
        if key_env:
            try:
                import getpass
                new_key = getpass.getpass(f"{key_env} (or Enter to cancel): ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                return
            if not new_key:
                print("Cancelled.")
                return
            save_env_value(key_env, new_key)
            print("API key saved.")
            print()
    else:
        print(f"  {pconfig.name} API key: {existing_key[:8]}... ✓")
        print()

    # Optional base URL override
    current_base = ""
    if base_url_env:
        current_base = get_env_value(base_url_env) or os.getenv(base_url_env, "")
    effective_base = current_base or pconfig.inference_base_url

    try:
        override = input(f"Base URL [{effective_base}]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        override = ""
    if override and base_url_env:
        if not override.startswith(("http://", "https://")):
            print("  Invalid URL — must start with http:// or https://. Keeping current value.")
        else:
            save_env_value(base_url_env, override)
            effective_base = override

    # Model selection — resolution order:
    #   1. models.dev registry (cached, filtered for agentic/tool-capable models)
    #   2. Curated static fallback list (offline insurance)
    #   3. Live /models endpoint probe (small providers without models.dev data)
    curated = _PROVIDER_MODELS.get(provider_id, [])

    # Try models.dev first — returns tool-capable models, filtered for noise
    mdev_models: list = []
    try:
        from agent.models_dev import list_agentic_models
        mdev_models = list_agentic_models(provider_id)
    except Exception:
        pass

    if mdev_models:
        model_list = mdev_models
        print(f"  Found {len(model_list)} model(s) from models.dev registry")
    elif curated and len(curated) >= 8:
        # Curated list is substantial — use it directly, skip live probe
        model_list = curated
        print(f"  Showing {len(model_list)} curated models — use \"Enter custom model name\" for others.")
    else:
        api_key_for_probe = existing_key or (get_env_value(key_env) if key_env else "")
        live_models = fetch_api_models(api_key_for_probe, effective_base)
        if live_models and len(live_models) >= len(curated):
            model_list = live_models
            print(f"  Found {len(model_list)} model(s) from {pconfig.name} API")
        else:
            model_list = curated
            if model_list:
                print(f"  Showing {len(model_list)} curated models — use \"Enter custom model name\" for others.")
        # else: no defaults either, will fall through to raw input

    if provider_id in {"opencode-zen", "opencode-go"}:
        model_list = [normalize_opencode_model_id(provider_id, mid) for mid in model_list]
        current_model = normalize_opencode_model_id(provider_id, current_model)
        model_list = list(dict.fromkeys(mid for mid in model_list if mid))

    if model_list:
        selected = _prompt_model_selection(model_list, current_model=current_model)
    else:
        try:
            selected = input("Model name: ").strip()
        except (KeyboardInterrupt, EOFError):
            selected = None

    if selected:
        if provider_id in {"opencode-zen", "opencode-go"}:
            selected = normalize_opencode_model_id(provider_id, selected)

        _save_model_choice(selected)

        # Update config with provider, base URL, and provider-specific API mode
        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = provider_id
        model["base_url"] = effective_base
        if provider_id in {"opencode-zen", "opencode-go"}:
            model["api_mode"] = opencode_model_api_mode(provider_id, selected)
        else:
            model.pop("api_mode", None)
        save_config(cfg)
        deactivate_provider()

        print(f"Default model set to: {selected} (via {pconfig.name})")
    else:
        print("No change.")


def _run_anthropic_oauth_flow(save_env_value):
    """Run the Claude OAuth setup-token flow. Returns True if credentials were saved."""
    from agent.anthropic_adapter import (
        run_oauth_setup_token,
        read_claude_code_credentials,
        is_claude_code_token_valid,
    )
    from hermes_cli.config import (
        save_anthropic_oauth_token,
        use_anthropic_claude_code_credentials,
    )

    def _activate_claude_code_credentials_if_available() -> bool:
        try:
            creds = read_claude_code_credentials()
        except Exception:
            creds = None
        if creds and (
            is_claude_code_token_valid(creds)
            or bool(creds.get("refreshToken"))
        ):
            use_anthropic_claude_code_credentials(save_fn=save_env_value)
            print("  ✓ Claude Code credentials linked.")
            from hermes_constants import display_hermes_home as _dhh_fn
            print(f"    Hermes will use Claude's credential store directly instead of copying a setup-token into {_dhh_fn()}/.env.")
            return True
        return False

    try:
        print()
        print("  Running 'claude setup-token' — follow the prompts below.")
        print("  A browser window will open for you to authorize access.")
        print()
        token = run_oauth_setup_token()
        if token:
            if _activate_claude_code_credentials_if_available():
                return True
            save_anthropic_oauth_token(token, save_fn=save_env_value)
            print("  ✓ OAuth credentials saved.")
            return True

        # Subprocess completed but no token auto-detected — ask user to paste
        print()
        print("  If the setup-token was displayed above, paste it here:")
        print()
        try:
            import getpass
            manual_token = getpass.getpass("  Paste setup-token (or Enter to cancel): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
        if manual_token:
            save_anthropic_oauth_token(manual_token, save_fn=save_env_value)
            print("  ✓ Setup-token saved.")
            return True

        print("  ⚠ Could not detect saved credentials.")
        return False

    except FileNotFoundError:
        # Claude CLI not installed — guide user through manual setup
        print()
        print("  The 'claude' CLI is required for OAuth login.")
        print()
        print("  To install and authenticate:")
        print()
        print("    1. Install Claude Code:  npm install -g @anthropic-ai/claude-code")
        print("    2. Run:                  claude setup-token")
        print("    3. Follow the browser prompts to authorize")
        print("    4. Re-run:               hermes model")
        print()
        print("  Or paste an existing setup-token now (sk-ant-oat-...):")
        print()
        try:
            import getpass
            token = getpass.getpass("  Setup-token (or Enter to cancel): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return False
        if token:
            save_anthropic_oauth_token(token, save_fn=save_env_value)
            print("  ✓ Setup-token saved.")
            return True
        print("  Cancelled — install Claude Code and try again.")
        return False


def _model_flow_anthropic(config, current_model=""):
    """Flow for Anthropic provider — OAuth subscription, API key, or Claude Code creds."""
    import os
    from hermes_cli.auth import (
        PROVIDER_REGISTRY, _prompt_model_selection, _save_model_choice,
        deactivate_provider,
    )
    from hermes_cli.config import (
        get_env_value, save_env_value, load_config, save_config,
        save_anthropic_api_key,
    )
    from hermes_cli.models import _PROVIDER_MODELS

    # Check ALL credential sources
    from hermes_cli.auth import get_anthropic_key
    existing_key = get_anthropic_key()
    cc_available = False
    try:
        from agent.anthropic_adapter import read_claude_code_credentials, is_claude_code_token_valid
        cc_creds = read_claude_code_credentials()
        if cc_creds and is_claude_code_token_valid(cc_creds):
            cc_available = True
    except Exception:
        pass

    has_creds = bool(existing_key) or cc_available
    needs_auth = not has_creds

    if has_creds:
        # Show what we found
        if existing_key:
            print(f"  Anthropic credentials: {existing_key[:12]}... ✓")
        elif cc_available:
            print("  Claude Code credentials: ✓ (auto-detected)")
        print()
        print("    1. Use existing credentials")
        print("    2. Reauthenticate (new OAuth login)")
        print("    3. Cancel")
        print()
        try:
            choice = input("  Choice [1/2/3]: ").strip()
        except (KeyboardInterrupt, EOFError):
            choice = "1"

        if choice == "2":
            needs_auth = True
        elif choice == "3":
            return
        # choice == "1" or default: use existing, proceed to model selection

    if needs_auth:
        # Show auth method choice
        print()
        print("  Choose authentication method:")
        print()
        print("    1. Claude Pro/Max subscription (OAuth login)")
        print("    2. Anthropic API key (pay-per-token)")
        print("    3. Cancel")
        print()
        try:
            choice = input("  Choice [1/2/3]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return

        if choice == "1":
            if not _run_anthropic_oauth_flow(save_env_value):
                return

        elif choice == "2":
            print()
            print("  Get an API key at: https://console.anthropic.com/settings/keys")
            print()
            try:
                import getpass
                api_key = getpass.getpass("  API key (sk-ant-...): ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                return
            if not api_key:
                print("  Cancelled.")
                return
            save_anthropic_api_key(api_key, save_fn=save_env_value)
            print("  ✓ API key saved.")

        else:
            print("  No change.")
            return
    print()

    # Model selection
    model_list = _PROVIDER_MODELS.get("anthropic", [])
    if model_list:
        selected = _prompt_model_selection(model_list, current_model=current_model)
    else:
        try:
            selected = input("Model name (e.g., claude-sonnet-4-20250514): ").strip()
        except (KeyboardInterrupt, EOFError):
            selected = None

    if selected:
        _save_model_choice(selected)

        # Update config with provider — clear base_url since
        # resolve_runtime_provider() always hardcodes Anthropic's URL.
        # Leaving a stale base_url in config can contaminate other
        # providers if the user switches without running 'hermes model'.
        cfg = load_config()
        model = cfg.get("model")
        if not isinstance(model, dict):
            model = {"default": model} if model else {}
            cfg["model"] = model
        model["provider"] = "anthropic"
        model.pop("base_url", None)
        save_config(cfg)
        deactivate_provider()

        print(f"Default model set to: {selected} (via Anthropic)")
    else:
        print("No change.")
