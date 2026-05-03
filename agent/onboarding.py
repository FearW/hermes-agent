from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from utils import atomic_yaml_write

BUSY_INPUT_FLAG = "busy_input_prompt"
TOOL_PROGRESS_FLAG = "tool_progress_prompt"


def _seen_map(cfg: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(cfg, dict):
        return {}
    onboarding = cfg.get("onboarding")
    if not isinstance(onboarding, dict):
        return {}
    seen = onboarding.get("seen")
    return seen if isinstance(seen, dict) else {}


def is_seen(cfg: dict[str, Any] | None, flag: str) -> bool:
    return bool(_seen_map(cfg).get(flag))


def mark_seen(config_path: Path, flag: str) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if config_path.exists():
        try:
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}

    onboarding = data.setdefault("onboarding", {})
    if not isinstance(onboarding, dict):
        onboarding = {}
        data["onboarding"] = onboarding
    seen = onboarding.setdefault("seen", {})
    if not isinstance(seen, dict):
        seen = {}
        onboarding["seen"] = seen
    seen[flag] = True
    atomic_yaml_write(config_path, data)


def busy_input_hint_gateway(mode: str) -> str:
    mode = str(mode or "interrupt").strip().lower()
    if mode == "queue":
        suggestion = "/busy interrupt"
        desc = "把后续忙碌时收到的消息改为打断当前运行"
    elif mode == "steer":
        suggestion = "/busy interrupt"
        desc = "把后续忙碌时收到的消息改为打断，而不是引导当前运行"
    else:
        suggestion = "/busy queue"
        desc = "把后续忙碌时收到的消息排到下一轮处理"
    return f"首次提示：可使用 `{suggestion}` 来{desc}。"


def tool_progress_hint_gateway() -> str:
    return "首次提示：可使用 `/verbose` 显示更多实时工具进度更新。"
