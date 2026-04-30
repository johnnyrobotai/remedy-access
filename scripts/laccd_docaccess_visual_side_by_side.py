from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from PIL import Image, ImageChops, ImageDraw, ImageStat
from playwright.async_api import Page, async_playwright

from scripts.laccd_docaccess_parity import ARTIFACT_DIR, INVENTORY, slug_for

OUT_DIR = ARTIFACT_DIR / "visual-side-by-side"
DOCACCESS_DIR = OUT_DIR / "docaccess"
REMEDY_DIR = OUT_DIR / "remedy"
PAIR_DIR = OUT_DIR / "pairs"
DIFF_DIR = OUT_DIR / "diffs"

VIEWPORT = {"width": 1600, "height": 1100}


def docaccess_viewer_url(item: dict[str, str | int]) -> str:
    return (
        "https://docaccess.com/docviewer.html"
        f"?url={quote(str(item['url']), safe='')}"
        f"&url_hash={item['hash']}"
        "&domain=laccd.edu"
    )


def remedy_viewer_url(item: dict[str, str | int], *, base_url: str) -> str:
    src = f"{base_url.rstrip('/')}/demo/pdfs/{item['file']}"
    return f"{base_url.rstrip('/')}/viewer?src={quote(src, safe='')}"


async def wait_for_docaccess_transcript(page: Page) -> None:
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_selector('[role="switch"]', timeout=60_000)
    close_terms = page.locator('[aria-label="Close terms notice"]')
    if await close_terms.count():
        try:
            await close_terms.first.click(timeout=2_000)
        except Exception:
            pass
    switch = page.locator('[role="switch"]').first
    checked = await switch.get_attribute("aria-checked")
    if checked != "true":
        await switch.click()
    await page.wait_for_function(
        """
        () => {
          const sw = document.querySelector('[role="switch"]');
          const text = document.body?.innerText || "";
          return sw?.getAttribute('aria-checked') === 'true'
            && text.length > 1000;
        }
        """,
        timeout=60_000,
    )


async def wait_for_remedy_transcript(page: Page) -> None:
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_function(
        """
        () => {
          const progress = document.querySelector('.progress-overlay');
          const error = document.querySelector('[role="alert"]');
          const title = document.querySelector('.doc-title')?.textContent?.trim();
          return !progress && !error && Boolean(title);
        }
        """,
        timeout=180_000,
    )
    switch = page.locator('[role="switch"]').first
    checked = await switch.get_attribute("aria-checked")
    if checked != "true":
        await switch.click()
    await page.wait_for_function(
        """
        () => {
          const sw = document.querySelector('[role="switch"]');
          const transcript = document.querySelector('.pane.active .transcript-pane, section[aria-label*="transcript"] .transcript-pane');
          return sw?.getAttribute('aria-checked') === 'true'
            && transcript
            && (transcript.textContent || '').trim().length > 500;
        }
        """,
        timeout=60_000,
    )


async def capture(page: Page, url: str, out_path: Path, *, kind: str) -> None:
    await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
    if kind == "docaccess":
        await wait_for_docaccess_transcript(page)
    elif kind == "remedy":
        await wait_for_remedy_transcript(page)
    else:
        raise ValueError(f"unknown capture kind: {kind}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(out_path), full_page=False)


def image_rms(left_path: Path, right_path: Path, diff_path: Path) -> float:
    left = Image.open(left_path).convert("RGBA")
    right = Image.open(right_path).convert("RGBA")
    width = max(left.width, right.width)
    height = max(left.height, right.height)
    left_canvas = Image.new("RGBA", (width, height), "white")
    right_canvas = Image.new("RGBA", (width, height), "white")
    left_canvas.paste(left, (0, 0))
    right_canvas.paste(right, (0, 0))
    diff = ImageChops.difference(left_canvas, right_canvas)
    stat = ImageStat.Stat(diff)
    rms = max(stat.rms)
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    enhanced = diff.convert("RGB").point(lambda value: min(255, value * 4))
    enhanced.save(diff_path)
    return float(rms)


def pair_image(left_path: Path, right_path: Path, out_path: Path, *, title: str) -> None:
    left = Image.open(left_path).convert("RGB")
    right = Image.open(right_path).convert("RGB")
    width = max(left.width, right.width)
    height = max(left.height, right.height)
    header_h = 52
    gap = 20
    canvas = Image.new("RGB", (width * 2 + gap, height + header_h), "white")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, canvas.width, header_h), fill=(245, 246, 248))
    draw.text((12, 12), f"DocAccess: {title}", fill=(0, 0, 0))
    draw.text((width + gap + 12, 12), f"Remedy Access: {title}", fill=(0, 0, 0))
    canvas.paste(left, (0, header_h))
    canvas.paste(right, (width + gap, header_h))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)


def write_reports(rows: list[dict[str, Any]]) -> None:
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "viewport": VIEWPORT,
        "rows": rows,
    }
    (OUT_DIR / "visual-side-by-side-report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    md_lines = [
        "# LACCD DocAccess vs Remedy Visual Side-by-Side",
        "",
        f"Viewport: `{VIEWPORT['width']}x{VIEWPORT['height']}`",
        "",
        "| # | Label | Status | RMS Diff | Pair | Diff |",
        "|---:|---|---|---:|---|---|",
    ]
    html_cards: list[str] = []
    for row in rows:
        rms = row.get("rms")
        rms_text = f"{rms:.2f}" if isinstance(rms, (float, int)) and math.isfinite(rms) else ""
        pair = row.get("pair")
        diff = row.get("diff")
        md_lines.append(
            f"| {row['index']} | {row['label']} | {row['status']} | {rms_text} | "
            f"[pair]({pair}) | [diff]({diff}) |"
        )
        if pair:
            html_cards.append(
                "<article>"
                f"<h2>{row['index']}. {row['label']}</h2>"
                f"<p>Status: <strong>{row['status']}</strong>. RMS diff: <strong>{rms_text}</strong>.</p>"
                f"<p><a href='{pair}'>Open pair image</a> · <a href='{diff}'>Open diff image</a></p>"
                f"<img src='{pair}' alt='Side-by-side screenshot for {row['label']}'>"
                "</article>"
            )

    (OUT_DIR / "visual-side-by-side-report.md").write_text(
        "\n".join(md_lines) + "\n",
        encoding="utf-8",
    )
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>LACCD Visual Side-by-Side</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:24px;background:#f6f7f9;color:#111}"
        "article{background:white;border:1px solid #ccd1d8;border-radius:8px;padding:16px;margin:0 0 24px}"
        "img{max-width:100%;height:auto;border:1px solid #ccd1d8}"
        "a{color:#1358bf}</style></head><body>"
        "<h1>LACCD DocAccess vs Remedy Visual Side-by-Side</h1>"
        f"<p>Viewport: <code>{VIEWPORT['width']}x{VIEWPORT['height']}</code></p>"
        + "\n".join(html_cards)
        + "</body></html>"
    )
    (OUT_DIR / "visual-side-by-side-report.html").write_text(html, encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page(viewport=VIEWPORT)
        try:
            enabled = [item for item in INVENTORY if item["status"] == "enabled"]
            if args.limit:
                enabled = enabled[: args.limit]
            for item in enabled:
                slug = slug_for(item)
                docaccess_path = DOCACCESS_DIR / f"{slug}.png"
                remedy_path = REMEDY_DIR / f"{slug}.png"
                pair_path = PAIR_DIR / f"{slug}.png"
                diff_path = DIFF_DIR / f"{slug}.png"
                status = "ok"
                error = None
                try:
                    await capture(
                        page,
                        docaccess_viewer_url(item),
                        docaccess_path,
                        kind="docaccess",
                    )
                    await capture(
                        page,
                        remedy_viewer_url(item, base_url=args.base_url),
                        remedy_path,
                        kind="remedy",
                    )
                    rms = image_rms(docaccess_path, remedy_path, diff_path)
                    pair_image(
                        docaccess_path,
                        remedy_path,
                        pair_path,
                        title=str(item["label"]),
                    )
                except Exception as exc:  # noqa: BLE001
                    status = "error"
                    error = f"{type(exc).__name__}: {exc}"
                    rms = None
                rows.append(
                    {
                        "index": item["index"],
                        "label": item["label"],
                        "status": status,
                        "error": error,
                        "rms": rms,
                        "docaccess": str(docaccess_path.relative_to(OUT_DIR)) if docaccess_path.exists() else None,
                        "remedy": str(remedy_path.relative_to(OUT_DIR)) if remedy_path.exists() else None,
                        "pair": str(pair_path.relative_to(OUT_DIR)) if pair_path.exists() else None,
                        "diff": str(diff_path.relative_to(OUT_DIR)) if diff_path.exists() else None,
                    }
                )
                print(f"{item['index']:02d} {item['label']}: {status}" + (f" rms={rms:.2f}" if rms is not None else f" {error}"))
        finally:
            await browser.close()
    write_reports(rows)


if __name__ == "__main__":
    asyncio.run(main())
