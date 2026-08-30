"""
CUQA Evaluation CLI Runner
--------------------------
Command-line interface to execute CUQA detection evaluation against ground truth datasets.
"""

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from evaluation.config import (
    DEFAULT_BOOTSTRAP_ITERATIONS,
    DEFAULT_GROUND_TRUTH_PATH,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RANDOM_SEED,
)
from evaluation.ground_truth_loader import load_ground_truth
from evaluation.cuqa_runner import run_cuqa_on_repository
from evaluation.prediction_normalizer import normalize_cuqa_predictions
from evaluation.matcher import match_predictions_to_ground_truth
from evaluation.metrics import calculate_binary_metrics, calculate_macro_micro_aggregations
from evaluation.agreement import calculate_cohens_kappa
from evaluation.bootstrap import calculate_bootstrap_ci
from evaluation.reports import export_evaluation_results


def run_evaluation_pipeline(args: argparse.Namespace) -> int:
    """Executes full evaluation pipeline."""
    gt_path = Path(args.ground_truth)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("==========================================================")
    print("      CUQA CODE SMELL DETECTION EVALUATION FRAMEWORK      ")
    print("==========================================================")
    print(f"Ground Truth File: {gt_path}")
    print(f"Target Repositories: {args.repos}")
    print(f"Output Directory: {output_dir}\n")

    # 1. Load Ground Truth Data
    gt_records, quality_report = load_ground_truth(gt_path)

    # Filter GT records if language / smell flags passed
    if args.language:
        gt_records = [r for r in gt_records if r["language"] == args.language.lower()]
    if args.smell:
        gt_records = [r for r in gt_records if r["smell_type"].lower() == args.smell.lower()]

    print(f"Data Quality Validation: {'PASSED' if quality_report['passed'] else 'WARNINGS/ERRORS'}")
    print(f"Valid Ground Truth Samples Loaded: {len(gt_records)}")

    if not quality_report["passed"]:
        print("\nData Quality Errors:")
        for err in quality_report["errors"]:
            print(f"  - {err}")
        if len(gt_records) == 0:
            print("\nCritical Data Quality Failure: Evaluation aborted.")
            # Export data quality report even on failure
            with open(output_dir / "data_quality_report.json", "w", encoding="utf-8") as f:
                json.dump(quality_report, f, indent=2)
            return 1

    # Check if empty dataset (Not Evaluated Yet state)
    if len(gt_records) == 0:
        print("\n[NOTE] No ground truth records available in dataset.")
        print("Evaluation Infrastructure is fully instantiated.")
        print("Status: NOT YET EVALUATED (Pending Human Ground-Truth Annotation).\n")

        overall_summary = {
            "status": "NOT YET EVALUATED",
            "message": "Evaluation infrastructure ready; pending ground truth data.",
            "total_samples_evaluated": 0,
            "overall_macro_f1": None,
            "overall_micro_f1": None,
        }
        metadata = {
            "evaluation_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "status": "NOT YET EVALUATED",
            "repository_count": 0,
            "evaluation_mode": "Pending Ground Truth",
        }
        export_evaluation_results(
            output_dir=output_dir,
            overall_summary=overall_summary,
            per_language_metrics={},
            per_smell_metrics={},
            confusion_matrices={},
            match_results={"all_predictions": [], "true_positives": [], "false_positives": [], "false_negatives": [], "true_negatives": [], "unmatched_predictions": [], "unmatched_ground_truth": []},
            agreement_report={"status": "Inter-rater agreement not available."},
            metadata=metadata,
            data_quality_report=quality_report,
        )
        return 0

    # 2. Run CUQA on Evaluation Repositories
    repos_dir = Path(args.repos)
    all_predictions: List[Dict[str, Any]] = []
    analyzed_repo_names: List[str] = []

    if repos_dir.exists() and repos_dir.is_dir():
        repo_subdirs = [d for d in repos_dir.iterdir() if d.is_dir()]
        if not repo_subdirs:
            # Check if repos_dir itself is a single code repository
            repo_subdirs = [repos_dir]

        for repo in repo_subdirs:
            print(f"Running CUQA on evaluation repository: '{repo.name}'...")
            try:
                cuqa_rep = run_cuqa_on_repository(repo)
                preds = normalize_cuqa_predictions(cuqa_rep)
                all_predictions.extend(preds)
                analyzed_repo_names.append(repo.name)
            except Exception as e:
                print(f"Warning: Failed to run CUQA on '{repo.name}': {e}")
    else:
        print(f"Note: Repositories directory '{repos_dir}' not found or empty.")

    # 3. Match Predictions vs Ground Truth
    print(f"Matching {len(all_predictions)} CUQA predictions against {len(gt_records)} Ground Truth records...")
    match_results = match_predictions_to_ground_truth(all_predictions, gt_records)
    match_results["all_predictions"] = all_predictions

    # 4. Calculate Per-Smell Metrics
    per_smell_metrics: Dict[str, Dict[str, Any]] = {}
    confusion_matrices: Dict[str, Dict[str, int]] = {}

    all_smells = set(r["smell_type"] for r in gt_records) | set(p["smell_type"] for p in all_predictions)
    for smell in sorted(all_smells):
        tps = sum(1 for m in match_results["true_positives"] if (m.get("ground_truth_sample") or {}).get("smell_type") == smell)
        fps = sum(1 for m in match_results["false_positives"] if (m.get("prediction") or {}).get("smell_type") == smell or (m.get("ground_truth_sample") or {}).get("smell_type") == smell)
        fns = sum(1 for m in match_results["false_negatives"] if (m.get("ground_truth_sample") or {}).get("smell_type") == smell)
        tns = sum(1 for m in match_results["true_negatives"] if (m.get("ground_truth_sample") or {}).get("smell_type") == smell)

        b_metrics = calculate_binary_metrics(tps, fps, fns, tns)
        per_smell_metrics[smell] = b_metrics
        confusion_matrices[smell] = {"tp": tps, "fp": fps, "fn": fns, "tn": tns}

    # 5. Calculate Per-Language Metrics & Macro/Micro Aggregations
    per_language_metrics: Dict[str, Dict[str, Any]] = {}
    languages = set(r["language"] for r in gt_records) | set(p["language"] for p in all_predictions)

    for lang in sorted(languages):
        lang_smell_metrics = {}
        for smell in sorted(all_smells):
            l_tps = sum(1 for m in match_results["true_positives"] if (m.get("ground_truth_sample") or {}).get("language") == lang and (m.get("ground_truth_sample") or {}).get("smell_type") == smell)
            l_fps = sum(1 for m in match_results["false_positives"] if (m.get("prediction") or {}).get("language") == lang and ((m.get("prediction") or {}).get("smell_type") == smell or (m.get("ground_truth_sample") or {}).get("smell_type") == smell))
            l_fns = sum(1 for m in match_results["false_negatives"] if (m.get("ground_truth_sample") or {}).get("language") == lang and (m.get("ground_truth_sample") or {}).get("smell_type") == smell)
            l_tns = sum(1 for m in match_results["true_negatives"] if (m.get("ground_truth_sample") or {}).get("language") == lang and (m.get("ground_truth_sample") or {}).get("smell_type") == smell)

            if l_tps + l_fps + l_fns + l_tns > 0:
                lang_smell_metrics[smell] = calculate_binary_metrics(l_tps, l_fps, l_fns, l_tns)

        per_language_metrics[lang] = calculate_macro_micro_aggregations(lang_smell_metrics)

    # 6. Overall Aggregations & Bootstrap CIs
    overall_aggregations = calculate_macro_micro_aggregations(per_smell_metrics)
    bootstrap_ci = calculate_bootstrap_ci(match_results, iterations=args.bootstrap_iterations, seed=args.seed)
    agreement_report = calculate_cohens_kappa(gt_records)

    eval_mode = "Controlled Rule-Correctness Benchmark" if args.controlled_only else "Real-World Empirical Evaluation"

    overall_summary = {
        "status": "EVALUATED",
        "evaluation_mode": eval_mode,
        "total_samples_evaluated": len(gt_records),
        "total_predictions_emitted": len(all_predictions),
        "overall_macro_precision": overall_aggregations["macro_precision"],
        "overall_macro_recall": overall_aggregations["macro_recall"],
        "overall_macro_f1": overall_aggregations["macro_f1"],
        "overall_micro_precision": overall_aggregations["micro_precision"],
        "overall_micro_recall": overall_aggregations["micro_recall"],
        "overall_micro_f1": overall_aggregations["micro_f1"],
        "bootstrap_95_ci": bootstrap_ci,
    }

    # Hash ground truth CSV for provenance tracking
    with open(gt_path, "rb") as f:
        gt_checksum = hashlib.sha256(f.read()).hexdigest()

    metadata = {
        "evaluation_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "evaluation_mode": eval_mode,
        "repository_names": analyzed_repo_names,
        "repository_count": len(analyzed_repo_names),
        "number_of_evaluated_samples": len(gt_records),
        "ground_truth_checksum_sha256": gt_checksum,
        "bootstrap_iterations": args.bootstrap_iterations,
        "random_seed": args.seed,
    }

    # Export All Results
    export_evaluation_results(
        output_dir=output_dir,
        overall_summary=overall_summary,
        per_language_metrics=per_language_metrics,
        per_smell_metrics=per_smell_metrics,
        confusion_matrices=confusion_matrices,
        match_results=match_results,
        agreement_report=agreement_report,
        metadata=metadata,
        data_quality_report=quality_report,
    )

    print("\n--------------------------------------------------")
    print("            EVALUATION COMPLETED SUCCESSFULLY       ")
    print("--------------------------------------------------")
    print(f"Overall Macro F1: {overall_summary['overall_macro_f1']}")
    print(f"Overall Micro F1: {overall_summary['overall_micro_f1']}")
    print(f"Results exported to: {output_dir}\n")
    return 0


def main():
    parser = argparse.ArgumentParser(description="CUQA Java Code Smell Detection Evaluation Framework (MLCQ Ground Truth)")
    parser.add_argument("--ground-truth", default=str(DEFAULT_GROUND_TRUTH_PATH), help="Path to ground truth CSV file (MLCQCodeSmellSamples.csv)")
    parser.add_argument("--repos", default="evaluation/repositories", help="Path to evaluation repositories directory")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR), help="Output directory for generated results")
    parser.add_argument("--language", default="java", choices=["java"], help="Language filter (Java focus)")
    parser.add_argument("--smell", help="Filter evaluation by smell type (e.g. LongMethod, LargeClass)")
    parser.add_argument("--bootstrap-iterations", type=int, default=DEFAULT_BOOTSTRAP_ITERATIONS, help="Number of bootstrap iterations")
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED, help="Random seed for bootstrap reproducibility")
    parser.add_argument("--controlled-only", action="store_true", help="Tag run as Controlled Rule-Correctness Benchmark")
    parser.add_argument("--real-world-only", action="store_true", help="Tag run as Real-World Empirical Evaluation")

    args = parser.parse_args()
    sys.exit(run_evaluation_pipeline(args))


if __name__ == "__main__":
    main()
