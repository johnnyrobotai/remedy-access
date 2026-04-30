from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.ollama_client import OllamaError, _client, _sanitize_schema_for_ollama


@pytest.mark.asyncio
async def test_ollama_client_allows_local_base_url_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.ollama_client.get_settings",
        lambda: SimpleNamespace(
            ollama_requires_api_key=False,
            ollama_api_key="",
            ollama_base_url="http://localhost:11434/api",
            llm_call_timeout=30.0,
        ),
    )

    client = _client()
    try:
        assert "Authorization" not in client.headers
    finally:
        await client.aclose()


def test_ollama_client_requires_api_key_for_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.ollama_client.get_settings",
        lambda: SimpleNamespace(
            ollama_requires_api_key=True,
            ollama_api_key="",
            ollama_base_url="https://ollama.com/api",
            llm_call_timeout=30.0,
        ),
    )

    with pytest.raises(OllamaError, match="OLLAMA_API_KEY"):
        _client()


def test_sanitize_schema_for_ollama_drops_problematic_keys() -> None:
    schema = {
        "title": "LayoutPlan",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sections": {
                "type": "array",
                "items": {
                    "title": "Section",
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string", "default": "x"},
                    },
                },
            }
        },
    }

    sanitized = _sanitize_schema_for_ollama(schema)
    assert "title" not in sanitized
    assert "additionalProperties" not in sanitized
    assert "title" not in sanitized["properties"]["sections"]["items"]
    assert "additionalProperties" not in sanitized["properties"]["sections"]["items"]
    assert "default" not in sanitized["properties"]["sections"]["items"]["properties"]["name"]
