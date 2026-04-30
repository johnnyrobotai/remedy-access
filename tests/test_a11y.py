from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app.a11y import check_html

RUN_AXE = os.getenv("RUN_AXE") == "1"


def test_clean_transcript_passes() -> None:
    html = """
    <main lang="en" id="content">
      <a href="#content" class="sr-skip">Skip to main content</a>
      <h1 id="h1-0">Form W-9</h1>
      <h2 id="h2-0">Part I</h2>
      <form>
        <label for="name">Legal name</label>
        <input id="name" type="text" />
      </form>
      <table>
        <caption>Rates</caption>
        <thead><tr><th scope="col">Year</th><th scope="col">Rate</th></tr></thead>
        <tbody><tr><th scope="row">2025</th><td>5%</td></tr></tbody>
      </table>
      <figure><img src="x.png" alt="chart of rates" /><figcaption>Rates</figcaption></figure>
    </main>
    """
    assert check_html(html) == []


def test_flags_missing_landmarks_and_heading() -> None:
    html = "<div>just a div</div>"
    errors = check_html(html)
    assert any("missing <main>" in e for e in errors)
    assert any("missing <h1>" in e for e in errors)


def test_flags_lang_on_main() -> None:
    html = '<main><h1>X</h1></main>'
    assert any("lang" in e for e in check_html(html))


def test_flags_heading_level_skip() -> None:
    html = '<main lang="en"><h1>A</h1><h3>B</h3></main>'
    errors = check_html(html)
    assert any("skipped levels" in e for e in errors)


def test_flags_inline_style() -> None:
    html = '<main lang="en"><h1 style="color:red">X</h1></main>'
    errors = check_html(html)
    assert any("inline style" in e for e in errors)


def test_flags_forbidden_script() -> None:
    html = '<main lang="en"><h1>X</h1><script>alert(1)</script></main>'
    errors = check_html(html)
    assert any("<script>" in e for e in errors)


def test_flags_img_without_alt() -> None:
    html = '<main lang="en"><h1>X</h1><img src="y.png"></main>'
    errors = check_html(html)
    assert any("img" in e and "alt" in e for e in errors)


def test_flags_data_table_without_th_scope() -> None:
    html = """
    <main lang="en"><h1>X</h1>
      <table>
        <tr><th>A</th><th>B</th></tr>
        <tr><td>1</td><td>2</td></tr>
      </table>
    </main>
    """
    errors = check_html(html)
    assert any("scope" in e for e in errors)


def test_flags_orphan_label_for() -> None:
    html = """
    <main lang="en"><h1>X</h1>
      <form><label for="missing">X</label></form>
    </main>
    """
    errors = check_html(html)
    assert any("nonexistent id" in e for e in errors)


# ---------------------------------------------------------------------------
# fix_html — automatic structural fixups applied before validation.
# ---------------------------------------------------------------------------


def test_fix_html_adds_scope_col_to_th_in_thead() -> None:
    """fix_html adds scope='col' to <th> inside <thead> that lack scope."""
    from backend.app.a11y import fix_html

    html = """<main lang="en"><h1>X</h1>
      <table><caption>C</caption>
        <thead><tr><th>A</th><th>B</th></tr></thead>
        <tbody><tr><td>1</td><td>2</td></tr></tbody>
      </table></main>"""
    fixed = fix_html(html)
    assert fixed.count('scope="col"') == 2


def test_fix_html_adds_scope_row_to_th_in_tbody() -> None:
    """fix_html adds scope='row' to <th> inside <tbody> rows that lack scope."""
    from backend.app.a11y import fix_html

    html = """<main lang="en"><h1>X</h1>
      <table><caption>C</caption>
        <thead><tr><th scope="col">Year</th><th scope="col">Rate</th></tr></thead>
        <tbody><tr><th>2025</th><td>5%</td></tr></tbody>
      </table></main>"""
    fixed = fix_html(html)
    assert 'scope="row"' in fixed


def test_fix_html_defaults_th_without_thead_to_scope_col() -> None:
    """Bare <th> outside <thead>/<tbody> defaults to scope='col'."""
    from backend.app.a11y import fix_html

    html = """<main lang="en"><h1>X</h1>
      <table><caption>C</caption>
        <tr><th>A</th><th>B</th></tr>
        <tr><td>1</td><td>2</td></tr>
      </table></main>"""
    fixed = fix_html(html)
    assert fixed.count('scope="col"') == 2


def test_fix_html_preserves_existing_scope() -> None:
    """fix_html does not change existing scope attributes."""
    from backend.app.a11y import fix_html

    html = """<main lang="en"><h1>X</h1>
      <table><caption>C</caption>
        <thead><tr><th scope="col">A</th></tr></thead>
        <tbody><tr><th scope="row">1</th></tr></tbody>
      </table></main>"""
    fixed = fix_html(html)
    assert 'scope="col"' in fixed
    assert 'scope="row"' in fixed
    # Should not double up
    assert fixed.count('scope=') == 2


def test_fix_html_repairs_orphan_label_by_renaming_input_id() -> None:
    """When <label for='X'> has no matching id, the next input's id is rewritten to X."""
    from backend.app.a11y import fix_html

    html = """<main lang="en"><h1>X</h1>
      <form>
        <label for="full-name">Full Name</label>
        <input id="full-name-text-field" type="text" />
      </form></main>"""
    fixed = fix_html(html)
    assert 'id="full-name"' in fixed
    assert 'full-name-text-field' not in fixed


def test_fix_html_assigns_id_to_input_with_no_id_when_label_orphan() -> None:
    """When orphan label is followed by an input without an id, the input gets the label's for value."""
    from backend.app.a11y import fix_html

    html = """<main lang="en"><h1>X</h1>
      <form>
        <label for="email">Email</label>
        <input type="email" />
      </form></main>"""
    fixed = fix_html(html)
    assert 'id="email"' in fixed


def test_fix_html_does_not_break_already_matched_label_input_pair() -> None:
    """fix_html leaves correctly paired label/input alone."""
    from backend.app.a11y import fix_html

    html = """<main lang="en"><h1>X</h1>
      <form>
        <label for="name">Name</label>
        <input id="name" type="text" />
      </form></main>"""
    fixed = fix_html(html)
    assert 'for="name"' in fixed
    assert 'id="name"' in fixed


def test_fix_html_passes_check_html_after_fixing_th_scope() -> None:
    """After fix_html, the WCAG checker no longer complains about <th> scope."""
    from backend.app.a11y import check_html, fix_html

    html = """<main lang="en" id="content"><h1 id="h1">X</h1>
      <table><caption>C</caption>
        <thead><tr><th>A</th><th>B</th></tr></thead>
        <tbody><tr><td>1</td><td>2</td></tr></tbody>
      </table></main>"""
    fixed = fix_html(html)
    errors = check_html(fixed)
    assert not any("scope" in e for e in errors), f"scope errors remain: {errors}"


def test_fix_html_does_not_overwrite_earlier_label_match_when_processing_later_orphan() -> None:
    """Multiple orphan labels before separate inputs each claim their own input — no churn."""
    from backend.app.a11y import check_html, fix_html

    html = """<main lang="en" id="content"><h1 id="h1">X</h1>
      <form>
        <label for="email">Email</label>
        <input type="email" />
        <label for="phone">Phone</label>
        <input type="tel" />
      </form></main>"""
    fixed = fix_html(html)
    assert fixed.count('id="email"') == 1
    assert fixed.count('id="phone"') == 1
    assert check_html(fixed) == [], f"unexpected errors: {check_html(fixed)}"


def test_fix_html_strips_for_when_multiple_labels_compete_for_one_input() -> None:
    """When 3 orphan labels precede 1 input, first claims it; the other 2 lose `for`."""
    from backend.app.a11y import check_html, fix_html

    html = """<main lang="en" id="content"><h1 id="h1">X</h1>
      <fieldset><legend>Notes</legend>
        <label for="aaa">Note A</label>
        <label for="bbb">Note B</label>
        <label for="ccc">Note C</label>
        <textarea rows="4"></textarea>
      </fieldset></main>"""
    fixed = fix_html(html)
    # First label gets the textarea
    assert 'id="aaa"' in fixed
    # The other two lose their for attribute (no input to claim)
    assert 'for="bbb"' not in fixed
    assert 'for="ccc"' not in fixed
    assert check_html(fixed) == [], f"unexpected errors: {check_html(fixed)}"


def test_fix_html_demotes_heading_that_skips_levels() -> None:
    """fix_html demotes a heading that skips levels (h2 → h4 becomes h2 → h3)."""
    from backend.app.a11y import fix_html

    html = """<main lang="en"><h1>X</h1>
      <h2>Section</h2>
      <h4>Subsection</h4>
      </main>"""
    fixed = fix_html(html)
    assert "<h3" in fixed
    assert "<h4" not in fixed


def test_fix_html_demotes_consecutive_skips() -> None:
    """A document with multiple skip jumps gets each one tightened."""
    from backend.app.a11y import fix_html

    html = """<main lang="en"><h1>A</h1>
      <h3>B</h3>
      <h5>C</h5>
      </main>"""
    fixed = fix_html(html)
    assert fixed.count("<h2") == 1
    assert fixed.count("<h3") == 1
    assert "<h5" not in fixed
    assert "<h4" not in fixed


def test_fix_html_assigns_generated_id_to_input_without_id_or_aria_label() -> None:
    """Inputs lacking both id and aria-label get a generated unique id so check_html passes."""
    from backend.app.a11y import check_html, fix_html

    html = """<main lang="en" id="content"><h1 id="h1">X</h1>
      <form>
        <input type="text" />
        <input type="email" />
      </form></main>"""
    fixed = fix_html(html)
    errors = check_html(fixed)
    assert not any("input" in e and "id" in e for e in errors), f"orphan-input errors remain: {errors}"


def test_fix_html_does_not_touch_input_with_aria_label() -> None:
    """An input that has aria-label is fine — fix_html should not add a generated id."""
    from backend.app.a11y import fix_html

    html = """<main lang="en" id="content"><h1 id="h1">X</h1>
      <input type="text" aria-label="Search" /></main>"""
    fixed = fix_html(html)
    assert 'aria-label="Search"' in fixed
    assert 'id="generated-input-' not in fixed


def test_fix_html_strips_for_from_orphan_label_with_no_input() -> None:
    """Orphan label with no following input gets its `for` attribute removed (rendered as plain text)."""
    from backend.app.a11y import fix_html

    html = """<main lang="en"><h1>X</h1>
      <fieldset><legend>Notes</legend>
        <label for="instructions-only">Read this carefully.</label>
      </fieldset>
      <p>Other content.</p>
      </main>"""
    fixed = fix_html(html)
    assert 'for="instructions-only"' not in fixed


def test_fix_html_passes_check_html_after_demoting_skipped_heading() -> None:
    """After fix_html, check_html no longer reports a heading-skip error."""
    from backend.app.a11y import check_html, fix_html

    html = """<main lang="en" id="content"><h1 id="h1">A</h1>
      <h2 id="h2">B</h2>
      <h4 id="h4">C</h4>
      </main>"""
    fixed = fix_html(html)
    errors = check_html(fixed)
    assert not any("skipped levels" in e for e in errors), f"heading-skip errors remain: {errors}"


def test_fix_html_passes_check_html_after_fixing_orphan_label() -> None:
    """After fix_html, the WCAG checker no longer complains about orphan label fors."""
    from backend.app.a11y import check_html, fix_html

    html = """<main lang="en" id="content"><h1 id="h1">X</h1>
      <form>
        <label for="email">Email</label>
        <input id="email-input" type="email" />
      </form></main>"""
    fixed = fix_html(html)
    errors = check_html(fixed)
    assert not any("nonexistent id" in e for e in errors), f"orphan label errors remain: {errors}"


@pytest.mark.skipif(not RUN_AXE, reason="RUN_AXE=1 + playwright required for real axe-core audit")
def test_axe_core_against_fixture() -> None:
    """Full WCAG 2.1 AA audit via Playwright + axe-core. Exercises the
    viewer's styles.css in a real browser so color-contrast checks are meaningful."""
    from playwright.sync_api import sync_playwright

    fixture = Path(__file__).parent / "fixtures" / "sample_transcript.html"
    if not fixture.exists():
        pytest.skip("no fixture present; run scripts/build_a11y_fixture.py first")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(f"file://{fixture.resolve()}")
        page.add_script_tag(url="https://unpkg.com/axe-core@4.10.2/axe.min.js")
        result = page.evaluate(
            """async () => {
                const res = await axe.run(document, {
                    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] }
                });
                return res.violations;
            }"""
        )
        browser.close()

    assert result == [], f"axe-core AA violations: {result}"
