from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import cache
from backend.app.pipeline_version import (
    STRUCTURED_RENDER_V2_TRANSCRIPT_PIPELINE_VERSION,
    TRANSCRIPT_PIPELINE_VERSION,
)
from backend.app.routes import transcript as transcript_routes


def test_get_transcript_returns_404_when_not_cached(client: TestClient) -> None:
    resp = client.get("/api/transcript", params={"src": "https://example.com/unknown.pdf"})
    assert resp.status_code == 404


def test_get_transcript_returns_cached(client: TestClient, tmp_data_dir) -> None:
    import asyncio

    async def seed():
        await cache.init_schema(tmp_data_dir / "cache.db")
        await cache.put(
            tmp_data_dir / "cache.db",
            sha256="sha_abc",
            source_url="https://example.com/seeded.pdf",
            html="<main lang=\"en\"><h1 id=\"h1-0\">Seeded</h1></main>",
            outline=[{"id": "h1-0", "level": 1, "text": "Seeded", "parent": None}],
            file_search_store="stores/xyz",
            page_count=2,
            title="Seeded",
            pipeline_version=TRANSCRIPT_PIPELINE_VERSION,
        )

    asyncio.run(seed())

    resp = client.get("/api/transcript", params={"src": "https://example.com/seeded.pdf"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is True
    assert body["title"] == "Seeded"
    assert body["outline"][0]["text"] == "Seeded"


def test_get_transcript_returns_cached_for_structured_render_v2(
    client: TestClient,
    tmp_data_dir,
) -> None:
    import asyncio

    async def seed():
        await cache.init_schema(tmp_data_dir / "cache.db")
        await cache.put(
            tmp_data_dir / "cache.db",
            sha256="sha_v2",
            source_url="https://example.com/seeded-v2.pdf",
            html="<main lang=\"en\"><h1 id=\"h1-0\">Seeded V2</h1></main>",
            outline=[{"id": "h1-0", "level": 1, "text": "Seeded V2", "parent": None}],
            file_search_store="stores/v2",
            page_count=1,
            title="Seeded V2",
            pipeline_version=STRUCTURED_RENDER_V2_TRANSCRIPT_PIPELINE_VERSION,
        )

    asyncio.run(seed())

    resp = client.get("/api/transcript", params={"src": "https://example.com/seeded-v2.pdf"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is True
    assert body["title"] == "Seeded V2"


def test_get_transcript_rejects_prior_structured_render_v2_cache(
    client: TestClient,
    tmp_data_dir,
) -> None:
    import asyncio

    async def seed():
        await cache.init_schema(tmp_data_dir / "cache.db")
        await cache.put(
            tmp_data_dir / "cache.db",
            sha256="sha_v2_unversioned",
            source_url="https://example.com/seeded-v2.pdf",
            html="<main lang=\"en\"><h1 id=\"h1-0\">Seeded V2</h1></main>",
            outline=[{"id": "h1-0", "level": 1, "text": "Seeded V2", "parent": None}],
            file_search_store="stores/v2",
            page_count=1,
            title="Seeded V2",
            pipeline_version="structured-render-v2",
        )
        await cache.put(
            tmp_data_dir / "cache.db",
            sha256="sha_v2_8",
            source_url="https://example.com/seeded-v2-8.pdf",
            html="<main lang=\"en\"><h1 id=\"h1-0\">Seeded V2.8</h1></main>",
            outline=[{"id": "h1-0", "level": 1, "text": "Seeded V2.8", "parent": None}],
            file_search_store="stores/v2-8",
            page_count=1,
            title="Seeded V2.8",
            pipeline_version="structured-render-v2.8",
        )

    asyncio.run(seed())

    for src in (
        "https://example.com/seeded-v2.pdf",
        "https://example.com/seeded-v2-8.pdf",
    ):
        resp = client.get("/api/transcript", params={"src": src})
        assert resp.status_code == 404


def test_ingest_requires_api_key(client: TestClient) -> None:
    resp = client.get(
        "/api/transcript/ingest", params={"src": "https://example.com/x.pdf"}
    )
    assert resp.status_code == 503
    assert "GOOGLE_API_KEY" in resp.json()["error"]


def test_trusted_fetch_hosts_expand_loopback_aliases() -> None:
    allowed = transcript_routes._trusted_fetch_hosts("localhost", "testserver")
    assert allowed >= {"localhost", "127.0.0.1", "::1", "testserver"}
