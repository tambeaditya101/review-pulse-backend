# Deployment Plan — `review-pulse`

## Overview

The `review-pulse` backend consists of two independently deployable components:

1. **LangGraph Pipeline + FastAPI REST API** — the main `review-pulse` application (this repository). Deployed on Render.
2. **google-mcp-server** — a separate FastAPI process (separate repository) that wraps Google OAuth credentials for Google Docs and Gmail delivery. Can run locally or be deployed separately.

---

## Components

### review-pulse (this repo)

Runs as a long-lived FastAPI + Uvicorn server on Render. The pipeline is triggered via the REST API or the CLI. SQLite is stored on a Render persistent disk.

### google-mcp-server (separate repo)

A FastAPI server that authenticates with Google OAuth and exposes:
- `POST /append_to_doc` — appends markdown content to a Google Doc
- `POST /create_email_draft` — creates a Gmail draft

The `review-pulse` pipeline communicates with this server via HTTP. For production delivery, this must be running and accessible at the address set in `MCP_SERVER_URL`.

> **Dry-run alternative:** If the MCP server is unavailable (e.g. GitHub Actions CI), run with `--dry-run`. Reports are saved to `data/reports/` only.

---

## REST API Endpoints

The FastAPI layer in `api.py` exposes the following routes:

| Method | Route | Auth | Description |
|---|---|---|---|
| `POST` | `/api/runs` | Optional | Trigger a new pipeline run |
| `GET` | `/api/runs/{product}` | Optional | List recent runs for a product slug |
| `GET` | `/api/runs/{run_id}/status` | Optional | Poll a run's current status |
| `GET` | `/api/runs/{run_id}/report` | Optional | Fetch the generated markdown report content |
| `GET` | `/api/themes/{run_id}` | Optional | Fetch LLM-generated themes for a run |
| `GET` | `/api/debug/mcp` | None | Diagnostic: test MCP server connectivity |

**Authentication:** When `API_KEY` is set as an environment variable, all routes except `/api/debug/mcp` require `X-API-Key: <value>` in the request header. If `API_KEY` is not set, the API is open.

---

## Deployment on Render

### Phase D1 — Persistent Disk

SQLite and generated reports are stored on a persistent Render disk. The `render.yaml` in this repository configures this automatically:

```yaml
disk:
  name: review-pulse-data
  mountPath: /data
  sizeGB: 1
```

`DATABASE_PATH` is set to `/data/review_pulse.db` so the database survives redeploys.

### Phase D2 — Actual `render.yaml`

The following is the current `render.yaml` in this repository:

```yaml
services:
  - type: web
    name: review-pulse-api
    runtime: python
    buildCommand: pip install -e . && pip install --upgrade requests urllib3
    startCommand: python -m review_pulse init-db && uvicorn review_pulse.api:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: GROQ_API_KEY
        sync: false
      - key: EMAIL_RECIPIENTS
        sync: false
      - key: GOOGLE_DOC_ID
        sync: false
      - key: MCP_SERVER_URL
        sync: false
      - key: DATABASE_PATH
        value: /data/review_pulse.db
    disk:
      name: review-pulse-data
      mountPath: /data
      sizeGB: 1
```

> **Note:** The `startCommand` runs `init-db` before starting Uvicorn, which is safe since `init-db` is idempotent (`CREATE TABLE IF NOT EXISTS`).

### Phase D3 — Deployment Steps

1. Push this repository to GitHub.
2. Connect the GitHub repository to Render via the Render dashboard.
3. Render will automatically detect `render.yaml` and create the service.
4. Set the following environment variables in the Render dashboard:

| Key | Value |
|---|---|
| `GROQ_API_KEY` | Your Groq API key |
| `EMAIL_RECIPIENTS` | Comma-separated stakeholder emails |
| `GOOGLE_DOC_ID` | Target Google Doc ID |
| `MCP_SERVER_URL` | URL of your running google-mcp-server |
| `API_KEY` | A strong random value for REST API authentication |

> `DATABASE_PATH` is already set to `/data/review_pulse.db` in `render.yaml` and does not need to be set manually.

5. Trigger a deploy. Render will run the build command then the start command.
6. Visit `https://your-service.onrender.com/docs` to verify the FastAPI docs are accessible.

---

## Phase D4 — Security

| Concern | Solution |
|---|---|
| Unauthorized API access | `X-API-Key` header validation (enabled by setting `API_KEY` env var) |
| API key exposure | Render environment variables only — never committed to code |
| Credential leakage | `.env` and `token.json` are gitignored |
| Duplicate reports/emails | SQLite `UNIQUE(product, week_start)` + `email_sent` boolean |
| Data persistence across redeploys | Render persistent disk mounted at `/data` |
| PII in LLM prompts | PII scrubbing runs before every Groq call |

---

## Phase D5 — GitHub Actions Integration

The workflow in `.github/workflows/weekly-pulse.yml` runs every Monday at 03:30 UTC.

**Important:** GitHub Actions always runs in `--dry-run` mode because the google-mcp-server is not available in the CI environment. To run a live delivery from CI, you would need to trigger the Render-deployed API instead:

```bash
# Example: trigger via deployed API from CI
curl -X POST https://your-render-url.onrender.com/api/runs \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${{ secrets.API_KEY }}" \
  -d '{"product": "indmoney", "dry_run": false}'
```

Required GitHub Actions secrets:

| Secret | Description |
|---|---|
| `GROQ_API_KEY` | Groq API key (used directly in `--dry-run` runs) |
| `EMAIL_RECIPIENTS` | Optional; not used in dry-run mode |

---

## Deployment Checklist

- [ ] `render.yaml` committed and pushed to `main`
- [ ] Repository connected to Render
- [ ] All required environment variables set in Render dashboard (`GROQ_API_KEY`, `EMAIL_RECIPIENTS`, `GOOGLE_DOC_ID`, `MCP_SERVER_URL`, `API_KEY`)
- [ ] `DATABASE_PATH=/data/review_pulse.db` confirmed in Render (set by `render.yaml`)
- [ ] Render deploy completes without errors
- [ ] `GET /docs` returns FastAPI documentation page
- [ ] `GET /api/runs/indmoney` returns empty array (no runs yet)
- [ ] `POST /api/runs` with `dry_run: true` completes and returns `status: completed`
- [ ] `data/review_pulse.db` persists across a manual redeploy
- [ ] GitHub Actions workflow runs cleanly (dry-run mode)
