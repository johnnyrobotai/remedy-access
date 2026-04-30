from __future__ import annotations

import io
import zipfile
from pathlib import Path

from backend.app.gemini.remediate import rewrite_image_srcs
from backend.app.images import ExtractedImageAsset, extract_docx_images

# 1x1 transparent PNG.
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
)

_CONTENT_TYPES_XML = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    b'<Default Extension="png" ContentType="image/png"/>'
    b'<Default Extension="xml" ContentType="application/xml"/>'
    b"</Types>"
)


def _build_docx(media: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        for name, data in media.items():
            zf.writestr(f"word/media/{name}", data)
    return buf.getvalue()


def test_rewrite_preserves_non_matching_src():
    html = '<img src="http://example.com/x.png" alt="x">'
    assert rewrite_image_srcs(html, "deadbeef", ["img-1.png"]) == html


def test_rewrite_substitutes_known_image_refs():
    html = '<img src="img-1" alt="logo"><img src="img-2.png" alt="chart">'
    out = rewrite_image_srcs(html, "deadbeef", ["img-1.png", "img-2.jpg"])
    assert 'src="/images/deadbeef/img-1.png"' in out
    assert 'src="/images/deadbeef/img-2.jpg"' in out


def test_rewrite_accepts_image_asset_objects():
    html = '<img src="img-1" alt="logo">'
    out = rewrite_image_srcs(
        html,
        "deadbeef",
        [ExtractedImageAsset(filename="img-1.png", page_number=1)],
    )
    assert 'src="/images/deadbeef/img-1.png"' in out


def test_rewrite_drops_unknown_image_refs():
    html = '<img src="img-7" alt="phantom">'
    out = rewrite_image_srcs(html, "deadbeef", ["img-1.png"])
    assert 'src=""' in out


def test_rewrite_noop_without_images():
    html = '<img src="img-1" alt="x">'
    assert rewrite_image_srcs(html, "deadbeef", []) == html


def test_extract_docx_images_writes_sequential_files(tmp_path: Path):
    docx = _build_docx({"image1.png": _PNG_BYTES})
    out_dir = tmp_path / "images"

    result = extract_docx_images(docx, out_dir)

    assert result == ["img-1.png"]
    assert (out_dir / "img-1.png").read_bytes() == _PNG_BYTES


def test_extract_docx_images_is_idempotent(tmp_path: Path):
    out_dir = tmp_path / "images"
    out_dir.mkdir()
    # Pre-populate with a file that looks like a prior extraction. Intentionally
    # use different bytes so we can prove the archive wasn't re-read.
    (out_dir / "img-1.png").write_bytes(b"prior-extract")

    docx = _build_docx({"image1.png": _PNG_BYTES})
    result = extract_docx_images(docx, out_dir)

    assert result == ["img-1.png"]
    assert (out_dir / "img-1.png").read_bytes() == b"prior-extract"


def test_extract_docx_images_empty_media_returns_empty(tmp_path: Path):
    docx = _build_docx({})
    out_dir = tmp_path / "images"

    assert extract_docx_images(docx, out_dir) == []


def test_extract_docx_images_skips_non_image_extensions(tmp_path: Path):
    docx = _build_docx(
        {
            "chart1.xml": b"<chart/>",
            "image2.png": _PNG_BYTES,
        }
    )
    out_dir = tmp_path / "images"

    result = extract_docx_images(docx, out_dir)

    assert result == ["img-1.png"]
    assert (out_dir / "img-1.png").read_bytes() == _PNG_BYTES
    # chart1.xml must not be copied under any name.
    assert not any(p.suffix == ".xml" for p in out_dir.iterdir())


def test_extract_docx_images_numbers_in_sorted_order(tmp_path: Path):
    docx = _build_docx(
        {
            "image2.jpeg": b"\xff\xd8\xff\xe0jpeg-2",
            "image1.png": _PNG_BYTES,
        }
    )
    out_dir = tmp_path / "images"

    result = extract_docx_images(docx, out_dir)

    # sorted(namelist()) puts image1.png before image2.jpeg, so PNG is img-1.
    assert result == ["img-1.png", "img-2.jpeg"]
    assert (out_dir / "img-1.png").read_bytes() == _PNG_BYTES
    assert (out_dir / "img-2.jpeg").read_bytes() == b"\xff\xd8\xff\xe0jpeg-2"
