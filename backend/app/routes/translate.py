from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Sequence

from bs4 import BeautifulSoup, NavigableString
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app import cache
from backend.app.a11y import check_html, fix_html
from backend.app.config import get_settings
from backend.app.ollama_client import OllamaError, chat_json as ollama_chat_json

router = APIRouter()


class TranslateRequest(BaseModel):
    src: str
    target_lang: str = Field(min_length=2, max_length=35)


TRANSLATE_SYSTEM = """\
Translate accessible document text while preserving structure.

Rules:
- Translate each source string into the requested language.
- Preserve numbers, dates, punctuation, and form option semantics.
- Do not add explanations, Markdown, code fences, or extra keys.
- Return JSON only with key translations.
- translations must be an array with exactly the same length and order as the input strings.
"""

_MAX_TRANSLATION_BATCH_ITEMS = 32
_MAX_TRANSLATION_BATCH_CHARS = 5_500
_TRANSLATABLE_ATTRS = ("alt", "title", "aria-label", "placeholder")


@dataclass
class _TranslationSlot:
    text: str
    node: NavigableString | None = None
    tag: Any | None = None
    attr: str | None = None


@router.post("/translate")
async def translate_transcript(req: TranslateRequest) -> dict:
    settings = get_settings()
    transcript = await cache.get_by_source_url(settings.cache_db_path, req.src)
    if transcript is None:
        raise HTTPException(404, "document not cached")
    if transcript.render_mode == "interactive":
        raise HTTPException(400, "interactive transcripts cannot be translated")

    original_heading_ids = _heading_ids(transcript.html)
    soup = BeautifulSoup(transcript.html, "html.parser")
    slots = _translation_slots(soup)
    if slots:
        try:
            translated = await _translate_texts(
                [slot.text for slot in slots],
                target_lang=req.target_lang,
                model=settings.ollama_remediation_model,
                think=settings.ollama_reasoning_level,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"translation failed: {exc}") from exc
        for slot, translated_text in zip(slots, translated, strict=True):
            if slot.node is not None:
                slot.node.replace_with(translated_text)
            elif slot.tag is not None and slot.attr is not None:
                slot.tag[slot.attr] = translated_text

    main = soup.find("main")
    if main is not None:
        main["lang"] = req.target_lang
    html = str(soup)
    html = fix_html(html)
    if _heading_ids(html) != original_heading_ids:
        raise HTTPException(502, "translation changed transcript structure")
    issues = check_html(html, render_mode=transcript.render_mode)
    if issues:
        raise HTTPException(502, "translated transcript failed accessibility checks")

    outline = _outline_from_html(html)
    title = outline[0]["text"] if outline else transcript.title
    return {
        "src": req.src,
        "target_lang": req.target_lang,
        "html": html,
        "outline": outline,
        "title": title,
        "description": transcript.description,
        "page_count": transcript.page_count,
        "format": transcript.format,
        "render_mode": transcript.render_mode,
    }


def _translation_slots(soup: BeautifulSoup) -> list[_TranslationSlot]:
    slots: list[_TranslationSlot] = []
    for node in soup.find_all(string=True):
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent is None or parent.name in {"script", "style", "noscript"}:
            continue
        text = str(node)
        if not _should_translate_text(text):
            continue
        slots.append(_TranslationSlot(text=text, node=node))

    for tag in soup.find_all(True):
        for attr in _TRANSLATABLE_ATTRS:
            value = tag.get(attr)
            if isinstance(value, str) and _should_translate_text(value):
                slots.append(_TranslationSlot(text=value, tag=tag, attr=attr))
    return slots


def _should_translate_text(value: str) -> bool:
    text = value.strip()
    return bool(text and re.search(r"[A-Za-zÀ-ÿ]", text))


async def _translate_texts(
    texts: list[str],
    *,
    target_lang: str,
    model: str,
    think: str | bool | None,
) -> list[str]:
    translated: list[str] = []
    for batch in _translation_batches(texts):
        translated.extend(
            await _translate_batch(
                batch,
                target_lang=target_lang,
                model=model,
                think=think,
            )
        )
    return translated


def _translation_batches(texts: list[str]) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for text in texts:
        size = len(text)
        if current and (
            len(current) >= _MAX_TRANSLATION_BATCH_ITEMS
            or current_chars + size > _MAX_TRANSLATION_BATCH_CHARS
        ):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(text)
        current_chars += size
    if current:
        batches.append(current)
    return batches


async def _translate_batch(
    texts: list[str],
    *,
    target_lang: str,
    model: str,
    think: str | bool | None,
) -> list[str]:
    schema = {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["translations"],
    }
    prompt = (
        f"Target language: {target_lang}\n"
        f"Input count: {len(texts)}\n"
        "Translate the JSON array values and return exactly the JSON object "
        '{"translations":[...]} with the same count and order.\n\n'
        f"Input strings:\n{json.dumps(texts, ensure_ascii=False)}"
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            content, _raw = await asyncio.wait_for(
                ollama_chat_json(
                    model=model,
                    system_instruction=TRANSLATE_SYSTEM,
                    user_prompt=prompt if attempt == 0 else prompt + "\n\nPrevious response was invalid. Return strict JSON only.",
                    schema=schema,
                    temperature=0.0,
                    think=False,
                    num_predict=_translation_num_predict(texts),
                ),
                timeout=45,
            )
            payload = _load_model_json(content)
            translations = payload.get("translations")
            if not isinstance(translations, list):
                raise ValueError("missing translations array")
            if len(translations) != len(texts):
                raise ValueError(
                    f"translation count mismatch: expected {len(texts)}, got {len(translations)}"
                )
            return [str(item) for item in translations]
        except (json.JSONDecodeError, ValueError, OllamaError, TimeoutError) as exc:
            last_error = exc
            continue
    raise RuntimeError(f"translation model returned invalid JSON: {last_error}")


def _translation_num_predict(texts: Sequence[str]) -> int:
    # The response is only a JSON array of translated strings. Keeping the
    # generation cap tight makes cloud-hosted models return promptly instead of
    # spending a long time on a full-document-sized budget.
    return min(8_192, max(512, sum(len(text) for text in texts) * 3 + 256))


def _load_model_json(content: str) -> dict[str, Any]:
    text = _strip_json_fences(content)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            raise
        payload, _end = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(payload, dict):
        raise ValueError("translation model returned a non-object JSON value")
    return payload


def _strip_json_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _heading_ids(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [str(tag.get("id") or "") for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])]


def _outline_from_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    outline: list[dict] = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        heading_id = tag.get("id")
        if not heading_id:
            continue
        outline.append(
            {
                "id": str(heading_id),
                "level": int(tag.name[1]),
                "text": tag.get_text(" ", strip=True),
                "parent": None,
            }
        )
    return outline
