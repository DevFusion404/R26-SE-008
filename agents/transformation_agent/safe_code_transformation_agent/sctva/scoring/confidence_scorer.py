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
    ) -> Tuple[float, Dict[str, float]]:
        syntax_component = bounded(syntax.score)
        structural_component = bounded(structural.score)
        behavioral_component = bounded(behavioral.score)

        base_score = (
            self.weights["syntax"] * syntax_component
            + self.weights["structural"] * structural_component
            + self.weights["behavioral"] * behavioral_component
        )

        details: Dict[str, Any] = {
            "syntax_component": round(syntax_component, 4),
            "structural_component": round(structural_component, 4),
            "behavioral_component": round(behavioral_component, 4),
            "weights": self.weights,
        }

        if invariant is None:
            return bounded(base_score), {
                **details,
                "formula": "syntax_w*syntax + structural_w*structural + behavioral_w*behavioral",
            }

        invariant_component = bounded(invariant.score)
        final_score = (1.0 - DEFAULT_INVARIANT_WEIGHT) * base_score + DEFAULT_INVARIANT_WEIGHT * invariant_component

        return bounded(final_score), {
            **details,
            "invariant_component": round(invariant_component, 4),
            "invariant_weight": DEFAULT_INVARIANT_WEIGHT,
            "formula": "(1-invariant_w)*(syntax_w*syntax + structural_w*structural + behavioral_w*behavioral) + invariant_w*invariant",
        }
