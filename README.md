# Review Pulse

Automated weekly AI-powered **Review Pulse** reports for **INDMoney**, built from public Google Play and Apple App Store reviews.
Ingests, cleans, scrubs PII, clusters reviews into themes locally, formats summaries via Groq LLM, and delivers reports idempotently to Google Docs and Gmail via a local Model Context Protocol (MCP) server.

---

## 📁 Project Directory Structure

```
review-pulse/
├── config/
│   └── indmoney.yaml        # YAML configuration for app/scraper IDs and limits
├── google-mcp-server/       # Model Context Protocol style Google REST server
│   ├── server.py            # FastAPI tool endpoints
│   ├── auth.py              # OAuth consent credentials loading
│   └── docs_tool.py         # Google Docs operations
├── src/review_pulse/
│   ├── db/                  # SQLite runs database layer
│   ├── ingest/              # Google Play & App Store scrapers
│   ├── process/             # PII scrubbing, caching embeddings, KMeans clustering
│   ├── llm/                 # Prompt structures, fallback models & Groq API
│   ├── validate/            # Fuzzy quote verifier via token_set_ratio
│   ├── render/              # Markdown document formatting
│   ├── deliver/             # HTTP-based MCP server client
│   ├── config.py            # Pydantic Settings loaders
│   └── __main__.py          # Typer CLI application entry point
├── tests/                   # 80 unit and integration tests (100% green)
└── data/                    # SQLite database, logs, and markdown files (Gitignored)
```

---

## 🛠️ Main Project Setup

1. Clone the repository and navigate to the project directory:
   ```bash
   cd review-pulse
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```

3. Initialize the SQLite database structure:
   ```bash
   python -m review_pulse init-db
   ```

4. Create a `.env` file from the template and fill in your keys:
   ```bash
   cp .env.example .env
   ```
   Configure `.env` with:
   - `GROQ_API_KEY`: Your Groq API token.
   - `EMAIL_RECIPIENTS`: Comma-separated list of recipient emails.
   - `GOOGLE_DOC_ID`: The ID of your blank Google Doc for report appending.

---

## ⚙️ Google Workspace MCP Server Setup

1. Enable the Docs and Gmail APIs in a Google Cloud Console project.
2. Download Desktop Application OAuth credentials, rename it to `credentials.json`, and place it in the `google-mcp-server/` directory.
3. Start the server (on port `8000`):
   ```bash
   cd google-mcp-server
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python server.py
   ```
4. Authenticate once via your browser when prompted. The server will output `token.json` and keep running to accept POST commands.

---

## 🚀 CLI Usage

```bash
# Run pipeline for a specific ISO week (downloads reviews, processes themes, delivers reports)
python -m review_pulse run --product indmoney --week 2026-W14 --force

# Run pipeline for the current ISO week (live latest data)
python -m review_pulse run --product indmoney --force

# Run pipeline in dry-run mode (markdown report saved to data/reports/ only)
python -m review_pulse run --product indmoney --dry-run

# Show run history and audit details
python -m review_pulse status --product indmoney
```

---

## ⏰ Scheduling & Automation

### 1. Local Cron Execution
To run the review pulse automatically every Monday at 09:00 IST:
```cron
0 9 * * 1 cd /path/to/review-pulse && .venv/bin/python -m review_pulse run --product indmoney
```

### 2. GitHub Actions CI
The pipeline can be automated in GitHub Actions via [.github/workflows/weekly-pulse.yml](.github/workflows/weekly-pulse.yml). Set up secrets for `GROQ_API_KEY` and `EMAIL_RECIPIENTS`. The pipeline is scheduled to run every Monday at 03:30 UTC.

---

## 🧪 Verification

Run the test suite offline using:
```bash
pytest -v
```
All 80 unit/integration tests must pass cleanly.
