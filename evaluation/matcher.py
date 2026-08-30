"""
Prediction-to-Ground-Truth Matcher
-----------------------------------
Matches normalized CUQA predictions against independent ground-truth records
by language, relative path, smell type, entity type, entity name, and line numbers.
"""

from typing import Any, Dict, List, Set, Tuple
from evaluation.config import DEFAULT_LINE_TOLERANCE, CLASS_LINE_TOLERANCE
from evaluation.prediction_normalizer import normalize_path

# Smell aliases mapping
SMELL_ALIASES = {
    "LongFunction": "LongMethod",
    "LongMethod": "LongMethod",
}


def canonical_smell_type(smell: str) -> str:
    """Normalizes smell type aliases (e.g. C 'LongFunction' -> 'LongMethod')."""
    return SMELL_ALIASES.get(smell, smell)


def matches_line_number(
    gt_start: int | None,
    gt_end: int | None,
    pred_start: int | None,
    pred_end: int | None,
    entity_type: str,
) -> bool:
    """
    Checks line range compatibility between ground truth and CUQA prediction.
    Allows tolerance window for AST parser positional differences.
    """
    if gt_start is None or pred_start is None:
        return True  # Entity-level or file-level match when lines not specified

    # For class-level entity matching, if entity names match and line is before or within class range
    if entity_type == "class":
        if gt_end and pred_start:
            if pred_start <= gt_end:
                return True
        tolerance = CLASS_LINE_TOLERANCE
        return abs(gt_start - pred_start) <= tolerance

    tolerance = DEFAULT_LINE_TOLERANCE

    # Check start line window
    if abs(gt_start - pred_start) <= tolerance:
        return True

    # Check range overlap if end lines exist
    if gt_end and pred_end:
        overlap = max(0, min(gt_end, pred_end) - max(gt_start, pred_start) + 1)
        if overlap > 0:
            return True

    return False


def match_predictions_to_ground_truth(
    predictions: List[Dict[str, Any]],
    ground_truth_records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Matches CUQA predictions to Ground Truth records.

    Returns dict containing:
        true_positives, false_positives, false_negatives, true_negatives,
        unmatched_predictions, unmatched_ground_truth
    """
    matched_gt_ids: Set[str] = set()
    used_pred_indices: Set[int] = set()

    true_positives: List[Dict[str, Any]] = []
    false_positives: List[Dict[str, Any]] = []
    false_negatives: List[Dict[str, Any]] = []
    true_negatives: List[Dict[str, Any]] = []
    unmatched_predictions: List[Dict[str, Any]] = []

    # Indexed predictions for efficient lookup
    # Key: (language, normalized_path, canonical_smell)
    pred_index = {}
    for p_idx, pred in enumerate(predictions):
        key = (
            pred["language"],
            normalize_path(pred["file_path"]),
            canonical_smell_type(pred["smell_type"]),
        )
        if key not in pred_index:
            pred_index[key] = []
        pred_index[key].append((p_idx, pred))

    # Evaluate each ground truth record
    for gt in ground_truth_records:
        gt_id = gt["sample_id"]
        gt_lang = gt["language"].lower()
        gt_path = normalize_path(gt["file_path"])
        gt_smell = canonical_smell_type(gt["smell_type"])
        gt_label = gt["ground_truth"]  # 1 = smell exists, 0 = no smell
        gt_entity_name = (gt.get("entity_name") or "").strip().lower()
        gt_entity_type = (gt.get("entity_type") or "").strip()

        lookup_key = (gt_lang, gt_path, gt_smell)
        candidate_preds = pred_index.get(lookup_key, [])

        matched_pred_idx = None
        matched_pred_record = None

        for p_idx, pred in candidate_preds:
            if p_idx in used_pred_indices:
                continue

            pred_entity_name = (pred.get("entity_name") or "").strip().lower()

            # Name matching
            name_match = True
            if gt_entity_name and pred_entity_name:
                name_match = (gt_entity_name in pred_entity_name) or (pred_entity_name in gt_entity_name)

            # Line range matching
            line_match = matches_line_number(
                gt["start_line"],
                gt["end_line"],
                pred["start_line"],
                pred["end_line"],
                gt_entity_type,
            )

            if name_match and line_match:
                matched_pred_idx = p_idx
                matched_pred_record = pred
                break

        if matched_pred_record is not None:
            matched_gt_ids.add(gt_id)
            used_pred_indices.add(matched_pred_idx)
            match_entry = {
                "ground_truth_sample": gt,
                "prediction": matched_pred_record,
            }

            if gt_label == 1:
                true_positives.append(match_entry)
            else:
                # CUQA detected smell, but ground truth says 0
                false_positives.append(match_entry)
        else:
            # Ground truth record not matched by any prediction
            if gt_label == 1:
                # CUQA failed to detect smell that exists
                false_negatives.append({
                    "ground_truth_sample": gt,
                    "prediction": None,
                })
            else:
                # Ground truth says 0 and CUQA emitted no prediction -> TN
                true_negatives.append({
                    "ground_truth_sample": gt,
                    "prediction": None,
                })

    # Unmatched predictions (CUQA predictions that don't correspond to any GT entity)
    for p_idx, pred in enumerate(predictions):
        if p_idx not in used_pred_indices:
            unmatched_predictions.append(pred)
            # Add to FP list as unmatched prediction
            false_positives.append({
                "ground_truth_sample": None,
                "prediction": pred,
            })

    unmatched_gt = [gt for gt in ground_truth_records if gt["sample_id"] not in matched_gt_ids]

    return {
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "true_negatives": true_negatives,
        "unmatched_predictions": unmatched_predictions,
        "unmatched_ground_truth": unmatched_gt,
    }
