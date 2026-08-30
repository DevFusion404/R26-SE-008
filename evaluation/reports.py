"""
Evaluation Output & Report Exporter
------------------------------------
Exports machine-readable JSON/CSV metrics and builds presentation-ready Markdown reports.
Generates all 14 required output artifacts into evaluation/results/latest/.
"""

import csv
import json
import datetime
from pathlib import Path
from typing import Any, Dict, List
from evaluation.config import OUTPUT_FILES


def export_evaluation_results(
    output_dir: str | Path,
    overall_summary: Dict[str, Any],
    per_language_metrics: Dict[str, Dict[str, Any]],
    per_smell_metrics: Dict[str, Dict[str, Any]],
    confusion_matrices: Dict[str, Dict[str, int]],
    match_results: Dict[str, Any],
    agreement_report: Dict[str, Any],
    metadata: Dict[str, Any],
    data_quality_report: Dict[str, Any],
) -> None:
    """
    Exports all evaluation result files to the target output directory.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1. 01_overall_summary.json
    with open(out / OUTPUT_FILES["overall_summary"], "w", encoding="utf-8") as f:
        json.dump(overall_summary, f, indent=2)

    # 2. 02_per_language_metrics.csv
    with open(out / OUTPUT_FILES["per_language_metrics"], "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "language", "evaluated_smells_count", "macro_precision",
            "macro_recall", "macro_f1", "micro_precision", "micro_recall", "micro_f1", "total_samples"
        ])
        for lang, lm in per_language_metrics.items():
            writer.writerow([
                lang,
                lm.get("evaluated_smells_count", 0),
                lm.get("macro_precision"),
                lm.get("macro_recall"),
                lm.get("macro_f1"),
                lm.get("micro_precision"),
                lm.get("micro_recall"),
                lm.get("micro_f1"),
                lm.get("total_pooled", {}).get("tp", 0) + lm.get("total_pooled", {}).get("fp", 0) + lm.get("total_pooled", {}).get("fn", 0) + lm.get("total_pooled", {}).get("tn", 0),
            ])

    # 3. 03_per_smell_metrics.csv
    with open(out / OUTPUT_FILES["per_smell_metrics"], "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "smell_type", "tp", "fp", "fn", "tn", "total_samples",
            "precision", "recall", "f1", "accuracy", "specificity", "mcc"
        ])
        for smell, sm in per_smell_metrics.items():
            writer.writerow([
                smell, sm["tp"], sm["fp"], sm["fn"], sm["tn"], sm["total_samples"],
                sm["precision"], sm["recall"], sm["f1"], sm["accuracy"], sm["specificity"], sm["mcc"]
            ])

    # 4. 04_confusion_matrices.csv
    with open(out / OUTPUT_FILES["confusion_matrices"], "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["smell_type", "tp", "fp", "fn", "tn"])
        for smell, cm in confusion_matrices.items():
            writer.writerow([smell, cm["tp"], cm["fp"], cm["fn"], cm["tn"]])

    # 5. 05_predictions.csv
    with open(out / OUTPUT_FILES["predictions"], "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["file_path", "language", "smell_type", "entity_type", "entity_name", "line", "severity", "message"])
        for pred in match_results.get("all_predictions", []):
            writer.writerow([
                pred.get("file_path"), pred.get("language"), pred.get("smell_type"),
                pred.get("entity_type"), pred.get("entity_name"), pred.get("line"),
                pred.get("severity"), pred.get("message")
            ])

    # Helper for matching output CSVs (06 to 09)
    def write_match_csv(filename: str, match_list: List[Dict[str, Any]]):
        with open(out / filename, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["sample_id", "file_path", "smell_type", "gt_label", "pred_file", "pred_smell", "pred_severity"])
            for m in match_list:
                gt = m.get("ground_truth_sample") or {}
                pred = m.get("prediction") or {}
                writer.writerow([
                    gt.get("sample_id", "N/A"),
                    gt.get("file_path", pred.get("file_path", "")),
                    gt.get("smell_type", pred.get("smell_type", "")),
                    gt.get("ground_truth", "N/A"),
                    pred.get("file_path", ""),
                    pred.get("smell_type", ""),
                    pred.get("severity", ""),
                ])

    # 6-9: TP, FP, FN, TN CSVs
    write_match_csv(OUTPUT_FILES["false_positives"], match_results.get("false_positives", []))
    write_match_csv(OUTPUT_FILES["false_negatives"], match_results.get("false_negatives", []))
    write_match_csv(OUTPUT_FILES["true_positives"], match_results.get("true_positives", []))
    write_match_csv(OUTPUT_FILES["true_negatives"], match_results.get("true_negatives", []))

    # 10. 10_unmatched_predictions.csv
    with open(out / OUTPUT_FILES["unmatched_predictions"], "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["file_path", "language", "smell_type", "entity_name", "line", "severity"])
        for p in match_results.get("unmatched_predictions", []):
            writer.writerow([p.get("file_path"), p.get("language"), p.get("smell_type"), p.get("entity_name"), p.get("line"), p.get("severity")])

    # 11. 11_unmatched_ground_truth.csv
    with open(out / OUTPUT_FILES["unmatched_ground_truth"], "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["sample_id", "file_path", "language", "smell_type", "entity_name", "ground_truth"])
        for gt in match_results.get("unmatched_ground_truth", []):
            writer.writerow([gt.get("sample_id"), gt.get("file_path"), gt.get("language"), gt.get("smell_type"), gt.get("entity_name"), gt.get("ground_truth")])

    # 12. 12_inter_rater_agreement.json
    with open(out / OUTPUT_FILES["inter_rater_agreement"], "w", encoding="utf-8") as f:
        json.dump(agreement_report, f, indent=2)

    # 13. 13_evaluation_metadata.json
    with open(out / OUTPUT_FILES["evaluation_metadata"], "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    # Data Quality Report
    with open(out / OUTPUT_FILES["data_quality_report"], "w", encoding="utf-8") as f:
        json.dump(data_quality_report, f, indent=2)

    # 14. 14_evaluation_report.md
    generate_markdown_report(out / OUTPUT_FILES["evaluation_report"], overall_summary, per_language_metrics, per_smell_metrics, agreement_report, metadata)


def generate_markdown_report(
    target_path: Path,
    summary: Dict[str, Any],
    per_language: Dict[str, Dict[str, Any]],
    per_smell: Dict[str, Dict[str, Any]],
    agreement: Dict[str, Any],
    metadata: Dict[str, Any],
) -> None:
    """Generates 14_evaluation_report.md presentation report."""
    md = []
    md.append("# CUQA Code Smell Detection Evaluation Report")
    md.append(f"**Timestamp**: `{metadata.get('evaluation_timestamp')}`  ")
    md.append(f"**Evaluated Repositories**: `{metadata.get('repository_count', 0)}` | **Evaluated Samples N**: `{summary.get('total_samples_evaluated', 0)}`  ")
    md.append(f"**Evaluation Mode**: `{metadata.get('evaluation_mode', 'Real-World Empirical Evaluation')}`\n")

    md.append("## Executive Performance Summary")
    md.append("```")
    md.append("CUQA Detection Evaluation Summary")
    md.append("--------------------------------------------------")
    md.append(f"{'Language':<10} {'Precision':<10} {'Recall':<10} {'Macro-F1':<10} {'N':<6}")
    md.append("--------------------------------------------------")
    for lang, lm in per_language.items():
        p_str = f"{lm['macro_precision']:.4f}" if lm.get("macro_precision") is not None else "N/A"
        r_str = f"{lm['macro_recall']:.4f}" if lm.get("macro_recall") is not None else "N/A"
        f1_str = f"{lm['macro_f1']:.4f}" if lm.get("macro_f1") is not None else "N/A"
        n_val = lm.get("total_pooled", {}).get("tp", 0) + lm.get("total_pooled", {}).get("fp", 0) + lm.get("total_pooled", {}).get("fn", 0) + lm.get("total_pooled", {}).get("tn", 0)
        md.append(f"{lang.capitalize():<10} {p_str:<10} {r_str:<10} {f1_str:<10} {n_val:<6}")
    md.append("--------------------------------------------------")
    overall_f1 = summary.get("overall_macro_f1")
    f1_disp = f"{overall_f1:.4f}" if overall_f1 is not None else "N/A (Pending Ground Truth)"
    md.append(f"Overall Macro-F1: {f1_disp}")
    md.append("```\n")

    md.append("## Detailed Performance breakdown by Smell Type")
    md.append("| Smell Type | TP | FP | FN | TN | Precision | Recall | F1 | MCC |")
    md.append("|---|---|---|---|---|---|---|---|---|")
    for smell, sm in per_smell.items():
        p_s = f"{sm['precision']:.4f}" if sm["precision"] is not None else "N/A"
        r_s = f"{sm['recall']:.4f}" if sm["recall"] is not None else "N/A"
        f1_s = f"{sm['f1']:.4f}" if sm["f1"] is not None else "N/A"
        mcc_s = f"{sm['mcc']:.4f}" if sm["mcc"] is not None else "N/A"
        md.append(f"| `{smell}` | {sm['tp']} | {sm['fp']} | {sm['fn']} | {sm['tn']} | {p_s} | {r_s} | {f1_s} | {mcc_s} |")

    md.append("\n## Inter-Rater Reliability (Human Annotation Consensus)")
    md.append(f"- **Status**: `{agreement.get('status')}`")
    if agreement.get("cohens_kappa") is not None:
        md.append(f"- **Cohen's Kappa (\\kappa)**: `{agreement.get('cohens_kappa')}`")
        md.append(f"- **Observed Agreement**: `{agreement.get('observed_agreement')}`")
        md.append(f"- **Disagreements**: `{agreement.get('disagreement_count')}` / `{agreement.get('jointly_labelled_samples')}`")
    else:
        md.append(f"- **Note**: {agreement.get('reason', 'Inter-rater agreement not available.')}")

    with open(target_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
