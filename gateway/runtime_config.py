import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from hermes_constants import get_hermes_home, parse_reasoning_effort

from gateway.restart import (
    DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT,
    parse_restart_drain_timeout,
)

logger = logging.getLogger(__name__)
_hermes_home = get_hermes_home()


def load_gateway_config() -> dict:
    """Load and parse ~/.hermes/config.yaml, returning {} on any error."""
    try:
        config_path = _hermes_home / "config.yaml"
        if config_path.exists():
            import yaml

            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception:
        logger.debug("Could not load gateway config from %s", _hermes_home / "config.yaml")
    return {}


def resolve_gateway_model(config: dict | None = None) -> str:
    """Read model from config.yaml as the gateway single source of truth."""
    cfg = config if config is not None else load_gateway_config()
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, str):
        return model_cfg
    if isinstance(model_cfg, dict):
        return model_cfg.get("default") or model_cfg.get("model") or ""
    return ""


def load_prefill_messages() -> List[Dict[str, Any]]:
    """Load ephemeral prefill messages from env or config."""
    file_path = os.getenv("HERMES_PREFILL_MESSAGES_FILE", "")
    if not file_path:
        try:
            import yaml as _y

            cfg_path = _hermes_home / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as _f:
                    cfg = _y.safe_load(_f) or {}
                file_path = cfg.get("prefill_messages_file", "")
        except Exception:
            pass
    if not file_path:
        return []
    path = Path(file_path).expanduser()
    if not path.is_absolute():
        path = _hermes_home / path
    if not path.exists():
        logger.warning("Prefill messages file not found: %s", path)
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            logger.warning("Prefill messages file must contain a JSON array: %s", path)
            return []
        return data
    except Exception as e:
        logger.warning("Failed to load prefill messages from %s: %s", path, e)
        return []


def load_ephemeral_system_prompt() -> str:
    """Load ephemeral system prompt from env or config."""
    prompt = os.getenv("HERMES_EPHEMERAL_SYSTEM_PROMPT", "")
    if prompt:
        return prompt
    try:
        import yaml as _y

        cfg_path = _hermes_home / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as _f:
                cfg = _y.safe_load(_f) or {}
            return (cfg.get("agent", {}).get("system_prompt", "") or "").strip()
    except Exception:
        pass
    return ""


def load_reasoning_config() -> dict | None:
    """Load reasoning effort from config.yaml."""
    effort = ""
    try:
        import yaml as _y

        cfg_path = _hermes_home / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as _f:
                cfg = _y.safe_load(_f) or {}
            effort = str(cfg.get("agent", {}).get("reasoning_effort", "") or "").strip()
    except Exception:
        pass
    result = parse_reasoning_effort(effort)
    if effort and effort.strip() and result is None:
        logger.warning("Unknown reasoning_effort '%s', using default (medium)", effort)
    return result


def load_service_tier() -> str | None:
    """Load Priority Processing setting from config.yaml."""
    raw = ""
    try:
        import yaml as _y

        cfg_path = _hermes_home / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as _f:
                cfg = _y.safe_load(_f) or {}
            raw = str(cfg.get("agent", {}).get("service_tier", "") or "").strip()
    except Exception:
        pass

    value = raw.lower()
    if not value or value in {"normal", "default", "standard", "off", "none"}:
        return None
    if value in {"fast", "priority", "on"}:
        return "priority"
    logger.warning("Unknown service_tier '%s', ignoring", raw)
    return None


def load_show_reasoning() -> bool:
    """Load show_reasoning toggle from config.yaml display section."""
    try:
        import yaml as _y

        cfg_path = _hermes_home / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as _f:
                cfg = _y.safe_load(_f) or {}
            return bool(cfg.get("display", {}).get("show_reasoning", False))
    except Exception:
        pass
    return False


def load_busy_input_mode() -> str:
    """Load gateway drain-time busy-input behavior from config/env."""
    mode = os.getenv("HERMES_GATEWAY_BUSY_INPUT_MODE", "").strip().lower()
    if not mode:
        try:
            import yaml as _y

            cfg_path = _hermes_home / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as _f:
                    cfg = _y.safe_load(_f) or {}
                mode = str(cfg.get("display", {}).get("busy_input_mode", "") or "").strip().lower()
        except Exception:
            pass
    return "queue" if mode == "queue" else "interrupt"


def load_restart_drain_timeout() -> float:
    """Load graceful gateway restart/stop drain timeout in seconds."""
    raw = os.getenv("HERMES_RESTART_DRAIN_TIMEOUT", "").strip()
    if not raw:
        try:
            import yaml as _y

            cfg_path = _hermes_home / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as _f:
                    cfg = _y.safe_load(_f) or {}
                raw = str(cfg.get("agent", {}).get("restart_drain_timeout", "") or "").strip()
        except Exception:
            pass
    value = parse_restart_drain_timeout(raw)
    if raw and value == DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT:
        try:
            float(raw)
        except (TypeError, ValueError):
            logger.warning(
                "Invalid restart_drain_timeout '%s', using default %.0fs",
                raw,
                DEFAULT_GATEWAY_RESTART_DRAIN_TIMEOUT,
            )
    return value


def load_background_notifications_mode() -> str:
    """Load background process notification mode from config or env var."""
    mode = os.getenv("HERMES_BACKGROUND_NOTIFICATIONS", "")
    if not mode:
        try:
            import yaml as _y

            cfg_path = _hermes_home / "config.yaml"
            if cfg_path.exists():
                with open(cfg_path, encoding="utf-8") as _f:
                    cfg = _y.safe_load(_f) or {}
                raw = cfg.get("display", {}).get("background_process_notifications")
                if raw is False:
                    mode = "off"
                elif raw not in (None, ""):
                    mode = str(raw)
        except Exception:
            pass
    mode = (mode or "all").strip().lower()
    valid = {"all", "result", "error", "off"}
    if mode not in valid:
        logger.warning(
            "Unknown background_process_notifications '%s', defaulting to 'all'",
            mode,
        )
        return "all"
    return mode


def load_provider_routing() -> dict:
    """Load OpenRouter provider routing preferences from config.yaml."""
    try:
        import yaml as _y

        cfg_path = _hermes_home / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as _f:
                cfg = _y.safe_load(_f) or {}
            return cfg.get("provider_routing", {}) or {}
    except Exception:
        pass
    return {}


def load_fallback_model() -> list | dict | None:
    """Load fallback provider chain from config.yaml."""
    try:
        import yaml as _y

        cfg_path = _hermes_home / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as _f:
                cfg = _y.safe_load(_f) or {}
            fb = cfg.get("fallback_providers") or cfg.get("fallback_model") or None
            if fb:
                return fb
    except Exception:
        pass
    return None


def load_smart_model_routing() -> dict:
    """Load optional smart cheap-vs-strong model routing config."""
    try:
        import yaml as _y

        cfg_path = _hermes_home / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as _f:
                cfg = _y.safe_load(_f) or {}
            return cfg.get("smart_model_routing", {}) or {}
    except Exception:
        pass
    return {}
