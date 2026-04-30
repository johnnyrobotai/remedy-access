"""Document format detection + metadata.

Single source of truth for the three formats the pipeline supports today
(PDF, DOCX, XLSX). Everything downstream — fetch, cache, remediation,
viewer — keys off `DocFormat`.

`detect_format()` is conservative: content-type wins when it's specific,
URL extension is the tiebreaker, magic bytes are the last resort. It
raises on anything we don't recognise so a malformed fetch can't silently
get labelled as something it isn't.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import urlparse


class DocFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"

    @property
    def mime_type(self) -> str:
        return _MIME_BY_FORMAT[self]

    @property
    def extension(self) -> str:
        return f".{self.value}"


_MIME_BY_FORMAT = {
    DocFormat.PDF: "application/pdf",
    DocFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    DocFormat.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# Map known MIME types (including a few historical aliases) to formats.
_FORMAT_BY_MIME: dict[str, DocFormat] = {
    "application/pdf": DocFormat.PDF,
    "application/x-pdf": DocFormat.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": DocFormat.DOCX,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": DocFormat.XLSX,
}

_EXT_TO_FORMAT = {
    ".pdf": DocFormat.PDF,
    ".docx": DocFormat.DOCX,
    ".xlsx": DocFormat.XLSX,
}

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"


class UnsupportedDocumentFormat(Exception):
    """Raised when bytes or headers don't match any supported format."""


@dataclass(frozen=True)
class DocMeta:
    fmt: DocFormat
    mime_type: str


def _mime_from_header(value: str | None) -> DocFormat | None:
    if not value:
        return None
    mime = value.split(";", 1)[0].strip().lower()
    return _FORMAT_BY_MIME.get(mime)


def _ext_from_url(url: str) -> DocFormat | None:
    try:
        path = urlparse(url).path
    except ValueError:
        return None
    suffix = PurePosixPath(path).suffix.lower()
    return _EXT_TO_FORMAT.get(suffix)


def _sniff_ooxml(head_bytes: bytes) -> DocFormat | None:
    """OOXML files are ZIP archives. Peek at the central directory to tell
    DOCX and XLSX apart — DOCX stores `word/` entries, XLSX stores `xl/`.
    Falls back to None if the buffer doesn't contain enough of the archive
    to open a ZipFile (common when we were only handed the magic bytes).
    """
    if not head_bytes.startswith(_ZIP_MAGIC):
        return None
    try:
        with zipfile.ZipFile(BytesIO(head_bytes)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return None
    if any(n.startswith("word/") for n in names):
        return DocFormat.DOCX
    if any(n.startswith("xl/") for n in names):
        return DocFormat.XLSX
    return None


def detect_format(
    url: str = "",
    headers: dict[str, str] | None = None,
    head_bytes: bytes = b"",
) -> DocFormat:
    """Return the document format, preferring the strongest signal available.

    Priority:
      1. Content-Type header (when it names a specific known MIME).
      2. URL path extension.
      3. Magic bytes (+ ZIP central-directory sniff for OOXML).

    Raises UnsupportedDocumentFormat if none of the signals match.
    """
    if headers:
        # httpx lowercases header names; be defensive about either case.
        for key in ("content-type", "Content-Type"):
            fmt = _mime_from_header(headers.get(key))
            if fmt is not None:
                return fmt

    if url:
        fmt = _ext_from_url(url)
        if fmt is not None:
            return fmt

    if head_bytes.startswith(_PDF_MAGIC):
        return DocFormat.PDF
    sniffed = _sniff_ooxml(head_bytes)
    if sniffed is not None:
        return sniffed

    raise UnsupportedDocumentFormat(
        "could not determine document format from headers, url, or bytes"
    )
