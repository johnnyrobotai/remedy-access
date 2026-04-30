from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app import cache
from backend.app.config import get_settings
from backend.app.gemini import file_search

router = APIRouter()


class AskRequest(BaseModel):
    src: str
    question: str


class AskResponse(BaseModel):
    answer: str
    citations: list[dict[str, str | None]]


@router.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    settings = get_settings()
    if not settings.google_api_key:
        raise HTTPException(503, "GOOGLE_API_KEY not configured")

    transcript = await cache.get_by_source_url(settings.cache_db_path, req.src)
    if transcript is None:
        raise HTTPException(
            404, "document not cached — request /api/transcript first"
        )
    if not transcript.file_search_store:
        raise HTTPException(503, "this document has no File Search store")

    result = await file_search.query(transcript.file_search_store, req.question)
    return AskResponse(
        answer=result.answer,
        citations=[{"text": c.text, "heading_id": c.heading_id} for c in result.citations],
    )
