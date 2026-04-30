import { useEffect, useRef, useState } from "react";
import { PdfPane } from "./components/PdfPane";
import { DocxPane } from "./components/DocxPane";
import { XlsxPane } from "./components/XlsxPane";
import { TranscriptPane } from "./components/TranscriptPane";
import { ProgressOverlay } from "./components/ProgressOverlay";
import { DrawerTabs } from "./components/DrawerTabs";
import {
  type DocFormat,
  type IngestEvent,
  type Transcript,
  getCachedTranscript,
  openIngestStream,
} from "./lib/api";
import { normalizeDocumentSrc } from "./lib/documentUrl";

type ViewMode = "original" | "transcript";

const ORIGINAL_LABELS: Record<DocFormat, string> = {
  pdf: "Print-Friendly View",
  docx: "Word View",
  xlsx: "Spreadsheet View",
};

const ORIGINAL_ARIA: Record<DocFormat, string> = {
  pdf: "Original PDF",
  docx: "Original Word document",
  xlsx: "Original Excel workbook",
};

export function App() {
  const rawSrc = new URLSearchParams(window.location.search).get("src") || "";
  const src = normalizeDocumentSrc(rawSrc);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [view, setView] = useState<ViewMode>("original");
  const [progress, setProgress] = useState<IngestEvent | null>(null);
  const [error, setError] = useState<string>("");
  const [translatedHtml, setTranslatedHtml] = useState<string | null>(null);
  const [translatedLang, setTranslatedLang] = useState<string | null>(null);
  const [originalPageCount, setOriginalPageCount] = useState<number | null>(null);
  const closerRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (!src) {
      setError("Missing ?src= query parameter");
      return;
    }

    let cancelled = false;
    setOriginalPageCount(null);
    (async () => {
      try {
        const cached = await getCachedTranscript(src);
        if (cached && !cancelled) {
          setTranscript(cached);
          return;
        }
        setProgress({ step: "started", pct: 0, message: "Starting" });
        closerRef.current = openIngestStream(
          src,
          (ev) => {
            if (cancelled) return;
            setProgress(ev);
            if (ev.step === "done" && ev.payload) {
              setTranscript(ev.payload);
              setProgress(null);
            } else if (ev.step === "error") {
              setError(ev.message || "ingest failed");
            }
          },
          (err) => !cancelled && setError(err)
        );
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();

    return () => {
      cancelled = true;
      closerRef.current?.();
    };
  }, [src]);

  const format: DocFormat = transcript?.format ?? "pdf";
  const renderMode = transcript?.render_mode ?? "static";

  return (
    <div className="shell">
      <header className="topbar" role="banner">
        <label
          className="toggle"
          aria-label={`Switch between ${ORIGINAL_LABELS[format]} and accessible transcript`}
        >
          <span className={`toggle-label ${view === "original" ? "active" : ""}`}>
            {ORIGINAL_LABELS[format]}
          </span>
          <input
            type="checkbox"
            role="switch"
            aria-checked={view === "transcript"}
            checked={view === "transcript"}
            onChange={(e) => setView(e.target.checked ? "transcript" : "original")}
          />
          <span className={`toggle-label ${view === "transcript" ? "active" : ""}`}>
            Accessible Transcript View
          </span>
        </label>
        <div className="topbar-spacer" />
        <h1 className="doc-title">{transcript?.title ?? " "}</h1>
      </header>

      <main id="content" className="layout" aria-live="polite">
        {error && <div className="error" role="alert">{error}</div>}

        {progress && <ProgressOverlay event={progress} />}

        {transcript && (
          <>
            <section
              aria-label={ORIGINAL_ARIA[format]}
              className={`pane ${view === "original" ? "active" : "hidden-by-toggle"}`}
            >
              {format === "pdf" && <PdfPane src={src} onPageCount={setOriginalPageCount} />}
              {format === "docx" && <DocxPane src={src} onPageCount={setOriginalPageCount} />}
              {format === "xlsx" && <XlsxPane src={src} />}
            </section>
            <section
              aria-label={
                translatedLang
                  ? `Accessible HTML transcript, translated to ${translatedLang}`
                  : "Accessible HTML transcript"
              }
              className={`pane ${view === "transcript" ? "active" : "hidden-by-toggle"}`}
            >
              <TranscriptPane
                html={translatedHtml ?? transcript.html}
                renderMode={translatedHtml ? "static" : renderMode}
              />
            </section>
          </>
        )}

        {transcript && (
          <aside className="drawer" aria-label="Document tools">
            <DrawerTabs
              src={src}
              transcript={transcript}
              pageCount={originalPageCount ?? undefined}
              hasTranslation={translatedHtml !== null}
              onTranslated={(html, lang) => {
                setTranslatedHtml(html);
                setTranslatedLang(lang);
                setView("transcript");
              }}
              onClearTranslation={() => {
                setTranslatedHtml(null);
                setTranslatedLang(null);
              }}
              onOutlineJump={() => setView("transcript")}
            />
          </aside>
        )}
      </main>
    </div>
  );
}
