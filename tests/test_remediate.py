from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backend.app.gemini.remediate import (
    OutlineNode,
    RemediationOutput,
    _validate_wcag_baseline,
)

SAMPLE_PDF = Path(__file__).parent.parent / "demo" / "pdfs" / "Basic_PDF_Form_Sample.pdf"
RUN_LIVE = os.getenv("RUN_LIVE") == "1"


def test_validate_wcag_baseline_flags_missing_landmarks() -> None:
    bad = "<div><p>no structure here</p></div>"
    issues = _validate_wcag_baseline(bad)
    assert any("missing <main>" in i for i in issues)
    assert any("missing <h1>" in i for i in issues)
    assert any("lang" in i for i in issues)


def test_validate_wcag_baseline_flags_inline_styles() -> None:
    bad = '<main lang="en"><h1 style="color:red">Hi</h1></main>'
    issues = _validate_wcag_baseline(bad)
    assert any("inline style" in i for i in issues)


def test_validate_wcag_baseline_accepts_clean() -> None:
    clean = '<main id="content" lang="en"><h1 id="h1-0">Title</h1></main>'
    assert _validate_wcag_baseline(clean) == []


def test_outline_node_rejects_out_of_range_level() -> None:
    with pytest.raises(Exception):  # pydantic ValidationError  # noqa: B017
        OutlineNode(id="x", level=7, text="deep")


def test_remediation_output_roundtrip() -> None:
    raw = {
        "html": '<main lang="en"><h1 id="h1-0">T</h1></main>',
        "outline": [{"id": "h1-0", "level": 1, "text": "T"}],
        "title": "T",
        "description": "",
        "language": "en",
        "page_count": 1,
    }
    parsed = RemediationOutput.model_validate(raw)
    assert parsed.outline[0].id == "h1-0"


# ---------------------------------------------------------------------------
# Fix #1 — retry on WCAG structural check failure
# ---------------------------------------------------------------------------

def _fake_client(responses: list) -> object:
    """Build a fake genai client that returns responses in order for generate_content."""
    call_index = {"n": 0}

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            idx = call_index["n"]
            call_index["n"] += 1
            return responses[idx]

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    return FakeClient()


def _make_response(html: str) -> object:
    class R:
        parsed = RemediationOutput(
            html=html,
            outline=[OutlineNode(id="document-title", level=1, text="T")] if "h1" in html else [],
            title="T",
            page_count=1,
        )

    return R()


def _truncated_response() -> object:
    """Simulate a Gemini response whose JSON is cut off mid-string."""
    class R:
        parsed = None
        text = '{"html": "incomplete response cut off here...'  # EOF mid-string

    return R()


@pytest.mark.asyncio
async def test_remediate_retries_on_wcag_structural_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First response fails WCAG structural check; second attempt succeeds."""
    from backend.app.gemini.remediate import remediate_pdf

    bad_html = '<main lang="en"><p>no heading here</p></main>'  # missing h1
    good_html = '<main id="content" lang="en"><h1 id="document-title">T</h1></main>'

    client = _fake_client([_make_response(bad_html), _make_response(good_html)])
    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: client)

    result = await remediate_pdf(b"%PDF-1.4\n%%EOF", "test.pdf")

    assert "<h1" in result.html


@pytest.mark.asyncio
async def test_remediate_retries_on_wcag_structural_failure_call_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly two generate_content calls are made when first fails structural checks."""
    from backend.app.gemini.remediate import remediate_pdf

    call_log: list[float] = []
    bad_html = '<main lang="en"><p>no heading</p></main>'
    good_html = '<main id="content" lang="en"><h1 id="document-title">T</h1></main>'

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            call_log.append(config.temperature)
            html = bad_html if len(call_log) == 1 else good_html
            return _make_response(html)

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: FakeClient())

    await remediate_pdf(b"%PDF-1.4\n%%EOF", "test.pdf")

    assert len(call_log) == 2
    assert call_log[0] == 0.2
    assert call_log[1] == 0.0


@pytest.mark.asyncio
async def test_remediate_raises_after_two_structural_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both attempts fail structural checks — RuntimeError names the failing checks."""
    from backend.app.gemini.remediate import remediate_pdf

    bad_html = '<main lang="en"><p>no heading</p></main>'

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            return _make_response(bad_html)

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: FakeClient())

    with pytest.raises(RuntimeError, match="structural checks"):
        await remediate_pdf(b"%PDF-1.4\n%%EOF", "test.pdf")


# ---------------------------------------------------------------------------
# Fix #2 — chunked synthesis fallback for truncated responses
# ---------------------------------------------------------------------------


def test_is_likely_truncation_recognises_timeout_message() -> None:
    """Timeout-like errors should trigger the chunked/placeholder fallback."""
    from backend.app.gemini.remediate import _is_likely_truncation

    assert _is_likely_truncation(RuntimeError("chunk Gemini call timed out after 60s"))
    assert _is_likely_truncation(TimeoutError("call exceeded budget"))


@pytest.mark.asyncio
async def test_remediate_chunk_times_out_and_raises_truncation_like_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a chunk's Gemini call exceeds GEMINI_CALL_TIMEOUT, it raises a truncation-class
    RuntimeError that the caller can recognise (so placeholders kick in instead of hanging)."""
    from backend.app.gemini.remediate import _is_likely_truncation, _remediate_chunk

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            import time

            time.sleep(5)

            class R:
                parsed = None
                text = "{}"

            return R()

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: FakeClient())
    monkeypatch.setenv("GEMINI_CALL_TIMEOUT", "1")
    from backend.app.config import get_settings

    get_settings.cache_clear()

    plan_json = json.dumps({"title": "T", "language": "en", "page_count": 1, "description": "",
                            "sections": [{"id": "s", "heading": "S", "layout": "prose", "fields": [], "images": []}]})

    with pytest.raises(RuntimeError) as exc_info:
        await _remediate_chunk(b"%PDF-1.4\n%%EOF", "slow.pdf", plan_json)
    assert _is_likely_truncation(exc_info.value), (
        f"timeout error should be truncation-class; got: {exc_info.value}"
    )


@pytest.mark.asyncio
async def test_remediate_pdf_chunked_returns_too_large_page_when_plan_has_no_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the plan has zero sections (e.g. chunked planner failed everything for a huge PDF),
    remediate_pdf_chunked returns a 'too large to render' placeholder page instead of hanging."""
    from backend.app.gemini.remediate import remediate_pdf_chunked

    plan = {
        "title": "Massive PDF",
        "language": "en",
        "page_count": 200,
        "description": "",
        "sections": [],
    }

    chunk_calls = []

    async def fake_chunk(*args, **kwargs):
        chunk_calls.append(True)
        from backend.app.gemini.remediate import RemediationChunkOutput

        return RemediationChunkOutput(html="", outline=[])

    monkeypatch.setattr("backend.app.gemini.remediate._remediate_chunk", fake_chunk)

    result = await remediate_pdf_chunked(
        b"%PDF-1.4\n%%EOF", "huge.pdf", json.dumps(plan), chunk_size=3
    )
    assert chunk_calls == [], "should not call _remediate_chunk for an empty plan"
    assert "too large" in result.html.lower() or "could not be rendered" in result.html.lower()
    assert "<h1" in result.html, "must still have an h1 to pass WCAG"
    assert result.title == "Massive PDF"


@pytest.mark.asyncio
async def test_remediate_pdf_chunked_aborts_after_consecutive_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If too many consecutive chunks fail with truncation, bail out and emit the too-large page
    instead of grinding through the rest."""
    from backend.app.gemini.remediate import remediate_pdf_chunked

    plan = {
        "title": "Pathological Doc",
        "language": "en",
        "page_count": 100,
        "description": "",
        "sections": [
            {"id": f"s-{i}", "heading": f"S{i}", "layout": "prose", "fields": [], "images": []}
            for i in range(20)
        ],
    }

    chunk_call_count = {"n": 0}

    async def fake_chunk(*args, **kwargs):
        chunk_call_count["n"] += 1
        raise RuntimeError("chunk Gemini call timed out after 90s")

    monkeypatch.setattr("backend.app.gemini.remediate._remediate_chunk", fake_chunk)

    result = await remediate_pdf_chunked(
        b"%PDF-1.4\n%%EOF",
        "doomed.pdf",
        json.dumps(plan),
        chunk_size=3,
    )
    assert chunk_call_count["n"] <= 4, (
        f"should abort after a few consecutive failures, but kept going for {chunk_call_count['n']} chunks"
    )
    assert "too large" in result.html.lower() or "could not be rendered" in result.html.lower()


@pytest.mark.asyncio
async def test_remediate_pdf_chunked_inserts_placeholder_for_failing_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a chunk fails, remediate_pdf_chunked emits a placeholder section and continues."""
    from backend.app.gemini.remediate import RemediationChunkOutput, remediate_pdf_chunked

    plan = {
        "title": "Mixed Doc",
        "language": "en",
        "page_count": 4,
        "description": "",
        "sections": [
            {"id": "good-1", "heading": "Good 1", "layout": "prose", "fields": [], "images": []},
            {"id": "huge-table", "heading": "Huge Table", "layout": "table", "fields": [], "images": []},
            {"id": "good-2", "heading": "Good 2", "layout": "prose", "fields": [], "images": []},
        ],
    }

    async def fake_chunk(pdf_bytes, source_hint, chunk_plan_json, *, image_filenames=None):
        chunk_plan = json.loads(chunk_plan_json)
        ids = [s["id"] for s in chunk_plan["sections"]]
        if "huge-table" in ids:
            raise RuntimeError("chunk remediation returned invalid JSON: EOF while parsing a string")
        sections_html = "".join(
            f'<section id="{s["id"]}"><h2 id="{s["id"]}">{s["heading"]}</h2><p>OK</p></section>'
            for s in chunk_plan["sections"]
        )
        return RemediationChunkOutput(
            html=sections_html,
            outline=[OutlineNode(id=s["id"], level=2, text=s["heading"]) for s in chunk_plan["sections"]],
        )

    monkeypatch.setattr("backend.app.gemini.remediate._remediate_chunk", fake_chunk)

    result = await remediate_pdf_chunked(
        b"%PDF-1.4\n%%EOF", "test.pdf", json.dumps(plan), chunk_size=1
    )

    assert "Huge Table" in result.html, "placeholder should preserve heading"
    assert 'id="huge-table"' in result.html, "placeholder should preserve section id"
    assert "Good 1" in result.html
    assert "Good 2" in result.html
    # Outline should include all three sections (good-1, huge-table placeholder, good-2)
    section_ids = [n["id"] for n in result.outline]
    assert "huge-table" in section_ids


@pytest.mark.asyncio
async def test_remediate_pdf_chunked_stitches_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """remediate_pdf_chunked splits sections into batches and assembles valid HTML."""
    from backend.app.gemini.remediate import RemediationChunkOutput, remediate_pdf_chunked

    plan = {
        "title": "Big Doc",
        "language": "en",
        "page_count": 4,
        "description": "",
        "sections": [
            {"id": f"sec-{i}", "heading": f"Section {i}", "layout": "prose", "fields": [], "images": []}
            for i in range(6)
        ],
    }

    chunk_calls: list[list[str]] = []

    async def fake_chunk(pdf_bytes, source_hint, chunk_plan_json, *, image_filenames=None):
        chunk_plan = json.loads(chunk_plan_json)
        ids = [s["id"] for s in chunk_plan["sections"]]
        chunk_calls.append(ids)
        sections_html = "".join(
            f'<section id="{s["id"]}"><h2 id="{s["id"]}">{s["heading"]}</h2><p>Text.</p></section>'
            for s in chunk_plan["sections"]
        )
        outline = [OutlineNode(id=s["id"], level=2, text=s["heading"]) for s in chunk_plan["sections"]]
        return RemediationChunkOutput(html=sections_html, outline=outline)

    monkeypatch.setattr("backend.app.gemini.remediate._remediate_chunk", fake_chunk)

    result = await remediate_pdf_chunked(
        b"%PDF-1.4\n%%EOF", "test.pdf", json.dumps(plan), chunk_size=4
    )

    assert "<main" in result.html and 'lang="en"' in result.html
    assert '<h1 id="document-title">Big Doc</h1>' in result.html
    assert result.html.count("<section") == 6
    assert len(chunk_calls) == 2
    assert chunk_calls[0] == ["sec-0", "sec-1", "sec-2", "sec-3"]
    assert chunk_calls[1] == ["sec-4", "sec-5"]
    assert result.title == "Big Doc"
    assert len(result.outline) == 7  # 1 h1 + 6 sections


@pytest.mark.asyncio
async def test_remediate_falls_back_to_chunked_on_malformed_large_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both retries return malformed JSON (not just EOF) and plan_json is set, falls back to chunked."""
    from backend.app.gemini.remediate import RemediationResult, remediate_pdf

    plan_json = json.dumps({
        "title": "Big",
        "language": "en",
        "page_count": 50,
        "description": "",
        "sections": [{"id": "s1", "heading": "One", "layout": "prose", "fields": [], "images": []}],
    })

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            class R:
                parsed = None
                # Real IRS_1040 failure mode: control character mid-string (model output corruption)
                text = '{"html": "<main lang=\\"en\\"><h1>Title</h1>\x01malformed</main>", "outline": []}'

            return R()

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: FakeClient())

    chunked_called = []

    async def fake_chunked(pdf_bytes, source_hint, pj, *, image_filenames=None, chunk_size=5):
        chunked_called.append(pj)
        return RemediationResult(
            html='<main id="content" lang="en"><h1 id="document-title">Big</h1></main>',
            outline=[{"id": "document-title", "level": 1, "text": "Big"}],
            title="Big",
            description="",
            language="en",
            page_count=50,
        )

    monkeypatch.setattr("backend.app.gemini.remediate.remediate_pdf_chunked", fake_chunked)

    result = await remediate_pdf(b"%PDF-1.4\n%%EOF", "test.pdf", plan_json=plan_json)
    assert chunked_called, "chunked synthesis should fire on any large-PDF JSON parse failure"
    assert result.title == "Big"


@pytest.mark.asyncio
async def test_remediate_retries_chunked_with_smaller_chunk_on_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If chunked synthesis at the default chunk_size truncates, retry at chunk_size=1."""
    from backend.app.gemini.remediate import RemediationResult, remediate_pdf

    plan_json = json.dumps({
        "title": "Massive",
        "language": "en",
        "page_count": 100,
        "description": "",
        "sections": [{"id": "s1", "heading": "One", "layout": "prose", "fields": [], "images": []}],
    })

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            class R:
                parsed = None
                text = '{"html": "incomplete...'  # always truncates
            return R()

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: FakeClient())

    chunked_calls: list[int] = []

    async def fake_chunked(pdf_bytes, source_hint, pj, *, image_filenames=None, chunk_size=5):
        chunked_calls.append(chunk_size)
        if chunk_size > 1:
            raise RuntimeError("chunk remediation returned invalid JSON: EOF while parsing a string")
        return RemediationResult(
            html='<main id="content" lang="en"><h1 id="document-title">Massive</h1></main>',
            outline=[{"id": "document-title", "level": 1, "text": "Massive"}],
            title="Massive",
            description="",
            language="en",
            page_count=100,
        )

    monkeypatch.setattr("backend.app.gemini.remediate.remediate_pdf_chunked", fake_chunked)

    result = await remediate_pdf(b"%PDF-1.4\n%%EOF", "test.pdf", plan_json=plan_json)
    # Chunked synthesis is the primary path for any plan with sections and uses
    # chunk_size=1. The old adaptive [3, 1] retry is obsolete — one call per section
    # is the reliable baseline.
    assert chunked_calls == [1], f"expected single chunk_size=1 call, got {chunked_calls}"
    assert result.title == "Massive"


@pytest.mark.asyncio
async def test_remediate_falls_back_to_chunked_on_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both retries produce EOF-truncated JSON and plan_json is set, falls back to chunked."""
    from backend.app.gemini.remediate import RemediationResult, remediate_pdf

    plan_json = json.dumps({
        "title": "Long Doc",
        "language": "en",
        "page_count": 20,
        "description": "",
        "sections": [{"id": "s1", "heading": "One", "layout": "prose", "fields": [], "images": []}],
    })

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            return _truncated_response()

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: FakeClient())

    chunked_called = []

    async def fake_chunked(pdf_bytes, source_hint, pj, *, image_filenames=None, chunk_size=5):
        chunked_called.append(pj)
        return RemediationResult(
            html='<main id="content" lang="en"><h1 id="document-title">Long Doc</h1><section id="s1"><h2 id="s1">One</h2></section></main>',
            outline=[{"id": "document-title", "level": 1, "text": "Long Doc"}],
            title="Long Doc",
            description="",
            language="en",
            page_count=20,
        )

    monkeypatch.setattr("backend.app.gemini.remediate.remediate_pdf_chunked", fake_chunked)

    result = await remediate_pdf(b"%PDF-1.4\n%%EOF", "test.pdf", plan_json=plan_json)

    assert chunked_called, "chunked synthesis was not invoked"
    assert result.title == "Long Doc"


@pytest.mark.asyncio
async def test_remediate_returns_too_large_page_when_truncated_with_empty_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When synthesis truncates and we only have an empty plan, return a 'too large' page."""
    from backend.app.gemini.remediate import remediate_pdf

    empty_plan = json.dumps({
        "title": "Huge Doc",
        "language": "en",
        "page_count": 200,
        "description": "",
        "sections": [],
    })

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            class R:
                parsed = None
                text = '{"html": "<main lang=\\"en\\"><h1>Title</h1></main>"'  # truncated EOF
            return R()

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: FakeClient())

    result = await remediate_pdf(b"%PDF-1.4\n%%EOF", "huge.pdf", plan_json=empty_plan)
    assert "too large" in result.html.lower() or "could not be rendered" in result.html.lower()
    assert result.title == "Huge Doc"


@pytest.mark.asyncio
async def test_remediate_raises_on_truncation_without_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without plan_json, truncation errors surface as RuntimeError (no chunked fallback)."""
    from backend.app.gemini.remediate import remediate_pdf

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            return _truncated_response()

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: FakeClient())

    with pytest.raises(RuntimeError, match="invalid JSON"):
        await remediate_pdf(b"%PDF-1.4\n%%EOF", "test.pdf", plan_json=None)


# ---------------------------------------------------------------------------
# Per-section-always-chunked tests — when a plan exists with sections, we go
# straight to chunked synthesis (one call per section) rather than trying a
# single full-document synthesis that Gemini silently summarises.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remediate_document_prefers_chunked_when_plan_has_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a plan with sections is provided, chunked synthesis runs first — no full-synthesis attempt."""
    from backend.app.documents import DocFormat
    from backend.app.gemini.remediate import (
        RemediationResult,
        remediate_document,
    )

    plan_json = json.dumps({
        "title": "Somatosensory",
        "language": "en",
        "page_count": 4,
        "description": "",
        "sections": [
            {"id": "intro", "heading": "Introduction", "layout": "prose", "fields": [], "images": []},
            {"id": "receptors", "heading": "Receptors", "layout": "prose", "fields": [], "images": []},
        ],
    })

    full_synth_calls: list[int] = []
    chunked_calls: list[int] = []

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            full_synth_calls.append(1)
            raise AssertionError("full-synthesis path should not be called when plan has sections")

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: FakeClient())

    async def fake_chunked(pdf_bytes, source_hint, pj, *, image_filenames=None, chunk_size=5):
        chunked_calls.append(chunk_size)
        return RemediationResult(
            html='<main id="content" lang="en"><h1 id="document-title">Somatosensory</h1><section id="intro"><h2 id="intro">Introduction</h2><p>Body text 1.</p></section><section id="receptors"><h2 id="receptors">Receptors</h2><p>Body text 2.</p></section></main>',
            outline=[
                {"id": "document-title", "level": 1, "text": "Somatosensory"},
                {"id": "intro", "level": 2, "text": "Introduction"},
                {"id": "receptors", "level": 2, "text": "Receptors"},
            ],
            title="Somatosensory",
            description="",
            language="en",
            page_count=4,
        )

    monkeypatch.setattr("backend.app.gemini.remediate.remediate_pdf_chunked", fake_chunked)

    result = await remediate_document(
        b"%PDF-1.4\n%%EOF",
        source_hint="somatosensory.pdf",
        fmt=DocFormat.PDF,
        plan_json=plan_json,
    )

    assert chunked_calls, "chunked synthesis should fire when plan has sections"
    assert not full_synth_calls, "full synthesis must be skipped when chunked path is available"
    assert "Somatosensory" in result.html


@pytest.mark.asyncio
async def test_remediate_document_falls_back_to_full_synthesis_without_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no plan, full-synthesis path is still used (chunked synthesis has no sections to iterate)."""
    from backend.app.documents import DocFormat
    from backend.app.gemini.remediate import remediate_document

    full_synth_calls: list[int] = []

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            full_synth_calls.append(1)
            return _make_response('<main id="content" lang="en"><h1 id="document-title">T</h1></main>')

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: FakeClient())

    chunked_calls = []

    async def fake_chunked(*args, **kwargs):
        chunked_calls.append(1)
        raise AssertionError("chunked synthesis must not be called without a plan")

    monkeypatch.setattr("backend.app.gemini.remediate.remediate_pdf_chunked", fake_chunked)

    await remediate_document(
        b"%PDF-1.4\n%%EOF",
        source_hint="test.pdf",
        fmt=DocFormat.PDF,
        plan_json=None,
    )
    assert full_synth_calls, "full synthesis should be used when there is no plan"
    assert not chunked_calls


def test_placeholder_language_regex_catches_summary_phrases() -> None:
    """The placeholder/summary phrase detector catches the phrases Gemini slips into truncated output."""
    from backend.app.gemini.remediate import _looks_like_placeholder_language

    assert _looks_like_placeholder_language("The rest of this content continues in the full document.")
    assert _looks_like_placeholder_language("Please refer to the original document for full details.")
    assert _looks_like_placeholder_language("For brevity, this section is summarized.")
    assert _looks_like_placeholder_language("...content omitted for brevity...")
    assert _looks_like_placeholder_language("See the source PDF for the complete content.")
    # Corruption patterns too
    assert _looks_like_placeholder_language("..................................................................")
    assert _looks_like_placeholder_language("```outline\n- heading\n```")
    # Normal content should pass
    assert not _looks_like_placeholder_language("<section><h2>Introduction</h2><p>This chapter examines the somatosensory system in detail.</p></section>")


@pytest.mark.asyncio
async def test_remediate_chunk_rejects_placeholder_language_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a chunk's HTML contains 'refer to original' language, it's rejected and retried."""
    from backend.app.gemini.remediate import RemediationChunkOutput, _remediate_chunk, OutlineNode

    call_count = {"n": 0}

    bad_html = '<section id="s"><h2 id="s">S</h2><p>For full details refer to the original document.</p></section>'
    good_html = '<section id="s"><h2 id="s">S</h2><p>This is the actual section content, reproduced in full from the source with every paragraph intact.</p></section>'

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            call_count["n"] += 1
            html = bad_html if call_count["n"] == 1 else good_html

            class R:
                parsed = RemediationChunkOutput(
                    html=html,
                    outline=[OutlineNode(id="s", level=2, text="S")],
                )

            return R()

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: FakeClient())

    chunk_plan = json.dumps({
        "title": "T", "language": "en", "page_count": 1, "description": "",
        "sections": [{"id": "s", "heading": "S", "layout": "prose", "fields": [], "images": []}],
    })

    result = await _remediate_chunk(b"%PDF-1.4\n%%EOF", "test.pdf", chunk_plan)

    assert "refer to the original" not in result.html
    assert call_count["n"] >= 2, "should have retried after rejecting placeholder content"


# ---------------------------------------------------------------------------
# Prompt contract tests — enforce that REMEDIATION_SYSTEM contains the
# explicit language needed to prevent repeat Gemini compliance failures.
# ---------------------------------------------------------------------------


def test_remediation_system_requires_every_th_has_scope() -> None:
    """REMEDIATION_SYSTEM must say every <th> requires a scope attribute (not just show examples)."""
    from backend.app.gemini import prompts

    text = prompts.REMEDIATION_SYSTEM
    assert "structural check failure" in text or (
        "every <th>" in text.lower() and "scope" in text.lower()
    ), "Prompt must explicitly state that every <th> must carry scope= to prevent silent omissions"


def test_remediation_system_requires_label_for_self_check() -> None:
    """REMEDIATION_SYSTEM must require a self-check that every label for= matches an existing id."""
    from backend.app.gemini import prompts

    text = prompts.REMEDIATION_SYSTEM.lower()
    assert "self-check" in text or (
        "verify" in text and "label for" in text and "id" in text
    ), "Prompt must require verifying every label for= has a matching element id="


def test_remediation_system_forbids_heading_level_jumps() -> None:
    """REMEDIATION_SYSTEM must explicitly forbid jumping heading levels (e.g. h1→h3)."""
    from backend.app.gemini import prompts

    text = prompts.REMEDIATION_SYSTEM.lower()
    assert "increment by one" in text or "one level at a time" in text or (
        "never jump" in text and "heading" in text
    ), "Prompt must explicitly forbid non-sequential heading levels"


def test_remediation_system_requires_exact_plan_field_ids() -> None:
    """REMEDIATION_SYSTEM must forbid renaming or abbreviating plan field IDs."""
    from backend.app.gemini import prompts

    text = prompts.REMEDIATION_SYSTEM.lower()
    assert "never rename" in text or "never abbreviate" in text or (
        "copy the id" in text or "copy each id" in text
    ), "Prompt must explicitly forbid renaming/abbreviating plan field IDs"


@pytest.mark.asyncio
async def test_remediate_applies_fix_html_before_wcag_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Gemini omits <th scope>, fix_html should add it so validation passes on the first attempt."""
    from backend.app.gemini.remediate import remediate_pdf

    call_count = {"n": 0}

    th_no_scope_html = (
        '<main id="content" lang="en">'
        '<h1 id="document-title">T</h1>'
        '<table><caption>C</caption>'
        '<thead><tr><th>A</th></tr></thead>'
        '<tbody><tr><td>1</td></tr></tbody>'
        '</table></main>'
    )

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            call_count["n"] += 1

            class R:
                parsed = RemediationOutput(
                    html=th_no_scope_html,
                    outline=[OutlineNode(id="document-title", level=1, text="T")],
                    title="T",
                    page_count=1,
                )

            return R()

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: FakeClient())

    result = await remediate_pdf(b"%PDF-1.4\n%%EOF", "test.pdf")

    assert call_count["n"] == 1, "fix_html should let first attempt succeed without retry"
    assert 'scope="col"' in result.html, "fix_html should have added scope to the <th>"


@pytest.mark.asyncio
async def test_remediate_applies_fix_html_to_orphan_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Gemini renames an input id, fix_html should rewrite it to match the label.for."""
    from backend.app.gemini.remediate import remediate_pdf

    orphan_label_html = (
        '<main id="content" lang="en">'
        '<h1 id="document-title">T</h1>'
        '<form>'
        '<label for="full-name">Full Name</label>'
        '<input id="full-name-text" type="text" />'
        '</form></main>'
    )

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            class R:
                parsed = RemediationOutput(
                    html=orphan_label_html,
                    outline=[OutlineNode(id="document-title", level=1, text="T")],
                    title="T",
                    page_count=1,
                )

            return R()

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: FakeClient())

    result = await remediate_pdf(b"%PDF-1.4\n%%EOF", "test.pdf")

    assert 'id="full-name"' in result.html
    assert "full-name-text" not in result.html


def test_remediate_chunk_uses_max_output_token_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """_remediate_chunk must request 65 536 output tokens so large section chunks don't truncate."""
    import asyncio

    from backend.app.gemini.remediate import _remediate_chunk

    seen: dict = {}

    class FakeFiles:
        def upload(self, *, file, config):
            return object()

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            seen["max_output_tokens"] = config.max_output_tokens

            class R:
                from backend.app.gemini.remediate import OutlineNode, RemediationChunkOutput

                parsed = RemediationChunkOutput(
                    html='<section id="s"><h2 id="s">S</h2><p>Text.</p></section>',
                    outline=[OutlineNode(id="s", level=2, text="S")],
                )

            return R()

    class FakeClient:
        files = FakeFiles()
        models = FakeModels()

    monkeypatch.setattr("backend.app.gemini.remediate.get_genai_client", lambda: FakeClient())

    plan = json.dumps({
        "title": "T", "language": "en", "page_count": 1, "description": "",
        "sections": [{"id": "s", "heading": "S", "layout": "prose", "fields": [], "images": []}],
    })
    asyncio.run(_remediate_chunk(b"%PDF-1.4\n%%EOF", "test.pdf", plan))

    assert seen["max_output_tokens"] >= 65_536, (
        f"chunk token budget too small: {seen['max_output_tokens']} < 65536"
    )


@pytest.mark.skipif(not RUN_LIVE, reason="RUN_LIVE=1 required for live Gemini call")
@pytest.mark.skipif(
    not SAMPLE_PDF.exists(), reason="demo PDF not present; run scripts/download_samples.sh"
)
@pytest.mark.asyncio
async def test_live_basic_form_pdf() -> None:
    """Exercise the real Gemini API end-to-end. Gated so CI doesn't burn tokens."""
    from backend.app.gemini.remediate import remediate_pdf

    pdf = SAMPLE_PDF.read_bytes()
    result = await remediate_pdf(pdf, source_hint=SAMPLE_PDF.name)
    assert "<main" in result.html
    assert "<h1" in result.html
    assert result.page_count >= 1
    assert result.outline
