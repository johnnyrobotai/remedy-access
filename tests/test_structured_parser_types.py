from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app import structured_parser as structured_parser_pkg
from backend.app.structured_parser.types import (
    FigureBlock,
    FootnotesBlock,
    FormFieldSpec,
    FormGroupBlock,
    ParsedDocument,
    ReferenceListBlock,
    TableBlock,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "structured_parser"
FIXTURE_NAMES = sorted(path.name for path in FIXTURE_DIR.glob("*.json"))


def _load_fixture(name: str) -> ParsedDocument:
    return ParsedDocument.model_validate(json.loads((FIXTURE_DIR / name).read_text()))


def _walk_blocks(document: ParsedDocument):
    def walk(blocks):
        for block in blocks:
            yield block
            nested = getattr(block, "blocks", None)
            if nested:
                yield from walk(nested)

    yield from walk(document.body)


def test_structured_parser_package_exports_canonical_types() -> None:
    assert structured_parser_pkg.ParsedDocument is ParsedDocument
    assert structured_parser_pkg.TableBlock is TableBlock


@pytest.mark.parametrize("fixture_name", FIXTURE_NAMES)
def test_structured_parser_fixture_roundtrip(fixture_name: str) -> None:
    parsed = _load_fixture(fixture_name)
    reparsed = ParsedDocument.model_validate_json(parsed.model_dump_json(exclude_none=True))
    assert reparsed == parsed


def test_opening_prose_fixture_keeps_root_prose_before_first_section() -> None:
    parsed = _load_fixture("opening-prose-before-first-heading.json")
    assert [block.type for block in parsed.body[:3]] == ["paragraph", "paragraph", "section"]
    assert parsed.body[0].placement == "lead"
    assert parsed.body[2].id == "inspection-findings"


def test_table_fixture_preserves_table_footnote_linkage() -> None:
    parsed = _load_fixture("table-with-footnotes.json")
    table = next(block for block in _walk_blocks(parsed) if isinstance(block, TableBlock))
    footnotes = next(block for block in _walk_blocks(parsed) if isinstance(block, FootnotesBlock))

    refs = [
        ref
        for row in table.body_rows
        for cell in row.cells
        for ref in cell.footnote_refs
    ]

    assert refs == ["rate-baseline", "zone-b-surcharge"]
    assert {entry.id for entry in footnotes.entries} == set(refs)


def test_figure_fixture_preserves_asset_linkage_and_hints() -> None:
    parsed = _load_fixture("figure-with-caption-and-asset-linkage.json")
    figure = next(block for block in _walk_blocks(parsed) if isinstance(block, FigureBlock))

    assert figure.asset_id == "img-003"
    assert figure.caption.startswith("Control panel layout")
    assert figure.alt_text_hint is not None
    assert figure.description_hint is not None


def test_form_fixture_preserves_grouped_control_order() -> None:
    parsed = _load_fixture("grouped-form-controls.json")
    group = next(block for block in _walk_blocks(parsed) if isinstance(block, FormGroupBlock))

    assert [field.id for field in group.fields] == [
        "preferred-channel",
        "activity-interests",
        "availability-window",
        "contact-notes",
    ]
    assert group.fields[0].control == "radio"
    assert len(group.fields[0].options) == 3
    assert group.fields[1].control == "checkbox"
    assert len(group.fields[1].options) == 2


def test_references_fixture_preserves_reference_order() -> None:
    parsed = _load_fixture("references.json")
    references = next(block for block in _walk_blocks(parsed) if isinstance(block, ReferenceListBlock))

    assert [entry.id for entry in references.entries] == ["ref-one", "ref-two"]
    assert references.list_style == "ordered"


def test_parsed_document_rejects_duplicate_block_ids() -> None:
    raw = json.loads((FIXTURE_DIR / "opening-prose-before-first-heading.json").read_text())
    raw["body"][1]["id"] = raw["body"][0]["id"]

    with pytest.raises(ValidationError, match="duplicate block id"):
        ParsedDocument.model_validate(raw)


def test_parsed_document_rejects_unknown_table_footnote_refs() -> None:
    raw = json.loads((FIXTURE_DIR / "table-with-footnotes.json").read_text())
    raw["body"][0]["blocks"][0]["body_rows"][0]["cells"][2]["footnote_refs"] = ["missing-note"]

    with pytest.raises(ValidationError, match="unknown footnote refs"):
        ParsedDocument.model_validate(raw)


def test_parsed_document_rejects_skipped_top_level_section_levels() -> None:
    raw = json.loads((FIXTURE_DIR / "opening-prose-before-first-heading.json").read_text())
    raw["body"][2]["level"] = 3

    with pytest.raises(ValidationError, match="top-level sections must use level 2"):
        ParsedDocument.model_validate(raw)


def test_form_field_requires_options_for_radio_controls() -> None:
    with pytest.raises(ValidationError, match="require options"):
        FormFieldSpec(id="preferred-channel", label="Preferred Channel", control="radio")


def test_figure_requires_accessibility_hint() -> None:
    with pytest.raises(ValidationError, match="alt_text_hint or description_hint"):
        FigureBlock(
            id="overview-figure",
            type="figure",
            asset_id="img-1",
            caption="Overview",
            page_start=1,
            page_end=1,
            placement="inline",
        )
