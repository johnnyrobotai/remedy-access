"""Post-processing cleanup of PDF text-extraction artifacts.

Gemini's remediated HTML sometimes preserves two artifacts that come
straight from the PDF text layer:

1. **Hyphenated line-breaks.** The PDF broke a word across lines (`re-\n
   ceptors`) and the extractor kept the hyphen and the newline verbatim.
   We join the halves and drop both.

2. **Noisy whitespace inside text nodes.** Multi-column PDFs get flattened
   with literal newlines mid-paragraph. Browsers collapse them to spaces
   at render time, but consumers of the raw HTML (the outline, the Ask
   panel, screen readers on platforms that honor \\n) see the broken text.

Both transforms apply only to text nodes and skip `<pre>`, `<code>`,
`<script>`, `<style>` — anywhere whitespace is semantically meaningful.
Kerning-induced mid-word spaces (`c orpuscle` → `corpuscle`) are left to
the prompt because they require dictionary knowledge we don't have here.
"""
from __future__ import annotations

import re

_HYPHEN_LINEBREAK = re.compile(r"([A-Za-z])-\s*\n\s*([a-z])")
_WHITESPACE_RUN = re.compile(r"[ \t]*\n[ \t\n]*")
_MULTISPACE = re.compile(r"  +")

_PROTECTED_TAGS = frozenset({"pre", "code", "script", "style"})


def _clean_text(value: str) -> str:
    # Rejoin hyphenated line breaks: `re-\nceptors` → `receptors`.
    value = _HYPHEN_LINEBREAK.sub(r"\1\2", value)
    # Collapse any remaining newline runs (often from multi-column flattening)
    # to single spaces so the rendered text flows cleanly.
    value = _WHITESPACE_RUN.sub(" ", value)
    # Collapse accidental multi-spaces introduced by the previous step.
    value = _MULTISPACE.sub(" ", value)
    return value


def clean_extraction_artifacts(html: str) -> str:
    """Strip PDF extraction artifacts from every user-visible text node.

    Safe on output that has no artifacts (no-op). Safe on the XLSX
    interactive calculator: `<script>` content is untouched.
    """
    from bs4 import BeautifulSoup, NavigableString

    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(string=True):
        parent = node.parent
        if parent is not None and parent.name in _PROTECTED_TAGS:
            continue
        original = str(node)
        cleaned = _clean_text(original)
        if cleaned != original:
            node.replace_with(NavigableString(cleaned))
    return str(soup)
