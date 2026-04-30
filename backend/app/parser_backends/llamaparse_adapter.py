"""LlamaParse adapter for structured-render-v2 PDF extraction.

The rest of the app deals only with normalized dataclasses from this module.
The Llama Cloud SDK import stays lazy so normal local development does not
require the optional service or an API key.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Literal, Protocol


class LlamaParseError(RuntimeError):
    """Base class for LlamaParse adapter failures."""


class LlamaParseUnavailableError(LlamaParseError):
    """Raised when the SDK or API credentials are unavailable."""


class LlamaParseExecutionError(LlamaParseError):
    """Raised when LlamaParse rejects or cannot complete a parse job."""


class LlamaParseOutputError(LlamaParseError):
    """Raised when LlamaParse returns malformed or unusable output."""


@dataclass(frozen=True, slots=True)
class LlamaParsePage:
    page_number: int
    markdown: str
    text: str | None
    items: tuple[dict[str, Any], ...]
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LlamaParseDocument:
    pages: tuple[LlamaParsePage, ...]
    markdown_full: str | None
    text_full: str | None
    job_metadata: dict[str, Any]
    raw: dict[str, Any]


class _LlamaParseClient(Protocol):
    files: Any
    parsing: Any


ClientFactory = Callable[..., _LlamaParseClient]
LlamaParseTier = Literal["fast", "cost_effective", "agentic", "agentic_plus"]


async def parse_pdf_with_llamaparse(
    doc_bytes: bytes,
    *,
    api_key: str,
    base_url: str = "",
    tier: LlamaParseTier = "cost_effective",
    version: str = "latest",
    max_pages: int | None = None,
    target_pages: str = "",
    use_cost_optimizer: bool = False,
    disable_cache: bool = False,
    do_not_cache: bool = False,
    timeout_s: float = 300.0,
    ocr_languages: str = "en",
    filename: str = "document.pdf",
    media_type: str = "application/pdf",
    client_factory: ClientFactory | None = None,
) -> LlamaParseDocument:
    """Upload PDF bytes to LlamaParse and return normalized result data."""
    api_key = api_key.strip()
    if not api_key:
        raise LlamaParseUnavailableError(
            "LLAMA_CLOUD_API_KEY is required when STRUCTURED_RENDER_V2_PDF_BACKEND=llamaparse"
        )

    client = _create_client(
        api_key=api_key,
        base_url=base_url.strip(),
        timeout_s=timeout_s,
        client_factory=client_factory,
    )
    try:
        uploaded = await _call_provider(
            client.files.create(
                file=(filename, doc_bytes, media_type),
                purpose="parse",
            )
        )
        file_id = _get_attr(uploaded, "id")
        if not isinstance(file_id, str) or not file_id.strip():
            raise LlamaParseOutputError("LlamaParse upload did not return a file id")

        request = _parse_request(
            file_id=file_id,
            tier=tier,
            version=version,
            max_pages=max_pages,
            target_pages=target_pages,
            use_cost_optimizer=use_cost_optimizer,
            disable_cache=disable_cache,
            do_not_cache=do_not_cache,
            timeout_s=timeout_s,
            ocr_languages=ocr_languages,
        )
        response = await _call_provider(client.parsing.parse(**request))
        return normalize_llamaparse_response(response)
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


def normalize_llamaparse_response(response: Any) -> LlamaParseDocument:
    raw = _plain(response)
    if not isinstance(raw, dict):
        raise LlamaParseOutputError("LlamaParse response was not an object")

    job = raw.get("job")
    if isinstance(job, Mapping):
        status = job.get("status")
        if status in {"FAILED", "CANCELLED"}:
            detail = job.get("error_message") or f"job status {status}"
            raise LlamaParseExecutionError(f"LlamaParse job did not complete: {detail}")

    markdown_pages = _markdown_pages(raw.get("markdown"))
    text_pages = _text_pages(raw.get("text"))
    items_pages = _items_pages(raw.get("items"))
    metadata_pages = _metadata_pages(raw.get("metadata"))

    page_numbers = sorted(
        set(markdown_pages)
        | set(text_pages)
        | set(items_pages)
        | set(metadata_pages)
    )
    pages: list[LlamaParsePage] = []
    for page_number in page_numbers:
        markdown = markdown_pages.get(page_number, "")
        text = text_pages.get(page_number)
        items = tuple(items_pages.get(page_number, ()))
        metadata = metadata_pages.get(page_number, {})
        if not (markdown.strip() or (text and text.strip()) or items):
            continue
        pages.append(
            LlamaParsePage(
                page_number=page_number,
                markdown=markdown,
                text=text,
                items=items,
                metadata=metadata,
            )
        )

    if not pages:
        raise LlamaParseOutputError("LlamaParse returned no usable page content")

    job_metadata = raw.get("job_metadata")
    return LlamaParseDocument(
        pages=tuple(pages),
        markdown_full=_optional_str(raw.get("markdown_full")),
        text_full=_optional_str(raw.get("text_full")),
        job_metadata=dict(job_metadata) if isinstance(job_metadata, Mapping) else {},
        raw=raw,
    )


def _create_client(
    *,
    api_key: str,
    base_url: str,
    timeout_s: float,
    client_factory: ClientFactory | None,
) -> _LlamaParseClient:
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": timeout_s,
        "max_retries": 0,
    }
    if base_url:
        kwargs["base_url"] = base_url
    if client_factory is not None:
        return client_factory(**kwargs)
    try:
        from llama_cloud import AsyncLlamaCloud
    except ImportError as exc:
        raise LlamaParseUnavailableError(
            "llama-cloud is not installed. Install project dependencies to use the LlamaParse backend."
        ) from exc
    return AsyncLlamaCloud(**kwargs)


def _parse_request(
    *,
    file_id: str,
    tier: LlamaParseTier,
    version: str,
    max_pages: int | None,
    target_pages: str,
    use_cost_optimizer: bool,
    disable_cache: bool,
    do_not_cache: bool,
    timeout_s: float,
    ocr_languages: str,
) -> dict[str, Any]:
    expand = (
        ["text", "metadata", "job_metadata"]
        if tier == "fast"
        else ["markdown", "items", "metadata", "job_metadata"]
    )
    request: dict[str, Any] = {
        "file_id": file_id,
        "tier": tier,
        "version": version,
        "expand": expand,
        "timeout": timeout_s,
        "client_name": "remedy-access",
    }
    if disable_cache:
        request["disable_cache"] = True
    if do_not_cache:
        request["extra_body"] = {"do_not_cache": True}

    page_ranges: dict[str, Any] = {}
    if max_pages is not None:
        page_ranges["max_pages"] = max_pages
    target_pages = target_pages.strip()
    if target_pages:
        page_ranges["target_pages"] = target_pages
    if page_ranges:
        request["page_ranges"] = page_ranges

    processing_options: dict[str, Any] = {}
    languages = _ocr_language_list(ocr_languages)
    if languages:
        processing_options["ocr_parameters"] = {"languages": languages}
    if use_cost_optimizer and tier in {"agentic", "agentic_plus"}:
        processing_options["cost_optimizer"] = {"enable": True}
    if processing_options:
        request["processing_options"] = processing_options

    if tier != "fast":
        request["output_options"] = {
            "markdown": {
                "tables": {
                    "merge_continued_tables": True,
                    "output_tables_as_markdown": True,
                }
            }
        }
    return request


async def _call_provider(awaitable: Any) -> Any:
    try:
        return await awaitable
    except LlamaParseError:
        raise
    except Exception as exc:
        raise _map_provider_error(exc) from exc


def _map_provider_error(exc: Exception) -> LlamaParseError:
    status_code = getattr(exc, "status_code", None)
    if status_code == 402:
        return LlamaParseExecutionError(
            "LlamaParse quota is exhausted or billing is required (HTTP 402); falling back"
        )
    if status_code == 429:
        return LlamaParseExecutionError(
            "LlamaParse rate limit was reached (HTTP 429); falling back"
        )
    if status_code in {401, 403}:
        return LlamaParseUnavailableError(
            "LlamaParse credentials were rejected; check LLAMA_CLOUD_API_KEY"
        )
    if exc.__class__.__name__ in {"PollingTimeoutError", "TimeoutException"}:
        return LlamaParseExecutionError(f"LlamaParse timed out: {exc}")
    if exc.__class__.__name__ == "APIResponseValidationError":
        return LlamaParseOutputError(f"LlamaParse returned malformed output: {exc}")
    return LlamaParseExecutionError(f"LlamaParse request failed: {exc}")


def _markdown_pages(value: Any) -> dict[int, str]:
    view = _mapping(value)
    raw_pages = view.get("pages") if view else None
    if not isinstance(raw_pages, Sequence) or isinstance(raw_pages, (str, bytes)):
        return {}
    pages: dict[int, str] = {}
    for index, raw_page in enumerate(raw_pages, start=1):
        page = _mapping(raw_page)
        if page is None or page.get("success", True) is False:
            continue
        text = _optional_str(page.get("markdown"))
        if text is not None:
            pages[_page_number(page, index)] = text
    return pages


def _text_pages(value: Any) -> dict[int, str]:
    view = _mapping(value)
    raw_pages = view.get("pages") if view else None
    if not isinstance(raw_pages, Sequence) or isinstance(raw_pages, (str, bytes)):
        return {}
    pages: dict[int, str] = {}
    for index, raw_page in enumerate(raw_pages, start=1):
        page = _mapping(raw_page)
        if page is None or page.get("success", True) is False:
            continue
        text = _optional_str(page.get("text"))
        if text is not None:
            pages[_page_number(page, index)] = text
    return pages


def _items_pages(value: Any) -> dict[int, tuple[dict[str, Any], ...]]:
    view = _mapping(value)
    raw_pages = view.get("pages") if view else None
    if not isinstance(raw_pages, Sequence) or isinstance(raw_pages, (str, bytes)):
        return {}
    pages: dict[int, tuple[dict[str, Any], ...]] = {}
    for index, raw_page in enumerate(raw_pages, start=1):
        page = _mapping(raw_page)
        if page is None or page.get("success", True) is False:
            continue
        raw_items = page.get("items")
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            continue
        items = tuple(item for item in (_mapping(raw_item) for raw_item in raw_items) if item)
        if items:
            pages[_page_number(page, index)] = items
    return pages


def _metadata_pages(value: Any) -> dict[int, dict[str, Any]]:
    view = _mapping(value)
    raw_pages = view.get("pages") if view else None
    if not isinstance(raw_pages, Sequence) or isinstance(raw_pages, (str, bytes)):
        return {}
    pages: dict[int, dict[str, Any]] = {}
    for index, raw_page in enumerate(raw_pages, start=1):
        page = _mapping(raw_page)
        if page is not None:
            pages[_page_number(page, index)] = dict(page)
    return pages


def _page_number(page: Mapping[str, Any], fallback: int) -> int:
    raw = page.get("page_number")
    if isinstance(raw, int) and raw >= 1:
        return raw
    raise LlamaParseOutputError("LlamaParse page entry is missing a valid page_number")


def _ocr_language_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _mapping(value: Any) -> dict[str, Any] | None:
    plain = _plain(value)
    return dict(plain) if isinstance(plain, Mapping) else None


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="python"))
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _plain(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(child) for child in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return _plain(vars(value))
    return value


def _get_attr(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


__all__ = [
    "LlamaParseDocument",
    "LlamaParseError",
    "LlamaParseExecutionError",
    "LlamaParseOutputError",
    "LlamaParsePage",
    "LlamaParseUnavailableError",
    "normalize_llamaparse_response",
    "parse_pdf_with_llamaparse",
]
