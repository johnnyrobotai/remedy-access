"""Static file routes: viewer SPA, docbox.js embed, and the demo landing page.

The mounts are attached to the FastAPI app directly by main.py since
APIRouter.mount plays poorly with sub-paths. This module only exposes
constants and the docbox.js helper."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter()

ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = ROOT / "static"
DEMO_DIR = ROOT / "demo"
VIEWER_BUILD_DIR = ROOT / "viewer" / "dist"
EMBED_BUILD_DIR = ROOT / "embed" / "dist"
VIEWER_DIR = (
    VIEWER_BUILD_DIR
    if (VIEWER_BUILD_DIR / "index.html").exists()
    else STATIC_DIR / "viewer"
)
EMBED_DIR = (
    EMBED_BUILD_DIR
    if (EMBED_BUILD_DIR / "docbox.js").exists()
    else STATIC_DIR / "embed"
)


@router.get("/viewer")
@router.get("/viewer/")
async def viewer_index() -> HTMLResponse:
    index = VIEWER_DIR / "index.html"
    if not index.exists():
        raise HTTPException(
            503,
            "viewer not built — run `cd viewer && npm install && npm run build`",
        )
    return HTMLResponse(index.read_text(encoding="utf-8"))


@router.get("/docbox.js")
async def docbox_js() -> FileResponse:
    bundle = EMBED_DIR / "docbox.js"
    if not bundle.exists():
        raise HTTPException(
            503,
            "embed bundle not built — run `cd embed && npm install && npm run build`",
        )
    return FileResponse(
        bundle,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )
