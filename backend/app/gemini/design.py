from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import ValidationError

from backend.app.config import get_settings
from backend.app.document_design import DesignPlan, heuristic_design_plan
from backend.app.documents import DocFormat
from backend.app.gemini.client import get_genai_client, thinking_config
from backend.app.gemini import prompts
from backend.app.gemini.plan import DocumentPlan
from backend.app.ollama_client import chat_json as ollama_chat_json

log = logging.getLogger(__name__)


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```")
    return text.strip()


async def plan_design(
    content_plan: DocumentPlan,
    *,
    source_hint: str,
    fmt: DocFormat,
) -> DesignPlan:
    settings = get_settings()
    fallback = heuristic_design_plan(content_plan, fmt=fmt.value)
    prompt = (
        f"Source hint: {source_hint}\n"
        f"Format: {fmt.value}\n"
        "Create a DesignPlan for this content plan:\n"
        + json.dumps(content_plan.model_dump(), indent=2)
    )
    if settings.llm_provider == "ollama":
        try:
            content, _raw = await asyncio.wait_for(
                ollama_chat_json(
                    model=settings.ollama_plan_model,
                    system_instruction=prompts.DESIGN_PLANNER_SYSTEM,
                    user_prompt=prompt,
                    schema=DesignPlan.model_json_schema(),
                    temperature=0.0,
                    think=settings.ollama_reasoning_level,
                ),
                timeout=max(45.0, settings.gemini_call_timeout / 2),
            )
            return DesignPlan.model_validate_json(_strip_json_fences(content))
        except Exception as e:  # noqa: BLE001
            log.warning("ollama design planning failed for %s: %s", source_hint, e)
            if settings.google_api_key:
                return await _plan_design_with_gemini(content_plan, prompt, fallback)
            return fallback
    return await _plan_design_with_gemini(content_plan, prompt, fallback)


async def _plan_design_with_gemini(
    content_plan: DocumentPlan,
    prompt: str,
    fallback: DesignPlan,
) -> DesignPlan:
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
                    response_schema=DesignPlan,
                    system_instruction=prompts.DESIGN_PLANNER_SYSTEM,
                    temperature=0.0,
                    max_output_tokens=8192,
                    thinking_config=thinking_config(),
                ),
            ),
            timeout=max(45.0, settings.gemini_call_timeout / 2),
        )
        parsed = getattr(response, "parsed", None)
        if parsed is None:
            return DesignPlan.model_validate_json(response.text)
        return parsed
    except (ValidationError, ValueError, TimeoutError, RuntimeError) as e:
        log.warning("gemini design planning failed: %s", e)
        return fallback
