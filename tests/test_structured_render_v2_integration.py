from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import cache
from backend.app.config import get_settings
from backend.app.document_design import heuristic_design_plan
from backend.app.documents import DocFormat
from backend.app.gemini.plan import DocumentPlan, SectionPlan
from backend.app.gemini.remediate import RemediationResult
from backend.app.parser_backends.liteparse_adapter import LiteParseUnavailableError
from backend.app.parser_backends.llamaparse_adapter import LlamaParseUnavailableError
from backend.app.pdf_fetch import FetchedDoc, sha256_of
from backend.app.pipeline_version import (
    STRUCTURED_RENDER_V2_TRANSCRIPT_PIPELINE_VERSION,
    TRANSCRIPT_PIPELINE_VERSION,
)
from backend.app.routes import transcript as transcript_routes


def _configure_ingest_env(
    monkeypatch,
    *,
    structured_render_v2_enabled: bool,
    pdf_backend: str = "native",
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    monkeypatch.setenv(
        "STRUCTURED_RENDER_V2_ENABLED",
        "true" if structured_render_v2_enabled else "false",
    )
    monkeypatch.setenv("STRUCTURED_RENDER_V2_PDF_BACKEND", pdf_backend)
    get_settings.cache_clear()


def _parse_sse_events(body: str) -> list[dict]:
    events: list[dict] = []
    event_name: str | None = None
    data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
            continue
        if line.startswith("data: "):
            data_lines.append(line.removeprefix("data: "))
            continue
        if not line.strip() and event_name is not None:
            events.append({"event": event_name, "data": json.loads("\n".join(data_lines))})
            event_name = None
            data_lines = []
    if event_name is not None:
        events.append({"event": event_name, "data": json.loads("\n".join(data_lines))})
    return events


def _final_payload(body: str) -> dict:
    events = _parse_sse_events(body)
    assert events
    assert events[-1]["event"] == "done"
    return events[-1]["data"]["payload"]


def _fake_fetched_doc(src: str, pdf_bytes: bytes) -> FetchedDoc:
    return FetchedDoc(
        bytes_=pdf_bytes,
        sha256=sha256_of(pdf_bytes),
        source_url=src,
        content_length=len(pdf_bytes),
        fmt=DocFormat.PDF,
    )


def _legacy_plan() -> DocumentPlan:
    return DocumentPlan(
        title="Legacy Title",
        language="en",
        page_count=1,
        sections=[SectionPlan(id="intro", heading="Introduction", layout="prose")],
    )


def _legacy_result() -> RemediationResult:
    return RemediationResult(
        html=(
            '<main id="content" lang="en">'
            '<h1 id="document-title">Legacy Title</h1>'
            '<section id="intro"><h2 id="intro">Introduction</h2><p>Legacy body.</p></section>'
            "</main>"
        ),
        outline=[
            {"id": "document-title", "level": 1, "text": "Legacy Title"},
            {"id": "intro", "level": 2, "text": "Introduction"},
        ],
        title="Legacy Title",
        description="legacy",
        language="en",
        page_count=1,
    )


def test_structured_render_v2_success_path_persists_artifacts(
    client: TestClient,
    tmp_data_dir: Path,
    minimal_pdf_bytes: bytes,
    monkeypatch,
) -> None:
    _configure_ingest_env(monkeypatch, structured_render_v2_enabled=True)

    src = "https://example.com/structured.pdf"
    fetched = _fake_fetched_doc(src, minimal_pdf_bytes)

    async def fake_fetch_document(url, *, max_bytes, allowed_hosts, headers):
        assert url == src
        return fetched

    async def fake_ensure_store(sha256, bytes_, *, fmt):
        assert sha256 == fetched.sha256
        assert fmt is DocFormat.PDF
        return "stores/structured"

    async def fake_run_structured_render_v2(
        doc_bytes,
        *,
        source_hint,
        fmt,
        image_filenames,
        sha256,
        pdf_backend,
    ):
        assert doc_bytes == minimal_pdf_bytes
        assert source_hint == src
        assert fmt is DocFormat.PDF
        assert image_filenames == []
        assert sha256 == fetched.sha256
        assert pdf_backend == "native"
        return transcript_routes.StructuredRenderV2Output(
            result=RemediationResult(
                html='<main lang="en"><h1 id="document-title">Structured Title</h1></main>',
                outline=[{"id": "document-title", "level": 1, "text": "Structured Title"}],
                title="Structured Title",
                description="v2",
                language="en",
                page_count=1,
            ),
            parsed_document={"kind": "parsed", "backend": pdf_backend, "pages": 1},
            layout_plan={"kind": "layout", "template": "article"},
        )

    async def fail_plan_pdf(*args, **kwargs):
        raise AssertionError("legacy planner should not run after structured-render-v2 succeeds")

    async def fail_remediate(*args, **kwargs):
        raise AssertionError("legacy remediation should not run after structured-render-v2 succeeds")

    monkeypatch.setattr("backend.app.routes.transcript.fetch_document", fake_fetch_document)
    monkeypatch.setattr("backend.app.routes.transcript.extract_pdf_image_assets", lambda *args, **kwargs: [])
    monkeypatch.setattr("backend.app.routes.transcript.file_search.ensure_store", fake_ensure_store)
    monkeypatch.setattr(
        "backend.app.routes.transcript.run_structured_render_v2",
        fake_run_structured_render_v2,
    )
    monkeypatch.setattr("backend.app.routes.transcript.plan_mod.plan_pdf", fail_plan_pdf)
    monkeypatch.setattr("backend.app.routes.transcript.remediate.remediate_document", fail_remediate)

    response = client.get("/api/transcript/ingest", params={"src": src})

    assert response.status_code == 200
    payload = _final_payload(response.text)
    assert payload["cached"] is False
    assert payload["title"] == "Structured Title"

    cached = asyncio.run(cache.get_by_hash(tmp_data_dir / "cache.db", fetched.sha256))
    assert cached is not None
    assert cached.pipeline_version == STRUCTURED_RENDER_V2_TRANSCRIPT_PIPELINE_VERSION

    artifact_dir = tmp_data_dir / "artifacts" / fetched.sha256
    assert json.loads((artifact_dir / "parsed_document.json").read_text()) == {
        "backend": "native",
        "kind": "parsed",
        "pages": 1,
    }
    assert json.loads((artifact_dir / "layout_plan.json").read_text()) == {
        "kind": "layout",
        "template": "article",
    }


def test_structured_render_v2_liteparse_unavailable_falls_back(
    client: TestClient,
    tmp_data_dir: Path,
    minimal_pdf_bytes: bytes,
    monkeypatch,
) -> None:
    _configure_ingest_env(
        monkeypatch,
        structured_render_v2_enabled=True,
        pdf_backend="liteparse",
    )

    src = "https://example.com/liteparse.pdf"
    fetched = _fake_fetched_doc(src, minimal_pdf_bytes)
    plan = _legacy_plan()

    async def fake_fetch_document(url, *, max_bytes, allowed_hosts, headers):
        assert url == src
        return fetched

    async def fake_plan_pdf(doc_bytes, *, source_hint, image_filenames, fmt):
        assert doc_bytes == minimal_pdf_bytes
        assert source_hint == src
        assert fmt is DocFormat.PDF
        return plan

    async def fake_plan_design(content_plan, *, source_hint, fmt):
        assert content_plan == plan
        assert source_hint == src
        return heuristic_design_plan(content_plan, fmt=fmt.value)

    async def fake_ensure_store(sha256, bytes_, *, fmt):
        assert sha256 == fetched.sha256
        return "stores/legacy"

    async def fake_remediate_document(
        doc_bytes,
        *,
        source_hint,
        fmt,
        image_filenames,
        plan_json,
    ):
        assert doc_bytes == minimal_pdf_bytes
        assert source_hint == src
        assert fmt is DocFormat.PDF
        assert plan_json is not None
        return _legacy_result()

    monkeypatch.setattr("backend.app.routes.transcript.fetch_document", fake_fetch_document)
    monkeypatch.setattr("backend.app.routes.transcript.extract_pdf_image_assets", lambda *args, **kwargs: [])
    monkeypatch.setattr("backend.app.routes.transcript.plan_mod.plan_pdf", fake_plan_pdf)
    monkeypatch.setattr("backend.app.routes.transcript.plan_design", fake_plan_design)
    monkeypatch.setattr("backend.app.routes.transcript.file_search.ensure_store", fake_ensure_store)
    monkeypatch.setattr(
        "backend.app.routes.transcript.remediate.remediate_document",
        fake_remediate_document,
    )
    monkeypatch.setattr(
        "backend.app.routes.transcript.render_designed_html",
        lambda html, *args, **kwargs: html,
    )
    monkeypatch.setattr(
        "backend.app.routes.transcript.import_module",
        lambda _name: type(
            "FakeStructuredRenderModule",
            (),
            {
                "run_structured_render_v2": staticmethod(
                    lambda *args, **kwargs: (_ for _ in ()).throw(
                        LiteParseUnavailableError("LiteParse CLI not found on PATH")
                    )
                )
            },
        )(),
    )

    response = client.get("/api/transcript/ingest", params={"src": src})

    assert response.status_code == 200
    payload = _final_payload(response.text)
    assert payload["cached"] is False
    assert payload["title"] == "Legacy Title"

    cached = asyncio.run(cache.get_by_hash(tmp_data_dir / "cache.db", fetched.sha256))
    assert cached is not None
    assert cached.pipeline_version == TRANSCRIPT_PIPELINE_VERSION
    assert not (tmp_data_dir / "artifacts" / fetched.sha256).exists()


def test_structured_render_v2_llamaparse_unavailable_falls_back(
    client: TestClient,
    tmp_data_dir: Path,
    minimal_pdf_bytes: bytes,
    monkeypatch,
) -> None:
    _configure_ingest_env(
        monkeypatch,
        structured_render_v2_enabled=True,
        pdf_backend="llamaparse",
    )

    src = "https://example.com/llamaparse.pdf"
    fetched = _fake_fetched_doc(src, minimal_pdf_bytes)
    plan = _legacy_plan()

    async def fake_fetch_document(url, *, max_bytes, allowed_hosts, headers):
        assert url == src
        return fetched

    async def fake_plan_pdf(doc_bytes, *, source_hint, image_filenames, fmt):
        assert doc_bytes == minimal_pdf_bytes
        assert source_hint == src
        assert fmt is DocFormat.PDF
        return plan

    async def fake_plan_design(content_plan, *, source_hint, fmt):
        assert content_plan == plan
        assert source_hint == src
        return heuristic_design_plan(content_plan, fmt=fmt.value)

    async def fake_ensure_store(sha256, bytes_, *, fmt):
        assert sha256 == fetched.sha256
        return "stores/legacy"

    async def fake_remediate_document(
        doc_bytes,
        *,
        source_hint,
        fmt,
        image_filenames,
        plan_json,
    ):
        assert doc_bytes == minimal_pdf_bytes
        assert source_hint == src
        assert fmt is DocFormat.PDF
        assert plan_json is not None
        return _legacy_result()

    monkeypatch.setattr("backend.app.routes.transcript.fetch_document", fake_fetch_document)
    monkeypatch.setattr("backend.app.routes.transcript.extract_pdf_image_assets", lambda *args, **kwargs: [])
    monkeypatch.setattr("backend.app.routes.transcript.plan_mod.plan_pdf", fake_plan_pdf)
    monkeypatch.setattr("backend.app.routes.transcript.plan_design", fake_plan_design)
    monkeypatch.setattr("backend.app.routes.transcript.file_search.ensure_store", fake_ensure_store)
    monkeypatch.setattr(
        "backend.app.routes.transcript.remediate.remediate_document",
        fake_remediate_document,
    )
    monkeypatch.setattr(
        "backend.app.routes.transcript.render_designed_html",
        lambda html, *args, **kwargs: html,
    )
    monkeypatch.setattr(
        "backend.app.routes.transcript.import_module",
        lambda _name: type(
            "FakeStructuredRenderModule",
            (),
            {
                "run_structured_render_v2": staticmethod(
                    lambda *args, **kwargs: (_ for _ in ()).throw(
                        LlamaParseUnavailableError("missing LlamaParse API key")
                    )
                )
            },
        )(),
    )

    response = client.get("/api/transcript/ingest", params={"src": src})

    assert response.status_code == 200
    payload = _final_payload(response.text)
    assert payload["cached"] is False
    assert payload["title"] == "Legacy Title"

    cached = asyncio.run(cache.get_by_hash(tmp_data_dir / "cache.db", fetched.sha256))
    assert cached is not None
    assert cached.pipeline_version == TRANSCRIPT_PIPELINE_VERSION
    assert not (tmp_data_dir / "artifacts" / fetched.sha256).exists()


def test_structured_render_v2_failure_falls_back_to_legacy_path(
    client: TestClient,
    tmp_data_dir: Path,
    minimal_pdf_bytes: bytes,
    monkeypatch,
) -> None:
    _configure_ingest_env(monkeypatch, structured_render_v2_enabled=True)

    src = "https://example.com/fallback.pdf"
    fetched = _fake_fetched_doc(src, minimal_pdf_bytes)
    plan = _legacy_plan()
    structured_attempts: list[str] = []

    async def fake_fetch_document(url, *, max_bytes, allowed_hosts, headers):
        assert url == src
        return fetched

    async def fake_run_structured_render_v2(*args, **kwargs):
        structured_attempts.append(kwargs["pdf_backend"])
        raise RuntimeError("v2 render failed")

    async def fake_plan_pdf(doc_bytes, *, source_hint, image_filenames, fmt):
        assert doc_bytes == minimal_pdf_bytes
        assert fmt is DocFormat.PDF
        return plan

    async def fake_plan_design(content_plan, *, source_hint, fmt):
        assert content_plan == plan
        return heuristic_design_plan(content_plan, fmt=fmt.value)

    async def fake_ensure_store(sha256, bytes_, *, fmt):
        assert sha256 == fetched.sha256
        return "stores/legacy"

    async def fake_remediate_document(
        doc_bytes,
        *,
        source_hint,
        fmt,
        image_filenames,
        plan_json,
    ):
        assert doc_bytes == minimal_pdf_bytes
        assert source_hint == src
        assert plan_json is not None
        return _legacy_result()

    monkeypatch.setattr("backend.app.routes.transcript.fetch_document", fake_fetch_document)
    monkeypatch.setattr("backend.app.routes.transcript.extract_pdf_image_assets", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "backend.app.routes.transcript.run_structured_render_v2",
        fake_run_structured_render_v2,
    )
    monkeypatch.setattr("backend.app.routes.transcript.plan_mod.plan_pdf", fake_plan_pdf)
    monkeypatch.setattr("backend.app.routes.transcript.plan_design", fake_plan_design)
    monkeypatch.setattr("backend.app.routes.transcript.file_search.ensure_store", fake_ensure_store)
    monkeypatch.setattr(
        "backend.app.routes.transcript.remediate.remediate_document",
        fake_remediate_document,
    )
    monkeypatch.setattr(
        "backend.app.routes.transcript.render_designed_html",
        lambda html, *args, **kwargs: html,
    )

    response = client.get("/api/transcript/ingest", params={"src": src})

    assert response.status_code == 200
    payload = _final_payload(response.text)
    assert payload["cached"] is False
    assert payload["title"] == "Legacy Title"
    assert structured_attempts == ["native"]

    cached = asyncio.run(cache.get_by_hash(tmp_data_dir / "cache.db", fetched.sha256))
    assert cached is not None
    assert cached.pipeline_version == TRANSCRIPT_PIPELINE_VERSION
    assert not (tmp_data_dir / "artifacts" / fetched.sha256).exists()
