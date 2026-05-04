from __future__ import annotations

import asyncio
import hashlib
import io
import os
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

from backend.app.config import get_settings
from backend.app.documents import DocFormat
from backend.app.structured_render_v2 import (
    _capture_layout_screenshot_sync,
    run_structured_render_v2,
)

_BASELINE_DIR = Path(__file__).parent / "fixtures" / "visual_regression"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_UPDATE_BASELINES = os.getenv("UPDATE_VISUAL_SNAPSHOTS") == "1"


def _demo_pdf(name: str) -> bytes:
    path = _REPO_ROOT / "demo" / "pdfs" / name
    if not path.exists():
        pytest.skip(f"demo PDF fixture not present: {name}; run scripts/download_samples.sh")
    return path.read_bytes()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _assert_matches_baseline(name: str, image_bytes: bytes, tmp_path: Path) -> None:
    baseline_path = _BASELINE_DIR / f"{name}.png"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    if _UPDATE_BASELINES or not baseline_path.exists():
        baseline_path.write_bytes(image_bytes)
        return

    expected = Image.open(baseline_path).convert("RGBA")
    actual = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    assert actual.size == expected.size

    diff = ImageChops.difference(expected, actual)
    rms = max(ImageStat.Stat(diff).rms)
    if rms > 2.0:
        actual_path = tmp_path / f"{name}.actual.png"
        diff_path = tmp_path / f"{name}.diff.png"
        actual.save(actual_path)
        diff.save(diff_path)
        pytest.fail(
            f"visual regression mismatch for {name}: rms={rms:.2f}; "
            f"wrote {actual_path} and {diff_path}"
        )


@pytest.mark.parametrize(
    ("doc_name", "baseline_name"),
    [
        ("Nonresident_Tuition_Fee_Waiver_Application.pdf", "laccd-nonresident-waiver-top"),
        ("K-12_Parent_Consent.pdf", "laccd-k12-parent-consent-top"),
        ("Petition_for_Credit_for_Prior_Learning_v7.pdf", "laccd-credit-prior-learning-top"),
    ],
)
def test_demo_documents_visual_regression(
    doc_name: str,
    baseline_name: str,
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LAYOUT_AGENT_ENABLED", "false")
    get_settings.cache_clear()
    settings = get_settings()
    settings.ensure_dirs()

    doc_bytes = _demo_pdf(doc_name)
    rendered = asyncio.run(
        run_structured_render_v2(
            doc_bytes,
            source_hint=doc_name,
            fmt=DocFormat.PDF,
            image_filenames=[],
            sha256=_sha256(doc_bytes),
            pdf_backend="native",
        )
    )["result"]

    screenshot = _capture_layout_screenshot_sync(rendered.html)
    if screenshot is None:
        pytest.skip("Playwright/browser not available for visual regression snapshot")

    _assert_matches_baseline(baseline_name, screenshot, tmp_path)
