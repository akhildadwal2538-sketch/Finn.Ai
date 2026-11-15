# FINN architecture

## Product boundary

FINN has two paths through the same portfolio context:

- **Push:** market quotes and categorized filings become a prioritized, time-windowed feed. Material categories sort first; routine items are collapsed. Alert rules evaluate latest EOD price and volume signals.
- **Pull:** a company symbol or name resolves to an NSE/BSE symbol. The user adds primary-source PDFs or official-source URLs. FINN extracts page-aware text, retrieves passages by lexical overlap, and returns source references or an explicit evidence gap.

The application is a research aid, not a buy/sell system or personalized advice engine.

## Runtime components

```text
Next.js / React client
  ├─ Intelligence feed, portfolio, company research, alerts, settings
  └─ same-origin `/api/*` proxy (optional `NEXT_PUBLIC_API_URL` override)
       ↓
FastAPI
  ├─ PortfolioService ─ SQLite holdings
  ├─ MarketDataService ─ Yahoo chart/volume API + SQLite quote cache
  ├─ CompanySearchService ─ Yahoo NSE/BSE equity lookup
  ├─ AnnouncementService ─ official NSE symbol/date feed + cache + sample fallback
  │    └─ on-demand exchange-PDF result rows with page citations and explicit evidence gaps
  │    └─ optional cached AI summary/materiality for high-priority live filings
  ├─ AlertDetectionService ─ persisted thresholds + EOD evaluation
  ├─ DocumentRetrievalService ─ source metadata, page-aware chunks, eight-section evidence dossier
  ├─ FinancialDataExtractionService ─ literal headline rows + conservative explicit-period comparisons
  └─ AIAnalysisService ─ retrieved evidence + optional OpenAI-compatible LLM
```

The service layer lives in `backend/services/core.py`; routes and request validation live in `backend/main.py`. The UI is currently a single client-rendered page so navigation and state remain lightweight for the prototype. SQLite initializes its schema on first connection; the repository/service boundary can be moved to PostgreSQL without changing the API contract.

## Data path and trust labels

| Information | Provider | Label and behavior |
| --- | --- | --- |
| EOD close, day move, volume, 20-day average | Yahoo Finance chart API | `live` after fetch, `cached` inside configured TTL, otherwise `unavailable` |
| NIFTY/SENSEX | Yahoo Finance index symbols | Same live/cache/unavailable behavior |
| NSE equity filings | NSE public `/api/corporate-announcements` endpoint, queried by portfolio symbol and date range | `live`, `cached`, `partial`, or `sample` if the endpoint cannot be reached; linked source filing retained |
| BSE portfolio filing collection | Not connected | BSE-only holdings have no automatic feed results; official BSE result PDFs can be added/extracted manually |
| Documents | User PDF or accepted company IR, Screener, NSE, or BSE URL | Primary-source document metadata and page references persist in SQLite |
| Analysis | Retrieved passages plus optional configured model | No source means explicit unavailable response; no outside model knowledge is used as evidence |

Market and NSE endpoints use bounded timeouts and cache responses in SQLite. Quote cache keys include the Yahoo symbol, including exchange suffix. Missing prices never receive demo fallback figures. When NSE is unavailable across the portfolio, the sample corpus is returned with explicit `sample` status.

## Retrieval and analysis flow

1. PDF upload is limited to 25 MB and parsed page by page with `pypdf`; accepted HTML URLs are reduced to readable text.
2. Bounded overlapping chunks are stored with company symbol, title, source URL, and a human-readable citation. PDF citations preserve page numbers; HTML citations use section numbers, never fabricated pages. The company research route returns excerpts selected for each of the eight requested research areas and an explicit unavailable state for areas without evidence.
3. A question is tokenized and ranked against that company's stored chunks using lexical overlap. The interface is deliberately replaceable with embeddings/vector search as source volume grows.
4. The optional server-side OpenAI-compatible provider adapter (configured through `.env.local`) receives only retrieved passages and is prompted for a structured observation, evidence, implication, relevance, and risk/opportunity response. The API filters model citations to citations present in retrieved evidence. With no LLM configured, FINN returns excerpts and citations without synthesizing a conclusion.
5. With no retrieved passage, the answer is `unavailable` and asks for a primary source. It does not fall back to the model's outside knowledge.
6. The financial-row extractor retains source wording and values. It computes QoQ/YoY only when the same extracted text includes explicit quarter labels with a one-to-one value-column match. It never infers units or silently assigns unlabeled columns.

## Alert and classification flow

Announcement categories follow the companion glossary, including `results`, `M&A`, `fund_raise`, `management_change`, `litigation`, `insider_trading`, `earnings_call`, `investor_meetings`, `credit_rating`, `dividend`, `bonus_split`, `capex`, `press_release`, `postal_ballot`, `agm_egm`, `pledging`, and `unclassified`. Deterministic keyword rules and priority defaults classify both official NSE filings and sample data.

Price and volume thresholds, enable switches, announcement categories, priority levels, and selected portfolio symbols are persisted in SQLite. `POST /api/alerts/evaluate` applies those settings to available EOD quotes and the current NSE filings (or labeled sample fallback). The prototype provides an on-demand in-app check; it does not run a background scheduler or push notifications outside the page.

## Database schema

- `portfolio`: symbol, display name, sector, exchange, attention label
- `market_cache`: Yahoo symbol, JSON quote, fetch timestamp
- `announcement_cache`: NSE symbol/window, filing JSON, fetch timestamp
- `announcement_highlights`: official filing URL, extracted headline rows/citations, fetch timestamp
- `announcement_analysis`: live filing id, optional model summary/materiality output, fetch timestamp
- `documents`: company symbol, title, source URL, source kind, creation time
- `chunks`: document, company symbol, text, PDF page, citation string
- `preferences`: named JSON settings, including alert thresholds

`FINN_DATABASE_URL` accepts a SQLite path in the prototype. A production PostgreSQL move should add formal migrations, user/workspace ownership, and per-user portfolio rows.

## API safety and validation

- Portfolio and alert bodies are Pydantic-validated.
- Search requires at least two characters; document upload accepts PDFs up to 25 MB.
- URL ingestion permits recognized Screener/exchange hosts and URLs whose host/path identifies investor relations; it is intentionally narrower than arbitrary URL fetching.
- AI credentials are read only from environment variables and are never returned from `/api/health`.
- CORS is limited to the local frontend origins used by the prototype.

## Production follow-up

Add an authorized NSE/BSE filing adapter, company IR discovery, executive background enrichment, robust table reconciliation across real quarterly filing formats, document deduplication, background quote/alert jobs, authentication, PostgreSQL migrations, observability, and evaluation data for classification and citation accuracy. Keep exchange event status separate from market data and retain an auditable source URL for every claim.
