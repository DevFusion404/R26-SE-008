"""Main orchestrator for Safe Code Transformation and Validation."""

from __future__ import annotations

from typing import Any, Dict

from .contracts import ContractValidationError, SCTVARequestContract
from .models import SCTVAResult
from .reporting.safety_reporter import SafetyReporter
from .rollback.rollback_manager import RollbackManager
from .scoring.confidence_scorer import ConfidenceScorer
from .transformers.engine import TransformationEngine
from .validators.behavioral_validator import BehavioralValidator
from .validators.invariant_miner import InvariantMiner
from .validators.structural_validator import StructuralValidator
from .validators.syntax_validator import SyntaxValidator


class SafeCodeTransformationValidationAgent:
    """Runs transformation + multi-level validation + rollback in one pipeline."""

    def __init__(self) -> None:
        self.transformer = TransformationEngine()
        self.syntax_validator = SyntaxValidator()
        self.structural_validator = StructuralValidator()
        self.behavioral_validator = BehavioralValidator()
        self.invariant_miner = InvariantMiner()
        self.rollback_manager = RollbackManager()
        self.scorer = ConfidenceScorer()
        self.reporter = SafetyReporter()

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute from raw payload and return result dict."""
        request = SCTVARequestContract.from_dict(payload)
        return self.execute_request(request)

    def execute_request(self, request: SCTVARequestContract) -> Dict[str, Any]:
        """Execute validated request contract."""
        transformed_code, transformation_log, transform_warnings = self.transformer.apply_actions(
            language=request.language,
            source_code=request.source_code,
            actions=request.refactoring_plan.actions,
            strict_mode=request.execution_options.strict_mode,
        )

        syntax_step = self.syntax_validator.validate(
            language=request.language,
            source_code=transformed_code,
            require_compilation=request.execution_options.require_compilation,
            timeout_seconds=request.execution_options.timeout_seconds,
        )

        structural_step = self.structural_validator.validate(
            language=request.language,
            original_code=request.source_code,
            transformed_code=transformed_code,
        )

        behavioral_step = self.behavioral_validator.validate(
            language=request.language,
            original_code=request.source_code,
            transformed_code=transformed_code,
            behavior_tests=request.refactoring_plan.behavior_tests,
            enable_behavior_tests=request.execution_options.enable_behavior_tests,
            actions=request.refactoring_plan.actions,
            strict_mode=request.execution_options.strict_mode,
        )

        invariant_step = self.invariant_miner.mine(
            language=request.language,
            behavioral_step=behavioral_step,
            actions=request.refactoring_plan.actions,
            strict_mode=request.execution_options.strict_mode,
        )

        rollback_occurred, rollback_reason = self.rollback_manager.evaluate(
            [syntax_step, structural_step, behavioral_step, invariant_step],
            rollback_on_behavior_failure=request.execution_options.rollback_on_behavior_failure,
        )

        final_code = request.source_code if rollback_occurred else transformed_code

        confidence_score, confidence_details = self.scorer.score(
            syntax=syntax_step,
            structural=structural_step,
            behavioral=behavioral_step,
            invariant=invariant_step,
        )
        if rollback_occurred:
            confidence_score = min(confidence_score, 0.49)

        safety_report = self.reporter.build(
            rollback_occurred=rollback_occurred,
            rollback_reason=rollback_reason,
            transformation_log=transformation_log,
            validation_steps=[syntax_step, structural_step, behavioral_step, invariant_step],
            extra_warnings=transform_warnings,
        )

        safety_report.human_messages.append(
            "Confidence formula: syntax_w*syntax + structural_w*structural + behavioral_w*behavioral"
        )

        success = (
            (not rollback_occurred)
            and syntax_step.passed
            and structural_step.passed
            and behavioral_step.passed
            and invariant_step.passed
        )

        result = SCTVAResult(
            request_id=request.request_id,
            language=request.language,
            success=success,
            rollback_occurred=rollback_occurred,
            confidence_score=confidence_score,
            refactored_code=final_code,
            validation_syntax=syntax_step,
            validation_structural=structural_step,
            validation_behavioral=behavioral_step,
            validation_invariant=invariant_step,
            safety_report=safety_report,
        ).to_dict()

        result["confidence_components"] = confidence_details
        return result


__all__ = ["SafeCodeTransformationValidationAgent", "ContractValidationError"]
