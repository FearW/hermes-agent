from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

PROFILE_PATH = get_hermes_home() / "profile.json"

DEFAULT_PROFILE: Dict[str, Any] = {
    "name": "",
    "identity": "",
    "communication": {
        "language": "zh-CN",
        "style": "direct, structured, high-signal",
    },
    "preferences": [],
    "workstyle": [],
    "domains": [],
    "risk_profile": "balanced",
}


def ensure_user_profile() -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not PROFILE_PATH.exists():
        PROFILE_PATH.write_text(json.dumps(DEFAULT_PROFILE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_user_profile() -> Dict[str, Any]:
    ensure_user_profile()
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("profile load failed", exc_info=True)
        return dict(DEFAULT_PROFILE)
    if not isinstance(data, dict):
        return dict(DEFAULT_PROFILE)
    merged = dict(DEFAULT_PROFILE)
    merged.update(data)
    return merged


def build_user_profile_prompt() -> str:
    profile = load_user_profile()
    lines = ["## User Profile", ""]
    if profile.get("name"):
        lines.append(f"- Name: {profile['name']}")
    if profile.get("identity"):
        lines.append(f"- Identity: {profile['identity']}")
    communication = profile.get("communication") or {}
    if communication.get("language"):
        lines.append(f"- Preferred language: {communication['language']}")
    if communication.get("style"):
        lines.append(f"- Communication style: {communication['style']}")
    if profile.get("risk_profile"):
        lines.append(f"- Risk profile: {profile['risk_profile']}")
    for key, label in [("preferences", "Preferences"), ("workstyle", "Workstyle"), ("domains", "Domains")]:
        items = profile.get(key) or []
        if items:
            lines.append(f"- {label}: " + ", ".join(str(x) for x in items))
    if len(lines) <= 2:
        return ""
    return "\n".join(lines)
