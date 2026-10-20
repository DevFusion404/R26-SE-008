"""Run the SCTVA Qwen benchmark corpus and write JSON/Markdown metrics."""

from __future__ import annotations

import argparse
import json
import time
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
    cases_for_rule_implemented_actions,
    generate_rdp_benchmark_cases,
    rdp_action_coverage,
    rule_implemented_action_coverage,
    summarize_rdp_csv,
)
from sctva.model_assistance import rule_benchmark


def _options(args: argparse.Namespace, engine: str) -> dict[str, Any]:
    return {
        "strict_mode": True,
        "enable_behavior_tests": True,
        "timeout_seconds": args.validation_timeout_seconds,
        "require_compilation": False,
        # Disable local detection so every score measures the supplied RDP
        # action only. This keeps the model and deterministic-rule runs fair.
        "enable_sctva_auto_refactoring": False,
        "enable_qwen": engine == "model",
        "qwen_mode": "primary" if engine == "model" else "fallback",
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


def _rule_case_result(
    case: Any,
    result: dict[str, Any],
    execution_seconds: float,
) -> dict[str, Any]:
    """Return a rule-only outcome without crediting any model output."""

    rule_actions = int(result.get("rule_action_count") or 0)
    validation_passed = bool(
        result.get("success") and not result.get("rollback_occurred")
    )
    output_retained = bool(
        validation_passed
        and result.get("transformation_applied")
        and rule_actions > 0
        and str(result.get("accepted_output_engine") or "") == "RULE_BASED"
    )
    confidence_score = result.get("confidence_score")
    return {
        "case_id": case.case_id,
        "language": case.language,
        "action_type": case.action_type,
        "source_refactoring": case.source_refactoring,
        "expected_rule_acceptance": bool(case.expected_model_acceptance),
        "dataset_split": case.dataset_split,
        "provenance": case.provenance,
        "accepted_rule_actions": rule_actions,
        "final_rule_output_retained": output_retained,
        "final_validation_passed": validation_passed,
        "validated_quality_score_percent": (
            round(float(confidence_score) * 100.0, 1)
            if output_retained and isinstance(confidence_score, (int, float))
            else None
        ),
        "total_execution_seconds": round(max(0.0, execution_seconds), 3),
        "sctva_status": result.get("status"),
        "sctva_success": bool(result.get("success")),
        "accepted_output_engine": result.get("accepted_output_engine"),
    }


def _execution_error_case(case: Any, engine: str, error: Exception) -> dict[str, Any]:
    """Keep failed benchmark cases visible in the relevant confusion matrix."""

    common = {
        "case_id": case.case_id,
        "language": case.language,
        "action_type": case.action_type,
        "source_refactoring": case.source_refactoring,
        "dataset_split": case.dataset_split,
        "provenance": case.provenance,
        "sctva_status": "EXECUTION_ERROR",
        "sctva_success": False,
        "error": str(error),
    }
    if engine == "rule":
        return {
            **common,
            "expected_rule_acceptance": bool(case.expected_model_acceptance),
            "accepted_rule_actions": 0,
            "final_rule_output_retained": False,
            "final_validation_passed": False,
            "validated_quality_score_percent": None,
            "total_execution_seconds": 0.0,
            "accepted_output_engine": "EXECUTION_ERROR",
        }
    return {
        **common,
        "expected_model_acceptance": bool(case.expected_model_acceptance),
        "model_attempts": 0,
        "generated_candidates": 0,
        "action_checkpoint_passed_candidates": 0,
        "accepted_model_actions": 0,
        "final_model_output_retained": False,
        "validated_quality_score_percent": None,
        "total_generation_seconds": 0.0,
        "rule_fallback_used": False,
        "model_status": "EXECUTION_ERROR",
    }


def _run_cases(
    cases: list[Any],
    options: dict[str, Any],
    engine: str,
) -> list[dict[str, Any]]:
    agent = SafeCodeTransformationValidationAgent()
    results = []
    for case in cases:
        print(f"Running {engine}: {case.case_id} ({case.language}, {case.action_type})...")
        started = time.perf_counter()
        try:
            result = agent.execute(_case_payload(case, options))
            elapsed = time.perf_counter() - started
            results.append(
                _rule_case_result(case, result, elapsed)
                if engine == "rule"
                else _case_result(case, result)
            )
        except Exception as exc:
            results.append(_execution_error_case(case, engine, exc))
    return results


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


def _comparison_markdown(reports: dict[str, dict[str, Any]]) -> str:
    """Render the common headline metrics when both engines were evaluated."""

    lines = [
        "# SCTVA Transformation Evaluation Comparison",
        "",
        "Both rows use the same seeded RDP-aligned Java, Python, and C cases.",
        "",
        "| Engine | Cases | Accuracy | Precision | Recall | Specificity | F1 | TP/FP/TN/FN |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for engine, report in reports.items():
        metrics = report.get("overall_classification_metrics") or {}
        show = lambda value: "N/A" if value is None else f"{value}%"
        lines.append(
            "| {engine} | {cases} | {accuracy} | {precision} | {recall} | {specificity} | {f1} | {tp}/{fp}/{tn}/{fn} |".format(
                engine="Qwen model" if engine == "model" else "Rule-based",
                cases=metrics.get("evaluated_cases", 0),
                accuracy=show(metrics.get("accuracy_percent")),
                precision=show(metrics.get("precision_percent")),
                recall=show(metrics.get("recall_percent")),
                specificity=show(metrics.get("specificity_percent")),
                f1=show(metrics.get("f1_score_percent")),
                tp=metrics.get("true_positive", 0),
                fp=metrics.get("false_positive", 0),
                tn=metrics.get("true_negative", 0),
                fn=metrics.get("false_negative", 0),
            )
        )
    return "\n".join(lines) + "\n"


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
        "--implemented-rules-only", action="store_true",
        help="evaluate only deterministic rule actions currently implemented by SCTVA",
    )
    parser.add_argument(
        "--dataset-only", action="store_true",
        help="write the generated language datasets without running either engine",
    )
    parser.add_argument(
        "--engine", choices=("model", "rule", "both"), default="model",
        help="evaluate Qwen, deterministic rules, or both on the same dataset",
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
            implemented_rules_only=args.implemented_rules_only,
        )
    else:
        selected_cases = cases_for_languages(args.language)
        if args.implemented_rules_only:
            selected_cases = cases_for_rule_implemented_actions(selected_cases)
    selected_cases = _limit_cases(selected_cases, args.limit_per_language)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_paths = _write_dataset_files(selected_cases, output_dir, timestamp)
    for dataset_path in dataset_paths:
        print(f"Dataset: {dataset_path}")
    if args.dataset_only:
        return 0

    rdp_csv = (
        Path(__file__).resolve().parents[2]
        / "rdp_agent" / "datasets" / "code_smells_refactoring_dataset_120k.csv"
    )
    dataset_metadata = {
        "seed": args.seed,
        "samples_per_action": args.samples_per_action,
        "negative_controls_included": not args.positive_only,
        "capability_scope": (
            "implemented_deterministic_rules"
            if args.implemented_rules_only
            else "full_rdp_catalog"
        ),
        "language_dataset_files": dataset_paths,
        "rdp_action_coverage": (
            rule_implemented_action_coverage()
            if args.implemented_rules_only
            else rdp_action_coverage()
        ),
        "rdp_reference": summarize_rdp_csv(rdp_csv),
    }
    selected_engines = ("model", "rule") if args.engine == "both" else (args.engine,)
    reports: dict[str, dict[str, Any]] = {}
    for engine in selected_engines:
        options = _options(args, engine)
        case_results = _run_cases(selected_cases, options, engine)
        metrics_module = rule_benchmark if engine == "rule" else None
        reports[engine] = {
            "benchmark": (
                "SCTVA rule-based transformation evaluation"
                if engine == "rule"
                else "SCTVA Qwen model evaluation"
            ),
            "timestamp_utc": timestamp,
            "model_id": "Qwen/Qwen2.5-Coder-0.5B-Instruct" if engine == "model" else None,
            "engine": engine,
            "suite": args.suite,
            "dataset": dataset_metadata,
            "execution_options": options,
            "cases": case_results,
            "overall_classification_metrics": (
                metrics_module.classification_metrics(case_results)
                if metrics_module
                else classification_metrics(case_results)
            ),
            "language_metrics": (
                metrics_module.aggregate_language_metrics(case_results)
                if metrics_module
                else aggregate_language_metrics(case_results)
            ),
            "action_metrics": (
                metrics_module.aggregate_action_metrics(case_results)
                if metrics_module
                else aggregate_action_metrics(case_results)
            ),
        }

        stem = f"sctva_{'rule' if engine == 'rule' else 'model'}_evaluation_{timestamp}"
        json_path = output_dir / f"{stem}.json"
        markdown_path = output_dir / f"{stem}.md"
        json_path.write_text(json.dumps(reports[engine], indent=2), encoding="utf-8")
        markdown_path.write_text(
            metrics_module.markdown_report(reports[engine])
            if metrics_module
            else markdown_report(reports[engine]),
            encoding="utf-8",
        )
        print(f"JSON report: {json_path}")
        print(f"Markdown report: {markdown_path}")

    if args.engine == "both":
        comparison = {
            "benchmark": "SCTVA transformation evaluation comparison",
            "timestamp_utc": timestamp,
            "dataset": dataset_metadata,
            "evaluations": reports,
        }
        comparison_stem = output_dir / f"sctva_transformation_comparison_{timestamp}"
        comparison_stem.with_suffix(".json").write_text(
            json.dumps(comparison, indent=2), encoding="utf-8"
        )
        comparison_stem.with_suffix(".md").write_text(
            _comparison_markdown(reports), encoding="utf-8"
        )
        print(f"Comparison JSON report: {comparison_stem.with_suffix('.json')}")
        print(f"Comparison Markdown report: {comparison_stem.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
