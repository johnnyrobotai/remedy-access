"""remediate_document format dispatch — verifies the MIME type and system
instruction change based on fmt, and that XLSX interactive output passes
the a11y gate while PDF/DOCX stay on the static rules."""
from __future__ import annotations

import pytest

from backend.app.documents import DocFormat
from backend.app.gemini.remediate import (
    OutlineNode,
    RemediationOutput,
    _count_pages,
    remediate_document,
)


def _fake_response(out: RemediationOutput) -> object:
    class R:
        parsed = out

    return R()


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch, out: RemediationOutput
) -> dict:
    seen: dict = {"upload_mime": None, "system": None, "contents": None}

    class FakeFiles:
        def upload(self, *, file, config):
            seen["upload_mime"] = config["mime_type"]
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            seen["system"] = config.system_instruction
            seen["contents"] = contents
            return _fake_response(out)

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr(
        "backend.app.gemini.remediate.get_genai_client", lambda: FakeClient()
    )
    return seen


@pytest.mark.asyncio
async def test_remediate_document_pdf_mime_and_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = RemediationOutput(
        html='<main id="content" lang="en"><h1 id="document-title">T</h1></main>',
        outline=[OutlineNode(id="document-title", level=1, text="T")],
        title="T",
        page_count=1,
    )
    seen = _install_fake_client(monkeypatch, out)

    result = await remediate_document(b"%PDF-1.4\n%%EOF", "t.pdf", fmt=DocFormat.PDF)
    assert result.render_mode == "static"
    assert seen["upload_mime"] == "application/pdf"
    assert "PDF" in seen["system"]


@pytest.mark.asyncio
async def test_remediate_document_docx_sends_extracted_markdown(
    monkeypatch: pytest.MonkeyPatch,
    minimal_docx_bytes: bytes,
) -> None:
    """Gemini File API rejects OOXML MIME types, so DOCX bypasses the file
    upload entirely: content is extracted to Markdown server-side and
    passed as a text prompt."""
    out = RemediationOutput(
        html='<main id="content" lang="en"><h1 id="document-title">Memo</h1></main>',
        outline=[OutlineNode(id="document-title", level=1, text="Memo")],
        title="Memo",
        page_count=1,
    )
    seen = _install_fake_client(monkeypatch, out)

    result = await remediate_document(minimal_docx_bytes, "memo.docx", fmt=DocFormat.DOCX)
    assert result.render_mode == "static"
    # No file upload path was exercised.
    assert seen["upload_mime"] is None
    assert "Word" in seen["system"]
    # The extracted text block is in the text prompt so Gemini can read it.
    text_prompt = "\n".join(c for c in seen["contents"] if isinstance(c, str))
    assert "Sample Memo" in text_prompt
    assert "```markdown" in text_prompt


@pytest.mark.asyncio
async def test_remediate_document_xlsx_interactive_passes_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interactive_html = (
        '<main id="content" lang="en">'
        '<a href="#content" class="sr-skip">Skip to main content</a>'
        '<h1 id="document-title">GPA</h1>'
        '<section id="gpa"><h2 id="gpa">Grades</h2>'
        '<form>'
        '<label for="b2">English grade</label>'
        '<input id="b2" type="number" step="any" inputmode="decimal" />'
        '<label for="b6">Weighted GPA</label>'
        '<output id="b6" for="b2">—</output>'
        '</form>'
        '<script>window.__docboxCalc = { ok: true };</script>'
        '</section></main>'
    )
    out = RemediationOutput(
        html=interactive_html,
        outline=[
            OutlineNode(id="document-title", level=1, text="GPA"),
            OutlineNode(id="gpa", level=2, text="Grades"),
        ],
        title="GPA",
        page_count=1,
        render_mode="interactive",
    )
    seen = _install_fake_client(monkeypatch, out)

    # Use the real GPA fixture so the extractor has a workbook to parse.
    import io as _io

    import openpyxl

    # Rebuild a tiny workbook inline (the fixture param isn't available here).
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "GPA"
    ws["A1"] = "Grade"
    ws["B1"] = 3.7
    ws["A2"] = "GPA"
    ws["B2"] = "=B1"
    buf = _io.BytesIO()
    wb.save(buf)

    result = await remediate_document(buf.getvalue(), "gpa.xlsx", fmt=DocFormat.XLSX)
    assert result.render_mode == "interactive"
    # XLSX bypasses file upload too.
    assert seen["upload_mime"] is None
    assert "Excel" in seen["system"]
    assert "XLSX calculator addendum" in seen["system"]
    # Formula preserved in the extracted JSON.
    text_prompt = "\n".join(c for c in seen["contents"] if isinstance(c, str))
    assert "```json" in text_prompt
    assert "=B1" in text_prompt or "\"formula\": \"=B1\"" in text_prompt
    # The <script> must survive into the cached HTML — it's the calculator.
    assert "<script" in result.html


@pytest.mark.asyncio
async def test_remediate_document_xlsx_interactive_with_broken_js_falls_back_to_static(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Gemini's interactive XLSX output has a literal JS parse error,
    we must coerce render_mode back to static so the a11y gate strips
    the script. The cache should never hold a silently broken calculator."""
    # `""—"` is the real regression snippet we hit in smoke: three
    # double-quotes wrapping an em-dash. Structurally fine HTML, dead JS.
    interactive_html_broken = (
        '<main id="content" lang="en">'
        '<a href="#content" class="sr-skip">Skip to main content</a>'
        '<h1 id="document-title">GPA</h1>'
        '<section id="gpa"><h2 id="gpa">Grades</h2>'
        '<form>'
        '<label for="b2">English grade</label>'
        '<input id="b2" type="number" step="any" inputmode="decimal" />'
        '<label for="b6">Weighted GPA</label>'
        '<output id="b6" for="b2">—</output>'
        '</form>'
        '<script>const placeholder = ""—";</script>'
        '</section></main>'
    )
    out = RemediationOutput(
        html=interactive_html_broken,
        outline=[
            OutlineNode(id="document-title", level=1, text="GPA"),
            OutlineNode(id="gpa", level=2, text="Grades"),
        ],
        title="GPA",
        page_count=1,
        render_mode="interactive",
    )
    _install_fake_client(monkeypatch, out)

    # Force validate_inline_scripts to report an error deterministically,
    # independent of whether node/acorn are available on the test host.
    monkeypatch.setattr(
        "backend.app.js_validate.validate_inline_scripts",
        lambda html: ["<script> #1: Unterminated string constant"],
    )

    import io as _io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "GPA"
    ws["A1"] = "Grade"
    ws["B1"] = 3.7
    ws["A2"] = "GPA"
    ws["B2"] = "=B1"
    buf = _io.BytesIO()
    wb.save(buf)

    # After the JS-validation failure the static gate will reject the
    # surviving <script> tag — both retries fail, so we expect a
    # RuntimeError that mentions the JS parse error.
    with pytest.raises(RuntimeError) as ei:
        await remediate_document(buf.getvalue(), "gpa.xlsx", fmt=DocFormat.XLSX)
    assert "invalid JS" in str(ei.value) or "Unterminated" in str(ei.value)


@pytest.mark.asyncio
async def test_remediate_document_ignores_interactive_mode_for_non_xlsx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PDF response that drifts into render_mode=interactive gets forced back
    to static so the WCAG gate (which rejects <script>) stays in effect."""
    out = RemediationOutput(
        html='<main id="content" lang="en"><h1 id="document-title">T</h1></main>',
        outline=[OutlineNode(id="document-title", level=1, text="T")],
        title="T",
        page_count=1,
        render_mode="interactive",
    )
    _install_fake_client(monkeypatch, out)

    result = await remediate_document(b"%PDF-1.4\n%%EOF", "t.pdf", fmt=DocFormat.PDF)
    assert result.render_mode == "static"


def test_count_pages_docx(minimal_docx_bytes: bytes) -> None:
    # Fixture has 2 paragraphs; coarse proxy returns 1.
    assert _count_pages(minimal_docx_bytes, DocFormat.DOCX) >= 1


def test_count_pages_xlsx(gpa_calculator_xlsx_bytes: bytes) -> None:
    # Single-sheet workbook.
    assert _count_pages(gpa_calculator_xlsx_bytes, DocFormat.XLSX) == 1
