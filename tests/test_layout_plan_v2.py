from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.app.documents import DocFormat
from backend.app.gemini.design_v2 import plan_layout_v2
from backend.app.layout_plan_v2 import (
    BlockPlacementPlan,
    LayoutPlan,
    LayoutPlanValidationError,
    SectionLayoutPlan,
    heuristic_layout_plan,
    validate_layout_plan_for_document,
)
from backend.app.structured_parser.types import ParsedDocument


def _sample_parsed_document() -> ParsedDocument:
    return ParsedDocument.model_validate(
        {
            "schema_version": "structured-render-v2",
            "source_format": "pdf",
            "title": "Systems Paper",
            "summary": "A concise summary of the paper.",
            "doc_kind": "scientific_article",
            "language": "en",
            "page_count": 8,
            "body": [
                {
                    "id": "opening-overview",
                    "type": "paragraph",
                    "text": "Opening prose before the first heading.",
                    "page_start": 1,
                    "page_end": 1,
                    "placement": "lead",
                },
                {
                    "id": "overview",
                    "type": "section",
                    "heading": "Overview",
                    "level": 2,
                    "page_start": 1,
                    "page_end": 2,
                    "placement": "inline",
                    "blocks": [
                        {
                            "id": "overview-paragraph",
                            "type": "paragraph",
                            "text": "Opening overview paragraph.",
                            "page_start": 1,
                            "page_end": 1,
                            "placement": "lead",
                        },
                        {
                            "id": "overview-figure",
                            "type": "figure",
                            "asset_id": "img-1",
                            "caption": "System overview figure.",
                            "alt_text_hint": "Architecture diagram showing the main services.",
                            "page_start": 1,
                            "page_end": 1,
                            "placement": "rail",
                        },
                        {
                            "id": "overview-callout",
                            "type": "callout",
                            "title": "Important",
                            "body": ["Important note."],
                            "page_start": 1,
                            "page_end": 1,
                            "placement": "rail",
                        },
                        {
                            "id": "subsystem-details",
                            "type": "section",
                            "heading": "Subsystem Details",
                            "level": 3,
                            "page_start": 2,
                            "page_end": 2,
                            "placement": "inline",
                            "blocks": [
                                {
                                    "id": "subsystem-paragraph",
                                    "type": "paragraph",
                                    "text": "Details about the subsystem design.",
                                    "page_start": 2,
                                    "page_end": 2,
                                    "placement": "inline",
                                },
                                {
                                    "id": "subsystem-list",
                                    "type": "list",
                                    "list_style": "unordered",
                                    "page_start": 2,
                                    "page_end": 2,
                                    "placement": "inline",
                                    "items": [
                                        {"text": "Shared event log"},
                                        {"text": "Replicated write path"},
                                    ],
                                },
                            ],
                        },
                    ],
                },
                {
                    "id": "results",
                    "type": "section",
                    "heading": "Results",
                    "level": 2,
                    "page_start": 3,
                    "page_end": 3,
                    "placement": "inline",
                    "blocks": [
                        {
                            "id": "results-table",
                            "type": "table",
                            "caption": "Experimental results table.",
                            "page_start": 3,
                            "page_end": 3,
                            "placement": "inline",
                            "header_rows": [
                                {
                                    "cells": [
                                        {"text": "Metric", "kind": "header", "scope": "col"},
                                        {"text": "Value", "kind": "header", "scope": "col"},
                                    ]
                                }
                            ],
                            "body_rows": [
                                {
                                    "cells": [
                                        {"text": "Recall", "kind": "header", "scope": "row"},
                                        {"text": "0.96", "kind": "data"},
                                    ]
                                }
                            ],
                            "footer_rows": [],
                        }
                    ],
                },
                {
                    "id": "registration",
                    "type": "section",
                    "heading": "Registration",
                    "level": 2,
                    "page_start": 4,
                    "page_end": 4,
                    "placement": "inline",
                    "blocks": [
                        {
                            "id": "registration-form",
                            "type": "form_group",
                            "legend": "Registration Inputs",
                            "page_start": 4,
                            "page_end": 4,
                            "placement": "inline",
                            "fields": [
                                {
                                    "id": "name",
                                    "label": "Name",
                                    "control": "text",
                                }
                            ],
                        }
                    ],
                },
                {
                    "id": "references",
                    "type": "section",
                    "heading": "References",
                    "level": 2,
                    "page_start": 5,
                    "page_end": 5,
                    "placement": "inline",
                    "blocks": [
                        {
                            "id": "reference-list",
                            "type": "reference_list",
                            "title": "References",
                            "page_start": 5,
                            "page_end": 5,
                            "placement": "footer",
                            "entries": [
                                {
                                    "id": "ref-one",
                                    "label": "[1]",
                                    "text": "Example citation.",
                                }
                            ],
                        },
                        {
                            "id": "reference-footnotes",
                            "type": "footnotes",
                            "title": "Notes",
                            "page_start": 5,
                            "page_end": 5,
                            "placement": "footer",
                            "entries": [
                                {
                                    "id": "note-one",
                                    "label": "1",
                                    "text": "Retrieved April 2026.",
                                }
                            ],
                        },
                    ],
                },
            ],
        }
    )


def test_heuristic_layout_plan_maps_parsed_document_sections_without_mutating_content() -> None:
    document = _sample_parsed_document()
    before = document.model_dump(mode="json")

    layout = heuristic_layout_plan(document, fmt="pdf")

    assert document.model_dump(mode="json") == before
    assert layout.hero_treatment == "summary"
    assert layout.density == "dense"
    assert layout.toc_policy == "expanded"

    assert [section.section_id for section in layout.sections] == [
        "overview",
        "subsystem-details",
        "results",
        "registration",
        "references",
    ]

    assert layout.sections[0].container == "flow-with-rail"
    assert layout.sections[0].rail_side == "right"
    assert [
        (block.block_index, block.block_type, block.placement, block.container)
        for block in layout.sections[0].blocks
    ] == [
        (0, "paragraph", "main", "lead-prose"),
        (1, "figure", "rail", "figure"),
        (2, "callout", "rail", "callout"),
        (3, "section", "main", "subsection"),
    ]

    assert layout.sections[1].container == "flow"
    assert [
        (block.block_type, block.placement, block.container)
        for block in layout.sections[1].blocks
    ] == [
        ("paragraph", "main", "prose"),
        ("list", "main", "list"),
    ]

    assert layout.sections[2].container == "table-stack"
    assert layout.sections[2].blocks[0].container == "table"
    assert layout.sections[2].blocks[0].placement == "main"

    assert layout.sections[3].container == "form-stack"
    assert layout.sections[3].blocks[0].container == "form-group"
    assert layout.sections[3].blocks[0].placement == "main"

    assert layout.sections[4].container == "appendix"
    assert [
        (block.block_type, block.placement, block.container)
        for block in layout.sections[4].blocks
    ] == [
        ("reference_list", "footer", "references"),
        ("footnotes", "footer", "footnotes"),
    ]


def test_heuristic_layout_plan_is_deterministic() -> None:
    document = _sample_parsed_document()

    first = heuristic_layout_plan(document, fmt="pdf")
    second = heuristic_layout_plan(document, fmt="pdf")

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_validate_layout_plan_rejects_reordered_or_destructive_block_mapping() -> None:
    document = _sample_parsed_document()
    candidate = LayoutPlan(
        hero_treatment="summary",
        density="dense",
        toc_policy="expanded",
        sections=[
            SectionLayoutPlan(
                section_id="overview",
                container="flow-with-rail",
                rail_side="right",
                blocks=[
                    BlockPlacementPlan(
                        block_index=1,
                        block_type="paragraph",
                        placement="main",
                        container="prose",
                    ),
                    BlockPlacementPlan(
                        block_index=0,
                        block_type="figure",
                        placement="rail",
                        container="figure",
                    ),
                    BlockPlacementPlan(
                        block_index=2,
                        block_type="callout",
                        placement="rail",
                        container="callout",
                    ),
                    BlockPlacementPlan(
                        block_index=3,
                        block_type="section",
                        placement="main",
                        container="subsection",
                    ),
                ],
            ),
            SectionLayoutPlan(
                section_id="subsystem-details",
                container="flow",
                rail_side="none",
                blocks=[
                    BlockPlacementPlan(
                        block_index=0,
                        block_type="paragraph",
                        placement="main",
                        container="prose",
                    ),
                    BlockPlacementPlan(
                        block_index=1,
                        block_type="list",
                        placement="main",
                        container="list",
                    ),
                ],
            ),
            SectionLayoutPlan(
                section_id="results",
                container="table-stack",
                rail_side="none",
                blocks=[
                    BlockPlacementPlan(
                        block_index=0,
                        block_type="table",
                        placement="main",
                        container="table",
                    )
                ],
            ),
            SectionLayoutPlan(
                section_id="registration",
                container="form-stack",
                rail_side="none",
                blocks=[
                    BlockPlacementPlan(
                        block_index=0,
                        block_type="form_group",
                        placement="main",
                        container="form-group",
                    )
                ],
            ),
            SectionLayoutPlan(
                section_id="references",
                container="appendix",
                rail_side="none",
                blocks=[
                    BlockPlacementPlan(
                        block_index=0,
                        block_type="reference_list",
                        placement="footer",
                        container="references",
                    ),
                    BlockPlacementPlan(
                        block_index=1,
                        block_type="footnotes",
                        placement="footer",
                        container="footnotes",
                    ),
                ],
            ),
        ],
    )

    with pytest.raises(LayoutPlanValidationError):
        validate_layout_plan_for_document(candidate, document)


def test_validate_layout_plan_rejects_missing_nested_section_layout() -> None:
    document = _sample_parsed_document()
    candidate = heuristic_layout_plan(document, fmt="pdf").model_copy(
        update={"sections": heuristic_layout_plan(document, fmt="pdf").sections[1:]}
    )

    with pytest.raises(LayoutPlanValidationError, match="section order"):
        validate_layout_plan_for_document(candidate, document)


def test_plan_layout_v2_falls_back_to_heuristic_on_invalid_model_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _sample_parsed_document()
    expected = heuristic_layout_plan(document, fmt="pdf")

    async def fake_ollama_chat_json(**_kwargs):
        invalid = LayoutPlan(
            hero_treatment="summary",
            density="dense",
            toc_policy="expanded",
            sections=[
                SectionLayoutPlan(
                    section_id="overview",
                    container="flow-with-rail",
                    rail_side="right",
                    blocks=[
                        BlockPlacementPlan(
                            block_index=1,
                            block_type="paragraph",
                            placement="main",
                            container="prose",
                        ),
                        BlockPlacementPlan(
                            block_index=0,
                            block_type="figure",
                            placement="rail",
                            container="figure",
                        ),
                        BlockPlacementPlan(
                            block_index=2,
                            block_type="callout",
                            placement="rail",
                            container="callout",
                        ),
                        BlockPlacementPlan(
                            block_index=3,
                            block_type="section",
                            placement="main",
                            container="subsection",
                        ),
                    ],
                ),
                SectionLayoutPlan(
                    section_id="subsystem-details",
                    container="flow",
                    rail_side="none",
                    blocks=[
                        BlockPlacementPlan(
                            block_index=0,
                            block_type="paragraph",
                            placement="main",
                            container="prose",
                        ),
                        BlockPlacementPlan(
                            block_index=1,
                            block_type="list",
                            placement="main",
                            container="list",
                        ),
                    ],
                ),
                SectionLayoutPlan(
                    section_id="results",
                    container="table-stack",
                    rail_side="none",
                    blocks=[
                        BlockPlacementPlan(
                            block_index=0,
                            block_type="table",
                            placement="main",
                            container="table",
                        )
                    ],
                ),
                SectionLayoutPlan(
                    section_id="registration",
                    container="form-stack",
                    rail_side="none",
                    blocks=[
                        BlockPlacementPlan(
                            block_index=0,
                            block_type="form_group",
                            placement="main",
                            container="form-group",
                        )
                    ],
                ),
                SectionLayoutPlan(
                    section_id="references",
                    container="appendix",
                    rail_side="none",
                    blocks=[
                        BlockPlacementPlan(
                            block_index=0,
                            block_type="reference_list",
                            placement="footer",
                            container="references",
                        ),
                        BlockPlacementPlan(
                            block_index=1,
                            block_type="footnotes",
                            placement="footer",
                            container="footnotes",
                        ),
                    ],
                ),
            ],
        )
        return invalid.model_dump_json(), {}

    monkeypatch.setattr("backend.app.gemini.design_v2.ollama_chat_json", fake_ollama_chat_json)
    monkeypatch.setattr(
        "backend.app.gemini.design_v2.get_settings",
        lambda: SimpleNamespace(
            llm_provider="ollama",
            ollama_plan_model="test-model",
            ollama_reasoning_level="low",
            gemini_call_timeout=30.0,
            google_api_key="",
        ),
    )

    result = asyncio.run(plan_layout_v2(document, source_hint="paper.pdf", fmt=DocFormat.PDF))

    assert result.model_dump(mode="json") == expected.model_dump(mode="json")


def test_plan_layout_v2_accepts_valid_model_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    document = _sample_parsed_document()
    expected = heuristic_layout_plan(document, fmt="pdf")

    async def fake_ollama_chat_json(**_kwargs):
        return expected.model_dump_json(), {}

    monkeypatch.setattr("backend.app.gemini.design_v2.ollama_chat_json", fake_ollama_chat_json)
    monkeypatch.setattr(
        "backend.app.gemini.design_v2.get_settings",
        lambda: SimpleNamespace(
            llm_provider="ollama",
            ollama_plan_model="test-model",
            ollama_reasoning_level="low",
            gemini_call_timeout=30.0,
            google_api_key="",
        ),
    )

    result = asyncio.run(plan_layout_v2(document, source_hint="paper.pdf", fmt=DocFormat.PDF))

    assert result.model_dump(mode="json") == expected.model_dump(mode="json")
