"""Main orchestrator for Safe Code Transformation and Validation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

if __name__ == "__main__" and not __package__:
    # Allow `python sctva/agent.py` to resolve the package-relative imports below.
    package_parent = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(package_parent))
    __package__ = "sctva"

if __name__ == "__main__":
    sys.modules.setdefault("sctva.agent", sys.modules[__name__])

from .analysis import LocalRefactorDetector
from .contracts import ContractValidationError, SCTVARequestContract, SourceFileContract
from .contracts import RefactoringAction
from .constants import ACTION_NOOP
from .models import SCTVAResult, TransformationLogEntry
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
        self.local_refactor_detector = LocalRefactorDetector()

    def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute from raw payload and return result dict."""
        request = SCTVARequestContract.from_dict(payload)
        return self.execute_request(request)

    def execute_request(self, request: SCTVARequestContract) -> Dict[str, Any]:
        """Execute validated request contract."""
        file_entries = self._collect_source_files(request)

        file_results: List[Dict[str, Any]] = []
        for file_entry in file_entries:
            plan_actions = self._actions_for_file(
                request.refactoring_plan.actions,
                file_entry.file_name,
            )
            local_actions = self._local_actions_for_file(
                request=request,
                file_entry=file_entry,
                existing_actions=plan_actions,
            )
            actions = [*plan_actions, *local_actions]
            if not actions:
                continue

            file_result = self._execute_single_file(
                request=request,
                file_entry=file_entry,
                actions=actions,
                project_files=file_entries,
            )
            file_results.append(file_result)

        if not file_results:
            raise ContractValidationError("No source files matched the refactoring plan targets.")

        if len(file_results) == 1:
            return file_results[0]

        language_summary = self._summarize_languages(file_results)
        success = all(result.get("success") for result in file_results)
        rollback_occurred = any(result.get("rollback_occurred") for result in file_results)
        transformation_applied = any(result.get("transformation_applied", False) for result in file_results)
        files_total = len(file_results)
        files_succeeded = sum(bool(result.get("success")) for result in file_results)
        files_rolled_back = sum(bool(result.get("rollback_occurred")) for result in file_results)
        files_applied = sum(bool(result.get("transformation_applied")) for result in file_results)
        files_not_applied = files_total - files_applied - files_rolled_back
        total_replacements = sum(
            int(result.get("total_replacements", 0) or 0)
            for result in file_results
        )
        confidence_scores = [
            result.get("confidence_score")
            for result in file_results
            if isinstance(result.get("confidence_score"), (int, float))
        ]
        avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else None
        validation_scores = [
            result.get("validation_score")
            for result in file_results
            if isinstance(result.get("validation_score"), (int, float))
        ]
        avg_validation = sum(validation_scores) / len(validation_scores) if validation_scores else None

        return {
            "request_id": request.request_id,
            "language": language_summary,
            "success": success,
            "rollback_occurred": rollback_occurred,
            "transformation_applied": transformation_applied,
            "total_replacements": total_replacements,
            "confidence_score": (
                round(max(0.0, min(1.0, avg_confidence)), 4)
                if isinstance(avg_confidence, (int, float))
                else None
            ),
            "confidence_applicable": bool(confidence_scores),
            "validation_score": (
                round(max(0.0, min(1.0, avg_validation)), 4)
                if isinstance(avg_validation, (int, float))
                else None
            ),
            "file_summary": {
                "total": files_total,
                "succeeded": files_succeeded,
                "applied": files_applied,
                "rolled_back": files_rolled_back,
                "not_applied": max(0, files_not_applied),
            },
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
                source_mode="raw",
            )
        ]

    def _execute_single_file(
        self,
        *,
        request: SCTVARequestContract,
        file_entry: SourceFileContract,
        actions: List[RefactoringAction],
        project_files: List[SourceFileContract],
    ) -> Dict[str, Any]:
        language = (file_entry.language or request.language).strip().lower()

        source_is_reconstructed = file_entry.source_mode != "raw"
        executable_actions = [
            action for action in actions if action.action_type != ACTION_NOOP
        ]
        if source_is_reconstructed and executable_actions:
            transformed_code = file_entry.source_code
            skip_warning = (
                "Action skipped because this file was imported from CUQA as "
                "reconstructed placeholder source. Accurate transformation requires raw source text."
            )
            validation_actions: List[RefactoringAction] = []
            transformation_log = [
                TransformationLogEntry(
                    action_index=index,
                    action_type=action.action_type,
                    replacements_count=0,
                    warnings=[*action.warnings, skip_warning],
                )
                for index, action in enumerate(actions, start=1)
            ]
            transform_warnings = [skip_warning]
        else:
            transformed_code, transformation_log, transform_warnings = self.transformer.apply_actions(
                language=language,
                source_code=file_entry.source_code,
                actions=actions,
                strict_mode=request.execution_options.strict_mode,
            )
            validation_actions = self._actions_with_effective_replacements(
                actions,
                transformation_log,
            )

        transformation_attempted = transformed_code != file_entry.source_code
        total_replacements = sum(
            entry.replacements_count for entry in transformation_log
        )
        if not transformation_attempted:
            if executable_actions:
                transform_warnings.append(
                    "No source-code change was applied: every executable action produced zero replacements."
                )
            else:
                transform_warnings.append(
                    "No source-code change was applied: the plan contained only noop actions."
                )
            if file_entry.source_mode != "raw":
                transform_warnings.append(
                    "This file was imported from CUQA as reconstructed placeholder source, "
                    "not raw file text. SCTVA preserved it because RDP line/literal actions "
                    "cannot be applied accurately without the original source code."
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
            actions=validation_actions,
            strict_mode=request.execution_options.strict_mode,
            project_source_files=project_files,
            current_file_name=file_entry.file_name,
        )

        invariant_step = self.invariant_miner.mine(
            language=language,
            behavioral_step=behavioral_step,
            actions=validation_actions,
            strict_mode=request.execution_options.strict_mode,
        )

        rollback_occurred, rollback_reason = self.rollback_manager.evaluate(
            [syntax_step, structural_step, behavioral_step, invariant_step],
            rollback_on_behavior_failure=request.execution_options.rollback_on_behavior_failure,
        )

        final_code = file_entry.source_code if rollback_occurred else transformed_code
        transformation_applied = final_code != file_entry.source_code

        validation_score, confidence_details = self.scorer.score(
            syntax=syntax_step,
            structural=structural_step,
            behavioral=behavioral_step,
            invariant=invariant_step,
        )
        confidence_score = validation_score
        if rollback_occurred:
            confidence_score = min(confidence_score, 0.49)
        confidence_applicable = transformation_applied or rollback_occurred
        if not confidence_applicable:
            confidence_score = None

        safety_report = self.reporter.build(
            rollback_occurred=rollback_occurred,
            rollback_reason=rollback_reason,
            transformation_log=transformation_log,
            validation_steps=[syntax_step, structural_step, behavioral_step, invariant_step],
            extra_warnings=transform_warnings,
            transformation_applied=transformation_applied,
        )

        safety_report.human_messages.append(
            "Confidence formula: syntax_w*syntax + structural_w*structural + behavioral_w*behavioral"
        )

        success = (
            transformation_applied
            and
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
            transformation_applied=transformation_applied,
            total_replacements=total_replacements,
            confidence_applicable=confidence_applicable,
            validation_score=validation_score,
        ).to_dict()

        result["file_name"] = file_entry.file_name
        result["source_mode"] = file_entry.source_mode
        result["origin"] = file_entry.origin
        result["confidence_components"] = confidence_details
        return result

    def _local_actions_for_file(
        self,
        *,
        request: SCTVARequestContract,
        file_entry: SourceFileContract,
        existing_actions: List[RefactoringAction],
    ) -> List[RefactoringAction]:
        if not request.execution_options.enable_sctva_auto_refactoring:
            return []
        if file_entry.source_mode != "raw":
            return []
        if not self._request_allows_local_refactoring(request):
            return []

        language = (file_entry.language or request.language).strip().lower()
        return self.local_refactor_detector.detect(
            language=language,
            file_name=file_entry.file_name,
            source_code=file_entry.source_code,
            existing_actions=existing_actions,
        )

    @staticmethod
    def _request_allows_local_refactoring(request: SCTVARequestContract) -> bool:
        metadata = request.refactoring_plan.metadata or {}
        if request.source_files:
            return True
        if metadata.get("enable_sctva_auto_refactoring") is True:
            return True
        return str(metadata.get("source_agent") or "").strip().lower() == "rdp_agent"

    @staticmethod
    def _actions_with_effective_replacements(
        actions: List[RefactoringAction],
        transformation_log: List[TransformationLogEntry],
    ) -> List[RefactoringAction]:
        effective_actions: List[RefactoringAction] = []
        for action, log_entry in zip(actions, transformation_log):
            if action.action_type == ACTION_NOOP:
                continue
            if log_entry.replacements_count > 0:
                effective_actions.append(action)
        return effective_actions

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


def _run_api_server() -> None:
    """Start the SCTVA Flask API when this module is run as a script."""
    app_dir = Path(__file__).resolve().parents[1]
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))

    from app import create_app

    port = int(os.getenv("SCTVA_PORT", "8002"))
    app = create_app()
    print(f"Starting SCTVA API server on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    _run_api_server()
