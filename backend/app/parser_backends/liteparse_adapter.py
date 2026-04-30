"""LiteParse CLI adapter for the future parser-v2 spike.

This module intentionally shells out to the npm-installed ``lit`` CLI rather
than using any Python wrapper. The normalization step keeps only a small raw
representation:

- page number
- page width / height
- page text
- text items with position, font, and optional confidence metadata

Everything else in LiteParse's JSON output is treated as an implementation
detail for now.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence


class LiteParseError(RuntimeError):
    """Base class for LiteParse adapter failures."""


class LiteParseUnavailableError(LiteParseError):
    """Raised when the ``lit`` CLI is not installed or cannot be executed."""


class LiteParseExecutionError(LiteParseError):
    """Raised when the ``lit`` CLI exits unsuccessfully."""


class LiteParseOutputError(LiteParseError):
    """Raised when the ``lit`` CLI returns malformed or unexpected output."""


@dataclass(frozen=True, slots=True)
class LiteParseRawTextItem:
    text: str
    x: float
    y: float
    width: float
    height: float
    font_name: str | None = None
    font_size: float | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class LiteParseRawPage:
    page_num: int
    width: float
    height: float
    text: str
    text_items: tuple[LiteParseRawTextItem, ...]


@dataclass(frozen=True, slots=True)
class LiteParseRawDocument:
    pages: tuple[LiteParseRawPage, ...]


@dataclass(frozen=True, slots=True)
class LiteParseScreenshot:
    page_num: int | None
    path: Path


_SCREENSHOT_NAME_RE = re.compile(r"^page_(?P<page_num>\d+)\.(?:png|jpg)$")


def parse_pdf_with_liteparse(
    pdf_path: str | Path,
    *,
    cli_bin: str = "lit",
    target_pages: str | None = None,
    ocr_enabled: bool = True,
    password: str | None = None,
    timeout_s: float = 120.0,
) -> LiteParseRawDocument:
    """Run ``lit parse --format json`` and normalize the result."""
    command = [
        _resolve_cli(cli_bin),
        "parse",
        str(pdf_path),
        "--format",
        "json",
        "-q",
    ]
    if target_pages:
        command.extend(["--target-pages", target_pages])
    if not ocr_enabled:
        command.append("--no-ocr")
    if password:
        command.extend(["--password", password])

    proc = _run_cli(command, timeout_s=timeout_s)
    return normalize_liteparse_json(proc.stdout)


def screenshot_pdf_with_liteparse(
    pdf_path: str | Path,
    *,
    output_dir: str | Path,
    cli_bin: str = "lit",
    target_pages: str | None = None,
    dpi: int | None = None,
    image_format: Literal["png", "jpg"] = "png",
    password: str | None = None,
    timeout_s: float = 120.0,
) -> tuple[LiteParseScreenshot, ...]:
    """Run ``lit screenshot`` and return discovered output files."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    command = [
        _resolve_cli(cli_bin),
        "screenshot",
        str(pdf_path),
        "--output-dir",
        str(output_path),
        "--format",
        image_format,
        "-q",
    ]
    if target_pages:
        command.extend(["--target-pages", target_pages])
    if dpi is not None:
        command.extend(["--dpi", str(dpi)])
    if password:
        command.extend(["--password", password])

    _run_cli(command, timeout_s=timeout_s)

    suffix = f".{image_format}"
    screenshots = tuple(
        sorted(
            (
                LiteParseScreenshot(
                    page_num=_page_num_from_name(path.name),
                    path=path,
                )
                for path in output_path.iterdir()
                if path.is_file() and path.suffix.lower() == suffix
            ),
            key=lambda shot: (shot.page_num is None, shot.page_num or 0, shot.path.name),
        )
    )
    if not screenshots:
        raise LiteParseOutputError(
            f"LiteParse screenshot produced no {image_format!r} files in {output_path}"
        )
    return screenshots


def normalize_liteparse_json(payload: str | bytes | Mapping[str, Any]) -> LiteParseRawDocument:
    """Normalize LiteParse JSON into the small internal raw representation."""
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LiteParseOutputError("LiteParse CLI output was not valid UTF-8") from exc

    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise LiteParseOutputError(
                "LiteParse CLI returned invalid JSON "
                f"(line {exc.lineno}, column {exc.colno}): {exc.msg}"
            ) from exc
    else:
        parsed = payload

    root = _require_mapping(parsed, "root")
    raw_pages = root.get("pages")
    if not isinstance(raw_pages, list):
        raise LiteParseOutputError("invalid LiteParse JSON: root.pages must be a list")

    pages = tuple(_normalize_page(page, idx) for idx, page in enumerate(raw_pages))
    return LiteParseRawDocument(pages=pages)


def _normalize_page(raw_page: Any, index: int) -> LiteParseRawPage:
    label = f"pages[{index}]"
    page = _require_mapping(raw_page, label)
    raw_items = page.get("textItems")
    if not isinstance(raw_items, list):
        raise LiteParseOutputError(f"invalid LiteParse JSON: {label}.textItems must be a list")

    return LiteParseRawPage(
        page_num=_require_int(page.get("page"), f"{label}.page"),
        width=_require_float(page.get("width"), f"{label}.width"),
        height=_require_float(page.get("height"), f"{label}.height"),
        text=_require_str(page.get("text"), f"{label}.text"),
        text_items=tuple(
            _normalize_text_item(item, page_index=index, item_index=item_index)
            for item_index, item in enumerate(raw_items)
        ),
    )


def _normalize_text_item(raw_item: Any, *, page_index: int, item_index: int) -> LiteParseRawTextItem:
    label = f"pages[{page_index}].textItems[{item_index}]"
    item = _require_mapping(raw_item, label)
    return LiteParseRawTextItem(
        text=_require_str(item.get("text"), f"{label}.text"),
        x=_require_float(item.get("x"), f"{label}.x"),
        y=_require_float(item.get("y"), f"{label}.y"),
        width=_require_float(item.get("width"), f"{label}.width"),
        height=_require_float(item.get("height"), f"{label}.height"),
        font_name=_optional_str(item.get("fontName"), f"{label}.fontName"),
        font_size=_optional_float(item.get("fontSize"), f"{label}.fontSize"),
        confidence=_optional_float(item.get("confidence"), f"{label}.confidence"),
    )


def _resolve_cli(cli_bin: str) -> str:
    candidate = Path(cli_bin)
    if candidate.is_absolute() or candidate.parent != Path("."):
        if candidate.exists():
            return str(candidate)
    resolved = shutil.which(cli_bin)
    if resolved:
        return resolved
    raise LiteParseUnavailableError(
        "LiteParse CLI not found on PATH. Install @llamaindex/liteparse "
        "to use the optional lit backend."
    )


def _run_cli(command: Sequence[str], *, timeout_s: float) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LiteParseExecutionError(
            f"LiteParse CLI timed out after {timeout_s:g}s while running {command[1]!r}"
        ) from exc
    except OSError as exc:
        raise LiteParseUnavailableError(f"failed to execute LiteParse CLI: {exc}") from exc

    if proc.returncode != 0:
        detail = _first_line(proc.stderr) or _first_line(proc.stdout) or "no error output"
        raise LiteParseExecutionError(
            f"LiteParse CLI exited with status {proc.returncode}: {detail}"
        )
    return proc


def _page_num_from_name(name: str) -> int | None:
    match = _SCREENSHOT_NAME_RE.match(name)
    if not match:
        return None
    return int(match.group("page_num"))


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LiteParseOutputError(f"invalid LiteParse JSON: {label} must be an object")
    return value


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise LiteParseOutputError(f"invalid LiteParse JSON: {label} must be a string")
    return value


def _optional_str(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, label)


def _require_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LiteParseOutputError(f"invalid LiteParse JSON: {label} must be an integer")
    return value


def _require_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise LiteParseOutputError(f"invalid LiteParse JSON: {label} must be a number")
    return float(value)


def _optional_float(value: Any, label: str) -> float | None:
    if value is None:
        return None
    return _require_float(value, label)


def _first_line(value: str | None) -> str:
    if not value:
        return ""
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


__all__ = [
    "LiteParseError",
    "LiteParseExecutionError",
    "LiteParseOutputError",
    "LiteParseRawDocument",
    "LiteParseRawPage",
    "LiteParseRawTextItem",
    "LiteParseScreenshot",
    "LiteParseUnavailableError",
    "normalize_liteparse_json",
    "parse_pdf_with_liteparse",
    "screenshot_pdf_with_liteparse",
]
