"""Safety report and final response models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .validation_step import ValidationStepResult


@dataclass
class TransformationLogEntry:
    """Audit entry for one action execution."""

    action_index: int
    action_type: str
    replacements_count: int
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_index": self.action_index,
            "action_type": self.action_type,
            "replacements_count": self.replacements_count,
            "warnings": self.warnings,
            "metadata": self.metadata,
        }


@dataclass
class SafetyReport:
    """Detailed report for traceability and rollback transparency."""

    summary: str
    rollback_reason: str
    transformation_log: List[TransformationLogEntry] = field(default_factory=list)
    validation_timestamps: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    risk_flags: List[str] = field(default_factory=list)
    human_messages: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "rollback_reason": self.rollback_reason,
            "transformation_log": [entry.to_dict() for entry in self.transformation_log],
            "validation_timestamps": self.validation_timestamps,
            "risk_flags": self.risk_flags,
            "human_messages": self.human_messages,
        }


@dataclass
class SCTVAResult:
    """Top-level execution result."""

    request_id: str
    language: str
    success: bool
    rollback_occurred: bool
    confidence_score: Optional[float]
    refactored_code: str
    validation_syntax: ValidationStepResult
    validation_structural: ValidationStepResult
    validation_behavioral: ValidationStepResult
    safety_report: SafetyReport
    validation_invariant: ValidationStepResult | None = None
    transformation_applied: bool = True
    total_replacements: int = 0
    confidence_applicable: bool = True
    validation_score: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "language": self.language,
            "success": self.success,
            "rollback_occurred": self.rollback_occurred,
            "confidence_score": (
                round(max(0.0, min(1.0, self.confidence_score)), 4)
                if isinstance(self.confidence_score, (int, float))
                else None
            ),
            "confidence_applicable": self.confidence_applicable,
            "validation_score": (
                round(max(0.0, min(1.0, self.validation_score)), 4)
                if isinstance(self.validation_score, (int, float))
                else None
            ),
            "refactored_code": self.refactored_code,
            "transformation_applied": self.transformation_applied,
            "total_replacements": self.total_replacements,
            "validation": {
                "syntax": self.validation_syntax.to_dict(),
                "structural": self.validation_structural.to_dict(),
                "behavioral": self.validation_behavioral.to_dict(),
                **({"invariant": self.validation_invariant.to_dict()} if self.validation_invariant else {}),
            },
            "safety_report": self.safety_report.to_dict(),
        }
