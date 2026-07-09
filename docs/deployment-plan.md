# Deployment Plan — `review-pulse` (Main Pipeline)

## Overview

The `review-pulse` backend consists of two layers that need to be deployed:

1. **LangGraph Pipeline** — the existing CLI that ingests, processes, and generates reports.
2. **FastAPI REST API** — a new layer (to be built) that wraps the pipeline so the frontend and MCP server can communicate with it over HTTP.

---

## Phase D1 — Add FastAPI REST Layer

Before deploying, a REST API layer must be added so the frontend can trigger runs, poll status, and fetch reports. The CLI will continue to work in parallel.

### Endpoints to Expose

| Method | Route | Purpose |
|--------|-------|---------|
| `POST` | `/api/runs` | Trigger a new pipeline run (product, week, force, dry_run) |
| `GET` | `/api/runs/{product}` | List last N runs for a product |
| `GET` | `/api/runs/{run_id}/status` | Poll a specific run's status |
| `GET` | `/api/runs/{run_id}/report` | Fetch the generated markdown report content |
| `GET` | `/api/themes/{run_id}` | Fetch LLM-enriched themes for a run |
| `POST` | `/api/runs/{run_id}/approve` | Approve a pending MCP delivery action (replaces terminal y/n) |

### Implementation Notes
- Use FastAPI's `BackgroundTasks` to run the LangGraph pipeline asynchronously without blocking the HTTP response.
- The `POST /api/runs` endpoint immediately returns a `run_id` with `status: running`; the frontend polls `/api/runs/{run_id}/status` to track progress.
- Store pipeline logs per `run_id` in `data/logs/{run_id}.log` for real-time status streaming.

---

## Phase D2 — Platform Selection

### Recommended: Render (Free/Starter Tier)

**Why Render:**
- Persistent disk support — required for SQLite (`data/review_pulse.db`) and reports.
- Native environment variable management for secrets (no `.env` files in production).
- GitHub auto-deploy on every push to `main`.
- Free tier is sufficient for a single-product weekly pipeline.

**Alternative Options:**

| Platform | Pros | Cons |
|----------|------|------|
| **Railway** | Simple, fast deploys | No persistent disk on free tier |
| **Fly.io** | Persistent volumes, Docker-based | Steeper setup curve |
| **DigitalOcean Droplet** | Full control, cheap VPS | Requires manual server setup |
| **Render** ✅ | Persistent disk, GitHub integration | Free tier has spin-down delays |

---

## Phase D3 — Deployment Steps (Render)

### 1. Add `render.yaml` to Project Root

```yaml
services:
  - type: web
    name: review-pulse-api
    runtime: python
    buildCommand: pip install -e . && python -m review_pulse init-db
    startCommand: uvicorn review_pulse.api:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: GROQ_API_KEY
        sync: false
      - key: EMAIL_RECIPIENTS
        sync: false
      - key: GOOGLE_DOC_ID
        sync: false
      - key: MCP_SERVER_URL
        sync: false
    disk:
      name: review-pulse-data
      mountPath: /data
      sizeGB: 1
```

### 2. Update Data Path References

Update `.env` / Render environment to use absolute paths on the mounted disk:
```env
DATABASE_PATH=/data/review_pulse.db
```

### 3. Set Secrets in Render Dashboard

| Secret Key | Value |
|------------|-------|
| `GROQ_API_KEY` | Your Groq API token |
| `EMAIL_RECIPIENTS` | Comma-separated stakeholder emails |
| `GOOGLE_DOC_ID` | Target Google Doc ID |
| `MCP_SERVER_URL` | Deployed URL of `google-mcp-server` |

---

## Phase D4 — Security

| Concern | Solution |
|---------|---------|
| Unauthorized API access | Add `X-API-Key` header validation middleware |
| API key exposure | Render environment variables only — never committed to code |
| SQLite concurrent writes | Enable WAL mode: `PRAGMA journal_mode=WAL` |
| Data persistence across redeploys | Render persistent disk mounted at `/data` |

---

## Phase D5 — GitHub Actions Update

Update `.github/workflows/weekly-pulse.yml` to trigger the deployed API instead of running the pipeline locally:

```yaml
- name: Trigger Weekly Pipeline Run
  run: |
    curl -X POST https://your-render-url.onrender.com/api/runs \
      -H "Content-Type: application/json" \
      -H "X-API-Key: ${{ secrets.API_KEY }}" \
      -d '{"product": "indmoney", "dry_run": false}'
```

---

## Deployment Checklist

- [ ] Build and test FastAPI REST layer locally
- [ ] Add `render.yaml` to project root
- [ ] Push to GitHub and connect repository to Render
- [ ] Set all environment variables in Render dashboard
- [ ] Verify `GET /api/runs/indmoney` returns correctly
- [ ] Run a live test pipeline trigger via `curl`
- [ ] Confirm `data/review_pulse.db` persists across redeploys
- [ ] Update GitHub Actions workflow to use the deployed API URL
