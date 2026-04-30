#!/usr/bin/env python3
"""Walk a domain (or sitemap) to discover every PDF link, then pre-convert each.

Usage:
    python scripts/crawl.py https://your-site.example [--sitemap] [--max-pages 200]

By default does a depth-first crawl starting from the given URL, staying on
the same host. With --sitemap, parses sitemap.xml instead.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.prewarm import prewarm_one  # noqa: E402
from backend.app import cache  # noqa: E402
from backend.app.config import get_settings  # noqa: E402

HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        print(f"[skip] {url}: {e}")
        return None
    if "html" not in resp.headers.get("content-type", "").lower() and not url.endswith(".xml"):
        return None
    return resp.text


def _same_host(base: str, candidate: str) -> bool:
    return urlparse(base).netloc == urlparse(candidate).netloc


async def crawl_domain(start: str, max_pages: int) -> list[str]:
    seen: set[str] = set()
    queue: list[str] = [start]
    pdfs: set[str] = set()

    async with httpx.AsyncClient(headers={"user-agent": "access-remedy-crawler/0.1"}) as client:
        while queue and len(seen) < max_pages:
            url = queue.pop()
            if url in seen:
                continue
            seen.add(url)
            body = await _fetch_text(client, url)
            if not body:
                continue
            for m in HREF_RE.finditer(body):
                link = urljoin(url, m.group(1).split("#", 1)[0])
                if not link.startswith(("http://", "https://")):
                    continue
                if link.lower().endswith(".pdf"):
                    pdfs.add(link)
                elif _same_host(start, link) and link not in seen:
                    queue.append(link)
    return sorted(pdfs)


async def crawl_sitemap(sitemap_url: str) -> list[str]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(sitemap_url, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    root = ET.fromstring(resp.text)
    pdfs: list[str] = []
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    for loc in root.findall(".//sm:loc", ns):
        if loc.text and loc.text.lower().endswith(".pdf"):
            pdfs.append(loc.text)
    return pdfs


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("start_url")
    parser.add_argument("--sitemap", action="store_true",
                        help="treat start_url as a sitemap.xml rather than a page to crawl")
    parser.add_argument("--max-pages", type=int, default=200)
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_dirs()
    await cache.init_schema(settings.cache_db_path)

    print(f"[discover] {args.start_url}")
    if args.sitemap:
        pdfs = await crawl_sitemap(args.start_url)
    else:
        start = args.start_url if args.start_url.startswith("http") else f"https://{args.start_url}"
        pdfs = await crawl_domain(start, max_pages=args.max_pages)
    print(f"[discover] found {len(pdfs)} PDF(s)")

    for url in pdfs:
        try:
            await prewarm_one(url)
        except Exception as e:  # noqa: BLE001
            print(f"[error] {url}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
