"""
Data Models for the RDP Agent
==============================

Defines the core data structures used throughout the refactoring pipeline:
- CodeSmell: A single detected code smell from the Code Understanding Agent.
- QualityReport: The complete quality report containing multiple smells.
- RefactoringStep: A single step in the refactoring plan.
- RefactoringPlan: The complete plan consumed by the Safe Transformation Agent.

Each model supports JSON serialization via ``to_dict()`` and ``from_dict()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# CodeSmell
# ---------------------------------------------------------------------------


@dataclass
class CodeSmell:
    """Represents a single code smell detected by the Code Understanding Agent.

    Attributes:
        id: Unique identifier for the smell (e.g., ``smell_001``).
        type: Category of the smell (e.g., ``Long Method``).
        location: Dictionary with ``class``, ``method``, and ``lines`` keys.
        metrics: Dictionary of quantitative metrics (e.g., LOC, complexity).
        severity: One of ``low``, ``medium``, ``high``, ``critical``.
        details: Optional free-text description or additional context.
    """

    id: str
    type: str
    location: Dict[str, Any]
    metrics: Dict[str, Any]
    severity: str
    details: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary."""
        d = asdict(self)
        if d.get("details") is None:
            d.pop("details", None)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CodeSmell":
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with CodeSmell fields.

        Returns:
            A new ``CodeSmell`` instance.
        """
        return cls(
            id=data["id"],
            type=data["type"],
            location=data["location"],
            metrics=data.get("metrics", {}),
            severity=data.get("severity", "medium"),
            details=data.get("details"),
        )


# ---------------------------------------------------------------------------
# QualityReport
# ---------------------------------------------------------------------------


@dataclass
class QualityReport:
    """Report produced by the Code Understanding Agent.

    Attributes:
        target: File or module being analyzed (e.g., ``OrderProcessor.java``).
        smells: List of detected :class:`CodeSmell` instances.
        metrics_summary: Aggregate metrics for the target (e.g., total LOC).
        file_name: Optional file name from the first agent (used as fallback
                   for ``target`` when the report uses ``file_name`` instead).
    """

    target: str
    smells: List[CodeSmell]
    metrics_summary: Dict[str, Any] = field(default_factory=dict)
    file_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary."""
        d: Dict[str, Any] = {
            "target": self.target,
            "smells": [s.to_dict() for s in self.smells],
            "metrics_summary": self.metrics_summary,
        }
        if self.file_name is not None:
            d["file_name"] = self.file_name
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QualityReport":
        """Deserialize from a dictionary.

        The ``target`` field is resolved with a fallback chain:
        ``target`` → ``file_name`` → ``"unknown"``.

        Args:
            data: Dictionary with QualityReport fields.

        Returns:
            A new ``QualityReport`` instance.
        """
        smells = [CodeSmell.from_dict(s) for s in data.get("smells", [])]
        target = data.get("target") or data.get("file_name", "unknown")
        return cls(
            target=target,
            smells=smells,
            metrics_summary=data.get("metrics_summary", {}),
            file_name=data.get("file_name"),
        )


# ---------------------------------------------------------------------------
# RefactoringStep
# ---------------------------------------------------------------------------


@dataclass
class RefactoringStep:
    """A single step in the refactoring plan.

    Attributes:
        step_id: Ordinal position within the plan.
        smell_id: ID of the code smell this step addresses.
        refactoring: Name of the refactoring technique.
        target: Dictionary identifying the target (class, method, etc.).
        parameters: Additional parameters for the transformation agent.
        explanation: Human-readable rationale for this step.
    """

    step_id: int
    smell_id: str
    refactoring: str
    target: Dict[str, Any]
    parameters: Dict[str, Any]
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RefactoringStep":
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with RefactoringStep fields.

        Returns:
            A new ``RefactoringStep`` instance.
        """
        return cls(
            step_id=data["step_id"],
            smell_id=data["smell_id"],
            refactoring=data["refactoring"],
            target=data.get("target", {}),
            parameters=data.get("parameters", {}),
            explanation=data.get("explanation", ""),
        )


# ---------------------------------------------------------------------------
# ImpactPrediction
# ---------------------------------------------------------------------------


@dataclass
class ImpactPrediction:
    """Predicted impact of applying a refactoring technique to a code smell.

    This model captures the estimated changes in software quality metrics
    that the Refactoring Impact Prediction module produces *before* the
    Decision Engine scores the candidates.

    Attributes:
        refactoring: Name of the refactoring technique (e.g., ``Extract Method``).
        smell_id: Identifier of the code smell being addressed.
        predicted_complexity_after: Estimated cyclomatic complexity after refactoring.
        coupling_change: Expected change in coupling (negative = reduction = better).
        cohesion_change: Expected change in cohesion (positive = improvement).
        maintainability_improvement: Estimated improvement in maintainability (0–1).
        risk_score: Predicted risk of introducing defects during refactoring (0–1).
    """

    refactoring: str
    smell_id: str
    predicted_complexity_after: float
    coupling_change: float
    cohesion_change: float
    maintainability_improvement: float
    risk_score: float

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ImpactPrediction":
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with ImpactPrediction fields.

        Returns:
            A new ``ImpactPrediction`` instance.
        """
        return cls(
            refactoring=data["refactoring"],
            smell_id=data["smell_id"],
            predicted_complexity_after=data.get("predicted_complexity_after", 0.0),
            coupling_change=data.get("coupling_change", 0.0),
            cohesion_change=data.get("cohesion_change", 0.0),
            maintainability_improvement=data.get("maintainability_improvement", 0.0),
            risk_score=data.get("risk_score", 0.5),
        )


# ---------------------------------------------------------------------------
# MLPrediction
# ---------------------------------------------------------------------------


@dataclass
class MLPrediction:
    """ML-based prediction for a refactoring candidate.

    Produced by the CodeBERT ML Scorer module as an additional
    signal for the Decision Engine.  The scorer encodes the smell
    context and refactoring candidate description into CodeBERT
    embeddings and derives three scores plus a confidence value.

    Attributes:
        refactoring: Name of the refactoring technique.
        smell_id: Identifier of the code smell being addressed.
        contextual_suitability: How well the refactoring fits the
            code context (0–1, higher is better).
        quality_improvement: Predicted quality gain (0–1).
        behavioral_risk: Predicted regression risk (0–1, lower is safer).
        confidence: Model confidence in its predictions (0–1).
        embedding_norm: L2 norm of the combined embedding (diagnostic).
    """

    refactoring: str
    smell_id: str
    contextual_suitability: float
    quality_improvement: float
    behavioral_risk: float
    confidence: float
    embedding_norm: float

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MLPrediction":
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with MLPrediction fields.

        Returns:
            A new ``MLPrediction`` instance.
        """
        return cls(
            refactoring=data["refactoring"],
            smell_id=data["smell_id"],
            contextual_suitability=data.get("contextual_suitability", 0.5),
            quality_improvement=data.get("quality_improvement", 0.5),
            behavioral_risk=data.get("behavioral_risk", 0.5),
            confidence=data.get("confidence", 0.0),
            embedding_norm=data.get("embedding_norm", 0.0),
        )


# ---------------------------------------------------------------------------
# RefactoringPlan
# ---------------------------------------------------------------------------


@dataclass
class RefactoringPlan:
    """Complete refactoring plan to be consumed by the Safe Transformation Agent.

    Attributes:
        plan_id: Unique identifier for the plan.
        target: File or module being refactored.
        steps: Ordered list of :class:`RefactoringStep` instances.
        summary: Human-readable summary of the entire plan.
    """

    plan_id: str
    target: str
    steps: List[RefactoringStep]
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "plan_id": self.plan_id,
            "target": self.target,
            "steps": [s.to_dict() for s in self.steps],
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RefactoringPlan":
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with RefactoringPlan fields.

        Returns:
            A new ``RefactoringPlan`` instance.
        """
        steps = [RefactoringStep.from_dict(s) for s in data.get("steps", [])]
        return cls(
            plan_id=data["plan_id"],
            target=data["target"],
            steps=steps,
            summary=data.get("summary", ""),
        )
