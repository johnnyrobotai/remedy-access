import { useEffect, useRef } from "react";

/**
 * Renders the server-provided accessible HTML transcript.
 *
 * `renderMode="static"` (default) sets innerHTML and trusts the backend
 * to have rejected any <script> in the WCAG gate.
 *
 * `renderMode="interactive"` is the XLSX calculator path: the backend
 * explicitly permits one inline <script> so the spreadsheet's formulas
 * work as live recomputation. Browsers don't execute <script> inserted
 * via innerHTML, so we re-create each script node after inserting the
 * HTML. The viewer already runs in a sandboxed iframe — the script can
 * only touch the transcript DOM, not the host page.
 */
export function TranscriptPane({
  html,
  renderMode = "static",
}: {
  html: string;
  renderMode?: "static" | "interactive";
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = ref.current;
    if (!root) return;
    root.innerHTML = html;
    if (renderMode !== "interactive") return;

    const scripts = Array.from(root.querySelectorAll("script"));
    for (const original of scripts) {
      const replacement = document.createElement("script");
      for (const attr of Array.from(original.attributes)) {
        replacement.setAttribute(attr.name, attr.value);
      }
      replacement.text = original.textContent ?? "";
      original.replaceWith(replacement);
    }
  }, [html, renderMode]);

  return <article ref={ref} className="transcript-pane" />;
}
