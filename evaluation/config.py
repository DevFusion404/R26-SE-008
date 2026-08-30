"""
Evaluation Configuration & Constants
-------------------------------------
Defines global default settings, line tolerance bounds, random seeds,
and file output filenames for reproducibility.
"""

from pathlib import Path

# Paths
DEFAULT_DATA_DIR = Path("evaluation/data")
DEFAULT_GROUND_TRUTH_PATH = DEFAULT_DATA_DIR / "MLCQCodeSmellSamples.csv"
DEFAULT_OUTPUT_DIR = Path("evaluation/results/latest")

# Reproducibility
DEFAULT_RANDOM_SEED = 42
DEFAULT_BOOTSTRAP_ITERATIONS = 1000
MIN_BOOTSTRAP_SAMPLE_SIZE = 10

# Matching Configuration
DEFAULT_LINE_TOLERANCE = 5  # Line range tolerance for parser offset differences
CLASS_LINE_TOLERANCE = 10   # Line tolerance for class-level declarations

# Supported Languages (Java Evaluation Focus)
SUPPORTED_LANGUAGES = {"java"}

# Required Ground Truth Columns
REQUIRED_GT_COLUMNS = [
    "sample_id",
    "repository",
    "language",
    "file_path",
    "entity_type",
    "entity_name",
    "start_line",
    "end_line",
    "smell_type",
    "ground_truth",
    "reviewer_1_label",
    "reviewer_2_label",
    "consensus_label",
    "reviewer_1_confidence",
    "reviewer_2_confidence",
    "notes",
]

# Output Filenames
OUTPUT_FILES = {
    "overall_summary": "01_overall_summary.json",
    "per_language_metrics": "02_per_language_metrics.csv",
    "per_smell_metrics": "03_per_smell_metrics.csv",
    "confusion_matrices": "04_confusion_matrices.csv",
    "predictions": "05_predictions.csv",
    "false_positives": "06_false_positives.csv",
    "false_negatives": "07_false_negatives.csv",
    "true_positives": "08_true_positives.csv",
    "true_negatives": "09_true_negatives.csv",
    "unmatched_predictions": "10_unmatched_predictions.csv",
    "unmatched_ground_truth": "11_unmatched_ground_truth.csv",
    "inter_rater_agreement": "12_inter_rater_agreement.json",
    "evaluation_metadata": "13_evaluation_metadata.json",
    "evaluation_report": "14_evaluation_report.md",
    "data_quality_report": "data_quality_report.json",
}
