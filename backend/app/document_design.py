from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field

from backend.app.gemini.plan import DocumentPlan, FigurePlan, SectionPlan
from backend.app.images import ImageAssetLike, normalize_image_assets


class SectionLayoutPlan(BaseModel):
    section_id: str
    layout: Literal[
        "single-column",
        "split-figure-right",
        "split-figure-left",
        "feature-figure",
        "sidebar-callout",
        "dense-reference",
    ] = "single-column"


class FigurePlacementPlan(BaseModel):
    section_id: str
    figure_ids: list[str] = Field(default_factory=list)
    placement: Literal["inline", "rail-right", "rail-left", "stacked"] = "inline"


class ThemeTokens(BaseModel):
    surface: str = "paper"
    accent: str = "ink"


class DesignPlan(BaseModel):
    template: Literal["scientific", "memo", "brochure", "reference", "form"] = "reference"
    hero_style: Literal["article", "memo", "immersive", "compact"] = "article"
    toc_enabled: bool = True
    visual_density: Literal["comfortable", "dense"] = "comfortable"
    section_layouts: list[SectionLayoutPlan] = Field(default_factory=list)
    sidebar_blocks: list[str] = Field(default_factory=list)
    figure_placements: list[FigurePlacementPlan] = Field(default_factory=list)
    theme_tokens: ThemeTokens = Field(default_factory=ThemeTokens)


def infer_doc_kind(plan: DocumentPlan, *, fmt: str = "pdf") -> str:
    if plan.doc_kind and plan.doc_kind != "report":
        return plan.doc_kind
    lowered_title = (plan.title or "").lower()
    if fmt == "docx":
        return "memo"
    if "catalogue" in lowered_title or "catalog" in lowered_title or "brochure" in lowered_title:
        return "brochure"
    if "anatomy" in lowered_title or "paper" in lowered_title or "system" in lowered_title:
        return "scientific_article"
    if "guide" in lowered_title or "reference" in lowered_title or "spec" in lowered_title:
        return "reference_doc"
    if any(section.layout == "form" for section in plan.sections):
        return "form_doc"
    return "report"


def heuristic_design_plan(plan: DocumentPlan, *, fmt: str = "pdf") -> DesignPlan:
    doc_kind = infer_doc_kind(plan, fmt=fmt)
    template = {
        "scientific_article": "scientific",
        "memo": "memo",
        "brochure": "brochure",
        "reference_doc": "reference",
        "form_doc": "form",
        "report": "reference",
    }[doc_kind]
    hero_style = {
        "scientific": "article",
        "memo": "memo",
        "brochure": "immersive",
        "reference": "compact",
        "form": "compact",
    }[template]
    toc_enabled = len(plan.sections) >= 3
    visual_density = "dense" if template in {"scientific", "reference"} else "comfortable"
    section_layouts: list[SectionLayoutPlan] = []
    figure_placements: list[FigurePlacementPlan] = []
    for section in plan.sections:
        has_figures = bool(section.images)
        if template == "scientific" and has_figures:
            section_layouts.append(
                SectionLayoutPlan(section_id=section.id, layout="split-figure-right")
            )
            figure_placements.append(
                FigurePlacementPlan(section_id=section.id, placement="rail-right")
            )
        elif template == "brochure" and has_figures:
            section_layouts.append(
                SectionLayoutPlan(section_id=section.id, layout="feature-figure")
            )
            figure_placements.append(
                FigurePlacementPlan(section_id=section.id, placement="stacked")
            )
        elif section.layout == "table":
            section_layouts.append(
                SectionLayoutPlan(section_id=section.id, layout="dense-reference")
            )
        else:
            section_layouts.append(
                SectionLayoutPlan(section_id=section.id, layout="single-column")
            )
    return DesignPlan(
        template=template,
        hero_style=hero_style,
        toc_enabled=toc_enabled,
        visual_density=visual_density,
        section_layouts=section_layouts,
        figure_placements=figure_placements,
        theme_tokens=ThemeTokens(
            surface="paper",
            accent="blueprint" if template == "scientific" else "ink",
        ),
    )


def _extract_img_stem(src: str | None) -> str | None:
    if not src:
        return None
    stem = Path(src.split("/")[-1]).stem
    return stem or None


def _new_tag(section_tag: Tag, name: str) -> Tag:
    root = section_tag if isinstance(section_tag, BeautifulSoup) else section_tag.parent
    while root is not None and not isinstance(root, BeautifulSoup):
        root = root.parent
    if isinstance(root, BeautifulSoup):
        return root.new_tag(name)
    return BeautifulSoup("", "html.parser").new_tag(name)


def _section_layout_for(section_id: str, design_plan: DesignPlan) -> str:
    for section in design_plan.section_layouts:
        if section.section_id == section_id:
            return section.layout
    return "single-column"


def _find_figure_plan(plan: DocumentPlan, section: SectionPlan, stem: str) -> FigurePlan | None:
    for figure in plan.figures:
        if figure.section_id == section.id and stem in figure.asset_stems:
            return figure
    return None


def _ensure_figure_wrappers(section_tag: Tag, section_plan: SectionPlan, plan: DocumentPlan) -> None:
    for img in list(section_tag.find_all("img")):
        if img.find_parent("figure") is not None:
            figure = img.find_parent("figure")
            if figure and figure.find("figcaption") is None:
                figcaption = _new_tag(section_tag, "figcaption")
                stem = _extract_img_stem(img.get("src"))
                figure_plan = _find_figure_plan(plan, section_plan, stem or "")
                figcaption.string = (
                    figure_plan.caption
                    if figure_plan and figure_plan.caption
                    else section_plan.caption or f"Illustration for {section_plan.heading}"
                )
                figure.append(figcaption)
            continue
        stem = _extract_img_stem(img.get("src"))
        figure_plan = _find_figure_plan(plan, section_plan, stem or "")
        figure = _new_tag(section_tag, "figure")
        figure["class"] = ["doc-figure", "doc-figure--embedded"]
        img.wrap(figure)
        figcaption = _new_tag(section_tag, "figcaption")
        figcaption.string = (
            figure_plan.caption
            if figure_plan and figure_plan.caption
            else section_plan.caption or f"Illustration for {section_plan.heading}"
        )
        figure.append(figcaption)
        if not (img.get("alt") or "").strip():
            img["alt"] = (
                figure_plan.alt_text_hint
                if figure_plan and figure_plan.alt_text_hint
                else f"Illustration for {section_plan.heading}"
            )


def _inject_missing_figures(
    section_tag: Tag,
    section_plan: SectionPlan,
    plan: DocumentPlan,
    image_assets: list[ImageAssetLike] | None,
    *,
    sha256: str | None,
) -> None:
    if not image_assets or not sha256:
        return
    assets = {Path(asset.filename).stem: asset.filename for asset in normalize_image_assets(image_assets)}
    present = {
        stem
        for stem in (_extract_img_stem(img.get("src")) for img in section_tag.find_all("img"))
        if stem
    }
    missing = [stem for stem in section_plan.images if stem not in present and stem in assets]
    if not missing:
        return
    for stem in missing:
        figure_plan = _find_figure_plan(plan, section_plan, stem)
        figure = _new_tag(section_tag, "figure")
        figure["class"] = ["doc-figure", "doc-figure--auto"]
        img = _new_tag(section_tag, "img")
        img["src"] = f"/images/{sha256}/{assets[stem]}"
        img["alt"] = (
            figure_plan.alt_text_hint
            if figure_plan and figure_plan.alt_text_hint
            else f"Illustration for {section_plan.heading}"
        )
        figcaption = _new_tag(section_tag, "figcaption")
        figcaption.string = (
            figure_plan.caption
            if figure_plan and figure_plan.caption
            else section_plan.caption or f"Illustration for {section_plan.heading}"
        )
        figure.append(img)
        figure.append(figcaption)
        section_tag.append(figure)


def _build_toc_html(plan: DocumentPlan) -> str:
    if not plan.sections:
        return ""
    items = "".join(
        f'<li><a href="#{section.id}">{section.heading}</a></li>'
        for section in plan.sections
    )
    return (
        '<nav class="doc-page__toc" aria-label="Table of contents">'
        '<h2 class="doc-page__toc-title">On this page</h2>'
        f"<ol>{items}</ol>"
        "</nav>"
    )


def _render_section(section_tag: Tag, section_plan: SectionPlan, design_plan: DesignPlan) -> str:
    layout = _section_layout_for(section_plan.id, design_plan)
    heading = section_tag.find(["h2", "h3", "h4", "h5", "h6"])
    if heading is None:
        heading = _new_tag(section_tag, "h2")
        heading["id"] = section_plan.id
        heading.string = section_plan.heading
        section_tag.insert(0, heading)
    else:
        heading["id"] = section_plan.id
    figures = section_tag.find_all("figure", recursive=False)
    callouts = section_tag.find_all("aside", recursive=False)
    main_nodes = []
    rail_nodes = []
    for child in list(section_tag.children):
        if not isinstance(child, Tag) or child is heading:
            continue
        if child in figures or child in callouts:
            rail_nodes.append(str(child))
        else:
            main_nodes.append(str(child))
    if layout == "single-column":
        body = "".join(main_nodes + rail_nodes)
    elif layout in {"split-figure-right", "sidebar-callout"}:
        body = (
            '<div class="doc-section__grid">'
            f'<div class="doc-section__main">{"".join(main_nodes)}</div>'
            f'<aside class="doc-section__rail">{"".join(rail_nodes)}</aside>'
            "</div>"
        )
    elif layout == "split-figure-left":
        body = (
            '<div class="doc-section__grid">'
            f'<aside class="doc-section__rail">{"".join(rail_nodes)}</aside>'
            f'<div class="doc-section__main">{"".join(main_nodes)}</div>'
            "</div>"
        )
    elif layout == "feature-figure":
        body = "".join(rail_nodes + main_nodes)
    else:
        body = "".join(main_nodes + rail_nodes)
    section_tag["class"] = [f"doc-section", f"doc-section--{layout}"]
    heading_html = str(heading)
    return f'<section id="{section_plan.id}" class="{" ".join(section_tag.get("class", []))}">{heading_html}{body}</section>'


def render_designed_html(
    html: str,
    plan: DocumentPlan,
    design_plan: DesignPlan,
    *,
    image_assets: list[ImageAssetLike] | None = None,
    sha256: str | None = None,
) -> str:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main")
    if main is None:
        return html
    rendered = BeautifulSoup("", "html.parser")
    shell = rendered.new_tag("main")
    shell["id"] = "content"
    shell["lang"] = main.get("lang", plan.language or "en")
    shell["class"] = [
        "doc-page",
        f"doc-page--{design_plan.template}",
        f"doc-page--density-{design_plan.visual_density}",
    ]
    skip = rendered.new_tag("a")
    skip["href"] = "#content"
    skip["class"] = ["sr-skip"]
    skip.string = "Skip to main content"
    shell.append(skip)

    hero = rendered.new_tag("header")
    hero["class"] = ["doc-hero", f"doc-hero--{design_plan.hero_style}"]
    if plan.kicker:
        kicker = rendered.new_tag("p")
        kicker["class"] = ["doc-hero__kicker"]
        kicker.string = plan.kicker
        hero.append(kicker)
    title = rendered.new_tag("h1")
    title["id"] = "document-title"
    title["class"] = ["doc-hero__title"]
    title.string = plan.title
    hero.append(title)
    if plan.subtitle:
        subtitle = rendered.new_tag("p")
        subtitle["class"] = ["doc-hero__subtitle"]
        subtitle.string = plan.subtitle
        hero.append(subtitle)
    if plan.summary or plan.description:
        summary = rendered.new_tag("p")
        summary["class"] = ["doc-hero__summary"]
        summary.string = plan.summary or plan.description or ""
        hero.append(summary)
    shell.append(hero)

    if design_plan.toc_enabled:
        toc_fragment = BeautifulSoup(_build_toc_html(plan), "html.parser")
        toc = toc_fragment.find("nav")
        if toc is not None:
            shell.append(toc)

    body = rendered.new_tag("div")
    body["class"] = ["doc-page__body"]
    section_tags = {section.get("id"): section for section in main.find_all("section", recursive=False)}
    for section_plan in plan.sections:
        section_tag = section_tags.get(section_plan.id)
        if section_tag is None:
            continue
        _ensure_figure_wrappers(section_tag, section_plan, plan)
        _inject_missing_figures(section_tag, section_plan, plan, image_assets, sha256=sha256)
        section_html = _render_section(section_tag, section_plan, design_plan)
        section_fragment = BeautifulSoup(section_html, "html.parser")
        section_rendered = section_fragment.find("section")
        if section_rendered is not None:
            body.append(section_rendered)
    for aside in main.find_all("aside", recursive=False):
        body.append(BeautifulSoup(str(aside), "html.parser"))
    shell.append(body)
    rendered.append(shell)
    return str(rendered)
