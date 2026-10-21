"""Validator-gated metrics for SCTVA's deterministic rule transformers."""

from __future__ import annotations

from statistics import mean
from typing import Any, Iterable


def _percent(numerator: int, denominator: int) -> float | None:
    return round((100.0 * numerator) / denominator, 1) if denominator else None


def classification_metrics(case_results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    # Calculate the same classification metrics for rule-based transformations
    # so rule and model paths remain directly comparable.
    """Measure rule-based transformation acceptance against the shared corpus.

    Positive cases are valid RDP action/target pairs. A positive prediction is
    counted only if deterministic rule output changed the source and survived
    SCTVA's final validation. Negative controls should remain unchanged.
    """

    rows = list(case_results)
    true_positive = false_positive = true_negative = false_negative = 0
    for row in rows:
        expected = bool(row.get("expected_rule_acceptance", True))
        predicted = bool(row.get("final_rule_output_retained"))
        if expected and predicted:
            true_positive += 1
        elif not expected and predicted:
            false_positive += 1
        elif not expected and not predicted:
            true_negative += 1
        else:
            false_negative += 1

    total = len(rows)
    f1_denominator = (2 * true_positive) + false_positive + false_negative
    return {
        "evaluated_cases": total,
        "positive_cases": true_positive + false_negative,
        "negative_cases": true_negative + false_positive,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "false_negative": false_negative,
        "accuracy_percent": _percent(true_positive + true_negative, total),
        "precision_percent": _percent(true_positive, true_positive + false_positive),
        "recall_percent": _percent(true_positive, true_positive + false_negative),
        "specificity_percent": _percent(true_negative, true_negative + false_positive),
        "f1_score_percent": (
            round((200.0 * true_positive) / f1_denominator, 1)
            if f1_denominator
            else None
        ),
    }


def aggregate_language_metrics(
    case_results: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    # Report rule-based outcomes separately for each supported language.
    """Aggregate deterministic rule metrics per language."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in case_results:
        language = str(result.get("language") or "unknown").lower()
        grouped.setdefault(language, []).append(result)

    summary: dict[str, dict[str, Any]] = {}
    for language, rows in sorted(grouped.items()):
        retained_cases = sum(bool(row.get("final_rule_output_retained")) for row in rows)
        validation_passed = sum(bool(row.get("final_validation_passed")) for row in rows)
        rule_actions = sum(int(row.get("accepted_rule_actions") or 0) for row in rows)
        execution_errors = sum(bool(row.get("error")) for row in rows)
        execution_seconds = sum(
            float(row.get("total_execution_seconds") or 0.0) for row in rows
        )
        quality_scores = [
            float(row["validated_quality_score_percent"])
            for row in rows
            if row.get("validated_quality_score_percent") is not None
        ]
        summary[language] = {
            "test_cases": len(rows),
            "accepted_rule_actions": rule_actions,
            "retained_rule_cases": retained_cases,
            "final_validation_passed_cases": validation_passed,
            "execution_errors": execution_errors,
            "rule_transformation_rate_percent": _percent(retained_cases, len(rows)),
            "final_validation_pass_rate_percent": _percent(
                validation_passed, len(rows)
            ),
            "mean_validated_quality_percent": (
                round(mean(quality_scores), 1) if quality_scores else None
            ),
            "total_execution_seconds": round(execution_seconds, 3),
            "average_execution_seconds_per_case": (
                round(execution_seconds / len(rows), 3) if rows else None
            ),
            **classification_metrics(rows),
        }
    return summary


def aggregate_action_metrics(
    case_results: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    # Report which individual refactoring actions succeed or fail in rules.
    """Return rule acceptance metrics for every language/action pair."""

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
    """Render a concise, panel-ready deterministic-rule report."""

    show = lambda value, suffix="%": "N/A" if value is None else f"{value}{suffix}"
    lines = [
        "# SCTVA Rule-Based Transformation Evaluation",
        "",
        "This report evaluates deterministic rule transformations only. A positive result requires the requested rule action to survive final SCTVA validation.",
        "",
    ]
    overall = report.get("overall_classification_metrics") or {}
    if overall:
        lines.extend((
            "## Overall Classification",
            "",
            "| Cases | Accuracy | Precision | Recall | Specificity | F1 | TP/FP/TN/FN |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            "| {cases} | {accuracy} | {precision} | {recall} | {specificity} | {f1} | {tp}/{fp}/{tn}/{fn} |".format(
                cases=overall.get("evaluated_cases", 0),
                accuracy=show(overall.get("accuracy_percent")),
                precision=show(overall.get("precision_percent")),
                recall=show(overall.get("recall_percent")),
                specificity=show(overall.get("specificity_percent")),
                f1=show(overall.get("f1_score_percent")),
                tp=overall.get("true_positive", 0),
                fp=overall.get("false_positive", 0),
                tn=overall.get("true_negative", 0),
                fn=overall.get("false_negative", 0),
            ),
            "",
        ))
    lines.extend((
        "## Language Results",
        "",
        "| Language | Cases | Accuracy | Precision | Recall | F1 | Validation pass | Rule transform | Avg execution |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ))
    for language, metric in report.get("language_metrics", {}).items():
        lines.append(
            "| {language} | {cases} | {accuracy} | {precision} | {recall} | {f1} | {validation} | {transform} | {latency} |".format(
                language=language.title(),
                cases=metric.get("test_cases", 0),
                accuracy=show(metric.get("accuracy_percent")),
                precision=show(metric.get("precision_percent")),
                recall=show(metric.get("recall_percent")),
                f1=show(metric.get("f1_score_percent")),
                validation=show(metric.get("final_validation_pass_rate_percent")),
                transform=show(metric.get("rule_transformation_rate_percent")),
                latency=show(metric.get("average_execution_seconds_per_case"), "s"),
            )
        )
    action_metrics = report.get("action_metrics") or {}
    if action_metrics:
        lines.extend((
            "",
            "## Refactoring Action Results",
            "",
            "| Language | Action | Cases | Accuracy | Precision | Recall | F1 | TP/FP/TN/FN |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ))
        for metric in action_metrics.values():
            lines.append(
                "| {language} | {action} | {cases} | {accuracy} | {precision} | {recall} | {f1} | {tp}/{fp}/{tn}/{fn} |".format(
                    language=str(metric.get("language") or "unknown").title(),
                    action=metric.get("action_type") or "unknown",
                    cases=metric.get("evaluated_cases", 0),
                    accuracy=show(metric.get("accuracy_percent")),
                    precision=show(metric.get("precision_percent")),
                    recall=show(metric.get("recall_percent")),
                    f1=show(metric.get("f1_score_percent")),
                    tp=metric.get("true_positive", 0),
                    fp=metric.get("false_positive", 0),
                    tn=metric.get("true_negative", 0),
                    fn=metric.get("false_negative", 0),
                )
            )
    lines.extend((
        "",
        "## Definitions",
        "",
        "- **Positive case**: a valid, executable RDP action/target pair that a deterministic rule should transform.",
        "- **Negative case**: an intentionally invalid or absent target that should remain unchanged.",
        "- **Validation pass**: the final SCTVA syntax, structural, behavioral, and invariant decision passed without rollback.",
        "- **Rule transform**: a rule-based output changed the source and was retained after final SCTVA validation.",
        "- **Accuracy/F1**: binary rule-acceptance metrics over the same positive and negative corpus used for Qwen evaluation.",
    ))
    return "\n".join(lines) + "\n"
