from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from backend.app import cache


def test_translate_requires_cached_transcript(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    from backend.app.config import get_settings

    get_settings.cache_clear()

    response = client.post(
        "/api/translate",
        json={
            "src": "http://127.0.0.1:8000/demo/pdfs/missing.pdf",
            "target_lang": "es",
        },
    )

    assert response.status_code == 404


def test_translate_rejects_interactive_transcripts(
    client: TestClient,
    tmp_data_dir,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    from backend.app.config import get_settings

    get_settings.cache_clear()

    src = "http://127.0.0.1:8000/demo/pdfs/GPA_Calculator.xlsx"
    asyncio.run(
        cache.put(
            tmp_data_dir / "cache.db",
            sha256="abc123",
            source_url=src,
            html='<main id="content" lang="en"><h1 id="document-title">GPA Calculator</h1></main>',
            outline=[{"id": "document-title", "level": 1, "text": "GPA Calculator", "parent": None}],
            file_search_store=None,
            page_count=1,
            title="GPA Calculator",
            description="",
            format="xlsx",
            render_mode="interactive",
            pipeline_version="structured-render-v2",
        )
    )

    response = client.post(
        "/api/translate",
        json={"src": src, "target_lang": "es"},
    )

    assert response.status_code == 400


def test_translate_returns_translated_html_and_outline(
    client: TestClient,
    tmp_data_dir,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    from backend.app.config import get_settings

    get_settings.cache_clear()

    src = "http://127.0.0.1:8000/demo/pdfs/example.pdf"
    original_html = (
        '<main id="content" lang="en">'
        '<h1 id="document-title">Example Document</h1>'
        '<section id="intro"><h2 id="intro">Introduction</h2><p>Hello world.</p></section>'
        "</main>"
    )
    asyncio.run(
        cache.put(
            tmp_data_dir / "cache.db",
            sha256="xyz789",
            source_url=src,
            html=original_html,
            outline=[
                {"id": "document-title", "level": 1, "text": "Example Document", "parent": None},
                {"id": "intro", "level": 2, "text": "Introduction", "parent": None},
            ],
            file_search_store=None,
            page_count=1,
            title="Example Document",
            description="",
            format="pdf",
            render_mode="static",
            pipeline_version="structured-render-v2",
        )
    )

    async def fake_ollama_chat_json(**_kwargs):
        return (
            json.dumps(
                {
                    "translations": [
                        "Documento de ejemplo",
                        "Introducción",
                        "Hola mundo.",
                    ],
                }
            ),
            {},
        )

    monkeypatch.setattr("backend.app.routes.translate.ollama_chat_json", fake_ollama_chat_json)

    response = client.post(
        "/api/translate",
        json={"src": src, "target_lang": "es"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["target_lang"] == "es"
    assert payload["title"] == "Documento de ejemplo"
    assert payload["outline"][0]["text"] == "Documento de ejemplo"
    assert payload["outline"][1]["text"] == "Introducción"
    assert 'lang="es"' in payload["html"]


def test_translate_retries_invalid_json_response(
    client: TestClient,
    tmp_data_dir,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    from backend.app.config import get_settings

    get_settings.cache_clear()

    src = "http://127.0.0.1:8000/demo/pdfs/example.pdf"
    asyncio.run(
        cache.put(
            tmp_data_dir / "cache.db",
            sha256="xyz789",
            source_url=src,
            html='<main id="content" lang="en"><h1 id="document-title">Example</h1></main>',
            outline=[{"id": "document-title", "level": 1, "text": "Example", "parent": None}],
            file_search_store=None,
            page_count=1,
            title="Example",
            description="",
            format="pdf",
            render_mode="static",
            pipeline_version="structured-render-v2",
        )
    )

    calls = 0

    async def fake_ollama_chat_json(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ("not json", {})
        return (json.dumps({"translations": ["Ejemplo"]}), {})

    monkeypatch.setattr("backend.app.routes.translate.ollama_chat_json", fake_ollama_chat_json)

    response = client.post(
        "/api/translate",
        json={"src": src, "target_lang": "es"},
    )

    assert response.status_code == 200
    assert calls == 2
    assert response.json()["title"] == "Ejemplo"
