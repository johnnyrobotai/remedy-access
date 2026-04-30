from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.parser_backends import llamaparse_adapter as adapter


class FakeFiles:
    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self.calls = calls

    async def create(self, **kwargs):
        self.calls.append(("files.create", kwargs))
        return SimpleNamespace(id="file-123")


class FakeParsing:
    def __init__(self, calls: list[tuple[str, dict]], error: Exception | None = None) -> None:
        self.calls = calls
        self.error = error

    async def parse(self, **kwargs):
        self.calls.append(("parsing.parse", kwargs))
        if self.error is not None:
            raise self.error
        return {
            "job": {"id": "job-123", "project_id": "project-123", "status": "COMPLETED"},
            "markdown": {
                "pages": [
                    {
                        "success": True,
                        "page_number": 1,
                        "markdown": "# Invoice\n\nIntro text.",
                    }
                ]
            },
            "items": {"pages": [{"success": True, "page_number": 1, "items": []}]},
            "metadata": {"pages": [{"page_number": 1, "confidence": 0.98}]},
            "job_metadata": {"credits": 3},
        }


class FakeClient:
    def __init__(self, calls: list[tuple[str, dict]], error: Exception | None = None) -> None:
        self.files = FakeFiles(calls)
        self.parsing = FakeParsing(calls, error=error)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class ProviderError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"provider status {status_code}")
        self.status_code = status_code


def _factory(calls: list[tuple[str, dict]], error: Exception | None = None):
    clients: list[FakeClient] = []

    def create_client(**kwargs):
        calls.append(("client", kwargs))
        client = FakeClient(calls, error=error)
        clients.append(client)
        return client

    return create_client, clients


@pytest.mark.asyncio
async def test_missing_api_key_raises_unavailable() -> None:
    with pytest.raises(adapter.LlamaParseUnavailableError, match="LLAMA_CLOUD_API_KEY"):
        await adapter.parse_pdf_with_llamaparse(
            b"%PDF",
            api_key="",
            client_factory=lambda **_: FakeClient([]),
        )


@pytest.mark.asyncio
async def test_cost_effective_request_uploads_file_and_requests_markdown_items() -> None:
    calls: list[tuple[str, dict]] = []
    factory, clients = _factory(calls)

    doc = await adapter.parse_pdf_with_llamaparse(
        b"%PDF",
        api_key="test-key",
        tier="cost_effective",
        version="latest",
        filename="sample.pdf",
        client_factory=factory,
    )

    assert doc.pages[0].markdown == "# Invoice\n\nIntro text."
    assert doc.pages[0].metadata["confidence"] == pytest.approx(0.98)
    assert clients[0].closed is True
    assert calls[0] == (
        "client",
        {"api_key": "test-key", "timeout": 300.0, "max_retries": 0},
    )
    assert calls[1][0] == "files.create"
    assert calls[1][1]["file"] == ("sample.pdf", b"%PDF", "application/pdf")
    assert calls[1][1]["purpose"] == "parse"
    parse_call = calls[2][1]
    assert parse_call["file_id"] == "file-123"
    assert parse_call["tier"] == "cost_effective"
    assert parse_call["version"] == "latest"
    assert parse_call["expand"] == ["markdown", "items", "metadata", "job_metadata"]
    assert parse_call["output_options"]["markdown"]["tables"]["merge_continued_tables"] is True


@pytest.mark.asyncio
async def test_fast_request_does_not_request_markdown_or_items() -> None:
    calls: list[tuple[str, dict]] = []
    factory, _ = _factory(calls)

    await adapter.parse_pdf_with_llamaparse(
        b"%PDF",
        api_key="test-key",
        tier="fast",
        client_factory=factory,
    )

    parse_call = calls[2][1]
    assert parse_call["expand"] == ["text", "metadata", "job_metadata"]
    assert "output_options" not in parse_call


@pytest.mark.asyncio
async def test_page_ranges_are_forwarded() -> None:
    calls: list[tuple[str, dict]] = []
    factory, _ = _factory(calls)

    await adapter.parse_pdf_with_llamaparse(
        b"%PDF",
        api_key="test-key",
        max_pages=5,
        target_pages="1,3-4",
        client_factory=factory,
    )

    assert calls[2][1]["page_ranges"] == {"max_pages": 5, "target_pages": "1,3-4"}


@pytest.mark.asyncio
async def test_agentic_cost_optimizer_is_forwarded_only_for_agentic_tiers() -> None:
    calls: list[tuple[str, dict]] = []
    factory, _ = _factory(calls)

    await adapter.parse_pdf_with_llamaparse(
        b"%PDF",
        api_key="test-key",
        tier="agentic",
        use_cost_optimizer=True,
        ocr_languages="en,es",
        client_factory=factory,
    )

    assert calls[2][1]["processing_options"] == {
        "ocr_parameters": {"languages": ["en", "es"]},
        "cost_optimizer": {"enable": True},
    }


@pytest.mark.asyncio
async def test_http_402_maps_to_execution_error() -> None:
    calls: list[tuple[str, dict]] = []
    factory, _ = _factory(calls, error=ProviderError(402))

    with pytest.raises(adapter.LlamaParseExecutionError, match="quota"):
        await adapter.parse_pdf_with_llamaparse(
            b"%PDF",
            api_key="test-key",
            client_factory=factory,
        )


@pytest.mark.asyncio
async def test_http_429_maps_to_execution_error() -> None:
    calls: list[tuple[str, dict]] = []
    factory, _ = _factory(calls, error=ProviderError(429))

    with pytest.raises(adapter.LlamaParseExecutionError, match="rate limit"):
        await adapter.parse_pdf_with_llamaparse(
            b"%PDF",
            api_key="test-key",
            client_factory=factory,
        )


def test_malformed_response_raises_output_error() -> None:
    with pytest.raises(adapter.LlamaParseOutputError, match="no usable page content"):
        adapter.normalize_llamaparse_response(
            {"job": {"status": "COMPLETED"}, "markdown": {"pages": []}}
        )
