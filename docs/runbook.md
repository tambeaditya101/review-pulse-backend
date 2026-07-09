# Operational Runbook — Review Pulse Pipeline

This runbook covers setup, manual execution, verification, troubleshooting, and database maintenance operations for the Review Pulse application.

---

## 1. System Architecture Summary

```
  ┌──────────────────────────────────────────────────────────┐
  │                   review-pulse (Main Pipeline)           │
  │                                                          │
  │  [Ingestion Scrapers] ──► [PII & Clustering] ──► [LLM]   │
  │                                                    │     │
  │  [Local Markdown] ◄── [Render Report] ◄────────────┘     │
  │          │                                               │
  │          ▼                                               │
  │  [MCP Client]                                            │
  └──────────┬───────────────────────────────────────────────┘
             │ HTTP (POST)
             ▼
  ┌──────────────────────────────────────────────────────────┐
  │                 google-mcp-server (Local REST)           │
  │                                                          │
  │  [FastAPI Endpoints] ──► [Manual Operator Confirmation]  │
  │                                       │                  │
  │  [Gmail Compose Draft] ◄──────────────┼───► [Docs API]   │
  └──────────────────────────────────────────────────────────┘
```

---

## 2. Setting Up Credentials & Environment

### Main Pipeline Setup
Create a `.env` file in the project root:
```env
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=llama-3.3-70b-versatile
EMAIL_RECIPIENTS=stakeholder1@example.com,stakeholder2@example.com
GOOGLE_DOC_ID=your_target_google_doc_id
REVIEW_WINDOW_WEEKS=10
DATABASE_PATH=data/review_pulse.db
```

### Google MCP Server Setup
1. Go to Google Cloud Console, enable **Docs API** and **Gmail API**.
2. Create Desktop OAuth credentials, download the secret file, rename it to `credentials.json`, and place it in the `google-mcp-server/` directory.
3. Start the server (see below) to trigger browser authentication once, producing `token.json`.

---

## 3. Running the Server & Pipeline

### Step 1: Start the MCP Server
Open a separate terminal window and run:
```bash
cd google-mcp-server
source .venv/bin/activate
python server.py
```
Keep this window open to receive and confirm action approvals.

### Step 2: Run the Main Pipeline CLI
In your main terminal window:
```bash
# 1. Initialize SQLite database structure (runs once)
python -m review_pulse init-db

# 2. Run for the current ISO week
python -m review_pulse run --product indmoney --force

# 3. Or run for a specific past week
python -m review_pulse run --product indmoney --week 2026-W14 --force
```

### Step 3: Approve Actions
In the MCP Server terminal, type **`y`** and press **Enter** when prompted to authorize Google Doc updates and Gmail draft creations.

---

## 4. Operational Maintenance & Troubleshooting

### Problem: Stale "Running" Status
If a run crashes or is terminated mid-execution, its status in the SQLite database remains set to `running`. 
* **Automatic Recovery:** The pipeline automatically detects stale running executions older than 1 hour and recovers them on the next run.
* **Manual Override:** Run the command with the `--force` flag to bypass all database idempotency checks and re-execute immediately:
  ```bash
  python -m review_pulse run --product indmoney --force
  ```

### Problem: MCP Server Connections Time Out
If the main pipeline client times out with `Failed to connect to MCP server: timed out`, check:
1. Is the MCP server actively running on `http://127.0.0.1:8000`?
2. Did you take longer than 120 seconds to type `y` in the server terminal? (The client timeout is capped at 2 minutes).

### Cost Summary
* **LLM Calls (Groq):** Free (within rate limits).
* **Workspace Operations (Docs, Gmail):** Free (using standard developer console desktop credentials).
* **Storage (SQLite):** Free (local database).
* **Total Operating Cost:** **$0.00 / month**

---

## 5. Architectural Decision: Theme Label & Description Mapping

### Context
K-Means clustering runs mathematically in python (`embed_and_cluster` node) and produces temporary theme headers (e.g. `"Theme 0"`, `"Theme 1"`) with empty descriptions. The LLM then analyzes the reviews of each cluster in a subsequent node (`generate_report`) and produces rich, descriptive labels (e.g. `"Biometric Login Issues"`) and summaries.

### Resolution & Database Sync
To ensure the SQLite `themes` table stores human-readable descriptive data instead of generic placeholders:
1. The LLM output JSON schema is configured to include a **`cluster_id`** key matching each generated theme back to its source K-Means cluster number (e.g., `1` for Cluster 1, `2` for Cluster 2, etc.).
2. The `generate_report` node parses the LLM output, converts `cluster_id` values to 0-based indices, and maps them to update the `label` and `description` attributes of the `ThemeCluster` instances in the graph state.
3. When the `audit_log` node runs, it persists these updated, descriptively populated objects to the SQLite database.

