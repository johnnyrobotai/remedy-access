from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import ValidationError

from backend.app.config import get_settings
from backend.app.documents import DocFormat
from backend.app.gemini.client import get_genai_client, thinking_config
from backend.app.layout_plan_v2 import (
    LayoutPlan,
    LayoutPlanValidationError,
    heuristic_layout_plan,
    validate_layout_plan_for_document,
)
from backend.app.ollama_client import chat_json as ollama_chat_json
from backend.app.structured_parser.types import ParsedDocument

log = logging.getLogger(__name__)

LAYOUT_PLANNER_SYSTEM = """\
You are the LayoutPlan v2 planner for an accessible document-to-web pipeline.
You receive a structured ParsedDocument and choose composition decisions only.

Rules:
- Return JSON only, matching the provided schema exactly.
- Never rewrite, summarize, split, merge, or drop content.
- Emit one section layout per source `section` block, in the exact same pre-order.
- Inside each planned section, emit one block decision per direct child block, in the exact same order.
- `block_index` must match the source block position.
- Only `figure` and `callout` blocks may move to `placement: "rail"`.
- Only `reference_list` and `footnotes` blocks may move to `placement: "footer"`.
- Nested `section` blocks must remain `placement: "main"` with `container: "subsection"`.
- Use `container: "lead-prose"` only for a main-flow paragraph or quote.
- Choose layout intent, not rendering details. No HTML.
"""


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```")
    return text.strip()


async def plan_layout_v2(
    parsed_document: ParsedDocument,
    *,
    source_hint: str,
    fmt: DocFormat,
) -> LayoutPlan:
    settings = get_settings()
    fallback = heuristic_layout_plan(parsed_document, fmt=fmt.value)
    prompt = _build_prompt(parsed_document, source_hint=source_hint, fmt=fmt)

    if not getattr(settings, "layout_agent_enabled", False):
        return fallback

    if settings.llm_provider == "ollama":
        try:
            content, _raw = await asyncio.wait_for(
                ollama_chat_json(
                    model=settings.ollama_plan_model,
                    system_instruction=LAYOUT_PLANNER_SYSTEM,
                    user_prompt=prompt,
                    schema=LayoutPlan.model_json_schema(),
                    temperature=0.0,
                    think=settings.ollama_reasoning_level,
                ),
                timeout=max(45.0, settings.gemini_call_timeout / 2),
            )
            candidate = LayoutPlan.model_validate_json(_strip_json_fences(content))
            return validate_layout_plan_for_document(candidate, parsed_document)
        except Exception as e:  # noqa: BLE001
            log.warning("ollama layout planning failed for %s: %s", source_hint, e)
            if settings.google_api_key:
                return await _plan_layout_with_gemini(parsed_document, prompt, fallback)
            return fallback

    if not settings.google_api_key:
        return fallback

    return await _plan_layout_with_gemini(parsed_document, prompt, fallback)


async def _plan_layout_with_gemini(
    parsed_document: ParsedDocument,
    prompt: str,
    fallback: LayoutPlan,
) -> LayoutPlan:
    settings = get_settings()
    client = get_genai_client()
    from google.genai import types as gtypes

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.models.generate_content,
                model=settings.gemini_ask_model,
                contents=prompt,
                config=gtypes.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=LayoutPlan,
                    system_instruction=LAYOUT_PLANNER_SYSTEM,
                    temperature=0.0,
                    max_output_tokens=8192,
                    thinking_config=thinking_config(settings.gemini_ask_model),
                ),
            ),
            timeout=max(45.0, settings.gemini_call_timeout / 2),
        )
        parsed: Any = getattr(response, "parsed", None)
        if parsed is None:
            parsed = LayoutPlan.model_validate_json(_strip_json_fences(response.text))
        candidate = LayoutPlan.model_validate(parsed)
        return validate_layout_plan_for_document(candidate, parsed_document)
    except Exception as e:  # noqa: BLE001
        log.warning("gemini layout planning failed: %s", e)
        return fallback


def _build_prompt(
    parsed_document: ParsedDocument,
    *,
    source_hint: str,
    fmt: DocFormat,
) -> str:
    return (
        f"Source hint: {source_hint}\n"
        f"Format: {fmt.value}\n"
        "Choose a LayoutPlan v2 for this parsed document. Preserve content order and block identity.\n"
        + json.dumps(parsed_document.model_dump(mode="json"), indent=2)
    )
