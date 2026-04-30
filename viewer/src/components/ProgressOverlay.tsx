import type { IngestEvent } from "../lib/api";

const STEP_LABEL: Record<IngestEvent["step"], string> = {
  started: "Starting",
  fetching: "Fetching PDF",
  fetched: "Fetched",
  planning: "Planning structure",
  planned: "Structure ready",
  indexing: "Indexing for Q&A",
  indexed: "Indexed",
  generating: "Remediating with Gemini",
  done: "Ready",
  error: "Error",
};

export function ProgressOverlay({ event }: { event: IngestEvent }) {
  const pct = Math.max(0, Math.min(100, event.pct));
  return (
    <div className="progress-overlay" role="status" aria-live="polite">
      <div className="progress-card">
        <h2>Converting to Accessible Transcript</h2>
        <p className="progress-step">{STEP_LABEL[event.step] ?? event.step}</p>
        {event.message && <p className="progress-msg">{event.message}</p>}
        <div
          className="progress-bar"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
          aria-label="Conversion progress"
        >
          <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
        </div>
      </div>
    </div>
  );
}
