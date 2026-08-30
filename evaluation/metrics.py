"""
Statistical Metrics Calculator
------------------------------
Calculates Precision, Recall, F1, Accuracy, Specificity, FPR, FNR, MCC,
Macro-F1, and Micro-F1 scores with robust zero-division handling.
"""

import math
from typing import Any, Dict, List, Tuple


def safe_div(num: float, den: float) -> Tuple[float | None, str | None]:
    """Safe division returning (result, note) or (None, reason) on zero division."""
    if den == 0:
        return None, "Zero denominator (undefined)"
    return num / den, None


def calculate_binary_metrics(tp: int, fp: int, fn: int, tn: int) -> Dict[str, Any]:
    """
    Computes all standard classification performance metrics for given TP, FP, FN, TN counts.

    Returns:
        Dict[str, Any]: Detailed metrics dictionary.
    """
    precision, p_note = safe_div(tp, tp + fp)
    recall, r_note = safe_div(tp, tp + fn)

    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = (2 * precision * recall) / (precision + recall)
        f1_note = None
    else:
        f1 = None
        f1_note = "Precision + Recall is zero or undefined"

    accuracy, acc_note = safe_div(tp + tn, tp + tn + fp + fn)
    specificity, spec_note = safe_div(tn, tn + fp)
    fpr, fpr_note = safe_div(fp, fp + tn)
    fnr, fnr_note = safe_div(fn, fn + tp)

    # MCC calculation
    mcc_num = (tp * tn) - (fp * fn)
    mcc_den_sq = (tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)

    if mcc_den_sq <= 0:
        mcc = None
        mcc_note = "Zero product in MCC denominator"
    else:
        mcc = mcc_num / math.sqrt(mcc_den_sq)
        mcc_note = None

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "total_samples": tp + fp + fn + tn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "accuracy": round(accuracy, 4) if accuracy is not None else None,
        "specificity": round(specificity, 4) if specificity is not None else None,
        "false_positive_rate": round(fpr, 4) if fpr is not None else None,
        "false_negative_rate": round(fnr, 4) if fnr is not None else None,
        "mcc": round(mcc, 4) if mcc is not None else None,
        "notes": {
            "precision": p_note,
            "recall": r_note,
            "f1": f1_note,
            "accuracy": acc_note,
            "specificity": spec_note,
            "mcc": mcc_note,
        },
    }


def calculate_macro_micro_aggregations(
    smell_metrics: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Computes Macro and Micro metrics across a set of smell metric records.

    Macro F1 = Arithmetic mean of valid individual smell F1 scores.
    Micro F1 = F1 score computed from pooled TP, FP, FN counts.
    """
    if not smell_metrics:
        return {
            "macro_precision": None,
            "macro_recall": None,
            "macro_f1": None,
            "micro_precision": None,
            "micro_recall": None,
            "micro_f1": None,
            "evaluated_smells_count": 0,
        }

    # Macro calculation
    precisions = [m["precision"] for m in smell_metrics.values() if m.get("precision") is not None]
    recalls = [m["recall"] for m in smell_metrics.values() if m.get("recall") is not None]
    f1s = [m["f1"] for m in smell_metrics.values() if m.get("f1") is not None]

    macro_p = sum(precisions) / len(precisions) if precisions else None
    macro_r = sum(recalls) / len(recalls) if recalls else None
    macro_f1 = sum(f1s) / len(f1s) if f1s else None

    # Micro calculation (pooled counts)
    total_tp = sum(m.get("tp", 0) for m in smell_metrics.values())
    total_fp = sum(m.get("fp", 0) for m in smell_metrics.values())
    total_fn = sum(m.get("fn", 0) for m in smell_metrics.values())
    total_tn = sum(m.get("tn", 0) for m in smell_metrics.values())

    micro_binary = calculate_binary_metrics(total_tp, total_fp, total_fn, total_tn)

    return {
        "macro_precision": round(macro_p, 4) if macro_p is not None else None,
        "macro_recall": round(macro_r, 4) if macro_r is not None else None,
        "macro_f1": round(macro_f1, 4) if macro_f1 is not None else None,
        "micro_precision": micro_binary["precision"],
        "micro_recall": micro_binary["recall"],
        "micro_f1": micro_binary["f1"],
        "total_pooled": {
            "tp": total_tp,
            "fp": total_fp,
            "fn": total_fn,
            "tn": total_tn,
        },
        "evaluated_smells_count": len(smell_metrics),
    }
