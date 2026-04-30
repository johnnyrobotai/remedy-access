"""Focused tests for the LiteParse CLI adapter.

The JSON fixtures are excerpted snapshots captured from real demo PDFs via:

- ``lit parse demo/pdfs/invoicesample.pdf --format json --target-pages 1 --no-ocr -q``
- ``lit parse demo/pdfs/sample-tables.pdf --format json --target-pages 1 --no-ocr -q``

They intentionally keep only part of the first page so we lock the CLI shape
without checking in full page dumps.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.app.parser_backends import liteparse_adapter as adapter

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "liteparse"


def _fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def test_parse_pdf_shells_out_and_normalizes_invoice_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=_fixture("invoicesample_page1_excerpt.json"),
            stderr="",
        )

    monkeypatch.setattr(adapter.shutil, "which", lambda _name: "/opt/homebrew/bin/lit")
    monkeypatch.setattr(adapter.subprocess, "run", fake_run)

    doc = adapter.parse_pdf_with_liteparse(
        "/tmp/invoicesample.pdf",
        target_pages="1",
        ocr_enabled=False,
    )

    assert calls == [[
        "/opt/homebrew/bin/lit",
        "parse",
        "/tmp/invoicesample.pdf",
        "--format",
        "json",
        "-q",
        "--target-pages",
        "1",
        "--no-ocr",
    ]]
    assert len(doc.pages) == 1
    page = doc.pages[0]
    assert page.page_num == 1
    assert page.width == pytest.approx(595.2756)
    assert page.height == pytest.approx(841.8898)
    assert "Invoice Number: #20130304" in page.text
    assert page.text_items[0].text == "Denny "
    assert page.text_items[0].font_name == "g_d0_f1"
    assert page.text_items[0].font_size == pytest.approx(15.0)
    assert page.text_items[0].confidence is None


def test_parse_pdf_normalizes_table_fixture_without_extra_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=_fixture("sample_tables_page1_excerpt.json"),
            stderr="",
        )

    monkeypatch.setattr(adapter.shutil, "which", lambda _name: "/opt/homebrew/bin/lit")
    monkeypatch.setattr(adapter.subprocess, "run", fake_run)

    doc = adapter.parse_pdf_with_liteparse("/tmp/sample-tables.pdf")

    assert len(doc.pages) == 1
    page = doc.pages[0]
    assert page.page_num == 1
    assert "Table 2: example of footnotes referenced from within a table" in page.text
    assert page.text_items[10].text == "Column "
    assert page.text_items[10].x == pytest.approx(90.0)
    assert not hasattr(page, "bounding_boxes")


def test_parse_pdf_preserves_explicit_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = """
    {
      "pages": [
        {
          "page": 1,
          "width": 612,
          "height": 792,
          "text": "OCR sample",
          "textItems": [
            {
              "text": "OCR sample",
              "x": 10,
              "y": 20,
              "width": 100,
              "height": 12,
              "fontName": "OCR",
              "fontSize": 12,
              "confidence": 0.73
            }
          ],
          "boundingBoxes": []
        }
      ]
    }
    """

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")

    monkeypatch.setattr(adapter.shutil, "which", lambda _name: "/opt/homebrew/bin/lit")
    monkeypatch.setattr(adapter.subprocess, "run", fake_run)

    doc = adapter.parse_pdf_with_liteparse("/tmp/ocr.pdf")

    item = doc.pages[0].text_items[0]
    assert item.font_name == "OCR"
    assert item.confidence == pytest.approx(0.73)


def test_parse_pdf_fails_cleanly_when_cli_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter.shutil, "which", lambda _name: None)

    with pytest.raises(adapter.LiteParseUnavailableError, match="not found on PATH"):
        adapter.parse_pdf_with_liteparse("/tmp/missing.pdf")


def test_parse_pdf_fails_cleanly_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="{not-json", stderr="")

    monkeypatch.setattr(adapter.shutil, "which", lambda _name: "/opt/homebrew/bin/lit")
    monkeypatch.setattr(adapter.subprocess, "run", fake_run)

    with pytest.raises(adapter.LiteParseOutputError, match="invalid JSON"):
        adapter.parse_pdf_with_liteparse("/tmp/bad.json.pdf")


def test_screenshot_helper_runs_cli_and_returns_sorted_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "shots"
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "page_3.jpg").write_bytes(b"three")
        (output_dir / "page_1.jpg").write_bytes(b"one")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(adapter.shutil, "which", lambda _name: "/opt/homebrew/bin/lit")
    monkeypatch.setattr(adapter.subprocess, "run", fake_run)

    screenshots = adapter.screenshot_pdf_with_liteparse(
        "/tmp/sample.pdf",
        output_dir=output_dir,
        target_pages="1,3",
        dpi=200,
        image_format="jpg",
    )

    assert calls == [[
        "/opt/homebrew/bin/lit",
        "screenshot",
        "/tmp/sample.pdf",
        "--output-dir",
        str(output_dir),
        "--format",
        "jpg",
        "-q",
        "--target-pages",
        "1,3",
        "--dpi",
        "200",
    ]]
    assert [shot.page_num for shot in screenshots] == [1, 3]
    assert [shot.path.name for shot in screenshots] == ["page_1.jpg", "page_3.jpg"]
