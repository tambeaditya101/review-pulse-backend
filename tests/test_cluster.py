"""Tests for review_pulse.process.cluster — embedding and clustering.

Uses mocked embeddings to avoid downloading the real model during CI.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from review_pulse.models import Review, ThemeCluster
from review_pulse.process.cluster import (
    _select_best_k,
    cluster_reviews,
    embed_texts,
    filter_english,
)


# ──────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────


def _make_reviews(n: int, texts: list[str] | None = None) -> list[Review]:
    """Create n Review objects with distinct texts."""
    if texts is None:
        texts = [f"Review text number {i} for testing" for i in range(n)]
    return [
        Review(
            review_id=f"rev_{i}",
            source="google_play",
            text=texts[i] if i < len(texts) else f"Review {i}",
            rating=(i % 5) + 1,
            review_date=date(2026, 6, 1 + (i % 28)),
        )
        for i in range(n)
    ]


def _fake_embeddings(n: int, dim: int = 384) -> np.ndarray:
    """Generate deterministic fake embeddings."""
    rng = np.random.RandomState(42)
    return rng.randn(n, dim).astype(np.float32)


# ──────────────────────────────────────────────────────────────
#  _select_best_k
# ──────────────────────────────────────────────────────────────


class TestSelectBestK:
    def test_returns_k_in_range(self) -> None:
        X = _fake_embeddings(50)
        k, km, score = _select_best_k(X, k_min=3, k_max=8)
        assert 3 <= k <= 8
        assert score > -1.0
        assert km.n_clusters == k

    def test_fallback_to_k1_when_too_few_samples(self) -> None:
        X = _fake_embeddings(2)
        k, km, score = _select_best_k(X, k_min=3, k_max=8)
        assert k == 1
        assert km.n_clusters == 1
        assert score == -1.0

    def test_adjusts_k_max_to_sample_count(self) -> None:
        X = _fake_embeddings(5)
        k, km, score = _select_best_k(X, k_min=3, k_max=8)
        # k_max effectively becomes 4 (n-1)
        assert k <= 4

    def test_single_sample_fallback(self) -> None:
        X = _fake_embeddings(1)
        k, km, score = _select_best_k(X)
        assert k == 1


# ──────────────────────────────────────────────────────────────
#  cluster_reviews (mocked embeddings)
# ──────────────────────────────────────────────────────────────


class TestClusterReviews:
    @patch("review_pulse.process.cluster.embed_texts")
    def test_basic_clustering(self, mock_embed: MagicMock) -> None:
        n = 30
        reviews = _make_reviews(n)
        mock_embed.return_value = _fake_embeddings(n)

        clusters = cluster_reviews(reviews)

        assert len(clusters) >= 1
        assert all(isinstance(c, ThemeCluster) for c in clusters)
        # Total reviews across clusters should equal input
        total = sum(c.review_count for c in clusters)
        assert total == n

    @patch("review_pulse.process.cluster.embed_texts")
    def test_cluster_has_stats(self, mock_embed: MagicMock) -> None:
        n = 20
        reviews = _make_reviews(n)
        mock_embed.return_value = _fake_embeddings(n)

        clusters = cluster_reviews(reviews)

        for c in clusters:
            assert c.review_count > 0
            assert 1.0 <= c.avg_rating <= 5.0
            assert len(c.sample_review_ids) <= 3
            assert c.label.startswith("Theme ")

    @patch("review_pulse.process.cluster.embed_texts")
    def test_reviews_tagged_with_cluster_id(self, mock_embed: MagicMock) -> None:
        n = 15
        reviews = _make_reviews(n)
        mock_embed.return_value = _fake_embeddings(n)

        clusters = cluster_reviews(reviews)

        for r in reviews:
            assert hasattr(r, "cluster_id")
            assert isinstance(r.cluster_id, int)  # type: ignore[attr-defined]

    @patch("review_pulse.process.cluster.embed_texts")
    def test_empty_reviews(self, mock_embed: MagicMock) -> None:
        clusters = cluster_reviews([])
        assert clusters == []
        mock_embed.assert_not_called()

    @patch("review_pulse.process.cluster.embed_texts")
    def test_few_reviews_fallback(self, mock_embed: MagicMock) -> None:
        """With only 2 reviews, should fall back to k=1."""
        reviews = _make_reviews(2)
        mock_embed.return_value = _fake_embeddings(2)

        clusters = cluster_reviews(reviews)

        assert len(clusters) == 1
        assert clusters[0].review_count == 2


# ──────────────────────────────────────────────────────────────
#  embed_texts (caching)
# ──────────────────────────────────────────────────────────────


class TestEmbedTexts:
    @patch("review_pulse.process.cluster._get_model")
    def test_caches_embeddings(self, mock_model: MagicMock, tmp_path) -> None:
        fake = _fake_embeddings(5)
        model_instance = MagicMock()
        model_instance.encode.return_value = fake
        mock_model.return_value = model_instance

        texts = ["text"] * 5
        result = embed_texts(texts, run_id="test_run", cache_dir=tmp_path)

        assert np.array_equal(result, fake)
        assert (tmp_path / "test_run.npy").exists()

    @patch("review_pulse.process.cluster._get_model")
    def test_loads_from_cache(self, mock_model: MagicMock, tmp_path) -> None:
        # Pre-save cache
        fake = _fake_embeddings(5)
        np.save(tmp_path / "cached_run.npy", fake)

        result = embed_texts(["text"] * 5, run_id="cached_run", cache_dir=tmp_path)

        assert np.array_equal(result, fake)
        # Model should NOT have been called
        mock_model.assert_not_called()


# ──────────────────────────────────────────────────────────────
#  filter_english
# ──────────────────────────────────────────────────────────────


class TestFilterEnglish:
    def test_keeps_english_reviews(self) -> None:
        reviews = _make_reviews(3, texts=[
            "This app is absolutely fantastic for investments",
            "Great user interface and smooth experience",
            "Best mutual fund tracking application ever",
        ])
        result = filter_english(reviews, confidence_threshold=0.9)
        # All English — should keep all
        assert len(result) == 3

    def test_returns_all_if_langdetect_unavailable(self) -> None:
        import builtins
        reviews = _make_reviews(3)
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "langdetect":
                raise ImportError("mocked")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = filter_english(reviews)
            assert len(result) == 3
