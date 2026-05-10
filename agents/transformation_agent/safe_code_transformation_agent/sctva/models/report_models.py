"""Safety report and final response models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from .validation_step import ValidationStepResult


@dataclass
class TransformationLogEntry:
    """Audit entry for one action execution."""

    action_index: int
    action_type: str
    replacements_count: int
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_index": self.action_index,
            "action_type": self.action_type,
            "replacements_count": self.replacements_count,
            "warnings": self.warnings,
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
    confidence_score: float
    refactored_code: str
    validation_syntax: ValidationStepResult
    validation_structural: ValidationStepResult
    validation_behavioral: ValidationStepResult
    safety_report: SafetyReport
    validation_invariant: ValidationStepResult | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "language": self.language,
            "success": self.success,
            "rollback_occurred": self.rollback_occurred,
            "confidence_score": round(max(0.0, min(1.0, self.confidence_score)), 4),
            "refactored_code": self.refactored_code,
            "validation": {
                "syntax": self.validation_syntax.to_dict(),
                "structural": self.validation_structural.to_dict(),
                "behavioral": self.validation_behavioral.to_dict(),
                **({"invariant": self.validation_invariant.to_dict()} if self.validation_invariant else {}),
            },
            "safety_report": self.safety_report.to_dict(),
        }
