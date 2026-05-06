import re
from typing import Any


def extract_content_or_reasoning(response) -> str:
    """Extract content from an LLM response, falling back to reasoning fields.

    Mirrors the main agent loop's behavior when a reasoning model (DeepSeek-R1,
    Qwen-QwQ, etc.) returns ``content=None`` with reasoning in structured fields.

    Resolution order:
      1. ``message.content`` — strip inline think/reasoning blocks, check for
         remaining non-whitespace text.
      2. ``message.reasoning`` / ``message.reasoning_content`` — direct
         structured reasoning fields (DeepSeek, Moonshot, Novita, etc.).
      3. ``message.reasoning_details`` — OpenRouter unified array format.

    Returns the best available text, or ``""`` if nothing found.
    """
    msg = response.choices[0].message
    content = (msg.content or "").strip()

    if content:
        cleaned = re.sub(
            r"<(?:think|thinking|reasoning|thought|REASONING_SCRATCHPAD)>"
            r".*?"
            r"</(?:think|thinking|reasoning|thought|REASONING_SCRATCHPAD)>",
            "", content, flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        if cleaned:
            return cleaned

    reasoning_parts: list[str] = []
    for field in ("reasoning", "reasoning_content"):
        val = getattr(msg, field, None)
        if val and isinstance(val, str) and val.strip() and val not in reasoning_parts:
            reasoning_parts.append(val.strip())

    details = getattr(msg, "reasoning_details", None)
    if details and isinstance(details, list):
        for detail in details:
            if isinstance(detail, dict):
                summary = (
                    detail.get("summary")
                    or detail.get("content")
                    or detail.get("text")
                )
                if summary and summary not in reasoning_parts:
                    reasoning_parts.append(summary.strip() if isinstance(summary, str) else str(summary))

    if reasoning_parts:
        return "\n\n".join(reasoning_parts)

    return ""
