# Remedy Access

Open-source, self-hostable [DocAccess](https://docaccess.com)-style overlay that turns every PDF link on your site into an accessible HTML transcript, plus an "Ask a Question" panel grounded in the document's own content.

Drop one script tag onto your page:

```html
<script async src="https://access.project-remedy.com/docbox.js"></script>
```

Every `<a href="*.pdf">` is wrapped in an overlay with the original PDF on one side and an AI-remediated, **WCAG 2.1 AA**-targeted semantic HTML transcript on the other, plus a document outline, translate controls, and an "Ask a Question" panel that answers using only the document's own content.

Generated transcripts are validated against WCAG 2.1 Level AA via `axe-core` checks in the test suite.

## AI/LLM stack (current state)

This codebase is mid-migration from Google Gemini to Ollama Cloud. Today the active paths are:

- **Translate**: Ollama Cloud (`backend/app/routes/translate.py` -> `ollama_client`).
- **Layout critique agent** (optional, off by default): Ollama Cloud, `kimi-k2.6:cloud`.
- **Transcript / remediation**: still uses Gemini (`backend.app.gemini.remediate`, `plan`, `design`). Configurable to Ollama via `LLM_PROVIDER=ollama` for the structured/plan path; full remediation fallback still calls Gemini.
- **Ask a Question**: still uses Gemini File Search (`backend.app.gemini.file_search`).
- **LiveKit + Gemini Live voice agent**: code present under `agent/` but disabled in `docker-compose.yml` and not a preservation target.

In practice, a working deployment today still wants both `OLLAMA_API_KEY` and `GOOGLE_API_KEY`. The production env example (`.env.production.example`) sets `LLM_PROVIDER=ollama` and provides both keys.

## Quickstart

1. Get an Ollama Cloud API key: <https://ollama.com>
2. (For now) get a Google AI Studio API key for the Ask panel and Gemini-backed remediation: <https://aistudio.google.com/apikey>
3. `cp .env.example .env` and fill in `OLLAMA_API_KEY` and `GOOGLE_API_KEY`.
4. `docker compose up --build`
5. Open <http://localhost:1337/demo/> and click any PDF.

The `docker-compose.yml` publishes the API on host port `1337`. The container itself listens on `$PORT` (defaults to `8000` in the Dockerfile, overridden to `1337` by compose).

The demo site deliberately skips the cache so visitors watch the live pipeline run behind a progress bar.

## Pre-convert your domain

```bash
python scripts/crawl.py https://your-site.example --sitemap
```

This discovers every PDF on the domain (or in a `sitemap.xml`) and pre-converts each so subsequent clicks are cache hits.

## Voice agent (LiveKit + Gemini Live, disabled by default)

The voice-agent worker under `agent/` and the standalone `access-remedy-livekit-agent/` are kept in tree but are not part of the default deploy. The compose `agent` service is commented out, and the migration plan no longer treats Gemini Live as a preservation target. Treat this path as experimental/legacy.

## Deploy to Cloud Run

`cloudrun.yaml` deploys a service named `access-remedy`:

```bash
gcloud run deploy access-remedy \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars=PUBLIC_ORIGIN=https://access.project-remedy.com \
  --set-secrets=GOOGLE_API_KEY=GOOGLE_API_KEY:latest,OLLAMA_API_KEY=OLLAMA_API_KEY:latest
gcloud run domain-mappings create \
  --service access-remedy \
  --domain access.project-remedy.com \
  --region us-central1
```

Note: the Cloud Run manifest and image path still use the legacy `access-remedy` service name. Folder/slug rename to `remedy-access` is in progress; manifests have not been renamed yet.

## Deploy to a VPS

```bash
cp .env.production.example .env.production
$EDITOR .env.production
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

The production stack puts Caddy in front of the FastAPI container on a private network. See `DEPLOYMENT.md` for HTTPS, auth, rate-limit, and backup details.

## Required env vars

From `.env.example` and `backend/app/config.py`:

- `GOOGLE_API_KEY` - currently required for transcript remediation and Ask panel.
- `OLLAMA_API_KEY` - required when `OLLAMA_BASE_URL` is `https://ollama.com/api` (the default), which translate and the layout agent use.
- `OLLAMA_BASE_URL` - defaults to `https://ollama.com/api`.
- `OLLAMA_PLAN_MODEL`, `OLLAMA_REMEDIATION_MODEL` - default `qwen3-vl:235b-cloud` in code; `.env.production.example` sets them to `kimi-k2.5:cloud`.
- `LAYOUT_AGENT_MODEL` - defaults to `kimi-k2.6:cloud`.
- `GEMINI_REMEDIATION_MODEL` (`gemini-2.5-flash-lite`), `GEMINI_ASK_MODEL` (`gemini-2.5-flash`), `GEMINI_LIVE_MODEL`.
- `PUBLIC_ORIGIN` - must match the origin serving `docbox.js`.
- `APP_API_KEY` - optional, gates `/api/*` with `X-API-Key` when set.
- `DATA_DIR`, `MAX_PDF_BYTES`.
- LiveKit (`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`) - all optional; voice agent is disabled when blank.
- LlamaParse (`LLAMA_CLOUD_API_KEY`, `LLAMAPARSE_*`) - required only when `STRUCTURED_RENDER_V2_PDF_BACKEND=llamaparse`.

## Tech

- Python 3.13 + FastAPI (`backend/`)
- Ollama Cloud (`kimi-k2.6:cloud`, `qwen3-vl:235b-cloud`) for translate and the layout agent
- Google Gemini (`gemini-2.5-flash-lite`, `gemini-2.5-flash`) and Gemini File Search for the remaining transcript/Ask paths
- React 19 + Vite + PDF.js viewer in a sandboxed iframe (`viewer/`, package `access-remedy-viewer`)
- Vanilla TS `docbox.js` embed (`embed/`, package `access-remedy-embed`)
- Optional LlamaParse PDF backend
- SQLite content-hash cache (`aiosqlite`)
- LiveKit Agents + Gemini Live (`agent/`, package `access-remedy-agent`) - disabled

## Repo layout

- `backend/` - FastAPI app, ingest/transcript/translate/ask routes, parser backends, Ollama and Gemini clients
- `viewer/` - React PDF viewer rendered inside the docbox iframe
- `embed/` - tiny `docbox.js` host-page script
- `agent/` - LiveKit voice agent worker (legacy, disabled)
- `access-remedy-livekit-agent/` - older standalone voice agent (legacy)
- `access-remedy-integration/` - integration scaffolding
- `demo/` - local demo site mounted at `/demo/`
- `scripts/` - crawler, prewarm, evaluation tooling
- `cloudrun.yaml`, `Dockerfile`, `docker-compose.yml`, `docker-compose.prod.yml`, `Caddyfile`

## License

MIT. See `LICENSE`.
