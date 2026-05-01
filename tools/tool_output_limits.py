"""Backward-compatible terminal output limit helpers.

Older terminal paths import ``tools.tool_output_limits.get_max_bytes`` before
the newer budget registry trims/persists tool results.  Keep this tiny module as
the canonical compatibility shim so foreground command execution never fails at
import time.
"""

from __future__ import annotations

import os

from tools.budget_config import DEFAULT_RESULT_SIZE_CHARS


def get_max_bytes(default: int = DEFAULT_RESULT_SIZE_CHARS) -> int:
    """Return the max inline terminal output size in characters.

    The historical name says "bytes", but callers slice Python strings, so this
    intentionally returns a character count.  ``HERMES_TOOL_OUTPUT_MAX_BYTES``
    remains supported for VPS deployments that already set it.
    """
    raw = os.getenv("HERMES_TOOL_OUTPUT_MAX_BYTES") or os.getenv("HERMES_TOOL_OUTPUT_MAX_CHARS")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return int(default)
