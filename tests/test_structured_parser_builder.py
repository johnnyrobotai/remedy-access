from __future__ import annotations

from backend.app.gemini.parse_v2 import (
    SemanticGroupingHint,
    SemanticGroupingResponse,
    build_semantic_grouping_payload,
    make_document_signals,
    needs_semantic_grouping,
    parse_document_v2,
)
from backend.app.images import ExtractedImageAsset
from backend.app.structured_parser.builder import FormField, PlacementHint, RawBlockSignal
from backend.app.structured_parser.types import ParsedDocument


def test_parse_document_v2_keeps_opening_prose_and_prefers_liteparse_table() -> None:
    signals = make_document_signals(
        format="pdf",
        page_count=2,
        blocks=[
            RawBlockSignal(
                id="doc-title",
                kind="heading",
                order=0,
                level=1,
                text="Operations Manual",
                page_start=1,
            ),
            RawBlockSignal(
                id="opening",
                kind="paragraph",
                order=1,
                text="This opening prose appears before the first real heading.",
                page_start=1,
            ),
            RawBlockSignal(
                id="spec-heading",
                kind="heading",
                order=2,
                level=2,
                text="Specifications",
                page_start=1,
            ),
            RawBlockSignal(
                id="table-raw",
                source_key="table-1",
                kind="unknown",
                order=3,
                page_start=2,
                text="Model A | 42",
            ),
        ],
        liteparse_blocks=[
            RawBlockSignal(
                id="table-liteparse",
                source="liteparse",
                source_key="table-1",
                kind="table",
                order=3,
                page_start=2,
                caption="Capacity",
                rows=[["Model", "Units"], ["A", "42"]],
                header_rows=[0],
            )
        ],
    )

    document = parse_document_v2(signals)

    assert isinstance(document, ParsedDocument)
    assert document.title == "Operations Manual"
    assert document.body[0].type == "paragraph"
    assert document.body[0].placement == "lead"
    assert document.body[0].text.startswith("This opening prose")

    section = document.body[1]
    assert section.type == "section"
    assert section.heading == "Specifications"
    assert section.page_start == 1
    assert section.page_end == 2
    assert section.blocks[0].type == "table"
    assert [cell.text for cell in section.blocks[0].body_rows[0].cells] == ["A", "42"]
    assert section.blocks[0].caption == "Capacity"


def test_parse_document_v2_groups_contiguous_form_fields() -> None:
    signals = make_document_signals(
        format="pdf",
        title="Registration Packet",
        page_count=1,
        blocks=[
            RawBlockSignal(
                id="identity",
                kind="heading",
                order=0,
                level=2,
                text="Identity",
                page_start=1,
            ),
            RawBlockSignal(
                id="first-name",
                kind="form_field",
                order=1,
                page_start=1,
                field=FormField(id="first-name", label="First name"),
            ),
            RawBlockSignal(
                id="email",
                kind="form_field",
                order=2,
                page_start=1,
                field=FormField(id="email", label="Email", field_type="email"),
            ),
        ],
    )

    document = parse_document_v2(signals)

    section = document.body[0]
    assert section.type == "section"
    assert section.blocks[0].type == "form_group"
    assert [field.id for field in section.blocks[0].fields] == ["first-name", "email"]
    assert [field.control for field in section.blocks[0].fields] == ["text", "email"]


def test_parse_document_v2_links_figures_to_assets_and_preserves_orphans() -> None:
    signals = make_document_signals(
        format="pdf",
        title="Field Guide",
        page_count=2,
        blocks=[
            RawBlockSignal(
                id="overview",
                kind="heading",
                order=0,
                level=2,
                text="Overview",
                page_start=1,
            ),
            RawBlockSignal(
                id="figure-1",
                kind="figure",
                order=1,
                page_start=1,
                text="Plant anatomy diagram",
            ),
        ],
        image_assets=[
            ExtractedImageAsset(filename="img-1.png", page_number=1),
            ExtractedImageAsset(filename="img-2.png", page_number=2, kind="page_raster"),
        ],
    )

    document = parse_document_v2(signals)

    section = document.body[0]
    assert section.type == "section"
    assert section.blocks[0].type == "figure"
    assert section.blocks[0].asset_id == "img-1"
    assert section.blocks[0].alt_text_hint is not None

    orphan = document.body[1]
    assert orphan.type == "figure"
    assert orphan.asset_id == "img-2"
    assert orphan.placement == "rail"


def test_parse_document_v2_emits_explicit_callout_blocks() -> None:
    signals = make_document_signals(
        format="pdf",
        title="Safety Memo",
        page_count=1,
        blocks=[
            RawBlockSignal(
                id="procedures",
                kind="heading",
                order=0,
                level=2,
                text="Procedures",
                page_start=1,
            ),
            RawBlockSignal(
                id="warning-box",
                kind="callout",
                order=1,
                page_start=1,
                text="Wear protective gloves before handling the solvent.",
                group_name="Warning",
                placement_hint=PlacementHint.ASIDE,
            ),
        ],
    )

    document = parse_document_v2(signals)

    section = document.body[0]
    assert section.type == "section"
    assert section.blocks[0].type == "callout"
    assert section.blocks[0].title == "Warning"
    assert section.blocks[0].placement == "after"


def test_parse_document_v2_applies_schema_bound_semantic_grouping_for_notes() -> None:
    signals = make_document_signals(
        format="pdf",
        title="Policy",
        page_count=1,
        blocks=[
            RawBlockSignal(
                id="body",
                kind="paragraph",
                order=0,
                page_start=1,
                text="Policy body text.",
            ),
            RawBlockSignal(
                id="fn-1",
                kind="paragraph",
                order=1,
                page_start=1,
                placement_hint=PlacementHint.FOOTER,
                text="1. Applies to staff only.",
            ),
            RawBlockSignal(
                id="fn-2",
                kind="paragraph",
                order=2,
                page_start=1,
                placement_hint=PlacementHint.FOOTER,
                text="2. Exceptions require approval.",
            ),
            RawBlockSignal(
                id="ref-1",
                kind="unknown",
                order=3,
                page_start=1,
                placement_hint=PlacementHint.END,
                text="[A] Internal handbook.",
            ),
            RawBlockSignal(
                id="ref-2",
                kind="unknown",
                order=4,
                page_start=1,
                placement_hint=PlacementHint.END,
                text="[B] State regulations.",
            ),
        ],
    )
    grouping = SemanticGroupingResponse(
        groups=[
            SemanticGroupingHint(
                id="footnotes-page-1",
                kind="footnotes",
                signal_ids=["fn-1", "fn-2"],
                placement_hint=PlacementHint.FOOTER,
            ),
            SemanticGroupingHint(
                id="references-page-1",
                kind="references",
                signal_ids=["ref-1", "ref-2"],
                placement_hint=PlacementHint.END,
            ),
        ]
    )

    assert needs_semantic_grouping(signals) is True
    payload = build_semantic_grouping_payload(signals)
    assert {item["id"] for item in payload["ambiguous_signals"]} >= {"fn-1", "fn-2", "ref-1", "ref-2"}

    document = parse_document_v2(signals, semantic_grouping=grouping)

    assert document.body[1].type == "footnotes"
    assert [item.label for item in document.body[1].entries] == ["1", "2"]
    assert document.body[2].type == "reference_list"
    assert [item.label for item in document.body[2].entries] == ["A", "B"]
