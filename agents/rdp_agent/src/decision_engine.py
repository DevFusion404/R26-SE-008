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

    # ----- MCDA (Multi-Criteria Decision Making) Scoring -----

    @property
    def mcda_quality_weight(self) -> float:
        """MCDA weight for quality/impact criterion (default 0.40)."""
        return self.weights.get("mcda_quality_weight", 0.40)

    @property
    def mcda_complexity_weight(self) -> float:
        """MCDA weight for complexity criterion (default 0.25)."""
        return self.weights.get("mcda_complexity_weight", 0.25)

    @property
    def mcda_risk_weight(self) -> float:
        """MCDA weight for risk criterion (default 0.20)."""
        return self.weights.get("mcda_risk_weight", 0.20)

    @property
    def mcda_dependency_weight(self) -> float:
        """MCDA weight for dependency criterion (default 0.15)."""
        return self.weights.get("mcda_dependency_weight", 0.15)

    @property
    def mcda_ml_weight(self) -> float:
        """MCDA adjustment weight for ML predictions (default 0.25)."""
        return self.weights.get(
            "mcda_ml_weight",
            self.weights.get("ml_prediction_weight", 0.25),
        )

    def normalize_score(self, score: float, min_val: float = 1.0, max_val: float = 3.0) -> float:
        """Normalize a score to 0-1 range.
        
        Args:
            score: The raw score to normalize.
            min_val: Minimum possible value (default 1).
            max_val: Maximum possible value (default 3).
            
        Returns:
            Normalized score in range [0, 1].
        """
        if max_val <= min_val:
            return 0.5
        return (score - min_val) / (max_val - min_val)

    def score_candidate_mcda(
        self,
        candidate: Dict[str, Any],
        smell: CodeSmell,
        dependency_score: float = 0.5,
        ml_prediction: Optional[MLPrediction] = None,
    ) -> Dict[str, Any]:
        """Score a candidate using MCDA (Multi-Criteria Decision Making).
        
        Formula:
            Final Score = (Quality × 0.40) + (Complexity × 0.25) + 
                          (Risk × 0.20) + (Dependency × 0.15)
        
        All scores are normalized to 0-1 range where higher is better.

        Args:
            candidate: Refactoring candidate dictionary with ``complexity``,
                       ``risk``, and ``impact`` fields.
            smell: The code smell being addressed.
            dependency_score: Dependency criterion score (0-1, higher is better).
                             Default 0.5 (neutral).
            ml_prediction: Optional ML prediction for this candidate. When
                           confidence is greater than zero, MCDA selection is
                           adjusted by the ML signal.

        Returns:
            Dictionary with keys:
                - ``quality``: Normalized quality/impact score (0-1)
                - ``complexity``: Normalized complexity score (0-1, inverted)
                - ``risk``: Normalized risk score (0-1, inverted)
                - ``dependency``: Dependency criterion score (0-1)
                - ``quality_weighted``: Quality × weight
                - ``complexity_weighted``: Complexity × weight
                - ``risk_weighted``: Risk × weight
                - ``dependency_weighted``: Dependency × weight
                - ``final_score``: Final MCDA score (0-1)
        """
        # Get raw scores (1-3 range)
        complexity = RATING_MAP.get(
            candidate.get("complexity", "medium"), 2
        )
        risk = RATING_MAP.get(candidate.get("risk", "medium"), 2)
        impact = RATING_MAP.get(candidate.get("impact", "medium"), 2)

        # Normalize to 0-1 range
        # Quality: higher impact is better → direct normalization
        quality_norm = self.normalize_score(impact, 1.0, 3.0)
        
        # Complexity: lower is better → invert (1=best, 3=worst)
        complexity_norm = self.normalize_score(4 - complexity, 1.0, 3.0)
        
        # Risk: lower is better → invert (1=best, 3=worst)
        risk_norm = self.normalize_score(4 - risk, 1.0, 3.0)
        
        # Dependency: use provided score (already 0-1)
        dependency_norm = min(1.0, max(0.0, dependency_score))

        # Apply weights
        quality_weighted = quality_norm * self.mcda_quality_weight
        complexity_weighted = complexity_norm * self.mcda_complexity_weight
        risk_weighted = risk_norm * self.mcda_risk_weight
        dependency_weighted = dependency_norm * self.mcda_dependency_weight

        # Calculate MCDA score before optional ML adjustment.
        base_final_score = (
            quality_weighted + complexity_weighted + 
            risk_weighted + dependency_weighted
        )
        final_score = base_final_score

        result = {
            "quality": round(quality_norm, 3),
            "complexity": round(complexity_norm, 3),
            "risk": round(risk_norm, 3),
            "dependency": round(dependency_norm, 3),
            "quality_weighted": round(quality_weighted, 3),
            "complexity_weighted": round(complexity_weighted, 3),
            "risk_weighted": round(risk_weighted, 3),
            "dependency_weighted": round(dependency_weighted, 3),
            "base_final_score": round(base_final_score, 3),
            "final_score": round(final_score, 3),
            "scoring_method": "mcda",
        }

        if ml_prediction is not None and ml_prediction.confidence > 0:
            ml_confidence = min(1.0, max(0.0, ml_prediction.confidence))
            ml_suitability = min(1.0, max(0.0, ml_prediction.contextual_suitability))
            ml_quality = min(1.0, max(0.0, ml_prediction.quality_improvement))
            ml_risk = min(1.0, max(0.0, ml_prediction.behavioral_risk))
            ml_safety = 1.0 - ml_risk
            ml_score = (ml_suitability + ml_quality + ml_safety) / 3.0
            ml_adjustment = (ml_score - 0.5) * 2.0 * ml_confidence * self.mcda_ml_weight
            final_score = min(1.0, max(0.0, base_final_score + ml_adjustment))

            result.update({
                "ml": round(ml_score, 3),
                "ml_contextual_suitability": round(ml_suitability, 3),
                "ml_quality_improvement": round(ml_quality, 3),
                "ml_behavioral_risk": round(ml_risk, 3),
                "ml_behavioral_safety": round(ml_safety, 3),
                "ml_confidence": round(ml_confidence, 3),
                "ml_weight": round(self.mcda_ml_weight, 3),
                "ml_adjustment": round(ml_adjustment, 3),
                "final_score": round(final_score, 3),
                "scoring_method": "mcda_ml",
            })

        logger.debug(
            "MCDA score for '%s': quality=%.3f complexity=%.3f risk=%.3f "
            "dependency=%.3f final=%.3f method=%s",
            candidate.get("name", "?"),
            quality_norm,
            complexity_norm,
            risk_norm,
            dependency_norm,
            final_score,
            result["scoring_method"],
        )
        return result


