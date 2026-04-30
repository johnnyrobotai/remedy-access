"""ensure_store format dispatch — PDF uploads raw bytes with
application/pdf; DOCX/XLSX upload extracted text as text/plain because
Gemini File Search rejects OOXML MIME types. The existing_store
idempotency shortcut must still return early without touching the client.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.app.documents import DocFormat
from backend.app.gemini import file_search


def _install_fake_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {
        "create_calls": 0,
        "upload_calls": 0,
        "upload_mime": None,
        "upload_bytes": None,
        "display_name": None,
    }

    class FakeStore:
        name = "fileSearchStores/fake-store"

    class FakeOp:
        done = True

    class FakeFileSearchStores:
        def create(self, *, config):
            seen["create_calls"] += 1
            return FakeStore()

        def upload_to_file_search_store(self, *, file, file_search_store_name, config):
            seen["upload_calls"] += 1
            # file is a BytesIO — capture the bytes so assertions can peek.
            seen["upload_bytes"] = file.getvalue() if hasattr(file, "getvalue") else file.read()
            seen["upload_mime"] = config["mime_type"]
            seen["display_name"] = config["display_name"]
            seen["file_search_store_name"] = file_search_store_name
            return FakeOp()

    class FakeOperations:
        def get(self, op):
            return op

    class FakeClient:
        file_search_stores = FakeFileSearchStores()
        operations = FakeOperations()

    monkeypatch.setattr(
        "backend.app.gemini.file_search.get_genai_client", lambda: FakeClient()
    )
    return seen


@pytest.mark.asyncio
async def test_ensure_store_pdf_uploads_raw_bytes_with_pdf_mime(
    monkeypatch: pytest.MonkeyPatch,
    minimal_pdf_bytes: bytes,
) -> None:
    seen = _install_fake_client(monkeypatch)

    name = await file_search.ensure_store(
        "a" * 64, minimal_pdf_bytes, fmt=DocFormat.PDF
    )

    assert name == "fileSearchStores/fake-store"
    assert seen["upload_calls"] == 1
    assert seen["upload_mime"] == "application/pdf"
    assert seen["upload_bytes"] == minimal_pdf_bytes
    assert seen["display_name"].endswith(".pdf")


@pytest.mark.asyncio
async def test_ensure_store_docx_uploads_extracted_markdown_as_text_plain(
    monkeypatch: pytest.MonkeyPatch,
    minimal_docx_bytes: bytes,
) -> None:
    seen = _install_fake_client(monkeypatch)

    await file_search.ensure_store(
        "b" * 64, minimal_docx_bytes, fmt=DocFormat.DOCX
    )

    assert seen["upload_mime"] == "text/plain"
    assert seen["display_name"].endswith(".md")
    decoded = seen["upload_bytes"].decode("utf-8")
    assert "Sample Memo" in decoded
    # Should NOT be the raw OOXML zip bytes.
    assert not seen["upload_bytes"].startswith(b"PK\x03\x04")


@pytest.mark.asyncio
async def test_ensure_store_xlsx_uploads_extracted_json_as_text_plain(
    monkeypatch: pytest.MonkeyPatch,
    gpa_calculator_xlsx_bytes: bytes,
) -> None:
    seen = _install_fake_client(monkeypatch)

    await file_search.ensure_store(
        "c" * 64, gpa_calculator_xlsx_bytes, fmt=DocFormat.XLSX
    )

    assert seen["upload_mime"] == "text/plain"
    assert seen["display_name"].endswith(".json")
    decoded = seen["upload_bytes"].decode("utf-8")
    # Formula strings preserved in the JSON dump.
    assert "=SUMPRODUCT(B2:B5,C2:C5)/SUM(C2:C5)" in decoded
    assert not seen["upload_bytes"].startswith(b"PK\x03\x04")


@pytest.mark.asyncio
async def test_ensure_store_existing_store_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
    minimal_pdf_bytes: bytes,
) -> None:
    seen = _install_fake_client(monkeypatch)

    name = await file_search.ensure_store(
        "d" * 64,
        minimal_pdf_bytes,
        existing_store="fileSearchStores/cached",
        fmt=DocFormat.PDF,
    )

    assert name == "fileSearchStores/cached"
    assert seen["create_calls"] == 0
    assert seen["upload_calls"] == 0
