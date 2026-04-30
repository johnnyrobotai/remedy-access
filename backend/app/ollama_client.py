from __future__ import annotations

import base64
import io
from typing import Any

import httpx

from backend.app.config import get_settings


class OllamaError(RuntimeError):
    pass


def _sanitize_schema_for_ollama(value: Any) -> Any:
    """Drop JSON Schema keys that Ollama's structured output parser rejects."""
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            if key in {"additionalProperties", "default", "title"}:
                continue
            sanitized[key] = _sanitize_schema_for_ollama(nested)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_schema_for_ollama(item) for item in value]
    return value


def _client() -> httpx.AsyncClient:
    settings = get_settings()
    if settings.ollama_requires_api_key and not settings.ollama_api_key:
        raise OllamaError("OLLAMA_API_KEY is not configured")
    headers = {"Content-Type": "application/json"}
    if settings.ollama_api_key:
        headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
    return httpx.AsyncClient(
        base_url=settings.ollama_base_url.rstrip("/"),
        headers=headers,
        timeout=httpx.Timeout(
            getattr(settings, "gemini_call_timeout", getattr(settings, "llm_call_timeout", 120.0)),
            connect=30.0,
        ),
    )


async def chat_json(
    *,
    model: str,
    system_instruction: str,
    user_prompt: str,
    schema: dict[str, Any] | str,
    images: list[str] | None = None,
    temperature: float = 0.0,
    think: str | bool | None = None,
    num_predict: int = 32_768,
) -> tuple[str, dict[str, Any]]:
    """Call Ollama's `/api/chat` endpoint and return `(content, raw_json)`."""
    normalized_schema = (
        _sanitize_schema_for_ollama(schema) if isinstance(schema, dict) else schema
    )
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {
                "role": "user",
                "content": user_prompt,
                **({"images": images} if images else {}),
            },
        ],
        "stream": False,
        "format": normalized_schema,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
        },
    }
    if think is not None:
        payload["think"] = think

    async with _client() as client:
        resp = await client.post("/chat", json=payload)
        if resp.status_code >= 400:
            raise OllamaError(f"Ollama chat failed: {resp.status_code} {resp.text[:400]}")
        data = resp.json()
    try:
        content = data["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        raise OllamaError(f"Malformed Ollama response: {data}") from exc
    if not isinstance(content, str) or not content.strip():
        raise OllamaError("Ollama returned empty content")
    return content, data


def render_pdf_page_images(
    pdf_bytes: bytes,
    *,
    dpi: int | None = None,
    max_pages: int | None = None,
) -> list[str]:
    """Render PDF pages to base64 PNG strings for Ollama vision input."""
    import fitz

    settings = get_settings()
    effective_dpi = dpi or settings.ollama_page_image_dpi
    page_limit = max_pages if max_pages is not None else settings.ollama_max_page_images

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        out: list[str] = []
        for page_index, page in enumerate(doc):
            if page_limit and page_index >= page_limit:
                break
            pix = page.get_pixmap(dpi=effective_dpi, alpha=False)
            out.append(base64.b64encode(pix.tobytes("png")).decode("ascii"))
        return out
    finally:
        doc.close()


def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    except Exception:  # noqa: BLE001
        return ""
    pages: list[str] = []
    for page_num, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        pages.append(f"<!-- Page {page_num} -->\n{text.strip()}")
    return "\n\n".join(pages).strip()
