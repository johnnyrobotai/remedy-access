import { useState } from "react";
import type { Transcript } from "../lib/api";
import { translateTranscript } from "../lib/api";

const LANGUAGES = [
  { value: "es", label: "Spanish" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
  { value: "zh-Hans", label: "Chinese" },
  { value: "vi", label: "Vietnamese" },
];

type TranslatePanelProps = {
  src: string;
  transcript: Transcript;
  hasTranslation: boolean;
  onTranslated: (html: string, lang: string) => void;
  onClearTranslation: () => void;
};

export function TranslatePanel({
  src,
  transcript,
  hasTranslation,
  onTranslated,
  onClearTranslation,
}: TranslatePanelProps) {
  const [targetLang, setTargetLang] = useState("es");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const disabled = busy || transcript.render_mode === "interactive";

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const result = await translateTranscript(src, targetLang);
      onTranslated(result.html, result.target_lang);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="translate-panel">
      <label htmlFor="target-language">Language</label>
      <select
        id="target-language"
        value={targetLang}
        disabled={disabled}
        onChange={(e) => setTargetLang(e.target.value)}
      >
        {LANGUAGES.map((lang) => (
          <option key={lang.value} value={lang.value}>
            {lang.label}
          </option>
        ))}
      </select>
      <button type="button" disabled={disabled} onClick={submit}>
        {busy ? "Translating" : "Translate"}
      </button>
      {hasTranslation && (
        <button type="button" onClick={onClearTranslation}>
          Show original transcript
        </button>
      )}
      {transcript.render_mode === "interactive" && (
        <p className="empty-state">Translation is unavailable for interactive worksheets.</p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
