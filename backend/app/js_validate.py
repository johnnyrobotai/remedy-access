"""Inline `<script>` syntax validation.

Gemini occasionally emits JavaScript with literal parse errors that our
Python-side WCAG gate doesn't catch — the HTML is structurally fine but
the calculator is silently broken at runtime. This module extracts every
inline `<script>` block and parses it with acorn (shelled out via `node
-e`) so we can reject the output before caching it.

Failure modes:
- No `<script>` tags → returns `[]` (nothing to validate).
- `node` not on PATH or acorn not installed → returns `[]` (degrade
  gracefully; we'd rather ship transcripts on hosts without node than
  block ingest).
- Parse error in any block → returns a list of human-readable errors.

Format-agnostic: the caller (XLSX interactive path in `remediate.py`)
decides what to do on failure. This module just parses.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess

log = logging.getLogger(__name__)

# Greedy-free: stop at the first closing tag. We ignore `<script src="...">`
# (external scripts wouldn't survive the a11y gate anyway, but be safe) and
# only validate blocks that have actual inline bodies.
_SCRIPT_RE = re.compile(
    r"<script\b(?P<attrs>[^>]*)>(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)

_NODE_SNIPPET = r"""
const vm = require('vm');
let src = '';
process.stdin.on('data', (c) => { src += c; });
process.stdin.on('end', () => {
  try {
    new vm.Script(src, { filename: 'inline-script.js' });
    process.stdout.write('OK');
  } catch (e) {
    process.stdout.write('ERR:' + e.message);
  }
});
"""


def _has_script_body(html: str) -> bool:
    for m in _SCRIPT_RE.finditer(html):
        if m.group("body").strip():
            return True
    return False


def validate_inline_scripts(html: str) -> list[str]:
    """Parse every inline `<script>` block with acorn. Return a list of
    error messages (empty = OK, no scripts = OK).

    Prefers Node's built-in parser when available. If the local `node`
    binary exists but cannot execute cleanly, falls back to the Python
    `esprima` package. If neither runtime is usable, degrades gracefully:
    we'd rather let remediation proceed than block ingest outright.
    """
    if not _has_script_body(html):
        return []

    node_bin = shutil.which("node")
    if node_bin:
        node_errors = _validate_with_node(html, node_bin=node_bin)
        if node_errors is not None:
            return node_errors
        log.info("node exists but could not execute cleanly; falling back to esprima")
    else:
        log.info("node not on PATH; falling back to esprima for inline <script> validation")

    esprima_errors = _validate_with_esprima(html)
    if esprima_errors is not None:
        return esprima_errors

    log.info("no usable JS parser available; skipping inline <script> validation")
    return []


def _validate_with_node(html: str, *, node_bin: str) -> list[str] | None:
    errors: list[str] = []
    for idx, m in enumerate(_SCRIPT_RE.finditer(html), start=1):
        body = m.group("body")
        if not body.strip():
            continue
        try:
            proc = subprocess.run(
                [node_bin, "-e", _NODE_SNIPPET],
                input=body,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"<script> #{idx}: parse timed out after 5s")
            continue
        except OSError as e:
            log.info("node invocation failed (%s)", e)
            return None

        out = (proc.stdout or "").strip()
        if out.startswith("ERR:"):
            errors.append(f"<script> #{idx}: {out[4:]}")
            continue
        if out == "OK":
            continue

        stderr = (proc.stderr or "").strip()
        log.info("node parser did not return OK (stderr=%s)", stderr[:200])
        return None
    return errors


def _validate_with_esprima(html: str) -> list[str] | None:
    try:
        import esprima
    except ImportError:
        return None

    errors: list[str] = []
    for idx, m in enumerate(_SCRIPT_RE.finditer(html), start=1):
        body = m.group("body")
        if not body.strip():
            continue
        try:
            esprima.parseScript(body, tolerant=False)
        except Exception as e:  # noqa: BLE001
            errors.append(f"<script> #{idx}: {e}")
    return errors
