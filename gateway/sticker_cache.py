"""Sticker description cache for Telegram."""

import json
import time
from typing import Optional

from hermes_constants import get_hermes_home


CACHE_PATH = get_hermes_home() / "sticker_cache.json"

STICKER_VISION_PROMPT = (
    "请用中文用 1-2 句话描述这个贴纸。"
    "重点描述角色、动作和情绪。"
    "保持简洁、客观；只有贴纸文字本身是英文时才保留英文。"
)


def _load_cache() -> dict:
    """Load the sticker cache from disk."""
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    """Save the sticker cache to disk."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_cached_description(file_unique_id: str) -> Optional[dict]:
    """Look up a cached sticker description."""
    cache = _load_cache()
    return cache.get(file_unique_id)


def cache_sticker_description(
    file_unique_id: str,
    description: str,
    emoji: str = "",
    set_name: str = "",
) -> None:
    """Store a sticker description in the cache."""
    cache = _load_cache()
    cache[file_unique_id] = {
        "description": description,
        "emoji": emoji,
        "set_name": set_name,
        "cached_at": time.time(),
    }
    _save_cache(cache)


def build_sticker_injection(
    description: str,
    emoji: str = "",
    set_name: str = "",
) -> str:
    """Build the canonical sticker injection text used in chat history."""
    if set_name and emoji:
        return f'[The user sent a sticker {emoji} from "{set_name}"~ It shows: "{description}" (=^.w.^=)]'
    if emoji:
        return f'[The user sent a sticker {emoji}~ It shows: "{description}" (=^.w.^=)]'
    return f'[The user sent a sticker~ It shows: "{description}" (=^.w.^=)]'


def build_animated_sticker_injection(emoji: str = "") -> str:
    """Build injection text for animated/video stickers we can't analyze."""
    if emoji:
        return (
            f"[The user sent an animated sticker {emoji}~ "
            f"I can't see animated ones yet, but the emoji suggests: {emoji}]"
        )
    return "[The user sent an animated sticker~ I can't see animated ones yet]"
