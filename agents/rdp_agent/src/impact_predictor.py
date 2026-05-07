"""
Refactoring Impact Prediction Module
======================================

Estimates the expected impact of each candidate refactoring on software
quality metrics **before** the Decision Engine ranks them.  This module
sits between candidate generation (Step 3) and decision scoring (Step 4)
in the agent pipeline.

Predicted metrics:
    - **Cyclomatic complexity** after refactoring.
    - **Coupling change** (negative = reduction = better).
    - **Cohesion change** (positive = improvement = better).
    - **Maintainability improvement** (0 – 1 scale).
    - **Risk score** (0 – 1 scale, lower is safer).

The predictions are produced by a configurable heuristic rules table
(:data:`DEFAULT_PREDICTION_RULES`) that maps refactoring names to
expected metric deltas.  For a research prototype this is preferable to
a trained ML model because the rules are transparent, reproducible, and
easy to extend.

Usage::

    from rdp_agent.impact_predictor import ImpactPredictor

    predictor = ImpactPredictor()
    prediction = predictor.predict(smell, candidate)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .models import CodeSmell, ImpactPrediction

logger = logging.getLogger("rdp_agent.impact_predictor")


# ---------------------------------------------------------------------------
# Default Prediction Rules
# ---------------------------------------------------------------------------

# Each rule specifies the *expected* metric deltas when the named
# refactoring is applied.  Values are heuristics derived from software
# engineering literature and can be tuned via configuration.
#
# Keys:
#   complexity_reduction_pct : % of current complexity expected to be removed
#   coupling_delta           : absolute change in coupling (negative = better)
#   cohesion_delta           : absolute change in cohesion (positive = better)
#   maintainability_gain     : 0 – 1 scale improvement in maintainability
#   base_risk                : inherent risk of the refactoring (0 – 1)

DEFAULT_PREDICTION_RULES: Dict[str, Dict[str, float]] = {
    "Extract Method": {
        "complexity_reduction_pct": 0.35,
        "coupling_delta": -2.0,
        "cohesion_delta": 3.0,
        "maintainability_gain": 0.25,
        "base_risk": 0.10,
    },
    "Extract Class": {
        "complexity_reduction_pct": 0.30,
        "coupling_delta": -5.0,
        "cohesion_delta": 5.0,
        "maintainability_gain": 0.35,
        "base_risk": 0.30,
    },
    "Extract Subclass": {
        "complexity_reduction_pct": 0.25,
        "coupling_delta": -3.0,
        "cohesion_delta": 4.0,
        "maintainability_gain": 0.30,
        "base_risk": 0.35,
    },
    "Move Method": {
        "complexity_reduction_pct": 0.15,
        "coupling_delta": -4.0,
        "cohesion_delta": 3.0,
        "maintainability_gain": 0.20,
        "base_risk": 0.15,
    },
    "Replace Temp with Query": {
        "complexity_reduction_pct": 0.20,
        "coupling_delta": -1.0,
        "cohesion_delta": 1.0,
        "maintainability_gain": 0.15,
        "base_risk": 0.10,
    },
    "Introduce Parameter Object": {
        "complexity_reduction_pct": 0.15,
        "coupling_delta": -3.0,
        "cohesion_delta": 2.0,
        "maintainability_gain": 0.20,
        "base_risk": 0.10,
    },
    "Pull Up Method": {
        "complexity_reduction_pct": 0.20,
        "coupling_delta": -2.0,
        "cohesion_delta": 2.0,
        "maintainability_gain": 0.20,
        "base_risk": 0.20,
    },
    "Inline Class": {
        "complexity_reduction_pct": 0.10,
        "coupling_delta": -3.0,
        "cohesion_delta": 1.0,
        "maintainability_gain": 0.15,
        "base_risk": 0.25,
    },
    "Replace Conditional with Polymorphism": {
        "complexity_reduction_pct": 0.40,
        "coupling_delta": -2.0,
        "cohesion_delta": 4.0,
        "maintainability_gain": 0.35,
        "base_risk": 0.30,
    },
    "Collapse Hierarchy": {
        "complexity_reduction_pct": 0.15,
        "coupling_delta": -2.0,
        "cohesion_delta": 1.0,
        "maintainability_gain": 0.15,
        "base_risk": 0.20,
    },
    "Remove Dead Code": {
        "complexity_reduction_pct": 0.10,
        "coupling_delta": -1.0,
        "cohesion_delta": 0.5,
        "maintainability_gain": 0.10,
        "base_risk": 0.05,
    },
    "Replace Data Value with Object": {
        "complexity_reduction_pct": 0.10,
        "coupling_delta": -1.0,
        "cohesion_delta": 2.0,
        "maintainability_gain": 0.15,
        "base_risk": 0.10,
    },
    "Replace Parameter with Method Call": {
        "complexity_reduction_pct": 0.10,
        "coupling_delta": -1.0,
        "cohesion_delta": 1.0,
        "maintainability_gain": 0.10,
        "base_risk": 0.05,
    },
    "Hide Delegate": {
        "complexity_reduction_pct": 0.10,
        "coupling_delta": -3.0,
        "cohesion_delta": 1.0,
        "maintainability_gain": 0.10,
        "base_risk": 0.05,
    },
    "Rename Method": {
        "complexity_reduction_pct": 0.0,
        "coupling_delta": 0.0,
        "cohesion_delta": 0.5,
        "maintainability_gain": 0.05,
        "base_risk": 0.02,
    },
    "Introduce Constant": {
        "complexity_reduction_pct": 0.05,
        "coupling_delta": 0.0,
        "cohesion_delta": 1.0,
        "maintainability_gain": 0.20,
        "base_risk": 0.05,
    },
    "Introduce Facade": {
        "complexity_reduction_pct": 0.15,
        "coupling_delta": -5.0,
        "cohesion_delta": 2.0,
        "maintainability_gain": 0.25,
        "base_risk": 0.20,
    },
}


# ---------------------------------------------------------------------------
# ImpactPredictor
# ---------------------------------------------------------------------------


class ImpactPredictor:
    """Predicts the expected impact of a refactoring on quality metrics.

    The predictor uses a heuristic rules table to estimate how applying a
    given refactoring technique to a specific code smell will change
    metrics such as cyclomatic complexity, coupling, cohesion,
    maintainability, and risk.

    Args:
        rules: Optional custom prediction rules table.  Defaults to
               :data:`DEFAULT_PREDICTION_RULES`.
    """

    def __init__(
        self,
        rules: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> None:
        self.rules = rules if rules is not None else DEFAULT_PREDICTION_RULES

    # ----- Core prediction method -----

    def predict(
        self,
        smell: CodeSmell,
        candidate: Dict[str, Any],
    ) -> ImpactPrediction:
        """Predict the quality-metric impact of applying *candidate* to *smell*.

        The prediction combines the smell's current metrics with the
        heuristic deltas defined in the rules table.  If no rule exists
        for the candidate's refactoring name, conservative defaults are
        used.

        Args:
            smell: The code smell being addressed.
            candidate: Candidate refactoring dictionary (must have a ``name`` key).

        Returns:
            An :class:`ImpactPrediction` instance with estimated metrics.
        """
        refactoring_name: str = candidate.get("name", "Unknown")
        rule = self.rules.get(refactoring_name, self._default_rule())

        # --- Cyclomatic complexity prediction ---
        # Use the smell's current complexity if available; otherwise assume 10.
        current_complexity = float(
            smell.metrics.get("cyclomatic_complexity", 10)
        )
        reduction_pct = rule["complexity_reduction_pct"]
        predicted_complexity = round(
            current_complexity * (1 - reduction_pct), 1
        )

        # --- Coupling change ---
        coupling_change = rule["coupling_delta"]

        # --- Cohesion change ---
        cohesion_change = rule["cohesion_delta"]

        # --- Maintainability improvement ---
        maintainability = rule["maintainability_gain"]

        # --- Risk score ---
        # Base risk adjusted by smell severity (higher severity → slightly
        # more risk because the surrounding code is more complex/fragile).
        severity_risk_bonus = {
            "low": 0.0,
            "medium": 0.05,
            "high": 0.10,
            "critical": 0.15,
        }
        risk = min(
            1.0,
            rule["base_risk"]
            + severity_risk_bonus.get(smell.severity, 0.05),
        )

        prediction = ImpactPrediction(
            refactoring=refactoring_name,
            smell_id=smell.id,
            predicted_complexity_after=predicted_complexity,
            coupling_change=coupling_change,
            cohesion_change=cohesion_change,
            maintainability_improvement=round(maintainability, 2),
            risk_score=round(risk, 2),
        )

        logger.debug(
            "Impact prediction for '%s' on smell %s: complexity %.1f → %.1f, "
            "coupling %+.1f, cohesion %+.1f, maint +%.2f, risk %.2f",
            refactoring_name,
            smell.id,
            current_complexity,
            predicted_complexity,
            coupling_change,
            cohesion_change,
            maintainability,
            risk,
        )

        return prediction

    # ----- Batch prediction -----

    def predict_all(
        self,
        smell: CodeSmell,
        candidates: List[Dict[str, Any]],
    ) -> List[ImpactPrediction]:
        """Predict impacts for every candidate in the list.

        Args:
            smell: The code smell being addressed.
            candidates: List of candidate refactoring dictionaries.

        Returns:
            List of :class:`ImpactPrediction` instances (same order as input).
        """
        return [self.predict(smell, c) for c in candidates]

    # ----- Private helpers -----

    @staticmethod
    def _default_rule() -> Dict[str, float]:
        """Conservative fallback rule for unknown refactoring names."""
        return {
            "complexity_reduction_pct": 0.10,
            "coupling_delta": -1.0,
            "cohesion_delta": 1.0,
            "maintainability_gain": 0.10,
            "base_risk": 0.25,
        }
