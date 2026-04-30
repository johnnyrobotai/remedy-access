export type OutlineNode = {
  id: string;
  level: number;
  text: string;
  parent: string | null;
};

export type DocFormat = "pdf" | "docx" | "xlsx";
export type RenderMode = "static" | "interactive";

export type Transcript = {
  sha256: string;
  source_url: string;
  html: string;
  outline: OutlineNode[];
  title: string | null;
  description: string | null;
  page_count: number;
  format: DocFormat;
  render_mode: RenderMode;
  cached: boolean;
};

export type IngestEvent = {
  step:
    | "started"
    | "fetching"
    | "fetched"
    | "planning"
    | "planned"
    | "indexing"
    | "indexed"
    | "generating"
    | "done"
    | "error";
  pct: number;
  message: string;
  payload?: Transcript;
};

export function apiBase(): string {
  // Viewer is served from the backend, so same origin works in production.
  // In `vite dev` the proxy forwards /api to :8000.
  return "";
}

export async function getCachedTranscript(src: string): Promise<Transcript | null> {
  const resp = await fetch(`${apiBase()}/api/transcript?src=${encodeURIComponent(src)}`);
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`transcript fetch failed: ${resp.status}`);
  return resp.json();
}

export function openIngestStream(
  src: string,
  onEvent: (ev: IngestEvent) => void,
  onError: (err: string) => void
): () => void {
  const url = `${apiBase()}/api/transcript/ingest?src=${encodeURIComponent(src)}`;
  const es = new EventSource(url);
  let completed = false;

  const handle = (ev: MessageEvent) => {
    try {
      const parsed = JSON.parse(ev.data) as IngestEvent;
      if (parsed.step === "done" || parsed.step === "error") {
        completed = true;
      }
      onEvent(parsed);
      if (completed) {
        es.close();
      }
    } catch (e) {
      onError(`parse failed: ${String(e)}`);
    }
  };

  for (const name of [
    "started",
    "fetching",
    "fetched",
    "planning",
    "planned",
    "indexing",
    "indexed",
    "generating",
    "done",
    "error",
  ]) {
    es.addEventListener(name, handle as EventListener);
  }
  es.onerror = () => {
    if (completed) return;
    es.close();
    onError("stream disconnected");
  };

  return () => {
    completed = true;
    es.close();
  };
}

export async function ask(
  src: string,
  question: string
): Promise<{ answer: string; citations: { text: string; heading_id: string | null }[] }> {
  const resp = await fetch(`${apiBase()}/api/ask`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ src, question }),
  });
  if (!resp.ok) throw new Error(`ask failed: ${resp.status}`);
  return resp.json();
}

export async function getLiveToken(src: string): Promise<{ enabled: boolean; token?: string; url?: string; room?: string }> {
  const resp = await fetch(`${apiBase()}/api/live-token`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ src }),
  });
  if (!resp.ok) return { enabled: false };
  return resp.json();
}

export async function translateTranscript(
  src: string,
  targetLang: string
): Promise<{ html: string; target_lang: string }> {
  const resp = await fetch(`${apiBase()}/api/translate`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ src, target_lang: targetLang }),
  });
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`translate failed: ${resp.status} ${body}`);
  }
  return resp.json();
}
