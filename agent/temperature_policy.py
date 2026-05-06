import logging
from typing import Optional

logger = logging.getLogger(__name__)

OMIT_TEMPERATURE: object = object()


def _is_kimi_model(model: Optional[str]) -> bool:
    bare = (model or "").strip().lower().rsplit("/", 1)[-1]
    return bare.startswith("kimi-") or bare == "kimi"


def _fixed_temperature_for_model(
    model: Optional[str],
    base_url: Optional[str] = None,
) -> "Optional[float] | object":
    if _is_kimi_model(model):
        logger.debug("Omitting temperature for Kimi model %r (server-managed)", model)
        return OMIT_TEMPERATURE
    return None


fixed_temperature_for_model = _fixed_temperature_for_model
