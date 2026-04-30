from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.structured_parser.types import BodyBlock, ParsedDocument, SectionBlock

LayoutBlockType = Literal[
    "section",
    "paragraph",
    "list",
    "table",
    "figure",
    "callout",
    "quote",
    "reference_list",
    "footnotes",
    "form_group",
]

HeroTreatment = Literal["title-only", "summary", "feature", "compact"]
LayoutDensity = Literal["comfortable", "balanced", "dense"]
TocPolicy = Literal["hidden", "compact", "expanded"]
SectionContainer = Literal[
    "flow",
    "flow-with-rail",
    "feature-stack",
    "table-stack",
    "form-stack",
    "appendix",
]
RailSide = Literal["none", "left", "right"]
BlockPlacement = Literal["main", "rail", "footer"]
BlockContainer = Literal[
    "lead-prose",
    "prose",
    "list",
    "table",
    "figure",
    "callout",
    "quote",
    "references",
    "footnotes",
    "form-group",
    "subsection",
]

_TEXT_BLOCK_TYPES = {"paragraph", "list", "quote"}
_RAIL_ELIGIBLE_TYPES = {"figure", "callout"}
_FOOTER_ELIGIBLE_TYPES = {"reference_list", "footnotes"}
_REFERENCE_SECTION_BLOCK_TYPES = {"reference_list", "footnotes"}
_REFERENCE_SECTION_KEYWORDS = {
    "references",
    "reference",
    "bibliography",
    "works cited",
    "citations",
    "notes",
    "footnotes",
    "appendix",
}
_ALLOWED_CONTAINERS_BY_TYPE: dict[str, set[str]] = {
    "section": {"subsection"},
    "paragraph": {"lead-prose", "prose"},
    "list": {"list"},
    "table": {"table"},
    "figure": {"figure"},
    "callout": {"callout"},
    "quote": {"lead-prose", "quote"},
    "reference_list": {"references"},
    "footnotes": {"footnotes"},
    "form_group": {"form-group"},
}


class LayoutPlanValidationError(ValueError):
    """Raised when a candidate LayoutPlan does not map cleanly to the content plan."""


class BlockPlacementPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_index: int = Field(ge=0)
    block_type: LayoutBlockType
    placement: BlockPlacement = "main"
    container: BlockContainer

    @model_validator(mode="after")
    def _validate_shape(self) -> BlockPlacementPlan:
        allowed_containers = _ALLOWED_CONTAINERS_BY_TYPE[self.block_type]
        if self.container not in allowed_containers:
            raise ValueError(
                f"container {self.container!r} is not allowed for block type {self.block_type!r}"
            )
        if self.placement == "rail" and self.block_type not in _RAIL_ELIGIBLE_TYPES:
            raise ValueError(f"block type {self.block_type!r} cannot move to a rail placement")
        if self.placement == "footer" and self.block_type not in _FOOTER_ELIGIBLE_TYPES:
            raise ValueError(f"block type {self.block_type!r} cannot move to a footer placement")
        if self.container == "lead-prose" and self.placement != "main":
            raise ValueError("lead prose must remain in the main flow")
        if self.container == "subsection" and self.placement != "main":
            raise ValueError("subsections must remain in the main flow")
        return self


class SectionLayoutPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str
    container: SectionContainer = "flow"
    rail_side: RailSide = "none"
    blocks: list[BlockPlacementPlan] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_section_shape(self) -> SectionLayoutPlan:
        has_rail = any(block.placement == "rail" for block in self.blocks)
        if has_rail and self.container != "flow-with-rail":
            raise ValueError("rail placements require a flow-with-rail section container")
        if not has_rail and self.container == "flow-with-rail":
            raise ValueError("flow-with-rail sections must include at least one rail block")
        if has_rail and self.rail_side == "none":
            raise ValueError("rail placements require rail_side to be left or right")
        if not has_rail and self.rail_side != "none":
            raise ValueError("rail_side must be none when a section has no rail placements")
        return self


class LayoutPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["v2"] = "v2"
    hero_treatment: HeroTreatment = "compact"
    density: LayoutDensity = "balanced"
    toc_policy: TocPolicy = "hidden"
    sections: list[SectionLayoutPlan] = Field(default_factory=list)


def heuristic_layout_plan(document: ParsedDocument, *, fmt: str = "pdf") -> LayoutPlan:
    """Build a deterministic layout plan that never rewrites or reorders source content."""
    doc_kind = _infer_doc_kind(document, fmt=fmt)
    sections = list(_iter_sections(document.body))
    layout = LayoutPlan(
        hero_treatment=_choose_hero_treatment(document, doc_kind),
        density=_choose_density(document, doc_kind),
        toc_policy=_choose_toc_policy(document, doc_kind),
        sections=[
            _heuristic_section_layout(section, section_index=index, doc_kind=doc_kind)
            for index, section in enumerate(sections)
        ],
    )
    return validate_layout_plan_for_document(layout, document)


def validate_layout_plan_for_document(
    candidate: LayoutPlan,
    content_document: ParsedDocument,
) -> LayoutPlan:
    source_sections = list(_iter_sections(content_document.body))
    expected_section_ids = [section.id for section in source_sections]
    actual_section_ids = [section.section_id for section in candidate.sections]
    if actual_section_ids != expected_section_ids:
        raise LayoutPlanValidationError(
            "layout plan section order does not match the parsed document"
        )

    for source_section, layout_section in zip(source_sections, candidate.sections, strict=True):
        _validate_section_layout(source_section, layout_section)

    return candidate


def _validate_section_layout(source: SectionBlock, layout: SectionLayoutPlan) -> None:
    if len(layout.blocks) != len(source.blocks):
        raise LayoutPlanValidationError(
            f"section {source.id!r} expected {len(source.blocks)} block decisions, "
            f"got {len(layout.blocks)}"
        )

    for expected_index, (source_block, block_plan) in enumerate(
        zip(source.blocks, layout.blocks, strict=True)
    ):
        if block_plan.block_index != expected_index:
            raise LayoutPlanValidationError(
                f"section {source.id!r} block order changed at index {expected_index}"
            )
        if block_plan.block_type != source_block.type:
            raise LayoutPlanValidationError(
                f"section {source.id!r} block {expected_index} changed type from "
                f"{source_block.type!r} to {block_plan.block_type!r}"
            )

    lead_blocks = [block for block in layout.blocks if block.container == "lead-prose"]
    if len(lead_blocks) > 1:
        raise LayoutPlanValidationError(f"section {source.id!r} has multiple lead-prose blocks")


def _heuristic_section_layout(
    section: SectionBlock,
    *,
    section_index: int,
    doc_kind: str,
) -> SectionLayoutPlan:
    block_types = [block.type for block in section.blocks]
    has_form = "form_group" in block_types
    has_table = "table" in block_types
    has_references = _is_reference_section(section)
    has_text = any(block_type in _TEXT_BLOCK_TYPES for block_type in block_types)
    has_requested_rail = any(
        block.type in _RAIL_ELIGIBLE_TYPES and block.placement == "rail"
        for block in section.blocks
    )
    has_rail_candidates = has_requested_rail or any(
        block_type in _RAIL_ELIGIBLE_TYPES for block_type in block_types
    )
    has_feature_figure = "figure" in block_types

    if has_form:
        container: SectionContainer = "form-stack"
    elif has_references:
        container = "appendix"
    elif has_table and not has_rail_candidates:
        container = "table-stack"
    elif doc_kind == "brochure" and has_feature_figure and not has_form and not has_references:
        container = "feature-stack"
    elif has_text and has_rail_candidates:
        container = "flow-with-rail"
    else:
        container = "flow"

    rail_side: RailSide = "right" if container == "flow-with-rail" else "none"
    lead_assigned = False
    blocks: list[BlockPlacementPlan] = []

    for block_index, block in enumerate(section.blocks):
        placement: BlockPlacement = "main"
        if block.type in _FOOTER_ELIGIBLE_TYPES:
            placement = "footer"
        elif container == "flow-with-rail" and block.type in _RAIL_ELIGIBLE_TYPES:
            placement = "rail"

        block_container = _container_for_block(
            block_type=block.type,
            section_index=section_index,
            placement=placement,
            lead_assigned=lead_assigned,
            source_placement=block.placement,
        )
        if block_container == "lead-prose":
            lead_assigned = True

        blocks.append(
            BlockPlacementPlan(
                block_index=block_index,
                block_type=block.type,
                placement=placement,
                container=block_container,
            )
        )

    return SectionLayoutPlan(
        section_id=section.id,
        container=container,
        rail_side=rail_side,
        blocks=blocks,
    )


def _container_for_block(
    *,
    block_type: LayoutBlockType,
    section_index: int,
    placement: BlockPlacement,
    lead_assigned: bool,
    source_placement: str,
) -> BlockContainer:
    if block_type == "section":
        return "subsection"
    if block_type == "paragraph":
        if placement == "main" and not lead_assigned and (
            source_placement == "lead" or section_index == 0
        ):
            return "lead-prose"
        return "prose"
    if block_type == "list":
        return "list"
    if block_type == "table":
        return "table"
    if block_type == "figure":
        return "figure"
    if block_type == "callout":
        return "callout"
    if block_type == "quote":
        if placement == "main" and not lead_assigned and (
            source_placement == "lead" or section_index == 0
        ):
            return "lead-prose"
        return "quote"
    if block_type == "reference_list":
        return "references"
    if block_type == "footnotes":
        return "footnotes"
    return "form-group"


def _choose_hero_treatment(document: ParsedDocument, doc_kind: str) -> HeroTreatment:
    if doc_kind == "brochure" and any(
        block.type == "figure" for block in _iter_content_blocks(document.body)
    ):
        return "feature"
    if document.summary:
        return "summary"
    if doc_kind in {"scientific_article", "reference_doc", "form_doc"}:
        return "compact"
    return "title-only"


def _choose_density(document: ParsedDocument, doc_kind: str) -> LayoutDensity:
    total_blocks = sum(1 for _ in _iter_content_blocks(document.body))
    section_count = sum(1 for _ in _iter_sections(document.body))
    if doc_kind in {"scientific_article", "reference_doc"}:
        return "dense"
    if doc_kind == "brochure":
        return "comfortable"
    if total_blocks >= max(8, section_count * 3):
        return "dense"
    return "balanced"


def _choose_toc_policy(document: ParsedDocument, doc_kind: str) -> TocPolicy:
    section_count = sum(1 for _ in _iter_sections(document.body))
    if section_count < 3:
        return "hidden"
    if doc_kind in {"scientific_article", "reference_doc"} or section_count >= 6:
        return "expanded"
    return "compact"


def _is_reference_section(section: SectionBlock) -> bool:
    heading = section.heading.strip().lower()
    if any(keyword in heading for keyword in _REFERENCE_SECTION_KEYWORDS):
        return True
    return any(block.type in _REFERENCE_SECTION_BLOCK_TYPES for block in section.blocks)


def _infer_doc_kind(document: ParsedDocument, *, fmt: str) -> str:
    if document.doc_kind and document.doc_kind != "report":
        return document.doc_kind

    lowered_title = (document.title or "").lower()
    if fmt == "docx":
        return "memo"
    if "catalogue" in lowered_title or "catalog" in lowered_title or "brochure" in lowered_title:
        return "brochure"
    if "paper" in lowered_title or "study" in lowered_title or "journal" in lowered_title:
        return "scientific_article"
    if "reference" in lowered_title or "guide" in lowered_title or "spec" in lowered_title:
        return "reference_doc"
    if any(block.type == "form_group" for block in _iter_content_blocks(document.body)):
        return "form_doc"
    return "report"


def _iter_sections(blocks: list[BodyBlock]) -> Iterator[SectionBlock]:
    for block in blocks:
        if isinstance(block, SectionBlock):
            yield block
            yield from _iter_sections(block.blocks)


def _iter_content_blocks(blocks: list[BodyBlock]) -> Iterator[BodyBlock]:
    for block in blocks:
        if isinstance(block, SectionBlock):
            yield from _iter_content_blocks(block.blocks)
            continue
        yield block
