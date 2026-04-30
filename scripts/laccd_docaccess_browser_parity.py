from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

import websocket

from backend.app.config import get_settings
from scripts.laccd_docaccess_parity import (
    ARTIFACT_DIR,
    INVENTORY,
    compare,
    render_remedy,
    slug_for,
)

BROWSER_DIR = ARTIFACT_DIR / "browser-cdp"
DOCACCESS_BROWSER_DIR = BROWSER_DIR / "docaccess"
REMEDY_BROWSER_DIR = BROWSER_DIR / "remedy"

METRICS_JS = r"""
(() => {
  const textOf = (el) => (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
  const labels = [...document.querySelectorAll("label")].map(textOf).filter(Boolean);
  const legends = [...document.querySelectorAll("legend")].map(textOf).filter(Boolean);
  const headings = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6")].map(textOf).filter(Boolean);
  const text = textOf(document.body || document.documentElement);
  const genericLabels = labels.filter((label) =>
    /\b(?:pg\d+[- ]\d+|check box[\w\\-]*|signature\d+|date\d+|text\d+|enter text|undefined)\b/i.test(label)
  );
  const badTokens = ["PDF Form Fields", "Figure for", "Instructions table", "undefined"]
    .filter((token) => text.toLowerCase().includes(token.toLowerCase()));
  return {
    forms: document.querySelectorAll("form").length,
    fieldsets: document.querySelectorAll("fieldset").length,
    inputs: document.querySelectorAll("input").length,
    textareas: document.querySelectorAll("textarea").length,
    selects: document.querySelectorAll("select").length,
    controls: document.querySelectorAll("input,textarea,select").length,
    tables: document.querySelectorAll("table").length,
    images: document.querySelectorAll("img").length,
    figures: document.querySelectorAll("figure").length,
    h1: document.querySelectorAll("h1").length,
    headings,
    legends,
    labels,
    generic_labels: genericLabels,
    bad_tokens: badTokens,
    text_len: text.length,
    url: location.href,
    title: document.title || "",
    body_sample: text.slice(0, 500),
  };
})()
"""


class CDPPage:
    def __init__(self, endpoint: str, page_url: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.page_url = page_url
        self.ws: websocket.WebSocket | None = None
        self._message_id = 0

    @classmethod
    def new(cls, endpoint: str, url: str = "about:blank") -> "CDPPage":
        endpoint = endpoint.rstrip("/")
        request = Request(f"{endpoint}/json/new?{quote(url, safe=':/?&=%')}", method="PUT")
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        page = cls(endpoint, payload["webSocketDebuggerUrl"])
        page.connect()
        page.call("Runtime.enable")
        page.call("Page.enable")
        return page

    def connect(self) -> None:
        self.ws = websocket.create_connection(self.page_url, timeout=20, suppress_origin=True)

    def close(self) -> None:
        try:
            target_id = self.call("Target.getTargetInfo")["targetInfo"]["targetId"]
            with urlopen(Request(f"{self.endpoint}/json/close/{target_id}", method="PUT"), timeout=10):
                pass
        except Exception:
            pass
        if self.ws is not None:
            self.ws.close()

    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout_s: float = 20) -> dict[str, Any]:
        if self.ws is None:
            raise RuntimeError("CDP page is not connected")
        self._message_id += 1
        message_id = self._message_id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"timed out waiting for CDP response to {method}")
            self.ws.settimeout(remaining)
            raw = self.ws.recv()
            payload = json.loads(raw)
            if payload.get("id") != message_id:
                continue
            if "error" in payload:
                raise RuntimeError(f"CDP {method} failed: {payload['error']}")
            return payload.get("result", {})

    def navigate(self, url: str) -> None:
        self.call("Page.navigate", {"url": url}, timeout_s=30)
        self.wait_ready()

    def set_content(self, html: str) -> None:
        self.navigate("about:blank")
        frame_id = self.call("Page.getFrameTree")["frameTree"]["frame"]["id"]
        self.call(
            "Page.setDocumentContent",
            {
                "frameId": frame_id,
                "html": "<!doctype html><html><head><meta charset='utf-8'></head><body>"
                + html
                + "</body></html>",
            },
            timeout_s=30,
        )
        self.wait_ready()

    def wait_ready(self, timeout_s: float = 30) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            state = self.evaluate("document.readyState")
            if state in {"interactive", "complete"}:
                return
            time.sleep(0.1)
        raise TimeoutError("browser page did not become ready")

    def evaluate(self, expression: str) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout_s=30,
        )
        remote = result.get("result", {})
        if "value" in remote:
            return remote["value"]
        return None

    def screenshot(self, out_path: Path) -> None:
        self.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": 1440,
                "height": 1100,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )
        result = self.call(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": True, "fromSurface": True},
            timeout_s=30,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(result["data"]))


def docaccess_url(item: dict[str, str | int]) -> str:
    return f"https://docaccess.com/docs/{item['hash']}/transcript.html"


def write_browser_markdown(rows: list[dict[str, Any]], *, pdf_backend: str) -> None:
    lines = [
        "# LACCD DocAccess browser parity report",
        "",
        f"PDF backend: `{pdf_backend}`",
        "",
        "| # | Label | Status | DocAccess controls/forms/fieldsets/tables/images | Remedy controls/forms/fieldsets/tables/images | Issues |",
        "|---:|---|---|---:|---:|---|",
    ]
    for row in rows:
        doc = row.get("docaccess")
        remedy = row.get("remedy")
        dm = doc["metrics"] if doc else None
        rm = remedy["metrics"] if remedy else None
        doc_counts = (
            f"{dm['controls']}/{dm['forms']}/{dm['fieldsets']}/{dm['tables']}/{dm['images']}"
            if dm
            else "unavailable"
        )
        remedy_counts = (
            f"{rm['controls']}/{rm['forms']}/{rm['fieldsets']}/{rm['tables']}/{rm['images']}"
            if rm
            else "failed"
        )
        lines.append(
            f"| {row['index']} | {row['label']} | {row['status']} | {doc_counts} | {remedy_counts} | {', '.join(row['issues'])} |"
        )
    (BROWSER_DIR / "browser-parity-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp", default="http://127.0.0.1:53543")
    parser.add_argument(
        "--pdf-backend",
        choices=("native", "liteparse", "llamaparse"),
        default=None,
        help="PDF parser backend for Remedy renders; defaults to configured settings.",
    )
    args = parser.parse_args()

    get_settings.cache_clear()
    settings = get_settings()
    pdf_backend = args.pdf_backend or settings.structured_render_v2_pdf_backend
    BROWSER_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    page = CDPPage.new(args.cdp)
    try:
        for item in INVENTORY:
            slug = slug_for(item)
            remedy, remedy_error = await render_remedy(item, pdf_backend=pdf_backend)
            docaccess_payload: dict[str, Any] | None = None
            docaccess_error: str | None = None

            if item["status"] == "enabled":
                try:
                    page.navigate(docaccess_url(item))
                    doc_metrics = page.evaluate(METRICS_JS)
                    page.screenshot(DOCACCESS_BROWSER_DIR / f"{slug}.png")
                    docaccess_payload = {"metrics": doc_metrics, "meta": {}}
                except Exception as exc:  # noqa: BLE001
                    docaccess_error = f"{type(exc).__name__}: {exc}"
            else:
                docaccess_error = "archived on DocAccess source page"

            remedy_payload: dict[str, Any] | None = None
            if remedy is not None:
                try:
                    page.set_content(remedy["html"])
                    remedy_metrics = page.evaluate(METRICS_JS)
                    page.screenshot(REMEDY_BROWSER_DIR / f"{slug}.png")
                    remedy_payload = {
                        "metrics": remedy_metrics,
                        "title": remedy["title"],
                        "sha256": remedy["sha256"],
                        "a11y_issues": remedy.get("a11y_issues", []),
                    }
                except Exception as exc:  # noqa: BLE001
                    remedy_error = f"{type(exc).__name__}: {exc}"

            row = {
                "index": item["index"],
                "label": item["label"],
                "format": item["format"],
                "file": item["file"],
                "status": item["status"],
                "docaccess_hash": item["hash"],
                "docaccess_error": docaccess_error,
                "docaccess": docaccess_payload,
                "remedy_error": remedy_error,
                "remedy_title": remedy["title"] if remedy else None,
                "remedy": remedy_payload,
            }
            row["issues"] = compare(row)
            rows.append(row)
            print(f"{item['index']:02d} {item['label']}: {', '.join(row['issues'])}")
    finally:
        page.close()

    payload = {
        "cdp_endpoint": args.cdp,
        "pdf_backend": pdf_backend,
        "rows": rows,
    }
    (BROWSER_DIR / "browser-parity-report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_browser_markdown(rows, pdf_backend=pdf_backend)


if __name__ == "__main__":
    asyncio.run(main())
