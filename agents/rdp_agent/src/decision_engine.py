"""
Decision Engine
================

Implements the weighted scoring mechanism used to evaluate and rank
candidate refactoring techniques. This is Step 4 of the agent pipeline.

The scoring formula is:

    score = w_c × (4 − complexity) + w_r × (4 − risk) + w_i × impact

Lower complexity and risk are better (inverted), higher impact is better.
The default weights are ``complexity=0.2``, ``risk=0.4``, ``impact=0.4``.

When an :class:`ImpactPrediction` is available the engine can apply a
secondary **impact-prediction bonus** via
:meth:`score_candidate_with_impact`, which folds predicted quality-metric
changes into the final score.

The :class:`DecisionEngine` can be extended by subclassing and overriding
:meth:`score_candidate` to implement alternative strategies (e.g., fuzzy
logic, multi-objective optimization, or machine-learning-based ranking).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .models import CodeSmell, ImpactPrediction, MLPrediction

logger = logging.getLogger("rdp_agent.decision_engine")

# Rating string → numeric value mapping
RATING_MAP: Dict[str, int] = {"low": 1, "medium": 2, "high": 3}


class DecisionEngine:
    """Weighted scoring engine for evaluating refactoring candidates.

    Args:
        weights: Optional dict with ``complexity_weight``, ``risk_weight``,
                 ``impact_weight``, and ``impact_prediction_weight``.
                 Defaults to 0.2 / 0.4 / 0.4 / 0.3.
    """

    def __init__(self, weights: Optional[Dict[str, float]] = None) -> None:
        self.weights = weights or {}

    @property
    def complexity_weight(self) -> float:
        return self.weights.get("complexity_weight", 0.2)

    @property
    def risk_weight(self) -> float:
        return self.weights.get("risk_weight", 0.4)

    @property
    def impact_weight(self) -> float:
        return self.weights.get("impact_weight", 0.4)

    @property
    def impact_prediction_weight(self) -> float:
        """Weight applied to the predicted-impact bonus (default 0.3)."""
        return self.weights.get("impact_prediction_weight", 0.3)

    def score_candidate(
        self,
        candidate: Dict[str, Any],
        smell: CodeSmell,
    ) -> float:
        """Score a refactoring candidate for a given smell.

        Args:
            candidate: Refactoring candidate dictionary with ``complexity``,
                       ``risk``, and ``impact`` fields.
            smell: The code smell being addressed (available for future
                   smell-aware scoring adjustments).

        Returns:
            Numeric score (higher is better).
        """
        complexity = RATING_MAP.get(
            candidate.get("complexity", "medium"), 2
        )
        risk = RATING_MAP.get(candidate.get("risk", "medium"), 2)
        impact = RATING_MAP.get(candidate.get("impact", "medium"), 2)

        score = (
            self.complexity_weight * (4 - complexity)
            + self.risk_weight * (4 - risk)
            + self.impact_weight * impact
        )

        logger.debug(
            "Scored '%s': complexity=%s risk=%s impact=%s → %.2f",
            candidate.get("name", "?"),
            candidate.get("complexity"),
            candidate.get("risk"),
            candidate.get("impact"),
            score,
        )
        return score

    # ----- Impact-aware scoring (new) -----

    def score_candidate_with_impact(
        self,
        candidate: Dict[str, Any],
        smell: CodeSmell,
        impact: ImpactPrediction,
    ) -> float:
        """Score a candidate using both catalog ratings *and* predicted impact.

        The final score is:

            base_score + impact_prediction_weight × impact_bonus

        where ``impact_bonus`` aggregates normalised predicted metric
        improvements (complexity reduction, coupling reduction, cohesion
        gain, maintainability) and subtracts a risk penalty.

        Args:
            candidate: Refactoring candidate dictionary.
            smell: The code smell being addressed.
            impact: Predicted impact from the :class:`ImpactPredictor`.

        Returns:
            Numeric score (higher is better).
        """
        base_score = self.score_candidate(candidate, smell)

        # --- Normalise predicted deltas into a 0-based bonus ---
        # Complexity reduction: compare predicted vs. current.
        current_complexity = float(
            smell.metrics.get("cyclomatic_complexity", 10)
        )
        if current_complexity > 0:
            complexity_bonus = (
                current_complexity - impact.predicted_complexity_after
            ) / current_complexity
        else:
            complexity_bonus = 0.0

        # Coupling: negative change is good → invert sign and cap at 1.
        coupling_bonus = min(1.0, max(-1.0, -impact.coupling_change / 10.0))

        # Cohesion: positive change is good → normalise by dividing by 5.
        cohesion_bonus = min(1.0, max(0.0, impact.cohesion_change / 5.0))

        # Maintainability improvement is already 0–1.
        maint_bonus = impact.maintainability_improvement

        # Risk penalty (0–1, higher is worse).
        risk_penalty = impact.risk_score

        impact_bonus = (
            complexity_bonus
            + coupling_bonus
            + cohesion_bonus
            + maint_bonus
            - risk_penalty
        )

        final_score = base_score + self.impact_prediction_weight * impact_bonus

        logger.debug(
            "Impact-adjusted score for '%s': base=%.2f bonus=%.2f → %.2f",
            candidate.get("name", "?"),
            base_score,
            impact_bonus,
            final_score,
        )
        return final_score

    # ----- ML-aware scoring (CodeBERT integration) -----

    @property
    def ml_prediction_weight(self) -> float:
        """Weight applied to the ML prediction bonus (default 0.25)."""
        return self.weights.get("ml_prediction_weight", 0.25)

    def score_candidate_with_ml(
        self,
        candidate: Dict[str, Any],
        smell: CodeSmell,
        impact: ImpactPrediction,
        ml_prediction: MLPrediction,
    ) -> float:
        """Score a candidate using catalog ratings, predicted impact,
        **and** CodeBERT ML predictions.

        The final score is:

            impact_score + ml_prediction_weight × ml_bonus

        where ``ml_bonus`` aggregates the ML scorer's contextual
        suitability, quality improvement, and behavioral risk, scaled
        by the model's own confidence estimate.

        Args:
            candidate: Refactoring candidate dictionary.
            smell: The code smell being addressed.
            impact: Predicted impact from the :class:`ImpactPredictor`.
            ml_prediction: ML-based prediction from the :class:`MLScorer`.

        Returns:
            Numeric score (higher is better).
        """
        # Start with the impact-aware score (base + impact bonus)
        impact_score = self.score_candidate_with_impact(
            candidate, smell, impact
        )

        # --- ML bonus ---
        # Combine the three ML signals, weighted by confidence.
        # contextual_suitability and quality_improvement are positive
        # contributors; behavioral_risk is a penalty.
        ml_raw = (
            ml_prediction.contextual_suitability
            + ml_prediction.quality_improvement
            - ml_prediction.behavioral_risk
        )
        # Scale by model confidence (0 confidence → no ML effect)
        ml_bonus = ml_raw * ml_prediction.confidence

        final_score = impact_score + self.ml_prediction_weight * ml_bonus

        logger.debug(
            "ML-adjusted score for '%s': impact_score=%.2f "
            "ml_raw=%.3f confidence=%.3f ml_bonus=%.3f → %.2f",
            candidate.get("name", "?"),
            impact_score,
            ml_raw,
            ml_prediction.confidence,
            ml_bonus,
            final_score,
        )
        return final_score


