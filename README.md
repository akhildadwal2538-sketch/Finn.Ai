# FINN — Financial Investment Agent

FINN is a mobile-first prototype for an investor's daily workflow: a prioritized portfolio intelligence feed (Push) and cited primary-source company research (Pull). It follows the ENAM challenge brief and its companion announcement taxonomy. Yahoo Finance provides cached EOD market data, and the announcement service queries NSE's public symbol/date filtered filing endpoint with a clearly labeled sample fallback if NSE is unavailable.

## Run locally

Requirements: Python 3.11+, Node.js 20.9+, npm.

```bash
cp .env.example .env
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000 --env-file .env
```

In another terminal, start the frontend:

```bash
npm install
npm run dev
```

Open http://localhost:3000. The UI works in clearly labeled demo mode without credentials. Its `/api/*` calls are proxied by Next.js to `FINN_BACKEND_URL`, so the browser and phone use one origin. Set `FINN_LLM_PROVIDER`, `FINN_LLM_BASE_URL`, `FINN_LLM_API_KEY`, and `FINN_LLM_MODEL` in the ignored server-side `.env.local` file to enable model-backed research. The current provider adapter supports OpenAI-compatible chat completions. With no configuration, FINN returns retrieved citations and an explicit setup state; it does not claim the model answered.

### Try FINN on a phone on the same Wi-Fi

With the backend already running, start the frontend on the LAN:

```bash
npm run dev:lan
```

## Deploy to Vercel without an LLM

The root [`vercel.json`](vercel.json) deploys the Next.js frontend and FastAPI backend as services in one Vercel project, with `/api/*` routed to FastAPI and all other paths to Next.js. Import this repository into Vercel; its GitHub App needs access to the `Finn.Ai` repository to deploy commits.

Open the resulting Vercel project URL on a phone. No LLM secret is needed: the research assistant remains in evidence-only mode until credentials are configured.

Vercel functions use a writable `/tmp` SQLite database per instance; portfolio edits, alerts, and uploaded/indexed documents are temporary and may not be shared between function instances or survive a cold start. The bundled demo portfolio remains available. Use a durable database before relying on saved user data.

## Deploy to Render

The root [`render.yaml`](render.yaml) defines two same-region services: a Next.js web service and a FastAPI service. In Render, create a new Blueprint from this GitHub repository and apply the Blueprint. The web service proxies `/api/*` requests to the backend over Render's private network.

Both services use Render's free compute plan. The backend stores its SQLite database on the service filesystem, which is ephemeral on this plan; portfolio edits and uploaded/indexed documents can reset after a restart or redeploy. Use a persistent disk or migrate the backend database to managed PostgreSQL before relying on stored user data. The LLM remains unconfigured until provider credentials are added to the backend service's environment.

Open `http://<computer-LAN-IP>:3001` on the phone. Find the computer's LAN IP in its network settings. Keep the phone and computer on the same Wi-Fi. The app uses the same-origin API proxy, so the phone does not try to contact its own `localhost` for API requests. This is local-network access, not a public deployment.

## Product decisions

- The home feed leads with what changed and why it matters; routine filings are collapsed below material developments.
- Portfolio market data uses Yahoo Finance chart API through a replaceable service adapter. EOD values are cached in SQLite and status/timestamp are returned with each response.
- Announcement collection uses NSE's public equity corporate-announcements endpoint, filtered per NSE portfolio symbol and date range, with a 15-minute SQLite cache. NSE-only coverage is labeled; BSE-only holdings currently receive no NSE filing matches. If all NSE requests fail, the service returns the clearly labeled sample corpus so the workflow remains demonstrable.
- Research ingestion accepts company primary-source PDF/HTML links or local PDFs, stores page-aware chunks, and fills eight research sections with matching cited excerpts. Without indexed evidence it returns an explicit unavailable message, never fabricated financial figures.
- Official NSE/BSE result filing cards expose on-demand Revenue, EBITDA, PBT, and PAT extraction. Rows retain source page citations; QoQ/YoY is calculated only from explicit aligned quarter columns. Missing rows stay unavailable.
- The document extractor surfaces literal Revenue, EBITDA, PBT, and PAT statement rows. QoQ/YoY percentages are calculated only when a row has the same number of values as explicit quarter labels in its source chunk; periods and units are never guessed.
- Sample announcement timestamps are generated relative to the current Indian market time so the no-credentials demo remains populated under the default 24-hour filter; every sample item remains labeled `sample`.
- When configured, the OpenAI-compatible model can summarize the four highest-priority live filings per request; summaries and materiality scores are cached for 24 hours and marked `AI`. If the model is not configured or fails, rule-based materiality and filing text remain labeled as such. The research assistant retrieves passages only for the selected company, carries up to eight recent chat turns, requests structured evidence/interpretation output, validates source citations, caches identical successful requests briefly, and reports missing configuration or provider errors explicitly.
- Announcement taxonomy covers the companion glossary and deterministic priority defaults. Official filing links, dates, exchange-provided filing descriptions, and live/cached/sample state are retained on each item.
- SQLite keeps setup simple; schema is initialized on API startup and can be migrated to PostgreSQL by changing the repository layer.

## Architecture

`app/` is the Next.js client. `backend/` is a FastAPI REST application, split into `services/` for market data, announcements, document retrieval, extraction, AI analysis, alerts, and portfolio data. `backend/data/finn.db` is created locally and is git-ignored. `backend/data/demo.json` contains explicitly sample-only announcements and seed portfolio metadata.

### API

- `GET /api/health` — service and source status
- `GET /api/market?range=1mo|3mo|6mo|1y` — Yahoo benchmark history for the selected chart range
- `GET /api/market/quote/{symbol}?exchange=NS|BO&range=1mo|3mo|6mo|1y` — supported company historical quote and volume series
- `GET /api/portfolio` — holdings plus cached/live market metrics
- `GET /api/companies/search?q=...` — Yahoo Finance NSE/BSE equity symbol lookup
- `POST /api/portfolio` and `DELETE /api/portfolio/{symbol}` — edit demo portfolio
- `GET /api/announcements?window=24h|7d|30d|all|custom&priority=...&category=...&symbol=...` — prioritized NSE portfolio feed; custom accepts `from_date` and `to_date` in `YYYY-MM-DD` format
- Add `demo=true` to the announcement query to load explicitly illustrative sample records even when exchange data is available
- `POST /api/announcements/highlights` — extract literal headline rows from a linked NSE/BSE results PDF, with page citations and explicit unavailable output
- `GET /api/alerts` / `PUT /api/alerts` — threshold and category preferences
- `POST /api/alerts/evaluate` — evaluate enabled price/volume thresholds and selected announcement categories, priorities, and companies
- `GET /api/research/{symbol}` — primary-source grounded company view, eight-section evidence dossier, and extracted financial rows
- `POST /api/research/{symbol}/documents` — ingest an uploaded PDF
- `POST /api/research/{symbol}/documents/url` — ingest a company IR, Screener, NSE, or BSE source URL
- `POST /api/research/{symbol}/ask` — evidence-grounded assistant response

All research claims contain source references when a document is available. Ingestion supports PDF text extraction and HTML text; page numbers are preserved for PDFs, while HTML sections are cited as sections rather than invented page numbers. The current lightweight retriever uses bounded overlapping chunks, token overlap, and SQLite persistence, with an interface that can be upgraded to embeddings without changing routes.

### Verification

```bash
npm run typecheck
npm run build
python3 -m unittest backend.test_services
```

The production build, TypeScript typecheck, and backend unit/API tests cover filing classification and source mapping, custom date validation, portfolio operations, quote lookup, alert persistence/evaluation, document retrieval, safe URL handling, and conservative financial extraction. The browser and backend use a same-origin API proxy so phone testing does not require a separate API URL.

## Data integrity and limitations

- Demo feed entries are marked `sample`; they are illustrative workflow examples, not claims about real companies or current events. Official NSE items retain `live` or `cached` labels and links.
- Market data is `live`, `cached`, or `unavailable`, with provider and timestamp. Yahoo may throttle requests or return incomplete coverage.
- Portfolio filing collection currently uses NSE. A BSE-only holding receives no filing matches unless a BSE listing enters through a future adapter. Executive biography enrichment and automatic company IR document discovery are not connected; FINN says this instead of inventing a profile. NSE's public filing endpoint can throttle or change; the sample fallback remains visible if NSE is unreachable.
- PDF financial extraction is deliberately conservative: it preserves source rows and calculates QoQ/YoY only when period labels and value columns are explicit in the same extracted table text. Reconciliation against real company filing formats remains future work.
- This product is for investment research and intelligence, not personalized financial advice.

## Build history and submission note

The Codex conversation used to build this prototype is the AI build history; share/export this conversation alongside the repository when submitting. The Vercel multi-service configuration and Render Blueprint are documented above.

**Covering note:** See [`docs/covering-note.md`](docs/covering-note.md) for a ready-to-edit submission draft.
