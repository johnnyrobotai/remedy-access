"""LiveKit voice-agent worker for Access Remedy.

Bridges a LiveKit room to the Gemini 2.5 Flash native-audio model (via the
Gemini Live API). The agent behaves like an Aira visual-interpreter: it sees
the user's screen-share and hears their microphone, and talks back.

Run with LIVEKIT_URL / API_KEY / API_SECRET and a GOOGLE_API_KEY with Live
API access:

    python agent/worker.py start

Without credentials, the backend's /api/live-token returns {enabled: false}
and the Assist panel in the viewer shows the "not configured" notice — the
whole system degrades gracefully to no-op.

The Live API model id is read from GEMINI_LIVE_MODEL so it can be bumped
when Google releases a newer native-audio preview.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli, function_tool
from livekit.plugins import google as google_plugin

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
log = logging.getLogger("access-remedy-agent")

SYSTEM_INSTRUCTIONS = """\
You are a live accessibility assistant for a blind or low-vision user who has
opened a document in the Access Remedy viewer. You can hear the user and see
their screen in real time.

Behave like an Aira agent:

- Begin with a short greeting and describe what is currently visible on
  screen (document title, which pane is active, the top heading).
- Offer choices, don't lecture: "Would you like me to read the current page,
  describe the form, or help you fill it in?"
- Read text clearly. For forms, read the label, explain what is expected,
  wait for the user, confirm what they say.
- Describe figures, charts, and layouts spatially.
- Help the user operate the viewer (toggle to Transcript View, open the
  outline, jump to a section, open the Ask panel).
- Keep responses short. Pause. Never talk over the user.
- Do not invent facts. If the user asks a factual question about the
  document, use the document_lookup tool to ground the answer and quote the
  passage.
- For medical, legal, or financial questions, quote the document verbatim
  and remind the user to consult a qualified professional.
"""


def _parse_room_metadata(ctx: JobContext) -> dict:
    raw = getattr(ctx.room, "metadata", "") or ""
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        log.warning("room metadata not valid JSON: %r", raw)
        return {}


def _build_document_lookup(src_url: str):
    """Return a FunctionTool that proxies to the backend /api/ask route so
    the Live model reuses the document's cached File Search store instead of
    re-indexing per session."""
    import httpx

    backend = os.getenv("ACCESS_REMEDY_BACKEND", "http://localhost:8000")

    @function_tool()
    async def document_lookup(question: str) -> str:
        """Look up a passage from the current document, grounded in its File
        Search index. Use this whenever the user asks a factual question
        about the document's content. Returns a short excerpt."""
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{backend}/api/ask",
                json={"src": src_url, "question": question},
            )
            r.raise_for_status()
            data = r.json()
            return data.get("answer") or "No grounded answer available."

    return document_lookup


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    metadata = _parse_room_metadata(ctx)
    src_url = metadata.get("src", "")
    file_search_store = metadata.get("file_search_store")
    log.info(
        "entering room %s src=%s store=%s",
        ctx.room.name,
        src_url,
        file_search_store,
    )

    tools = []
    if src_url and file_search_store:
        tools.append(_build_document_lookup(src_url))

    agent = Agent(instructions=SYSTEM_INSTRUCTIONS, tools=tools)

    session = AgentSession(
        llm=google_plugin.realtime.RealtimeModel(
            model=os.getenv(
                "GEMINI_LIVE_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025"
            ),
            voice="Aoede",
        ),
    )

    await session.start(room=ctx.room, agent=agent)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
