from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from backend.app.a11y import check_html
from backend.app.config import get_settings
from backend.app.documents import DocFormat
from backend.app.images import extract_docx_image_assets, extract_pdf_image_assets
from backend.app.pdf_fetch import sha256_of
from backend.app.structured_render_v2 import run_structured_render_v2

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "data" / "artifacts" / "laccd-docaccess-parity"
DOCACCESS_DIR = ARTIFACT_DIR / "docaccess"
REMEDY_DIR = ARTIFACT_DIR / "remedy"
IMAGE_DIR = ARTIFACT_DIR / "images"
DEMO_DOC_DIR = ROOT / "demo" / "pdfs"

INVENTORY: list[dict[str, str | int]] = [
    {
        "index": 1,
        "label": "AB 540 California Nonresident Tuition Exemption Request",
        "format": "pdf",
        "file": "Nonresident_Tuition_Exemption_Request.pdf",
        "url": "https://www.laccd.edu/sites/laccd.edu/files/2022-08/Nonresident_Tuition_Exemption_Request.pdf",
        "hash": "235667b536722bd765835d95a960245b5add6e1afc5e21726fcd670564f171fb",
        "status": "enabled",
    },
    {
        "index": 2,
        "label": "Nonresident Tuition Fee Waiver (Word Version)",
        "format": "docx",
        "file": "Nonresident_Tuition_Fee_Waiver_Application.docx",
        "url": "https://www.laccd.edu/sites/laccd.edu/files/2024-08/Nonresident%20Tuition%20Fee%20Waiver%20Application.docx",
        "hash": "b12ab99fd53ffd5aa1c64a4b023c1f53b2f19a9d52c57872c59a6e624a87e947",
        "status": "archived",
    },
    {
        "index": 3,
        "label": "Nonresident Tuition Fee Waiver (PDF Version)",
        "format": "pdf",
        "file": "Nonresident_Tuition_Fee_Waiver_Application.pdf",
        "url": "https://www.laccd.edu/sites/laccd.edu/files/2024-08/Nonresident%20Tuition%20Fee%20Waiver%20Application.pdf",
        "hash": "edf23c36fbec6c30017e85fd884c4d0ea7fd5bf834cab2f6ac2830da1525d2db",
        "status": "enabled",
    },
    {
        "index": 4,
        "label": "Supplemental Residency Questionnaire",
        "format": "pdf",
        "file": "Supplemental_Residency_Questionnaire.pdf",
        "url": "https://www.laccd.edu/sites/laccd.edu/files/2022-08/Supplemental_Residency_Questionnaire.pdf",
        "hash": "0436174b1991865d298b8aad406cf4bf926339c3841281c71b58c52957d12d09",
        "status": "enabled",
    },
    {
        "index": 5,
        "label": "Certification of Homeless Status (Word Version)",
        "format": "docx",
        "file": "Certification_of_Homeless_Status_REV_012018.docx",
        "url": "https://www.laccd.edu/sites/laccd.edu/files/2022-08/Certification%20of%20Homeless%20Status%20REV%20012018.docx",
        "hash": "b454a32b344432988b3a26a2dfb21b701c2067f6b85153d8f36bc74e51aab727",
        "status": "archived",
    },
    {
        "index": 6,
        "label": "Certification of Homeless Status (PDF Version)",
        "format": "pdf",
        "file": "Certification_of_Homeless_Status_REV_012018.pdf",
        "url": "https://www.laccd.edu/sites/laccd.edu/files/2022-08/Certification%20of%20Homeless%20Status%20REV%20012018.pdf",
        "hash": "6f8f53846ddab01be62b0eaf6bb6382ca05b554872dc16ecf616e07ccb57863d",
        "status": "enabled",
    },
    {
        "index": 7,
        "label": "Application for Noncredit Admission",
        "format": "pdf",
        "file": "APPLICATION_FOR_NONCREDIT_ADMISSION_2.6_Fillable.pdf",
        "url": "https://www.laccd.edu/sites/laccd.edu/files/2022-08/APPLICATION%20FOR%20NONCREDIT%20ADMISSION%202.6%20Fillable.pdf",
        "hash": "12c77c450b7062432cb0ddf1221ec9ee3b4857fced81e8832588e489fc43994b",
        "status": "enabled",
    },
    {
        "index": 8,
        "label": "Application for Noncredit Admission (Spanish)",
        "format": "pdf",
        "file": "APPLICATION_FOR_NONCREDIT_ADMISSION_2.7_Fillable_SPANISH.pdf",
        "url": "https://www.laccd.edu/sites/laccd.edu/files/2022-08/APPLICATION%20FOR%20NONCREDIT%20ADMISSION%202.7%20Fillable%20SPANISH.pdf",
        "hash": "16d7ef4ff6a00da09f1182208a31f4d6b1ee226c7236ebbdd80ed7326b0e4674",
        "status": "enabled",
    },
    {
        "index": 9,
        "label": "Pass No Pass Petition (PDF version)",
        "format": "pdf",
        "file": "pass_no_pass_petition.pdf",
        "url": "https://www.laccd.edu/sites/laccd.edu/files/2023-10/pass_no_pass_petition.pdf",
        "hash": "5f187133dec5b14c2d2a013b7a063e8280c727856e04b7418baaf40d8774ae63",
        "status": "enabled",
    },
    {
        "index": 10,
        "label": "High School Graduation Update Form",
        "format": "pdf",
        "file": "High_School_Graduation_Update_Form.pdf",
        "url": "https://www.laccd.edu/sites/laccd.edu/files/2022-08/High%20School%20Graduation%20Update%20Form.pdf",
        "hash": "148eedce26730e91fa452182f404823080deee02935d8b0bc1de07aa835453a8",
        "status": "enabled",
    },
    {
        "index": 11,
        "label": "Excused Withdrawal (EW) Petition",
        "format": "pdf",
        "file": "laccd_ew_petition_240209_0.pdf",
        "url": "https://www.laccd.edu/sites/laccd.edu/files/2024-05/laccd_ew_petition_240209_0.pdf",
        "hash": "cb8bb02c03674142688f5e385d4b2c22ca958eb485818d5c22948a84d2ed4684",
        "status": "enabled",
    },
    {
        "index": 12,
        "label": "K-12 Parent Consent Form",
        "format": "pdf",
        "file": "K-12_Parent_Consent.pdf",
        "url": "https://www.laccd.edu/sites/laccd.edu/files/2026-02/K-12%20Parent%20Consent.pdf",
        "hash": "1049156b8c699fa89afa068a786667b21ae59e4d78eba1e8318e2ca4d228cb53",
        "status": "enabled",
    },
    {
        "index": 13,
        "label": "LACCD Petition for Academic Renewal.pdf",
        "format": "pdf",
        "file": "LACCD_Petition_for_Academic_Renewal_250505.pdf",
        "url": "https://www.laccd.edu/sites/laccd.edu/files/2025-09/LACCD%20Petition%20for%20Academic%20Renewal%20250505.pdf",
        "hash": "d38ed4ce0372ca7f33a590bc76f122978217671d1cfbc044982e163fa919e5a9",
        "status": "enabled",
    },
    {
        "index": 14,
        "label": "Petition for Credit for Prior Learning",
        "format": "pdf",
        "file": "Petition_for_Credit_for_Prior_Learning_v7.pdf",
        "url": "https://www.laccd.edu/sites/laccd.edu/files/2024-07/Petition%20for%20Credit%20for%20Prior%20Learning%20v7.pdf",
        "hash": "65a9c1af4b1d4baaf614e2fdf661548329dfa8cd9ee35b641ca53a8c3ad2b2ec",
        "status": "enabled",
    },
]


def slug_for(item: dict[str, str | int]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(item["label"]).lower()).strip("-")
    return f"{int(item['index']):02d}-{slug}"


def metrics(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    labels = [tag.get_text(" ", strip=True) for tag in soup.find_all("label")]
    legends = [tag.get_text(" ", strip=True) for tag in soup.find_all("legend")]
    headings = [
        tag.get_text(" ", strip=True)
        for tag in soup.find_all(re.compile(r"^h[1-6]$"))
    ]
    text = soup.get_text(" ", strip=True)
    generic_labels = [
        label
        for label in labels
        if re.search(r"\b(?:pg\d+[- ]\d+|check box[\w\\-]*|signature\d+|date\d+|text\d+|enter text|undefined)\b", label, re.IGNORECASE)
    ]
    bad_tokens = [
        token
        for token in ["PDF Form Fields", "Figure for", "Instructions table", "undefined"]
        if token.lower() in text.lower()
    ]
    return {
        "forms": len(soup.find_all("form")),
        "fieldsets": len(soup.find_all("fieldset")),
        "inputs": len(soup.find_all("input")),
        "textareas": len(soup.find_all("textarea")),
        "selects": len(soup.find_all("select")),
        "controls": len(soup.find_all(["input", "textarea", "select"])),
        "tables": len(soup.find_all("table")),
        "images": len(soup.find_all("img")),
        "figures": len(soup.find_all("figure")),
        "h1": len(soup.find_all("h1")),
        "headings": headings,
        "legends": legends,
        "labels": labels,
        "generic_labels": generic_labels,
        "bad_tokens": bad_tokens,
        "text_len": len(text),
    }


def load_docaccess(item: dict[str, str | int], *, fetch_missing: bool) -> tuple[dict[str, Any] | None, str | None]:
    slug = slug_for(item)
    html_path = DOCACCESS_DIR / f"{slug}.html"
    json_path = DOCACCESS_DIR / f"{slug}.json"
    if fetch_missing and (not html_path.exists() or not json_path.exists()):
        DOCACCESS_DIR.mkdir(parents=True, exist_ok=True)
        for suffix, path in (("transcript.html", html_path), ("document.json", json_path)):
            url = f"https://docaccess.com/docs/{item['hash']}/{suffix}"
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                with urlopen(request, timeout=30) as response:
                    path.write_bytes(response.read())
            except (HTTPError, URLError, TimeoutError) as exc:
                return None, f"{type(exc).__name__}: {exc}"
    if not html_path.exists():
        return None, "transcript artifact unavailable"
    html = html_path.read_text(encoding="utf-8")
    meta = json.loads(json_path.read_text(encoding="utf-8")) if json_path.exists() else {}
    return {"html": html, "meta": meta, "metrics": metrics(html)}, None


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    return value


async def render_remedy(
    item: dict[str, str | int],
    *,
    pdf_backend: str,
) -> tuple[dict[str, Any] | None, str | None]:
    path = DEMO_DOC_DIR / str(item["file"])
    if not path.exists():
        return None, f"missing local file: {path}"
    data = path.read_bytes()
    sha = sha256_of(data)
    fmt = DocFormat(str(item["format"]))
    image_assets = []
    image_dir = IMAGE_DIR / sha
    if fmt is DocFormat.PDF:
        image_assets = extract_pdf_image_assets(data, image_dir)
    elif fmt is DocFormat.DOCX:
        image_assets = extract_docx_image_assets(data, image_dir)
    output = await run_structured_render_v2(
        data,
        source_hint=str(item["url"]),
        fmt=fmt,
        image_filenames=image_assets,
        sha256=sha,
        pdf_backend=pdf_backend,
    )
    result = output["result"]
    html = result.html
    slug = slug_for(item)
    REMEDY_DIR.mkdir(parents=True, exist_ok=True)
    (REMEDY_DIR / f"{slug}.html").write_text(html, encoding="utf-8")
    (REMEDY_DIR / f"{slug}.parsed.json").write_text(
        json.dumps(jsonable(output.get("parsed_document")), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "html": html,
        "metrics": metrics(html),
        "title": result.title,
        "description": result.description,
        "sha256": sha,
        "a11y_issues": check_html(html),
    }, None


def normalized_title(title: str | None) -> str:
    if not title:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def compare(row: dict[str, Any]) -> list[str]:
    doc = row.get("docaccess")
    remedy = row.get("remedy")
    if doc is None:
        return [f"docaccess_transcript_unavailable_{row['status']}"]
    if remedy is None:
        return ["remedy_render_failed"]
    issues: list[str] = []
    dm = doc["metrics"]
    rm = remedy["metrics"]
    doc_title = doc.get("meta", {}).get("title") or (dm["headings"][0] if dm["headings"] else "")
    remedy_title = remedy.get("title") or (rm["headings"][0] if rm["headings"] else "")
    if normalized_title(doc_title) and normalized_title(remedy_title):
        dnorm = normalized_title(doc_title)
        rnorm = normalized_title(remedy_title)
        if dnorm not in rnorm and rnorm not in dnorm:
            issues.append("title_mismatch")
    if dm["controls"] and rm["controls"] < dm["controls"] - max(5, round(dm["controls"] * 0.15)):
        issues.append("missing_controls")
    if dm["controls"] and rm["controls"] > dm["controls"] + max(8, round(dm["controls"] * 0.25)):
        issues.append("too_many_controls")
    if dm["tables"] > rm["tables"]:
        issues.append("missing_tables")
    if dm["fieldsets"] and rm["fieldsets"] < max(1, round(dm["fieldsets"] * 0.6)):
        issues.append("missing_fieldsets")
    if rm["forms"] > max(1, dm["forms"]) + 1:
        issues.append("too_many_forms")
    if rm["generic_labels"]:
        issues.append("generic_field_labels")
    if rm["bad_tokens"]:
        issues.append("placeholder_or_generic_output")
    if remedy.get("a11y_issues"):
        issues.append("a11y_issues")
    return issues or ["ok"]


def write_markdown(rows: list[dict[str, Any]], *, pdf_backend: str) -> None:
    lines = [
        "# LACCD DocAccess parity report",
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
    (ARTIFACT_DIR / "parity-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch-docaccess", action="store_true")
    parser.add_argument(
        "--pdf-backend",
        choices=("native", "liteparse", "llamaparse"),
        default=None,
        help="PDF parser backend for Remedy renders; defaults to configured settings.",
    )
    args = parser.parse_args()

    os.environ.setdefault("LAYOUT_AGENT_ENABLED", "false")
    get_settings.cache_clear()
    settings = get_settings()
    pdf_backend = args.pdf_backend or settings.structured_render_v2_pdf_backend
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for item in INVENTORY:
        docaccess, docaccess_error = load_docaccess(item, fetch_missing=args.fetch_docaccess)
        remedy, remedy_error = await render_remedy(item, pdf_backend=pdf_backend)
        row = {
            "index": item["index"],
            "label": item["label"],
            "format": item["format"],
            "file": item["file"],
            "status": item["status"],
            "docaccess_hash": item["hash"],
            "docaccess_error": docaccess_error,
            "docaccess_meta": docaccess["meta"] if docaccess else None,
            "local_sha256": remedy["sha256"] if remedy else None,
            "sha_matches_docaccess": (
                remedy["sha256"] == docaccess["meta"].get("fileHash")
                if remedy and docaccess and docaccess.get("meta")
                else None
            ),
            "remedy_error": remedy_error,
            "remedy_title": remedy["title"] if remedy else None,
            "docaccess": docaccess,
            "remedy": remedy,
        }
        row["issues"] = compare(row)
        rows.append(row)
        print(f"{item['index']:02d} {item['label']}: {', '.join(row['issues'])}")

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pdf_backend": pdf_backend,
        "rows": rows,
    }
    (ARTIFACT_DIR / "parity-report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_markdown(rows, pdf_backend=pdf_backend)


if __name__ == "__main__":
    asyncio.run(main())
