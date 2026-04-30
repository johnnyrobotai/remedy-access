from __future__ import annotations

import httpx
import pytest

from backend.app.pdf_fetch import PdfFetchError, fetch_pdf, sha256_of


def _mock_transport(pdf_bytes: bytes) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=pdf_bytes,
            headers={"content-type": "application/pdf", "content-length": str(len(pdf_bytes))},
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_fetch_pdf_returns_bytes_and_hash(minimal_pdf_bytes: bytes) -> None:
    transport = _mock_transport(minimal_pdf_bytes)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_pdf(
            "https://example.com/doc.pdf", max_bytes=1_000_000, client=client
        )
    assert result.bytes_ == minimal_pdf_bytes
    assert result.sha256 == sha256_of(minimal_pdf_bytes)


@pytest.mark.asyncio
async def test_fetch_pdf_rejects_non_pdf() -> None:
    transport = _mock_transport(b"<html>not a pdf</html>")
    with pytest.raises(PdfFetchError, match="not a PDF"):
        async with httpx.AsyncClient(transport=transport) as client:
            await fetch_pdf("https://example.com/fake.pdf", max_bytes=1_000, client=client)


@pytest.mark.asyncio
async def test_fetch_pdf_enforces_declared_content_length(minimal_pdf_bytes: bytes) -> None:
    transport = _mock_transport(minimal_pdf_bytes)
    with pytest.raises(PdfFetchError, match="too large"):
        async with httpx.AsyncClient(transport=transport) as client:
            await fetch_pdf("https://example.com/x.pdf", max_bytes=10, client=client)


@pytest.mark.asyncio
async def test_fetch_pdf_blocks_private_hosts() -> None:
    with pytest.raises(PdfFetchError):
        await fetch_pdf("http://127.0.0.1/x.pdf", max_bytes=1_000)
    with pytest.raises(PdfFetchError):
        await fetch_pdf("http://localhost/x.pdf", max_bytes=1_000)
    with pytest.raises(PdfFetchError):
        await fetch_pdf("http://10.0.0.5/x.pdf", max_bytes=1_000)


@pytest.mark.asyncio
async def test_fetch_pdf_rejects_bad_scheme() -> None:
    with pytest.raises(PdfFetchError, match="scheme"):
        await fetch_pdf("file:///etc/passwd", max_bytes=1_000)


@pytest.mark.asyncio
async def test_fetch_pdf_forwards_caller_headers(minimal_pdf_bytes: bytes) -> None:
    """fetch_pdf passes optional headers (e.g. Authorization) to the upstream request."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.headers))
        return httpx.Response(
            200,
            content=minimal_pdf_bytes,
            headers={"content-type": "application/pdf"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await fetch_pdf(
            "https://example.com/x.pdf",
            max_bytes=1_000_000,
            client=client,
            headers={"Authorization": "Basic ZGVmc29sOmRlZnNvbA=="},
        )
    assert captured.get("authorization") == "Basic ZGVmc29sOmRlZnNvbA=="


@pytest.mark.asyncio
async def test_fetch_pdf_allows_loopback_when_host_allow_listed(minimal_pdf_bytes: bytes) -> None:
    transport = _mock_transport(minimal_pdf_bytes)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_pdf(
            "http://localhost:8000/demo/x.pdf",
            max_bytes=1_000_000,
            client=client,
            allowed_hosts=frozenset({"localhost"}),
        )
    assert result.bytes_ == minimal_pdf_bytes
