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
        syntax_component = bounded(syntax.score)
        structural_component = bounded(structural.score)
        behavioral_component = bounded(behavioral.score)

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
        if behavioral_applicable:
            details["behavioral_component"] = round(behavioral_component, 4)
        else:
            details["excluded_components"] = ["behavioral"]

        if invariant is None:
            return bounded(base_score), {
                **details,
                "formula": (
                    "normalized(syntax_w*syntax + structural_w*structural"
                    + (" + behavioral_w*behavioral" if behavioral_applicable else "")
                    + ")"
                ),
            }

        invariant_component = bounded(invariant.score)
        final_score = (1.0 - DEFAULT_INVARIANT_WEIGHT) * base_score + DEFAULT_INVARIANT_WEIGHT * invariant_component

        return bounded(final_score), {
            **details,
            "invariant_component": round(invariant_component, 4),
            "invariant_weight": DEFAULT_INVARIANT_WEIGHT,
            "formula": (
                "(1-invariant_w)*normalized(syntax_w*syntax + structural_w*structural"
                + (" + behavioral_w*behavioral" if behavioral_applicable else "")
                + ") + invariant_w*invariant"
            ),
        }
