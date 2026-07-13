"""Embed review texts and cluster them into themes.

Pipeline:
    1. (Optional) Filter non-English reviews via ``langdetect``
    2. Encode texts with ``sentence-transformers/all-MiniLM-L6-v2`` → 384-dim
    3. Scale with ``StandardScaler``
    4. Try ``KMeans`` for k ∈ [3, 8], pick best silhouette score
    5. Build ``ThemeCluster`` objects with per-cluster stats

Embedding cache:
    Embeddings are saved to ``data/embeddings/{run_id}.npy`` so retries
    skip the (slow) encoding step.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from review_pulse.config import PROJECT_ROOT
from review_pulse.models import Review, ThemeCluster

logger = logging.getLogger(__name__)

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_EMBEDDING_DIR = PROJECT_ROOT / "data" / "embeddings"

# Clustering hyperparams
_K_MIN = 3
_K_MAX = 8


# ──────────────────────────────────────────────────────────────
#  Optional language filter
# ──────────────────────────────────────────────────────────────


def filter_english(reviews: list[Review], confidence_threshold: float = 0.9) -> list[Review]:
    """Drop reviews that are confidently non-English.

    Uses ``langdetect`` if available; silently returns all reviews on
    import failure.
    """
    try:
        from langdetect import detect_langs
    except ImportError:
        logger.debug("langdetect not installed; skipping language filter")
        return reviews

    kept: list[Review] = []
    for review in reviews:
        try:
            langs = detect_langs(review.text)
            # Keep if top language is English with sufficient confidence
            top = langs[0]
            if top.lang == "en" and top.prob >= confidence_threshold:
                kept.append(review)
            elif top.lang == "en":
                # English but low confidence — keep anyway
                kept.append(review)
            elif top.prob < confidence_threshold:
                # Non-English but low confidence — keep (could be mixed)
                kept.append(review)
            else:
                logger.debug(
                    "Dropping non-English review (lang=%s, prob=%.2f): %.40s…",
                    top.lang,
                    top.prob,
                    review.text,
                )
        except Exception:
            # langdetect can fail on very short texts — keep those
            kept.append(review)

    logger.info(
        "Language filter: kept %d / %d reviews",
        len(kept),
        len(reviews),
    )
    return kept


# ──────────────────────────────────────────────────────────────
#  Embedding
# ──────────────────────────────────────────────────────────────

# Lazy-loaded model singleton
_model = None


def _get_model():
    """Lazy-load the sentence-transformer model (cached after first call)."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model: %s (first run may download ~90 MB)", _MODEL_NAME)
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def embed_texts(
    texts: list[str],
    run_id: str | None = None,
    cache_dir: Path | None = None,
) -> np.ndarray:
    """Encode texts into 384-dim embeddings.

    If *run_id* is given, attempts to load from / save to the cache.
    """
    import os
    cache_path: Path | None = None
    if run_id:
        if cache_dir is None:
            from review_pulse.config import load_settings
            settings = load_settings()
            cache_dir = settings.database_path.parent / "embeddings"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{run_id}.npy"

        if cache_path.exists():
            logger.info("Loading cached embeddings from %s", cache_path)
            return np.load(cache_path)

    # Check if we should run in low resource/memory mode
    low_resource = (
        os.environ.get("LOW_RESOURCE_MODE", "").lower() in ("true", "1")
        or os.environ.get("RENDER") is not None
    )

    if low_resource:
        logger.info("Low resource mode active: using TF-IDF for text vectorization (saves 400MB+ RAM)")
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            vectorizer = TfidfVectorizer(max_features=384, stop_words="english")
            embeddings = vectorizer.fit_transform(texts).toarray()
            # Pad with zeros if we have fewer unique words than 384
            if embeddings.shape[1] < 384:
                padding = np.zeros((embeddings.shape[0], 384 - embeddings.shape[1]))
                embeddings = np.hstack([embeddings, padding])
            
            if cache_path is not None:
                np.save(cache_path, embeddings)
                logger.info("Cached embeddings to %s", cache_path)
            return embeddings
        except Exception as exc:
            logger.warning("TF-IDF fallback vectorization failed: %s. Trying sentence-transformers...", exc)

    try:
        model = _get_model()
        logger.info("Encoding %d texts…", len(texts))
        embeddings = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
    except Exception as exc:
        logger.warning(
            "Failed to load sentence-transformers model (possibly OOM or import error): %s. "
            "Falling back to TF-IDF vectorizer.",
            exc,
        )
        from sklearn.feature_extraction.text import TfidfVectorizer
        vectorizer = TfidfVectorizer(max_features=384, stop_words="english")
        embeddings = vectorizer.fit_transform(texts).toarray()
        if embeddings.shape[1] < 384:
            padding = np.zeros((embeddings.shape[0], 384 - embeddings.shape[1]))
            embeddings = np.hstack([embeddings, padding])

    if cache_path is not None:
        np.save(cache_path, embeddings)
        logger.info("Cached embeddings to %s", cache_path)

    return embeddings


# ──────────────────────────────────────────────────────────────
#  Clustering
# ──────────────────────────────────────────────────────────────


def _select_best_k(
    X: np.ndarray,
    k_min: int = _K_MIN,
    k_max: int = _K_MAX,
) -> tuple[int, KMeans, float]:
    """Try KMeans for k ∈ [k_min, k_max] and return the best by silhouette score."""
    n_samples = X.shape[0]

    # Can't cluster fewer samples than k
    effective_k_max = min(k_max, n_samples - 1)
    effective_k_min = min(k_min, effective_k_max)

    if effective_k_min < 2:
        # Too few reviews to compute silhouette; use k=1
        logger.warning(
            "Only %d reviews — falling back to k=1 (single cluster)", n_samples
        )
        km = KMeans(n_clusters=1, random_state=42, n_init=10)
        km.fit(X)
        return 1, km, -1.0

    best_k = effective_k_min
    best_km: KMeans | None = None
    best_score = -1.0

    for k in range(effective_k_min, effective_k_max + 1):
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(X)
        score = silhouette_score(X, labels)
        logger.debug("k=%d  silhouette=%.4f", k, score)
        if score > best_score:
            best_k = k
            best_km = km
            best_score = score

    assert best_km is not None
    logger.info("Best k=%d with silhouette=%.4f", best_k, best_score)
    return best_k, best_km, best_score


def cluster_reviews(
    reviews: list[Review],
    run_id: str | None = None,
) -> list[ThemeCluster]:
    """Embed, scale, cluster reviews and return ``ThemeCluster`` objects.

    Each review's text is embedded, scaled, then clustered via KMeans.
    Returns one ``ThemeCluster`` per cluster with stats.

    Also mutates the input reviews by setting a ``cluster_id`` attribute
    (added dynamically if not present).

    Args:
        reviews: Cleaned, PII-scrubbed reviews.
        run_id: Optional run ID for embedding cache.

    Returns:
        List of ``ThemeCluster`` objects (one per cluster).
    """
    if not reviews:
        logger.warning("No reviews to cluster")
        return []

    texts = [r.text for r in reviews]

    # 1. Embed
    embeddings = embed_texts(texts, run_id=run_id)

    # 2. Scale
    scaler = StandardScaler()
    X = scaler.fit_transform(embeddings)

    # 3. Cluster
    best_k, km, score = _select_best_k(X)
    labels = km.labels_

    # 4. Build ThemeClusters
    clusters: list[ThemeCluster] = []
    for cid in range(best_k):
        mask = labels == cid
        cluster_reviews_list = [r for r, m in zip(reviews, mask) if m]

        review_count = len(cluster_reviews_list)
        avg_rating = (
            sum(r.rating for r in cluster_reviews_list) / review_count
            if review_count
            else 0.0
        )
        # Pick up to 3 sample review IDs
        sample_ids = [r.review_id for r in cluster_reviews_list[:3]]

        cluster = ThemeCluster(
            cluster_id=cid,
            label=f"Theme {cid + 1}",  # Placeholder; P3 LLM will assign real labels
            description="",  # Filled by LLM in P3
            review_count=review_count,
            avg_rating=round(avg_rating, 2),
            sample_review_ids=sample_ids,
        )
        clusters.append(cluster)

    # 5. Tag each review with its cluster_id (for DB persistence)
    for review, label in zip(reviews, labels):
        review.cluster_id = int(label)  # type: ignore[attr-defined]

    logger.info(
        "Clustered %d reviews into %d themes (silhouette=%.4f)",
        len(reviews),
        best_k,
        score,
    )

    return clusters
