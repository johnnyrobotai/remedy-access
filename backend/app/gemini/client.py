from __future__ import annotations

from functools import lru_cache
from typing import Any

from backend.app.config import get_settings


@lru_cache(maxsize=1)
def get_genai_client():
    from google import genai  # lazy so tests don't need google-genai installed to import the module

    settings = get_settings()
    if not settings.google_api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY is not configured. Set it in .env to enable Gemini calls."
        )
    return genai.Client(api_key=settings.google_api_key)


def thinking_config(model: str | None = None) -> Any | None:
    """Return a ThinkingConfig sourced from settings, or None to omit it.

    Empty `GEMINI_THINKING_LEVEL` (the default) means: don't pass thinking_config,
    let the model use its default. Useful for older Gemini families that don't
    accept thinking_level. Set to `"low"` / `"high"` etc. for 3.x models.

    Pass `model` so we can drop the config for families that reject it
    (Gemini 2.x returns 400 INVALID_ARGUMENT "Thinking level is not supported
    for this model"). Only Gemini 3.x accepts thinking_level on generate_content.
    """
    level = (get_settings().gemini_thinking_level or "").strip().lower()
    if not level:
        return None
    if model is not None and not model.startswith("gemini-3"):
        return None
    from google.genai import types as gtypes
    return gtypes.ThinkingConfig(thinking_level=level)
