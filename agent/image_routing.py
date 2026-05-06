"""Route inbound user images to native multimodal input vs vision-analyze preprocessing.

Gateways and the CLI mirror the same routing policy as `AIAgent._model_supports_vision()`
so non-vision main models automatically use the auxiliary vision analyzer first.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

# Synthesized user-turn text for image-only (native multimodal + text routing).
# Shown to the LLM only, not as a separate chat bubble to the end user.
IMAGE_ONLY_USER_MESSAGE = (
    "The user sent an image without any accompanying text. "
    "Ask what they'd like you to do with it."
)

# Legacy CN placeholder — still recognized for queued / in-flight messages.
_LEGACY_IMAGE_ONLY_USER_MESSAGE_CN = (
    "用户仅发送了图片，未附加文字。请用中文简短确认已收到图片，并礼貌询问对方希望你怎么处理。"
)

IMAGE_ONLY_REPLY_GUIDANCE = (
    "[The user attached no caption. Briefly acknowledge receipt and ask what they "
    "want you to do with the image.]"
)


_SHARED_USER_PREFIX_RE = re.compile(r"^\[[^\]]+\]\s+")


def strip_shared_session_user_prefix(text: str) -> str:
    """Drop leading ``[DisplayName] `` from shared multi-user channel injections."""
    s = (text or "").strip()
    if not s:
        return s
    return _SHARED_USER_PREFIX_RE.sub("", s, count=1).strip()


def is_synthetic_image_only_user_text(text: str) -> bool:
    """True when ``text`` is the built-in image-only placeholder (EN or legacy CN)."""
    s = strip_shared_session_user_prefix(text)
    if not s:
        return False
    return s in (
        IMAGE_ONLY_USER_MESSAGE.strip(),
        _LEGACY_IMAGE_ONLY_USER_MESSAGE_CN.strip(),
    )


def decide_image_input_mode(
    provider: str,
    model: str,
    cfg: Mapping[str, Any] | None,
) -> str:
    """Return ``"native"`` when the active main model can accept inline images else ``"text"``.

    ``cfg`` may set ``agent.image_input_mode`` to ``native`` / ``text`` to
    bypass automatic capability detection.

    Mirrors `run_agent.AIAgent._model_supports_vision()` so gateway/CLI behave
    like the interactive agent strip policy.
    """
    prov = (provider or "").strip()
    mdl = (model or "").strip()
    if not prov or not mdl:
        return "text"

    forced = ""
    try:
        if isinstance(cfg, Mapping):
            agent_cfg = cfg.get("agent") or {}
            if isinstance(agent_cfg, dict):
                raw = agent_cfg.get("image_input_mode", "")
                if isinstance(raw, str):
                    forced = raw.strip().lower()
    except Exception:
        forced = ""

    if forced in ("native", "text"):
        return forced

    try:
        from agent.models_dev import get_model_capabilities

        caps = get_model_capabilities(prov, mdl)
        if caps is None or not caps.supports_vision:
            return "text"
        return "native"
    except Exception:
        return "text"


_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


def _mime_for_path(path: Path) -> str:
    return _MIME_BY_EXT.get(path.suffix.lower(), "image/jpeg")


def build_native_content_parts(
    text: str,
    image_paths: Sequence[str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Build OpenAI-style multimodal ``content`` parts for local images.

    Returns ``(parts, skipped_paths)`` where ``skipped_paths`` lists unreadable inputs.
    """
    parts: List[Dict[str, Any]] = []
    skipped: List[str] = []

    trimmed = text if isinstance(text, str) else ""
    trimmed = trimmed.strip()

    if trimmed:
        parts.append({"type": "text", "text": trimmed})

    for raw in image_paths:
        token = str(raw or "").strip()
        if not token:
            skipped.append(token or "<empty>")
            continue
        try:
            p = Path(token)
            if not p.is_file():
                skipped.append(token)
                continue
            mime = _mime_for_path(p)
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        except OSError:
            skipped.append(token)
        except Exception:
            skipped.append(token)

    return parts, skipped
