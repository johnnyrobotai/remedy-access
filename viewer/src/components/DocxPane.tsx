import { useEffect, useRef, useState } from "react";
import { PdfPane } from "./PdfPane";
import { shouldSendCredentials } from "../lib/documentUrl";

/**
 * Original-format pane for Word documents. Renders the raw .docx into
 * styled HTML via docx-preview so screen readers and sighted users see
 * a close-to-Word representation of the source. The accessible
 * remediated transcript is still served by TranscriptPane on the
 * other side of the toggle.
 */
type DocxPaneProps = {
  src: string;
  onPageCount?: (pageCount: number) => void;
};

export function DocxPane({ src, onPageCount }: DocxPaneProps) {
  const [pdfPreviewSrc, setPdfPreviewSrc] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    setPdfPreviewSrc(undefined);

    const candidate = companionPdfSrc(src);
    if (!candidate) {
      setPdfPreviewSrc(null);
      return () => {
        cancelled = true;
      };
    }

    (async () => {
      try {
        const response = await fetch(candidate, {
          method: "HEAD",
          credentials: shouldSendCredentials(candidate) ? "same-origin" : "omit",
        });
        if (!cancelled) setPdfPreviewSrc(response.ok ? candidate : null);
      } catch {
        if (!cancelled) setPdfPreviewSrc(null);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [src]);

  if (pdfPreviewSrc) return <PdfPane src={pdfPreviewSrc} onPageCount={onPageCount} />;
  if (pdfPreviewSrc === undefined) {
    return (
      <div className="docx-pane" aria-busy="true">
        <p className="loading-msg">Loading Word document…</p>
      </div>
    );
  }

  return <DocxHtmlPreview src={src} />;
}

function DocxHtmlPreview({ src }: { src: string }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const container = containerRef.current;
    if (!container) return;

    (async () => {
      const [{ renderAsync }, response] = await Promise.all([
        import("docx-preview"),
        fetch(src, { credentials: shouldSendCredentials(src) ? "same-origin" : "omit" }),
      ]);
      if (!response.ok) throw new Error(`fetch failed: ${response.status}`);
      const buf = await response.arrayBuffer();
      if (cancelled) return;
      container.innerHTML = "";
      await renderAsync(buf, container, undefined, {
        className: "docx-preview",
        inWrapper: true,
        ignoreWidth: true,
        ignoreHeight: true,
        breakPages: true,
      });
    })().catch((e) => {
      if (!cancelled) {
        const msg = document.createElement("p");
        msg.textContent = `Could not render Word document: ${String(e)}`;
        msg.setAttribute("role", "alert");
        container.replaceChildren(msg);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [src]);

  return (
    <div ref={containerRef} className="docx-pane" aria-busy="false">
      <p className="loading-msg">Loading Word document…</p>
    </div>
  );
}

function companionPdfSrc(src: string): string | null {
  try {
    const url = new URL(src, window.location.href);
    if (!/\.docx$/i.test(url.pathname)) return null;
    url.pathname = url.pathname.replace(/\.docx$/i, ".pdf");
    return url.href;
  } catch {
    if (!/\.docx(?:[?#]|$)/i.test(src)) return null;
    return src.replace(/\.docx(?=[?#]|$)/i, ".pdf");
  }
}
