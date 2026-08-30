"""
Human Annotator Agreement Calculator
-----------------------------------
Calculates Cohen's Kappa (kappa) and observed agreement between reviewers.
Never uses CUQA predictions as an annotator.
"""

from typing import Any, Dict, List


def calculate_cohens_kappa(
    ground_truth_records: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Calculates Cohen's Kappa score for ground-truth records containing
    both reviewer_1_label and reviewer_2_label.

    Returns:
        Dict[str, Any]: Agreement statistics report.
    """
    paired_samples = []

    for rec in ground_truth_records:
        r1 = rec.get("reviewer_1_label")
        r2 = rec.get("reviewer_2_label")
        if r1 is not None and r2 is not None and r1 in (0, 1) and r2 in (0, 1):
            paired_samples.append((r1, r2))

    n = len(paired_samples)
    if n < 5:
        return {
            "status": "Inter-rater agreement not available.",
            "reason": f"Insufficient paired reviewer labels (N = {n}, minimum 5 required).",
            "jointly_labelled_samples": n,
            "observed_agreement": None,
            "cohens_kappa": None,
            "disagreement_count": 0,
        }

    # Contingency matrix
    # a: (1, 1), b: (1, 0), c: (0, 1), d: (0, 0)
    a = sum(1 for r1, r2 in paired_samples if r1 == 1 and r2 == 1)
    b = sum(1 for r1, r2 in paired_samples if r1 == 1 and r2 == 0)
    c = sum(1 for r1, r2 in paired_samples if r1 == 0 and r2 == 1)
    d = sum(1 for r1, r2 in paired_samples if r1 == 0 and r2 == 0)

    observed_agreement = (a + d) / n
    disagreement_count = b + c

    # Expected agreement under independence
    r1_pos = (a + b) / n
    r1_neg = (c + d) / n
    r2_pos = (a + c) / n
    r2_neg = (b + d) / n

    p_e = (r1_pos * r2_pos) + (r1_neg * r2_neg)

    if p_e == 1.0:
        kappa = 1.0
    else:
        kappa = (observed_agreement - p_e) / (1.0 - p_e)

    return {
        "status": "Calculated",
        "jointly_labelled_samples": n,
        "observed_agreement": round(observed_agreement, 4),
        "cohens_kappa": round(kappa, 4),
        "disagreement_count": disagreement_count,
        "contingency_matrix": {
            "r1_1_r2_1": a,
            "r1_1_r2_0": b,
            "r1_0_r2_1": c,
            "r1_0_r2_0": d,
        },
    }
