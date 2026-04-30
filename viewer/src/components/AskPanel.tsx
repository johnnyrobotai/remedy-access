import { useState } from "react";
import { ask } from "../lib/api";

type Answer = {
  answer: string;
  citations: { text: string; heading_id: string | null }[];
};

export function AskPanel({ src }: { src: string }) {
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<Answer | null>(null);
  const [error, setError] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!q.trim()) return;
    setLoading(true);
    setError("");
    try {
      const r = await ask(src, q);
      setResult(r);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="ask-panel">
      <form onSubmit={submit}>
        <label htmlFor="ask-q" className="ask-label">
          Ask a question about this document
        </label>
        <textarea
          id="ask-q"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          rows={3}
          placeholder="e.g. What is the filing deadline?"
        />
        <button type="submit" disabled={loading || !q.trim()}>
          {loading ? "Asking…" : "Ask"}
        </button>
      </form>
      {error && <p role="alert" className="error">{error}</p>}
      {result && (
        <section aria-label="Answer" className="ask-answer">
          <p>{result.answer}</p>
          {result.citations.length > 0 && (
            <details>
              <summary>Sources</summary>
              <ul>
                {result.citations.map((c, i) => (
                  <li key={i}>
                    {c.heading_id ? (
                      <a href={`#${c.heading_id}`}>{c.text}</a>
                    ) : (
                      <span>{c.text}</span>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}
    </div>
  );
}
