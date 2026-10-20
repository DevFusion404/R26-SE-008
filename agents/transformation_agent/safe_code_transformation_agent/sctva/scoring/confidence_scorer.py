"""Deterministic confidence scoring for SCTVA results."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from ..constants import DEFAULT_CONFIDENCE_WEIGHTS, DEFAULT_INVARIANT_WEIGHT
from ..models import ValidationStepResult
from ..utils.metrics import bounded


class ConfidenceScorer:
    """Computes confidence score using transparent weighted components."""

    def __init__(self, weights: Dict[str, float] | None = None) -> None:
        self.weights = dict(DEFAULT_CONFIDENCE_WEIGHTS)
        if weights:
            self.weights.update(weights)

    def score(
        self,
        *,
        syntax: ValidationStepResult,
        structural: ValidationStepResult,
        behavioral: ValidationStepResult,
        invariant: ValidationStepResult | None = None,
    ) -> Tuple[float, Dict[str, Any]]:
        # Weight only applicable validation evidence and force failed stages to
        # contribute zero confidence before returning score details.
        # A validator can retain a diagnostic similarity score while rejecting
        # an action-specific contract. Confidence follows the final pass/fail
        # decision, so a rejected stage cannot be credited as safe evidence.
        syntax_component = bounded(syntax.score) if syntax.passed else 0.0
        structural_component = bounded(structural.score) if structural.passed else 0.0
        behavioral_component = bounded(behavioral.score) if behavioral.passed else 0.0

        behavioral_applicable = (
            (behavioral.details or {}).get("score_applicable") is not False
        )
        active_components = {
            "syntax": syntax_component,
            "structural": structural_component,
        }
        if behavioral_applicable:
            active_components["behavioral"] = behavioral_component

        active_weight_total = sum(
            self.weights[name] for name in active_components
        )
        # A passed static fallback carries useful evidence but is not a
        # runtime measurement. Exclude it from confidence rather than letting
        # the 0.75 safety cap look like a failed behavioral test. Renormalize
        # the remaining weights so their confidence scale remains 0..1.
        base_score = (
            sum(
                self.weights[name] * component
                for name, component in active_components.items()
            )
            / active_weight_total
            if active_weight_total > 0
            else 0.0
        )
        effective_weights = {
            name: round(self.weights[name] / active_weight_total, 4)
            for name in active_components
        } if active_weight_total > 0 else {}

        details: Dict[str, Any] = {
            "syntax_component": round(syntax_component, 4),
            "structural_component": round(structural_component, 4),
            "weights": effective_weights,
            "configured_weights": self.weights,
            "behavioral_included": behavioral_applicable,
        }
        failed_components = [
            name
            for name, step in (
                ("syntax", syntax),
                ("structural", structural),
                ("behavioral", behavioral),
            )
            if not step.passed
        ]
        if behavioral_applicable:
            details["behavioral_component"] = round(behavioral_component, 4)
        else:
            details["excluded_components"] = ["behavioral"]

        if invariant is None:
            final_score = bounded(base_score)
            if failed_components:
                final_score = min(final_score, 0.49)
            return final_score, {
                **details,
                "failed_components": failed_components,
                "failure_confidence_cap": 0.49 if failed_components else None,
                "formula": (
                    "normalized(syntax_w*syntax + structural_w*structural"
                    + (" + behavioral_w*behavioral" if behavioral_applicable else "")
                    + ")"
                ),
            }

        invariant_component = bounded(invariant.score) if invariant.passed else 0.0
        final_score = (1.0 - DEFAULT_INVARIANT_WEIGHT) * base_score + DEFAULT_INVARIANT_WEIGHT * invariant_component
        if not invariant.passed:
            failed_components.append("invariant")
        final_score = bounded(final_score)
        if failed_components:
            final_score = min(final_score, 0.49)

        return final_score, {
            **details,
            "failed_components": failed_components,
            "failure_confidence_cap": 0.49 if failed_components else None,
            "invariant_component": round(invariant_component, 4),
            "invariant_weight": DEFAULT_INVARIANT_WEIGHT,
            "formula": (
                "(1-invariant_w)*normalized(syntax_w*syntax + structural_w*structural"
                + (" + behavioral_w*behavioral" if behavioral_applicable else "")
                + ") + invariant_w*invariant"
            ),
        }
