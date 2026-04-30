from __future__ import annotations

import io
import zipfile

import pytest

from backend.app.documents import DocFormat, UnsupportedDocumentFormat, detect_format


def _make_ooxml(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_detects_pdf_from_magic_bytes() -> None:
    assert detect_format(head_bytes=b"%PDF-1.4\n...") == DocFormat.PDF


def test_detects_docx_from_ooxml_contents() -> None:
    raw = _make_ooxml({"word/document.xml": b"<x/>", "[Content_Types].xml": b"<x/>"})
    assert detect_format(head_bytes=raw) == DocFormat.DOCX


def test_detects_xlsx_from_ooxml_contents() -> None:
    raw = _make_ooxml({"xl/workbook.xml": b"<x/>", "[Content_Types].xml": b"<x/>"})
    assert detect_format(head_bytes=raw) == DocFormat.XLSX


def test_url_extension_takes_precedence_over_bytes() -> None:
    # URL says xlsx; bytes would also match but header takes priority when present.
    assert detect_format(url="https://example.com/foo.xlsx") == DocFormat.XLSX
    assert detect_format(url="https://example.com/foo.docx") == DocFormat.DOCX
    assert detect_format(url="https://example.com/foo.pdf") == DocFormat.PDF


def test_content_type_header_wins() -> None:
    assert (
        detect_format(
            url="https://example.com/x",
            headers={"content-type": "application/pdf; charset=binary"},
        )
        == DocFormat.PDF
    )
    assert (
        detect_format(
            headers={
                "Content-Type": (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            }
        )
        == DocFormat.XLSX
    )


def test_unknown_format_raises() -> None:
    with pytest.raises(UnsupportedDocumentFormat):
        detect_format(head_bytes=b"<html>not a document</html>")


def test_format_mime_and_extension_round_trip() -> None:
    for fmt in DocFormat:
        assert fmt.extension.startswith(".")
        assert fmt.mime_type  # non-empty
