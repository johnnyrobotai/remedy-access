"""fetch_document end-to-end: verifies format auto-detection against fixture
bytes (PDF, DOCX, XLSX) without hitting the real network."""
from __future__ import annotations

import httpx
import pytest

from backend.app.documents import DocFormat
from backend.app.pdf_fetch import PdfFetchError, fetch_document


def _transport(body: bytes, content_type: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": content_type})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_fetch_document_detects_pdf(minimal_pdf_bytes: bytes) -> None:
    async with httpx.AsyncClient(transport=_transport(minimal_pdf_bytes, "application/pdf")) as c:
        result = await fetch_document(
            "https://example.com/x.pdf", max_bytes=1_000_000, client=c
        )
    assert result.fmt is DocFormat.PDF


@pytest.mark.asyncio
async def test_fetch_document_detects_docx(minimal_docx_bytes: bytes) -> None:
    async with httpx.AsyncClient(
        transport=_transport(
            minimal_docx_bytes,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    ) as c:
        result = await fetch_document(
            "https://example.com/memo.docx", max_bytes=1_000_000, client=c
        )
    assert result.fmt is DocFormat.DOCX


@pytest.mark.asyncio
async def test_fetch_document_detects_xlsx(gpa_calculator_xlsx_bytes: bytes) -> None:
    async with httpx.AsyncClient(
        transport=_transport(
            gpa_calculator_xlsx_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    ) as c:
        result = await fetch_document(
            "https://example.com/gpa.xlsx", max_bytes=1_000_000, client=c
        )
    assert result.fmt is DocFormat.XLSX


@pytest.mark.asyncio
async def test_fetch_document_rejects_mismatched_expected_format(
    minimal_pdf_bytes: bytes,
) -> None:
    async with httpx.AsyncClient(transport=_transport(minimal_pdf_bytes, "application/pdf")) as c:
        with pytest.raises(PdfFetchError, match="expected xlsx"):
            await fetch_document(
                "https://example.com/x.pdf",
                max_bytes=1_000_000,
                client=c,
                expected_format=DocFormat.XLSX,
            )


@pytest.mark.asyncio
async def test_fetch_document_rejects_unknown_type() -> None:
    transport = _transport(b"<html>not a doc</html>", "text/html")
    async with httpx.AsyncClient(transport=transport) as c:
        with pytest.raises(PdfFetchError, match="not a supported document"):
            await fetch_document(
                "https://example.com/x", max_bytes=1_000, client=c
            )
