"""Main orchestrator for Safe Code Transformation and Validation."""

from __future__ import annotations

from typing import Any, Dict, List

from .contracts import ContractValidationError, SCTVARequestContract, SourceFileContract
from .contracts import RefactoringAction
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
        file_entries = self._collect_source_files(request)

        file_results: List[Dict[str, Any]] = []
        for file_entry in file_entries:
            actions = self._actions_for_file(
                request.refactoring_plan.actions,
                file_entry.file_name,
            )
            if not actions:
                continue

            file_result = self._execute_single_file(
                request=request,
                file_entry=file_entry,
                actions=actions,
            )
            file_results.append(file_result)

        if not file_results:
            raise ContractValidationError("No source files matched the refactoring plan targets.")

        if len(file_results) == 1:
            return file_results[0]

        language_summary = self._summarize_languages(file_results)
        success = all(result.get("success") for result in file_results)
        rollback_occurred = any(result.get("rollback_occurred") for result in file_results)
        confidence_scores = [
            result.get("confidence_score")
            for result in file_results
            if isinstance(result.get("confidence_score"), (int, float))
        ]
        avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.0

        return {
            "request_id": request.request_id,
            "language": language_summary,
            "success": success,
            "rollback_occurred": rollback_occurred,
            "confidence_score": round(max(0.0, min(1.0, avg_confidence)), 4),
            "file_results": file_results,
        }

    def _collect_source_files(self, request: SCTVARequestContract) -> List[SourceFileContract]:
        if request.source_files:
            return request.source_files
        return [
            SourceFileContract(
                file_name="source_code",
                source_code=request.source_code,
                language=request.language,
            )
        ]

    def _execute_single_file(
        self,
        *,
        request: SCTVARequestContract,
        file_entry: SourceFileContract,
        actions: List[RefactoringAction],
    ) -> Dict[str, Any]:
        language = (file_entry.language or request.language).strip().lower()

        transformed_code, transformation_log, transform_warnings = self.transformer.apply_actions(
            language=language,
            source_code=file_entry.source_code,
            actions=actions,
            strict_mode=request.execution_options.strict_mode,
        )

        syntax_step = self.syntax_validator.validate(
            language=language,
            source_code=transformed_code,
            require_compilation=request.execution_options.require_compilation,
            timeout_seconds=request.execution_options.timeout_seconds,
        )

        structural_step = self.structural_validator.validate(
            language=language,
            original_code=file_entry.source_code,
            transformed_code=transformed_code,
        )

        behavioral_step = self.behavioral_validator.validate(
            language=language,
            original_code=file_entry.source_code,
            transformed_code=transformed_code,
            behavior_tests=request.refactoring_plan.behavior_tests,
            enable_behavior_tests=request.execution_options.enable_behavior_tests,
            actions=actions,
            strict_mode=request.execution_options.strict_mode,
        )

        invariant_step = self.invariant_miner.mine(
            language=language,
            behavioral_step=behavioral_step,
            actions=actions,
            strict_mode=request.execution_options.strict_mode,
        )

        rollback_occurred, rollback_reason = self.rollback_manager.evaluate(
            [syntax_step, structural_step, behavioral_step, invariant_step],
            rollback_on_behavior_failure=request.execution_options.rollback_on_behavior_failure,
        )

        final_code = file_entry.source_code if rollback_occurred else transformed_code

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
            language=language,
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

        result["file_name"] = file_entry.file_name
        result["confidence_components"] = confidence_details
        return result

    @classmethod
    def _actions_for_file(
        cls,
        actions: List[RefactoringAction],
        file_name: str,
    ) -> List[RefactoringAction]:
        scoped_actions = [
            action
            for action in actions
            if cls._action_source_file(action)
        ]
        if not scoped_actions:
            return actions

        return [
            action
            for action in actions
            if cls._file_matches(action_source_file=cls._action_source_file(action), file_name=file_name)
        ]

    @staticmethod
    def _action_source_file(action: RefactoringAction) -> str:
        params = action.parameters or {}
        for key in (
            "source_file",
            "sourceFile",
            "target_file",
            "targetFile",
            "file",
            "file_name",
            "fileName",
            "file_path",
            "filePath",
            "relative_path",
            "relativePath",
        ):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @classmethod
    def _file_matches(cls, *, action_source_file: str, file_name: str) -> bool:
        action_path = cls._normalize_path(action_source_file)
        file_path = cls._normalize_path(file_name)
        if not action_path or not file_path:
            return False

        action_base = action_path.rsplit("/", 1)[-1]
        file_base = file_path.rsplit("/", 1)[-1]
        return (
            action_path == file_path
            or file_path.endswith(f"/{action_path}")
            or action_path.endswith(f"/{file_path}")
            or action_base == file_base
        )

    @staticmethod
    def _normalize_path(value: str) -> str:
        return "/".join(
            part
            for part in str(value).replace("\\", "/").strip().lower().split("/")
            if part and part != "."
        )

    @staticmethod
    def _summarize_languages(file_results: List[Dict[str, Any]]) -> str:
        languages = {
            str(result.get("language", "")).strip().lower()
            for result in file_results
            if result.get("language")
        }
        if not languages:
            return "unknown"
        if len(languages) == 1:
            return next(iter(languages))
        return "mixed"


__all__ = ["SafeCodeTransformationValidationAgent", "ContractValidationError"]
