-- Idempotency + audit
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    product         TEXT NOT NULL,
    week_start      DATE NOT NULL,
    week_end        DATE NOT NULL,
    status          TEXT NOT NULL,
    reviews_fetched INTEGER DEFAULT 0,
    reviews_processed INTEGER DEFAULT 0,
    report_path     TEXT,
    google_doc_id   TEXT,
    email_sent      BOOLEAN DEFAULT FALSE,
    groq_tokens_used INTEGER DEFAULT 0,
    error_message   TEXT,
    started_at      DATETIME,
    completed_at    DATETIME,
    UNIQUE(product, week_start)
);

-- Raw + cleaned review cache (avoid re-fetching on retry)
CREATE TABLE IF NOT EXISTS reviews (
    review_id       TEXT NOT NULL,
    source          TEXT NOT NULL,
    run_id          TEXT REFERENCES runs(run_id),
    text_clean      TEXT NOT NULL,
    rating          INTEGER,
    review_date     DATE,
    cluster_id      INTEGER,
    PRIMARY KEY (review_id, source)
);

-- Theme clusters per run
CREATE TABLE IF NOT EXISTS themes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT REFERENCES runs(run_id),
    label           TEXT,
    description     TEXT,
    review_count    INTEGER,
    avg_rating      REAL
);

CREATE INDEX IF NOT EXISTS idx_runs_product_week ON runs(product, week_start);
CREATE INDEX IF NOT EXISTS idx_reviews_run_id ON reviews(run_id);
CREATE INDEX IF NOT EXISTS idx_themes_run_id ON themes(run_id);
