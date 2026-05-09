"""Shared metric helpers for structural and confidence calculations."""

from __future__ import annotations

from collections import Counter
from math import sqrt
from typing import Dict, Iterable


def bounded(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp a numeric value to [low, high]."""
    return max(low, min(high, value))


def jaccard_similarity(a: Iterable[str], b: Iterable[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    set_a = set(a)
    set_b = set(b)
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 1.0
    return len(set_a & set_b) / len(union)


def cosine_similarity_from_counts(a: Dict[str, int], b: Dict[str, int]) -> float:
    """Compute cosine similarity from two sparse count maps."""
    keys = set(a) | set(b)
    if not keys:
        return 1.0

    dot = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    norm_a = sqrt(sum(v * v for v in a.values()))
    norm_b = sqrt(sum(v * v for v in b.values()))

    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def normalized_count_similarity(a: int, b: int) -> float:
    """Return 1.0 when counts equal, falling towards 0 as they diverge."""
    if a == 0 and b == 0:
        return 1.0
    high = max(a, b)
    if high == 0:
        return 1.0
    return bounded(1.0 - (abs(a - b) / high))


def count_tokens(tokens: Iterable[str]) -> Dict[str, int]:
    """Count tokens into a dictionary."""
    return dict(Counter(tokens))
