"""Sticker description cache for Telegram."""

import json
import time
from typing import Optional

from hermes_cli.config import get_hermes_home


CACHE_PATH = get_hermes_home() / "sticker_cache.json"

STICKER_VISION_PROMPT = (
    "\u8bf7\u7528\u4e2d\u6587\u7528 1-2 \u53e5\u8bdd\u63cf\u8ff0\u8fd9\u4e2a\u8d34\u7eb8\u3002"
    "\u91cd\u70b9\u63cf\u8ff0\u89d2\u8272\u3001\u52a8\u4f5c\u548c\u60c5\u7eea\u3002"
    "\u4fdd\u6301\u7b80\u6d01\u3001\u5ba2\u89c2\uff1b\u53ea\u6709\u8d34\u7eb8\u6587\u5b57\u672c\u8eab\u662f\u82f1\u6587\u65f6\u624d\u4fdd\u7559\u82f1\u6587\u3002"
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
    """Build Chinese Hermes injection text for a sticker description."""
    context = ""
    if set_name and emoji:
        context = f" {emoji}\uff0c\u6765\u81ea\u201c{set_name}\u201d"
    elif emoji:
        context = f" {emoji}"

    return f"[\u7528\u6237\u53d1\u9001\u4e86\u4e00\u4e2a\u8d34\u7eb8{context}\u3002\u8d34\u7eb8\u5185\u5bb9\uff1a\u201c{description}\u201d]"


def build_animated_sticker_injection(emoji: str = "") -> str:
    """Build injection text for animated/video stickers we can't analyze."""
    if emoji:
        return (
            f"[\u7528\u6237\u53d1\u9001\u4e86\u4e00\u4e2a\u52a8\u6001\u8d34\u7eb8 {emoji}\u3002"
            f"\u5f53\u524d\u65e0\u6cd5\u76f4\u63a5\u67e5\u770b\u52a8\u6001\u8d34\u7eb8\uff0c\u4f46\u8868\u60c5\u63d0\u793a\u4e3a\uff1a{emoji}]"
        )
    return "[\u7528\u6237\u53d1\u9001\u4e86\u4e00\u4e2a\u52a8\u6001\u8d34\u7eb8\u3002\u5f53\u524d\u65e0\u6cd5\u76f4\u63a5\u67e5\u770b\u52a8\u6001\u8d34\u7eb8\u3002]"
