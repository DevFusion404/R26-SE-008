"""Small, repeatable SCTVA benchmark corpus for Qwen code transformation."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Any, Iterable


@dataclass(frozen=True)
class ModelBenchmarkCase:
    """One isolated RDP-style source/action pair for model evaluation."""

    case_id: str
    language: str
    file_name: str
    source_code: str
    action_type: str
    source_refactoring: str
    parameters: dict[str, Any]
    expected_model_acceptance: bool = True
    dataset_split: str = "positive"
    provenance: str = "sctva_curated"

    def action_payload(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "source_refactoring": self.source_refactoring,
            "parameters": {"source_file": self.file_name, **self.parameters},
        }

    def dataset_payload(self) -> dict[str, Any]:
        """Return a portable, RDP-compatible benchmark record."""

        return {
            "case_id": self.case_id,
            "language": self.language,
            "file_name": self.file_name,
            "source_code": self.source_code,
            "action": self.action_payload(),
            "ground_truth": {
                "model_should_accept": self.expected_model_acceptance,
                "label": "positive" if self.expected_model_acceptance else "negative",
            },
            "dataset_split": self.dataset_split,
            "provenance": self.provenance,
        }


BENCHMARK_CASES: tuple[ModelBenchmarkCase, ...] = (
    ModelBenchmarkCase(
        "java_introduce_constant", "java", "JavaLimit.java",
        "public class JavaLimit {\n    public int maxItems() {\n        return 8;\n    }\n}\n",
        "introduce_constant", "Introduce Constant",
        {"source_class": "JavaLimit", "source_method": "maxItems", "source_line": 3,
         "literal_value": 8, "constant_name": "MAX_ITEMS"},
    ),
    ModelBenchmarkCase(
        "java_rename_symbol", "java", "JavaRename.java",
        "public class JavaRename {\n    public int calculate(int value) {\n        int total = value + 1;\n        return total;\n    }\n}\n",
        "rename_symbol", "Rename Variable",
        {"source_class": "JavaRename", "source_method": "calculate", "old_name": "total", "new_name": "result"},
    ),
    ModelBenchmarkCase(
        "java_extract_method", "java", "JavaExtract.java",
        "public class JavaExtract {\n    public int calculate(int value) {\n        int incremented = value + 1;\n        int doubled = incremented * 2;\n        int adjusted = doubled - 3;\n        int total = adjusted + 5;\n        return total;\n    }\n}\n",
        "extract_method", "Extract Method",
        {"source_class": "JavaExtract", "source_method": "calculate", "start_line": 3,
         "end_line": 6, "new_method_name": "calculateCore"},
    ),
    ModelBenchmarkCase(
        "python_introduce_constant", "python", "python_limit.py",
        "class PythonLimit:\n    def max_items(self):\n        return 10\n",
        "introduce_constant", "Introduce Constant",
        {"source_class": "PythonLimit", "source_method": "max_items", "source_line": 3,
         "literal_value": 10, "constant_name": "MAX_ITEMS"},
    ),
    ModelBenchmarkCase(
        "python_rename_symbol", "python", "python_rename.py",
        "class PythonRename:\n    def calculate(self, value):\n        total = value + 1\n        return total\n",
        "rename_symbol", "Rename Variable",
        {"source_class": "PythonRename", "source_method": "calculate", "old_name": "total", "new_name": "result"},
    ),
    ModelBenchmarkCase(
        "python_extract_method", "python", "python_extract.py",
        "class PythonExtract:\n    def calculate(self, value):\n        incremented = value + 1\n        doubled = incremented * 2\n        adjusted = doubled - 3\n        total = adjusted + 5\n        return total\n",
        "extract_method", "Extract Method",
        {"source_class": "PythonExtract", "source_method": "calculate", "start_line": 3,
         "end_line": 6, "new_method_name": "calculate_core"},
    ),
    ModelBenchmarkCase(
        "c_introduce_constant", "c", "c_limit.c",
        "int max_items(void) {\n    return 16;\n}\n",
        "introduce_constant", "Introduce Constant",
        {"source_method": "max_items", "source_line": 2, "literal_value": 16,
         "constant_name": "MAX_ITEMS"},
    ),
    ModelBenchmarkCase(
        "c_rename_symbol", "c", "c_rename.c",
        "int calculate(int value) {\n    int total = value + 1;\n    return total;\n}\n",
        "rename_symbol", "Rename Variable",
        {"source_method": "calculate", "old_name": "total", "new_name": "result"},
    ),
    ModelBenchmarkCase(
        "c_extract_method", "c", "c_extract.c",
        "int calculate(int value) {\n    int incremented = value + 1;\n    int doubled = incremented * 2;\n    int adjusted = doubled - 3;\n    int total = adjusted + 5;\n    return total;\n}\n",
        "extract_method", "Extract Method",
        {"source_method": "calculate", "start_line": 2, "end_line": 5,
         "new_method_name": "calculate_core"},
    ),
)


def cases_for_languages(languages: Iterable[str] | None = None) -> list[ModelBenchmarkCase]:
    """Return cases in a stable language/case order."""

    selected = {str(item).strip().lower() for item in (languages or []) if str(item).strip()}
    return [case for case in BENCHMARK_CASES if not selected or case.language in selected]


def _percent(numerator: int, denominator: int) -> float | None:
    return round((100.0 * numerator) / denominator, 1) if denominator else None


def classification_metrics(case_results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Measure validator-gated model acceptance as a binary classifier.

    Positive ground truth means the RDP action and target are intentionally
    executable. A positive prediction requires the model-generated output to
    survive final SCTVA validation. Rule fallback is never a model prediction.
    """

    rows = list(case_results)
    true_positive = false_positive = true_negative = false_negative = 0
    attempted_positive_cases = 0
    for row in rows:
        expected = bool(row.get("expected_model_acceptance", True))
        predicted = bool(row.get("final_model_output_retained"))
        if expected and int(row.get("model_attempts") or 0) > 0:
            attempted_positive_cases += 1
        if expected and predicted:
            true_positive += 1
        elif not expected and predicted:
            false_positive += 1
        elif not expected and not predicted:
            true_negative += 1
        else:
            false_negative += 1

    total = len(rows)
    precision = _percent(true_positive, true_positive + false_positive)
    recall = _percent(true_positive, true_positive + false_negative)
    specificity = _percent(true_negative, true_negative + false_positive)
    f1_denominator = (2 * true_positive) + false_positive + false_negative
    return {
        "evaluated_cases": total,
        "positive_cases": true_positive + false_negative,
        "negative_cases": true_negative + false_positive,
        "attempted_positive_cases": attempted_positive_cases,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "false_negative": false_negative,
        "accuracy_percent": _percent(true_positive + true_negative, total),
        "precision_percent": precision,
        "recall_percent": recall,
        "specificity_percent": specificity,
        "f1_score_percent": (
            round((200.0 * true_positive) / f1_denominator, 1)
            if f1_denominator else None
        ),
        "positive_model_attempt_rate_percent": _percent(
            attempted_positive_cases, true_positive + false_negative
        ),
        "retained_among_attempted_positive_percent": _percent(
            true_positive, attempted_positive_cases
        ),
    }


def aggregate_language_metrics(case_results: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate model-only, validator-gated results by language."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in case_results:
        grouped.setdefault(str(result.get("language") or "unknown").lower(), []).append(result)

    summary: dict[str, dict[str, Any]] = {}
    for language, rows in sorted(grouped.items()):
        attempts = sum(int(row.get("model_attempts") or 0) for row in rows)
        candidates = sum(int(row.get("generated_candidates") or 0) for row in rows)
        checkpoint_passed = sum(int(row.get("action_checkpoint_passed_candidates") or 0) for row in rows)
        accepted_actions = sum(int(row.get("accepted_model_actions") or 0) for row in rows)
        retained_cases = sum(bool(row.get("final_model_output_retained")) for row in rows)
        attempted_cases = sum(bool(row.get("model_attempts")) for row in rows)
        fallback_cases = sum(bool(row.get("rule_fallback_used")) for row in rows)
        errors = sum(bool(row.get("error")) for row in rows)
        generation_seconds = sum(float(row.get("total_generation_seconds") or 0.0) for row in rows)
        quality_scores = [float(row["validated_quality_score_percent"]) for row in rows if row.get("validated_quality_score_percent") is not None]
        summary[language] = {
            "test_cases": len(rows),
            "attempted_cases": attempted_cases,
            "model_attempts": attempts,
            "generated_candidates": candidates,
            "action_checkpoint_passed_candidates": checkpoint_passed,
            "accepted_model_actions": accepted_actions,
            "retained_model_cases": retained_cases,
            "rule_fallback_cases": fallback_cases,
            "execution_errors": errors,
            "candidate_generation_rate_percent": _percent(candidates, attempts),
            "action_checkpoint_pass_rate_percent": _percent(checkpoint_passed, candidates),
            "accepted_action_rate_percent": _percent(accepted_actions, candidates),
            "end_to_end_model_retention_rate_percent": _percent(
                retained_cases, attempted_cases
            ),
            "mean_validated_quality_percent": round(mean(quality_scores), 1) if quality_scores else None,
            "total_generation_seconds": round(generation_seconds, 3),
            "average_generation_seconds_per_attempt": round(generation_seconds / attempts, 3) if attempts else None,
            **classification_metrics(rows),
        }
    return summary


def aggregate_action_metrics(case_results: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return classification metrics for every language/action pair."""

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for result in case_results:
        key = (
            str(result.get("language") or "unknown").lower(),
            str(result.get("action_type") or "unknown").lower(),
        )
        grouped.setdefault(key, []).append(result)
    return {
        f"{language}:{action_type}": {
            "language": language,
            "action_type": action_type,
            **classification_metrics(rows),
        }
        for (language, action_type), rows in sorted(grouped.items())
    }


def markdown_report(report: dict[str, Any]) -> str:
    """Render a concise panel-ready benchmark report without inventing scores."""

    lines = [
        "# SCTVA Qwen Model Evaluation", "",
        "This report evaluates model-generated candidates only. Rule-based fallback output is excluded from model-success metrics.", "",
    ]
    overall = report.get("overall_classification_metrics") or {}
    if overall:
        show_overall = lambda value: "N/A" if value is None else f"{value}%"
        lines.extend((
            "## Overall Classification", "",
            "| Cases | Accuracy | Precision | Recall | Specificity | F1 | TP/FP/TN/FN | Positive model attempt rate |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            "| {cases} | {accuracy} | {precision} | {recall} | {specificity} | {f1} | {tp}/{fp}/{tn}/{fn} | {attempt_rate} |".format(
                cases=overall.get("evaluated_cases", 0),
                accuracy=show_overall(overall.get("accuracy_percent")),
                precision=show_overall(overall.get("precision_percent")),
                recall=show_overall(overall.get("recall_percent")),
                specificity=show_overall(overall.get("specificity_percent")),
                f1=show_overall(overall.get("f1_score_percent")),
                tp=overall.get("true_positive", 0),
                fp=overall.get("false_positive", 0),
                tn=overall.get("true_negative", 0),
                fn=overall.get("false_negative", 0),
                attempt_rate=show_overall(
                    overall.get("positive_model_attempt_rate_percent")
                ),
            ), "",
        ))
    lines.extend((
        "## Language Results", "",
        "| Language | Cases | Accuracy | Precision | Recall | F1 | TP/FP/TN/FN | Candidate yield | Checkpoint pass | Avg generation |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ))
    for language, metric in report.get("language_metrics", {}).items():
        show = lambda value, suffix="%": "N/A" if value is None else f"{value}{suffix}"
        lines.append(
            "| {language} | {cases} | {accuracy} | {precision} | {recall} | {f1} | {confusion} | {yield_rate} | {checkpoint} | {latency} |".format(
                language=language.title(), cases=metric["test_cases"],
                accuracy=show(metric.get("accuracy_percent")),
                precision=show(metric.get("precision_percent")),
                recall=show(metric.get("recall_percent")),
                f1=show(metric.get("f1_score_percent")),
                confusion="{}/{}/{}/{}".format(
                    metric.get("true_positive", 0), metric.get("false_positive", 0),
                    metric.get("true_negative", 0), metric.get("false_negative", 0),
                ),
                yield_rate=show(metric["candidate_generation_rate_percent"]),
                checkpoint=show(metric["action_checkpoint_pass_rate_percent"]),
                latency=show(metric["average_generation_seconds_per_attempt"], "s"),
            )
        )
    action_metrics = report.get("action_metrics") or {}
    if action_metrics:
        lines.extend((
            "", "## Action Results", "",
            "| Language | Action | Cases | Accuracy | Recall | F1 | TP/FP/TN/FN |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ))
        for metric in action_metrics.values():
            show = lambda value: "N/A" if value is None else f"{value}%"
            lines.append(
                "| {language} | {action} | {cases} | {accuracy} | {recall} | {f1} | {tp}/{fp}/{tn}/{fn} |".format(
                    language=str(metric.get("language") or "unknown").title(),
                    action=metric.get("action_type") or "unknown",
                    cases=metric.get("evaluated_cases", 0),
                    accuracy=show(metric.get("accuracy_percent")),
                    recall=show(metric.get("recall_percent")),
                    f1=show(metric.get("f1_score_percent")),
                    tp=metric.get("true_positive", 0),
                    fp=metric.get("false_positive", 0),
                    tn=metric.get("true_negative", 0),
                    fn=metric.get("false_negative", 0),
                )
            )
    lines.extend(("", "## Definitions", "",
        "- **Candidate yield**: source-code candidates generated divided by model inference attempts.",
        "- **Eligible cases**: cases that passed SCTVA's safety preconditions and reached model inference.",
        "- **Checkpoint pass**: candidates passing action-level SCTVA syntax, structural, behavioral, and invariant checks.",
        "- **Positive case**: a valid, executable RDP action/target pair that the model should transform.",
        "- **Negative case**: an intentionally invalid or absent target that the model should not transform.",
        "- **Accuracy/F1**: binary model-acceptance metrics. A positive prediction requires model output to survive final SCTVA validation; rule fallback is excluded.",
        "- **Rule-based fallback**: may complete the SCTVA request safely, but is never credited as model success.",
        "- **TP/FP/TN/FN**: true positive, false positive, true negative, and false negative counts."))
    return "\n".join(lines) + "\n"
