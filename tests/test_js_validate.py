"""Inline <script> syntax validation via acorn (shell out to node).

Guards against the real regression we hit in smoke: Gemini emitted
`""—"` (three double-quotes wrapping an em-dash) as a JS string, which
passed WCAG structural checks but silently broke the XLSX calculator at
runtime. These tests pin the validator so that class of bug fails the
ingest gate instead of being cached.
"""
from __future__ import annotations

import shutil

import pytest

from backend.app.js_validate import validate_inline_scripts

# Skip the whole module on hosts without node — the validator degrades
# gracefully there (returns []), which would make the "bad JS" assertions
# meaningless.
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH"
)


def test_no_scripts_is_ok() -> None:
    assert validate_inline_scripts("<main><h1>No scripts here</h1></main>") == []


def test_valid_script_passes() -> None:
    html = (
        '<main><h1>Calc</h1>'
        '<script>const x = 1; function f(a) { return a + x; } f(2);</script>'
        '</main>'
    )
    assert validate_inline_scripts(html) == []


def test_valid_calculator_script_passes() -> None:
    # Mirrors the shape of the real calculator output.
    html = (
        '<main><h1>GPA</h1>'
        '<script>'
        'window.__docboxCalc = { ok: true };'
        'document.querySelectorAll("input").forEach((el) => {'
        '  el.addEventListener("input", () => { el.value = el.value; });'
        '});'
        '</script>'
        '</main>'
    )
    assert validate_inline_scripts(html) == []


def test_regression_triple_quote_em_dash_fails() -> None:
    """The exact parse-broken snippet Gemini produced in smoke.

    `""—"` is parsed as an empty string `""` followed by `—"` which is a
    lone identifier-like char plus an unterminated string literal. acorn
    must reject it."""
    html = (
        '<main><h1>GPA</h1>'
        '<script>const placeholder = ""—";</script>'
        '</main>'
    )
    errors = validate_inline_scripts(html)
    assert errors, "expected acorn to reject the triple-quote em-dash literal"
    assert "#1" in errors[0]


def test_unterminated_string_fails() -> None:
    html = '<main><script>const s = "oops;</script></main>'
    errors = validate_inline_scripts(html)
    assert errors


def test_empty_script_body_is_ok() -> None:
    # Empty <script></script> has no body to parse — treat as OK.
    assert validate_inline_scripts("<main><script></script></main>") == []


def test_multiple_scripts_reports_per_block() -> None:
    html = (
        '<main>'
        '<script>const a = 1;</script>'
        '<script>const b = "bad;</script>'
        '<script>const c = 3;</script>'
        '</main>'
    )
    errors = validate_inline_scripts(html)
    assert len(errors) == 1
    assert "#2" in errors[0]
