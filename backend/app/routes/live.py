from __future__ import annotations

import json
import secrets
import time

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app import cache
from backend.app.config import get_settings

router = APIRouter()


class LiveTokenRequest(BaseModel):
    src: str


class LiveTokenResponse(BaseModel):
    enabled: bool
    token: str | None = None
    url: str | None = None
    room: str | None = None


@router.post("/live-token", response_model=LiveTokenResponse)
async def live_token(req: LiveTokenRequest) -> LiveTokenResponse:
    settings = get_settings()
    if not settings.livekit_configured:
        return LiveTokenResponse(enabled=False)

    try:
        from livekit import api as lkapi
        from livekit.protocol.room import RoomConfiguration
    except ImportError:
        return LiveTokenResponse(enabled=False)

    identity = f"user-{secrets.token_hex(4)}"
    room = f"doc-{int(time.time())}-{secrets.token_hex(3)}"

    transcript = await cache.get_by_source_url(settings.cache_db_path, req.src)
    room_metadata = json.dumps(
        {
            "src": req.src,
            "file_search_store": transcript.file_search_store if transcript else None,
        }
    )

    token = (
        lkapi.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name("viewer")
        .with_grants(
            lkapi.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_sources=["microphone", "screen_share", "screen_share_audio"],
            )
        )
        .with_room_config(RoomConfiguration(name=room, metadata=room_metadata))
        .to_jwt()
    )

    return LiveTokenResponse(
        enabled=True, token=token, url=settings.livekit_url, room=room
    )
