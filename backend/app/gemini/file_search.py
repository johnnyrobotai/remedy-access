from __future__ import annotations

import asyncio
import io
import logging
import time
from dataclasses import dataclass
from typing import Any

from backend.app.config import get_settings
from backend.app.documents import DocFormat
from backend.app.gemini import prompts
from backend.app.gemini.client import get_genai_client, thinking_config

log = logging.getLogger(__name__)


@dataclass
class Citation:
    text: str
    heading_id: str | None = None


@dataclass
class AskResult:
    answer: str
    citations: list[Citation]
    store_name: str


async def ensure_store(
    sha256: str,
    doc_bytes: bytes,
    *,
    existing_store: str | None = None,
    fmt: DocFormat = DocFormat.PDF,
) -> str:
    """Create a FileSearchStore for this document if one does not exist yet, and
    index the bytes into it. Returns the store name. Idempotent when
    existing_store is passed in (we trust the cache). `fmt` drives the MIME
    type sent to Gemini; File Search itself is format-agnostic."""
    if existing_store:
        return existing_store

    client = get_genai_client()
    display = f"access-remedy-{sha256[:12]}"

    store = await asyncio.to_thread(
        client.file_search_stores.create,
        config={"display_name": display},
    )
    store_name: str = store.name

    # Gemini File Search rejects OOXML MIME types, so for DOCX/XLSX we
    # upload an extracted text representation (Markdown / JSON) as
    # `text/plain`. RAG quality is equivalent and the Ask panel stays
    # available for all three formats.
    if fmt is DocFormat.PDF:
        upload_bytes = doc_bytes
        upload_mime = "application/pdf"
        upload_ext = ".pdf"
    elif fmt is DocFormat.DOCX:
        from backend.app.extract import extract_docx_as_markdown

        upload_bytes = extract_docx_as_markdown(doc_bytes).encode("utf-8")
        upload_mime = "text/plain"
        upload_ext = ".md"
    elif fmt is DocFormat.XLSX:
        from backend.app.extract import extract_xlsx_as_json

        upload_bytes = extract_xlsx_as_json(doc_bytes).encode("utf-8")
        upload_mime = "text/plain"
        upload_ext = ".json"
    else:  # pragma: no cover — DocFormat is a closed enum
        upload_bytes = doc_bytes
        upload_mime = fmt.mime_type
        upload_ext = fmt.extension

    doc_io = io.BytesIO(upload_bytes)
    op = await asyncio.to_thread(
        client.file_search_stores.upload_to_file_search_store,
        file=doc_io,
        file_search_store_name=store_name,
        config={
            "display_name": f"{display}{upload_ext}",
            "mime_type": upload_mime,
        },
    )

    # Long-running op. Poll briefly; the index is usually queryable in a few
    # seconds even when `op.done` stays False (the google-genai LRO shape
    # doesn't refresh cleanly via client.operations.get(op) in the installed
    # SDK). Short timeout + quick return so ingest doesn't block on it; the
    # store is functional by the time /api/ask is called in practice.
    deadline = time.monotonic() + 10
    while getattr(op, "done", False) is False and time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        try:
            op = await asyncio.to_thread(client.operations.get, op)
        except Exception:
            break

    if not getattr(op, "done", False):
        log.info("file_search index still not marked done after poll for %s — continuing", store_name)

    return store_name


async def query(store_name: str, question: str) -> AskResult:
    settings = get_settings()
    client = get_genai_client()
    from google.genai import types as gtypes

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=settings.gemini_ask_model,
        contents=question,
        config=gtypes.GenerateContentConfig(
            system_instruction=prompts.ASK_SYSTEM,
            tools=[
                gtypes.Tool(
                    file_search=gtypes.FileSearch(file_search_store_names=[store_name])
                )
            ],
            temperature=0.1,
            thinking_config=thinking_config(settings.gemini_ask_model),
        ),
    )

    answer = getattr(response, "text", "") or ""
    citations = _extract_citations(response)
    return AskResult(answer=answer.strip(), citations=citations, store_name=store_name)


def _extract_citations(response: Any) -> list[Citation]:
    citations: list[Citation] = []
    try:
        cand = response.candidates[0]
        gm = getattr(cand, "grounding_metadata", None)
        if not gm:
            return citations
        for support in getattr(gm, "grounding_supports", []) or []:
            text = getattr(support, "segment", None)
            if text and getattr(text, "text", None):
                citations.append(Citation(text=text.text))
        for chunk in getattr(gm, "grounding_chunks", []) or []:
            web = getattr(chunk, "retrieved_context", None) or getattr(chunk, "file", None)
            if web and getattr(web, "text", None):
                citations.append(Citation(text=web.text))
    except (AttributeError, IndexError, KeyError) as e:
        log.debug("no citations extracted: %s", e)
    return citations
