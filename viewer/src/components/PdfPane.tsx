import { useEffect, useRef } from "react";
import * as pdfjsLib from "pdfjs-dist";
import pdfjsWorker from "pdfjs-dist/build/pdf.worker.mjs?url";
import { shouldSendCredentials } from "../lib/documentUrl";

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker;

type PdfPaneProps = {
  src: string;
  onPageCount?: (pageCount: number) => void;
};

export function PdfPane({ src, onPageCount }: PdfPaneProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const container = containerRef.current;
    if (!container) return;

    (async () => {
      const loadingTask = pdfjsLib.getDocument({
        url: src,
        withCredentials: shouldSendCredentials(src),
      });
      const pdf = await loadingTask.promise;
      if (cancelled) return;
      onPageCount?.(pdf.numPages);
      container.innerHTML = "";
      for (let i = 1; i <= pdf.numPages; i++) {
        const page = await pdf.getPage(i);
        if (cancelled) return;
        const viewport = page.getViewport({ scale: 1.35 });
        const canvas = document.createElement("canvas");
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        canvas.setAttribute("role", "img");
        canvas.setAttribute("aria-label", `PDF page ${i} of ${pdf.numPages}`);
        const ctx = canvas.getContext("2d")!;
        await page.render({ canvasContext: ctx, viewport }).promise;
        container.appendChild(canvas);
      }
    })().catch((e) => {
      if (!cancelled) {
        const msg = document.createElement("p");
        msg.textContent = `Could not render PDF: ${String(e)}`;
        msg.setAttribute("role", "alert");
        container.replaceChildren(msg);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [src, onPageCount]);

  return (
    <div ref={containerRef} className="pdf-pane" aria-busy="false">
      <p className="loading-msg">Loading PDF…</p>
    </div>
  );
}
