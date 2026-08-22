"""Main orchestrator for Safe Code Transformation and Validation."""

from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from .constants import (
    ACTION_EXTRACT_CLASS,
    ACTION_EXTRACT_METHOD,
    ACTION_NARROW_EXCEPTION_HANDLER,
    ACTION_NOOP,
    ACTION_REMOVE_DEAD_CODE,
    EXTRACT_CLASS_ACTIONS,
    EXTRACT_CLASS_ACTION_BY_LANGUAGE,
    PARAMETER_OBJECT_ACTIONS,
    PARAMETER_OBJECT_ACTION_BY_LANGUAGE,
)
from .models import SCTVAResult, TransformationLogEntry
from .reporting.safety_reporter import SafetyReporter
from .rollback.rollback_manager import RollbackManager
from .scoring.confidence_scorer import ConfidenceScorer
from .transformers.engine import TransformationEngine
from .transformers.c_extract_method import target_match_count as c_method_target_count
from .transformers.java_extract_class import _parse_java_class, declared_class_names
from .transformers.java_extract_method import target_match_count as java_method_target_count
from .transformers.python_extract_method import target_match_count as python_method_target_count
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
        self._resolve_extract_method_source_files(
            request.refactoring_plan.actions,
            file_entries,
        )
        self._resolve_extract_class_source_files(
            request.refactoring_plan.actions,
            file_entries,
        )
        self._resolve_parameter_object_source_files(
            request.refactoring_plan.actions,
            file_entries,
        )
        action_scope = self._build_action_scope_index(request.refactoring_plan.actions)
        project_file_payloads = [item.to_dict() for item in file_entries]

        def run_file(index: int, file_entry: SourceFileContract) -> tuple[int, Dict[str, Any] | None]:
            plan_actions = self._actions_for_file_from_scope(
                request.refactoring_plan.actions,
                action_scope,
                file_entry.file_name,
            )
            local_actions = self._local_actions_for_file(
                request=request,
                file_entry=file_entry,
                existing_actions=plan_actions,
            )
            actions = [*plan_actions, *local_actions]
            if not actions:
                return index, None

            file_result = self._execute_single_file(
                request=request,
                file_entry=file_entry,
                actions=actions,
                project_files=project_file_payloads,
            )
            return index, file_result

        indexed_results: Dict[int, Dict[str, Any]] = {}
        max_workers = self._parallel_file_workers(request, len(file_entries))

        if max_workers <= 1:
            for index, file_entry in enumerate(file_entries):
                result_index, file_result = run_file(index, file_entry)
                if file_result is not None:
                    indexed_results[result_index] = file_result
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(run_file, index, file_entry): index
                    for index, file_entry in enumerate(file_entries)
                }
                for future in as_completed(futures):
                    result_index, file_result = future.result()
                    if file_result is not None:
                        indexed_results[result_index] = file_result

        file_results = [
            indexed_results[index]
            for index in sorted(indexed_results)
        ]

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
        project_files: List[Dict[str, Any]],
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
                project_source_files=project_files,
                current_file_name=file_entry.file_name,
                repository_complete=bool(request.source_files),
                behavior_tests=request.refactoring_plan.behavior_tests,
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
            actions=validation_actions,
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

        self._finalize_extract_class_logs(
            transformation_log,
            syntax_passed=syntax_step.passed,
            structural_passed=structural_step.passed,
            behavioral_passed=behavioral_step.passed,
            invariant_passed=invariant_step.passed,
            rollback_occurred=rollback_occurred,
        )
        self._finalize_extract_method_logs(
            transformation_log,
            syntax_passed=syntax_step.passed,
            structural_passed=structural_step.passed,
            behavioral_passed=behavioral_step.passed,
            invariant_passed=invariant_step.passed,
            rollback_occurred=rollback_occurred,
        )
        self._finalize_parameter_object_logs(
            transformation_log,
            syntax_passed=syntax_step.passed,
            structural_passed=structural_step.passed,
            behavioral_passed=behavioral_step.passed,
            invariant_passed=invariant_step.passed,
            rollback_occurred=rollback_occurred,
        )
        self._finalize_remove_dead_code_logs(
            transformation_log,
            syntax_passed=syntax_step.passed,
            structural_passed=structural_step.passed,
            behavioral_passed=behavioral_step.passed,
            invariant_passed=invariant_step.passed,
            rollback_occurred=rollback_occurred,
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

    @classmethod
    def _resolve_extract_method_source_files(
        cls,
        actions: List[RefactoringAction],
        file_entries: List[SourceFileContract],
    ) -> None:
        """Resolve Extract Method by semantic routine identity, not stale lines."""

        for action in actions:
            if action.action_type != ACTION_EXTRACT_METHOD:
                continue
            params = action.parameters or {}
            method_name = str(
                params.get("method")
                or params.get("method_name")
                or params.get("function")
                or params.get("function_name")
                or ""
            ).strip()
            source_class = str(
                params.get("source_class")
                or params.get("target_class")
                or params.get("class_name")
                or params.get("module_name")
                or ""
            ).strip()
            signature = str(
                params.get("method_signature")
                or params.get("function_signature")
                or params.get("signature")
                or ""
            ).strip()
            configured_file = cls._action_source_file(action)
            candidates = [
                entry
                for entry in file_entries
                if not configured_file
                or cls._file_matches(
                    action_source_file=configured_file,
                    file_name=entry.file_name,
                )
            ]
            if configured_file and len(candidates) != 1:
                params["source_resolution_error"] = "SOURCE_FILE_TARGET_MISMATCH"
                continue

            semantic_matches: list[SourceFileContract] = []
            ambiguous_within_file = False
            for entry in candidates:
                count = cls._extract_method_target_count(
                    entry,
                    method_name=method_name,
                    source_class=source_class,
                    signature=signature,
                )
                if count == 1:
                    semantic_matches.append(entry)
                elif count > 1:
                    ambiguous_within_file = True

            if len(semantic_matches) == 1 and not ambiguous_within_file:
                params["source_file"] = semantic_matches[0].file_name
                params.pop("source_resolution_error", None)
            elif ambiguous_within_file or len(semantic_matches) > 1:
                params["source_resolution_error"] = "AMBIGUOUS_METHOD_TARGET"
            else:
                params["source_resolution_error"] = "METHOD_TARGET_NOT_FOUND"
                if len(candidates) == 1:
                    params["source_file"] = candidates[0].file_name

    @staticmethod
    def _extract_method_target_count(
        file_entry: SourceFileContract,
        *,
        method_name: str,
        source_class: str,
        signature: str,
    ) -> int:
        if not method_name:
            return 0
        language = (file_entry.language or "").strip().lower()
        lower_name = file_entry.file_name.lower()
        kwargs = {
            "method_name": method_name,
            "source_class": source_class,
            "method_signature": signature,
        }
        if language == "python" or lower_name.endswith(".py"):
            return python_method_target_count(file_entry.source_code, **kwargs)
        if language == "java" or lower_name.endswith(".java"):
            return java_method_target_count(file_entry.source_code, **kwargs)
        if language == "c" or lower_name.endswith((".c", ".h")):
            return c_method_target_count(file_entry.source_code, **kwargs)
        return 0

    @classmethod
    def _resolve_extract_class_source_files(
        cls,
        actions: List[RefactoringAction],
        file_entries: List[SourceFileContract],
    ) -> None:
        """Resolve Extract Class actions to Python, Java, or C source files."""

        class_files: List[tuple[SourceFileContract, set[str]]] = []
        c_files: List[SourceFileContract] = []
        for file_entry in file_entries:
            language = (file_entry.language or "").strip().lower()
            lower_name = file_entry.file_name.lower()
            if language == "c" or lower_name.endswith((".c", ".h")):
                c_files.append(file_entry)
                continue
            if language == "java" or lower_name.endswith(".java"):
                class_files.append((file_entry, declared_class_names(file_entry.source_code)))
                continue
            if language == "python" or lower_name.endswith(".py"):
                try:
                    import ast

                    tree = ast.parse(file_entry.source_code)
                except SyntaxError:
                    continue
                classes = {
                    node.name
                    for node in tree.body
                    if isinstance(node, ast.ClassDef)
                }
                class_files.append((file_entry, classes))

        for action in actions:
            if action.action_type not in EXTRACT_CLASS_ACTIONS:
                continue
            params = action.parameters or {}
            source_class = str(params.get("source_class") or "").strip()
            configured_file = cls._action_source_file(action)
            configured_matches = [
                file_entry for file_entry in file_entries
                if configured_file and cls._file_matches(
                    action_source_file=configured_file,
                    file_name=file_entry.file_name,
                )
            ]
            if len(configured_matches) == 1:
                configured_entry = configured_matches[0]
                language = (configured_entry.language or "").strip().lower()
                lower_name = configured_entry.file_name.lower()
                if language == "c" or lower_name.endswith((".c", ".h")):
                    if not cls._specialize_extract_class_action(action, configured_entry):
                        params["source_resolution_error"] = "SOURCE_FILE_LANGUAGE_MISMATCH"
                        continue
                    params["source_file"] = configured_entry.file_name
                    if not source_class:
                        params["source_class"] = cls._file_stem(configured_entry.file_name)
                    params.pop("source_resolution_error", None)
                    continue
                classes = next(
                    (
                        names for entry, names in class_files
                        if entry.file_name == configured_entry.file_name
                    ),
                    set(),
                )
                resolved_class = cls._resolve_extract_class_name_in_file(
                    configured_entry,
                    classes=classes,
                    requested_class=source_class,
                    parameters=params,
                )
                if not resolved_class:
                    params["source_resolution_error"] = "SOURCE_FILE_CLASS_MISMATCH"
                    continue
                if not cls._specialize_extract_class_action(action, configured_entry):
                    params["source_resolution_error"] = "SOURCE_FILE_LANGUAGE_MISMATCH"
                    continue
                cls._apply_resolved_extract_class_name(
                    params,
                    resolved_class=resolved_class,
                    requested_class=source_class,
                )
                params["source_file"] = configured_entry.file_name
                params.pop("source_resolution_error", None)
                continue

            if configured_file:
                params["source_resolution_error"] = "SOURCE_FILE_CLASS_MISMATCH"
                continue

            if not source_class:
                if len(c_files) == 1:
                    if not cls._specialize_extract_class_action(action, c_files[0]):
                        params["source_resolution_error"] = "SOURCE_FILE_LANGUAGE_MISMATCH"
                        continue
                    params["source_file"] = c_files[0].file_name
                    params["source_class"] = cls._file_stem(c_files[0].file_name)
                    params.pop("source_resolution_error", None)
                else:
                    params["source_resolution_error"] = "SOURCE_CLASS_NOT_FOUND"
                continue

            class_matches = [
                file_entry
                for file_entry, classes in class_files
                if source_class in classes
            ]
            c_matches = [
                file_entry for file_entry in c_files
                if cls._file_stem(file_entry.file_name).lower() == source_class.lower()
            ]
            matches = [*class_matches, *c_matches]
            if len(matches) == 1:
                if not cls._specialize_extract_class_action(action, matches[0]):
                    params["source_resolution_error"] = "SOURCE_FILE_LANGUAGE_MISMATCH"
                    continue
                params["source_file"] = matches[0].file_name
                params.pop("source_resolution_error", None)
            elif not matches:
                params["source_resolution_error"] = "SOURCE_CLASS_NOT_FOUND"
            else:
                params["source_resolution_error"] = "AMBIGUOUS_SOURCE_CLASS"

    @classmethod
    def _resolve_parameter_object_source_files(
        cls,
        actions: List[RefactoringAction],
        file_entries: List[SourceFileContract],
    ) -> None:
        """Resolve filename-derived class targets through parsed method ownership."""

        for action in actions:
            if action.action_type not in PARAMETER_OBJECT_ACTIONS:
                continue
            params = action.parameters or {}
            method = str(
                params.get("method")
                or params.get("method_name")
                or params.get("function")
                or params.get("function_name")
                or ""
            ).strip()
            configured_file = cls._action_source_file(action)
            candidates = [
                entry for entry in file_entries
                if not configured_file
                or cls._file_matches(
                    action_source_file=configured_file,
                    file_name=entry.file_name,
                )
            ]
            if configured_file and len(candidates) != 1:
                params["source_resolution_error"] = "SOURCE_FILE_TARGET_MISMATCH"
                continue

            matches: list[tuple[SourceFileContract, str]] = []
            for entry in candidates:
                language = (entry.language or "").strip().lower()
                lower_name = entry.file_name.lower()
                if language == "java" or lower_name.endswith(".java"):
                    owners = cls._java_parameter_object_method_owners(entry.source_code, method)
                    matches.extend((entry, owner) for owner in owners)
                elif language == "python" or lower_name.endswith(".py"):
                    owners = cls._python_parameter_object_method_owners(entry.source_code, method)
                    matches.extend((entry, owner) for owner in owners)

            requested_class = str(params.get("source_class") or "").strip()
            exact = [item for item in matches if item[1] == requested_class]
            resolved: tuple[SourceFileContract, str] | None = None
            if len(exact) == 1:
                resolved = exact[0]
            else:
                inferred_class = (
                    not requested_class
                    or str(params.get("source_class_origin") or "").lower() == "file_stem_fallback"
                    or (
                        len(candidates) == 1
                        and requested_class.lower() == cls._file_stem(candidates[0].file_name).lower()
                    )
                )
                if inferred_class and len(matches) == 1:
                    resolved = matches[0]

            if resolved is None:
                params["source_resolution_error"] = (
                    "PARAMETER_OBJECT_TARGET_NOT_FOUND"
                    if not matches
                    else "AMBIGUOUS_PARAMETER_OBJECT_TARGET"
                )
                if len(candidates) == 1:
                    params["source_file"] = candidates[0].file_name
                continue

            entry, owner = resolved
            language = (entry.language or "").strip().lower()
            if not language:
                language = "java" if entry.file_name.lower().endswith(".java") else "python"
            specialized = PARAMETER_OBJECT_ACTION_BY_LANGUAGE.get(language)
            if not specialized:
                params["source_resolution_error"] = "SOURCE_FILE_LANGUAGE_MISMATCH"
                continue
            if action.action_type not in {"introduce_parameter_object", specialized}:
                params["source_resolution_error"] = "SOURCE_FILE_LANGUAGE_MISMATCH"
                continue
            action.action_type = specialized
            params["source_file"] = entry.file_name
            if requested_class != owner:
                params["requested_source_class"] = requested_class
                params["source_class_resolution"] = "parsed_unique_method_owner"
            params["source_class"] = owner
            params.pop("source_resolution_error", None)

    @staticmethod
    def _java_parameter_object_method_owners(source: str, method: str) -> list[str]:
        if not method:
            return []
        owners: list[str] = []
        for class_name in declared_class_names(source):
            model = _parse_java_class(source, class_name)
            if model and any(item.name == method and not item.is_constructor for item in model.methods):
                owners.append(class_name)
        return sorted(set(owners))

    @staticmethod
    def _python_parameter_object_method_owners(source: str, method: str) -> list[str]:
        if not method:
            return []
        try:
            import ast

            tree = ast.parse(source)
        except SyntaxError:
            return []
        owners: list[str] = []
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method
            for node in tree.body
        ):
            owners.append("")
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if any(
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method
                for child in node.body
            ):
                owners.append(node.name)
        return sorted(set(owners))

    @staticmethod
    def _specialize_extract_class_action(
        action: RefactoringAction,
        file_entry: SourceFileContract,
    ) -> bool:
        """Bind a legacy Extract Class intent to exactly one language operation."""

        language = (file_entry.language or "").strip().lower()
        lower_name = file_entry.file_name.lower()
        if language not in EXTRACT_CLASS_ACTION_BY_LANGUAGE:
            if lower_name.endswith(".py"):
                language = "python"
            elif lower_name.endswith(".java"):
                language = "java"
            elif lower_name.endswith((".c", ".h")):
                language = "c"

        specialized_action = EXTRACT_CLASS_ACTION_BY_LANGUAGE.get(language)
        if not specialized_action:
            return False
        if action.action_type != ACTION_EXTRACT_CLASS and action.action_type != specialized_action:
            return False

        action.action_type = specialized_action
        action.parameters["extract_class_language"] = language
        action.parameters["legacy_action_type"] = ACTION_EXTRACT_CLASS
        return True

    @classmethod
    def _resolve_extract_class_name_in_file(
        cls,
        file_entry: SourceFileContract,
        *,
        classes: set[str],
        requested_class: str,
        parameters: Dict[str, Any],
    ) -> str:
        if requested_class in classes:
            return requested_class
        if not classes:
            return ""

        source_class_origin = str(parameters.get("source_class_origin") or "").strip().lower()
        inferred = source_class_origin == "file_stem_fallback"
        requested_file_stem = cls._file_stem(requested_class).lower()
        actual_file_stem = cls._file_stem(file_entry.file_name).lower()
        inferred_from_configured_file = bool(
            not source_class_origin
            and requested_file_stem
            and requested_file_stem == actual_file_stem
        )
        if (inferred or inferred_from_configured_file) and len(classes) == 1:
            return next(iter(classes))
        if not requested_class and len(classes) == 1:
            return next(iter(classes))

        requested_methods = parameters.get("methods_to_extract") or []
        requested_fields = parameters.get("fields_to_extract") or []
        if not isinstance(requested_methods, list):
            requested_methods = []
        if not isinstance(requested_fields, list):
            requested_fields = []
        if not requested_methods and not requested_fields:
            return ""

        member_matches = [
            class_name
            for class_name in classes
            if cls._extract_class_members_match(
                file_entry,
                class_name=class_name,
                requested_methods={str(item).strip() for item in requested_methods if str(item).strip()},
                requested_fields={str(item).strip() for item in requested_fields if str(item).strip()},
            )
        ]
        return member_matches[0] if len(member_matches) == 1 else ""

    @staticmethod
    def _extract_class_members_match(
        file_entry: SourceFileContract,
        *,
        class_name: str,
        requested_methods: set[str],
        requested_fields: set[str],
    ) -> bool:
        language = (file_entry.language or "").strip().lower()
        lower_name = file_entry.file_name.lower()
        if language == "java" or lower_name.endswith(".java"):
            model = _parse_java_class(file_entry.source_code, class_name)
            return bool(
                model
                and requested_methods <= set(model.methods_by_name)
                and requested_fields <= set(model.fields)
            )
        if language == "python" or lower_name.endswith(".py"):
            try:
                import ast

                tree = ast.parse(file_entry.source_code)
            except SyntaxError:
                return False
            source_node = next(
                (
                    node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == class_name
                ),
                None,
            )
            if source_node is None:
                return False
            methods = {
                node.name
                for node in source_node.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            fields = {
                node.attr
                for node in ast.walk(source_node)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in {"self", "cls"}
            }
            fields.update(
                target.id
                for node in source_node.body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if isinstance(target, ast.Name)
            )
            return requested_methods <= methods and requested_fields <= fields
        return False

    @staticmethod
    def _apply_resolved_extract_class_name(
        parameters: Dict[str, Any],
        *,
        resolved_class: str,
        requested_class: str,
    ) -> None:
        if requested_class and requested_class != resolved_class:
            parameters["requested_source_class"] = requested_class
            parameters["source_class_resolution"] = "parsed_class_and_member_identity"
        elif not requested_class:
            parameters["source_class_resolution"] = "single_top_level_class"
        parameters["source_class"] = resolved_class
        if str(parameters.get("new_class_name_origin") or "").strip().lower() == "generated":
            parameters["new_class_name"] = f"{resolved_class}Helper"

    @staticmethod
    def _file_stem(file_name: str) -> str:
        normalized = str(file_name or "").replace("\\", "/").rsplit("/", 1)[-1]
        return normalized.rsplit(".", 1)[0]

    @staticmethod
    def _finalize_extract_class_logs(
        transformation_log: List[TransformationLogEntry],
        *,
        syntax_passed: bool,
        structural_passed: bool,
        behavioral_passed: bool,
        invariant_passed: bool,
        rollback_occurred: bool,
    ) -> None:
        for entry in transformation_log:
            if entry.action_type not in EXTRACT_CLASS_ACTIONS:
                continue
            metadata = entry.metadata
            internal = metadata.get("validation") or {}
            checks = {
                "plan_compliance": "PASS" if metadata.get("plan_compliance") == "PASS" else "FAIL",
                "structural_refactoring": (
                    "PASS"
                    if syntax_passed
                    and structural_passed
                    and internal.get("structural") == "PASS"
                    and internal.get("dependency") == "PASS"
                    else "FAIL"
                ),
                "behavior_preservation": "PASS" if behavioral_passed else "FAIL",
                "full_api_preservation": SafeCodeTransformationValidationAgent._compatibility_check(
                    internal.get("full_api_preservation")
                ),
                "state_compatibility": SafeCodeTransformationValidationAgent._compatibility_check(
                    internal.get("state_compatibility")
                ),
                "single_state_owner": SafeCodeTransformationValidationAgent._compatibility_check(
                    internal.get("single_state_owner")
                ),
                "large_class_reduction": (
                    "PASS" if internal.get("large_class_reduction") == "PASS" else "FAIL"
                ),
                "invariant_preservation": "PASS" if invariant_passed else "FAIL",
            }
            metadata["final_checks"] = checks
            metadata["behavioral_safety"] = checks["behavior_preservation"]
            if rollback_occurred:
                metadata["status"] = "rolled_back"
                metadata["final_decision"] = "ROLLBACK"
            elif entry.replacements_count <= 0 or "FAIL" in checks.values():
                metadata["status"] = "review_required"
                metadata["final_decision"] = "REVIEW_REQUIRED"
            else:
                metadata["status"] = "pass"
                metadata["final_decision"] = "PASS"

    @staticmethod
    def _finalize_extract_method_logs(
        transformation_log: List[TransformationLogEntry],
        *,
        syntax_passed: bool,
        structural_passed: bool,
        behavioral_passed: bool,
        invariant_passed: bool,
        rollback_occurred: bool,
    ) -> None:
        for entry in transformation_log:
            if entry.action_type != ACTION_EXTRACT_METHOD:
                continue
            metadata = entry.metadata
            internal = metadata.get("validation") or {}
            checks = {
                "plan_compliance": "PASS" if metadata.get("plan_compliance") == "PASS" else "FAIL",
                "extract_method_structural_validation": (
                    "PASS"
                    if structural_passed
                    and internal.get("target_resolution") == "PASS"
                    and internal.get("data_flow") == "PASS"
                    and internal.get("structural") == "PASS"
                    else "FAIL"
                ),
                "long_method_reduction": (
                    "PASS" if internal.get("long_method_reduction") == "PASS" else "FAIL"
                ),
                "behavior_preservation": "PASS" if behavioral_passed else "FAIL",
                "compilation_syntax_validation": "PASS" if syntax_passed else "FAIL",
                "invariant_preservation": "PASS" if invariant_passed else "FAIL",
                "no_severe_new_smell": (
                    "PASS" if internal.get("no_severe_new_smell") == "PASS" else "FAIL"
                ),
            }
            metadata["final_checks"] = checks
            metadata["syntax"] = checks["compilation_syntax_validation"]
            metadata["behavior"] = checks["behavior_preservation"]
            metadata["smell_reduction"] = checks["long_method_reduction"]
            if rollback_occurred:
                metadata["status"] = "rolled_back"
                metadata["final_status"] = "ROLLED_BACK"
                metadata["final_decision"] = "ROLLBACK"
            elif entry.replacements_count <= 0 or "FAIL" in checks.values():
                metadata["status"] = "review_required"
                metadata["final_status"] = "REVIEW_REQUIRED"
                metadata["final_decision"] = "REVIEW_REQUIRED"
            else:
                metadata["status"] = "pass"
                metadata["final_status"] = "PASS"
                metadata["final_decision"] = "PASS"

    @staticmethod
    def _finalize_parameter_object_logs(
        transformation_log: List[TransformationLogEntry],
        *,
        syntax_passed: bool,
        structural_passed: bool,
        behavioral_passed: bool,
        invariant_passed: bool,
        rollback_occurred: bool,
    ) -> None:
        for entry in transformation_log:
            if entry.action_type not in PARAMETER_OBJECT_ACTIONS:
                continue
            internal = entry.metadata.get("validation") or {}
            checks = {
                "plan_compliance": "PASS" if entry.metadata.get("plan_compliance") == "PASS" else "FAIL",
                "parameter_object_created": internal.get("parameter_object", "FAIL"),
                "parameter_count_reduced": internal.get("signature_reduction", "FAIL"),
                "body_access_migrated": internal.get("body_access", "FAIL"),
                "call_sites_updated": internal.get("call_sites", "FAIL"),
                "syntax_validation": "PASS" if syntax_passed else "FAIL",
                "structural_validation": "PASS" if structural_passed else "FAIL",
                "behavior_preservation": "PASS" if behavioral_passed else "FAIL",
                "invariant_preservation": "PASS" if invariant_passed else "FAIL",
            }
            entry.metadata["final_checks"] = checks
            if rollback_occurred:
                entry.metadata["status"] = "rolled_back"
                entry.metadata["final_decision"] = "ROLLBACK"
            elif entry.replacements_count <= 0 or "FAIL" in checks.values():
                entry.metadata["status"] = "review_required"
                entry.metadata["final_decision"] = "REVIEW_REQUIRED"
            else:
                entry.metadata["status"] = "pass"
                entry.metadata["final_decision"] = "PASS"

    @staticmethod
    def _finalize_remove_dead_code_logs(
        transformation_log: List[TransformationLogEntry],
        *,
        syntax_passed: bool,
        structural_passed: bool,
        behavioral_passed: bool,
        invariant_passed: bool,
        rollback_occurred: bool,
    ) -> None:
        """Make skipped or unsafe dead-code actions explicit in the report."""

        for entry in transformation_log:
            if entry.action_type != ACTION_REMOVE_DEAD_CODE:
                continue
            if rollback_occurred:
                entry.metadata["status"] = "rolled_back"
                entry.metadata["final_decision"] = "ROLLBACK"
                continue

            checks = {
                "target_removed": entry.replacements_count > 0,
                "syntax_validation": syntax_passed,
                "structural_validation": structural_passed,
                "behavior_preservation": behavioral_passed,
                "invariant_preservation": invariant_passed,
            }
            entry.metadata["checks"] = checks
            entry.metadata["final_checks"] = {
                key: "PASS" if value else "FAIL"
                for key, value in checks.items()
            }
            if all(checks.values()) and entry.replacements_count > 0:
                entry.metadata["status"] = "pass"
                entry.metadata["final_decision"] = "PASS"
            else:
                entry.metadata["status"] = "review_required"
                entry.metadata["final_decision"] = "REVIEW_REQUIRED"

    @staticmethod
    def _compatibility_check(value: Any) -> str:
        normalized = str(value or "").strip().upper()
        if normalized == "PASS":
            return "PASS"
        if normalized in {"", "NOT_APPLICABLE", "N/A"}:
            return "NOT_APPLICABLE"
        return "FAIL"

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
        detected_actions = self.local_refactor_detector.detect(
            language=language,
            file_name=file_entry.file_name,
            source_code=file_entry.source_code,
            existing_actions=existing_actions,
        )
        planned_types = {
            action.action_type
            for action in request.refactoring_plan.actions
        }
        if not planned_types:
            return detected_actions

        # A non-empty RDP plan is authoritative.  SCTVA may supplement a
        # requested action type (for example, locate additional proven-dead
        # Python statements), but it must not introduce unrelated constants,
        # string rewrites, or smell refactorings that the plan did not ask for.
        planned_literals: set[Any] = set()
        for action in request.refactoring_plan.actions:
            if action.action_type not in {"extract_constant", "introduce_constant"}:
                continue
            if "literal_value" in action.parameters:
                planned_literals.add(action.parameters["literal_value"])
            values = action.parameters.get("literal_values")
            if isinstance(values, list):
                planned_literals.update(values)

        return [
            action
            for action in detected_actions
            if action.action_type in planned_types
            and (
                action.action_type not in {"extract_constant", "introduce_constant"}
                or not planned_literals
                or action.parameters.get("literal_value") in planned_literals
            )
        ]

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
                effective_type = str(
                    log_entry.metadata.get("reclassified_action_type")
                    or action.action_type
                ).strip()
                if effective_type == action.action_type:
                    effective_actions.append(action)
                    continue

                # A legacy RDP Remove Dead Code step can be safely transformed
                # into an exception-handler refactoring. Validators must see
                # the operation that was actually applied, not the stale RDP
                # recommendation, otherwise they would demand that a live
                # handler was deleted.
                effective_parameters = log_entry.metadata.get("effective_action_parameters")
                if not isinstance(effective_parameters, dict):
                    effective_parameters = dict(action.parameters or {})
                effective_actions.append(
                    RefactoringAction(
                        action_type=effective_type,
                        parameters=dict(effective_parameters),
                        source_step_id=action.source_step_id,
                        source_refactoring=action.source_refactoring,
                        warnings=list(action.warnings),
                    )
                )
        return effective_actions

    @staticmethod
    def _parallel_file_workers(
        request: SCTVARequestContract,
        file_count: int,
    ) -> int:
        if file_count <= 1:
            return 1

        configured = request.execution_options.max_parallel_files
        if configured > 0:
            return max(1, min(configured, file_count))

        env_value = os.getenv("SCTVA_MAX_PARALLEL_FILES", "").strip()
        if env_value.isdigit():
            return max(1, min(int(env_value), file_count))

        cpu_count = os.cpu_count() or 2
        return max(1, min(4, cpu_count, file_count))

    @classmethod
    def _build_action_scope_index(
        cls,
        actions: List[RefactoringAction],
    ) -> List[Dict[str, Any]]:
        indexed: List[Dict[str, Any]] = []
        for action in actions:
            source_file = cls._action_source_file(action)
            if not source_file:
                continue
            normalized = cls._normalize_path(source_file)
            if not normalized:
                continue
            indexed.append(
                {
                    "action": action,
                    "path": normalized,
                    "base": normalized.rsplit("/", 1)[-1],
                }
            )
        return indexed

    @classmethod
    def _actions_for_file_from_scope(
        cls,
        actions: List[RefactoringAction],
        action_scope: List[Dict[str, Any]],
        file_name: str,
    ) -> List[RefactoringAction]:
        if not action_scope:
            return actions

        file_path = cls._normalize_path(file_name)
        if not file_path:
            return []
        file_base = file_path.rsplit("/", 1)[-1]

        return [
            entry["action"]
            for entry in action_scope
            if cls._file_matches_normalized(
                action_path=entry["path"],
                action_base=entry["base"],
                file_path=file_path,
                file_base=file_base,
            )
        ]

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
        return cls._file_matches_normalized(
            action_path=action_path,
            action_base=action_base,
            file_path=file_path,
            file_base=file_base,
        )

    @staticmethod
    def _file_matches_normalized(
        *,
        action_path: str,
        action_base: str,
        file_path: str,
        file_base: str,
    ) -> bool:
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
