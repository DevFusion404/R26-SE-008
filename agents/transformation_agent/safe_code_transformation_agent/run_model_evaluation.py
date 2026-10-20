"""Run the SCTVA Qwen benchmark corpus and write JSON/Markdown metrics."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.model_assistance.benchmark import (
    aggregate_action_metrics,
    aggregate_language_metrics,
    cases_for_languages,
    classification_metrics,
    markdown_report,
)
from sctva.model_assistance.rdp_benchmark_dataset import (
    generate_rdp_benchmark_cases,
    rdp_action_coverage,
    summarize_rdp_csv,
)


def _options(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "strict_mode": True,
        "enable_behavior_tests": True,
        "timeout_seconds": args.validation_timeout_seconds,
        "require_compilation": False,
        "enable_qwen": True,
        "qwen_mode": "primary",
        "qwen_device": args.device,
        "qwen_backend": args.backend,
        "qwen_openvino_model_path": args.openvino_model_path,
        "qwen_model_id": "Qwen/Qwen2.5-Coder-0.5B-Instruct",
        "qwen_max_input_tokens": args.max_input_tokens,
        "qwen_max_new_tokens": args.max_new_tokens,
        "qwen_cpu_threads": args.cpu_threads,
        "qwen_max_actions_per_request": 100,
        "qwen_max_actions_per_file": 100,
        "qwen_generation_timeout_seconds": args.generation_timeout_seconds,
        "qwen_max_generation_seconds_per_request": 0,
        "qwen_runtime_cache": True,
        "qwen_allow_download": args.allow_download,
        "max_parallel_files": 1,
    }


def _case_payload(case: Any, options: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": f"benchmark_{case.case_id}",
        "language": case.language,
        "source_files": [{
            "file_name": case.file_name,
            "language": case.language,
            "source_code": case.source_code,
        }],
        "refactoring_plan": {
            "plan_id": f"benchmark_plan_{case.case_id}",
            "actions": [case.action_payload()],
            "behavior_tests": [],
            "metadata": {"benchmark_case": case.case_id},
        },
        "execution_options": options,
    }


def _case_result(case: Any, result: dict[str, Any]) -> dict[str, Any]:
    evaluation = result.get("model_evaluation") or {}
    return {
        "case_id": case.case_id,
        "language": case.language,
        "action_type": case.action_type,
        "source_refactoring": case.source_refactoring,
        "expected_model_acceptance": bool(case.expected_model_acceptance),
        "dataset_split": case.dataset_split,
        "provenance": case.provenance,
        "model_attempts": int(evaluation.get("model_attempts") or 0),
        "generated_candidates": int(evaluation.get("generated_candidates") or 0),
        "action_checkpoint_passed_candidates": int(
            evaluation.get("action_checkpoint_passed_candidates") or 0
        ),
        "accepted_model_actions": int(evaluation.get("accepted_model_actions") or 0),
        "final_model_output_retained": bool(
            evaluation.get("final_model_output_retained")
        ),
        "validated_quality_score_percent": evaluation.get(
            "validated_quality_score_percent"
        ),
        "total_generation_seconds": float(
            evaluation.get("total_generation_seconds") or 0.0
        ),
        "rule_fallback_used": bool(result.get("rule_fallback_used")),
        "sctva_status": result.get("status"),
        "sctva_success": bool(result.get("success")),
        "model_status": result.get("model_status"),
    }


def _write_dataset_files(
    cases: list[Any], output_dir: Path, timestamp: str
) -> list[str]:
    """Write one inspectable ground-truth corpus per language."""

    paths: list[str] = []
    for language in ("java", "python", "c"):
        records = [case.dataset_payload() for case in cases if case.language == language]
        if not records:
            continue
        path = output_dir / f"sctva_rdp_dataset_{language}_{timestamp}.json"
        path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        paths.append(str(path))
    return paths


def _limit_cases(cases: list[Any], limit: int) -> list[Any]:
    if limit <= 0:
        return cases
    counts: dict[str, int] = {}
    selected = []
    for case in cases:
        if counts.get(case.language, 0) >= limit:
            continue
        counts[case.language] = counts.get(case.language, 0) + 1
        selected.append(case)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", choices=("smoke", "rdp-full"), default="smoke",
        help="smoke runs the original 3 actions/language; rdp-full covers all RDP techniques",
    )
    parser.add_argument("--language", action="append", choices=("java", "python", "c"))
    parser.add_argument("--limit-per-language", type=int, default=0)
    parser.add_argument("--samples-per-action", type=int, default=1)
    parser.add_argument("--seed", type=int, default=26008)
    parser.add_argument(
        "--positive-only", action="store_true",
        help="omit invalid-target negative controls (Accuracy/F1 then have no TN/FP evidence)",
    )
    parser.add_argument(
        "--dataset-only", action="store_true",
        help="write the generated language datasets without running Qwen",
    )
    parser.add_argument("--backend", choices=("auto", "pytorch", "openvino"), default="auto")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cpu")
    parser.add_argument("--openvino-model-path", default="")
    parser.add_argument("--cpu-threads", type=int, default=6)
    parser.add_argument("--max-input-tokens", type=int, default=512)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--generation-timeout-seconds", type=int, default=30)
    parser.add_argument("--validation-timeout-seconds", type=int, default=10)
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--output-dir", default="evaluation_reports")
    args = parser.parse_args()

    if args.suite == "rdp-full":
        selected_cases = generate_rdp_benchmark_cases(
            seed=args.seed,
            samples_per_action=args.samples_per_action,
            include_negative=not args.positive_only,
            languages=args.language,
        )
    else:
        selected_cases = cases_for_languages(args.language)
    selected_cases = _limit_cases(selected_cases, args.limit_per_language)
    options = _options(args)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_paths = _write_dataset_files(selected_cases, output_dir, timestamp)
    for dataset_path in dataset_paths:
        print(f"Dataset: {dataset_path}")
    if args.dataset_only:
        return 0

    agent = SafeCodeTransformationValidationAgent()
    case_results = []
    for case in selected_cases:
        print(f"Running {case.case_id} ({case.language}, {case.action_type})...")
        try:
            case_results.append(_case_result(case, agent.execute(_case_payload(case, options))))
        except Exception as exc:
            case_results.append({
                "case_id": case.case_id,
                "language": case.language,
                "action_type": case.action_type,
                "source_refactoring": case.source_refactoring,
                "expected_model_acceptance": bool(case.expected_model_acceptance),
                "dataset_split": case.dataset_split,
                "provenance": case.provenance,
                "model_attempts": 0,
                "generated_candidates": 0,
                "action_checkpoint_passed_candidates": 0,
                "accepted_model_actions": 0,
                "final_model_output_retained": False,
                "validated_quality_score_percent": None,
                "total_generation_seconds": 0.0,
                "rule_fallback_used": False,
                "sctva_status": "EXECUTION_ERROR",
                "sctva_success": False,
                "model_status": "EXECUTION_ERROR",
                "error": str(exc),
            })

    rdp_csv = (
        Path(__file__).resolve().parents[2]
        / "rdp_agent" / "datasets" / "code_smells_refactoring_dataset_120k.csv"
    )
    report = {
        "benchmark": "SCTVA Qwen model evaluation",
        "timestamp_utc": timestamp,
        "model_id": "Qwen/Qwen2.5-Coder-0.5B-Instruct",
        "suite": args.suite,
        "dataset": {
            "seed": args.seed,
            "samples_per_action": args.samples_per_action,
            "negative_controls_included": not args.positive_only,
            "language_dataset_files": dataset_paths,
            "rdp_action_coverage": rdp_action_coverage(),
            "rdp_reference": summarize_rdp_csv(rdp_csv),
        },
        "execution_options": options,
        "cases": case_results,
        "overall_classification_metrics": classification_metrics(case_results),
        "language_metrics": aggregate_language_metrics(case_results),
        "action_metrics": aggregate_action_metrics(case_results),
    }
    json_path = output_dir / f"sctva_model_evaluation_{timestamp}.json"
    markdown_path = output_dir / f"sctva_model_evaluation_{timestamp}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown_report(report), encoding="utf-8")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
