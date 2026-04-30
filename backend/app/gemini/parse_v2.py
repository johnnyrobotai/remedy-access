from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.images import ImageAssetLike
from backend.app.structured_parser.builder import (
    DocumentAsset,
    DocumentSignals,
    FormField,
    PlacementHint,
    RawBlockSignal,
    TextEntry,
    build_parsed_document,
    normalize_document_assets,
)
from backend.app.structured_parser.types import ParsedDocument

SEMANTIC_GROUPING_SYSTEM = """You repair raw document parsing by grouping only the
provided source signal ids into higher-confidence semantic blocks.

Rules:
- Use only source ids that appear in the payload.
- Do not rewrite, summarize, or invent text.
- Preserve source order and page spans exactly.
- Use `callout`, `footnotes`, `references`, `form_group`, or `figure` only.
- Return the smallest set of groups needed to fix obviously ambiguous structure.
"""


class SemanticGroupingHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["callout", "footnotes", "references", "form_group", "figure"]
    signal_ids: list[str] = Field(min_length=1)
    title: str | None = None
    caption: str | None = None
    placement_hint: PlacementHint | None = None
    asset_ids: list[str] = Field(default_factory=list)


class SemanticGroupingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[SemanticGroupingHint] = Field(default_factory=list)


def make_document_signals(
    *,
    format: Literal["pdf", "docx", "xlsx", "unknown"] = "pdf",
    title: str | None = None,
    language: str = "en",
    page_count: int | None = None,
    blocks: Sequence[RawBlockSignal] | None = None,
    liteparse_blocks: Sequence[RawBlockSignal] | None = None,
    image_assets: Sequence[DocumentAsset | ImageAssetLike] | None = None,
    metadata: dict[str, Any] | None = None,
) -> DocumentSignals:
    return DocumentSignals(
        format=format,
        title=title,
        language=language,
        page_count=page_count,
        blocks=list(blocks or []),
        liteparse_blocks=list(liteparse_blocks or []),
        assets=normalize_document_assets(image_assets),
        metadata=metadata or {},
    )


def needs_semantic_grouping(signals: DocumentSignals) -> bool:
    return bool(build_semantic_grouping_payload(signals)["ambiguous_signals"])


def build_semantic_grouping_payload(signals: DocumentSignals) -> dict[str, Any]:
    ambiguous: list[dict[str, Any]] = []
    for signal in [*signals.blocks, *signals.liteparse_blocks]:
        if signal.kind == "unknown":
            ambiguous.append(_signal_payload(signal))
            continue
        if signal.kind == "paragraph" and signal.placement_hint in {
            PlacementHint.ASIDE,
            PlacementHint.FOOTER,
            PlacementHint.END,
        }:
            ambiguous.append(_signal_payload(signal))
            continue
        if signal.kind == "form_field" and not signal.group_name:
            ambiguous.append(_signal_payload(signal))
    return {
        "format": signals.format,
        "title": signals.title,
        "language": signals.language,
        "page_count": signals.page_count,
        "assets": [
            {"id": asset.id, "page_number": asset.page_number, "kind": asset.kind}
            for asset in signals.assets
        ],
        "ambiguous_signals": ambiguous,
    }


def parse_document_v2(
    signals: DocumentSignals,
    *,
    semantic_grouping: SemanticGroupingResponse | None = None,
) -> ParsedDocument:
    grouped_signals = (
        _apply_semantic_grouping(signals, semantic_grouping)
        if semantic_grouping is not None and semantic_grouping.groups
        else signals
    )
    return build_parsed_document(grouped_signals)


def _apply_semantic_grouping(
    signals: DocumentSignals,
    semantic_grouping: SemanticGroupingResponse,
) -> DocumentSignals:
    combined = sorted([*signals.blocks, *signals.liteparse_blocks], key=_signal_sort_key)
    by_id = {signal.id: signal for signal in combined}
    group_by_lead: dict[str, SemanticGroupingHint] = {}
    consumed: set[str] = set()

    for group in semantic_grouping.groups:
        members = [by_id[signal_id] for signal_id in group.signal_ids if signal_id in by_id]
        if not members:
            continue
        lead_id = min(members, key=_signal_sort_key).id
        group_by_lead[lead_id] = group
        consumed.update(member.id for member in members[1:])

    merged: list[RawBlockSignal] = []
    for signal in combined:
        if signal.id in consumed and signal.id not in group_by_lead:
            continue
        group = group_by_lead.get(signal.id)
        if group is None:
            merged.append(signal)
            continue
        members = [by_id[signal_id] for signal_id in group.signal_ids if signal_id in by_id]
        merged.append(_group_to_signal(group, members))

    return signals.model_copy(update={"blocks": merged, "liteparse_blocks": []})


def _group_to_signal(
    group: SemanticGroupingHint,
    members: Sequence[RawBlockSignal],
) -> RawBlockSignal:
    text_parts: list[str] = []
    items: list[str] = []
    entries: list[TextEntry] = []
    fields: list[FormField] = []
    rows: list[list[str]] = []
    header_rows: list[int] = []
    asset_ids: list[str] = list(group.asset_ids)
    placement_hint = group.placement_hint
    page_start = None
    page_end = None

    for member in members:
        if member.text and member.text.strip():
            if group.kind in {"footnotes", "references"}:
                items.append(member.text.strip())
            else:
                text_parts.append(member.text.strip())
        items.extend(member.items)
        entries.extend(member.entries)
        if member.field is not None:
            fields.append(member.field)
        fields.extend(member.fields)
        if not rows and member.rows:
            rows = member.rows
            header_rows = member.header_rows
        for asset_id in member.asset_ids:
            if asset_id not in asset_ids:
                asset_ids.append(asset_id)
        placement_hint = placement_hint or member.placement_hint
        page_start = _min_page(page_start, member.page_start)
        page_end = _max_page(page_end, member.page_end or member.page_start)

    return RawBlockSignal(
        id=group.id,
        kind=group.kind,
        order=min(member.order for member in members),
        source="semantic_hint",
        source_key=group.id,
        page_start=page_start,
        page_end=page_end,
        text="\n".join(text_parts) or None,
        caption=group.caption,
        items=items,
        entries=entries,
        rows=rows,
        header_rows=header_rows,
        fields=fields,
        group_name=group.title,
        asset_ids=asset_ids,
        placement_hint=placement_hint,
    )


def _signal_payload(signal: RawBlockSignal) -> dict[str, Any]:
    return {
        "id": signal.id,
        "kind": signal.kind,
        "order": signal.order,
        "page_start": signal.page_start,
        "page_end": signal.page_end,
        "placement_hint": signal.placement_hint,
        "text": signal.text,
        "items": signal.items,
        "entries": [entry.model_dump() for entry in signal.entries],
        "asset_ids": signal.asset_ids,
        "group_name": signal.group_name,
    }


def _signal_sort_key(signal: RawBlockSignal) -> tuple[int, int, int, str]:
    start_page = signal.page_start or signal.page_end or 1_000_000
    end_page = signal.page_end or signal.page_start or start_page
    return (signal.order, start_page, end_page, signal.id)


def _min_page(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


def _max_page(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)
