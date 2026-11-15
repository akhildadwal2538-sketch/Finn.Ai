# ENAM submission covering note

**Subject:** IIT Delhi – AI Engineer – 2027

Dear ENAM Assessment Team,

I built FINN as a mobile-first financial intelligence prototype for an 18-company Indian equity portfolio. The prototype combines an EOD market-data view, a prioritized announcement feed, configurable price/volume/category alerts, portfolio management, and on-demand company research grounded in indexed primary-source documents.

The research workflow accepts PDFs and official document URLs, preserves page citations, extracts literal financial rows without inferring periods or units, and returns evidence gaps when a claim cannot be verified. An optional OpenAI-compatible model endpoint can be configured for question answering; without it, retrieval and citations still work. The interface is designed to foreground high-priority changes and keep routine items grouped.

Key trade-offs: Yahoo Finance is used for EOD market data and cached locally. The exchange-announcement feed and corporate calendar remain adapter boundaries, so the visible announcement examples are explicitly labeled sample data. No broker research or news is used as evidence for company research. The prototype is not deployed, and this draft does not imply that it has been pushed to GitHub.

- **Repository:** add the GitHub URL after publishing the repository.
- **Prototype:** run locally using the setup instructions in `README.md`.
- **AI build history:** share or export the Codex conversation used to build the project alongside the submission.

Given more time, I would connect a reliable NSE/BSE filing feed and corporate calendar, ingest company IR/Screener primary documents for the ad-hoc research set, and evaluate extraction quality against real quarterly results and transcripts.

Sincerely,

Akhil Kumar
IIT Delhi
