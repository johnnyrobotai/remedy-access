#!/usr/bin/env python3
"""Pre-convert document URLs so the first visitor gets an instant cache hit.

Usage:
    python scripts/prewarm.py <url-or-path> [<url-or-path> ...]

Local file paths are staged through the backend's public demo URL so the
cache stores a canonical source_url.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Make the backend package importable regardless of cwd.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import cache  # noqa: E402
from backend.app.config import get_settings  # noqa: E402
from backend.app.documents import DocFormat, detect_format  # noqa: E402
from backend.app.gemini import file_search, remediate  # noqa: E402
from backend.app.images import extract_docx_image_assets, extract_pdf_image_assets  # noqa: E402
from backend.app.pdf_fetch import fetch_document, sha256_of, store_document  # noqa: E402
from backend.app.pipeline_version import (  # noqa: E402
    STRUCTURED_RENDER_V2_TRANSCRIPT_PIPELINE_VERSION,
    TRANSCRIPT_PIPELINE_VERSION,
    is_current_transcript_pipeline,
)
from backend.app.routes.transcript import (  # noqa: E402
    StructuredRenderV2UnavailableError,
    run_structured_render_v2,
)

LACCD_STUDENT_FORM_URLS = [
    "https://www.laccd.edu/sites/laccd.edu/files/2022-08/Nonresident_Tuition_Exemption_Request.pdf",
    "https://www.laccd.edu/sites/laccd.edu/files/2024-08/Nonresident%20Tuition%20Fee%20Waiver%20Application.docx",
    "https://www.laccd.edu/sites/laccd.edu/files/2024-08/Nonresident%20Tuition%20Fee%20Waiver%20Application.pdf",
    "https://www.laccd.edu/sites/laccd.edu/files/2022-08/Supplemental_Residency_Questionnaire.pdf",
    "https://www.laccd.edu/sites/laccd.edu/files/2022-08/Certification%20of%20Homeless%20Status%20REV%20012018.docx",
    "https://www.laccd.edu/sites/laccd.edu/files/2022-08/Certification%20of%20Homeless%20Status%20REV%20012018.pdf",
    "https://www.laccd.edu/sites/laccd.edu/files/2022-08/APPLICATION%20FOR%20NONCREDIT%20ADMISSION%202.6%20Fillable.pdf",
    "https://www.laccd.edu/sites/laccd.edu/files/2022-08/APPLICATION%20FOR%20NONCREDIT%20ADMISSION%202.7%20Fillable%20SPANISH.pdf",
    "https://www.laccd.edu/sites/laccd.edu/files/2023-10/pass_no_pass_petition.pdf",
    "https://www.laccd.edu/sites/laccd.edu/files/2022-08/High%20School%20Graduation%20Update%20Form.pdf",
    "https://www.laccd.edu/sites/laccd.edu/files/2024-05/laccd_ew_petition_240209_0.pdf",
    "https://www.laccd.edu/sites/laccd.edu/files/2026-02/K-12%20Parent%20Consent.pdf",
    "https://www.laccd.edu/sites/laccd.edu/files/2025-09/LACCD%20Petition%20for%20Academic%20Renewal%20250505.pdf",
    "https://www.laccd.edu/sites/laccd.edu/files/2024-07/Petition%20for%20Credit%20for%20Prior%20Learning%20v7.pdf",
]


async def prewarm_one(src: str) -> None:
    settings = get_settings()
    existing_for_url = await cache.get_by_source_url(settings.cache_db_path, src)
    if existing_for_url and is_current_transcript_pipeline(existing_for_url.pipeline_version):
        print(f"[skip] cached    {src}")
        return

    if src.startswith(("http://", "https://")):
        print(f"[fetch] {src}")
        fetched = await fetch_document(
            src,
            max_bytes=settings.max_pdf_bytes,
            headers=_fetch_headers_for_source(src),
        )
        doc_bytes, sha, fmt = fetched.bytes_, fetched.sha256, fetched.fmt
    else:
        path = Path(src)
        doc_bytes = path.read_bytes()
        sha = sha256_of(doc_bytes)
        fmt = detect_format(url=str(path), head_bytes=doc_bytes[:4096])
        src = f"file://{path.resolve()}"

    existing = await cache.get_by_hash(settings.cache_db_path, sha)
    if existing is not None and is_current_transcript_pipeline(existing.pipeline_version):
        print(f"[reuse] content-match {sha[:12]}")
        await cache.put(
            settings.cache_db_path,
            sha256=existing.sha256,
            source_url=src,
            html=existing.html,
            outline=existing.outline,
            file_search_store=existing.file_search_store,
            page_count=existing.page_count,
            title=existing.title,
            description=existing.description,
            published_at=existing.published_at,
            format=existing.format,
            render_mode=existing.render_mode,
            pipeline_version=existing.pipeline_version,
        )
        return

    store_document(settings.pdf_dir, sha, doc_bytes, fmt)
    image_filenames = []
    if fmt is DocFormat.PDF:
        image_filenames = extract_pdf_image_assets(
            doc_bytes,
            settings.images_dir_for(sha),
            include_vector_pages=settings.pdf_vector_images_enabled,
            vector_min_ops=settings.pdf_vector_min_ops,
            vector_dpi=settings.pdf_vector_raster_dpi,
        )
    elif fmt is DocFormat.DOCX:
        image_filenames = extract_docx_image_assets(doc_bytes, settings.images_dir_for(sha))

    print(f"[index] {src}")
    try:
        store_name = await file_search.ensure_store(sha, doc_bytes, fmt=fmt)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] index skipped for {src}: {e}")
        store_name = None

    print(f"[generate] {src}")
    pipeline_version = TRANSCRIPT_PIPELINE_VERSION
    result = None
    if settings.structured_render_v2_enabled:
        try:
            structured = await run_structured_render_v2(
                doc_bytes,
                source_hint=src,
                fmt=fmt,
                image_filenames=image_filenames,
                sha256=sha,
                pdf_backend=settings.structured_render_v2_pdf_backend,
            )
            result = structured.result
            pipeline_version = STRUCTURED_RENDER_V2_TRANSCRIPT_PIPELINE_VERSION
        except StructuredRenderV2UnavailableError as e:
            print(f"[warn] structured render unavailable for {src}: {e}")
    if result is None:
        result = await remediate.remediate_document(
            doc_bytes,
            source_hint=src,
            fmt=fmt,
            image_filenames=image_filenames,
        )
    if image_filenames:
        result.html = remediate.rewrite_image_srcs(result.html, sha, image_filenames)

    await cache.put(
        settings.cache_db_path,
        sha256=sha,
        source_url=src,
        html=result.html,
        outline=result.outline,
        file_search_store=store_name,
        page_count=result.page_count,
        title=result.title,
        description=result.description,
        format=fmt.value,
        render_mode=result.render_mode,
        pipeline_version=pipeline_version,
    )
    print(f"[done] {src}  format={fmt.value} pages={result.page_count}")


def _fetch_headers_for_source(src: str) -> dict[str, str] | None:
    if "laccd.edu/" not in src:
        return None
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/147.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.laccd.edu/students/student-forms",
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("urls", nargs="*", help="Document URLs or local file paths")
    parser.add_argument(
        "--laccd-student-forms",
        action="store_true",
        help="Prewarm the LACCD Student Forms inventory used by the demo.",
    )
    args = parser.parse_args()
    urls = [*args.urls]
    if args.laccd_student_forms:
        urls.extend(LACCD_STUDENT_FORM_URLS)
    if not urls:
        parser.error("provide at least one URL/path or pass --laccd-student-forms")

    settings = get_settings()
    settings.ensure_dirs()
    await cache.init_schema(settings.cache_db_path)

    for src in urls:
        try:
            await prewarm_one(src)
        except Exception as e:  # noqa: BLE001
            print(f"[error] {src}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
