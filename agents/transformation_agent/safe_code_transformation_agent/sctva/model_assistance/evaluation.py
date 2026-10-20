"""Transparent, per-request evaluation metrics for local code-model output."""

from __future__ import annotations

from typing import Any, Iterable


_ACTION_CHECKPOINT_PASSED = {
    "ACCEPTED",
    "PASSED_ACTION_CHECKPOINT_FILE_REJECTED",
}


def _percentage(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round((100.0 * numerator) / denominator, 1)


def build_model_evaluation(
    *,
    model_attempt_count: int,
    model_candidate_count: int,
    model_action_count: int,
    model_generation_seconds: float,
    model_validated_accuracy: float,
    model_output_retained: bool,
    model_used: bool,
    model_candidates: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build honest model metrics from one SCTVA execution scope.

    A rule-based fallback is deliberately excluded from every success metric.
    ``validated_quality_score`` is only present when SCTVA retained model output
    after final validation; it is not a benchmark-wide model accuracy claim.
    """

    attempts = max(0, int(model_attempt_count or 0))
    candidate_count = max(0, int(model_candidate_count or 0))
    accepted_actions = max(0, int(model_action_count or 0))
    generation_seconds = max(0.0, float(model_generation_seconds or 0.0))
    candidates = [
        item for item in (model_candidates or []) if isinstance(item, dict)
    ]
    checkpoint_passed = sum(
        1
        for item in candidates
        if str(item.get("status") or "").strip().upper()
        in _ACTION_CHECKPOINT_PASSED
    )
    # Candidate metadata can be omitted by older backends. Preserve the
    # response's declared count rather than under-reporting those runs.
    generated_candidates = max(candidate_count, len(candidates))

    return {
        "scope": "CURRENT_SCTVA_RUN",
        "evaluable": attempts > 0,
        "model_attempts": attempts,
        "generated_candidates": generated_candidates,
        "action_checkpoint_passed_candidates": checkpoint_passed,
        "accepted_model_actions": accepted_actions,
        "candidate_generation_rate_percent": _percentage(
            generated_candidates,
            attempts,
        ),
        "action_checkpoint_pass_rate_percent": _percentage(
            checkpoint_passed,
            generated_candidates,
        ),
        "accepted_action_rate_percent": _percentage(
            accepted_actions,
            generated_candidates,
        ),
        "final_model_output_retained": bool(model_output_retained and model_used),
        "validated_quality_score_percent": (
            round(max(0.0, min(100.0, float(model_validated_accuracy))), 1)
            if model_used and model_output_retained
            else None
        ),
        "total_generation_seconds": round(generation_seconds, 3),
        "average_generation_seconds_per_attempt": (
            round(generation_seconds / attempts, 3) if attempts else None
        ),
    }
