# Operational Runbook — Review Pulse Pipeline

This runbook covers setup, manual execution, verification, troubleshooting, and database maintenance for the Review Pulse application.

---

## 1. System Architecture Summary

```
  ┌────────────────────────────────────────────────────────────┐
  │               review-pulse (Main Pipeline)                 │
  │                                                            │
  │  [CLI / API] ──► [LangGraph Graph]                         │
  │                        │                                   │
  │  [Ingestion] ──► [Clean + PII] ──► [Embed + Cluster]       │
  │                                          │                 │
  │  [Groq LLM] ◄────────────────────────────┘                 │
  │       │                                                    │
  │  [Validate Quotes] ──► [Render Markdown]                   │
  │                               │                           │
  │  [MCP Client] ◄───────────────┘                           │
  └──────────┬─────────────────────────────────────────────────┘
             │ HTTP (POST)
             ▼
  ┌────────────────────────────────────────────────────────────┐
  │              google-mcp-server (Separate Process)          │
  │                                                            │
  │  POST /append_to_doc ──► Google Docs API                   │
  │  POST /create_email_draft ──► Gmail API                    │
  │                                                            │
  │  Terminal: Approve? (y/n) before each Google API call      │
  └────────────────────────────────────────────────────────────┘
```

> **Note:** The google-mcp-server is a **separate project** not included in this repository. This pipeline communicates with it over HTTP via `deliver/mcp_client.py`.

---

## 2. Setting Up Credentials & Environment

### Main Pipeline Setup

Create a `.env` file in the project root (copy from `.env.example`):

```env
# Required
GROQ_API_KEY=your_groq_api_key

# Required for Google Docs and Gmail delivery
GOOGLE_DOC_ID=your_target_google_doc_id
EMAIL_RECIPIENTS=stakeholder1@example.com,stakeholder2@example.com

# MCP server connection
MCP_SERVER_URL=http://127.0.0.1:8000

# Optional REST API auth (enables X-API-Key header validation)
API_KEY=your_strong_random_key

# Optional overrides
GROQ_MODEL=llama-3.3-70b-versatile
REVIEW_WINDOW_WEEKS=10
DATABASE_PATH=data/review_pulse.db
```

### Google MCP Server Setup

The google-mcp-server is a separate project. To set it up:

1. Go to Google Cloud Console, enable **Docs API** and **Gmail API**.
2. Create Desktop OAuth credentials, download the secret file, rename it to `credentials.json`, and place it in the google-mcp-server directory.
3. Start the server (from the google-mcp-server repository):
   ```bash
   cd google-mcp-server
   source .venv/bin/activate
   python server.py
   ```
4. Authenticate once in your browser when prompted. The server saves `token.json` and continues running to accept POST requests.

---

## 3. Running the Pipeline

### Step 1: Start the MCP Server (for delivery)

Open a separate terminal window and start the google-mcp-server (from its own repository directory). Keep this window open — it will prompt `Approve? (y/n)` before any Google API call.

Skip this step if running in `--dry-run` mode.

### Step 2: Run the Main Pipeline CLI

```bash
# Initialize SQLite database (runs once)
python -m review_pulse init-db

# Run for the current ISO week (delivery enabled)
python -m review_pulse run --product indmoney --force

# Run for a specific past week
python -m review_pulse run --product indmoney --week 2026-W14 --force

# Dry run — generate markdown only, skip MCP delivery
python -m review_pulse run --product indmoney --dry-run

# View recent run history
python -m review_pulse status --product indmoney
```

### Step 3: Approve Actions (delivery runs only)

In the MCP server terminal, type **`y`** and press **Enter** when prompted to authorize each Google Doc update and Gmail draft creation.

---

## 4. Operational Troubleshooting

### Problem: Stale "Running" Status

If a run crashes or is terminated mid-execution, its status in SQLite remains `running`.

**Automatic recovery:** The pipeline detects stale `running` records older than 1 hour and treats them as recoverable on the next run.

**Manual override:** Use `--force` to bypass all idempotency checks and re-execute immediately:

```bash
python -m review_pulse run --product indmoney --force
```

---

### Problem: MCP Server Connection Times Out

If the pipeline logs `Failed to connect to MCP server` or times out:

1. Verify the MCP server is actively running on `http://127.0.0.1:8000`.
2. Check that you responded to the `Approve? (y/n)` prompt within the configured timeout (`MCP_TIMEOUT_SECONDS`, default 60 seconds).
3. If using a remote MCP server, verify `MCP_SERVER_URL` is set correctly.

The MCP client will retry `ConnectTimeout` and `ConnectError` up to `MCP_MAX_RETRIES` times with exponential backoff. It does **not** retry `ReadTimeout` to prevent duplicate writes.

---

### Problem: Groq Rate Limit (429)

The Groq client automatically:
1. Backs off with exponential delay.
2. Falls back to `llama-3.1-8b-instant` after exhausting retries on the primary model.

If both models are rate-limited, the run will fail with `status=failed`. Wait a few minutes and re-run with `--force`.

---

### Problem: Quote Validation Fails Repeatedly

If the pipeline logs `Quote validation failed` multiple times:

- The LLM is generating quotes that cannot be fuzzy-matched (token_set_ratio ≥ 90) to any source review.
- The pipeline retries `generate_report` up to 3 total attempts.
- After 3 attempts, it proceeds with only the quotes that passed validation (or no quotes if all fail).
- The run still completes with `status=completed`; failed quotes are logged.

---

### Problem: Low Review Count

If very few reviews are fetched (< 10):

- The pipeline will still attempt clustering with `k=1` fallback.
- LLM output quality will be lower but the run will complete.
- Check whether the analysis window (`REVIEW_WINDOW_WEEKS`) is wide enough.
- Verify the App Store and Google Play scrapers are returning data (check logs).

---

## 5. Cost Summary

| Service | Cost |
|---|---|
| LLM calls (Groq) | Free (within rate limits) |
| Workspace operations (Docs, Gmail) | Free (developer desktop OAuth credentials) |
| Review scraping (Google Play, App Store) | Free (public data) |
| Storage (SQLite + reports) | Free (local or Render persistent disk) |
| Hosting (Render free/starter tier) | Free (with cold-start delays on free tier) |
| **Total** | **$0.00 / month** |

---

## 6. Architectural Decision Record

### Decision: MCP Server Architecture for Delivery

**Context:** The original plan called for direct Google Docs and Gmail API integration within the pipeline (`deliver/google_docs.py`, `deliver/gmail.py`). This would have required OAuth credentials to be bundled into the main pipeline process.

**Decision:** Extracted Google Workspace integration into a separate `google-mcp-server` process. The pipeline communicates with it via HTTP POST requests (`deliver/mcp_client.py`).

**Reasons:**
- OAuth credential management is isolated from the pipeline
- Enables a human-in-the-loop `Approve? (y/n)` approval step before each Google API call
- The MCP server can be run, updated, or replaced independently
- Keeps the main pipeline process free of Google SDK dependencies

---

### Decision: Native Groq SDK Over LangChain LLM Wrapper

**Context:** The original plan specified using LangChain's `ChatGroq` wrapper and `JsonOutputParser` for LLM calls.

**Note:** **LangGraph is fully used** as the core pipeline orchestration engine (`StateGraph`, nodes, conditional edges). This decision applies only to the LLM call layer — not to the graph layer.

**Decision:** LLM calls use the native `groq` Python SDK with `response_format={"type":"json_object"}` and `json.loads()` instead of LangChain's `ChatGroq` wrapper.

**Reasons:**
- Simpler code with fewer abstraction layers for a single API call
- Direct control over retry logic and model fallback
- Avoids a dependency on `langchain-groq` for a task that only requires one function
- `langchain` and `langchain-groq` remain in `pyproject.toml` but are not imported; `langgraph` is actively used

---

### Decision: TF-IDF Fallback for Low-Resource Environments

**Context:** `sentence-transformers/all-MiniLM-L6-v2` downloads ~90 MB on first use and requires ~400 MB RAM at runtime — exceeding Render's free-tier memory limit.

**Decision:** `process/cluster.py` automatically switches to `TfidfVectorizer` when `RENDER` or `LOW_RESOURCE_MODE=true` is detected in the environment.

**Trade-off:** TF-IDF clustering is less semantically accurate than transformer embeddings, but it is sufficient for a single-product weekly pipeline and keeps the deployment on free infrastructure.

---

### Decision: Theme Label Assignment Workflow

**Context:** KMeans clustering produces generic placeholder labels (`"Theme 0"`, `"Theme 1"`) because the clustering algorithm has no semantic understanding of review content.

**Resolution:**
1. KMeans runs in `embed_and_cluster` and produces `ThemeCluster` objects with placeholder labels.
2. The LLM output JSON schema includes a `cluster_id` field (1-indexed) that maps each generated theme back to its source cluster.
3. The `generate_report` node parses the LLM output, converts `cluster_id` to 0-indexed, and updates the `label` and `description` fields of the in-state `ThemeCluster` objects.
4. The `audit_log` node persists the descriptively-labelled objects to the SQLite `themes` table.

This ensures the SQLite `themes` table stores human-readable labels (e.g., `"Biometric Login Issues"`) rather than generic placeholders.

---

## 7. Database Inspection

```bash
# Open SQLite shell
sqlite3 data/review_pulse.db

# List all runs
SELECT run_id, product, week_start, status, reviews_fetched, groq_tokens_used FROM runs ORDER BY started_at DESC;

# List themes for the latest run
SELECT r.week_start, t.label, t.review_count, t.avg_rating
FROM themes t
JOIN runs r ON t.run_id = r.run_id
ORDER BY r.started_at DESC, t.review_count DESC
LIMIT 20;

# Check for stale running records
SELECT run_id, product, week_start, started_at
FROM runs
WHERE status = 'running'
AND datetime(started_at) < datetime('now', '-1 hour');
```
