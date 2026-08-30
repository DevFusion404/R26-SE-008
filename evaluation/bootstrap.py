"""
Bootstrap Confidence Interval Calculator
----------------------------------------
Computes 95% bootstrap confidence intervals for Precision, Recall, and F1
with fixed random seed for scientific reproducibility.
"""

import random
from typing import Any, Dict, List, Tuple
from evaluation.config import DEFAULT_BOOTSTRAP_ITERATIONS, DEFAULT_RANDOM_SEED, MIN_BOOTSTRAP_SAMPLE_SIZE
from evaluation.metrics import calculate_binary_metrics


def calculate_bootstrap_ci(
    match_entries: Dict[str, List[Dict[str, Any]]],
    iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = DEFAULT_RANDOM_SEED,
) -> Dict[str, Any]:
    """
    Computes 95% confidence intervals via non-parametric bootstrap resampling.

    Args:
        match_entries: Dict with keys 'true_positives', 'false_positives',
                       'false_negatives', 'true_negatives'.
        iterations: Number of bootstrap iterations (e.g. 1000 or 5000).
        seed: Fixed random seed.

    Returns:
        Dict[str, Any]: 95% Confidence Intervals for Precision, Recall, F1.
    """
    # Pool all matched/unmatched entity evaluation instances
    # Each sample item is tagged with (is_tp, is_fp, is_fn, is_tn)
    sample_pool = []

    for item in match_entries.get("true_positives", []):
        sample_pool.append(("tp", item))
    for item in match_entries.get("false_positives", []):
        sample_pool.append(("fp", item))
    for item in match_entries.get("false_negatives", []):
        sample_pool.append(("fn", item))
    for item in match_entries.get("true_negatives", []):
        sample_pool.append(("tn", item))

    n = len(sample_pool)
    if n < MIN_BOOTSTRAP_SAMPLE_SIZE:
        return {
            "status": "Bootstrap CI unavailable",
            "reason": f"Sample size N = {n} is below minimum required ({MIN_BOOTSTRAP_SAMPLE_SIZE}).",
            "iterations": iterations,
            "seed": seed,
            "precision_95_ci": None,
            "recall_95_ci": None,
            "f1_95_ci": None,
        }

    rng = random.Random(seed)
    precisions: List[float] = []
    recalls: List[float] = []
    f1s: List[float] = []

    for _ in range(iterations):
        resampled = [rng.choice(sample_pool) for _ in range(n)]

        tp_count = sum(1 for tag, _ in resampled if tag == "tp")
        fp_count = sum(1 for tag, _ in resampled if tag == "fp")
        fn_count = sum(1 for tag, _ in resampled if tag == "fn")
        tn_count = sum(1 for tag, _ in resampled if tag == "tn")

        b_metrics = calculate_binary_metrics(tp_count, fp_count, fn_count, tn_count)

        if b_metrics["precision"] is not None:
            precisions.append(b_metrics["precision"])
        if b_metrics["recall"] is not None:
            recalls.append(b_metrics["recall"])
        if b_metrics["f1"] is not None:
            f1s.append(b_metrics["f1"])

    def get_percentiles(values: List[float]) -> Tuple[float, float] | None:
        if not values:
            return None
        values.sort()
        low_idx = int(0.025 * len(values))
        high_idx = int(0.975 * len(values))
        high_idx = min(high_idx, len(values) - 1)
        return (round(values[low_idx], 4), round(values[high_idx], 4))

    return {
        "status": "Calculated",
        "sample_size": n,
        "iterations": iterations,
        "seed": seed,
        "precision_95_ci": get_percentiles(precisions),
        "recall_95_ci": get_percentiles(recalls),
        "f1_95_ci": get_percentiles(f1s),
    }
