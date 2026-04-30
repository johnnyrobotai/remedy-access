"""PDF / DOCX image extraction.

We extract visual assets from ingested documents and save them to
`{data_dir}/images/{sha256}/img-{N}{ext}` in document order. The viewer serves
these via a static mount at `/images/{sha256}/...`.

For PDFs we extract embedded raster images and, when enabled, also rasterize
vector-heavy pages that appear to contain figures. Gemini receives the ordered
asset list in the remediation prompt and is told to reference them as
`<img src="img-N" alt="...">`. A post-processing step in
`gemini/remediate.py` rewrites those relative src values into the served URL.

Kept separate from `pdf_fetch.py` because this is a disk-side side-effect of
ingest, not part of the untrusted network fetch.
"""
from __future__ import annotations

import json
import io
import logging
import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_ALLOWED_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp"})
_ASSET_MANIFEST = "manifest.json"
_FIGURE_CUE_RE = re.compile(
    r"\b(?:fig(?:ure)?\.?\s*\d+|chart\s*\d+|diagram|illustration|plate\s*\d+|map\s*\d+)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractedImageAsset:
    filename: str
    page_number: int | None = None
    kind: str = "embedded"


ImageAssetLike = str | ExtractedImageAsset


@dataclass(frozen=True)
class _PendingAsset:
    data: bytes
    ext: str
    page_number: int | None
    kind: str
    order: int


def normalize_image_assets(image_assets: Sequence[ImageAssetLike] | None) -> list[ExtractedImageAsset]:
    if not image_assets:
        return []
    normalized: list[ExtractedImageAsset] = []
    for asset in image_assets:
        if isinstance(asset, ExtractedImageAsset):
            normalized.append(asset)
        else:
            normalized.append(ExtractedImageAsset(filename=asset))
    return normalized


def image_asset_filenames(image_assets: Sequence[ImageAssetLike] | None) -> list[str]:
    return [asset.filename for asset in normalize_image_assets(image_assets)]


def filter_image_assets_for_page_range(
    image_assets: Sequence[ImageAssetLike] | None,
    start_page: int,
    end_page: int,
) -> list[ExtractedImageAsset]:
    assets = normalize_image_assets(image_assets)
    return [
        asset
        for asset in assets
        if asset.page_number is None or start_page <= asset.page_number <= end_page
    ]


def filter_image_assets_for_stems(
    image_assets: Sequence[ImageAssetLike] | None,
    stems: Iterable[str],
) -> list[ExtractedImageAsset]:
    stem_set = {stem for stem in stems if stem}
    if not stem_set:
        return normalize_image_assets(image_assets)
    assets = normalize_image_assets(image_assets)
    return [asset for asset in assets if Path(asset.filename).stem in stem_set]


def describe_image_assets_for_prompt(image_assets: Sequence[ImageAssetLike] | None) -> str:
    assets = normalize_image_assets(image_assets)
    if not assets:
        return ""
    lines: list[str] = []
    for asset in assets:
        stem = Path(asset.filename).stem
        details: list[str] = []
        if asset.page_number is not None:
            details.append(f"page {asset.page_number}")
        if asset.kind == "page_raster":
            details.append("full-page raster added to preserve vector-drawn artwork")
        else:
            details.append("embedded image")
        lines.append(f"- {stem} ({'; '.join(details)})")
    return "\n".join(lines)


def extract_pdf_image_assets(
    pdf_bytes: bytes,
    out_dir: Path,
    *,
    include_vector_pages: bool = False,
    vector_min_ops: int = 25,
    vector_dpi: int = 110,
) -> list[ExtractedImageAsset]:
    """Extract PDF visual assets in document order.

    Embedded raster images are always considered. When `include_vector_pages`
    is true, vector-heavy pages that look like figures are also rasterized and
    added as `kind="page_raster"` assets so Gemini can describe diagrams that
    were never embedded as raster XObjects.
    """
    cached_assets, has_manifest = _load_cached_assets(out_dir)
    if cached_assets is not None and (has_manifest or not include_vector_pages):
        return cached_assets
    if cached_assets is not None and include_vector_pages and not has_manifest:
        # Pre-manifest caches contain filenames only, so we rebuild once to
        # recover page metadata and optionally add vector-derived assets.
        _clear_generated_assets(out_dir)

    pending: list[_PendingAsset] = []

    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning("pypdf not available; embedded PDF images will not be extracted")
    else:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                page_images = list(page.images)
            except Exception as e:  # noqa: BLE001
                log.warning("failed to list images for PDF page %d: %s", page_number, e)
                continue
            for img in page_images:
                ext = Path(img.name).suffix.lower() if img.name else ""
                if ext not in _ALLOWED_EXTS:
                    ext = ".png"
                pending.append(
                    _PendingAsset(
                        data=img.data,
                        ext=ext,
                        page_number=page_number,
                        kind="embedded",
                        order=len(pending),
                    )
                )

    if include_vector_pages:
        pending.extend(
            _extract_pdf_vector_page_assets(
                pdf_bytes,
                min_ops=vector_min_ops,
                dpi=vector_dpi,
                starting_order=len(pending),
            )
        )

    return _write_pending_assets(out_dir, pending)


def extract_pdf_images(pdf_bytes: bytes, out_dir: Path) -> list[str]:
    """Back-compat helper: return only embedded PDF image filenames."""
    assets = extract_pdf_image_assets(pdf_bytes, out_dir, include_vector_pages=False)
    return [asset.filename for asset in assets if asset.kind == "embedded"]


def extract_docx_image_assets(docx_bytes: bytes, out_dir: Path) -> list[ExtractedImageAsset]:
    cached_assets, _ = _load_cached_assets(out_dir)
    if cached_assets is not None:
        return cached_assets

    try:
        zf = zipfile.ZipFile(io.BytesIO(docx_bytes))
    except zipfile.BadZipFile as e:
        log.warning("docx is not a valid zip archive: %s", e)
        return []

    pending: list[_PendingAsset] = []
    with zf:
        for name in sorted(zf.namelist()):
            if not name.startswith("word/media/"):
                continue
            ext = Path(name).suffix.lower()
            if ext not in _ALLOWED_EXTS:
                continue
            try:
                data = zf.read(name)
            except KeyError:  # pragma: no cover - defensive
                continue
            pending.append(
                _PendingAsset(
                    data=data,
                    ext=ext,
                    page_number=None,
                    kind="embedded",
                    order=len(pending),
                )
            )

    return _write_pending_assets(out_dir, pending)


def extract_docx_images(docx_bytes: bytes, out_dir: Path) -> list[str]:
    """Back-compat helper: return DOCX image filenames only."""
    return image_asset_filenames(extract_docx_image_assets(docx_bytes, out_dir))


def _extract_pdf_vector_page_assets(
    pdf_bytes: bytes,
    *,
    min_ops: int,
    dpi: int,
    starting_order: int,
) -> list[_PendingAsset]:
    try:
        import fitz
    except ImportError:
        log.warning("PyMuPDF not available; vector PDF pages will not be rasterized")
        return []

    pending: list[_PendingAsset] = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page_number, page in enumerate(doc, start=1):
            try:
                drawings = page.get_drawings()
            except Exception as e:  # noqa: BLE001
                log.warning("failed to inspect vector drawings for PDF page %d: %s", page_number, e)
                continue
            if not drawings:
                continue
            op_count = _drawing_op_count(drawings)
            if op_count < min_ops:
                continue
            page_text = page.get_text("text")
            if _looks_like_table_grid(drawings, page_text=page_text):
                continue
            word_count = len(page.get_text("words"))
            if not _looks_like_vector_figure(
                drawings,
                page_text=page_text,
                word_count=word_count,
                page_rect=page.rect,
                min_ops=min_ops,
            ):
                continue
            try:
                pix = page.get_pixmap(dpi=dpi, alpha=False)
            except Exception as e:  # noqa: BLE001
                log.warning("failed to rasterize vector PDF page %d: %s", page_number, e)
                continue
            pending.append(
                _PendingAsset(
                    data=pix.tobytes("png"),
                    ext=".png",
                    page_number=page_number,
                    kind="page_raster",
                    order=starting_order + len(pending),
                )
            )
    finally:
        doc.close()

    return pending


def _looks_like_vector_figure(
    drawings: Sequence[dict],
    *,
    page_text: str,
    word_count: int,
    page_rect,
    min_ops: int,
) -> bool:
    if _FIGURE_CUE_RE.search(page_text):
        return True

    op_count = _drawing_op_count(drawings)
    non_axis_lines = 0
    filled_shapes = 0
    for drawing in drawings:
        if drawing.get("fill") is not None:
            filled_shapes += 1
        for item in drawing.get("items", ()):
            if item and item[0] == "l" and not _line_is_axis_aligned(item[1], item[2]):
                non_axis_lines += 1

    coverage = _drawing_coverage_ratio(drawings, page_rect)
    if non_axis_lines >= 2 or filled_shapes >= 2:
        return True
    if word_count <= 80 and coverage >= 0.10:
        return True
    return coverage >= 0.20 and op_count >= max(min_ops * 2, min_ops + 10)


def _looks_like_table_grid(drawings: Sequence[dict], *, page_text: str) -> bool:
    if _FIGURE_CUE_RE.search(page_text):
        return False

    axis_aligned_lines = 0
    non_axis_lines = 0
    rectangles = 0
    curves = 0

    for drawing in drawings:
        for item in drawing.get("items", ()):
            if not item:
                continue
            op = item[0]
            if op == "l":
                if _line_is_axis_aligned(item[1], item[2]):
                    axis_aligned_lines += 1
                else:
                    non_axis_lines += 1
            elif op == "re":
                rectangles += 1
            else:
                curves += 1

    grid_ops = axis_aligned_lines + rectangles
    if curves or non_axis_lines:
        return False
    if grid_ops < 12:
        return False
    return rectangles >= 4 or axis_aligned_lines >= 12


def _drawing_op_count(drawings: Sequence[dict]) -> int:
    return sum(len(drawing.get("items", ())) or 1 for drawing in drawings)


def _drawing_coverage_ratio(drawings: Sequence[dict], page_rect) -> float:
    page_area = max(float(page_rect.width * page_rect.height), 1.0)
    covered_area = 0.0
    for drawing in drawings:
        rect = drawing.get("rect")
        if rect is None:
            continue
        covered_area += max(float(rect.width * rect.height), 0.0)
    return min(covered_area / page_area, 1.0)


def _line_is_axis_aligned(p1, p2) -> bool:
    return round(float(p1.x), 3) == round(float(p2.x), 3) or round(float(p1.y), 3) == round(
        float(p2.y), 3
    )


def _write_pending_assets(out_dir: Path, pending: Sequence[_PendingAsset]) -> list[ExtractedImageAsset]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        pending,
        key=lambda asset: (
            asset.page_number if asset.page_number is not None else 10_000,
            0 if asset.kind == "embedded" else 1,
            asset.order,
        ),
    )
    assets: list[ExtractedImageAsset] = []
    for counter, item in enumerate(ordered, start=1):
        fname = f"img-{counter}{item.ext}"
        (out_dir / fname).write_bytes(item.data)
        assets.append(
            ExtractedImageAsset(
                filename=fname,
                page_number=item.page_number,
                kind=item.kind,
            )
        )
    _write_asset_manifest(out_dir, assets)
    return assets


def _write_asset_manifest(out_dir: Path, assets: Sequence[ExtractedImageAsset]) -> None:
    manifest_path = out_dir / _ASSET_MANIFEST
    payload = {"assets": [asdict(asset) for asset in assets]}
    manifest_path.write_text(json.dumps(payload, indent=2))


def _load_cached_assets(out_dir: Path) -> tuple[list[ExtractedImageAsset] | None, bool]:
    manifest_path = out_dir / _ASSET_MANIFEST
    if manifest_path.exists():
        try:
            payload = json.loads(manifest_path.read_text())
        except Exception as e:  # noqa: BLE001
            log.warning("failed to parse image asset manifest %s: %s", manifest_path, e)
        else:
            raw_assets = payload.get("assets", [])
            assets: list[ExtractedImageAsset] = []
            for raw in raw_assets:
                filename = raw.get("filename")
                if not filename:
                    continue
                page_number = raw.get("page_number")
                if not isinstance(page_number, int):
                    page_number = None
                kind = raw.get("kind") or "embedded"
                assets.append(
                    ExtractedImageAsset(
                        filename=filename,
                        page_number=page_number,
                        kind=kind,
                    )
                )
            return assets, True

    if out_dir.exists():
        existing = sorted(
            (p.name for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() in _ALLOWED_EXTS),
            key=_img_sort_key,
        )
        if existing:
            return [ExtractedImageAsset(filename=name) for name in existing], False

    return None, False


def _clear_generated_assets(out_dir: Path) -> None:
    if not out_dir.exists():
        return
    for path in out_dir.iterdir():
        if path.is_file() and (
            path.name == _ASSET_MANIFEST or path.suffix.lower() in _ALLOWED_EXTS
        ):
            path.unlink()


def _img_sort_key(name: str) -> tuple[int, str]:
    stem = Path(name).stem
    if stem.startswith("img-"):
        try:
            return (int(stem[4:]), name)
        except ValueError:
            pass
    return (10_000, name)
