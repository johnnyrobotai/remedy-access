from __future__ import annotations

import json
from pathlib import Path

from scripts.layout_agent_eval import LayoutAgentReportEntry, _build_markdown_report, _score_result


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_score_result_prefers_changed_over_retry_errors(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "layout-agent"
    artifact_dir.mkdir()
    _write_json(
        artifact_dir / "initial_layout_plan.json",
        {
            "hero_treatment": "compact",
            "density": "comfortable",
            "toc_policy": "auto",
            "sections": [{"section_id": "intro", "blocks": [{"block_index": 0, "placement": "main"}]}],
        },
    )
    _write_json(
        artifact_dir / "revised_layout_plan.json",
        {
            "hero_treatment": "summary",
            "density": "comfortable",
            "toc_policy": "auto",
            "sections": [{"section_id": "intro", "blocks": [{"block_index": 0, "placement": "main"}]}],
        },
    )
    _write_json(artifact_dir / "critique.json", {"summary": "Promote the hero."})
    _write_json(
        artifact_dir / "critique_attempts.json",
        [
            {"error": "timed out"},
            {"error": None, "raw_content": '{"summary":"Promote the hero."}'},
        ],
    )
    _write_json(artifact_dir / "skip_reason.json", {"skip_reason": None})

    score, detail = _score_result(artifact_dir)

    assert score == "changed"
    assert detail["changed_fields"] == ["hero_treatment: compact -> summary"]
    assert detail["error_count"] == 1


def test_score_result_marks_trivial_layout_skip_without_attempts(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "layout-agent"
    artifact_dir.mkdir()
    _write_json(artifact_dir / "initial_layout_plan.json", {"sections": []})
    _write_json(artifact_dir / "revised_layout_plan.json", {"sections": []})
    _write_json(artifact_dir / "critique.json", {})
    _write_json(artifact_dir / "critique_attempts.json", [])
    _write_json(
        artifact_dir / "skip_reason.json",
        {"skip_reason": "layout plan has fewer than two blocks across sections"},
    )

    score, detail = _score_result(artifact_dir)

    assert score == "skipped"
    assert detail["attempt_count"] == 0
    assert detail["skip_reason"] == "layout plan has fewer than two blocks across sections"


def test_build_markdown_report_includes_counts_and_changes(tmp_path: Path) -> None:
    report = _build_markdown_report(
        [
            LayoutAgentReportEntry(
                name="sample-tables.pdf",
                sha256="abc",
                title="Sample Tables",
                score="changed",
                artifact_dir="data/artifacts/abc/layout-agent",
                before="data/artifacts/abc/layout-agent/before.png",
                after="data/artifacts/abc/layout-agent/after.png",
                critique_summary="Increase whitespace.",
                skip_reason=None,
                attempt_count=1,
                error_count=0,
                attempt_errors=[],
                changed_fields=["density: dense -> comfortable"],
                missing_files=[],
            )
        ],
        generated_at="2026-04-22T18:00:00+00:00",
        reran_docs=True,
        pdf_backend="native",
        repo_root=tmp_path,
    )

    assert "# Layout Agent Eval Report" in report
    assert "`changed`: 1" in report
    assert "| `sample-tables.pdf` | `changed` | 1 | 0 | density: dense -> comfortable | - |" in report
