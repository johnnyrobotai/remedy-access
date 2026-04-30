"""check_html(render_mode="interactive") — relaxed rules for the XLSX
calculator path. Ensures <script> is permitted, but only when the page
actually looks like a calculator (has <output> + a labeled <input>)."""
from __future__ import annotations

from backend.app.a11y import check_html

_CALCULATOR_HTML = """
<main id="content" lang="en">
  <a href="#content" class="sr-skip">Skip to main content</a>
  <h1 id="document-title">GPA Calculator</h1>
  <section id="gpa">
    <h2 id="gpa">Grades</h2>
    <form>
      <label for="b2">English grade</label>
      <input id="b2" type="number" step="any" inputmode="decimal" />
      <label for="c2">English credits</label>
      <input id="c2" type="number" step="any" inputmode="decimal" />
      <label for="b6">Weighted GPA</label>
      <output id="b6" for="b2 c2">—</output>
    </form>
    <script>window.__docboxCalc = { ok: true };</script>
  </section>
</main>
"""


def test_interactive_mode_permits_script_when_calculator_shaped() -> None:
    assert check_html(_CALCULATOR_HTML, render_mode="interactive") == []


def test_interactive_mode_still_blocks_script_when_no_output() -> None:
    html = _CALCULATOR_HTML.replace('<output id="b6" for="b2 c2">—</output>', "")
    errors = check_html(html, render_mode="interactive")
    assert any("<output>" in e for e in errors), errors


def test_interactive_mode_still_blocks_script_when_no_labeled_input() -> None:
    stripped = (
        '<main id="content" lang="en">'
        '<h1 id="document-title">GPA</h1>'
        '<output id="x">—</output>'
        "<script>1;</script>"
        "</main>"
    )
    errors = check_html(stripped, render_mode="interactive")
    assert any("labeled <input>" in e for e in errors), errors


def test_static_mode_still_rejects_script() -> None:
    html = _CALCULATOR_HTML  # has a <script> tag
    errors = check_html(html, render_mode="static")
    assert any("<script>" in e for e in errors), errors
