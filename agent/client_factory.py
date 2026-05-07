import logging
from typing import Any, Optional, Tuple, TYPE_CHECKING

from utils import base_url_host_matches

if TYPE_CHECKING:
    from openai import OpenAI  # noqa: F401 — type hints only

logger = logging.getLogger(__name__)

_OPENAI_CLS_CACHE: Optional[type] = None


def _load_openai_cls() -> type:
    """Import and cache ``openai.OpenAI``."""
    global _OPENAI_CLS_CACHE
    if _OPENAI_CLS_CACHE is None:
        from openai import OpenAI as _cls
        _OPENAI_CLS_CACHE = _cls
    return _OPENAI_CLS_CACHE


class _OpenAIProxy:
    """Module-level proxy that looks like the ``openai.OpenAI`` class.

    Forwards ``OpenAI(...)`` calls and ``isinstance(x, OpenAI)`` checks to the
    real SDK class, importing the SDK lazily on first use.
    """

    __slots__ = ()

    def __call__(self, *args, **kwargs):
        return _load_openai_cls()(*args, **kwargs)

    def __instancecheck__(self, obj):
        return isinstance(obj, _load_openai_cls())

    def __repr__(self):
        return "<lazy openai.OpenAI proxy>"


OpenAI = _OpenAIProxy()  # module-level name, resolves lazily on call/isinstance


def _to_openai_base_url(base_url: str) -> str:
    """Normalize an Anthropic-style base URL to OpenAI-compatible format.

    Some providers (MiniMax, MiniMax-CN) expose an ``/anthropic`` endpoint for
    the Anthropic Messages API and a separate ``/v1`` endpoint for OpenAI chat
    completions.  The auxiliary client uses the OpenAI SDK, so it must hit the
    ``/v1`` surface.  Passing the raw ``inference_base_url`` causes requests to
    land on ``/anthropic/chat/completions`` — a 404.
    """
    url = str(base_url or "").strip().rstrip("/")
    if url.endswith("/anthropic"):
        rewritten = url[: -len("/anthropic")] + "/v1"
        logger.debug("Auxiliary client: rewrote base URL %s → %s", url, rewritten)
        return rewritten
    return url


def _to_async_client(sync_client, model: str, is_vision: bool = False):
    """Convert a sync client to its async counterpart, preserving Codex routing.

    When ``is_vision=True`` and the underlying base URL is Copilot, the
    resulting async client carries the ``Copilot-Vision-Request: true``
    header so the request is routed to Copilot's vision-capable
    infrastructure (otherwise vision payloads silently time out).
    """
    from agent.auxiliary_client import (
        AsyncAnthropicAuxiliaryClient,
        AsyncCodexAuxiliaryClient,
        AnthropicAuxiliaryClient,
        CodexAuxiliaryClient,
        _OR_HEADERS,
    )
    from openai import AsyncOpenAI

    if isinstance(sync_client, CodexAuxiliaryClient):
        return AsyncCodexAuxiliaryClient(sync_client), model
    if isinstance(sync_client, AnthropicAuxiliaryClient):
        return AsyncAnthropicAuxiliaryClient(sync_client), model
    try:
        from agent.gemini_native_adapter import GeminiNativeClient, AsyncGeminiNativeClient

        if isinstance(sync_client, GeminiNativeClient):
            return AsyncGeminiNativeClient(sync_client), model
    except ImportError:
        pass
    try:
        from agent.copilot_acp_client import CopilotACPClient
        if isinstance(sync_client, CopilotACPClient):
            return sync_client, model
    except ImportError:
        pass

    async_kwargs = {
        "api_key": sync_client.api_key,
        "base_url": str(sync_client.base_url),
    }
    sync_base_url = str(sync_client.base_url)
    if base_url_host_matches(sync_base_url, "openrouter.ai"):
        async_kwargs["default_headers"] = dict(_OR_HEADERS)
    elif base_url_host_matches(sync_base_url, "api.githubcopilot.com"):
        from hermes_cli.copilot_auth import copilot_request_headers

        async_kwargs["default_headers"] = copilot_request_headers(
            is_agent_turn=True, is_vision=is_vision
        )
    elif base_url_host_matches(sync_base_url, "api.kimi.com"):
        async_kwargs["default_headers"] = {"User-Agent": "claude-code/0.1.0"}
    return AsyncOpenAI(**async_kwargs), model


def _normalize_resolved_model(model_name: Optional[str], provider: str) -> Optional[str]:
    """Normalize a resolved model for the provider that will receive it."""
    if not model_name:
        return model_name
    try:
        from hermes_cli.model_normalize import normalize_model_for_provider

        return normalize_model_for_provider(model_name, provider)
    except Exception:
        return model_name
