"""
Unit Tests for CUQA Evaluation Framework
-----------------------------------------
Validates mathematical metric formulas, path normalization, matching logic,
zero-division safety, Cohen's Kappa, bootstrap reproducibility, and data quality checks.
"""

import pytest
from evaluation.prediction_normalizer import normalize_path
from evaluation.metrics import calculate_binary_metrics, calculate_macro_micro_aggregations, safe_div
from evaluation.matcher import match_predictions_to_ground_truth, matches_line_number
from evaluation.agreement import calculate_cohens_kappa
from evaluation.bootstrap import calculate_bootstrap_ci
from evaluation.ground_truth_loader import load_ground_truth


def test_path_normalization():
    """Test Windows to Linux path normalization."""
    assert normalize_path("src\\utils\\processor.py") == "src/utils/processor.py"
    assert normalize_path("C:/project/src/main.py") == "project/src/main.py"
    assert normalize_path("///src//app.py") == "src/app.py"


def test_binary_metrics_formula():
    """Test Precision, Recall, F1, Accuracy, Specificity, MCC math."""
    # TP=10, FP=2, FN=3, TN=85
    m = calculate_binary_metrics(10, 2, 3, 85)
    assert m["tp"] == 10
    assert m["fp"] == 2
    assert m["fn"] == 3
    assert m["tn"] == 85
    assert m["precision"] == round(10 / 12, 4)
    assert m["recall"] == round(10 / 13, 4)
    expected_f1 = (2 * (10 / 12) * (10 / 13)) / ((10 / 12) + (10 / 13))
    assert m["f1"] == round(expected_f1, 4)
    assert m["accuracy"] == round(95 / 100, 4)


def test_zero_division_handling():
    """Test safe division returning None with explanation when denominator is 0."""
    res, note = safe_div(5, 0)
    assert res is None
    assert note is not None

    m = calculate_binary_metrics(0, 0, 0, 0)
    assert m["precision"] is None
    assert m["recall"] is None
    assert m["f1"] is None
    assert m["mcc"] is None


def test_macro_micro_aggregations():
    """Test Macro F1 vs Micro F1 calculations."""
    smell_metrics = {
        "LongMethod": calculate_binary_metrics(10, 2, 2, 50),  # Precision=0.8333, Recall=0.8333, F1=0.8333
        "TooManyParameters": calculate_binary_metrics(5, 5, 0, 50), # Precision=0.5, Recall=1.0, F1=0.6667
    }
    agg = calculate_macro_micro_aggregations(smell_metrics)

    # Macro F1 = (0.8333 + 0.6667) / 2 = 0.75
    assert agg["macro_f1"] == pytest.approx(0.75, abs=1e-3)
    # Micro F1 = Pooled TP=15, FP=7, FN=2 -> Micro P=15/22, Micro R=15/17
    assert agg["micro_precision"] == round(15 / 22, 4)
    assert agg["micro_recall"] == round(15 / 17, 4)


def test_prediction_to_gt_matching():
    """Test prediction matching by path, smell type, entity name, and line tolerance."""
    gt_records = [
        {
            "sample_id": "S1",
            "language": "python",
            "file_path": "src/main.py",
            "smell_type": "LongMethod",
            "entity_type": "function",
            "entity_name": "process_data",
            "start_line": 20,
            "end_line": 60,
            "ground_truth": 1,
        },
        {
            "sample_id": "S2",
            "language": "python",
            "file_path": "src/main.py",
            "smell_type": "TooManyParameters",
            "entity_type": "function",
            "entity_name": "calculate_tax",
            "start_line": 80,
            "end_line": 85,
            "ground_truth": 1,
        },
    ]

    predictions = [
        {
            "language": "python",
            "file_path": "src/main.py",
            "smell_type": "LongMethod",
            "entity_type": "function",
            "entity_name": "process_data",
            "start_line": 22,  # within tolerance window of 20
            "end_line": 62,
            "severity": "high",
        }
    ]

    matched = match_predictions_to_ground_truth(predictions, gt_records)
    assert len(matched["true_positives"]) == 1
    assert matched["true_positives"][0]["ground_truth_sample"]["sample_id"] == "S1"
    assert len(matched["false_negatives"]) == 1
    assert matched["false_negatives"][0]["ground_truth_sample"]["sample_id"] == "S2"


def test_line_number_tolerance():
    """Test line range matching with tolerance window."""
    assert matches_line_number(gt_start=10, gt_end=40, pred_start=14, pred_end=42, entity_type="function") is True
    assert matches_line_number(gt_start=10, gt_end=40, pred_start=30, pred_end=70, entity_type="function") is True
    assert matches_line_number(gt_start=10, gt_end=20, pred_start=100, pred_end=120, entity_type="function") is False


def test_cohens_kappa_calculation():
    """Test Cohen's Kappa agreement metric."""
    gt_records = [
        {"reviewer_1_label": 1, "reviewer_2_label": 1},
        {"reviewer_1_label": 1, "reviewer_2_label": 1},
        {"reviewer_1_label": 0, "reviewer_2_label": 0},
        {"reviewer_1_label": 0, "reviewer_2_label": 0},
        {"reviewer_1_label": 1, "reviewer_2_label": 0},
        {"reviewer_1_label": 0, "reviewer_2_label": 0},
    ]
    kappa_res = calculate_cohens_kappa(gt_records)
    assert kappa_res["status"] == "Calculated"
    assert kappa_res["jointly_labelled_samples"] == 6
    assert kappa_res["cohens_kappa"] is not None


def test_bootstrap_reproducibility():
    """Test bootstrap reproducibility with fixed seed."""
    match_entries = {
        "true_positives": [{"id": i} for i in range(15)],
        "false_positives": [{"id": i} for i in range(5)],
        "false_negatives": [{"id": i} for i in range(3)],
        "true_negatives": [{"id": i} for i in range(20)],
    }

    res1 = calculate_bootstrap_ci(match_entries, iterations=100, seed=42)
    res2 = calculate_bootstrap_ci(match_entries, iterations=100, seed=42)
    res3 = calculate_bootstrap_ci(match_entries, iterations=100, seed=999)

    assert res1["f1_95_ci"] == res2["f1_95_ci"]
    assert res1["f1_95_ci"] != res3["f1_95_ci"]  # Different seed gives different bootstrap sample


def test_pipeline_end_to_end(tmp_path):
    """Test full evaluation pipeline execution on temporary ground truth and repository."""
    # Create sample ground truth CSV
    gt_file = tmp_path / "ground_truth.csv"
    gt_file.write_text(
        "sample_id,repository,language,file_path,entity_type,entity_name,start_line,end_line,smell_type,ground_truth,reviewer_1_label,reviewer_2_label,consensus_label,reviewer_1_confidence,reviewer_2_confidence,notes\n"
        "S1,test_repo,python,test.py,function,long_func,1,45,LongMethod,1,1,1,1,1.0,1.0,Test long method\n"
        "S2,test_repo,python,test.py,function,short_func,50,55,LongMethod,0,0,0,0,1.0,1.0,Test short method\n",
        encoding="utf-8",
    )

    records, q_report = load_ground_truth(gt_file)
    assert q_report["passed"] is True
    assert len(records) == 2

    # Dummy CUQA prediction
    predictions = [
        {
            "file_path": "test.py",
            "language": "python",
            "smell_type": "LongMethod",
            "entity_type": "function",
            "entity_name": "long_func",
            "start_line": 1,
            "end_line": 45,
            "severity": "high",
        }
    ]

    match_res = match_predictions_to_ground_truth(predictions, records)
    assert len(match_res["true_positives"]) == 1
    assert len(match_res["true_negatives"]) == 1
    assert len(match_res["false_positives"]) == 0
    assert len(match_res["false_negatives"]) == 0
