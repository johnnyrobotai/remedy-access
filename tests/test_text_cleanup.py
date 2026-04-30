"""PDF text-extraction artifact cleanup — hyphen line-breaks + noisy
whitespace in text nodes. Runs as a post-processor on every transcript."""
from __future__ import annotations

from backend.app.text_cleanup import clean_extraction_artifacts


def test_joins_hyphenated_line_break() -> None:
    html = "<p>The re-\nceptors in the skin tell us about sur-\nface texture.</p>"
    assert clean_extraction_artifacts(html) == (
        "<p>The receptors in the skin tell us about surface texture.</p>"
    )


def test_joins_multiple_hyphen_breaks_in_one_paragraph() -> None:
    html = (
        "<p>Scattered throughout every striated mus-\ncle in the body are long, "
        "thin stretch re-\nceptors called muscle spin-\ndles.</p>"
    )
    cleaned = clean_extraction_artifacts(html)
    assert "muscle" in cleaned
    assert "receptors" in cleaned
    assert "spindles" in cleaned
    assert "-" not in cleaned.replace("stretch", "")  # no stray hyphens


def test_preserves_real_compound_hyphens() -> None:
    html = "<p>high-threshold mechanoreceptors and well-known polymodal types.</p>"
    # No newline after the hyphen → leave alone.
    assert clean_extraction_artifacts(html) == html


def test_preserves_hyphen_before_capital() -> None:
    html = "<p>Section A-\nSection B overview.</p>"
    # Capital letter after the newline → probably not a line-broken word.
    # Pattern requires lowercase on the other side, so this stays intact
    # (just the whitespace collapses).
    out = clean_extraction_artifacts(html)
    assert "A-" in out  # hyphen preserved
    assert "Section B" in out


def test_collapses_multi_column_line_breaks_in_paragraphs() -> None:
    html = (
        "<p>Our somatosensory system consists of sensors in the skin\n"
        "and sensors in our muscles, tendons, and joints.</p>"
    )
    cleaned = clean_extraction_artifacts(html)
    assert "\n" not in cleaned
    assert "skin and sensors" in cleaned


def test_leaves_pre_and_code_blocks_alone() -> None:
    html = "<pre>line-\nbroken\n  indent</pre><code>foo-\nbar</code>"
    # Inside <pre>/<code> the whitespace is meaningful — keep verbatim.
    assert clean_extraction_artifacts(html) == html


def test_leaves_inline_script_alone_for_interactive_calculator() -> None:
    html = (
        '<main id="content" lang="en"><h1 id="t">X</h1>'
        '<output id="o">—</output>'
        '<script>const x = 1 - \n  2;</script></main>'
    )
    # Script body must not be rewritten — whitespace is syntactically
    # significant (and the hyphen might be subtraction, not a word break).
    assert "1 - \n  2" in clean_extraction_artifacts(html)


def test_idempotent() -> None:
    html = "<p>re-\nceptors</p>"
    once = clean_extraction_artifacts(html)
    twice = clean_extraction_artifacts(once)
    assert once == twice


def test_empty_and_no_op() -> None:
    assert clean_extraction_artifacts("") == ""
    clean = "<p>Already clean text.</p>"
    assert clean_extraction_artifacts(clean) == clean
