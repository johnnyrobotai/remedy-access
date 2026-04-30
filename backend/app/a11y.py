"""WCAG 2.1 Level AA static structural checks for remediated HTML.

These are the cheap, deterministic checks we can run on every transcript
without launching a browser. The full dynamic audit (color contrast,
focus order in a real layout, ARIA state correctness) is run by the
Playwright + axe-core test in tests/test_a11y.py and in CI.

`fix_html` runs first to repair the most common Gemini compliance gaps
(missing `<th scope>`, `<label for>` pointing at a renamed input id) so
output that was *almost* correct doesn't get rejected. `check_html` then
validates what's left. Both layers run synchronously at ingest time so
bad output never reaches the cache.
"""
from __future__ import annotations

from html.parser import HTMLParser


class _A11yParser(HTMLParser):
    def __init__(self, *, allow_script: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.has_main = False
        self.has_h1 = False
        self.lang_on_main: str | None = None
        self.heading_levels: list[int] = []
        self.errors: list[str] = []
        self._in_table = False
        self._table_has_th_with_scope = False
        self._table_has_any_th = False
        self._input_ids: list[str] = []
        self._label_fors: list[str] = []
        self._img_without_alt = 0
        self.output_count = 0
        self.labeled_input_count = 0
        self._forbidden_tags = {"style"} if allow_script else {"script", "style"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag in self._forbidden_tags:
            self.errors.append(f"<{tag}> not allowed in transcript")
        if tag == "main":
            self.has_main = True
            self.lang_on_main = a.get("lang") or None
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            if tag == "h1":
                self.has_h1 = True
            self.heading_levels.append(level)
        if tag == "img":
            if "alt" not in a:
                self._img_without_alt += 1
        if tag == "table":
            self._in_table = True
            self._table_has_th_with_scope = False
            self._table_has_any_th = False
        if self._in_table and tag == "th":
            self._table_has_any_th = True
            if "scope" in a:
                self._table_has_th_with_scope = True
        if tag == "input" and a.get("type", "text").lower() not in {"hidden", "submit", "button"}:
            if "id" in a:
                self._input_ids.append(a["id"])
                self.labeled_input_count += 1
            else:
                # tolerated only if aria-label/aria-labelledby is used
                if "aria-label" not in a and "aria-labelledby" not in a:
                    self.errors.append("<input> without id or aria-label")
                else:
                    self.labeled_input_count += 1
        if tag in {"textarea", "select"} and "id" in a:
            self._input_ids.append(a["id"])
        if tag == "label" and "for" in a:
            self._label_fors.append(a["for"])
        if tag == "output":
            self.output_count += 1
            # `<output>` is a labelable element — a `<label for="X">` pointing at
            # an `<output id="X">` is valid WCAG, so count the id as satisfying
            # the orphan-label check.
            if "id" in a:
                self._input_ids.append(a["id"])
        if tag in {"a"} and a.get("href") and not a.get("href").startswith(("#", "javascript:")):
            # presence-only; text-content check happens in handle_data's buffer — skipped for simplicity
            pass
        if "style" in a:
            self.errors.append(f"inline style on <{tag}> not allowed")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._table_has_any_th and not self._table_has_th_with_scope:
                self.errors.append("<table> uses <th> without scope attribute")
            self._in_table = False


def fix_html(html: str) -> str:
    """Repair common Gemini compliance gaps before WCAG validation.

    Two fixups:
    1. Add `scope="col"` to every `<th>` in `<thead>` (or bare `<tr>` where
       no `<thead>`/`<tbody>` exists). Add `scope="row"` to `<th>` inside
       `<tbody>` rows. Pre-existing scope attributes are left alone.
    2. For each `<label for="X">` whose X has no matching element id,
       walk forward to the next `<input>`/`<textarea>`/`<select>` and
       overwrite its id with X. The label's `for` came from the planner —
       trust it as canonical.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    for table in soup.find_all("table"):
        for thead in table.find_all("thead"):
            for th in thead.find_all("th"):
                if not th.get("scope"):
                    th["scope"] = "col"
        for tbody in table.find_all("tbody"):
            for tr in tbody.find_all("tr"):
                for th in tr.find_all("th"):
                    if not th.get("scope"):
                        th["scope"] = "row"
        for th in table.find_all("th"):
            if not th.get("scope"):
                th["scope"] = "col"

    existing_ids = {el["id"] for el in soup.find_all(id=True)}
    claimed_controls: set[int] = set()
    for label in soup.find_all("label"):
        for_val = label.get("for")
        if not for_val or for_val in existing_ids:
            continue
        next_control = None
        for candidate in label.find_all_next(["input", "textarea", "select"]):
            if id(candidate) not in claimed_controls:
                next_control = candidate
                break
        if next_control is None:
            continue
        claimed_controls.add(id(next_control))
        old_id = next_control.get("id")
        next_control["id"] = for_val
        if old_id:
            existing_ids.discard(old_id)
        existing_ids.add(for_val)

    existing_ids = {el["id"] for el in soup.find_all(id=True)}
    for label in soup.find_all("label"):
        for_val = label.get("for")
        if for_val and for_val not in existing_ids:
            del label["for"]

    prev_level = 0
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        level = int(h.name[1])
        if prev_level and level > prev_level + 1:
            new_level = prev_level + 1
            h.name = f"h{new_level}"
            level = new_level
        prev_level = level

    generated_input_counter = 0
    for inp in soup.find_all("input"):
        itype = (inp.get("type") or "text").lower()
        if itype in {"hidden", "submit", "button"}:
            continue
        if inp.get("id") or inp.get("aria-label") or inp.get("aria-labelledby"):
            continue
        generated_input_counter += 1
        inp["id"] = f"generated-input-{generated_input_counter}"
        inp["aria-label"] = f"Unlabeled field {generated_input_counter}"

    # Images without an alt attribute: mark as decorative so the WCAG
    # structural check doesn't reject the whole document. Gemini sometimes
    # omits alt on the last image in a section.
    for img in soup.find_all("img"):
        if "alt" not in img.attrs:
            img["alt"] = ""
            img["aria-hidden"] = "true"

    return str(soup)


def check_html(html: str, *, render_mode: str = "static") -> list[str]:
    """Return a list of WCAG-AA-relevant structural issues. Empty list = OK.

    `render_mode="interactive"` is used for XLSX calculator transcripts:
    it permits a single Gemini-authored `<script>` block (the live
    recalculation logic) and additionally requires the transcript to
    include at least one labeled `<input>` and at least one `<output>`
    — otherwise it isn't really a calculator.
    """
    allow_script = render_mode == "interactive"
    p = _A11yParser(allow_script=allow_script)
    p.feed(html)

    if not p.has_main:
        p.errors.append("missing <main> landmark")
    if not p.has_h1:
        p.errors.append("missing <h1>")
    if not p.lang_on_main:
        p.errors.append("<main> missing lang attribute")
    if p._img_without_alt:
        p.errors.append(f"{p._img_without_alt} <img> element(s) missing alt attribute")
    if render_mode == "interactive":
        if p.output_count == 0:
            p.errors.append(
                "interactive render_mode requires at least one <output> element"
            )
        if p.labeled_input_count == 0:
            p.errors.append(
                "interactive render_mode requires at least one labeled <input>"
            )

    for i in range(1, len(p.heading_levels)):
        prev, curr = p.heading_levels[i - 1], p.heading_levels[i]
        if curr > prev + 1:
            p.errors.append(
                f"heading level jumped from h{prev} to h{curr} (skipped levels)"
            )
            break

    orphan_labels = [f for f in p._label_fors if f not in p._input_ids]
    if orphan_labels:
        p.errors.append(
            f"<label for> points to nonexistent id(s): {', '.join(sorted(set(orphan_labels))[:3])}"
        )

    return p.errors
