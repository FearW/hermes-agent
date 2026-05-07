"""Shared helpers for direct xAI HTTP integrations."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def hermes_xai_user_agent() -> str:
    """Return a stable Hermes-specific User-Agent for xAI HTTP calls."""
    try:
        from hermes_cli import __version__
    except Exception:
        logger.debug("hermes_xai_user_agent failed", exc_info=True)
        __version__ = "unknown"

    return f"Hermes-Agent/{__version__}"
