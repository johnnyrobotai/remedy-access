import { useEffect, useRef } from "react";
import { shouldSendCredentials } from "../lib/documentUrl";

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function filenameFromSrc(src: string): string {
  try {
    const url = new URL(src, window.location.href);
    const filename = safeDecode(url.pathname.split("/").filter(Boolean).pop() || "");
    return filename || "spreadsheet.xlsx";
  } catch {
    const filename = src.split(/[?#]/)[0]?.split("/").filter(Boolean).pop();
    return filename ? safeDecode(filename) : "spreadsheet.xlsx";
  }
}

/**
 * Original-format pane for Excel workbooks. Renders each sheet as a
 * read-only HTML table via SheetJS. We don't load a heavyweight grid
 * library for the spike — the accessible calculator lives on the
 * Transcript side when render_mode === "interactive".
 */
export function XlsxPane({ src }: { src: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const filename = filenameFromSrc(src);

  useEffect(() => {
    let cancelled = false;
    const container = containerRef.current;
    if (!container) return;

    (async () => {
      const [XLSX, response] = await Promise.all([
        import("xlsx-js-style"),
        fetch(src, { credentials: shouldSendCredentials(src) ? "same-origin" : "omit" }),
      ]);
      if (!response.ok) throw new Error(`fetch failed: ${response.status}`);
      const buf = await response.arrayBuffer();
      if (cancelled) return;
      const wb = XLSX.read(buf, { type: "array", cellStyles: true });
      container.innerHTML = "";
      for (const name of wb.SheetNames) {
        const sheet = wb.Sheets[name];
        const heading = document.createElement("h2");
        heading.textContent = name;
        heading.className = "xlsx-sheet-name";
        const table = XLSX.utils.sheet_to_html(sheet, { id: `sheet-${name}` });
        const wrap = document.createElement("section");
        wrap.setAttribute("aria-label", `Sheet: ${name}`);
        wrap.appendChild(heading);
        const tableWrap = document.createElement("div");
        tableWrap.innerHTML = table;
        wrap.appendChild(tableWrap);
        container.appendChild(wrap);
      }
    })().catch((e) => {
      if (!cancelled) {
        const msg = document.createElement("p");
        msg.textContent = `Could not render Excel workbook: ${String(e)}`;
        msg.setAttribute("role", "alert");
        container.replaceChildren(msg);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [src]);

  return (
    <div className="xlsx-pane" aria-busy="false">
      <div className="xlsx-toolbar">
        <a
          className="xlsx-download-link"
          href={src}
          download={filename}
          aria-label={`Download spreadsheet file: ${filename}`}
        >
          Download spreadsheet file
        </a>
      </div>
      <div ref={containerRef} className="xlsx-workbook">
        <p className="loading-msg">Loading Excel workbook…</p>
      </div>
    </div>
  );
}
