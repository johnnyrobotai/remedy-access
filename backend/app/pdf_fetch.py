from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from backend.app.documents import (
    DocFormat,
    UnsupportedDocumentFormat,
    detect_format,
)

PDF_MAGIC = b"%PDF-"
BLOCKED_HOSTS = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})


class PdfFetchError(Exception):
    pass


# Alias used by non-PDF call sites. The two names resolve to the same class
# so existing `except PdfFetchError` blocks still catch everything.
DocumentFetchError = PdfFetchError


@dataclass(frozen=True)
class FetchedDoc:
    bytes_: bytes
    sha256: str
    source_url: str
    content_length: int
    fmt: DocFormat


@dataclass(frozen=True)
class FetchedPdf:
    """Back-compat wrapper kept so older imports keep working.

    New code should use `FetchedDoc`; the two are structurally identical
    minus the `fmt` field (implicitly PDF here).
    """

    bytes_: bytes
    sha256: str
    source_url: str
    content_length: int


def _host_is_private(host: str) -> bool:
    if host.lower() in BLOCKED_HOSTS:
        return True
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise PdfFetchError(f"cannot resolve host {host!r}: {e}") from e
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            return True
    return False


def _validate_url(url: str, *, allowed_hosts: frozenset[str] = frozenset()) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise PdfFetchError(f"unsupported scheme {parsed.scheme!r}")
    if not parsed.hostname:
        raise PdfFetchError("missing host")
    if parsed.hostname.lower() in allowed_hosts:
        return
    if _host_is_private(parsed.hostname):
        raise PdfFetchError(f"host {parsed.hostname!r} is private/loopback")


async def _stream_bytes(
    url: str,
    *,
    max_bytes: int,
    client: httpx.AsyncClient | None,
    headers: dict[str, str] | None,
) -> tuple[bytes, dict[str, str]]:
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)
    try:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            declared = int(resp.headers.get("content-length") or 0)
            if declared and declared > max_bytes:
                raise PdfFetchError(f"document too large: {declared} > {max_bytes}")
            chunks = bytearray()
            async for chunk in resp.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > max_bytes:
                    raise PdfFetchError(f"document exceeded max_bytes={max_bytes}")
            response_headers = dict(resp.headers)
    finally:
        if owns_client:
            await client.aclose()
    return bytes(chunks), response_headers


async def fetch_document(
    url: str,
    *,
    max_bytes: int,
    client: httpx.AsyncClient | None = None,
    allowed_hosts: frozenset[str] = frozenset(),
    headers: dict[str, str] | None = None,
    expected_format: DocFormat | None = None,
) -> FetchedDoc:
    """Fetch `url`, hash it, and classify it into a `DocFormat`.

    When `expected_format` is given, the detected format must match or we
    raise — lets callers say "I asked for a PDF, reject anything else" while
    still using one code path for all three formats.
    """
    _validate_url(url, allowed_hosts=allowed_hosts)
    data, response_headers = await _stream_bytes(
        url, max_bytes=max_bytes, client=client, headers=headers
    )
    try:
        fmt = detect_format(url=url, headers=response_headers, head_bytes=data[:4096])
    except UnsupportedDocumentFormat as e:
        raise PdfFetchError(f"response is not a supported document: {e}") from e

    if expected_format is not None and fmt is not expected_format:
        raise PdfFetchError(
            f"expected {expected_format.value} but received {fmt.value}"
        )

    sha = hashlib.sha256(data).hexdigest()
    return FetchedDoc(
        bytes_=data,
        sha256=sha,
        source_url=url,
        content_length=len(data),
        fmt=fmt,
    )


async def fetch_pdf(
    url: str,
    *,
    max_bytes: int,
    client: httpx.AsyncClient | None = None,
    allowed_hosts: frozenset[str] = frozenset(),
    headers: dict[str, str] | None = None,
) -> FetchedPdf:
    """Back-compat wrapper: fetch a URL and verify it's a PDF.

    Kept so the transcript route, tests, and any external callers that still
    speak the PDF-only API continue to work. The error message format
    ("not a PDF (missing %PDF- header)") is preserved so existing tests
    that match on it stay green.
    """
    _validate_url(url, allowed_hosts=allowed_hosts)
    data, _headers = await _stream_bytes(
        url, max_bytes=max_bytes, client=client, headers=headers
    )
    if not data.startswith(PDF_MAGIC):
        raise PdfFetchError("response is not a PDF (missing %PDF- header)")
    sha = hashlib.sha256(data).hexdigest()
    return FetchedPdf(bytes_=data, sha256=sha, source_url=url, content_length=len(data))


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store_document(doc_dir: Path, sha: str, data: bytes, fmt: DocFormat) -> Path:
    doc_dir.mkdir(parents=True, exist_ok=True)
    path = doc_dir / f"{sha}{fmt.extension}"
    if not path.exists():
        path.write_bytes(data)
    return path


def store_pdf(pdf_dir: Path, sha: str, data: bytes) -> Path:
    """Back-compat wrapper around `store_document` for the PDF path."""
    return store_document(pdf_dir, sha, data, DocFormat.PDF)
