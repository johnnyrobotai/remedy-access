from __future__ import annotations

import json

import jwt
import pytest
from fastapi.testclient import TestClient

from backend.app import cache


def test_live_token_disabled_without_env(client: TestClient) -> None:
    resp = client.post("/api/live-token", json={"src": "https://example.com/a.pdf"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["token"] is None


@pytest.fixture
def livekit_client(tmp_data_dir, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://test.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "APItestkey")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "a" * 32)
    from backend.app.config import get_settings

    get_settings.cache_clear()
    from backend.app.main import app

    with TestClient(app) as c:
        yield c


def _decode(token: str) -> dict:
    return jwt.decode(token, "a" * 32, algorithms=["HS256"])


def test_live_token_enabled_no_cache(livekit_client: TestClient) -> None:
    resp = livekit_client.post(
        "/api/live-token", json={"src": "https://example.com/uncached.pdf"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["url"] == "wss://test.livekit.cloud"
    assert body["room"].startswith("doc-")

    claims = _decode(body["token"])
    assert claims["video"]["roomJoin"] is True
    assert claims["video"]["room"] == body["room"]
    assert set(claims["video"]["canPublishSources"]) == {
        "microphone",
        "screen_share",
        "screen_share_audio",
    }

    room_metadata = json.loads(claims["roomConfig"]["metadata"])
    assert room_metadata["src"] == "https://example.com/uncached.pdf"
    assert room_metadata["file_search_store"] is None
    assert claims["roomConfig"]["name"] == body["room"]


def test_live_token_populates_store_from_cache(
    livekit_client: TestClient, tmp_data_dir
) -> None:
    import asyncio

    async def seed():
        await cache.init_schema(tmp_data_dir / "cache.db")
        await cache.put(
            tmp_data_dir / "cache.db",
            sha256="sha_live",
            source_url="https://example.com/cached.pdf",
            html="<main lang=\"en\"><h1 id=\"h1-0\">X</h1></main>",
            outline=[{"id": "h1-0", "level": 1, "text": "X", "parent": None}],
            file_search_store="fileSearchStores/abc123",
            page_count=1,
        )

    asyncio.run(seed())

    resp = livekit_client.post(
        "/api/live-token", json={"src": "https://example.com/cached.pdf"}
    )
    assert resp.status_code == 200
    claims = _decode(resp.json()["token"])
    room_metadata = json.loads(claims["roomConfig"]["metadata"])
    assert room_metadata["src"] == "https://example.com/cached.pdf"
    assert room_metadata["file_search_store"] == "fileSearchStores/abc123"
