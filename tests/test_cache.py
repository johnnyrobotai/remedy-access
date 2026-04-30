from __future__ import annotations

from pathlib import Path

import pytest

from backend.app import cache


async def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "cache.db"
    await cache.init_schema(db)
    return db


@pytest.mark.asyncio
async def test_put_and_get_by_hash_roundtrip(tmp_path: Path) -> None:
    db = await _fresh_db(tmp_path)
    await cache.put(
        db,
        sha256="abc123",
        source_url="https://example.com/a.pdf",
        html="<main><h1>Hello</h1></main>",
        outline=[{"id": "h1-0", "level": 1, "text": "Hello"}],
        file_search_store="stores/xyz",
        page_count=3,
        title="Hello",
    )

    got = await cache.get_by_hash(db, "abc123")
    assert got is not None
    assert got.html.startswith("<main>")
    assert got.outline[0]["text"] == "Hello"
    assert got.file_search_store == "stores/xyz"
    assert got.page_count == 3


@pytest.mark.asyncio
async def test_get_by_source_url(tmp_path: Path) -> None:
    db = await _fresh_db(tmp_path)
    await cache.put(
        db,
        sha256="deadbeef",
        source_url="https://example.com/form.pdf",
        html="<main></main>",
        outline=[],
        file_search_store=None,
        page_count=1,
    )
    got = await cache.get_by_source_url(db, "https://example.com/form.pdf")
    assert got is not None
    assert got.sha256 == "deadbeef"

    assert await cache.get_by_source_url(db, "https://example.com/other.pdf") is None


@pytest.mark.asyncio
async def test_put_is_idempotent_and_updates(tmp_path: Path) -> None:
    db = await _fresh_db(tmp_path)
    common = {
        "sha256": "x",
        "source_url": "https://x.test/a.pdf",
        "file_search_store": None,
        "page_count": 1,
    }
    await cache.put(db, html="<main>v1</main>", outline=[], **common)
    await cache.put(db, html="<main>v2</main>", outline=[], **common)
    got = await cache.get_by_hash(db, "x")
    assert got and "v2" in got.html


@pytest.mark.asyncio
async def test_url_alias_follows_rehash(tmp_path: Path) -> None:
    db = await _fresh_db(tmp_path)
    await cache.put(
        db,
        sha256="old",
        source_url="https://x.test/a.pdf",
        html="<main>old</main>",
        outline=[],
        file_search_store=None,
        page_count=1,
    )
    # Same URL, new bytes → alias should point at the new hash.
    await cache.put(
        db,
        sha256="new",
        source_url="https://x.test/a.pdf",
        html="<main>new</main>",
        outline=[],
        file_search_store=None,
        page_count=1,
    )
    got = await cache.get_by_source_url(db, "https://x.test/a.pdf")
    assert got and got.sha256 == "new"
