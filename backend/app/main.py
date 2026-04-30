from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Deque

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.config import get_settings

log = logging.getLogger("access_remedy")
_rate_limit_buckets: dict[tuple[str, str], Deque[float]] = defaultdict(deque)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    from backend.app import cache

    await cache.init_schema(settings.cache_db_path)
    log.info("access-remedy ready; data_dir=%s", settings.data_dir)
    yield


app = FastAPI(title="Remedy Access", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)


def _client_ip(request: Request) -> str:
    settings = get_settings()
    if settings.trust_proxy_headers:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()
        real_ip = request.headers.get("x-real-ip", "")
        if real_ip:
            return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_for_path(path: str) -> tuple[str, int]:
    settings = get_settings()
    if path == "/api/transcript/ingest":
        return "ingest", settings.rate_limit_ingest_per_minute
    if path == "/api/translate":
        return "translate", settings.rate_limit_translate_per_minute
    if path == "/api/ask":
        return "ask", settings.rate_limit_ask_per_minute
    if path.startswith("/api/"):
        return "api", settings.rate_limit_api_per_minute
    return "public", 0


@app.middleware("http")
async def rate_limit_guard(request: Request, call_next):
    settings = get_settings()
    bucket_name, limit = _rate_limit_for_path(request.url.path)
    if not settings.rate_limit_enabled or limit <= 0:
        return await call_next(request)

    now = time.monotonic()
    window = float(settings.rate_limit_window_seconds)
    key = (_client_ip(request), bucket_name)
    bucket = _rate_limit_buckets[key]
    while bucket and now - bucket[0] >= window:
        bucket.popleft()

    if len(bucket) >= limit:
        retry_after = max(1, int(window - (now - bucket[0]))) if bucket else int(window)
        return JSONResponse(
            {"error": "rate_limited", "retry_after": retry_after},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    bucket.append(now)
    if len(_rate_limit_buckets) > 10_000:
        empty_keys = [item_key for item_key, item in _rate_limit_buckets.items() if not item]
        for item_key in empty_keys[:1000]:
            _rate_limit_buckets.pop(item_key, None)

    return await call_next(request)


@app.middleware("http")
async def api_key_guard(request: Request, call_next):
    settings = get_settings()
    if settings.app_api_key and request.url.path.startswith("/api/"):
        provided = request.headers.get("x-api-key", "")
        if provided != settings.app_api_key:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.get("/healthz")
async def healthz():
    settings = get_settings()
    return {
        "ok": True,
        "livekit_configured": settings.livekit_configured,
        "gemini_configured": bool(settings.google_api_key),
    }


# Routes are wired in after they're built (Step 5+).
try:
    from backend.app.routes import transcript as transcript_routes
    app.include_router(transcript_routes.router, prefix="/api")
except ImportError:
    pass

try:
    from backend.app.routes import ask as ask_routes
    app.include_router(ask_routes.router, prefix="/api")
except ImportError:
    pass

try:
    from backend.app.routes import live as live_routes
    app.include_router(live_routes.router, prefix="/api")
except ImportError:
    pass

try:
    from backend.app.routes import translate as translate_routes
    app.include_router(translate_routes.router, prefix="/api")
except ImportError:
    pass

try:
    from fastapi.staticfiles import StaticFiles

    from backend.app.routes import static_sites

    settings = get_settings()
    settings.ensure_dirs()
    app.include_router(static_sites.router)
    app.mount(
        "/images",
        StaticFiles(directory=settings.images_dir),
        name="images",
    )
    if static_sites.VIEWER_DIR.exists() and (static_sites.VIEWER_DIR / "assets").exists():
        app.mount(
            "/viewer/assets",
            StaticFiles(directory=static_sites.VIEWER_DIR / "assets"),
            name="viewer-assets",
        )
    if static_sites.DEMO_DIR.exists():
        app.mount(
            "/demo",
            StaticFiles(directory=static_sites.DEMO_DIR, html=True),
            name="demo",
        )
except ImportError:
    pass


@app.exception_handler(HTTPException)
async def http_exception_handler(_request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
