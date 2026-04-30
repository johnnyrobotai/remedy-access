from __future__ import annotations

from bs4 import BeautifulSoup

from backend.app.a11y import check_html
from backend.app.layout_plan_v2 import BlockPlacementPlan, LayoutPlan, SectionLayoutPlan
from backend.app.render_v2 import render_document
from backend.app.structured_parser import (
    FigureBlock,
    ParagraphBlock,
    ParsedDocument,
    SectionBlock,
)


def _sample_parsed_document() -> dict:
    return {
        "title": "Coastal Access Guide",
        "language": "en",
        "subtitle": "Summer 2026 edition",
        "summary": "How to use the boardwalk, visitor services, and accessible parking.",
        "lead": ["This guide covers the accessible route from arrival to visitor services."],
        "metadata": [
            {"label": "Author", "value": "City Accessibility Office"},
            {"label": "Pages", "value": "12"},
        ],
        "assets": [
            {
                "id": "fig-boardwalk",
                "url": "/images/demo/boardwalk.png",
                "alt": "Ramped boardwalk entrance seen from the parking lot",
            }
        ],
        "heading_tree": [
            {
                "id": "arrival",
                "text": "Arrival and Orientation",
                "children": [{"id": "parking", "text": "Accessible Parking"}],
            },
            {"id": "facilities", "text": "Facilities Snapshot"},
            {"id": "contact", "text": "Feedback Form"},
        ],
        "sections": [
            {
                "id": "arrival",
                "lead": [
                    [
                        {
                            "kind": "text",
                            "text": "Start at the main entrance and follow the blue wayfinding markers",
                        },
                        {"kind": "footnote_ref", "id": "parking-note", "label": "1"},
                        {"kind": "text", "text": " to reach the boardwalk."},
                    ]
                ],
                "blocks": [
                    {
                        "kind": "paragraph",
                        "text": "The accessible route remains step-free from the curb cut to the visitor plaza.",
                    },
                    {
                        "kind": "callout",
                        "title": "Note",
                        "text": "Borrow a beach wheelchair at the information desk.",
                    },
                    {
                        "kind": "figure",
                        "asset_id": "fig-boardwalk",
                        "caption": "Boardwalk entrance with ramp and tactile edge strip.",
                    },
                ],
            },
            {
                "id": "parking",
                "blocks": [
                    {
                        "kind": "paragraph",
                        "text": "Accessible parking bays sit closest to the pedestrian entrance.",
                    },
                    {
                        "kind": "list",
                        "items": [
                            "Two van-accessible spaces",
                            "Loading aisle connects to the sidewalk",
                        ],
                    },
                ],
            },
            {
                "id": "facilities",
                "blocks": [
                    {
                        "kind": "table",
                        "caption": "Visitor services by location",
                        "headers": ["Location", "Service", "Hours"],
                        "row_headers": True,
                        "rows": [
                            ["Entry kiosk", "Maps and tactile guides", "8 AM to 6 PM"],
                            ["Visitor center", "Restrooms and loaner chairs", "9 AM to 5 PM"],
                        ],
                    }
                ],
            },
            {
                "id": "contact",
                "blocks": [
                    {
                        "kind": "form",
                        "intro": "Share access feedback with the visitor services team.",
                        "fields": [
                            {"id": "name", "label": "Name", "type": "text"},
                            {"id": "email", "label": "Email", "type": "email"},
                            {"id": "visit-date", "label": "Visit date", "type": "date"},
                            {
                                "id": "contact-method",
                                "label": "Preferred contact method",
                                "type": "radio",
                                "options": [
                                    {"value": "email", "label": "Email"},
                                    {"value": "phone", "label": "Phone"},
                                ],
                            },
                            {
                                "id": "updates",
                                "label": "Send service updates",
                                "type": "checkbox",
                            },
                        ],
                    }
                ],
            },
        ],
        "references": [
            "City Coastal Access Plan, 2026 update.",
            "Visitor Services Operations Manual, section 4.",
        ],
        "footnotes": [
            {
                "id": "parking-note",
                "label": "1",
                "text": "Accessible parking is first come, first served.",
            }
        ],
    }


def _sample_layout_plan() -> dict:
    return {
        "template": "reference",
        "hero_style": "compact",
        "visual_density": "comfortable",
        "section_layouts": [
            {"section_id": "arrival", "layout": "split-figure-right"},
            {"section_id": "facilities", "layout": "dense-reference"},
        ],
    }


def _normalize_html(html: str) -> str:
    return BeautifulSoup(html, "html.parser").prettify()


def test_render_v2_snapshot_mixed_document() -> None:
    rendered = render_document(_sample_parsed_document(), _sample_layout_plan())

    assert _normalize_html(rendered.html) == """<main class="doc-page doc-page--reference doc-page--density-comfortable" id="content" lang="en">
 <a class="sr-skip" href="#content">
  Skip to main content
 </a>
 <header class="doc-hero doc-hero--compact">
  <h1 class="doc-hero__title" id="document-title">
   Coastal Access Guide
  </h1>
  <p class="doc-hero__subtitle">
   Summer 2026 edition
  </p>
  <p class="doc-hero__summary">
   How to use the boardwalk, visitor services, and accessible parking.
  </p>
  <dl class="doc-hero__meta">
   <div>
    <dt class="doc-hero__meta-label">
     Author
    </dt>
    <dd class="doc-hero__meta-value">
     City Accessibility Office
    </dd>
   </div>
   <div>
    <dt class="doc-hero__meta-label">
     Pages
    </dt>
    <dd class="doc-hero__meta-value">
     12
    </dd>
   </div>
  </dl>
  <div class="doc-lede">
   <p>
    This guide covers the accessible route from arrival to visitor services.
   </p>
  </div>
 </header>
 <nav aria-labelledby="doc-toc-title" class="doc-page__toc">
  <p class="doc-page__toc-title" id="doc-toc-title">
   On this page
  </p>
  <ol>
   <li class="doc-page__toc-item doc-page__toc-item--level-2">
    <a href="#arrival">
     Arrival and Orientation
    </a>
   </li>
   <li class="doc-page__toc-item doc-page__toc-item--level-3">
    <a href="#parking">
     Accessible Parking
    </a>
   </li>
   <li class="doc-page__toc-item doc-page__toc-item--level-2">
    <a href="#facilities">
     Facilities Snapshot
    </a>
   </li>
   <li class="doc-page__toc-item doc-page__toc-item--level-2">
    <a href="#contact">
     Feedback Form
    </a>
   </li>
  </ol>
 </nav>
 <div class="doc-page__body">
  <section class="doc-section doc-section--split-figure-right" id="arrival">
   <header class="doc-section__header">
    <h2 id="arrival">
     Arrival and Orientation
    </h2>
    <div class="doc-section__intro">
     <p>
      Start at the main entrance and follow the blue wayfinding markers
      <sup>
       <a aria-label="Footnote 1" href="#fn-parking-note" id="fn-parking-note-ref-1">
        1
       </a>
      </sup>
      to reach the boardwalk.
     </p>
    </div>
   </header>
   <div class="doc-section__grid">
    <div class="doc-section__main">
     <p>
      The accessible route remains step-free from the curb cut to the visitor plaza.
     </p>
    </div>
    <aside aria-label="Supporting content" class="doc-section__rail">
     <aside aria-label="Note" class="callout doc-callout">
      <p class="doc-callout__title">
       Note
      </p>
      <p>
       Borrow a beach wheelchair at the information desk.
      </p>
     </aside>
     <figure class="doc-figure">
      <div class="doc-figure__media">
       <img alt="Ramped boardwalk entrance seen from the parking lot" src="/images/demo/boardwalk.png"/>
      </div>
      <figcaption>
       Boardwalk entrance with ramp and tactile edge strip.
      </figcaption>
     </figure>
    </aside>
   </div>
  </section>
  <section class="doc-section doc-section--single-column" id="parking">
   <header class="doc-section__header">
    <h3 id="parking">
     Accessible Parking
    </h3>
   </header>
   <div class="doc-section__stack">
    <p>
     Accessible parking bays sit closest to the pedestrian entrance.
    </p>
    <ul>
     <li>
      Two van-accessible spaces
     </li>
     <li>
      Loading aisle connects to the sidewalk
     </li>
    </ul>
   </div>
  </section>
  <section class="doc-section doc-section--dense-reference" id="facilities">
   <header class="doc-section__header">
    <h2 id="facilities">
     Facilities Snapshot
    </h2>
   </header>
   <div class="doc-section__stack">
    <div class="doc-table">
     <table>
      <caption>
       Visitor services by location
      </caption>
      <thead>
       <tr>
        <th scope="col">
         Location
        </th>
        <th scope="col">
         Service
        </th>
        <th scope="col">
         Hours
        </th>
       </tr>
      </thead>
      <tbody>
       <tr>
        <th scope="row">
         Entry kiosk
        </th>
        <td>
         Maps and tactile guides
        </td>
        <td>
         8 AM to 6 PM
        </td>
       </tr>
       <tr>
        <th scope="row">
         Visitor center
        </th>
        <td>
         Restrooms and loaner chairs
        </td>
        <td>
         9 AM to 5 PM
        </td>
       </tr>
      </tbody>
     </table>
    </div>
   </div>
  </section>
  <section class="doc-section doc-section--single-column" id="contact">
   <header class="doc-section__header">
    <h2 id="contact">
     Feedback Form
    </h2>
   </header>
   <div class="doc-section__stack">
    <form class="doc-form">
     <p class="doc-form__intro">
      Share access feedback with the visitor services team.
     </p>
     <div class="doc-form__field">
      <label for="name">
       Name
      </label>
      <input id="name" type="text"/>
     </div>
     <div class="doc-form__field">
      <label for="email">
       Email
      </label>
      <input id="email" type="email"/>
     </div>
     <div class="doc-form__field">
      <label for="visit-date">
       Visit date
      </label>
      <input id="visit-date" type="date"/>
     </div>
     <fieldset class="doc-form__group">
      <legend>
       Preferred contact method
      </legend>
      <div class="doc-form__choices">
       <div class="doc-form__choice">
        <input id="contact-method-email" name="contact-method" type="radio" value="email"/>
        <label for="contact-method-email">
         Email
        </label>
       </div>
       <div class="doc-form__choice">
        <input id="contact-method-phone" name="contact-method" type="radio" value="phone"/>
        <label for="contact-method-phone">
         Phone
        </label>
       </div>
      </div>
     </fieldset>
     <div class="doc-form__field">
      <div class="doc-form__choice">
       <input id="updates" type="checkbox"/>
       <label for="updates">
        Send service updates
       </label>
      </div>
     </div>
    </form>
   </div>
  </section>
  <section aria-labelledby="references-label" class="doc-references">
   <p class="doc-references__title" id="references-label">
    References
   </p>
   <ol class="doc-references__list">
    <li>
     City Coastal Access Plan, 2026 update.
    </li>
    <li>
     Visitor Services Operations Manual, section 4.
    </li>
   </ol>
  </section>
  <aside aria-labelledby="doc-footnotes-label" class="footnotes doc-footnotes">
   <p class="doc-footnotes__title" id="doc-footnotes-label">
    Footnotes
   </p>
   <ol>
    <li id="fn-parking-note">
     Accessible parking is first come, first served.
     <a aria-label="Return from footnote 1" href="#fn-parking-note-ref-1">
      Return
     </a>
    </li>
   </ol>
  </aside>
 </div>
</main>
"""


def test_render_v2_preserves_structure_and_wcag_guarantees() -> None:
    rendered = render_document(_sample_parsed_document(), _sample_layout_plan())
    soup = BeautifulSoup(rendered.html, "html.parser")

    assert rendered.outline == [
        {"id": "document-title", "level": 1, "text": "Coastal Access Guide", "parent": None},
        {"id": "arrival", "level": 2, "text": "Arrival and Orientation", "parent": None},
        {"id": "parking", "level": 3, "text": "Accessible Parking", "parent": "arrival"},
        {"id": "facilities", "level": 2, "text": "Facilities Snapshot", "parent": None},
        {"id": "contact", "level": 2, "text": "Feedback Form", "parent": None},
    ]
    assert check_html(rendered.html) == []
    assert [tag.name for tag in soup.find_all(["h1", "h2", "h3"])] == [
        "h1",
        "h2",
        "h3",
        "h2",
        "h2",
    ]

    figure = soup.find("figure")
    assert figure is not None
    assert figure.find("img")["src"] == "/images/demo/boardwalk.png"

    table = soup.find("table")
    assert table is not None
    assert [th["scope"] for th in table.thead.find_all("th")] == ["col", "col", "col"]
    assert [th["scope"] for th in table.tbody.find_all("th")] == ["row", "row"]

    labels = {label["for"] for label in soup.find_all("label")}
    controls = {control["id"] for control in soup.find_all(["input", "select", "textarea"]) if control.has_attr("id")}
    assert labels <= controls

    footnote_ref = soup.find("a", {"id": "fn-parking-note-ref-1"})
    footnote = soup.find("li", {"id": "fn-parking-note"})
    assert footnote_ref is not None
    assert footnote is not None
    assert footnote_ref["href"] == "#fn-parking-note"
    assert footnote.find("a")["href"] == "#fn-parking-note-ref-1"


def test_render_v2_ignores_heading_tree_title_root() -> None:
    parsed = {
        "title": "Transit Handbook",
        "language": "en",
        "heading_tree": [
            {
                "text": "Transit Handbook",
                "children": [{"id": "boarding", "text": "Boarding Assistance"}],
            }
        ],
        "sections": [
            {
                "id": "boarding",
                "blocks": [{"kind": "paragraph", "text": "Drivers can deploy the ramp on request."}],
            }
        ],
    }

    rendered = render_document(parsed, {"toc_enabled": False})
    soup = BeautifulSoup(rendered.html, "html.parser")

    assert [heading.get_text(strip=True) for heading in soup.find_all(["h1", "h2", "h3"])] == [
        "Transit Handbook",
        "Boarding Assistance",
    ]
    assert rendered.outline == [
        {"id": "document-title", "level": 1, "text": "Transit Handbook", "parent": None},
        {"id": "boarding", "level": 2, "text": "Boarding Assistance", "parent": None},
    ]


def test_render_v2_accepts_canonical_models() -> None:
    parsed = ParsedDocument(
        source_format="pdf",
        title="Coastal Access Guide",
        language="en",
        page_count=1,
        body=[
            SectionBlock(
                id="arrival",
                page_start=1,
                page_end=1,
                heading="Arrival and Orientation",
                level=2,
                blocks=[
                    ParagraphBlock(
                        id="arrival-p-1",
                        page_start=1,
                        page_end=1,
                        text="The accessible route remains step-free from the curb cut to the visitor plaza.",
                    ),
                    FigureBlock(
                        id="arrival-fig-1",
                        page_start=1,
                        page_end=1,
                        asset_id="/images/demo/boardwalk.png",
                        caption="Boardwalk entrance with ramp and tactile edge strip.",
                        alt_text_hint="Boardwalk entrance with ramp and tactile edge strip.",
                    ),
                ],
            )
        ],
    )
    layout = LayoutPlan(
        hero_treatment="compact",
        density="balanced",
        toc_policy="compact",
        sections=[
            SectionLayoutPlan(
                section_id="arrival",
                container="flow-with-rail",
                rail_side="right",
                blocks=[
                    BlockPlacementPlan(
                        block_index=0,
                        block_type="paragraph",
                        placement="main",
                        container="lead-prose",
                    ),
                    BlockPlacementPlan(
                        block_index=1,
                        block_type="figure",
                        placement="rail",
                        container="figure",
                    ),
                ],
            )
        ],
    )

    rendered = render_document(parsed, layout)
    soup = BeautifulSoup(rendered.html, "html.parser")

    assert rendered.title == "Coastal Access Guide"
    assert rendered.outline == [
        {"id": "document-title", "level": 1, "text": "Coastal Access Guide", "parent": None},
        {"id": "arrival", "level": 2, "text": "Arrival and Orientation", "parent": None},
    ]
    assert soup.find("section", {"id": "arrival"}) is not None
    assert "doc-section--split-figure-right" in soup.find("section", {"id": "arrival"})["class"]
    assert soup.find("img")["src"] == "/images/demo/boardwalk.png"
