from __future__ import annotations

import asyncio
import inspect
import ipaddress
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, is_dataclass
from importlib import import_module
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from backend.app import cache
from backend.app.config import get_settings
from backend.app.document_design import render_designed_html
from backend.app.documents import DocFormat
from backend.app.gemini import file_search, remediate
from backend.app.gemini import plan as plan_mod
from backend.app.gemini.design import plan_design
from backend.app.images import (
    ExtractedImageAsset,
    extract_docx_image_assets,
    extract_pdf_image_assets,
)
from backend.app.parser_backends.liteparse_adapter import LiteParseUnavailableError
from backend.app.parser_backends.llamaparse_adapter import LlamaParseError
from backend.app.pdf_fetch import PdfFetchError, fetch_document, store_document
from backend.app.pipeline_version import (
    STRUCTURED_RENDER_V2_TRANSCRIPT_PIPELINE_VERSION,
    TRANSCRIPT_PIPELINE_VERSION,
    is_current_transcript_pipeline,
)

router = APIRouter()
log = logging.getLogger(__name__)


class StructuredRenderV2UnavailableError(RuntimeError):
    pass


@dataclass
class StructuredRenderV2Output:
    result: remediate.RemediationResult
    parsed_document: Any
    layout_plan: Any


def _is_current_pipeline(transcript: cache.Transcript | None) -> bool:
    return transcript is not None and is_current_transcript_pipeline(transcript.pipeline_version)


def _should_try_structured_render_v2(settings, fmt: DocFormat) -> bool:
    return settings.structured_render_v2_enabled and fmt in {
        DocFormat.PDF,
        DocFormat.DOCX,
        DocFormat.XLSX,
    }


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    lowered = host.lower()
    if lowered == "localhost":
        return True
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def _trusted_fetch_hosts(*hosts: str | None) -> frozenset[str]:
    allowed = {host.lower() for host in hosts if host}
    if any(_is_loopback_host(host) for host in allowed):
        allowed.update({"localhost", "127.0.0.1", "::1"})
    return frozenset(allowed)


def _should_attach_self_fetch_auth(
    *,
    src_host: str | None,
    trusted_hosts: frozenset[str],
) -> bool:
    if not src_host:
        return False
    return src_host.lower() in trusted_hosts


def _jsonable_artifact(payload: Any) -> Any:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    if is_dataclass(payload):
        return asdict(payload)
    if hasattr(payload, "dict"):
        return payload.dict()
    if isinstance(payload, (dict, list, str, int, float, bool)) or payload is None:
        return payload
    if hasattr(payload, "__dict__"):
        return payload.__dict__
    return payload


def _write_artifact_json(path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable_artifact(payload), indent=2, sort_keys=True),
        encoding="utf-8",
    )


async def _persist_structured_render_artifacts(
    settings,
    sha256: str,
    *,
    parsed_document: Any,
    layout_plan: Any,
) -> None:
    artifact_dir = settings.artifacts_dir_for(sha256)
    if parsed_document is not None:
        await asyncio.to_thread(
            _write_artifact_json,
            artifact_dir / "parsed_document.json",
            parsed_document,
        )
    if layout_plan is not None:
        await asyncio.to_thread(
            _write_artifact_json,
            artifact_dir / "layout_plan.json",
            layout_plan,
        )


def _coerce_structured_render_result(
    payload: Any,
    *,
    source_hint: str,
    doc_bytes: bytes,
    fmt: DocFormat,
) -> remediate.RemediationResult:
    if isinstance(payload, remediate.RemediationResult):
        return payload

    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="python")
    elif is_dataclass(payload):
        payload = asdict(payload)

    if not isinstance(payload, dict):
        raise TypeError(f"unsupported structured render result payload: {type(payload)!r}")

    title = payload.get("title") or source_hint.rstrip("/").rsplit("/", 1)[-1] or "Document"
    return remediate.RemediationResult(
        html=payload["html"],
        outline=_jsonable_artifact(payload.get("outline") or []),
        title=title,
        description=payload.get("description") or "",
        language=payload.get("language") or "en",
        page_count=int(payload.get("page_count") or remediate._count_pages(doc_bytes, fmt)),
        render_mode=payload.get("render_mode") or "static",
    )


def _coerce_structured_render_v2_output(
    payload: Any,
    *,
    source_hint: str,
    doc_bytes: bytes,
    fmt: DocFormat,
) -> StructuredRenderV2Output:
    if isinstance(payload, StructuredRenderV2Output):
        return payload

    parsed_document = None
    layout_plan = None
    result_payload = payload

    if isinstance(payload, dict):
        parsed_document = payload.get("parsed_document")
        layout_plan = payload.get("layout_plan")
        result_payload = payload.get("result") or payload.get("transcript") or payload
    elif isinstance(payload, tuple) and len(payload) == 3:
        result_payload, parsed_document, layout_plan = payload

    return StructuredRenderV2Output(
        result=_coerce_structured_render_result(
            result_payload,
            source_hint=source_hint,
            doc_bytes=doc_bytes,
            fmt=fmt,
        ),
        parsed_document=parsed_document,
        layout_plan=layout_plan,
    )


async def run_structured_render_v2(
    doc_bytes: bytes,
    *,
    source_hint: str,
    fmt: DocFormat,
    image_filenames: list[str | ExtractedImageAsset],
    sha256: str,
    pdf_backend: str,
) -> StructuredRenderV2Output:
    try:
        module = import_module("backend.app.structured_render_v2")
    except ImportError as exc:
        raise StructuredRenderV2UnavailableError("structured-render-v2 module is unavailable") from exc

    runner = getattr(module, "run_structured_render_v2", None)
    if runner is None:
        raise StructuredRenderV2UnavailableError("structured-render-v2 runner is unavailable")

    try:
        payload = runner(
            doc_bytes,
            source_hint=source_hint,
            fmt=fmt,
            image_filenames=image_filenames,
            sha256=sha256,
            pdf_backend=pdf_backend,
        )
    except LiteParseUnavailableError as exc:
        raise StructuredRenderV2UnavailableError("liteparse backend is unavailable") from exc
    except LlamaParseError as exc:
        raise StructuredRenderV2UnavailableError(f"llamaparse backend is unavailable: {exc}") from exc

    if inspect.isawaitable(payload):
        try:
            payload = await payload
        except LiteParseUnavailableError as exc:
            raise StructuredRenderV2UnavailableError("liteparse backend is unavailable") from exc
        except LlamaParseError as exc:
            raise StructuredRenderV2UnavailableError(
                f"llamaparse backend is unavailable: {exc}"
            ) from exc

    return _coerce_structured_render_v2_output(
        payload,
        source_hint=source_hint,
        doc_bytes=doc_bytes,
        fmt=fmt,
    )


@router.get("/transcript")
async def get_transcript(src: str = Query(..., description="URL of the original document")) -> dict:
    """Return the cached transcript for this source URL, or 404."""
    settings = get_settings()
    transcript = await cache.get_by_source_url(settings.cache_db_path, src)
    if not _is_current_pipeline(transcript):
        raise HTTPException(404, "not_cached")
    return _transcript_payload(transcript)


@router.get("/transcript/ingest")
async def ingest_transcript(
    request: Request,
    src: str = Query(..., description="URL of the document to ingest")
):
    """Stream progress events as the document is fetched, uploaded, indexed,
    and remediated. Works for PDF, DOCX, and XLSX. On the final `done` event
    the full transcript payload (including `format` + `render_mode`) is
    delivered.
    """
    settings = get_settings()
    if not settings.transcript_llm_configured:
        if settings.llm_provider == "ollama":
            raise HTTPException(503, "OLLAMA_API_KEY not configured")
        raise HTTPException(503, "GOOGLE_API_KEY not configured")

    async def event_stream() -> AsyncIterator[dict]:
        try:
            yield _event("started", 5, "Queued")

            existing = await cache.get_by_source_url(settings.cache_db_path, src)
            if _is_current_pipeline(existing):
                yield _event(
                    "done",
                    100,
                    "Cached",
                    payload={**_transcript_payload(existing), "cached": True},
                )
                return

            yield _event("fetching", 15, "Fetching document")
            try:
                origin_host = urlparse(settings.public_origin).hostname
                request_host = request.url.hostname
                allowed = _trusted_fetch_hosts(origin_host, request_host)
                # Self-fetches to PUBLIC_ORIGIN must include the demo basic-auth header
                # so the middleware doesn't 401 the backend's own request.
                src_host = urlparse(src).hostname
                self_fetch_headers: dict[str, str] | None = None
                if (
                    settings.basic_auth_enabled
                    and _should_attach_self_fetch_auth(
                        src_host=src_host,
                        trusted_hosts=allowed,
                    )
                ):
                    import base64
                    creds = f"{settings.basic_auth_user}:{settings.basic_auth_password}".encode()
                    self_fetch_headers = {
                        "Authorization": "Basic " + base64.b64encode(creds).decode()
                    }
                fetched = await fetch_document(
                    src,
                    max_bytes=settings.max_pdf_bytes,
                    allowed_hosts=allowed,
                    headers=self_fetch_headers,
                )
            except PdfFetchError as e:
                yield _event("error", 0, f"fetch failed: {e}")
                return
            yield _event(
                "fetched",
                25,
                f"Fetched {fetched.content_length // 1024} KB ({fetched.fmt.value.upper()})",
            )

            fmt = fetched.fmt

            existing_by_hash = await cache.get_by_hash(settings.cache_db_path, fetched.sha256)
            if _is_current_pipeline(existing_by_hash):
                await cache.put(
                    settings.cache_db_path,
                    sha256=existing_by_hash.sha256,
                    source_url=src,
                    html=existing_by_hash.html,
                    outline=existing_by_hash.outline,
                    file_search_store=existing_by_hash.file_search_store,
                    page_count=existing_by_hash.page_count,
                    title=existing_by_hash.title,
                    description=existing_by_hash.description,
                    published_at=existing_by_hash.published_at,
                    format=existing_by_hash.format,
                    render_mode=existing_by_hash.render_mode,
                    pipeline_version=existing_by_hash.pipeline_version,
                )
                yield _event(
                    "done",
                    100,
                    "Cached (content match)",
                    payload={**_transcript_payload(existing_by_hash), "cached": True},
                )
                return

            store_document(settings.pdf_dir, fetched.sha256, fetched.bytes_, fmt)
            # PDF and DOCX expose inline images we can serve directly; XLSX
            # stays on the empty list until we have a fixture that needs it.
            image_filenames: list[str | ExtractedImageAsset] = []
            if fmt is DocFormat.PDF:
                image_filenames = extract_pdf_image_assets(
                    fetched.bytes_,
                    settings.images_dir_for(fetched.sha256),
                    include_vector_pages=settings.pdf_vector_images_enabled,
                    vector_min_ops=settings.pdf_vector_min_ops,
                    vector_dpi=settings.pdf_vector_raster_dpi,
                )
            elif fmt is DocFormat.DOCX:
                image_filenames = extract_docx_image_assets(
                    fetched.bytes_, settings.images_dir_for(fetched.sha256)
                )

            yield _event("planning", 35, "Planning document structure")
            result: remediate.RemediationResult | None = None
            result_pipeline_version = TRANSCRIPT_PIPELINE_VERSION
            plan_json: str | None = None
            content_plan = None
            design_plan = None
            if _should_try_structured_render_v2(settings, fmt):
                try:
                    structured_result = await run_structured_render_v2(
                        fetched.bytes_,
                        source_hint=src,
                        fmt=fmt,
                        image_filenames=image_filenames,
                        sha256=fetched.sha256,
                        pdf_backend=settings.structured_render_v2_pdf_backend,
                    )
                    result = structured_result.result
                    result_pipeline_version = STRUCTURED_RENDER_V2_TRANSCRIPT_PIPELINE_VERSION
                    if image_filenames:
                        result.html = remediate.rewrite_image_srcs(
                            result.html, fetched.sha256, image_filenames
                        )
                    try:
                        await _persist_structured_render_artifacts(
                            settings,
                            fetched.sha256,
                            parsed_document=structured_result.parsed_document,
                            layout_plan=structured_result.layout_plan,
                        )
                    except Exception:
                        log.exception(
                            "failed to persist structured-render-v2 artifacts for %s",
                            fetched.sha256,
                        )
                except StructuredRenderV2UnavailableError as exc:
                    log.warning(
                        "structured-render-v2 unavailable for %s; falling back: %s",
                        src,
                        exc,
                    )
                except Exception:
                    log.exception("structured-render-v2 failed for %s; falling back", src)

            if result is None:
                try:
                    content_plan = await plan_mod.plan_pdf(
                        fetched.bytes_,
                        source_hint=src,
                        image_filenames=image_filenames,
                        fmt=fmt,
                    )
                    plan_json = content_plan.model_dump_json()
                    if fmt in {DocFormat.PDF, DocFormat.DOCX}:
                        design_plan = await plan_design(
                            content_plan,
                            source_hint=src,
                            fmt=fmt,
                        )
                except Exception:
                    log.exception("planner failed; falling back to direct synthesis")
            yield _event(
                "planned",
                50,
                "Structured render ready" if result is not None else ("Plan ready" if plan_json else "Plan skipped"),
            )

            yield _event("indexing", 60, "Indexing for Ask panel")
            store_name: str | None
            try:
                store_name = await file_search.ensure_store(
                    fetched.sha256, fetched.bytes_, fmt=fmt
                )
                yield _event("indexed", 70, "Indexed")
            except Exception:
                # Gemini File Search currently rejects some OOXML MIME types;
                # rather than fail the whole ingest, skip the index and let
                # the Ask panel degrade for this document. Remediation still
                # ships — that's the primary contract.
                log.exception(
                    "file_search indexing failed for %s; Ask panel will be unavailable",
                    src,
                )
                store_name = None
                yield _event("indexed", 70, "Indexing skipped (Ask unavailable)")

            yield _event("generating", 82, "Remediating document")
            if result is None:
                result = await remediate.remediate_document(
                    fetched.bytes_,
                    source_hint=src,
                    fmt=fmt,
                    image_filenames=image_filenames,
                    plan_json=plan_json,
                )
                if image_filenames:
                    result.html = remediate.rewrite_image_srcs(
                        result.html, fetched.sha256, image_filenames
                    )
                if (
                    fmt in {DocFormat.PDF, DocFormat.DOCX}
                    and result.render_mode == "static"
                    and content_plan is not None
                    and design_plan is not None
                ):
                    result.html = render_designed_html(
                        result.html,
                        content_plan,
                        design_plan,
                        image_assets=image_filenames,
                        sha256=fetched.sha256,
                    )

            await cache.put(
                settings.cache_db_path,
                sha256=fetched.sha256,
                source_url=src,
                html=result.html,
                outline=result.outline,
                file_search_store=store_name,
                page_count=result.page_count,
                title=result.title,
                description=result.description,
                format=fmt.value,
                render_mode=result.render_mode,
                pipeline_version=result_pipeline_version,
            )

            yield _event(
                "done",
                100,
                "Ready",
                payload={
                    "sha256": fetched.sha256,
                    "source_url": src,
                    "html": result.html,
                    "outline": result.outline,
                    "title": result.title,
                    "description": result.description,
                    "page_count": result.page_count,
                    "format": fmt.value,
                    "render_mode": result.render_mode,
                    "cached": False,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("ingest failed for %s", src)
            yield _event("error", 0, f"{type(e).__name__}: {e}")

    return EventSourceResponse(event_stream())


def _transcript_payload(t: cache.Transcript) -> dict:
    return {
        "sha256": t.sha256,
        "source_url": t.source_url,
        "html": t.html,
        "outline": t.outline,
        "title": t.title,
        "description": t.description,
        "page_count": t.page_count,
        "format": t.format,
        "render_mode": t.render_mode,
        "cached": True,
    }


def _event(step: str, pct: int, message: str, *, payload: dict | None = None) -> dict:
    data = {"step": step, "pct": pct, "message": message}
    if payload is not None:
        data["payload"] = payload
    return {"event": step, "data": json.dumps(data)}
