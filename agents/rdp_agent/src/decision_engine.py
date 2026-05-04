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

from .models import CodeSmell, ImpactPrediction

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

