import { useState } from "react";
import type { Transcript } from "../lib/api";
import { OutlinePanel } from "./OutlinePanel";
import { AskPanel } from "./AskPanel";
import { TranslatePanel } from "./TranslatePanel";
import { AssistPanel } from "./AssistPanel";

const TABS = [
  { id: "outline", label: "Outline" },
  { id: "ask", label: "Ask" },
  { id: "translate", label: "Translate" },
  { id: "assist", label: "Assist" },
] as const;

type TabId = (typeof TABS)[number]["id"];

type DrawerTabsProps = {
  src: string;
  transcript: Transcript;
  pageCount?: number;
  hasTranslation: boolean;
  onTranslated: (html: string, lang: string) => void;
  onClearTranslation: () => void;
  onOutlineJump: () => void;
};

export function DrawerTabs({
  src,
  transcript,
  pageCount,
  hasTranslation,
  onTranslated,
  onClearTranslation,
  onOutlineJump,
}: DrawerTabsProps) {
  const [active, setActive] = useState<TabId>("outline");
  const displayedPageCount = pageCount ?? transcript.page_count;
  const pageLabel =
    displayedPageCount === 1 ? "1 page" : `${displayedPageCount} pages`;

  return (
    <div className="drawer-tabs">
      <section className="drawer-summary" aria-label="Document summary">
        <h2>{transcript.title ?? "Document"}</h2>
        <p className="drawer-summary__meta">{pageLabel}</p>
        {transcript.description ? <p>{transcript.description}</p> : null}
      </section>

      <div role="tablist" aria-label="Document tools" className="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={active === t.id}
            aria-controls={`panel-${t.id}`}
            id={`tab-${t.id}`}
            className={active === t.id ? "tab active" : "tab"}
            onClick={() => setActive(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div
        id="panel-outline"
        role="tabpanel"
        aria-labelledby="tab-outline"
        hidden={active !== "outline"}
      >
        <OutlinePanel outline={transcript.outline} onJump={onOutlineJump} />
      </div>
      <div
        id="panel-ask"
        role="tabpanel"
        aria-labelledby="tab-ask"
        hidden={active !== "ask"}
      >
        <AskPanel src={src} />
      </div>
      <div
        id="panel-translate"
        role="tabpanel"
        aria-labelledby="tab-translate"
        hidden={active !== "translate"}
      >
        <TranslatePanel
          src={src}
          transcript={transcript}
          hasTranslation={hasTranslation}
          onTranslated={onTranslated}
          onClearTranslation={onClearTranslation}
        />
      </div>
      <div
        id="panel-assist"
        role="tabpanel"
        aria-labelledby="tab-assist"
        hidden={active !== "assist"}
      >
        <AssistPanel src={src} />
      </div>
    </div>
  );
}
