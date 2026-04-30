from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from html import escape
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from backend.app.a11y import check_html
from backend.app.layout_plan_v2 import LayoutPlan
from backend.app.structured_parser import (
    FootnotesBlock,
    ParsedDocument,
    ReferenceListBlock,
    SectionBlock,
)


@dataclass
class RenderedDocument:
    html: str
    outline: list[dict[str, Any]]
    title: str
    description: str
    language: str
    page_count: int
    render_mode: str = "static"


@dataclass
class _RenderContext:
    used_ids: set[str]
    asset_lookup: dict[str, Any]
    layout_by_section: dict[str, str]
    footnote_ref_ids: dict[str, list[str]]
    document_form_wrapper: bool = False


_INLINE_TEXT_KEYS = ("text", "value", "label", "title", "content")
_VOID_TAGS = {"img", "input"}
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_FIGURE_RAIL_KINDS = {"figure", "callout"}
_CALL_OUT_KINDS = {"callout", "sidebar", "note", "pull_quote", "pull-quote"}
_REFERENCE_KINDS = {"references", "reference_list", "reference-list", "bibliography"}
_FOOTNOTE_KINDS = {"footnotes", "footnote_list", "footnote-list"}
_LIST_KINDS = {"list", "bullet_list", "bullet-list", "ordered_list", "ordered-list"}


def render_document(
    parsed: ParsedDocument | Mapping[str, Any] | Any,
    layout: LayoutPlan | Mapping[str, Any] | Any,
) -> RenderedDocument:
    parsed_doc = _normalize_parsed_document(parsed)
    layout_plan = _normalize_layout_plan(layout)

    parsed_hero = _to_mapping(parsed_doc.get("hero"))
    layout_hero = _to_mapping(layout_plan.get("hero"))
    title = (
        _coerce_text(layout_hero.get("title"))
        or _coerce_text(parsed_hero.get("title"))
        or _coerce_text(parsed_doc.get("title"))
        or "Document"
    )
    language = (
        _coerce_text(layout_hero.get("language"))
        or _coerce_text(parsed_hero.get("language"))
        or _coerce_text(parsed_doc.get("language"))
        or "en"
    )
    description = _coerce_text(parsed_doc.get("description")) or _coerce_text(parsed_doc.get("summary"))
    heading_tree = _coerce_heading_tree(parsed_doc, title)

    document_form_wrapper = _coerce_text(parsed_doc.get("doc_kind")) == "form_doc"
    ctx = _RenderContext(
        used_ids={"content", "document-title", "doc-toc-title", "doc-references-label", "doc-footnotes-label"},
        asset_lookup=_build_asset_lookup(_listify(parsed_doc.get("assets"))),
        layout_by_section={
            _coerce_text(_get(item, "section_id")): _coerce_text(_get(item, "layout")) or "single-column"
            for item in _listify(layout_plan.get("section_layouts"))
            if _coerce_text(_get(item, "section_id"))
        },
        footnote_ref_ids={},
        document_form_wrapper=document_form_wrapper,
    )

    outline = [{"id": "document-title", "level": 1, "text": title, "parent": None}]
    sections_by_key = _index_sections(_listify(parsed_doc.get("sections")))

    section_html: list[str] = []
    for node in heading_tree:
        rendered_sections, rendered_outline = _render_heading_node(
            node=node,
            depth=0,
            parent_id=None,
            ctx=ctx,
            sections_by_key=sections_by_key,
        )
        section_html.extend(rendered_sections)
        outline.extend(rendered_outline)

    hero_metadata = _listify(layout_plan.get("hero_metadata")) or _listify(parsed_doc.get("metadata"))
    lead_value = layout_plan.get("lead") if layout_plan.get("lead") is not None else parsed_doc.get("lead")
    toc_html = (
        _render_toc(outline[1:])
        if not document_form_wrapper and bool(layout_plan.get("toc_enabled", True)) and len(outline) > 2
        else ""
    )
    references_html = _render_references(_listify(parsed_doc.get("references")))
    footnotes_html = _render_footnotes(_listify(parsed_doc.get("footnotes")), ctx)
    body_chunks = [html for html in (*section_html, references_html, footnotes_html) if html]
    body_html = "".join(body_chunks)
    if document_form_wrapper:
        body_html = _tag("form", body_html, **{"class": "doc-form doc-form--document"})

    html = _tag(
        "main",
        "".join(
            [
                _tag(
                    "a",
                    "Skip to main content",
                    href="#content",
                    **{"class": "sr-skip"},
                ),
                _render_hero(
                    title=title,
                    kicker=_coerce_text(layout_hero.get("kicker"))
                    or _coerce_text(parsed_hero.get("kicker"))
                    or _coerce_text(parsed_doc.get("kicker")),
                    subtitle=_coerce_text(layout_hero.get("subtitle"))
                    or _coerce_text(parsed_hero.get("subtitle"))
                    or _coerce_text(parsed_doc.get("subtitle")),
                    summary=_coerce_text(layout_hero.get("summary"))
                    or _coerce_text(parsed_hero.get("summary"))
                    or _coerce_text(parsed_doc.get("summary"))
                    or _coerce_text(parsed_doc.get("description")),
                    lead=lead_value,
                    metadata=hero_metadata,
                    ctx=ctx,
                    hero_style=_coerce_text(layout_plan.get("hero_style")) or "compact",
                ),
                toc_html,
                _tag("div", body_html, **{"class": "doc-page__body"}),
            ]
        ),
        id="content",
        lang=language,
        **{
            "class": " ".join(
                part for part in [
                    "doc-page",
                    f"doc-page--{_slugify(_coerce_text(layout_plan.get('template')) or 'reference')}",
                    "doc-page--facsimile" if document_form_wrapper else "",
                    f"doc-page--density-{_slugify(_coerce_text(layout_plan.get('visual_density')) or 'comfortable')}",
                ] if part
            )
        },
    )

    errors = check_html(html)
    if errors:
        raise ValueError(f"render_v2 produced invalid HTML: {'; '.join(errors)}")

    return RenderedDocument(
        html=html,
        outline=outline,
        title=title,
        description=description,
        language=language,
        page_count=max(1, int(parsed_doc.get("page_count") or 1)),
    )


def render_html(
    parsed: ParsedDocument | Mapping[str, Any] | Any,
    layout: LayoutPlan | Mapping[str, Any] | Any,
) -> str:
    return render_document(parsed, layout).html


def _normalize_parsed_document(parsed: ParsedDocument | Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(parsed, ParsedDocument):
        return _normalize_canonical_parsed_document(parsed)
    payload = _to_mapping(parsed)
    if payload and "body" in payload:
        canonical_keys = {
            "schema_version",
            "source_format",
            "title",
            "kicker",
            "subtitle",
            "summary",
            "language",
            "page_count",
            "doc_kind",
            "body",
        }
        canonical = ParsedDocument.model_validate(
            {key: payload[key] for key in canonical_keys if key in payload}
        )
        normalized = _normalize_canonical_parsed_document(canonical)
        if "assets" in payload:
            normalized["assets"] = _listify(payload.get("assets"))
        if "metadata" in payload:
            normalized["metadata"] = _listify(payload.get("metadata"))
        return normalized
    return payload


def _normalize_layout_plan(layout: LayoutPlan | Mapping[str, Any] | Any) -> dict[str, Any]:
    if isinstance(layout, LayoutPlan):
        return _normalize_layout_plan_v2(layout)
    payload = _to_mapping(layout)
    if payload.get("version") == "v2" and "sections" in payload and "section_layouts" not in payload:
        return _normalize_layout_plan_v2(LayoutPlan.model_validate(payload))
    return payload


def _normalize_canonical_parsed_document(parsed: ParsedDocument) -> dict[str, Any]:
    opening_blocks: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    references: list[Any] = []
    footnotes: list[dict[str, Any]] = []

    for node in parsed.body:
        if isinstance(node, SectionBlock):
            sections.append(_normalize_canonical_section(node, references=references, footnotes=footnotes))
            continue
        if isinstance(node, FootnotesBlock):
            footnotes.extend(_normalize_canonical_footnotes(node))
            continue
        if isinstance(node, ReferenceListBlock):
            references.extend(_normalize_canonical_references(node))
            continue
        opening_blocks.append(_normalize_canonical_block(node))

    if opening_blocks:
        if sections:
            first_section = dict(sections[0])
            first_section["blocks"] = [*opening_blocks, *_listify(first_section.get("blocks"))]
            sections[0] = first_section
        else:
            sections.append({
                "id": "document-body",
                "heading": "Document Body",
                "blocks": opening_blocks,
            })

    return {
        "title": parsed.title,
        "kicker": parsed.kicker,
        "subtitle": parsed.subtitle,
        "summary": parsed.summary,
        "language": parsed.language,
        "page_count": parsed.page_count,
        "doc_kind": parsed.doc_kind,
        "sections": sections,
        "references": references,
        "footnotes": footnotes,
    }


def _normalize_canonical_section(
    section: SectionBlock,
    *,
    references: list[Any],
    footnotes: list[dict[str, Any]],
) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    children: list[dict[str, Any]] = []
    for block in section.blocks:
        if isinstance(block, FootnotesBlock):
            footnotes.extend(_normalize_canonical_footnotes(block))
            continue
        if isinstance(block, ReferenceListBlock):
            references.extend(_normalize_canonical_references(block))
            continue
        if isinstance(block, SectionBlock):
            children.append(
                _normalize_canonical_section(block, references=references, footnotes=footnotes)
            )
            continue
        blocks.append(_normalize_canonical_block(block))
    payload = {
        "id": section.id,
        "heading": section.heading,
        "caption": section.caption,
        "blocks": blocks,
    }
    if children:
        payload["children"] = children
    return payload


def _normalize_canonical_block(block: Any) -> dict[str, Any]:
    payload = _to_mapping(block)
    kind = _canonical_block_kind(payload)

    if kind == "figure":
        asset_ids = [_coerce_text(_get(payload, "asset_id"))] if _coerce_text(_get(payload, "asset_id")) else []
        if asset_ids:
            payload["asset_ids"] = asset_ids
            payload.setdefault("asset_id", asset_ids[0])
    elif kind == "form_group":
        payload["type"] = "form_group"
        payload["fields"] = [
            _normalize_canonical_field(field)
            for field in _listify(_get(payload, "fields"))
        ]
    elif kind == "table":
        payload["head"] = _listify(_get(payload, "header_rows"))
        payload["body"] = _listify(_get(payload, "body_rows"))
    elif kind == "callout":
        payload["text"] = _listify(_get(payload, "body"))
    return payload


def _normalize_canonical_field(field: Any) -> dict[str, Any]:
    payload = _to_mapping(field)
    field_type = _coerce_text(_get(payload, "control", "field_type", "type")) or "text"
    payload["type"] = field_type
    if field_type in {"checkbox", "radio", "select"}:
        payload["options"] = [
            {
                "label": _coerce_text(_get(option, "label", "text", default=option)) or _coerce_text(option),
                "value": _coerce_text(_get(option, "value", default=option)) or _coerce_text(option),
                "selected": bool(_get(option, "selected")),
            }
            for option in _listify(_get(payload, "options"))
            if _coerce_text(_get(option, "label", "text", default=option)) or _coerce_text(option)
        ]
    return payload


def _normalize_canonical_references(block: ReferenceListBlock) -> list[Any]:
    return [
        _normalize_canonical_text_entry(item)
        for item in block.entries
        if _coerce_text(_get(item, "text"))
    ]


def _normalize_canonical_footnotes(block: FootnotesBlock) -> list[dict[str, Any]]:
    return [
        {
            "id": _coerce_text(_get(item, "id")) or f"{block.id}-{index}",
            "label": _coerce_text(_get(item, "label")) or str(index),
            "text": _coerce_text(_get(item, "text")),
        }
        for index, item in enumerate(block.entries, start=1)
        if _coerce_text(_get(item, "text"))
    ]


def _normalize_canonical_text_entry(entry: Any) -> Any:
    label = _coerce_text(_get(entry, "label"))
    text = _coerce_text(_get(entry, "text"))
    href = _coerce_text(_get(entry, "href"))
    if href:
        return {
            "href": href,
            "text": f"{label} {text}".strip() if label else text,
        }
    if label:
        return f"{label} {text}".strip()
    return text


def _normalize_layout_plan_v2(layout: LayoutPlan) -> dict[str, Any]:
    return {
        "template": _template_for_layout_v2(layout),
        "hero_style": _hero_style_for_layout_v2(layout.hero_treatment),
        "toc_enabled": layout.toc_policy != "hidden",
        "visual_density": layout.density,
        "section_layouts": [
            {
                "section_id": section.section_id,
                "layout": _section_layout_for_layout_v2(section),
            }
            for section in layout.sections
        ],
    }


def _template_for_layout_v2(layout: LayoutPlan) -> str:
    containers = {section.container for section in layout.sections}
    if "feature-stack" in containers:
        return "brochure"
    if "form-stack" in containers:
        return "form"
    if layout.hero_treatment == "summary":
        return "article"
    return "reference"


def _hero_style_for_layout_v2(hero_treatment: str) -> str:
    return {
        "title-only": "compact",
        "summary": "article",
        "feature": "immersive",
        "compact": "compact",
    }.get(hero_treatment, "compact")


def _section_layout_for_layout_v2(section: Any) -> str:
    container = _coerce_text(_get(section, "container"))
    if container == "feature-stack":
        return "feature-figure"
    if container == "flow-with-rail":
        rail_types = {
            _coerce_text(_get(block, "block_type"))
            for block in _listify(_get(section, "blocks"))
            if _coerce_text(_get(block, "placement")) == "rail"
        }
        if rail_types and rail_types <= {"callout"}:
            return "sidebar-callout"
        return "split-figure-left" if _coerce_text(_get(section, "rail_side")) == "left" else "split-figure-right"
    if container in {"table-stack", "appendix"}:
        return "dense-reference"
    return "single-column"


def _to_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _get(value: Any, *keys: str, default: Any = None) -> Any:
    if value is None:
        return default
    mapping = value if isinstance(value, Mapping) else None
    for key in keys:
        if mapping is not None and key in mapping:
            return mapping[key]
        if hasattr(value, key):
            return getattr(value, key)
    return default


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "".join(_coerce_text(item) for item in value).strip()
    for key in _INLINE_TEXT_KEYS:
        candidate = _get(value, key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return slug or "section"


def _unique_id(raw: str, *, used_ids: set[str]) -> str:
    base = _slugify(raw)
    candidate = base
    counter = 2
    while candidate in used_ids:
        candidate = f"{base}-{counter}"
        counter += 1
    used_ids.add(candidate)
    return candidate


def _attrs(**attrs: Any) -> str:
    rendered: list[str] = []
    for key, value in attrs.items():
        if value is None or value is False:
            continue
        if isinstance(value, (list, tuple, set)):
            value = " ".join(str(item) for item in value if item)
        if value is True:
            rendered.append(key)
            continue
        rendered.append(f'{key}="{escape(str(value), quote=True)}"')
    return (" " + " ".join(rendered)) if rendered else ""


def _tag(tag_name: str, content: str = "", **attrs: Any) -> str:
    attr_text = _attrs(**attrs)
    if tag_name in _VOID_TAGS:
        return f"<{tag_name}{attr_text} />"
    return f"<{tag_name}{attr_text}>{content}</{tag_name}>"


def _render_hero(
    *,
    title: str,
    kicker: str,
    subtitle: str,
    summary: str,
    lead: Any,
    metadata: list[Any],
    ctx: _RenderContext,
    hero_style: str,
) -> str:
    parts: list[str] = []
    if kicker:
        parts.append(_tag("p", escape(kicker), **{"class": "doc-hero__kicker"}))
    parts.append(
        _tag("h1", escape(title), id="document-title", **{"class": "doc-hero__title"})
    )
    if subtitle:
        parts.append(_tag("p", escape(subtitle), **{"class": "doc-hero__subtitle"}))
    if summary:
        parts.append(_tag("p", escape(summary), **{"class": "doc-hero__summary"}))
    meta_html = _render_metadata(metadata)
    if meta_html:
        parts.append(meta_html)
    lead_html = _render_lead(lead, ctx)
    if lead_html:
        parts.append(lead_html)
    return _tag(
        "header",
        "".join(parts),
        **{"class": f"doc-hero doc-hero--{_slugify(hero_style or 'compact')}"},
    )


def _render_metadata(items: list[Any]) -> str:
    pairs: list[str] = []
    for item in items:
        label = _coerce_text(_get(item, "label", "name", "key"))
        value = _coerce_text(_get(item, "value", "text", "content"))
        if not label and not value:
            text = _coerce_text(item)
            if text:
                pairs.append(
                    _tag(
                        "div",
                        _tag("dt", "Detail") + _tag("dd", escape(text)),
                    )
                )
            continue
        if not label or not value:
            continue
        pairs.append(
            _tag(
                "div",
                _tag("dt", escape(label), **{"class": "doc-hero__meta-label"})
                + _tag("dd", escape(value), **{"class": "doc-hero__meta-value"}),
            )
        )
    if not pairs:
        return ""
    return _tag("dl", "".join(pairs), **{"class": "doc-hero__meta"})


def _render_lead(value: Any, ctx: _RenderContext) -> str:
    paragraphs = _coerce_paragraph_values(value)
    if not paragraphs:
        return ""
    body = "".join(
        _tag("p", _render_inline(paragraph, ctx)) for paragraph in paragraphs
    )
    return _tag("div", body, **{"class": "doc-lede"})


def _coerce_heading_tree(parsed: Mapping[str, Any], title: str) -> list[Any]:
    heading_tree = _listify(parsed.get("heading_tree"))
    if not heading_tree:
        return _derive_heading_tree_from_sections(_listify(parsed.get("sections")))
    if len(heading_tree) == 1:
        root = heading_tree[0]
        if _slugify(_coerce_text(_get(root, "text", "heading", "title"))) == _slugify(title):
            children = _listify(_get(root, "children"))
            if children:
                return children
    return heading_tree


def _derive_heading_tree_from_sections(sections: list[Any]) -> list[Any]:
    derived: list[Any] = []
    for section in sections:
        text = _coerce_text(_get(section, "heading", "title", "text"))
        if not text:
            continue
        derived.append(
            {
                "id": _get(section, "id"),
                "text": text,
                "children": _derive_heading_tree_from_sections(_listify(_get(section, "children"))),
            }
        )
    return derived


def _index_sections(sections: list[Any]) -> dict[str, Any]:
    indexed: dict[str, Any] = {}

    def walk(section: Any) -> None:
        section_id = _coerce_text(_get(section, "id"))
        heading = _coerce_text(_get(section, "heading", "title", "text"))
        if section_id:
            indexed[section_id] = section
        if heading:
            indexed.setdefault(_slugify(heading), section)
        for child in _listify(_get(section, "children")):
            walk(child)

    for section in sections:
        walk(section)
    return indexed


def _render_heading_node(
    *,
    node: Any,
    depth: int,
    parent_id: str | None,
    ctx: _RenderContext,
    sections_by_key: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    heading_text = _coerce_text(_get(node, "text", "heading", "title")) or "Section"
    source = _resolve_section_source(node, sections_by_key)
    raw_id = (
        _coerce_text(_get(node, "id", "section_id"))
        or _coerce_text(_get(source, "id"))
        or heading_text
    )
    section_id = _unique_id(raw_id, used_ids=ctx.used_ids)
    heading_level = min(depth + 2, 6)

    section_html = _render_section(
        section_id=section_id,
        heading_text=heading_text,
        heading_level=heading_level,
        section=source,
        ctx=ctx,
    )
    outline = [
        {
            "id": section_id,
            "level": heading_level,
            "text": heading_text,
            "parent": parent_id,
        }
    ]

    children_html: list[str] = [section_html]
    for child in _listify(_get(node, "children")):
        child_html, child_outline = _render_heading_node(
            node=child,
            depth=depth + 1,
            parent_id=section_id,
            ctx=ctx,
            sections_by_key=sections_by_key,
        )
        children_html.extend(child_html)
        outline.extend(child_outline)
    return children_html, outline


def _resolve_section_source(node: Any, sections_by_key: dict[str, Any]) -> Any:
    section_id = _coerce_text(_get(node, "section_id", "id"))
    if section_id and section_id in sections_by_key:
        return sections_by_key[section_id]
    heading_text = _coerce_text(_get(node, "text", "heading", "title"))
    slug = _slugify(heading_text) if heading_text else ""
    if slug and slug in sections_by_key:
        return sections_by_key[slug]
    return node


def _render_section(
    *,
    section_id: str,
    heading_text: str,
    heading_level: int,
    section: Any,
    ctx: _RenderContext,
) -> str:
    layout = ctx.layout_by_section.get(section_id) or ctx.layout_by_section.get(
        _coerce_text(_get(section, "id"))
    )
    layout = layout or "single-column"
    layout_class = _slugify(layout)

    intro_html = "".join(
        [
            _tag("p", escape(caption), **{"class": "doc-section__caption"})
            for caption in [
                _coerce_text(_get(section, "caption")),
                _coerce_text(_get(section, "summary")),
            ]
            if caption
        ]
    )
    lead_html = _render_section_lead(section, ctx)
    blocks = _collect_section_blocks(section)

    main_parts: list[str] = []
    rail_parts: list[str] = []
    feature_parts: list[str] = []

    for block in blocks:
        block_kind = _canonical_block_kind(block)
        if block_kind in _FOOTNOTE_KINDS:
            continue
        html = _render_block(
            block=block,
            ctx=ctx,
            section_id=section_id,
            section_heading=heading_text,
        )
        if not html:
            continue
        if layout_class in {"split-figure-right", "split-figure-left"} and block_kind in _FIGURE_RAIL_KINDS:
            rail_parts.append(html)
        elif layout_class == "sidebar-callout" and block_kind in _CALL_OUT_KINDS:
            rail_parts.append(html)
        elif layout_class == "feature-figure" and block_kind == "figure":
            feature_parts.append(html)
        else:
            main_parts.append(html)

    heading_tag = f"h{heading_level}"
    header_html = _tag(
        "header",
        _tag(heading_tag, escape(heading_text), id=section_id)
        + intro_html
        + lead_html,
        **{"class": "doc-section__header"},
    )
    body_html = _render_section_body(
        layout=layout_class,
        main_parts=main_parts,
        rail_parts=rail_parts,
        feature_parts=feature_parts,
    )
    return _tag(
        "section",
        header_html + body_html,
        id=section_id,
        **{"class": f"doc-section doc-section--{layout_class}"},
    )


def _render_section_body(
    *,
    layout: str,
    main_parts: list[str],
    rail_parts: list[str],
    feature_parts: list[str],
) -> str:
    if layout == "feature-figure":
        return "".join(
            [
                _tag("div", "".join(feature_parts), **{"class": "doc-section__feature"}) if feature_parts else "",
                _tag("div", "".join(main_parts + rail_parts), **{"class": "doc-section__stack"}),
            ]
        )
    if layout in {"split-figure-right", "split-figure-left", "sidebar-callout"} and rail_parts:
        rail = _tag(
            "aside",
            "".join(rail_parts),
            **{"class": "doc-section__rail", "aria-label": "Supporting content"},
        )
        main = _tag("div", "".join(main_parts), **{"class": "doc-section__main"})
        grid_children = main + rail if layout != "split-figure-left" else rail + main
        return _tag("div", grid_children, **{"class": "doc-section__grid"})
    return _tag("div", "".join(main_parts + rail_parts), **{"class": "doc-section__stack"})


def _render_section_lead(section: Any, ctx: _RenderContext) -> str:
    lead_value = _get(section, "lead", "intro")
    paragraphs = _coerce_paragraph_values(lead_value)
    if not paragraphs:
        return ""
    content = "".join(
        _tag("p", _render_inline(paragraph, ctx)) for paragraph in paragraphs
    )
    return _tag("div", content, **{"class": "doc-section__intro"})


def _collect_section_blocks(section: Any) -> list[Any]:
    explicit_blocks = _listify(_get(section, "blocks"))
    if explicit_blocks:
        return explicit_blocks

    blocks: list[Any] = []
    for paragraph in _listify(_get(section, "paragraphs")):
        blocks.append({"kind": "paragraph", "text": paragraph})

    text_value = _get(section, "text", "body_text")
    if text_value:
        blocks.append({"kind": "paragraph", "text": text_value})

    for item in _listify(_get(section, "lists")):
        block = _to_mapping(item)
        block.setdefault("kind", "list")
        blocks.append(block)
    for key, kind in (
        ("figures", "figure"),
        ("callouts", "callout"),
        ("tables", "table"),
        ("forms", "form"),
        ("references", "references"),
        ("footnotes", "footnotes"),
        ("quotes", "quote"),
    ):
        for item in _listify(_get(section, key)):
            block = _to_mapping(item)
            block.setdefault("kind", kind)
            blocks.append(block)
    return blocks


def _canonical_block_kind(block: Any) -> str:
    raw = _coerce_text(_get(block, "kind", "type")) or "paragraph"
    return raw.lower().replace(" ", "_")


def _render_block(*, block: Any, ctx: _RenderContext, section_id: str, section_heading: str) -> str:
    kind = _canonical_block_kind(block)
    if kind in {"paragraph", "prose", "text"}:
        paragraphs = _coerce_paragraph_values(_get(block, "text", "content", default=block))
        return "".join(_tag("p", _render_inline(item, ctx)) for item in paragraphs)
    if kind in _LIST_KINDS:
        return _render_list(block, ctx)
    if kind == "quote":
        text = _render_inline(_get(block, "text", "content", default=block), ctx)
        citation = _coerce_text(_get(block, "citation", "cite", "attribution"))
        cite_html = _tag("cite", escape(citation), **{"class": "doc-quote__cite"}) if citation else ""
        return _tag("blockquote", _tag("p", text) + cite_html, **{"class": "doc-quote"})
    if kind == "figure":
        return _render_figure(block=block, ctx=ctx, section_heading=section_heading)
    if kind in _CALL_OUT_KINDS:
        return _render_callout(block, ctx)
    if kind == "table":
        return _render_table(block=block, ctx=ctx, section_heading=section_heading)
    if kind in {"form", "form_group", "form-group"}:
        return _render_form(block=block, ctx=ctx, section_id=section_id)
    if kind in _REFERENCE_KINDS:
        return _render_reference_block(block, ctx)
    return _tag("p", _render_inline(_get(block, "text", "content", default=block), ctx))


def _coerce_paragraph_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not value:
            return []
        if _looks_like_inline_sequence(value):
            return [list(value)]
        if all(isinstance(item, (str, int, float)) for item in value):
            return [text for text in (_coerce_text(item) for item in value) if text]
        if any(isinstance(item, (Mapping, list, tuple)) for item in value):
            return list(value)
        joined = " ".join(_coerce_text(item) for item in value if _coerce_text(item))
        return [joined] if joined else []
    text = _coerce_text(value)
    return [text] if text else []


def _looks_like_inline_sequence(value: Sequence[Any]) -> bool:
    saw_inline_marker = False
    for item in value:
        if isinstance(item, (str, int, float)):
            continue
        if isinstance(item, Mapping):
            if _get(item, "href"):
                saw_inline_marker = True
                continue
            if _coerce_text(_get(item, "kind", "type")) in {
                "text",
                "link",
                "footnote_ref",
                "footnote-ref",
                "footnote-reference",
                "strong",
                "bold",
                "em",
                "italic",
                "code",
            }:
                saw_inline_marker = True
                continue
        return False
    return saw_inline_marker


def _render_inline(value: Any, ctx: _RenderContext) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return escape(value)
    if isinstance(value, (int, float)):
        return escape(str(value))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "".join(_render_inline(item, ctx) for item in value)

    kind = _canonical_block_kind(value)
    if kind == "link" or _get(value, "href"):
        href = _coerce_text(_get(value, "href", "url"))
        text = _render_inline(_get(value, "text", "label", "title", default=href or "Link"), ctx)
        return _tag("a", text, href=href or "#")
    if kind in {"footnote_ref", "footnote-reference", "footnote-ref", "fn_ref"}:
        footnote_key = (
            _coerce_text(_get(value, "id", "footnote_id", "target"))
            or _coerce_text(_get(value, "label"))
            or "1"
        )
        footnote_id = _footnote_target_id(footnote_key)
        ref_count = len(ctx.footnote_ref_ids.get(footnote_id, [])) + 1
        ref_id = f"{footnote_id}-ref-{ref_count}"
        ctx.footnote_ref_ids.setdefault(footnote_id, []).append(ref_id)
        label = _coerce_text(_get(value, "label", "text")) or footnote_key
        return _tag(
            "sup",
            _tag(
                "a",
                escape(label),
                href=f"#{footnote_id}",
                id=ref_id,
                **{"aria-label": f"Footnote {label}"},
            ),
        )
    if kind in {"strong", "bold"}:
        return _tag("strong", _render_inline(_get(value, "text", "content"), ctx))
    if kind in {"em", "italic"}:
        return _tag("em", _render_inline(_get(value, "text", "content"), ctx))
    if kind == "code":
        return _tag("code", escape(_coerce_text(_get(value, "text", "content"))))

    for key in _INLINE_TEXT_KEYS:
        candidate = _get(value, key)
        if candidate is not None:
            return escape(_coerce_text(candidate))
    return ""


def _render_list(block: Any, ctx: _RenderContext) -> str:
    items = _listify(_get(block, "items", "children"))
    ordered = bool(_get(block, "ordered"))
    if _canonical_block_kind(block) in {"ordered_list", "ordered-list"}:
        ordered = True
    tag_name = "ol" if ordered else "ul"
    inner = "".join(_tag("li", _render_inline(item, ctx)) for item in items)
    return _tag(tag_name, inner)


def _build_asset_lookup(assets: list[Any]) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for asset in assets:
        keys = {
            _coerce_text(_get(asset, "id", "asset_id", "stem")),
            _coerce_text(_get(asset, "filename", "name")),
        }
        filename = _coerce_text(_get(asset, "filename", "name"))
        if filename:
            keys.add(Path(filename).stem)
        for key in keys:
            if key:
                lookup[key] = asset
    return lookup


def _resolve_figure_assets(block: Any, ctx: _RenderContext) -> list[Any]:
    resolved: list[Any] = []

    def append_asset(candidate: Any) -> None:
        if candidate is None:
            return
        if isinstance(candidate, (str, bytes, bytearray)):
            key = candidate.decode() if isinstance(candidate, (bytes, bytearray)) else candidate
            key = key.strip()
            if not key:
                return
            resolved.append(ctx.asset_lookup.get(key, {"url": key}))
            return
        resolved.append(candidate)

    direct_url = _coerce_text(_get(block, "asset_url", "url", "src"))
    if direct_url:
        resolved.append(block)

    asset_id = _coerce_text(_get(block, "asset_id", "figure_id", "id_ref"))
    if asset_id and asset_id in ctx.asset_lookup:
        append_asset(ctx.asset_lookup[asset_id])

    for key in ("asset_ids", "assets", "images"):
        for item in _listify(_get(block, key)):
            if isinstance(item, Mapping) and not _coerce_text(_get(item, "url", "src", "asset_url")):
                ref = _coerce_text(_get(item, "id", "asset_id", "stem"))
                append_asset(ctx.asset_lookup.get(ref, item))
            else:
                append_asset(item)
    return resolved


def _asset_url(asset: Any) -> str:
    return _coerce_text(_get(asset, "url", "src", "asset_url", "filename", "name"))


def _render_figure(*, block: Any, ctx: _RenderContext, section_heading: str) -> str:
    assets = _resolve_figure_assets(block, ctx)
    caption = _coerce_text(_get(block, "caption", "title")) or f"Illustration for {section_heading}"
    alt_override = _coerce_text(_get(block, "alt", "alt_text"))

    media_parts: list[str] = []
    for asset in assets:
        url = _asset_url(asset)
        if not url:
            continue
        alt = (
            alt_override
            or _coerce_text(_get(asset, "alt", "alt_text", "alt_text_hint"))
            or caption
        )
        img_html = _tag("img", "", src=url, alt=alt)
        href = _coerce_text(_get(asset, "href", "link_url")) or _coerce_text(_get(block, "href", "link_url"))
        if href:
            img_html = _tag("a", img_html, href=href)
        media_parts.append(img_html)
    if not media_parts:
        media_parts.append(_tag("div", "", **{"class": "doc-figure__placeholder", "aria-hidden": "true"}))
    figcaption = _tag("figcaption", escape(caption))
    return _tag(
        "figure",
        _tag("div", "".join(media_parts), **{"class": "doc-figure__media"}) + figcaption,
        **{"class": "doc-figure"},
    )


def _render_callout(block: Any, ctx: _RenderContext) -> str:
    label = _coerce_text(_get(block, "label", "title")) or "Callout"
    title_html = _tag("p", escape(label), **{"class": "doc-callout__title"})
    body = "".join(
        _tag("p", _render_inline(item, ctx)) for item in _coerce_paragraph_values(_get(block, "text", "content"))
    )
    if not body:
        body = _tag("p", escape(label))
    return _tag(
        "aside",
        title_html + body,
        **{"class": "callout doc-callout", "aria-label": label},
    )


def _render_table(*, block: Any, ctx: _RenderContext, section_heading: str) -> str:
    caption = _coerce_text(_get(block, "caption", "title")) or f"{section_heading} table"
    all_rows = _coerce_rows(_get(block, "rows"))
    head_rows = _coerce_rows(_get(block, "head", "thead"))
    body_rows = _coerce_rows(_get(block, "body", "rows"))
    header_indexes = {
        index
        for index in _listify(_get(block, "header_rows"))
        if isinstance(index, int)
    }
    if header_indexes and all_rows and not head_rows:
        head_rows = [row for idx, row in enumerate(all_rows) if idx in header_indexes]
        body_rows = [row for idx, row in enumerate(all_rows) if idx not in header_indexes]
    elif all_rows and not body_rows:
        body_rows = all_rows
    row_headers = bool(_get(block, "row_headers", "rowHeaders"))

    if not body_rows and head_rows:
        body_rows = []
    if not head_rows:
        simple_headers = _listify(_get(block, "headers"))
        if simple_headers and not any(isinstance(item, (Mapping, list, tuple)) for item in simple_headers):
            head_rows = [[{"text": header, "header": True, "scope": "col"} for header in simple_headers]]

    thead_html = (
        _tag("thead", "".join(_render_table_row(row, section="head", row_headers=row_headers, ctx=ctx) for row in head_rows))
        if head_rows
        else ""
    )
    tbody_html = _tag(
        "tbody",
        "".join(
            _render_table_row(row, section="body", row_headers=row_headers, ctx=ctx)
            for row in body_rows
        ),
    )
    table_html = _tag(
        "table",
        _tag("caption", escape(caption)) + thead_html + tbody_html,
    )
    return _tag("div", table_html, **{"class": "doc-table"})


def _coerce_rows(value: Any) -> list[list[Any]]:
    rows = _listify(value)
    normalized: list[list[Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            cells = _listify(_get(row, "cells", "items"))
            if cells:
                normalized.append(cells)
                continue
        normalized.append(_listify(row))
    return normalized


def _render_table_row(row: list[Any], *, section: str, row_headers: bool, ctx: _RenderContext) -> str:
    cells: list[str] = []
    for index, cell in enumerate(row):
        is_header = bool(_get(cell, "header")) or (section == "head")
        scope = _coerce_text(_get(cell, "scope"))
        if section == "head":
            scope = scope or "col"
            is_header = True
        elif is_header or (row_headers and index == 0):
            scope = scope or "row"
            is_header = True
        tag_name = "th" if is_header else "td"
        attrs: dict[str, Any] = {}
        if scope:
            attrs["scope"] = scope
        colspan = _get(cell, "colspan")
        rowspan = _get(cell, "rowspan")
        if colspan:
            attrs["colspan"] = colspan
        if rowspan:
            attrs["rowspan"] = rowspan
        cells.append(_tag(tag_name, _render_inline(_get(cell, "text", "value", default=cell), ctx), **attrs))
    return _tag("tr", "".join(cells))


def _render_form(*, block: Any, ctx: _RenderContext, section_id: str) -> str:
    form_parts: list[str] = []
    intro = _coerce_text(_get(block, "intro", "description"))
    if intro:
        form_parts.append(_tag("p", escape(intro), **{"class": "doc-form__intro"}))

    for index, field in enumerate(_listify(_get(block, "fields", "items"))):
        form_parts.append(
            _render_field(
                field=field,
                ctx=ctx,
                section_id=section_id,
                index=index,
            )
        )
    if ctx.document_form_wrapper:
        return _tag("div", "".join(form_parts), **{"class": "doc-form__group-block"})
    return _tag("form", "".join(form_parts), **{"class": "doc-form"})


def _render_field(*, field: Any, ctx: _RenderContext, section_id: str, index: int) -> str:
    del ctx  # reserved for future inline annotations
    field_type = (_coerce_text(_get(field, "type", "field_type")) or "text").lower()
    label = _coerce_text(_get(field, "label", "title")) or f"Field {index + 1}"
    raw_id = (
        _coerce_text(_get(field, "id"))
        or f"{section_id}-{index + 1}-{_slugify(label)}"
    )
    field_id = _slugify(raw_id)
    help_text = _coerce_text(_get(field, "help_text", "description", "hint"))
    required = bool(_get(field, "required"))
    placeholder = _coerce_text(_get(field, "placeholder"))
    value = _coerce_text(_get(field, "value"))
    label_html = _tag("label", escape(label), **{"for": field_id})
    hint_id = f"{field_id}-hint" if help_text else ""
    hint_html = _tag("p", escape(help_text), id=hint_id, **{"class": "doc-form__hint"}) if help_text else ""

    common_attrs: dict[str, Any] = {
        "id": field_id,
        "required": required if required else None,
        "aria-describedby": hint_id or None,
        "placeholder": placeholder or None,
    }

    if field_type == "textarea" or field_type == "signature":
        control = _tag("textarea", escape(value), rows="4", **common_attrs)
        return _tag("div", label_html + hint_html + control, **{"class": "doc-form__field"})

    if field_type == "select":
        options_html = "".join(
            _tag(
                "option",
                escape(_coerce_text(_get(option, "label", "text", default=option)) or _coerce_text(option)),
                value=_coerce_text(_get(option, "value")) or _coerce_text(option),
                selected=bool(_get(option, "selected")) or None,
            )
            for option in _listify(_get(field, "options"))
        )
        control = _tag("select", options_html, **common_attrs)
        return _tag("div", label_html + hint_html + control, **{"class": "doc-form__field"})

    if field_type in {"checkbox", "radio"} and _listify(_get(field, "options")):
        legend = _tag("legend", escape(label))
        choices = "".join(
            _render_choice(
                option=option,
                field_type=field_type,
                base_id=field_id,
                name=field_id,
            )
            for option in _listify(_get(field, "options"))
        )
        return _tag(
            "fieldset",
            legend + hint_html + _tag("div", choices, **{"class": "doc-form__choices"}),
            **{"class": "doc-form__group"},
        )

    if field_type == "checkbox":
        control = _tag("input", "", type="checkbox", checked=bool(_get(field, "checked")) or None, **common_attrs)
        return _tag(
            "div",
            _tag("div", control + label_html, **{"class": "doc-form__choice"}) + hint_html,
            **{"class": "doc-form__field"},
        )

    input_type = field_type if field_type in {"email", "tel", "url", "number", "date"} else "text"
    control = _tag("input", "", type=input_type, value=value or None, **common_attrs)
    return _tag("div", label_html + hint_html + control, **{"class": "doc-form__field"})


def _render_choice(*, option: Any, field_type: str, base_id: str, name: str) -> str:
    value = _coerce_text(_get(option, "value")) or _coerce_text(_get(option, "label", "text", default=option))
    label = _coerce_text(_get(option, "label", "text", default=option)) or value
    option_id = _slugify(f"{base_id}-{value or label}")
    control = _tag(
        "input",
        "",
        id=option_id,
        type=field_type,
        name=name,
        value=value or label,
        checked=bool(_get(option, "checked", "selected")) or None,
    )
    label_html = _tag("label", escape(label), **{"for": option_id})
    return _tag("div", control + label_html, **{"class": "doc-form__choice"})


def _render_reference_block(block: Any, ctx: _RenderContext) -> str:
    items = _listify(_get(block, "items", "references"))
    if not items:
        text = _coerce_text(_get(block, "text", "content"))
        items = [text] if text else []
    return _render_reference_list(items, ctx=ctx, title=_coerce_text(_get(block, "title")) or "References")


def _render_references(references: list[Any]) -> str:
    if not references:
        return ""
    # Temporary context: references can still include links / emphasis.
    ctx = _RenderContext(used_ids=set(), asset_lookup={}, layout_by_section={}, footnote_ref_ids={})
    return _render_reference_list(references, ctx=ctx, title="References")


def _render_reference_list(items: list[Any], *, ctx: _RenderContext, title: str) -> str:
    if not items:
        return ""
    label_id = _unique_id(f"{title}-label", used_ids=ctx.used_ids)
    list_html = _tag(
        "ol",
        "".join(_tag("li", _render_inline(item, ctx)) for item in items),
        **{"class": "doc-references__list"},
    )
    title_html = _tag(
        "p",
        escape(title),
        id=label_id,
        **{"class": "doc-references__title"},
    )
    return _tag(
        "section",
        title_html + list_html,
        **{"class": "doc-references", "aria-labelledby": label_id},
    )


def _footnote_target_id(raw: str) -> str:
    slug = _slugify(raw)
    if slug.startswith("fn-"):
        return slug
    return f"fn-{slug}"


def _render_footnotes(footnotes: list[Any], ctx: _RenderContext) -> str:
    if not footnotes:
        return ""
    items: list[str] = []
    for index, footnote in enumerate(footnotes, start=1):
        label = _coerce_text(_get(footnote, "label", "number")) or str(index)
        raw_id = _coerce_text(_get(footnote, "id")) or label
        footnote_id = _footnote_target_id(raw_id)
        text = _render_inline(_get(footnote, "text", "content", default=footnote), ctx)
        ref_targets = ctx.footnote_ref_ids.get(footnote_id) or []
        backlink = _tag(
            "a",
            "Return",
            href=f"#{ref_targets[0] if ref_targets else 'document-title'}",
            **{"aria-label": f"Return from footnote {label}"},
        )
        items.append(_tag("li", f"{text} {backlink}".strip(), id=footnote_id))
    body = _tag(
        "p",
        "Footnotes",
        id="doc-footnotes-label",
        **{"class": "doc-footnotes__title"},
    ) + _tag("ol", "".join(items))
    return _tag(
        "aside",
        body,
        **{"class": "footnotes doc-footnotes", "aria-labelledby": "doc-footnotes-label"},
    )


def _render_toc(outline: list[dict[str, Any]]) -> str:
    items = "".join(
        _tag(
            "li",
            _tag("a", escape(node["text"]), href=f"#{node['id']}"),
            **{"class": f"doc-page__toc-item doc-page__toc-item--level-{node['level']}"},
        )
        for node in outline
    )
    return _tag(
        "nav",
        _tag("p", "On this page", id="doc-toc-title", **{"class": "doc-page__toc-title"})
        + _tag("ol", items),
        **{"class": "doc-page__toc", "aria-labelledby": "doc-toc-title"},
    )
