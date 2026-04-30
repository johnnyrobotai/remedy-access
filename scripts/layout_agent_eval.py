#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.config import get_settings
from backend.app.documents import DocFormat
from backend.app.images import extract_pdf_image_assets
from backend.app.structured_render_v2 import run_structured_render_v2

DEFAULT_DOCS = [
    "Academic_Paper_Sample.pdf",
    "sample-tables.pdf",
    "USCIS_I9.pdf",
]

_REQUIRED_ARTIFACT_FILES = (
    "initial_layout_plan.json",
    "revised_layout_plan.json",
    "critique.json",
    "critique_attempts.json",
    "skip_reason.json",
)


@dataclass
class LayoutAgentReportEntry:
    name: str
    sha256: str
    title: str | None
    score: str
    artifact_dir: str
    before: str | None
    after: str | None
    critique_summary: str
    skip_reason: str | None
    attempt_count: int
    error_count: int
    attempt_errors: list[str]
    changed_fields: list[str]
    missing_files: list[str]
    run_error: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sha256": self.sha256,
            "title": self.title,
            "score": self.score,
            "artifact_dir": self.artifact_dir,
            "before": self.before,
            "after": self.after,
            "critique_summary": self.critique_summary,
            "skip_reason": self.skip_reason,
            "attempt_count": self.attempt_count,
            "error_count": self.error_count,
            "attempt_errors": self.attempt_errors,
            "changed_fields": self.changed_fields,
            "missing_files": self.missing_files,
            "run_error": self.run_error,
        }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _doc_format_for_name(name: str) -> DocFormat:
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        return DocFormat.PDF
    if suffix == ".docx":
        return DocFormat.DOCX
    if suffix == ".xlsx":
        return DocFormat.XLSX
    raise ValueError(f"unsupported eval document type: {name}")


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _summarize_layout_changes(initial: dict[str, Any], revised: dict[str, Any]) -> list[str]:
    changes: list[str] = []

    for key in ("hero_treatment", "density", "toc_policy"):
        if initial.get(key) != revised.get(key):
            changes.append(f"{key}: {initial.get(key)} -> {revised.get(key)}")

    initial_sections = {section["section_id"]: section for section in initial.get("sections", [])}
    revised_sections = {section["section_id"]: section for section in revised.get("sections", [])}
    for section_id, revised_section in revised_sections.items():
        initial_section = initial_sections.get(section_id)
        if initial_section is None:
            changes.append(f"section {section_id}: added")
            continue
        for key in ("container", "rail_side"):
            if initial_section.get(key) != revised_section.get(key):
                changes.append(
                    f"section {section_id} {key}: "
                    f"{initial_section.get(key)} -> {revised_section.get(key)}"
                )
        initial_blocks = {
            block["block_index"]: block for block in initial_section.get("blocks", [])
        }
        for revised_block in revised_section.get("blocks", []):
            initial_block = initial_blocks.get(revised_block["block_index"])
            if initial_block is None:
                changes.append(
                    f"section {section_id} block {revised_block['block_index']}: added"
                )
                continue
            for key in ("placement", "container"):
                if initial_block.get(key) != revised_block.get(key):
                    changes.append(
                        f"section {section_id} block {revised_block['block_index']} {key}: "
                        f"{initial_block.get(key)} -> {revised_block.get(key)}"
                    )

    return changes


def _score_result(artifact_dir: Path) -> tuple[str, dict[str, Any]]:
    missing_files = [name for name in _REQUIRED_ARTIFACT_FILES if not (artifact_dir / name).exists()]
    if missing_files:
        return "missing-artifacts", {
            "missing_files": missing_files,
            "critique_summary": "",
            "skip_reason": None,
            "attempt_count": 0,
            "error_count": 0,
            "attempt_errors": [],
            "changed_fields": [],
            "before": artifact_dir / "before.png",
            "after": artifact_dir / "after.png",
        }

    initial = _read_json(artifact_dir / "initial_layout_plan.json") or {}
    revised = _read_json(artifact_dir / "revised_layout_plan.json") or {}
    critique = _read_json(artifact_dir / "critique.json") or {}
    attempts = _read_json(artifact_dir / "critique_attempts.json") or []
    skip_reason = (_read_json(artifact_dir / "skip_reason.json") or {}).get("skip_reason")
    changed_fields = _summarize_layout_changes(initial, revised)
    attempt_errors = [attempt.get("error") for attempt in attempts if attempt.get("error")]
    critique_present = bool(critique)

    if changed_fields:
        score = "changed"
    elif critique_present:
        score = "no-op"
    elif skip_reason and not attempts:
        score = "skipped"
    elif attempt_errors or skip_reason:
        score = "invalid"
    else:
        score = "no-op"

    return score, {
        "missing_files": [],
        "critique_summary": critique.get("summary", ""),
        "skip_reason": skip_reason,
        "attempt_count": len(attempts),
        "error_count": len(attempt_errors),
        "attempt_errors": attempt_errors,
        "changed_fields": changed_fields,
        "before": artifact_dir / "before.png",
        "after": artifact_dir / "after.png",
    }


def _build_markdown_report(
    results: list[LayoutAgentReportEntry],
    *,
    generated_at: str,
    reran_docs: bool,
    pdf_backend: str,
    repo_root: Path,
) -> str:
    counts = Counter(entry.score for entry in results)
    lines = [
        "# Layout Agent Eval Report",
        "",
        f"- Generated: `{generated_at}`",
        f"- Mode: `{'rerun' if reran_docs else 'summary-only'}`",
        f"- PDF backend: `{pdf_backend}`",
        "",
        "## Counts",
        "",
    ]
    for score in ("changed", "no-op", "invalid", "skipped", "missing-artifacts", "run-failed"):
        if counts.get(score):
            lines.append(f"- `{score}`: {counts[score]}")
    lines.extend(
        [
            "",
            "## Documents",
            "",
            "| Document | Score | Attempts | Errors | Change Summary | Reason |",
            "| --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    for entry in results:
        reason = entry.run_error or entry.skip_reason or "; ".join(entry.attempt_errors) or "-"
        changes = "; ".join(entry.changed_fields) or "-"
        lines.append(
            f"| `{entry.name}` | `{entry.score}` | {entry.attempt_count} | "
            f"{entry.error_count} | {changes} | {reason} |"
        )

    lines.extend(["", "## Artifact Paths", ""])
    for entry in results:
        lines.append(f"### `{entry.name}`")
        lines.append(f"- Artifact dir: `{entry.artifact_dir}`")
        if entry.before:
            lines.append(f"- Before: `{_relative(Path(entry.before), repo_root)}`")
        if entry.after:
            lines.append(f"- After: `{_relative(Path(entry.after), repo_root)}`")
        if entry.missing_files:
            lines.append(f"- Missing files: `{', '.join(entry.missing_files)}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


async def _evaluate_doc(
    name: str,
    *,
    force: bool,
    summary_only: bool,
    pdf_backend: str,
    repo_root: Path,
) -> LayoutAgentReportEntry:
    doc_path = repo_root / "demo" / "pdfs" / name
    if not doc_path.exists():
        return LayoutAgentReportEntry(
            name=name,
            sha256="",
            title=None,
            score="missing-artifacts",
            artifact_dir="",
            before=None,
            after=None,
            critique_summary="",
            skip_reason=None,
            attempt_count=0,
            error_count=0,
            attempt_errors=[],
            changed_fields=[],
            missing_files=["demo document is missing"],
            run_error=None,
        )

    settings = get_settings()
    fmt = _doc_format_for_name(name)
    data = doc_path.read_bytes()
    sha = _sha256(data)
    artifact_root = settings.artifacts_dir_for(sha)
    layout_agent_dir = artifact_root / "layout-agent"

    title: str | None = None
    run_error: str | None = None
    if not summary_only:
        if force and artifact_root.exists():
            shutil.rmtree(artifact_root)
        image_assets = []
        if fmt is DocFormat.PDF:
            image_dir = settings.images_dir_for(sha)
            if force and image_dir.exists():
                shutil.rmtree(image_dir)
            image_assets = extract_pdf_image_assets(
                data,
                image_dir,
                include_vector_pages=settings.pdf_vector_images_enabled,
                vector_min_ops=settings.pdf_vector_min_ops,
                vector_dpi=settings.pdf_vector_raster_dpi,
            )
        try:
            result = await run_structured_render_v2(
                data,
                source_hint=name,
                fmt=fmt,
                image_filenames=image_assets,
                sha256=sha,
                pdf_backend=pdf_backend,
            )
            title = result["result"].title
        except Exception as exc:  # noqa: BLE001
            run_error = str(exc)

    if title is None:
        cached_report = _read_json(settings.data_dir / "demo_cache_report.json") or []
        title = next(
            (
                item.get("title")
                for item in cached_report
                if item.get("name") == name and item.get("title")
            ),
            None,
        )

    score, detail = _score_result(layout_agent_dir)
    if run_error is not None:
        score = "run-failed"

    return LayoutAgentReportEntry(
        name=name,
        sha256=sha,
        title=title,
        score=score,
        artifact_dir=_relative(layout_agent_dir, repo_root),
        before=_relative(detail["before"], repo_root) if detail["before"].exists() else None,
        after=_relative(detail["after"], repo_root) if detail["after"].exists() else None,
        critique_summary=detail["critique_summary"],
        skip_reason=detail["skip_reason"],
        attempt_count=detail["attempt_count"],
        error_count=detail["error_count"],
        attempt_errors=detail["attempt_errors"],
        changed_fields=detail["changed_fields"],
        missing_files=detail["missing_files"],
        run_error=run_error,
    )


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Run or summarize a tiny layout-agent eval subset.")
    parser.add_argument("docs", nargs="*", default=DEFAULT_DOCS)
    parser.add_argument("--force", action="store_true", help="Delete prior artifacts and re-run docs.")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Only summarize existing artifacts; do not rerun structured rendering.",
    )
    parser.add_argument(
        "--pdf-backend",
        default="native",
        choices=("native", "liteparse"),
        help="PDF backend to use when rerunning docs.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Path for the JSON report. Defaults to data/layout_agent_eval_report.json.",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        help="Path for the Markdown summary. Defaults to data/layout_agent_eval_report.md.",
    )
    args = parser.parse_args()

    os.environ["LAYOUT_AGENT_ENABLED"] = "true"
    get_settings.cache_clear()
    settings = get_settings()
    settings.ensure_dirs()

    repo_root = Path(__file__).resolve().parents[1]
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    results = [
        await _evaluate_doc(
            name,
            force=args.force,
            summary_only=args.summary_only,
            pdf_backend=args.pdf_backend,
            repo_root=repo_root,
        )
        for name in args.docs
    ]

    report = {
        "generated_at": generated_at,
        "mode": "summary-only" if args.summary_only else "rerun",
        "pdf_backend": args.pdf_backend,
        "counts": dict(Counter(entry.score for entry in results)),
        "results": [entry.as_json() for entry in results],
    }

    json_output = args.json_output or settings.data_dir / "layout_agent_eval_report.json"
    markdown_output = args.markdown_output or settings.data_dir / "layout_agent_eval_report.md"
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown_output.write_text(
        _build_markdown_report(
            results,
            generated_at=generated_at,
            reran_docs=not args.summary_only,
            pdf_backend=args.pdf_backend,
            repo_root=repo_root,
        ),
        encoding="utf-8",
    )

    print(json_output)
    print(markdown_output)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(_main())
