from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.images import ImageAssetLike, normalize_image_assets
from backend.app.structured_parser.types import (
    CalloutBlock as CanonicalCalloutBlock,
)
from backend.app.structured_parser.types import (
    FieldOption,
    FootnoteEntry,
    FormFieldSpec,
    ListItem,
    ReferenceEntry,
    ReferenceListBlock,
    SectionBlock,
    TableCell,
    TableRow,
)
from backend.app.structured_parser.types import (
    FigureBlock as CanonicalFigureBlock,
)
from backend.app.structured_parser.types import (
    FootnotesBlock as CanonicalFootnotesBlock,
)
from backend.app.structured_parser.types import (
    FormGroupBlock as CanonicalFormGroupBlock,
)
from backend.app.structured_parser.types import (
    ListBlock as CanonicalListBlock,
)
from backend.app.structured_parser.types import (
    ParagraphBlock as CanonicalParagraphBlock,
)
from backend.app.structured_parser.types import (
    ParsedDocument as CanonicalParsedDocument,
)
from backend.app.structured_parser.types import (
    TableBlock as CanonicalTableBlock,
)

_LABELLED_ENTRY_RE = re.compile(
    r"^\s*(?:\[(?P<bracket>[^\]]+)\]|(?P<plain>[A-Za-z0-9]+)[\.\)])\s*(?P<body>.+?)\s*$"
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_FORM_FIELD_TYPES = Literal[
    "text",
    "textarea",
    "checkbox",
    "radio",
    "select",
    "number",
    "date",
    "email",
    "tel",
    "url",
    "signature",
]


class PlacementHint(StrEnum):
    INLINE = "inline"
    ASIDE = "aside"
    FOOTER = "footer"
    END = "end"
    PAGE = "page"
    UNKNOWN = "unknown"


class SourceSpan(BaseModel):
    order_start: int = Field(ge=0)
    order_end: int = Field(ge=0)
    start_page: int | None = Field(default=None, ge=1)
    end_page: int | None = Field(default=None, ge=1)
    signal_ids: list[str] = Field(default_factory=list)


class ParserNotice(BaseModel):
    code: str
    message: str
    signal_ids: list[str] = Field(default_factory=list)


class DocumentAsset(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    filename: str
    order: int = Field(default=0, ge=0)
    page_number: int | None = Field(default=None, ge=1)
    kind: Literal["embedded", "page_raster", "unknown"] = "embedded"


class TextEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    label: str | None = None


class FormField(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    label: str
    field_type: _FORM_FIELD_TYPES = "text"
    required: bool = False
    options: list[str] = Field(default_factory=list)


class RawBlockSignal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    kind: Literal[
        "title",
        "heading",
        "paragraph",
        "list",
        "table",
        "figure",
        "caption",
        "callout",
        "footnote",
        "footnotes",
        "reference",
        "references",
        "form_field",
        "form_group",
        "unknown",
    ]
    order: int = Field(ge=0)
    source: Literal[
        "extract",
        "liteparse",
        "llamaparse",
        "semantic_hint",
        "asset_manifest",
    ] = "extract"
    source_key: str | None = None
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    level: int | None = Field(default=None, ge=1, le=6)
    text: str | None = None
    caption: str | None = None
    items: list[str] = Field(default_factory=list)
    entries: list[TextEntry] = Field(default_factory=list)
    ordered: bool = False
    rows: list[list[str]] = Field(default_factory=list)
    header_rows: list[int] = Field(default_factory=list)
    field: FormField | None = None
    fields: list[FormField] = Field(default_factory=list)
    group_name: str | None = None
    asset_ids: list[str] = Field(default_factory=list)
    placement_hint: PlacementHint | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentSignals(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format: Literal["pdf", "docx", "xlsx", "unknown"] = "pdf"
    title: str | None = None
    language: str = "en"
    page_count: int | None = Field(default=None, ge=1)
    blocks: list[RawBlockSignal] = Field(default_factory=list)
    liteparse_blocks: list[RawBlockSignal] = Field(default_factory=list)
    assets: list[DocumentAsset] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class _ParagraphNode(BaseModel):
    kind: Literal["paragraph"] = "paragraph"
    id: str
    text: str
    source_span: SourceSpan
    placement_hint: PlacementHint = PlacementHint.INLINE


class _ListNode(BaseModel):
    kind: Literal["list"] = "list"
    id: str
    items: list[str]
    ordered: bool = False
    source_span: SourceSpan
    placement_hint: PlacementHint = PlacementHint.INLINE


class _TableNode(BaseModel):
    kind: Literal["table"] = "table"
    id: str
    rows: list[list[str]]
    header_rows: list[int] = Field(default_factory=list)
    caption: str | None = None
    source_span: SourceSpan
    placement_hint: PlacementHint = PlacementHint.INLINE


class _FigureNode(BaseModel):
    kind: Literal["figure"] = "figure"
    id: str
    asset_ids: list[str] = Field(default_factory=list)
    caption: str | None = None
    source_span: SourceSpan
    placement_hint: PlacementHint = PlacementHint.INLINE


class _CalloutNode(BaseModel):
    kind: Literal["callout"] = "callout"
    id: str
    text: str
    title: str | None = None
    source_span: SourceSpan
    placement_hint: PlacementHint = PlacementHint.ASIDE


class _FootnotesNode(BaseModel):
    kind: Literal["footnotes"] = "footnotes"
    id: str
    items: list[TextEntry]
    source_span: SourceSpan
    placement_hint: PlacementHint = PlacementHint.FOOTER


class _ReferencesNode(BaseModel):
    kind: Literal["references"] = "references"
    id: str
    items: list[TextEntry]
    source_span: SourceSpan
    placement_hint: PlacementHint = PlacementHint.END


class _FormGroupNode(BaseModel):
    kind: Literal["form_group"] = "form_group"
    id: str
    fields: list[FormField]
    title: str | None = None
    description: str | None = None
    source_span: SourceSpan
    placement_hint: PlacementHint = PlacementHint.INLINE


_BlockNode = (
    _ParagraphNode
    | _ListNode
    | _TableNode
    | _FigureNode
    | _CalloutNode
    | _FootnotesNode
    | _ReferencesNode
    | _FormGroupNode
)


class _SectionNode(BaseModel):
    kind: Literal["section"] = "section"
    id: str
    heading: str
    level: int = Field(ge=2, le=6)
    source_span: SourceSpan
    blocks: list[_BlockNode] = Field(default_factory=list)


_Node = _SectionNode | _BlockNode


class _BuiltDocument(BaseModel):
    format: Literal["pdf", "docx", "xlsx", "unknown"] = "pdf"
    title: str
    language: str = "en"
    page_count: int = Field(ge=1)
    source_span: SourceSpan
    assets: list[DocumentAsset] = Field(default_factory=list)
    nodes: list[_Node] = Field(default_factory=list)
    notices: list[ParserNotice] = Field(default_factory=list)


def normalize_document_assets(
    assets: Sequence[DocumentAsset | ImageAssetLike] | None,
) -> list[DocumentAsset]:
    if not assets:
        return []
    normalized: list[DocumentAsset] = []
    for order, asset in enumerate(assets):
        if isinstance(asset, DocumentAsset):
            normalized.append(
                asset.model_copy(
                    update={"id": _normalize_asset_id(asset.id or asset.filename), "order": order}
                )
            )
            continue
        extracted = normalize_image_assets([asset])[0]
        normalized.append(
            DocumentAsset(
                id=_normalize_asset_id(extracted.filename),
                filename=extracted.filename,
                order=order,
                page_number=extracted.page_number,
                kind=extracted.kind if extracted.kind in {"embedded", "page_raster"} else "unknown",
            )
        )
    return normalized


def build_parsed_document(signals: DocumentSignals) -> CanonicalParsedDocument:
    notices: list[ParserNotice] = []
    assets = normalize_document_assets(signals.assets)
    prepared = _prepare_signals(signals)
    seen_ids: set[str] = set()
    used_asset_ids: set[str] = set()
    nodes: list[_Node] = []
    current_section: _SectionNode | None = None
    buffer: list[RawBlockSignal] = []

    def append_block(block: _BlockNode) -> None:
        nonlocal current_section
        if current_section is None:
            nodes.append(block)
            return
        current_section.blocks.append(block)
        current_section.source_span = merge_source_spans(
            [current_section.source_span, block.source_span]
        )

    def flush_buffer() -> None:
        nonlocal buffer
        if not buffer:
            return
        block = _build_buffer_block(
            buffer,
            seen_ids=seen_ids,
            used_asset_ids=used_asset_ids,
            assets=assets,
            notices=notices,
        )
        buffer = []
        if block is not None:
            append_block(block)

    title = (signals.title or "").strip() or None
    for signal in prepared:
        if _is_document_title_signal(signal, title=title, nodes=nodes, current_section=current_section):
            flush_buffer()
            text = _signal_text(signal)
            if text:
                title = text
            continue

        if _is_section_heading(signal, title=title):
            flush_buffer()
            heading = _signal_text(signal)
            if not heading:
                notices.append(
                    ParserNotice(
                        code="empty_heading_dropped",
                        message="Dropped a heading signal with no text.",
                        signal_ids=[signal.id],
                    )
                )
                continue
            section = _SectionNode(
                id=_unique_id(_slugify(heading), seen_ids, prefix="section"),
                heading=heading,
                level=max(2, signal.level or 2),
                source_span=source_span_from_signal(signal),
            )
            nodes.append(section)
            current_section = section
            continue

        if _can_buffer(buffer, signal):
            buffer.append(signal)
            continue

        flush_buffer()
        if signal.kind in {"form_field", "footnote", "reference"}:
            buffer = [signal]
            continue

        block = _signal_to_block(
            signal,
            seen_ids=seen_ids,
            used_asset_ids=used_asset_ids,
            assets=assets,
            notices=notices,
        )
        if block is not None:
            append_block(block)

    flush_buffer()
    _append_orphan_asset_figures(
        nodes,
        assets=assets,
        used_asset_ids=used_asset_ids,
        seen_ids=seen_ids,
        notices=notices,
    )

    if not title:
        first_section = next((node for node in nodes if isinstance(node, _SectionNode)), None)
        title = first_section.heading if first_section is not None else "Untitled document"

    page_count = signals.page_count or _infer_page_count(nodes, assets)
    source_span = _document_source_span(nodes, assets)
    built = _BuiltDocument(
        format=signals.format,
        title=title,
        language=signals.language or "en",
        page_count=page_count,
        source_span=source_span,
        assets=assets,
        nodes=nodes,
        notices=notices,
    )
    return _to_canonical_document(built, signals=signals)


def _to_canonical_document(
    built: _BuiltDocument,
    *,
    signals: DocumentSignals,
) -> CanonicalParsedDocument:
    body: list[Any] = []
    seen_section = False
    for node in built.nodes:
        body.append(
            _to_canonical_node(
                node,
                leading_paragraph=not seen_section and isinstance(node, _ParagraphNode),
            )
        )
        if isinstance(node, _SectionNode):
            seen_section = True

    if not body:
        body.append(
            CanonicalParagraphBlock(
                id="document-body",
                text=built.title,
                page_start=1,
                page_end=max(1, built.page_count),
                placement="lead",
            )
        )

    return CanonicalParsedDocument(
        source_format=_coerce_source_format(built.format),
        title=built.title,
        kicker=_metadata_text(signals, "kicker"),
        subtitle=_metadata_text(signals, "subtitle"),
        summary=_metadata_text(signals, "summary"),
        language=signals.language or "en",
        page_count=max(1, built.page_count),
        doc_kind=_infer_doc_kind(signals, built),
        body=body,
    )


def _to_canonical_node(
    node: _Node,
    *,
    leading_paragraph: bool = False,
) -> Any:
    if isinstance(node, _SectionNode):
        return SectionBlock(
            id=node.id,
            heading=node.heading,
            level=node.level,
            page_start=_page_start(node.source_span),
            page_end=_page_end(node.source_span),
            placement="inline",
            caption=None,
            blocks=[_to_canonical_node(child) for child in node.blocks],
        )
    if isinstance(node, _ParagraphNode):
        return CanonicalParagraphBlock(
            id=node.id,
            text=node.text,
            page_start=_page_start(node.source_span),
            page_end=_page_end(node.source_span),
            placement="lead" if leading_paragraph else _canonical_text_placement(node.placement_hint),
        )
    if isinstance(node, _ListNode):
        return CanonicalListBlock(
            id=node.id,
            page_start=_page_start(node.source_span),
            page_end=_page_end(node.source_span),
            placement=_canonical_text_placement(node.placement_hint),
            list_style="ordered" if node.ordered else "unordered",
            items=[ListItem(text=item) for item in node.items if item.strip()],
        )
    if isinstance(node, _TableNode):
        header_indexes = set(node.header_rows)
        header_rows: list[TableRow] = []
        body_rows: list[TableRow] = []
        footer_rows: list[TableRow] = []
        for index, row in enumerate(node.rows):
            rendered = TableRow(cells=_table_cells_from_row(row, header=index in header_indexes, row_header=bool(header_indexes)))
            if index in header_indexes:
                header_rows.append(rendered)
            else:
                body_rows.append(rendered)
        return CanonicalTableBlock(
            id=node.id,
            caption=node.caption,
            page_start=_page_start(node.source_span),
            page_end=_page_end(node.source_span),
            placement="inline",
            header_rows=header_rows,
            body_rows=body_rows or [TableRow(cells=[TableCell(text="", kind="data")])],
            footer_rows=footer_rows,
        )
    if isinstance(node, _FigureNode):
        asset_id = node.asset_ids[0] if node.asset_ids else node.id
        caption = node.caption or f"Figure for {node.id}"
        return CanonicalFigureBlock(
            id=node.id,
            asset_id=asset_id,
            caption=caption,
            alt_text_hint=caption,
            description_hint=None,
            page_start=_page_start(node.source_span),
            page_end=_page_end(node.source_span),
            placement=_canonical_figure_placement(node.placement_hint),
        )
    if isinstance(node, _CalloutNode):
        return CanonicalCalloutBlock(
            id=node.id,
            tone=_infer_callout_tone(node),
            title=node.title,
            body=_split_paragraphs(node.text),
            page_start=_page_start(node.source_span),
            page_end=_page_end(node.source_span),
            placement=_canonical_callout_placement(node.placement_hint),
        )
    if isinstance(node, _FootnotesNode):
        return CanonicalFootnotesBlock(
            id=node.id,
            title="Footnotes",
            entries=[
                FootnoteEntry(
                    id=_entry_id(node.id, item.label, index),
                    label=item.label,
                    text=item.text,
                )
                for index, item in enumerate(node.items, start=1)
            ],
            page_start=_page_start(node.source_span),
            page_end=_page_end(node.source_span),
            placement="footer",
        )
    if isinstance(node, _ReferencesNode):
        return ReferenceListBlock(
            id=node.id,
            title="References",
            list_style="ordered",
            entries=[
                ReferenceEntry(
                    id=_entry_id(node.id, item.label, index),
                    label=item.label,
                    text=item.text,
                )
                for index, item in enumerate(node.items, start=1)
            ],
            page_start=_page_start(node.source_span),
            page_end=_page_end(node.source_span),
            placement="footer",
        )
    if isinstance(node, _FormGroupNode):
        return CanonicalFormGroupBlock(
            id=node.id,
            legend=node.title or "Form fields",
            caption=node.description,
            fields=[_to_canonical_field(field) for field in node.fields],
            page_start=_page_start(node.source_span),
            page_end=_page_end(node.source_span),
            placement="inline",
        )
    raise TypeError(f"unsupported builder node: {type(node)!r}")


def _to_canonical_field(field: FormField) -> FormFieldSpec:
    option_values = [option for option in field.options if option.strip()]
    return FormFieldSpec(
        id=_slugify(field.id or field.label),
        label=field.label,
        control=field.field_type,
        name=_slugify(field.id or field.label),
        required=field.required,
        options=[
            FieldOption(
                id=_slugify(f"{field.id or field.label}-{option}"),
                label=option,
                value=option,
                selected=False,
            )
            for option in option_values
        ],
    )


def _table_cells_from_row(
    row: Sequence[str],
    *,
    header: bool,
    row_header: bool,
) -> list[TableCell]:
    cells: list[TableCell] = []
    for index, value in enumerate(row):
        if header:
            cells.append(TableCell(text=value, kind="header", scope="col"))
            continue
        if row_header and index == 0:
            cells.append(TableCell(text=value, kind="header", scope="row"))
            continue
        cells.append(TableCell(text=value, kind="data"))
    return cells


def _infer_callout_tone(node: _CalloutNode) -> str:
    title = (node.title or "").lower()
    if "warning" in title:
        return "warning"
    if "important" in title or "action" in title:
        return "important"
    if "tip" in title:
        return "tip"
    return "note"


def _entry_id(block_id: str, label: str | None, index: int) -> str:
    if label:
        return _slugify(label)
    return _slugify(f"{block_id}-{index}")


def _split_paragraphs(text: str) -> list[str]:
    parts = [part.strip() for part in text.splitlines() if part.strip()]
    return parts or [text.strip() or "Note"]


def _page_start(span: SourceSpan) -> int:
    return span.start_page or span.end_page or 1


def _page_end(span: SourceSpan) -> int:
    return span.end_page or span.start_page or 1


def _canonical_text_placement(hint: PlacementHint | None) -> str:
    if hint == PlacementHint.FOOTER:
        return "footer"
    if hint == PlacementHint.END:
        return "after"
    if hint == PlacementHint.ASIDE:
        return "after"
    return "inline"


def _canonical_callout_placement(hint: PlacementHint | None) -> str:
    if hint == PlacementHint.FOOTER:
        return "footer"
    if hint in {PlacementHint.ASIDE, PlacementHint.END, PlacementHint.PAGE}:
        return "after"
    return "inline"


def _canonical_figure_placement(hint: PlacementHint | None) -> str:
    if hint in {PlacementHint.ASIDE, PlacementHint.PAGE}:
        return "rail"
    if hint == PlacementHint.END:
        return "after"
    if hint == PlacementHint.FOOTER:
        return "footer"
    return "inline"


def _metadata_text(signals: DocumentSignals, key: str) -> str | None:
    value = signals.metadata.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _infer_doc_kind(signals: DocumentSignals, built: _BuiltDocument) -> str:
    explicit = _metadata_text(signals, "doc_kind")
    if explicit in {"scientific_article", "memo", "brochure", "reference_doc", "form_doc", "report"}:
        return explicit

    title = built.title.lower()
    if _contains_block_type(built.nodes, _FormGroupNode):
        return "form_doc"
    if any(keyword in title for keyword in {"memo", "briefing", "bulletin"}):
        return "memo"
    if any(keyword in title for keyword in {"catalog", "catalogue", "brochure", "guidebook"}):
        return "brochure"
    if any(keyword in title for keyword in {"paper", "study", "protocol", "journal"}):
        return "scientific_article"
    if any(keyword in title for keyword in {"reference", "manual", "spec", "appendix"}):
        return "reference_doc"
    return "report"


def _contains_block_type(nodes: Sequence[_Node], block_type: type[Any]) -> bool:
    for node in nodes:
        if isinstance(node, block_type):
            return True
        if isinstance(node, _SectionNode) and _contains_block_type(node.blocks, block_type):
            return True
    return False


def _coerce_source_format(value: str) -> str:
    if value not in {"pdf", "docx", "xlsx"}:
        raise ValueError(f"structured parser v2 does not support source_format={value!r}")
    return value


def merge_source_spans(spans: Sequence[SourceSpan]) -> SourceSpan:
    valid = [span for span in spans if span is not None]
    if not valid:
        return SourceSpan(order_start=0, order_end=0, signal_ids=[])
    start_pages = [page for span in valid if (page := span.start_page) is not None]
    end_pages = [page for span in valid if (page := span.end_page) is not None]
    signal_ids: list[str] = []
    for span in valid:
        for signal_id in span.signal_ids:
            if signal_id not in signal_ids:
                signal_ids.append(signal_id)
    return SourceSpan(
        order_start=min(span.order_start for span in valid),
        order_end=max(span.order_end for span in valid),
        start_page=min(start_pages) if start_pages else None,
        end_page=max(end_pages) if end_pages else None,
        signal_ids=signal_ids,
    )


def source_span_from_signal(signal: RawBlockSignal) -> SourceSpan:
    start_page, end_page = _normalized_page_range(signal.page_start, signal.page_end)
    return SourceSpan(
        order_start=signal.order,
        order_end=signal.order,
        start_page=start_page,
        end_page=end_page,
        signal_ids=[signal.id],
    )


def source_span_from_asset(asset: DocumentAsset) -> SourceSpan:
    return SourceSpan(
        order_start=asset.order,
        order_end=asset.order,
        start_page=asset.page_number,
        end_page=asset.page_number,
        signal_ids=[asset.id],
    )


def _prepare_signals(signals: DocumentSignals) -> list[RawBlockSignal]:
    combined = [*signals.blocks, *signals.liteparse_blocks]
    if not combined:
        return []
    by_key: dict[str, RawBlockSignal] = {}
    ordered_keys: list[str] = []
    for signal in combined:
        key = signal.source_key or signal.id
        if key in by_key:
            by_key[key] = _merge_signals(by_key[key], signal)
            continue
        by_key[key] = signal
        ordered_keys.append(key)
    merged = [by_key[key] for key in ordered_keys]
    return sorted(merged, key=_signal_sort_key)


def _merge_signals(left: RawBlockSignal, right: RawBlockSignal) -> RawBlockSignal:
    preferred, other = _preferred_signal(left, right)
    merged_asset_ids = _dedupe_preserving_order([*preferred.asset_ids, *other.asset_ids])
    merged_fields = preferred.fields or other.fields
    if preferred.field is not None and preferred.field not in merged_fields:
        merged_fields = [preferred.field, *merged_fields]
    if other.field is not None and other.field not in merged_fields:
        merged_fields = [*merged_fields, other.field]
    update: dict[str, Any] = {
        "order": min(left.order, right.order),
        "page_start": _min_page(left.page_start, right.page_start),
        "page_end": _max_page(left.page_end or left.page_start, right.page_end or right.page_start),
        "text": preferred.text or other.text,
        "caption": preferred.caption or other.caption,
        "items": preferred.items or other.items,
        "entries": preferred.entries or other.entries,
        "rows": preferred.rows or other.rows,
        "header_rows": preferred.header_rows or other.header_rows,
        "field": preferred.field or other.field,
        "fields": merged_fields,
        "group_name": preferred.group_name or other.group_name,
        "asset_ids": merged_asset_ids,
        "placement_hint": preferred.placement_hint or other.placement_hint,
        "metadata": {**other.metadata, **preferred.metadata},
    }
    return preferred.model_copy(update=update)


def _preferred_signal(left: RawBlockSignal, right: RawBlockSignal) -> tuple[RawBlockSignal, RawBlockSignal]:
    left_score = (_source_priority(left.source), _kind_priority(left.kind))
    right_score = (_source_priority(right.source), _kind_priority(right.kind))
    if left_score > right_score:
        return left, right
    if right_score > left_score:
        return right, left
    return (left, right) if left.order <= right.order else (right, left)


def _source_priority(source: str) -> int:
    return {
        "semantic_hint": 4,
        "llamaparse": 3,
        "liteparse": 3,
        "extract": 2,
        "asset_manifest": 1,
    }.get(source, 0)


def _kind_priority(kind: str) -> int:
    return {
        "title": 8,
        "heading": 7,
        "table": 6,
        "figure": 6,
        "callout": 6,
        "form_group": 6,
        "footnotes": 6,
        "references": 6,
        "form_field": 5,
        "footnote": 5,
        "reference": 5,
        "list": 4,
        "paragraph": 3,
        "caption": 2,
        "unknown": 1,
    }.get(kind, 0)


def _signal_sort_key(signal: RawBlockSignal) -> tuple[int, int, int, str]:
    page_start = signal.page_start or signal.page_end or 1_000_000
    page_end = signal.page_end or signal.page_start or page_start
    return (signal.order, page_start, page_end, signal.id)


def _is_document_title_signal(
    signal: RawBlockSignal,
    *,
    title: str | None,
    nodes: Sequence[_Node],
    current_section: _SectionNode | None,
) -> bool:
    if signal.kind == "title":
        return True
    return (
        signal.kind == "heading"
        and (signal.level or 1) == 1
        and title is None
        and not nodes
        and current_section is None
    )


def _is_section_heading(signal: RawBlockSignal, *, title: str | None) -> bool:
    if signal.kind != "heading":
        return False
    if (signal.level or 2) >= 2:
        return True
    return title is not None


def _can_buffer(buffer: Sequence[RawBlockSignal], signal: RawBlockSignal) -> bool:
    if not buffer:
        return False
    first = buffer[0]
    if first.kind != signal.kind:
        return False
    if signal.kind == "form_field":
        first_group = first.group_name or ""
        next_group = signal.group_name or ""
        return first_group == next_group
    return signal.kind in {"footnote", "reference"}


def _build_buffer_block(
    buffer: Sequence[RawBlockSignal],
    *,
    seen_ids: set[str],
    used_asset_ids: set[str],
    assets: Sequence[DocumentAsset],
    notices: list[ParserNotice],
) -> _BlockNode | None:
    first = buffer[0]
    if first.kind == "form_field":
        fields: list[FormField] = []
        for signal in buffer:
            if signal.field is not None:
                fields.append(signal.field)
            fields.extend(signal.fields)
        if not fields:
            notices.append(
                ParserNotice(
                    code="form_fields_dropped",
                    message="Dropped form-field signals with no normalized field payloads.",
                    signal_ids=[signal.id for signal in buffer],
                )
            )
            return None
        group_name = next((signal.group_name for signal in buffer if signal.group_name), None)
        if group_name is None:
            notices.append(
                ParserNotice(
                    code="form_group_fallback",
                    message="Grouped contiguous form fields into an unlabeled form group.",
                    signal_ids=[signal.id for signal in buffer],
                )
            )
        return _FormGroupNode(
            id=_unique_id(_slugify(group_name or f"form-group-{first.order}"), seen_ids, prefix="form-group"),
            fields=_dedupe_fields(fields),
            title=group_name,
            description=None,
            source_span=merge_source_spans([source_span_from_signal(signal) for signal in buffer]),
            placement_hint=PlacementHint.INLINE,
        )
    if first.kind == "footnote":
        items = _buffer_entries(buffer)
        return _FootnotesNode(
            id=_unique_id(f"footnotes-{first.order}", seen_ids, prefix="footnotes"),
            items=items,
            source_span=merge_source_spans([source_span_from_signal(signal) for signal in buffer]),
            placement_hint=PlacementHint.FOOTER,
        )
    if first.kind == "reference":
        items = _buffer_entries(buffer)
        return _ReferencesNode(
            id=_unique_id(f"references-{first.order}", seen_ids, prefix="references"),
            items=items,
            source_span=merge_source_spans([source_span_from_signal(signal) for signal in buffer]),
            placement_hint=PlacementHint.END,
        )
    return _signal_to_block(
        first,
        seen_ids=seen_ids,
        used_asset_ids=used_asset_ids,
        assets=assets,
        notices=notices,
    )


def _buffer_entries(buffer: Sequence[RawBlockSignal]) -> list[TextEntry]:
    entries: list[TextEntry] = []
    for signal in buffer:
        entries.extend(_entries_from_signal(signal))
    return entries


def _signal_to_block(
    signal: RawBlockSignal,
    *,
    seen_ids: set[str],
    used_asset_ids: set[str],
    assets: Sequence[DocumentAsset],
    notices: list[ParserNotice],
) -> _BlockNode | None:
    if signal.kind == "paragraph":
        text = _signal_text(signal)
        if not text:
            return None
        return _ParagraphNode(
            id=_unique_id(signal.id, seen_ids, prefix="paragraph"),
            text=text,
            source_span=source_span_from_signal(signal),
            placement_hint=signal.placement_hint or PlacementHint.INLINE,
        )
    if signal.kind == "list":
        items = signal.items or _split_list_items(signal.text)
        if not items:
            return None
        return _ListNode(
            id=_unique_id(signal.id, seen_ids, prefix="list"),
            items=items,
            ordered=signal.ordered,
            source_span=source_span_from_signal(signal),
            placement_hint=signal.placement_hint or PlacementHint.INLINE,
        )
    if signal.kind == "table":
        if not signal.rows:
            notices.append(
                ParserNotice(
                    code="table_fallback",
                    message="Table signal had no row grid; preserved it as paragraph text.",
                    signal_ids=[signal.id],
                )
            )
            text = _signal_text(signal)
            if not text:
                return None
            return _ParagraphNode(
                id=_unique_id(signal.id, seen_ids, prefix="paragraph"),
                text=text,
                source_span=source_span_from_signal(signal),
                placement_hint=signal.placement_hint or PlacementHint.INLINE,
            )
        return _TableNode(
            id=_unique_id(signal.id, seen_ids, prefix="table"),
            rows=signal.rows,
            header_rows=signal.header_rows,
            caption=signal.caption,
            source_span=source_span_from_signal(signal),
            placement_hint=signal.placement_hint or PlacementHint.INLINE,
        )
    if signal.kind == "figure":
        asset_ids = _resolve_figure_asset_ids(
            signal=signal,
            assets=assets,
            used_asset_ids=used_asset_ids,
            notices=notices,
        )
        return _FigureNode(
            id=_unique_id(signal.id, seen_ids, prefix="figure"),
            asset_ids=asset_ids,
            caption=signal.caption or signal.text,
            source_span=source_span_from_signal(signal),
            placement_hint=_figure_placement(signal, assets, asset_ids),
        )
    if signal.kind == "callout":
        text = _signal_text(signal)
        if not text:
            return None
        return _CalloutNode(
            id=_unique_id(signal.id, seen_ids, prefix="callout"),
            text=text,
            title=signal.group_name or _metadata_title(signal),
            source_span=source_span_from_signal(signal),
            placement_hint=signal.placement_hint or PlacementHint.ASIDE,
        )
    if signal.kind == "footnotes":
        return _FootnotesNode(
            id=_unique_id(signal.id, seen_ids, prefix="footnotes"),
            items=_entries_from_signal(signal),
            source_span=source_span_from_signal(signal),
            placement_hint=signal.placement_hint or PlacementHint.FOOTER,
        )
    if signal.kind == "references":
        return _ReferencesNode(
            id=_unique_id(signal.id, seen_ids, prefix="references"),
            items=_entries_from_signal(signal),
            source_span=source_span_from_signal(signal),
            placement_hint=signal.placement_hint or PlacementHint.END,
        )
    if signal.kind == "form_group":
        fields = signal.fields or ([signal.field] if signal.field is not None else [])
        if not fields:
            notices.append(
                ParserNotice(
                    code="form_group_fallback",
                    message="Form-group signal had no fields; preserved it as paragraph text.",
                    signal_ids=[signal.id],
                )
            )
            text = _signal_text(signal)
            if not text:
                return None
            return _ParagraphNode(
                id=_unique_id(signal.id, seen_ids, prefix="paragraph"),
                text=text,
                source_span=source_span_from_signal(signal),
                placement_hint=signal.placement_hint or PlacementHint.INLINE,
            )
        return _FormGroupNode(
            id=_unique_id(signal.id, seen_ids, prefix="form-group"),
            fields=_dedupe_fields(fields),
            title=signal.group_name or _metadata_title(signal),
            description=signal.text,
            source_span=source_span_from_signal(signal),
            placement_hint=signal.placement_hint or PlacementHint.INLINE,
        )
    if signal.kind == "unknown":
        return _unknown_signal_to_block(
            signal,
            seen_ids=seen_ids,
            used_asset_ids=used_asset_ids,
            assets=assets,
            notices=notices,
        )
    if signal.kind == "caption":
        notices.append(
            ParserNotice(
                code="caption_fallback",
                message="Standalone caption signal was preserved as paragraph text.",
                signal_ids=[signal.id],
            )
        )
        text = _signal_text(signal)
        if not text:
            return None
        return _ParagraphNode(
            id=_unique_id(signal.id, seen_ids, prefix="paragraph"),
            text=text,
            source_span=source_span_from_signal(signal),
            placement_hint=signal.placement_hint or PlacementHint.INLINE,
        )
    return None


def _unknown_signal_to_block(
    signal: RawBlockSignal,
    *,
    seen_ids: set[str],
    used_asset_ids: set[str],
    assets: Sequence[DocumentAsset],
    notices: list[ParserNotice],
) -> _BlockNode | None:
    if signal.rows:
        return _TableNode(
            id=_unique_id(signal.id, seen_ids, prefix="table"),
            rows=signal.rows,
            header_rows=signal.header_rows,
            caption=signal.caption,
            source_span=source_span_from_signal(signal),
            placement_hint=signal.placement_hint or PlacementHint.INLINE,
        )
    if signal.field is not None or signal.fields:
        return _FormGroupNode(
            id=_unique_id(signal.id, seen_ids, prefix="form-group"),
            fields=_dedupe_fields(signal.fields or ([signal.field] if signal.field is not None else [])),
            title=signal.group_name or _metadata_title(signal),
            description=signal.text,
            source_span=source_span_from_signal(signal),
            placement_hint=signal.placement_hint or PlacementHint.INLINE,
        )
    if signal.asset_ids:
        return _FigureNode(
            id=_unique_id(signal.id, seen_ids, prefix="figure"),
            asset_ids=_resolve_figure_asset_ids(
                signal=signal,
                assets=assets,
                used_asset_ids=used_asset_ids,
                notices=notices,
            ),
            caption=signal.caption or signal.text,
            source_span=source_span_from_signal(signal),
            placement_hint=_figure_placement(signal, assets, signal.asset_ids),
        )
    if signal.placement_hint == PlacementHint.ASIDE and _signal_text(signal):
        return _CalloutNode(
            id=_unique_id(signal.id, seen_ids, prefix="callout"),
            text=_signal_text(signal),
            title=_metadata_title(signal),
            source_span=source_span_from_signal(signal),
            placement_hint=PlacementHint.ASIDE,
        )
    text = _signal_text(signal)
    if not text:
        return None
    notices.append(
        ParserNotice(
            code="unknown_block_fallback",
            message="Unknown signal kind was preserved as a paragraph block.",
            signal_ids=[signal.id],
        )
    )
    return _ParagraphNode(
        id=_unique_id(signal.id, seen_ids, prefix="paragraph"),
        text=text,
        source_span=source_span_from_signal(signal),
        placement_hint=signal.placement_hint or PlacementHint.INLINE,
    )


def _resolve_figure_asset_ids(
    *,
    signal: RawBlockSignal,
    assets: Sequence[DocumentAsset],
    used_asset_ids: set[str],
    notices: list[ParserNotice],
) -> list[str]:
    explicit = [_normalize_asset_id(asset_id) for asset_id in signal.asset_ids if asset_id]
    explicit = [asset_id for asset_id in explicit if asset_id]
    for asset_id in explicit:
        used_asset_ids.add(asset_id)
    if explicit:
        return explicit

    start_page, end_page = _normalized_page_range(signal.page_start, signal.page_end)
    matching = [
        asset
        for asset in assets
        if asset.id not in used_asset_ids
        and (
            start_page is None
            or asset.page_number is None
            or (asset.page_number >= start_page and (end_page is None or asset.page_number <= end_page))
        )
    ]
    if not matching:
        notices.append(
            ParserNotice(
                code="figure_missing_asset",
                message="Figure block could not be linked to any extracted asset id.",
                signal_ids=[signal.id],
            )
        )
        return []

    chosen = matching[0]
    used_asset_ids.add(chosen.id)
    notices.append(
        ParserNotice(
            code="figure_asset_fallback",
            message="Figure block used the nearest unmatched asset on the same page span.",
            signal_ids=[signal.id, chosen.id],
        )
    )
    return [chosen.id]


def _figure_placement(
    signal: RawBlockSignal,
    assets: Sequence[DocumentAsset],
    asset_ids: Sequence[str],
) -> PlacementHint:
    if signal.placement_hint is not None:
        return signal.placement_hint
    page_raster_ids = {asset.id for asset in assets if asset.kind == "page_raster"}
    return PlacementHint.PAGE if any(asset_id in page_raster_ids for asset_id in asset_ids) else PlacementHint.INLINE


def _append_orphan_asset_figures(
    nodes: list[_Node],
    *,
    assets: Sequence[DocumentAsset],
    used_asset_ids: set[str],
    seen_ids: set[str],
    notices: list[ParserNotice],
) -> None:
    for asset in assets:
        if asset.id in used_asset_ids:
            continue
        figure = _FigureNode(
            id=_unique_id(f"figure-{asset.id}", seen_ids, prefix="figure"),
            asset_ids=[asset.id],
            caption=None,
            source_span=source_span_from_asset(asset),
            placement_hint=PlacementHint.PAGE if asset.kind == "page_raster" else PlacementHint.INLINE,
        )
        target = _find_asset_target(nodes, asset.page_number)
        if isinstance(target, _SectionNode):
            target.blocks.append(figure)
            target.source_span = merge_source_spans([target.source_span, figure.source_span])
        else:
            nodes.append(figure)
        used_asset_ids.add(asset.id)
        notices.append(
            ParserNotice(
                code="orphan_asset_figure",
                message="Unclaimed extracted asset was appended as a standalone figure block.",
                signal_ids=[asset.id],
            )
        )


def _find_asset_target(nodes: Sequence[_Node], page_number: int | None) -> _SectionNode | None:
    if page_number is None:
        for node in reversed(nodes):
            if isinstance(node, _SectionNode):
                return node
        return None
    for node in reversed(nodes):
        if not isinstance(node, _SectionNode):
            continue
        start_page = node.source_span.start_page
        end_page = node.source_span.end_page
        if start_page is None or end_page is None:
            continue
        if start_page <= page_number <= end_page:
            return node
    return None


def _entries_from_signal(signal: RawBlockSignal) -> list[TextEntry]:
    if signal.entries:
        return [entry.model_copy() for entry in signal.entries]
    if signal.items:
        return [_parse_text_entry(item) for item in signal.items if item.strip()]
    if signal.text:
        return [_parse_text_entry(signal.text)]
    return []


def _signal_text(signal: RawBlockSignal) -> str:
    if signal.text and signal.text.strip():
        return signal.text.strip()
    if signal.entries:
        return "\n".join(entry.text for entry in signal.entries if entry.text.strip())
    if signal.items:
        return "\n".join(item.strip() for item in signal.items if item.strip())
    return ""


def _parse_text_entry(text: str) -> TextEntry:
    stripped = text.strip()
    match = _LABELLED_ENTRY_RE.match(stripped)
    if not match:
        return TextEntry(text=stripped)
    label = match.group("bracket") or match.group("plain")
    body = match.group("body").strip()
    return TextEntry(text=body, label=label.strip() if label else None)


def _split_list_items(text: str | None) -> list[str]:
    if not text:
        return []
    items: list[str] = []
    for line in text.splitlines():
        cleaned = line.strip().lstrip("-*").strip()
        if cleaned:
            items.append(cleaned)
    return items


def _metadata_title(signal: RawBlockSignal) -> str | None:
    value = signal.metadata.get("title") or signal.metadata.get("label")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _dedupe_fields(fields: Sequence[FormField]) -> list[FormField]:
    seen: set[str] = set()
    deduped: list[FormField] = []
    for field in fields:
        key = field.id or field.label
        if key in seen:
            continue
        seen.add(key)
        deduped.append(field)
    return deduped


def _document_source_span(nodes: Sequence[_Node], assets: Sequence[DocumentAsset]) -> SourceSpan:
    spans = [node.source_span for node in nodes]
    if not spans and assets:
        spans = [source_span_from_asset(asset) for asset in assets]
    return merge_source_spans(spans)


def _infer_page_count(nodes: Sequence[_Node], assets: Sequence[DocumentAsset]) -> int:
    pages = [asset.page_number for asset in assets if asset.page_number is not None]
    for node in nodes:
        if node.source_span.end_page is not None:
            pages.append(node.source_span.end_page)
    return max(pages) if pages else 1


def _slugify(value: str) -> str:
    lowered = value.lower()
    slug = _NON_ALNUM_RE.sub("-", lowered).strip("-")
    return slug or "node"


def _unique_id(candidate: str, seen_ids: set[str], *, prefix: str) -> str:
    base = _slugify(candidate or prefix)
    if base not in seen_ids:
        seen_ids.add(base)
        return base
    index = 2
    while f"{base}-{index}" in seen_ids:
        index += 1
    unique = f"{base}-{index}"
    seen_ids.add(unique)
    return unique


def _dedupe_preserving_order(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = _normalize_asset_id(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _normalize_asset_id(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""
    return Path(candidate).stem or candidate


def _normalized_page_range(
    start_page: int | None,
    end_page: int | None,
) -> tuple[int | None, int | None]:
    if start_page is None and end_page is None:
        return None, None
    if start_page is None:
        return end_page, end_page
    if end_page is None:
        return start_page, start_page
    return (start_page, end_page) if start_page <= end_page else (end_page, start_page)


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
