import { useEffect, useState } from "react";
import { getLiveToken } from "../lib/api";
import { startLiveSession, type LiveSession } from "../lib/livekit";

export function AssistPanel({ src }: { src: string }) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [session, setSession] = useState<LiveSession | null>(null);
  const [error, setError] = useState("");
  const [connecting, setConnecting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/healthz")
      .then((r) => r.json())
      .then((d) => !cancelled && setEnabled(Boolean(d.livekit_configured)))
      .catch(() => !cancelled && setEnabled(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const startCall = async () => {
    setConnecting(true);
    setError("");
    try {
      const tok = await getLiveToken(src);
      if (!tok.enabled || !tok.token || !tok.url) {
        setError("LiveKit is not configured on this server.");
        return;
      }
      const s = await startLiveSession({
        url: tok.url,
        token: tok.token,
        onAgentAudio: (track) => {
          const el = document.createElement("audio");
          el.autoplay = true;
          el.srcObject = new MediaStream([track]);
          el.setAttribute("aria-label", "Voice agent");
          document.body.appendChild(el);
        },
      });
      setSession(s);
    } catch (e) {
      setError(String(e));
    } finally {
      setConnecting(false);
    }
  };

  const endCall = async () => {
    if (session) {
      await session.disconnect();
      setSession(null);
    }
  };

  if (enabled === null) return <p>Checking voice agent availability…</p>;

  return (
    <div className="assist-panel">
      <h3>Live accessibility assistant</h3>
      <p>
        Speak with a voice agent that can see this window and help you navigate
        the document, read it aloud, or fill in a form.
      </p>
      {!enabled && (
        <p className="warning">
          Voice agent is not configured on this deployment. The host needs to
          set <code>LIVEKIT_URL</code>, <code>LIVEKIT_API_KEY</code>, and{" "}
          <code>LIVEKIT_API_SECRET</code>, and run the agent worker.
        </p>
      )}
      {!session && (
        <button
          type="button"
          onClick={startCall}
          disabled={!enabled || connecting}
        >
          {connecting ? "Connecting…" : "Call Agent"}
        </button>
      )}
      {session && (
        <button type="button" onClick={endCall}>
          End call
        </button>
      )}
      {error && <p role="alert" className="error">{error}</p>}
    </div>
  );
}
