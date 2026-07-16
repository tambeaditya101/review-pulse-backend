# Review Pulse 🔍

> An AI-powered weekly report pipeline that automatically reads thousands of app store reviews, finds the patterns, and delivers a structured insight report — built entirely on free-tier infrastructure.

**Review Pulse** turns raw user feedback from Google Play and Apple App Store into a concise weekly intelligence report for product teams. It scrapes reviews, cleans and scrubs PII, clusters them into themes using local embeddings, generates a structured summary via Groq LLM, validates every quote against source text, and delivers the final report to Google Docs and Gmail — all automatically, every week, at zero cost.

Built for **INDMoney** as a hands-on AI engineering project exploring LangGraph, LLM orchestration, semantic clustering, and production-style pipeline design.

---

<!-- SCREENSHOT PLACEHOLDER: Hero image or architecture diagram -->
<!-- ![Review Pulse Architecture](docs/assets/architecture.png) -->

---

## 🤔 Why I Built This

Product teams often have hundreds or thousands of user reviews flowing in each week from multiple app stores. Reading them manually is time-consuming and prone to selection bias. Important signals — a spike in crash reports, frustration with a new feature, or praise for something that's working — can easily go unnoticed.

This project was my attempt to solve that problem using the AI engineering tools I was learning. I wanted to build something that was genuinely useful, not just a tutorial exercise — a system I could actually run every Monday and hand to a product manager.

Along the way, I explored:

- **LangGraph** — building stateful, multi-step AI pipelines with conditional edges and retry loops
- **LLM prompt engineering** — structured JSON outputs, XML delimiters for prompt injection resistance, and quote grounding
- **Local semantic embeddings** — running `sentence-transformers` on CPU without any API dependency
- **Semantic clustering** — using KMeans + silhouette scoring to find themes in unstructured text
- **MCP (Model Context Protocol)** — designing an HTTP-based tool server for human-in-the-loop Google Workspace integrations
- **Production pipeline patterns** — idempotency, PII scrubbing, retry logic, audit logging, and background job execution
- **FastAPI** — building a REST layer that wraps a blocking pipeline into an async-friendly HTTP API

The result is a complete, deployable AI backend that I'd be comfortable running in production.

---

## ✨ Features at a Glance

| Feature | Detail |
|---|---|
| 🤖 **LangGraph orchestration** | Stateful 10-node pipeline with conditional retry edges |
| 🧠 **Local embeddings** | `sentence-transformers` on CPU — no embedding API cost |
| 🔒 **PII scrubbing** | Email, phone, PAN, Aadhaar patterns redacted before any LLM call |
| ✅ **Hallucination guard** | Every LLM quote fuzzy-matched against source text (rapidfuzz ≥ 90) |
| 🔁 **Idempotent runs** | SQLite `UNIQUE(product, week_start)` — safe to re-trigger anytime |
| 🌐 **FastAPI REST layer** | Trigger runs, poll status, fetch reports over HTTP |
| 💸 **Zero cost** | Groq free tier · public scrapers · SQLite · Render free tier |
| 🖥️ **CLI + API** | Both interfaces fully supported |

---

## 🎬 How It Works

The pipeline follows a linear flow through 10 LangGraph nodes, with a retry loop for LLM quote validation:

```
App Store Reviews + Google Play Reviews
           │
           ▼
    ┌─────────────────────────────────────────────┐
    │           LangGraph StateGraph              │
    │                                             │
    │  1. check_idempotency  ← skip if already done
    │  2. fetch_reviews      ← scrape both stores  │
    │  3. clean_reviews      ← dedup, HTML strip   │
    │  4. scrub_pii          ← redact before LLM   │
    │  5. embed_and_cluster  ← local embeddings    │
    │              + KMeans (k=3–8)                │
    │  6. generate_report    ← Groq LLM            │
    │  7. validate_quotes ───┐ ← rapidfuzz check   │
    │       ↑ retry (×2)    │                      │
    │       └───────────────┘                      │
    │  8. render_report      ← markdown file       │
    │  9. deliver_report     ── HTTP → MCP server  │
    │  10. audit_log         ← SQLite record       │
    └─────────────────────────────────────────────┘
           │
           ▼
    Google Docs + Gmail Draft (via MCP server)
    + data/reports/indmoney-YYYY-MM-DD.md
```

Each node reads from and writes to a shared `PulseState` TypedDict. If quote validation fails, the graph loops back to `generate_report` (up to 3 total attempts) before proceeding with valid quotes only.

---

## 📊 Sample Report Output

Here's what the pipeline produces each week:

```markdown
# INDMoney — Weekly Review Pulse

**Period:** April 07, 2026 – April 13, 2026
**Sources:** Google Play, Apple App Store
**Reviews analyzed:** 387

## Executive Summary
Users express strong satisfaction with INDMoney's mutual fund tracking and
portfolio visualization features. However, a persistent cluster of complaints
centres on app crashes during market-open hours (9:15–9:30 AM IST), particularly
on Android 14 devices. Redemption processing delays are the most-cited
frustration in 1–2★ reviews.

## Top Themes
1. **App Stability & Crashes** — Repeated freezes during active trading. (89 reviews, avg ★1.8)
2. **Mutual Fund Tracking** — Praised for real-time NAV and gain/loss clarity. (76 reviews, avg ★4.6)
3. **Redemption Delays** — 3–5 day waits vs. stated T+1 timeline. (64 reviews, avg ★2.1)
4. **Login & Biometrics** — Face ID fails after app updates. (52 reviews, avg ★2.3)
5. **Customer Support** — Escalation paths unclear for failed transactions. (38 reviews, avg ★2.9)

## Representative Quotes
> "App crashes every time the market opens at 9:15. Have to restart 3–4 times." — 1★, Google Play, 2026-04-08
> "The mutual fund tracking is outstanding. Best interface I've used." — 5★, App Store, 2026-04-10

## Action Ideas
- Profile and fix crash hotspots during the 9:15–9:30 AM IST market-open window.
- Surface clear ETAs for redemption requests in the transaction history screen.
- Investigate biometric unlock regression introduced in the latest Android build.

---
_Generated by Review Pulse · Run ID: a1b2c3d4_
```

<!-- SCREENSHOT PLACEHOLDER: Screenshot of the report rendered in Google Docs -->
<!-- ![Report in Google Docs](docs/assets/google_doc_output.png) -->

---

## 📐 Architecture Overview

```
Trigger (CLI / API / cron)
    │
    ▼
LangGraph Pipeline (StateGraph)
    │
    ├── fetch_reviews     ← Google Play + App Store scrapers (no API keys)
    ├── clean_reviews     ← dedup, HTML unescape, length filter
    ├── scrub_pii         ← regex redaction before any LLM call
    ├── embed_and_cluster ← local sentence-transformers + KMeans
    ├── generate_report   ← Groq LLM (llama-3.3-70b-versatile)
    ├── validate_quotes   ← rapidfuzz token_set_ratio ≥ 90
    ├── render_report     ← markdown file → data/reports/
    ├── deliver_report    ── HTTP → google-mcp-server → Google Docs + Gmail
    └── audit_log         ← SQLite run record, reviews, themes
```

The **google-mcp-server** is a separately running FastAPI process (separate repository) that wraps Google OAuth credentials. The pipeline communicates with it over HTTP — no OAuth credentials are required in this repository.

See [docs/architecture.md](docs/architecture.md) for the complete technical architecture.

<!-- SCREENSHOT PLACEHOLDER: Full architecture diagram -->
<!-- ![Architecture Diagram](docs/assets/architecture_diagram.png) -->

---

## 🔬 Technology Stack

| Layer | Technology | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| Orchestration | **LangGraph** | StateGraph, conditional edges, retry loops |
| LLM | **Groq** (`llama-3.3-70b-versatile`) | Free tier; fallback `llama-3.1-8b-instant` |
| LLM SDK | `groq` (native) | JSON object mode + `json.loads()` |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` | Runs on CPU, no API cost |
| Embedding fallback | `TfidfVectorizer` | Auto-activates on low-memory environments |
| Clustering | scikit-learn KMeans + silhouette score | |
| Quote validation | `rapidfuzz` token_set_ratio | Grounding check: score ≥ 90 |
| PII scrubbing | Regex patterns | Email, phone, PAN, Aadhaar, long digits |
| Review scraping | `google-play-scraper`, `app-store-scraper` | No API keys required |
| REST API | **FastAPI** + Uvicorn | Async; pipeline runs in ThreadPoolExecutor |
| Configuration | Pydantic Settings + PyYAML | |
| Database | SQLite | Idempotency, audit trail, review cache |
| Google delivery | HTTP → google-mcp-server | MCP-style tool server (separate project) |
| Scheduling | cron / GitHub Actions | |

---

## 🧠 Engineering Concepts Explored

Building this project was a practical exercise in the following areas:

**AI & LLM Engineering**
- Designing stateful multi-step LLM pipelines with **LangGraph**
- Structured JSON output with Groq's `response_format` API
- Prompt engineering with XML delimiters to resist injection attacks
- Automated hallucination detection via fuzzy quote grounding

**ML & NLP**
- Local semantic embeddings with `sentence-transformers`
- Unsupervised clustering (KMeans) with silhouette-based `k` selection
- Fallback to TF-IDF in low-resource environments

**Backend Engineering**
- REST API design with **FastAPI** and background thread execution
- SQLite idempotency using `UNIQUE` constraints and status tracking
- Retry logic with exponential backoff for LLM and HTTP calls
- Structured JSON logging with rotating file output
- Pydantic settings management and YAML-based product config

**System Design**
- Decoupled delivery via a separately running MCP-style tool server
- Human-in-the-loop approval flow for irreversible Google API actions
- Embedding cache (`.npy` files) to avoid re-encoding on retry
- `--dry-run` mode for safe CI execution without side effects

---

## 🌍 Public Demo

The API is deployed on Render's free tier.

A few things to know about the public deployment:

- **Cold starts:** The service may take 30–60 seconds to respond after a period of inactivity. This is expected behaviour on free-tier hosting.
- **Google integrations are private:** The Google Docs and Gmail delivery features use the developer's own Google Workspace account. The public API runs in `--dry-run` mode — reports are generated and logged, but not delivered to external services.
- **Reports are not persisted indefinitely:** Render's free tier may reset state between redeploys. The pipeline is designed to be re-run at any time.
- **The core pipeline works in full:** Ingestion, embedding, clustering, LLM summarisation, quote validation, and markdown rendering all run live.

<!-- SCREENSHOT PLACEHOLDER: API response from a live run -->
<!-- ![Live API Response](docs/assets/api_response.png) -->

---

## 📁 Project Structure

```
review-pulse/
├── config/
│   └── indmoney.yaml       # Product config: package IDs, report limits
├── docs/                   # Architecture, deployment, and runbook docs
├── src/review_pulse/
│   ├── __main__.py         # CLI entry point
│   ├── api.py              # FastAPI REST API
│   ├── config.py           # Pydantic Settings + YAML loader
│   ├── models.py           # Core dataclasses
│   ├── db/                 # SQLite persistence layer
│   ├── deliver/            # MCP HTTP client (→ google-mcp-server)
│   ├── graph/              # LangGraph nodes, state, builder
│   ├── ingest/             # Google Play + App Store scrapers
│   ├── llm/                # Groq client + prompt templates
│   ├── process/            # PII scrubbing, embeddings, KMeans
│   ├── render/             # Markdown report renderer
│   └── validate/           # Quote grounding validator
├── tests/                  # Full unit and integration test suite
├── data/                   # SQLite DB, logs, and reports (gitignored)
├── .env.example
├── pyproject.toml
└── render.yaml             # Render platform deployment config
```

---

## 🛠️ Setup — Main Pipeline

### Prerequisites

- Python 3.11+
- A [Groq API key](https://console.groq.com/keys) (free, no credit card required)

### Installation

```bash
# 1. Clone and enter the project
git clone https://github.com/your-username/review-pulse.git
cd review-pulse

# 2. Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. Create a .env file from the template
cp .env.example .env
# → Edit .env and add your GROQ_API_KEY

# 4. Initialize the SQLite database
python -m review_pulse init-db
```

### Quick Test (no delivery needed)

```bash
# Run the pipeline in dry-run mode — no Google Docs/Gmail required
python -m review_pulse run --product indmoney --dry-run
```

The report will be saved to `data/reports/indmoney-YYYY-MM-DD.md`.

---

## ⚙️ Google Workspace MCP Server Setup

The pipeline delivers reports via HTTP to a separately running **google-mcp-server**. This server manages Google OAuth credentials and exposes REST endpoints for writing to Google Docs and creating Gmail drafts. It is a **separate project** in its own repository.

> **Skip this section** if you only want to generate the markdown report locally (`--dry-run`).

To set up the MCP server:

1. Enable the **Google Docs API** and **Gmail API** in a Google Cloud Console project.
2. Create Desktop Application OAuth credentials, download the secret file, rename it to `credentials.json`, and place it in the `google-mcp-server/` directory.
3. Start the MCP server (from the google-mcp-server repository):
   ```bash
   cd google-mcp-server
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python server.py
   ```
4. Authenticate once in your browser when prompted. The server will save `token.json` and keep running to accept delivery requests.
5. Set `MCP_SERVER_URL=http://127.0.0.1:8000` in your `.env` file (this is the default).

---

## 🔑 Configuration

Copy `.env.example` to `.env` and fill in the required values:

```env
# Required
GROQ_API_KEY=gsk_...

# Required for Google Docs and Gmail delivery
GOOGLE_DOC_ID=your_google_doc_id
EMAIL_RECIPIENTS=you@example.com,teammate@example.com

# MCP server connection (defaults work for a locally running server)
MCP_SERVER_URL=http://127.0.0.1:8000
MCP_API_KEY=                       # optional, for MCP server auth
MCP_TIMEOUT_SECONDS=60
MCP_MAX_RETRIES=3

# Optional overrides
GROQ_MODEL=llama-3.3-70b-versatile
REVIEW_WINDOW_WEEKS=10
DATABASE_PATH=data/review_pulse.db

# REST API security (optional — if set, all API routes require X-API-Key header)
API_KEY=
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `GROQ_API_KEY` | **Yes** | — | Groq API key |
| `GOOGLE_DOC_ID` | For delivery | — | Target Google Doc ID |
| `EMAIL_RECIPIENTS` | For delivery | — | Comma-separated emails |
| `MCP_SERVER_URL` | For delivery | `http://127.0.0.1:8000` | google-mcp-server address |
| `MCP_API_KEY` | No | — | Optional auth for MCP server |
| `MCP_TIMEOUT_SECONDS` | No | `60.0` | MCP request timeout |
| `MCP_MAX_RETRIES` | No | `3` | MCP retry count on transient errors |
| `REVIEW_WINDOW_WEEKS` | No | `10` | Review analysis window width |
| `DATABASE_PATH` | No | `data/review_pulse.db` | SQLite path |
| `API_KEY` | No | — | REST API key (enables `X-API-Key` auth) |
| `LOW_RESOURCE_MODE` | No | — | Set `true` to use TF-IDF instead of sentence-transformers |

---

## 🚀 CLI Usage

```bash
# Run pipeline for the current ISO week
python -m review_pulse run --product indmoney

# Run pipeline for a specific past week
python -m review_pulse run --product indmoney --week 2026-W14

# Re-run a completed week (override idempotency)
python -m review_pulse run --product indmoney --week 2026-W14 --force

# Dry run — generate markdown report only, skip Google Docs/Gmail delivery
python -m review_pulse run --product indmoney --dry-run

# Show recent run history and status
python -m review_pulse status --product indmoney
```

---

## 🌐 REST API

When deployed (or run locally via `uvicorn review_pulse.api:app`), the pipeline exposes a REST API:

| Method | Route | Description |
|---|---|---|
| `POST` | `/api/runs` | Trigger a new pipeline run |
| `GET` | `/api/runs/{product}` | List recent runs for a product slug |
| `GET` | `/api/runs/{run_id}/status` | Poll a run's current status |
| `GET` | `/api/runs/{run_id}/report` | Fetch the generated markdown report |
| `GET` | `/api/themes/{run_id}` | Fetch LLM-generated themes for a run |
| `GET` | `/api/debug/mcp` | Diagnostic: test MCP server connectivity |

**Authentication:** Set `API_KEY=your-secret` in `.env`. When set, all routes except `/api/debug/mcp` require `X-API-Key: your-secret` in the request header. If `API_KEY` is not set, the API is open.

### Trigger a run

```bash
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"product": "indmoney", "dry_run": true}'
```

Response:

```json
{
    "run_id": "3f1a9b…",
    "status": "running",
    "product": "indmoney",
    "week": "2026-W14"
}
```

### Poll status

```bash
curl http://localhost:8000/api/runs/3f1a9b…/status \
  -H "X-API-Key: your-api-key"
```

<!-- SCREENSHOT PLACEHOLDER: Dashboard showing run history and theme cards -->
<!-- ![Dashboard](docs/assets/dashboard.png) -->

### Start the API server locally

```bash
uvicorn review_pulse.api:app --reload
```

---

## ⏰ Scheduling & Automation

### Local Cron

```cron
# Every Monday at 09:00 IST (03:30 UTC)
0 9 * * 1 cd /path/to/review-pulse && .venv/bin/python -m review_pulse run --product indmoney
```

### GitHub Actions

The pipeline ships with [`.github/workflows/weekly-pulse.yml`](.github/workflows/weekly-pulse.yml), which runs every Monday at 03:30 UTC.

> **Note:** GitHub Actions always runs in `--dry-run` mode because the google-mcp-server is not available in CI. The markdown report is generated and logged to SQLite, but no Google Doc is updated and no Gmail draft is created. Live delivery requires a running MCP server.

Set the following repository secrets for the workflow:
- `GROQ_API_KEY`
- `EMAIL_RECIPIENTS` *(optional for dry-run)*

---

## 🏗️ Deployment on Render

See [docs/deployment-plan.md](docs/deployment-plan.md) for the full guide.

**Quick start:**

1. Connect this repository to Render.
2. Render will pick up `render.yaml` automatically.
3. Set the following environment variables in the Render dashboard:

| Key | Value |
|---|---|
| `GROQ_API_KEY` | Your Groq API key |
| `EMAIL_RECIPIENTS` | Comma-separated stakeholder emails |
| `GOOGLE_DOC_ID` | Target Google Doc ID |
| `MCP_SERVER_URL` | URL of your deployed google-mcp-server |
| `API_KEY` | A strong random key for REST API auth |

The startup command in `render.yaml` initialises the database and starts the FastAPI server:

```bash
python -m review_pulse init-db && uvicorn review_pulse.api:app --host 0.0.0.0 --port $PORT
```

SQLite data is stored on a 1 GB persistent disk mounted at `/data`.

---

## 🧪 Testing

```bash
# Run the full test suite
pytest -v

# Run a specific test file
pytest tests/test_graph.py -v
```

The test suite covers all major modules with unit tests and one end-to-end graph integration test (mocked scrapers + mocked Groq). All external HTTP calls (Groq, MCP server) are mocked.

---

## ⚠️ Limitations

- **INDMoney only.** The pipeline is configured for a single product. Multi-product support is not implemented.
- **No real-time data.** Scrapers use public review APIs and are subject to store-side pagination and rate limits.
- **Delivery requires MCP server.** The google-mcp-server must be running for Google Docs and Gmail delivery. Use `--dry-run` otherwise.
- **Groq free-tier limits.** One weekly run uses ~8,000–10,000 tokens, well within Groq's free daily limits. Very large review volumes may trigger model fallback.
- **SQLite concurrency.** Not suitable for high-concurrency write workloads. Single-product weekly runs are well within SQLite's capacity.

---

## 🗺️ What I'd Improve Next

- Enable SQLite WAL mode for better concurrent read/write performance
- Add `GET /health` endpoint for deployment platform health probes
- Wire `email_subject_template` from YAML config into the delivery node
- Implement `POST /api/runs/{run_id}/approve` to replace the terminal MCP approval flow
- Add structured `run_id` log field injection across all pipeline nodes
- Add a LangGraph SQLite checkpointer for mid-pipeline resumability
