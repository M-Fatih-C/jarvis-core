"""Ranking heuristics combining semantic similarity, importance, recency, and confidence."""

from datetime import datetime, timezone
import math
from typing import Sequence
from core.config.settings import Settings, get_settings
from core.memory.models import MemoryRecord, MemorySearchResult


class MemoryRanker:
    """Computes composite relevance scores for memory search results."""

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        self.w_semantic = cfg.ranking_weight_semantic
        self.w_importance = cfg.ranking_weight_importance
        self.w_recency = cfg.ranking_weight_recency
        self.w_confidence = cfg.ranking_weight_confidence

    def compute_recency_score(self, updated_at: datetime, now: datetime | None = None) -> float:
        """Compute exponential decay score for recency (half-life of ~14 days)."""
        current_time = now or datetime.now(timezone.utc)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        delta_seconds = max(0.0, (current_time - updated_at).total_seconds())
        days = delta_seconds / 86400.0
        # Decay: e^(-days / 14)
        return float(math.exp(-days / 14.0))

    def rank(
        self,
        hits: Sequence[MemorySearchResult],
        now: datetime | None = None,
    ) -> list[MemorySearchResult]:
        """Rank and score search results using weighted multi-factor heuristic."""
        current_time = now or datetime.now(timezone.utc)
        ranked: list[MemorySearchResult] = []

        for hit in hits:
            rec = hit.record
            recency = self.compute_recency_score(rec.updated_at, now=current_time)
            semantic = max(0.0, hit.semantic_similarity)
            importance = max(0.0, min(1.0, rec.importance))
            confidence = max(0.0, min(1.0, rec.confidence))

            composite = (
                self.w_semantic * semantic
                + self.w_importance * importance
                + self.w_recency * recency
                + self.w_confidence * confidence
            )

            ranked.append(MemorySearchResult(
                record=rec,
                score=composite,
                semantic_similarity=semantic,
            ))

        ranked.sort(key=lambda x: x.score, reverse=True)
        return ranked
