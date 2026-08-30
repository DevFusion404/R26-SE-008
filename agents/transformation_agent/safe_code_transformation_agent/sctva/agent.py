"""Main orchestrator for Safe Code Transformation and Validation."""

from __future__ import annotations

import copy
import os
import re
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
from .integration.planner_adapter import normalize_sctva_request_payload
from .constants import (
    ACTION_EXTRACT_CLASS,
    ACTION_EXTRACT_METHOD,
    ACTION_ENCAPSULATE_C_VARIABLE,
    ACTION_ENCAPSULATE_VARIABLE,
    ACTION_HIDE_DELEGATE,
    ACTION_INTRODUCE_JAVA_PARAMETER_OBJECT,
    ACTION_INLINE_PYTHON_CLASS,
    ACTION_MOVE_PYTHON_METHOD,
    ACTION_NARROW_EXCEPTION_HANDLER,
    ACTION_NOOP,
    ACTION_REMOVE_DEAD_CODE,
    ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM,
    ACTION_REPLACE_NESTED_CONDITIONAL_WITH_GUARD_CLAUSES,
    ACTION_REPLACE_CONDITIONAL_WITH_GUARD_CLAUSES,
    ACTION_GUARD_CLAUSES,
    ACTION_UPDATE_JAVA_PARAMETER_OBJECT_CALL_SITE,
    EXTRACT_CLASS_ACTIONS,
    EXTRACT_CLASS_ACTION_BY_LANGUAGE,
    PARAMETER_OBJECT_ACTIONS,
    PARAMETER_OBJECT_ACTION_BY_LANGUAGE,
)
from .models import SCTVAResult, TransformationLogEntry, ValidationStepResult
from .reporting.safety_reporter import SafetyReporter
from .rollback.rollback_manager import RollbackManager
from .scoring.confidence_scorer import ConfidenceScorer
from .transformers import (
    c_transformers,
    java_parameter_object,
    python_inline_class,
    python_transformers,
)
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
        # Keep programmatic callers on the same RDP normalization path as both
        # HTTP entry points.  This is idempotent for an ordinary SCTVA request.
        normalized_payload, integrity_issues = normalize_sctva_request_payload(payload)
        if integrity_issues:
            return {
                "success": False,
                "status": "REVIEW_REQUIRED",
                "reason": "RDP_MOVE_METHOD_PARAMETERS_LOST",
                "rollback_occurred": False,
                "transformation_applied": False,
                "normalization_diagnostics": integrity_issues,
                "safety_report": {
                    "summary": "Transformation requires review before execution.",
                    "risk_flags": ["RDP_MOVE_METHOD_PARAMETERS_LOST"],
                    "human_messages": [
                        "Move Method planner parameters were lost before AST resolution; no source code was changed.",
                    ],
                },
            }
        request = SCTVARequestContract.from_dict(normalized_payload)
        return self.execute_request(request)

    def execute_request(self, request: SCTVARequestContract) -> Dict[str, Any]:
        """Execute validated request contract."""
        file_entries = self._collect_source_files(request)
        # Recover Move Method steps before file scoping.  This handles both
        # modern move_python_method actions and legacy/upstream noop actions
        # whose source_refactoring is still "Move Method".
        self._promote_move_method_noops(request.refactoring_plan.actions)
        self._promote_inline_class_noops(request.refactoring_plan.actions)
        self._canonicalize_inline_class_targets(request.refactoring_plan.actions)
        self._promote_hide_delegate_noops(request.refactoring_plan.actions)
        self._promote_polymorphism_noops(request.refactoring_plan.actions)
        self._promote_guard_clauses_noops(request.refactoring_plan.actions)
        self._resolve_action_source_files(
            request.refactoring_plan.actions,
            file_entries,
        )
        # Bind unscoped C Remove Dead Code actions before the immutable file
        # scope index is built. Without this pass, the presence of any other
        # file-scoped action caused an unscoped dead-code action to disappear
        # from every file execution.
        self._resolve_c_dead_code_source_files(
            request.refactoring_plan.actions,
            file_entries,
            fallback_language=request.language,
        )
        self._resolve_python_dead_code_source_files(
            request.refactoring_plan.actions,
            file_entries,
            fallback_language=request.language,
        )
        self._resolve_hide_delegate_source_files(
            request.refactoring_plan.actions,
            file_entries,
        )
        self._resolve_move_method_source_files(
            request.refactoring_plan.actions,
            file_entries,
        )
        self._resolve_inline_class_source_files(
            request.refactoring_plan.actions,
            file_entries,
        )
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
        self._resolve_guard_clauses_source_files(
            request.refactoring_plan.actions,
            file_entries,
            fallback_language=request.language,
        coordinated_parameter_transactions = (
            self._prepare_java_parameter_object_transactions(
                request.refactoring_plan.actions,
                file_entries,
            )
        )
        self._mark_unresolved_legacy_actions(request.refactoring_plan.actions)
        action_scope = self._build_action_scope_index(request.refactoring_plan.actions)
        project_file_payloads = [item.to_dict() for item in file_entries]

        def run_file(index: int, file_entry: SourceFileContract) -> tuple[int, Dict[str, Any] | None]:
            plan_actions = [
                RefactoringAction(
                    action_type=action.action_type,
                    parameters=copy.deepcopy(action.parameters),
                    source_step_id=action.source_step_id,
                    source_refactoring=action.source_refactoring,
                    warnings=list(action.warnings),
                )
                for action in self._actions_for_file_from_scope(
                    request.refactoring_plan.actions,
                    action_scope,
                    file_entry.file_name,
                )
            ]
            # RDP can emit generic C Global Variable targets such as
            # variable/get_variable/set_variable.  Resolve those placeholders
            # against SCTVA's own conservative C detector before local actions
            # are merged.  Mutating the existing RefactoringAction instances is
            # intentional: validation and plan-compliance must see the same
            # concrete target that the transformer receives.
            self._resolve_c_global_variable_plan_actions(
                plan_actions,
                file_entry=file_entry,
                fallback_language=request.language,
            )
            local_actions = self._local_actions_for_file(
                request=request,
                file_entry=file_entry,
                existing_actions=plan_actions,
            )
            local_actions = self._apply_local_target_recovery(
                plan_actions=plan_actions,
                local_actions=local_actions,
            )
            actions = [*plan_actions, *local_actions]
            if not actions:
                file_result = {
                    "file_name": file_entry.file_name,
                    "source_mode": file_entry.source_mode,
                    "origin": file_entry.origin,
                    "success": True,
                    "rollback_occurred": False,
                    "transformation_applied": False,
                    "refactored_code": file_entry.source_code,
                    "total_replacements": 0,
                    "status": "FULL_SUCCESS",
                    "safety_report": {
                        "rollback_occurred": False,
                        "rollback_reason": None,
                        "transformation_log": [],
                        "risk_flags": [],
                        "human_messages": ["No plan actions required for this file."],
                    },
                }
                return index, file_result

            file_result = self._execute_single_file(
                request=request,
                file_entry=file_entry,
                actions=actions,
                project_files=project_file_payloads,
            )
            return index, file_result

        indexed_results: Dict[int, Dict[str, Any]] = {}
        # A coordinated Java Parameter Object edit shares a repository-wide
        # caller set.  Execute its file actions deterministically so its audit
        # trail and transaction decision cannot race other file workers.
        max_workers = 1 if coordinated_parameter_transactions else self._parallel_file_workers(
            request,
            len(file_entries),
        )

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

        if coordinated_parameter_transactions:
            self._finalize_java_parameter_object_transactions(
                file_results=file_results,
                file_entries=file_entries,
                transactions=coordinated_parameter_transactions,
                request=request,
            )

        # Python Inline Class can require a true repository transaction when a
        # tiny class is imported, instantiated, or inherited in peer files.
        # Per-file execution intentionally preserves those classes first; this
        # phase then rewrites the accepted workspace atomically and upgrades
        # the original REVIEW_REQUIRED action only if every repository check
        # passes.
        self._finalize_python_inline_class_transactions(
            file_results=file_results,
            file_entries=file_entries,
            request=request,
        )

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

        modified_by_name = {
            res.get("file_name"): res.get("refactored_code")
            for res in file_results
            if isinstance(res, dict) and res.get("file_name")
        }
        all_workspace_files = []
        for file_entry in file_entries:
            fname = file_entry.file_name
            final_content = modified_by_name.get(fname, file_entry.source_code)
            all_workspace_files.append({
                "file_name": fname,
                "file_path": fname,
                "source_code": final_content,
                "language": file_entry.language,
                "modified": fname in modified_by_name and final_content != file_entry.source_code,
            })

        has_review_global = any(
            res.get("status") == "REVIEW_REQUIRED"
            or any(
                str(e.get("metadata", {}).get("status") if isinstance(e, dict) else getattr(e, "metadata", {}).get("status", "")).lower() == "review_required"
                or "review" in str(e.get("warnings") if isinstance(e, dict) else getattr(e, "warnings", "")).lower()
                for e in res.get("safety_report", {}).get("transformation_log", [])
            )
            for res in file_results if isinstance(res, dict)
        )
        safely_not_applicable_files = sum(
            1
            for res in file_results
            if isinstance(res, dict) and res.get("status") == "NOT_APPLICABLE"
        )
        if rollback_occurred or any(res.get("rollback_occurred") for res in file_results if isinstance(res, dict)):
            overall_status = "FAILED"
        elif has_review_global:
            overall_status = "REVIEW_REQUIRED"
        elif safely_not_applicable_files == files_total and files_total > 0:
            overall_status = "NOT_APPLICABLE"
        elif (
            success
            and files_succeeded == files_total
            and files_applied == files_total
            and files_total > 0
        ):
            overall_status = "FULL_SUCCESS"
        elif files_succeeded > 0 or total_replacements > 0:
            overall_status = "PARTIAL_SUCCESS"
        else:
            overall_status = "FAILED"

        target_res = None
        if len(file_results) == 1:
            target_res = file_results[0]
        else:
            modified = [res for res in file_results if isinstance(res, dict) and res.get("transformation_applied")]
            if modified:
                target_res = modified[0]
            elif file_results and isinstance(file_results[0], dict):
                target_res = file_results[0]

        res_dict = {
            "request_id": request.request_id,
            "status": overall_status,
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
            "transformed_workspace_files": all_workspace_files,
        }
        if target_res and isinstance(target_res, dict):
            res_dict["file_name"] = target_res.get("file_name")
            res_dict["source_mode"] = target_res.get("source_mode")
            res_dict["origin"] = target_res.get("origin")
            res_dict["refactored_code"] = target_res.get("refactored_code")
            res_dict["safety_report"] = target_res.get("safety_report")
            res_dict["validation_syntax"] = target_res.get("validation_syntax")
            res_dict["validation_structural"] = target_res.get("validation_structural")
            res_dict["validation_behavioral"] = target_res.get("validation_behavioral")
            res_dict["validation_invariant"] = target_res.get("validation_invariant")
        return res_dict

    def _collect_source_files(self, request: SCTVARequestContract) -> List[SourceFileContract]:
        if request.source_files:
            return request.source_files

        # A single-source request historically used the synthetic name
        # ``source_code``.  RDP actions, however, are commonly scoped to the
        # real filename (for example ``jarvis.py``).  When there is exactly
        # one unambiguous plan filename, bind the source text to that name so
        # action scoping and AST resolution operate on the same file identity.
        plan_file_names = {
            str(action.parameters.get("source_file") or "").strip()
            for action in request.refactoring_plan.actions
            if str(action.parameters.get("source_file") or "").strip()
        }
        file_name = next(iter(plan_file_names)) if len(plan_file_names) == 1 else "source_code"
        return [
            SourceFileContract(
                file_name=file_name,
                source_code=request.source_code,
                language=request.language,
                source_mode="raw",
            )
        ]

    @classmethod
    def _prepare_java_parameter_object_transactions(
        cls,
        actions: List[RefactoringAction],
        file_entries: List[SourceFileContract],
    ) -> List[Dict[str, Any]]:
        """Expand verified Java callers into one coordinated project edit.

        The target transformer remains the source of truth for target safety.
        This preflight only resolves concrete external invocations and creates
        internal caller actions after every candidate was resolved.
        """
        project_files = [entry.to_dict() for entry in file_entries]
        additions: List[RefactoringAction] = []
        transactions: List[Dict[str, Any]] = []
        generated_keys: set[tuple[str, str, str]] = set()

        for action_index, action in enumerate(list(actions), start=1):
            if action.action_type != ACTION_INTRODUCE_JAVA_PARAMETER_OBJECT:
                continue
            if action.parameters.get("java_parameter_object_role") == "call_site":
                continue
            source_file = cls._action_source_file(action)
            candidates = [
                entry for entry in file_entries
                if source_file and cls._file_matches(
                    action_source_file=source_file,
                    file_name=entry.file_name,
                )
            ]
            if len(candidates) != 1:
                continue
            target_entry = candidates[0]
            if (target_entry.language or "").lower() != "java" and not target_entry.file_name.lower().endswith(".java"):
                continue

            params = action.parameters
            _, _, preflight = java_parameter_object.apply_introduce_parameter_object(
                target_entry.source_code,
                method=str(params.get("method") or params.get("method_name") or "").strip(),
                parameter_object_name=str(
                    params.get("parameter_object_name")
                    or params.get("new_class_name")
                    or params.get("parameter_class_name")
                    or ""
                ).strip(),
                source_class=str(params.get("source_class") or "").strip(),
                source_file=target_entry.file_name,
                current_file_name=target_entry.file_name,
                parameter_name=str(params.get("parameter_name") or "params").strip(),
                project_source_files=project_files,
                source_resolution_error=str(params.get("source_resolution_error") or ""),
                target_parameter_count=params.get("target_parameter_count"),
                parameter_types=params.get("parameter_types"),
            )
            resolution = preflight.get("cross_file_call_site_resolution")
            if not isinstance(resolution, dict):
                continue
            params["cross_file_call_site_resolution"] = copy.deepcopy(resolution)
            if resolution.get("unresolved"):
                # The real action will report this same diagnostic and leave
                # every source file untouched.
                continue
            callers = list(resolution.get("resolved") or [])
            if not callers:
                continue

            transaction_id = f"java_parameter_object_{action_index}"
            params["coordinated_project_transaction_id"] = transaction_id
            params["coordinated_project_callers"] = copy.deepcopy(callers)
            params["parameter_count"] = int(preflight.get("before_parameter_count") or 0)
            params["target_signature"] = str(resolution.get("target_signature") or "")
            params["source_class"] = str(preflight.get("source_class") or params.get("source_class") or "")
            transaction_files = {target_entry.file_name}
            for caller in callers:
                caller_file = str(caller.get("file_name") or "")
                if not caller_file:
                    continue
                key = (transaction_id, caller_file, str(params.get("method") or ""))
                if key in generated_keys:
                    continue
                generated_keys.add(key)
                transaction_files.add(caller_file)
                additions.append(
                    RefactoringAction(
                        action_type=ACTION_UPDATE_JAVA_PARAMETER_OBJECT_CALL_SITE,
                        parameters={
                            "source_file": caller_file,
                            "java_parameter_object_role": "call_site",
                            "coordinated_project_transaction_id": transaction_id,
                            "target_class": params["source_class"],
                            "method": str(params.get("method") or params.get("method_name") or ""),
                            "parameter_object_name": str(params.get("parameter_object_name") or ""),
                            "parameter_count": params["parameter_count"],
                            "target_signature": params["target_signature"],
                            "caller_resolution": copy.deepcopy(caller),
                        },
                        source_step_id=action.source_step_id,
                        source_refactoring="Introduce Parameter Object coordinated call-site update",
                    )
                )
            transactions.append({
                "id": transaction_id,
                "target_file": target_entry.file_name,
                "files": sorted(transaction_files),
                "callers": callers,
                "target_signature": params["target_signature"],
            })

        actions.extend(additions)
        return transactions

    def _finalize_java_parameter_object_transactions(
        self,
        *,
        file_results: List[Dict[str, Any]],
        file_entries: List[SourceFileContract],
        transactions: List[Dict[str, Any]],
        request: SCTVARequestContract,
    ) -> None:
        """Accept coordinated edits only after the complete project re-parses."""
        entries_by_path = {
            self._normalize_path(entry.file_name): entry
            for entry in file_entries
        }
        results_by_path = {
            self._normalize_path(str(result.get("file_name") or "")): result
            for result in file_results
        }

        for transaction in transactions:
            transaction_paths = {
                self._normalize_path(file_name) for file_name in transaction["files"]
            }
            participant_results = [
                results_by_path.get(path) for path in transaction_paths
            ]
            incomplete = any(result is None or not result.get("transformation_applied") for result in participant_results)
            project_sources = []
            for path, entry in entries_by_path.items():
                result = results_by_path.get(path)
                project_sources.append({
                    "file_name": entry.file_name,
                    "source_code": str(result.get("refactored_code")) if result else entry.source_code,
                })
            repository_validation = self.syntax_validator.validate_java_project(
                project_sources,
                require_compilation=request.execution_options.require_compilation,
                timeout_seconds=request.execution_options.timeout_seconds,
            )
            accepted = not incomplete and repository_validation.passed
            for result in participant_results:
                if result is None:
                    continue
                report = result.get("safety_report") or {}
                report.setdefault("human_messages", []).append(
                    "Repository-wide Java Parameter Object validation: " + repository_validation.message
                )
                report.setdefault("risk_flags", [])
                for log in report.get("transformation_log") or []:
                    metadata = log.get("metadata") or {}
                    if metadata.get("coordinated_project_transaction_id") == transaction["id"]:
                        metadata["repository_validation"] = {
                            "passed": repository_validation.passed,
                            "details": repository_validation.details,
                        }
                if accepted:
                    continue
                original = entries_by_path[self._normalize_path(str(result.get("file_name") or ""))].source_code
                result["refactored_code"] = original
                result["transformation_applied"] = False
                result["rollback_occurred"] = True
                result["success"] = False
                result["rollback_reason"] = "CROSS_FILE_CALL_SITES_REQUIRE_COORDINATED_EDIT"
                report["summary"] = "Coordinated Java Parameter Object transformation rejected and rolled back."
                report["rollback_reason"] = "CROSS_FILE_CALL_SITES_REQUIRE_COORDINATED_EDIT"
                report["risk_flags"].append("validation_failed:cross_file_parameter_object")


    def _finalize_python_inline_class_transactions(
        self,
        *,
        file_results: List[Dict[str, Any]],
        file_entries: List[SourceFileContract],
        request: SCTVARequestContract,
    ) -> None:
        """Upgrade safe cross-file Python Inline Class actions atomically.

        The per-file transformer deliberately reports external references
        instead of mutating a class that peer files still depend on. This
        stage starts from the accepted workspace, rewrites every participant
        in memory, validates the complete candidate, and commits all files
        together only when every check passes.
        """

        entries_by_path = {
            self._normalize_path(entry.file_name): entry
            for entry in file_entries
        }
        results_by_path = {
            self._normalize_path(str(result.get("file_name") or "")): result
            for result in file_results
        }

        candidates: List[Dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for result in file_results:
            file_name = str(result.get("file_name") or "")
            report = result.get("safety_report") or {}
            for log in report.get("transformation_log") or []:
                if str(log.get("action_type") or "") != ACTION_INLINE_PYTHON_CLASS:
                    continue
                metadata = log.get("metadata") or {}
                if (
                    str(metadata.get("status") or "").lower() != "review_required"
                    or str(metadata.get("reason") or "")
                    != "EXTERNAL_CLASS_REFERENCES_REQUIRE_REPOSITORY_INLINE"
                ):
                    continue
                class_name = str(
                    metadata.get("class_to_inline")
                    or metadata.get("normalized_target_class")
                    or ""
                ).strip()
                if not class_name:
                    continue
                key = (self._normalize_path(file_name), class_name)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append({
                    "file_name": file_name,
                    "class_name": class_name,
                })

        for candidate in candidates:
            target_file = candidate["file_name"]
            class_name = candidate["class_name"]
            target_path = self._normalize_path(target_file)
            target_result = results_by_path.get(target_path)
            target_entry = entries_by_path.get(target_path)
            if target_result is None or target_entry is None:
                continue

            workspace_sources: List[Dict[str, Any]] = []
            for entry in file_entries:
                path_key = self._normalize_path(entry.file_name)
                result = results_by_path.get(path_key)
                workspace_sources.append({
                    "file_name": entry.file_name,
                    "file_path": entry.file_name,
                    "language": entry.language or request.language,
                    "source_code": (
                        str(result.get("refactored_code"))
                        if result is not None
                        else entry.source_code
                    ),
                })

            transaction = python_inline_class.apply_repository_inline_class_transaction(
                workspace_sources,
                target_file_name=target_file,
                class_to_inline=class_name,
            )
            target_report = target_result.get("safety_report") or {}
            target_report.setdefault("human_messages", [])
            target_report.setdefault("risk_flags", [])

            if transaction.get("status") != "success":
                reason = str(
                    transaction.get("reason")
                    or "REPOSITORY_INLINE_TRANSACTION_NOT_PROVEN_SAFE"
                )
                target_report["human_messages"].append(
                    "Repository-wide Inline Class was not committed: " + reason + "."
                )
                target_result["repository_inline_transaction"] = {
                    "status": "review_required",
                    "reason": reason,
                    "details": transaction,
                }
                continue

            transformed_sources = dict(transaction.get("transformed_sources") or {})
            replacement_counts = {
                str(name): int(count or 0)
                for name, count in (transaction.get("replacement_counts") or {}).items()
            }
            candidate_repository = list(transaction.get("candidate_repository") or [])

            repository_syntax = self.syntax_validator.validate_python_project(
                candidate_repository,
                timeout_seconds=request.execution_options.timeout_seconds,
            )

            current_target_source = str(target_result.get("refactored_code") or "")
            candidate_target_source = str(
                transformed_sources.get(target_file, current_target_source)
            )
            synthetic_action = RefactoringAction(
                action_type=ACTION_INLINE_PYTHON_CLASS,
                parameters={
                    "class_to_inline": class_name,
                    "source_file": target_file,
                    "inline_mode": "module_function",
                    "repository_atomic_inline": True,
                },
                source_refactoring="Inline Class repository transaction",
            )
            target_structural = self.structural_validator.validate(
                language="python",
                original_code=current_target_source,
                transformed_code=candidate_target_source,
                actions=[synthetic_action],
            )
            target_behavioral = self.behavioral_validator.validate(
                language="python",
                original_code=current_target_source,
                transformed_code=candidate_target_source,
                behavior_tests=request.refactoring_plan.behavior_tests,
                enable_behavior_tests=request.execution_options.enable_behavior_tests,
                actions=[synthetic_action],
                strict_mode=request.execution_options.strict_mode,
                project_source_files=candidate_repository,
                current_file_name=target_file,
                structural_validation_passed=target_structural.passed,
            )
            target_invariant = self.invariant_miner.mine(
                language="python",
                behavioral_step=target_behavioral,
                actions=[synthetic_action],
                strict_mode=request.execution_options.strict_mode,
            )

            accepted = all((
                repository_syntax.passed,
                target_structural.passed,
                target_behavioral.passed,
                target_invariant.passed,
            ))
            validation_details = {
                "repository_syntax": repository_syntax.to_dict(),
                "target_structural": target_structural.to_dict(),
                "target_behavioral": target_behavioral.to_dict(),
                "target_invariant": target_invariant.to_dict(),
            }
            transaction_id = (
                f"python_inline_class:{self._normalize_path(target_file)}:{class_name}"
            )

            if not accepted:
                failed = [
                    name
                    for name, step in (
                        ("repository_syntax", repository_syntax),
                        ("target_structural", target_structural),
                        ("target_behavioral", target_behavioral),
                        ("target_invariant", target_invariant),
                    )
                    if not step.passed
                ]
                target_report["human_messages"].append(
                    "Repository-wide Inline Class candidate was rolled back; "
                    "failed validation stage(s): " + ", ".join(failed) + "."
                )
                if "validation_failed:repository_inline_class" not in target_report["risk_flags"]:
                    target_report["risk_flags"].append(
                        "validation_failed:repository_inline_class"
                    )
                target_result["repository_inline_transaction"] = {
                    "id": transaction_id,
                    "status": "rolled_back",
                    "failed_stages": failed,
                    "validation": validation_details,
                }
                continue

            peer_metadata_by_file = {
                self._normalize_path(str(item.get("file_name") or "")): item
                for item in transaction.get("peer_metadata") or []
                if isinstance(item, dict)
            }
            affected_files = [
                target_file,
                *[str(name) for name in transaction.get("reference_files") or []],
            ]

            participants_present = all(
                self._normalize_path(affected_file) in results_by_path
                and self._normalize_path(affected_file) in entries_by_path
                and affected_file in transformed_sources
                for affected_file in affected_files
            )
            if not participants_present:
                target_report["human_messages"].append(
                    "Repository-wide Inline Class candidate was discarded because "
                    "a transaction participant disappeared before commit."
                )
                if "validation_failed:repository_inline_class_participant" not in target_report["risk_flags"]:
                    target_report["risk_flags"].append(
                        "validation_failed:repository_inline_class_participant"
                    )
                continue

            for affected_file in affected_files:
                affected_path = self._normalize_path(affected_file)
                result = results_by_path[affected_path]
                entry = entries_by_path[affected_path]
                new_source = str(transformed_sources[affected_file])
                replacements = int(replacement_counts.get(affected_file, 0) or 0)
                result["refactored_code"] = new_source
                result["transformation_applied"] = new_source != entry.source_code
                result["total_replacements"] = int(result.get("total_replacements", 0) or 0) + replacements
                result["confidence_applicable"] = bool(
                    result.get("transformation_applied") or result.get("rollback_occurred")
                )
                result["repository_inline_transaction"] = {
                    "id": transaction_id,
                    "status": "success",
                    "strategy": transaction.get("strategy"),
                    "class_to_inline": class_name,
                    "target_file": target_file,
                    "affected_files": affected_files,
                    "method_names": list(transaction.get("method_names") or []),
                    "validation": validation_details,
                }

                report = result.get("safety_report") or {}
                report.setdefault("human_messages", [])
                report.setdefault("risk_flags", [])
                report.setdefault("transformation_log", [])

                if affected_path == target_path:
                    target_inline_log = None
                    for log in report.get("transformation_log") or []:
                        metadata = log.get("metadata") or {}
                        if (
                            str(log.get("action_type") or "") == ACTION_INLINE_PYTHON_CLASS
                            and str(metadata.get("class_to_inline") or "") == class_name
                            and str(metadata.get("reason") or "")
                            == "EXTERNAL_CLASS_REFERENCES_REQUIRE_REPOSITORY_INLINE"
                        ):
                            target_inline_log = log
                            break
                    if target_inline_log is not None:
                        metadata = target_inline_log.setdefault("metadata", {})
                        metadata.update({
                            "status": "success",
                            "reason": "REPOSITORY_ATOMIC_INLINE_APPLIED",
                            "strategy": "repository_atomic_module_function",
                            "inline_mode": "module_function",
                            "repository_transaction_id": transaction_id,
                            "repository_affected_files": affected_files,
                            "repository_reference_files": list(
                                transaction.get("reference_files") or []
                            ),
                            "repository_method_names": list(
                                transaction.get("method_names") or []
                            ),
                            "repository_validation": validation_details,
                            "reclassified_action_type": ACTION_INLINE_PYTHON_CLASS,
                            "plan_compliance": "PASS",
                        })
                        target_inline_log["replacements_count"] = replacements
                        target_inline_log["warnings"] = [
                            f"Inline Class applied atomically across the repository: "
                            f"{class_name} was safely inlined."
                        ]
                else:
                    peer_metadata = dict(peer_metadata_by_file.get(affected_path) or {})
                    report["transformation_log"].append({
                        "action_index": len(report["transformation_log"]) + 1,
                        "action_type": "inline_python_class_repository_update",
                        "replacements_count": replacements,
                        "warnings": [],
                        "metadata": {
                            **peer_metadata,
                            "status": "success",
                            "class_to_inline": class_name,
                            "source_file": affected_file,
                            "repository_transaction_id": transaction_id,
                            "target_file": target_file,
                            "strategy": "repository_atomic_module_function",
                        },
                    })
                    report["human_messages"].append(
                        f"Repository Inline Class updated references to {class_name} "
                        f"atomically with {target_file}."
                    )

            target_report = target_result.get("safety_report") or {}
            filtered_messages = []
            for message in target_report.get("human_messages") or []:
                lowered = str(message).lower()
                if (
                    lowered.startswith("no source-code change was applied")
                    or lowered.startswith("inline class requires review:")
                    or lowered == "no replacements were applied."
                    or lowered.startswith("inline class plan compliance failed:")
                ):
                    continue
                filtered_messages.append(message)
            filtered_messages.extend([
                f"Repository-wide Inline Class applied successfully: {class_name} "
                f"was rewritten across {len(affected_files)} file(s).",
                repository_syntax.message,
                target_structural.message,
            ])
            target_report["human_messages"] = filtered_messages
            target_report["risk_flags"] = [
                flag
                for flag in target_report.get("risk_flags") or []
                if flag not in {
                    "transformation_not_applied",
                    "transformation_warning",
                    "plan_compliance_failed:inline_class",
                }
            ]
            target_report["summary"] = (
                "Repository-wide Inline Class transformation accepted after all safety checks."
            )
            target_report["rollback_reason"] = ""

            target_result.setdefault("plan_compliance", {})["inline_class"] = "PASS"
            target_result["rollback_occurred"] = False
            target_result["success"] = True
            target_result["status"] = "FULL_SUCCESS"

            validation_score, confidence_details = self.scorer.score(
                syntax=repository_syntax,
                structural=target_structural,
                behavioral=target_behavioral,
                invariant=target_invariant,
            )
            target_result["validation_score"] = round(validation_score, 4)
            target_result["confidence_score"] = round(validation_score, 4)
            target_result["confidence_components"] = confidence_details
            target_result["confidence_applicable"] = True
            target_result["validation"] = {
                "syntax": repository_syntax.to_dict(),
                "structural": target_structural.to_dict(),
                "behavioral": target_behavioral.to_dict(),
                "invariant": target_invariant.to_dict(),
            }

    @classmethod
    def _resolve_action_source_files(
        cls,
        actions: List[RefactoringAction],
        file_entries: List[SourceFileContract],
    ) -> None:
        """Resolve every plan path to one canonical imported source file.

        CUQA commonly preserves a repository prefix (for example
        ``Jarvis/jarvis.py``) while RDP emits only ``jarvis.py``.  File
        resolution is deliberately completed before symbol resolution so a
        missing method/class cannot be misreported as a missing source file.
        Basename recovery is accepted only when it is unique in the imported
        workspace.
        """

        indexed_entries = [
            {
                "entry": entry,
                "path": cls._normalize_path(entry.file_name),
                "base": cls._normalize_path(entry.file_name).rsplit("/", 1)[-1],
            }
            for entry in file_entries
            if cls._normalize_path(entry.file_name)
        ]

        for action in actions:
            params = action.parameters
            requested = cls._action_source_file(action)
            requested_path = cls._normalize_path(requested)

            if not requested_path:
                if len(indexed_entries) == 1:
                    resolved_entry = indexed_entries[0]["entry"]
                    params["source_file"] = resolved_entry.file_name
                    params["source_file_resolution"] = {
                        "status": "success",
                        "strategy": "single_imported_source",
                        "requested": "",
                        "resolved": resolved_entry.file_name,
                    }
                continue

            requested_base = requested_path.rsplit("/", 1)[-1]
            exact_matches = [
                item
                for item in indexed_entries
                if item["path"] == requested_path
            ]
            suffix_matches = [
                item
                for item in indexed_entries
                if item["path"].endswith(f"/{requested_path}")
                or requested_path.endswith(f"/{item['path']}")
            ]
            basename_matches = [
                item
                for item in indexed_entries
                if item["base"] == requested_base
            ]

            if len(exact_matches) == 1:
                matches = exact_matches
                strategy = "exact_path"
            elif len(suffix_matches) == 1:
                matches = suffix_matches
                strategy = "repository_suffix"
            elif len(basename_matches) == 1:
                matches = basename_matches
                strategy = "unique_basename"
            else:
                matches = exact_matches or suffix_matches or basename_matches
                strategy = ""

            if len(matches) == 1:
                resolved_entry = matches[0]["entry"]
                params["source_file"] = resolved_entry.file_name
                params["source_file_resolution"] = {
                    "status": "success",
                    "strategy": strategy,
                    "requested": requested,
                    "resolved": resolved_entry.file_name,
                }
                params.pop("source_file_resolution_error", None)
                continue

            status = "review_required" if matches else "not_applicable"
            reason = (
                "AMBIGUOUS_SOURCE_FILE_TARGET"
                if matches
                else "SOURCE_FILE_NOT_FOUND_IN_IMPORTED_WORKSPACE"
            )
            params["source_file_resolution"] = {
                "status": status,
                "strategy": "failed",
                "requested": requested,
                "resolved": "",
                "reason": reason,
                "candidates": [item["entry"].file_name for item in matches],
            }
            params["source_file_resolution_error"] = reason
            params["source_resolution_error"] = reason
            params["source_resolution_status"] = status

    @classmethod
    def _resolve_c_dead_code_source_files(
        cls,
        actions: List[RefactoringAction],
        file_entries: List[SourceFileContract],
        *,
        fallback_language: str,
    ) -> None:
        """Resolve C dead-code actions against all imported C/H sources.

        Resolution is conservative and happens before file/action scoping. A
        named function is bound only when its definition is unique in the
        imported repository. An unnamed action is bound only when exactly one
        repository-proven unused static function exists. Ambiguous actions are
        retained as an explicit review-required log on a C file rather than
        being silently filtered out.
        """

        c_files = [
            entry for entry in file_entries
            if (entry.language or "").strip().lower() == "c"
            or entry.file_name.lower().endswith((".c", ".h"))
        ]
        if not c_files and str(fallback_language or "").strip().lower() == "c":
            c_files = list(file_entries)
        if not c_files:
            return

        project_files = [entry.to_dict() for entry in file_entries]
        for action in actions:
            if action.action_type != ACTION_REMOVE_DEAD_CODE:
                continue
            params = action.parameters
            configured_file = cls._action_source_file(action)
            if (
                str(fallback_language or "").strip().lower() != "c"
                and not configured_file.lower().endswith((".c", ".h"))
            ):
                continue
            configured_matches = [
                entry for entry in c_files
                if configured_file
                and cls._file_matches(
                    action_source_file=configured_file,
                    file_name=entry.file_name,
                )
            ]
            if configured_file and len(configured_matches) == 1:
                candidates = configured_matches
            elif configured_file:
                candidates = []
            else:
                candidates = list(c_files)

            method = str(
                params.get("method")
                or params.get("method_name")
                or params.get("function")
                or params.get("function_name")
                or ""
            ).strip()
            raw_line = params.get("source_line")
            source_line = int(raw_line) if isinstance(raw_line, (int, float)) else None

            definition_matches: list[tuple[SourceFileContract, str]] = []
            if method:
                definition_matches = [
                    (entry, method)
                    for entry in candidates
                    if c_transformers.c_function_definition_count(
                        entry.source_code,
                        method,
                    ) == 1
                ]
            elif len(candidates) == 1 and source_line is not None:
                analysis = c_transformers.analyze_c_dead_code_target(
                    candidates[0].source_code,
                    source_line=source_line,
                    project_source_files=project_files,
                    current_file_name=candidates[0].file_name,
                )
                target = (
                    str(analysis.get("target") or "").strip()
                    if analysis.get("removable") is True
                    else ""
                )
                definition_matches = [(candidates[0], target)]
            else:
                for entry in candidates:
                    if source_line is not None:
                        analysis = c_transformers.analyze_c_dead_code_target(
                            entry.source_code,
                            source_line=source_line,
                            project_source_files=project_files,
                            current_file_name=entry.file_name,
                        )
                        target = str(analysis.get("target") or "").strip()
                        if target and analysis.get("removable") is True:
                            definition_matches.append((entry, target))
                        continue
                    for target in c_transformers.proven_unused_static_functions(
                        entry.source_code,
                        project_source_files=project_files,
                        current_file_name=entry.file_name,
                    ):
                        definition_matches.append((entry, target))

            if len(definition_matches) == 1:
                entry, resolved_method = definition_matches[0]
                params["source_file"] = entry.file_name
                if resolved_method:
                    params["method"] = resolved_method
                params["source_file_resolution"] = {
                    "status": "success",
                    "strategy": "repository_c_dead_code_target",
                    "requested": configured_file,
                    "resolved": entry.file_name,
                }
                params["dead_code_scope_resolution"] = "repository_wide_c_h_scan"
                params.pop("source_file_resolution_error", None)
                params.pop("source_resolution_error", None)
                continue

            # Keep the action visible in a per-file report when the target is
            # missing or ambiguous. It must never vanish merely because other
            # actions happen to be file-scoped.
            diagnostic_file = configured_matches[0] if configured_matches else c_files[0]
            reason = (
                "AMBIGUOUS_C_DEAD_CODE_TARGET"
                if len(definition_matches) > 1
                else "C_DEAD_CODE_TARGET_NOT_FOUND"
            )
            params["source_file"] = diagnostic_file.file_name
            params["unresolved_legacy_target"] = True
            params["unresolved_status"] = "review_required"
            params["unresolved_reason"] = reason
            params["source_resolution_error"] = reason
            params["dead_code_scope_resolution"] = "repository_wide_c_h_scan_failed"

    @classmethod
    def _resolve_python_dead_code_source_files(
        cls,
        actions: List[RefactoringAction],
        file_entries: List[SourceFileContract],
        *,
        fallback_language: str,
    ) -> None:
        """Keep named/line-targeted Python dead-code actions through scoping."""

        python_files = [
            entry for entry in file_entries
            if (entry.language or "").strip().lower() == "python"
            or entry.file_name.lower().endswith(".py")
        ]
        if not python_files and str(fallback_language or "").strip().lower() == "python":
            python_files = list(file_entries)
        if not python_files:
            return

        for action in actions:
            if action.action_type != ACTION_REMOVE_DEAD_CODE:
                continue
            params = action.parameters
            configured_file = cls._action_source_file(action)
            if (
                str(fallback_language or "").strip().lower() != "python"
                and not configured_file.lower().endswith(".py")
            ):
                continue
            configured_matches = [
                entry for entry in python_files
                if configured_file
                and cls._file_matches(
                    action_source_file=configured_file,
                    file_name=entry.file_name,
                )
            ]
            if configured_file and len(configured_matches) == 1:
                continue
            candidates = configured_matches if configured_file else python_files
            method = str(
                params.get("method") or params.get("method_name") or ""
            ).strip()
            class_name = str(
                params.get("class_name")
                or params.get("source_class")
                or params.get("target_class")
                or ""
            ).strip() or None
            raw_line = params.get("source_line")
            source_line = int(raw_line) if isinstance(raw_line, (int, float)) else None
            matches = []
            if method or source_line is not None:
                for entry in candidates:
                    kind, fingerprint = python_transformers.resolve_dead_code_target(
                        entry.source_code,
                        method_name=method,
                        class_name=class_name,
                        source_line=source_line,
                    )
                    if kind and fingerprint:
                        matches.append(entry)

            if len(matches) == 1:
                params["source_file"] = matches[0].file_name
                params["source_file_resolution"] = {
                    "status": "success",
                    "strategy": "python_dead_code_ast_target",
                    "requested": configured_file,
                    "resolved": matches[0].file_name,
                }
                params["dead_code_scope_resolution"] = "python_ast_scan"
                params.pop("source_file_resolution_error", None)
                params.pop("source_resolution_error", None)
                continue

            diagnostic_file = configured_matches[0] if configured_matches else python_files[0]
            params["source_file"] = diagnostic_file.file_name
            params["unresolved_legacy_target"] = True
            params["unresolved_status"] = "review_required"
            params["unresolved_reason"] = (
                "AMBIGUOUS_PYTHON_DEAD_CODE_TARGET"
                if len(matches) > 1
                else "PYTHON_DEAD_CODE_TARGET_NOT_FOUND"
            )
            params["dead_code_scope_resolution"] = "python_ast_scan_failed"

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
        safely_not_applicable_logs = [
            entry
            for entry in transformation_log
            if entry.action_type != ACTION_NOOP
            and str(entry.metadata.get("status") or "").lower()
            in {"not_applicable", "already_applied", "satisfied"}
        ]
        all_executable_actions_safely_not_applicable = (
            bool(executable_actions)
            and len(safely_not_applicable_logs) == len(executable_actions)
        )

        if not transformation_attempted:
            if all_executable_actions_safely_not_applicable:
                transform_warnings.append(
                    "No source-code change was required: every executable action was safely classified as not applicable or already satisfied."
                )
            elif executable_actions:
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
            # A coordinated caller can legitimately depend on the target class
            # in another imported file.  It is compiled with that full source
            # set by the transaction finalizer below.
            require_compilation=(
                request.execution_options.require_compilation
                and language != "c"
                and not any(
                    action.action_type == ACTION_UPDATE_JAVA_PARAMETER_OBJECT_CALL_SITE
                    for action in actions
                )
            ),
            timeout_seconds=request.execution_options.timeout_seconds,
        )

        if (
            language == "c"
            and syntax_step.passed
            and request.execution_options.require_compilation
        ):
            candidate_c_repository = []
            target_path = self._normalize_path(file_entry.file_name)
            for project_file in project_files:
                project_name = str(
                    project_file.get("file_name") or project_file.get("file_path") or ""
                )
                candidate_c_repository.append({
                    **project_file,
                    "source_code": (
                        transformed_code
                        if self._normalize_path(project_name) == target_path
                        else str(project_file.get("source_code") or "")
                    ),
                })
            repository_syntax = self.syntax_validator.validate_c_project(
                candidate_c_repository,
                require_compilation=True,
                timeout_seconds=request.execution_options.timeout_seconds,
            )
            syntax_step.details["repository_compiler_validation"] = (
                repository_syntax.to_dict()
            )
            syntax_step.details["compiler_validation"] = (
                repository_syntax.details.get("compiler_validation", "UNAVAILABLE")
            )
            syntax_step.details["compiler"] = repository_syntax.details.get("compiler")
            if repository_syntax.details.get("warnings"):
                syntax_step.details.setdefault("warnings", []).extend(
                    repository_syntax.details["warnings"]
                )
            if not repository_syntax.passed:
                syntax_step.passed = False
                syntax_step.score = 0.0
                syntax_step.message = repository_syntax.message
                syntax_step.details.setdefault("diagnostics", []).extend(
                    repository_syntax.details.get("diagnostics", [])
                )

        structural_step = self.structural_validator.validate(
            language=language,
            original_code=file_entry.source_code,
            transformed_code=transformed_code,
            actions=validation_actions,
        )

        coordinated_call_site_only = (
            language == "java"
            and bool(actions)
            and all(
                action.action_type == ACTION_UPDATE_JAVA_PARAMETER_OBJECT_CALL_SITE
                for action in actions
            )
        )
        coordinated_parameter_target = (
            language == "java"
            and any(
                action.action_type == ACTION_INTRODUCE_JAVA_PARAMETER_OBJECT
                and action.parameters.get("coordinated_project_transaction_id")
                for action in actions
            )
        )
        if coordinated_call_site_only:
            # The caller now depends on the target's new nested parameter
            # object, so isolated probes would execute an intentionally
            # incomplete one-file compilation unit.  Repository compilation
            # is performed atomically after every participating file changes.
            behavioral_step = ValidationStepResult(
                name="behavioral",
                passed=True,
                score=1.0,
                message="Java coordinated caller behavior deferred to repository validation.",
                details={"mode": "coordinated_java_repository_validation"},
            )
            invariant_step = ValidationStepResult(
                name="invariant",
                passed=True,
                score=1.0,
                message="Java coordinated caller invariants deferred to repository validation.",
                details={"mode": "coordinated_java_repository_validation"},
            )
        else:
            behavioral_step = self.behavioral_validator.validate(
                language=language,
                original_code=file_entry.source_code,
                transformed_code=transformed_code,
                behavior_tests=request.refactoring_plan.behavior_tests,
                enable_behavior_tests=request.execution_options.enable_behavior_tests,
                actions=validation_actions,
                strict_mode=request.execution_options.strict_mode,
                # The imported project snapshot still contains the original
                # caller files while this target is being validated.  Probe the
                # target in isolation here; the final transaction stage then
                # compiles the fully rewritten repository together.
                project_source_files=[] if coordinated_parameter_target else project_files,
                current_file_name=file_entry.file_name,
                structural_validation_passed=structural_step.passed,
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

        selective_replay = self._attempt_selective_java_parameter_object_replay(
            request=request,
            file_entry=file_entry,
            actions=actions,
            project_files=project_files,
            structural_step=structural_step,
            rollback_occurred=rollback_occurred,
        )
        if selective_replay is not None:
            (
                transformed_code,
                transformation_log,
                transform_warnings,
                validation_actions,
                syntax_step,
                structural_step,
                behavioral_step,
                invariant_step,
            ) = selective_replay
            rollback_occurred = False
            rollback_reason = ""
            transform_warnings.append(
                "Selective rollback preserved independent accepted actions after an invalid Java Parameter Object action was removed."
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
            dead_code_validation=list(
                structural_step.details.get("dead_code_validation") or []
            ),
        )
        self._finalize_guard_clauses_logs(
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
            extra_warnings=(
                []
                if all_executable_actions_safely_not_applicable
                else transform_warnings
            ),
            transformation_applied=transformation_applied,
            not_applicable=all_executable_actions_safely_not_applicable,
        )

        safety_report.human_messages.append(
            "Confidence formula: syntax_w*syntax + structural_w*structural + behavioral_w*behavioral"
        )

        requested_move_count = sum(
            1
            for action in actions
            if action.action_type == ACTION_MOVE_PYTHON_METHOD
        )
        completed_move_count = sum(
            1
            for log_entry in transformation_log
            if str(
                log_entry.metadata.get("reclassified_action_type")
                or log_entry.action_type
            ).strip() == ACTION_MOVE_PYTHON_METHOD
            and str(log_entry.metadata.get("status") or "success").lower()
            in {"success", "already_applied"}
        )
        unresolved_move_count = sum(
            1
            for log_entry in transformation_log
            if log_entry.action_type == ACTION_MOVE_PYTHON_METHOD
            and str(log_entry.metadata.get("status") or "").lower() == "not_applicable"
        )
        review_move_count = sum(
            1
            for log_entry in transformation_log
            if log_entry.action_type == ACTION_MOVE_PYTHON_METHOD
            and str(log_entry.metadata.get("status") or "").lower() == "review_required"
        )
        move_method_plan_complete = (
            requested_move_count == 0
            or completed_move_count + unresolved_move_count == requested_move_count
        )
        if requested_move_count and not move_method_plan_complete:
            safety_report.risk_flags.append("plan_compliance_failed:move_method")
            safety_report.human_messages.append(
                "Move Method plan compliance failed: not every requested Move Method action was applied."
            )

        requested_inline_count = sum(
            1
            for action in actions
            if action.action_type == ACTION_INLINE_PYTHON_CLASS
        )
        successful_inline_count = sum(
            1
            for log_entry in transformation_log
            if log_entry.replacements_count > 0
            and str(
                log_entry.metadata.get("reclassified_action_type")
                or log_entry.action_type
            ).strip() == ACTION_INLINE_PYTHON_CLASS
            and str(log_entry.metadata.get("status") or "success").lower() == "success"
        )
        unresolved_inline_count = sum(
            1
            for log_entry in transformation_log
            if log_entry.action_type == ACTION_INLINE_PYTHON_CLASS
            and str(log_entry.metadata.get("status") or "").lower() == "not_applicable"
            and str(log_entry.metadata.get("reason") or "")
            != "SMELL_RESOLVED_BY_PRIOR_REFACTORING"
        )
        resolved_by_prior_refactoring_inline_count = sum(
            1
            for log_entry in transformation_log
            if log_entry.action_type == ACTION_INLINE_PYTHON_CLASS
            and str(log_entry.metadata.get("status") or "").lower()
            in {"not_applicable", "satisfied"}
            and str(log_entry.metadata.get("reason") or "")
            == "SMELL_RESOLVED_BY_PRIOR_REFACTORING"
        )
        already_handled_inline_count = sum(
            1
            for log_entry in transformation_log
            if log_entry.action_type == ACTION_INLINE_PYTHON_CLASS
            and str(log_entry.metadata.get("status") or "").lower()
            == "already_handled"
            and str(log_entry.metadata.get("reason") or "")
            == "ALREADY_HANDLED_BY_PRIOR_INLINE_CLASS"
        )
        review_inline_count = sum(
            1
            for log_entry in transformation_log
            if log_entry.action_type == ACTION_INLINE_PYTHON_CLASS
            and str(log_entry.metadata.get("status") or "").lower() == "review_required"
        )
        inline_class_plan_complete = (
            requested_inline_count == 0
            or successful_inline_count
            + unresolved_inline_count
            + resolved_by_prior_refactoring_inline_count
            + already_handled_inline_count
            == requested_inline_count
        )
        if requested_inline_count and not inline_class_plan_complete:
            safety_report.risk_flags.append("plan_compliance_failed:inline_class")
            safety_report.human_messages.append(
                "Inline Class plan compliance failed: not every requested Inline Class action was applied."
            )

        requested_global_variable_count = sum(
            1
            for action in actions
            if action.action_type in {
                ACTION_ENCAPSULATE_C_VARIABLE,
                ACTION_ENCAPSULATE_VARIABLE,
            }
            or str(action.source_refactoring or "").strip().lower()
            in {"encapsulate variable", "global variable"}
        )
        successful_global_variable_count = sum(
            1
            for log_entry in transformation_log
            if log_entry.replacements_count > 0
            and str(
                log_entry.metadata.get("reclassified_action_type")
                or log_entry.action_type
            ).strip() == ACTION_ENCAPSULATE_C_VARIABLE
            and str(log_entry.metadata.get("status") or "success").lower() == "success"
        )
        global_variable_plan_complete = (
            requested_global_variable_count == 0
            or successful_global_variable_count == requested_global_variable_count
        )
        if requested_global_variable_count and not global_variable_plan_complete:
            safety_report.risk_flags.append("plan_compliance_failed:global_variable")
            safety_report.human_messages.append(
                "Global Variable plan compliance failed: not every requested Encapsulate Variable action was applied."
            )

        requested_hide_delegate_count = sum(
            1
            for action in actions
            if action.action_type == ACTION_HIDE_DELEGATE
        )
        successful_hide_delegate_count = sum(
            1
            for log_entry in transformation_log
            if log_entry.replacements_count > 0
            and str(
                log_entry.metadata.get("reclassified_action_type")
                or log_entry.action_type
            ).strip() == ACTION_HIDE_DELEGATE
            and str(log_entry.metadata.get("status") or "success").lower() == "success"
        )
        unresolved_hide_delegate_count = sum(
            1
            for log_entry in transformation_log
            if log_entry.action_type == ACTION_HIDE_DELEGATE
            and str(log_entry.metadata.get("status") or "").lower() == "not_applicable"
        )
        review_hide_delegate_count = sum(
            1
            for log_entry in transformation_log
            if log_entry.action_type == ACTION_HIDE_DELEGATE
            and str(log_entry.metadata.get("status") or "").lower() == "review_required"
        )
        hide_delegate_plan_complete = (
            requested_hide_delegate_count == 0
            or successful_hide_delegate_count + unresolved_hide_delegate_count
            == requested_hide_delegate_count
        )
        if requested_hide_delegate_count and not hide_delegate_plan_complete:
            safety_report.risk_flags.append("plan_compliance_failed:hide_delegate")
            safety_report.human_messages.append(
                "Hide Delegate plan compliance failed: not every requested Hide Delegate action was applied."
            )

        requested_polymorphism_count = sum(
            1
            for action in actions
            if action.action_type == ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM
        )
        successful_polymorphism_count = sum(
            1
            for log_entry in transformation_log
            if log_entry.replacements_count > 0
            and str(
                log_entry.metadata.get("reclassified_action_type")
                or log_entry.action_type
            ).strip() == ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM
            and str(log_entry.metadata.get("status") or "success").lower() == "success"
        )
        not_applicable_polymorphism_count = sum(
            1
            for log_entry in transformation_log
            if log_entry.action_type == ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM
            and str(log_entry.metadata.get("status") or "").lower() == "not_applicable"
        )
        review_polymorphism_count = sum(
            1
            for log_entry in transformation_log
            if log_entry.action_type == ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM
            and str(log_entry.metadata.get("status") or "").lower() == "review_required"
        )
        polymorphism_plan_complete = (
            requested_polymorphism_count == 0
            or successful_polymorphism_count + not_applicable_polymorphism_count
            == requested_polymorphism_count
        )
        if requested_polymorphism_count and not polymorphism_plan_complete:
            safety_report.risk_flags.append("plan_compliance_failed:polymorphism")
            safety_report.human_messages.append(
                "Replace Conditional with Polymorphism plan compliance failed: "
                "not every requested action was safely applied."
            )

        validation_passed = (
            (not rollback_occurred)
            and syntax_step.passed
            and structural_step.passed
            and behavioral_step.passed
            and invariant_step.passed
            and move_method_plan_complete
            and inline_class_plan_complete
            and global_variable_plan_complete
            and hide_delegate_plan_complete
            and polymorphism_plan_complete
        )
        success = validation_passed and (
            transformation_applied
            or all_executable_actions_safely_not_applicable
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
        result["plan_compliance"] = {
            "move_method": (
                "NOT_APPLICABLE"
                if requested_move_count > 0
                and unresolved_move_count == requested_move_count
                else "REVIEW_REQUIRED"
                if unresolved_move_count or review_move_count
                else ("PASS" if move_method_plan_complete else "FAIL")
            ),
            "inline_class": (
                "NOT_APPLICABLE"
                if requested_inline_count > 0
                and unresolved_inline_count == requested_inline_count
                else "REVIEW_REQUIRED"
                if unresolved_inline_count or review_inline_count
                else ("PASS" if inline_class_plan_complete else "FAIL")
            ),
            "global_variable": (
                "PASS" if global_variable_plan_complete else "FAIL"
            ),
            "hide_delegate": (
                "NOT_APPLICABLE"
                if requested_hide_delegate_count > 0
                and unresolved_hide_delegate_count == requested_hide_delegate_count
                else "REVIEW_REQUIRED"
                if unresolved_hide_delegate_count or review_hide_delegate_count
                else ("PASS" if hide_delegate_plan_complete else "FAIL")
            ),
            "replace_conditional_with_polymorphism": (
                "NOT_APPLICABLE"
                if requested_polymorphism_count > 0
                and not_applicable_polymorphism_count == requested_polymorphism_count
                else "REVIEW_REQUIRED"
                if not_applicable_polymorphism_count or review_polymorphism_count
                else ("PASS" if polymorphism_plan_complete else "FAIL")
            ),
        }
        has_review = any(
            str(e.metadata.get("status") if hasattr(e, "metadata") else "").lower() == "review_required"
            or any("review" in str(w).lower() for w in (e.warnings if hasattr(e, "warnings") else []))
            for e in transformation_log
        )
        if rollback_occurred:
            file_status = "FAILED"
        elif has_review:
            file_status = "REVIEW_REQUIRED"
        elif all_executable_actions_safely_not_applicable and not transformation_applied:
            file_status = "NOT_APPLICABLE"
        elif success:
            file_status = "FULL_SUCCESS"
        elif total_replacements > 0:
            file_status = "PARTIAL_SUCCESS"
        else:
            file_status = "FAILED"

        result["status"] = file_status
        return result


    @staticmethod
    def _promote_guard_clauses_noops(actions: List[RefactoringAction]) -> None:
        """Promote legacy Guard Clause noops back to an executable action."""
        for action in actions:
            if action.action_type != ACTION_NOOP:
                continue
            source_refactoring = str(action.source_refactoring or "").strip().lower()
            warnings = " ".join(str(item) for item in action.warnings).lower()
            if (
                source_refactoring in {
                    "replace nested conditional with guard clauses",
                    "replace nested conditionals with guard clauses",
                    "replace conditional with guard clauses",
                    "deep nesting",
                    "deepnesting",
                    "guard clauses",
                }
                or "guard clause" in warnings
                or "deep nesting" in warnings
            ):
                action.action_type = ACTION_REPLACE_NESTED_CONDITIONAL_WITH_GUARD_CLAUSES
                params = action.parameters
                params["promoted_from_noop"] = True
                params.setdefault("method", "")
                action.warnings = [
                    warning
                    for warning in action.warnings
                    if not (
                        "guard clause" in str(warning).lower()
                        and ("mapped to noop" in str(warning).lower() or "not simulated" in str(warning).lower())
                    )
                ]

    def _resolve_guard_clauses_source_files(
        self,
        actions: List[RefactoringAction],
        file_entries: List[SourceFileContract],
        *,
        fallback_language: str = "",
    ) -> None:
        """Bind Guard Clause actions to the matching C source file."""
        c_files = [
            f for f in file_entries
            if (f.language or fallback_language).strip().lower() == "c"
            or f.file_name.lower().endswith(".c")
        ]
        if not c_files:
            return

        guard_actions = {
            ACTION_REPLACE_NESTED_CONDITIONAL_WITH_GUARD_CLAUSES,
            ACTION_REPLACE_CONDITIONAL_WITH_GUARD_CLAUSES,
            ACTION_GUARD_CLAUSES,
        }

        for action in actions:
            if action.action_type not in guard_actions:
                continue
            params = action.parameters
            configured_file = str(params.get("source_file") or "").strip()
            if configured_file:
                continue

            method = str(params.get("method") or params.get("target_method") or "").strip()

            matches: List[SourceFileContract] = []
            if method:
                for entry in c_files:
                    if re.search(rf"\b{re.escape(method)}\s*\(", entry.source_code):
                        matches.append(entry)

            if len(matches) == 1:
                params["source_file"] = matches[0].file_name
            elif len(c_files) == 1:
                params["source_file"] = c_files[0].file_name

    @staticmethod
    def _finalize_guard_clauses_logs(
        transformation_log: List[TransformationLogEntry],
        *,
        syntax_passed: bool,
        structural_passed: bool,
        behavioral_passed: bool,
        invariant_passed: bool,
        rollback_occurred: bool,
    ) -> None:
        """Finalize metrics and validation status for Replace Nested Conditional with Guard Clauses."""
        guard_actions = {
            ACTION_REPLACE_NESTED_CONDITIONAL_WITH_GUARD_CLAUSES,
            ACTION_REPLACE_CONDITIONAL_WITH_GUARD_CLAUSES,
            ACTION_GUARD_CLAUSES,
        }
        for entry in transformation_log:
            if entry.action_type not in guard_actions:
                continue

            metadata = entry.metadata
            if str(metadata.get("status") or "").lower() == "not_applicable":
                metadata["final_checks"] = {
                    "plan_compliance": "NOT_APPLICABLE",
                    "nesting_depth_reduction": "NOT_APPLICABLE",
                    "behavior_preservation": "PASS" if behavioral_passed else "FAIL",
                    "compilation_syntax_validation": "PASS" if syntax_passed else "FAIL",
                    "invariant_preservation": "PASS" if invariant_passed else "FAIL",
                }
                metadata["syntax"] = metadata["final_checks"]["compilation_syntax_validation"]
                metadata["behavior"] = metadata["final_checks"]["behavior_preservation"]
                metadata["smell_reduction"] = "NOT_APPLICABLE"
                metadata["final_status"] = "NOT_APPLICABLE"
                metadata["final_decision"] = "NOT_APPLICABLE"
                continue

            nesting_reduced = bool(metadata.get("nesting_reduced"))
            after_depth = metadata.get("after_nesting_depth")
            depth_below_threshold = isinstance(after_depth, int) and after_depth <= 4
            smell_reduced_passed = nesting_reduced and depth_below_threshold

            checks = {
                "plan_compliance": "PASS" if metadata.get("plan_compliance") == "PASS" else "FAIL",
                "nesting_depth_reduction": "PASS" if smell_reduced_passed else "FAIL",
                "behavior_preservation": "PASS" if behavioral_passed else "FAIL",
                "compilation_syntax_validation": "PASS" if syntax_passed else "FAIL",
                "invariant_preservation": "PASS" if invariant_passed else "FAIL",
            }
            metadata["final_checks"] = checks
            metadata["syntax"] = checks["compilation_syntax_validation"]
            metadata["behavior"] = checks["behavior_preservation"]
            metadata["smell_reduction"] = "PASS" if smell_reduced_passed else "FAIL"

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
    def _promote_move_method_noops(actions: List[RefactoringAction]) -> None:
        """Promote legacy Move Method noops back to an executable action.

        Older PlannerAdapter versions rejected filename-derived identifiers and
        stored the original refactoring only in ``source_refactoring``/warnings.
        The real target is recovered from source AST in the next step.
        """

        for action in actions:
            if action.action_type != ACTION_NOOP:
                continue
            source_refactoring = str(action.source_refactoring or "").strip().lower()
            warnings = " ".join(str(item) for item in action.warnings).lower()
            if source_refactoring not in {"move method", "feature envy"} and "move method" not in warnings:
                continue
            action.action_type = ACTION_MOVE_PYTHON_METHOD
            params = action.parameters or {}
            params["promoted_from_noop"] = True
            params.setdefault("method", "")
            params.setdefault("source_class", "")
            params.setdefault("destination_class", "")
            params.setdefault("destination_parameter", "")

    @staticmethod
    def _promote_inline_class_noops(actions: List[RefactoringAction]) -> None:
        """Recover legacy RDP Inline Class steps that were stored as noops."""

        for action in actions:
            if action.action_type != ACTION_NOOP:
                continue
            source_refactoring = str(action.source_refactoring or "").strip().lower()
            warnings = " ".join(str(item) for item in action.warnings).lower()
            if source_refactoring != "inline class" and "inline class" not in warnings:
                continue
            action.action_type = ACTION_INLINE_PYTHON_CLASS
            # ``parameters`` is a dataclass-owned dict.  Do not use ``or {}``
            # here: an empty legacy parameter object must receive the resolved
            # class and source-file values below.
            params = action.parameters
            params["promoted_from_noop"] = True
            params.setdefault("class_to_inline", "")
            action.warnings = [
                warning
                for warning in action.warnings
                if not (
                    "inline class" in str(warning).lower()
                    and (
                        "richer semantic edits" in str(warning).lower()
                        or "not simulated" in str(warning).lower()
                        or "mapped to noop" in str(warning).lower()
                    )
                )
            ]

    @staticmethod
    def _canonicalize_inline_class_targets(actions: List[RefactoringAction]) -> None:
        """Preserve one semantic Inline Class target across planner formats."""

        for action in actions:
            if action.action_type != ACTION_INLINE_PYTHON_CLASS:
                continue
            params = action.parameters
            requested_target = params.get("requested_target")
            requested_target = (
                requested_target if isinstance(requested_target, dict) else {}
            )
            legacy_step = params.get("legacy_step")
            legacy_step = legacy_step if isinstance(legacy_step, dict) else {}
            legacy_params = legacy_step.get("parameters")
            legacy_params = legacy_params if isinstance(legacy_params, dict) else {}
            legacy_target = legacy_step.get("target")
            legacy_target = legacy_target if isinstance(legacy_target, dict) else {}

            target_class = str(
                params.get("qualified_class_name")
                or params.get("class_to_inline")
                or params.get("target_class")
                or params.get("source_class")
                or params.get("class_name")
                or requested_target.get("class_to_inline")
                or legacy_params.get("class_to_inline")
                or legacy_params.get("target_class")
                or legacy_params.get("source_class")
                or legacy_target.get("class")
                or ""
            ).strip()
            source_file = str(
                params.get("source_file")
                or requested_target.get("source_file")
                or legacy_params.get("source_file")
                or legacy_target.get("source_file")
                or ""
            ).strip()

            params["class_to_inline"] = target_class
            params["target_class"] = target_class
            if source_file:
                params["source_file"] = source_file
            params["requested_target"] = {
                "class_to_inline": target_class,
                "source_file": source_file,
            }
            if target_class:
                params.pop("target_resolution_error", None)
                if params.get("source_resolution_error") == "INLINE_CLASS_TARGET_MISSING":
                    params.pop("source_resolution_error", None)
                continue

            params["target_resolution_error"] = "INLINE_CLASS_TARGET_MISSING"
            params["source_resolution_error"] = "INLINE_CLASS_TARGET_MISSING"
            params["source_resolution_status"] = "review_required"

    @staticmethod
    def _promote_hide_delegate_noops(actions: List[RefactoringAction]) -> None:
        """Recover legacy Hide Delegate steps before they are silently ignored.

        Earlier adapter versions treated Hide Delegate as an unsupported
        semantic edit and replaced it with ``noop``.  The current transformer
        implements this operation for proven Python and Java message chains,
        so preserve the original RDP target data and dispatch the real action.
        """

        for action in actions:
            if action.action_type != ACTION_NOOP:
                continue
            source_refactoring = str(action.source_refactoring or "").strip().lower()
            warnings = " ".join(str(item) for item in action.warnings).lower()
            if source_refactoring != "hide delegate" and "hide delegate" not in warnings:
                continue

            params = action.parameters
            legacy_step = params.get("legacy_step")
            if not isinstance(legacy_step, dict):
                legacy_step = {}
            legacy_params = legacy_step.get("parameters")
            legacy_params = legacy_params if isinstance(legacy_params, dict) else {}
            legacy_target = legacy_step.get("target")
            legacy_target = legacy_target if isinstance(legacy_target, dict) else {}

            def first_text(*values: Any) -> str:
                for value in values:
                    if isinstance(value, str) and value.strip():
                        return value.strip()
                return ""

            params["source_class"] = first_text(
                params.get("source_class"),
                legacy_params.get("source_class"),
                legacy_target.get("class"),
                legacy_target.get("source_class"),
            )
            params["delegate_member"] = first_text(
                params.get("delegate_member"),
                params.get("delegate_field"),
                legacy_params.get("delegate_member"),
                legacy_params.get("delegate_field"),
                legacy_params.get("delegate"),
            )
            params["delegated_member"] = first_text(
                params.get("delegated_member"),
                params.get("target_member"),
                legacy_params.get("delegated_member"),
                legacy_params.get("target_member"),
                legacy_params.get("member"),
            )
            params["new_method_name"] = first_text(
                params.get("new_method_name"),
                legacy_params.get("new_method_name"),
                legacy_params.get("delegate_method_name"),
            )
            # Preserve the method/line hints from RDP even when the adapter had
            # to map an incomplete Hide Delegate step to noop.  These hints are
            # essential for repository-sized files because the semantic resolver
            # must search the correct function instead of an unrelated message
            # chain elsewhere in the module.
            params["method"] = first_text(
                params.get("method"),
                params.get("method_name"),
                legacy_target.get("method"),
                legacy_target.get("function"),
                legacy_params.get("method"),
                legacy_params.get("method_name"),
            )
            legacy_lines = legacy_target.get("lines")
            if not isinstance(legacy_lines, (list, tuple)):
                legacy_lines = legacy_params.get("lines")
            if not isinstance(params.get("source_line"), (int, float)):
                explicit_line = legacy_params.get("source_line")
                if isinstance(explicit_line, (int, float)):
                    params["source_line"] = int(explicit_line)
                elif (
                    isinstance(legacy_lines, (list, tuple))
                    and legacy_lines
                    and isinstance(legacy_lines[0], (int, float))
                ):
                    params["source_line"] = int(legacy_lines[0])
            params["source_file"] = first_text(
                params.get("source_file"),
                legacy_params.get("source_file"),
                legacy_target.get("file"),
                legacy_target.get("source_file"),
            )
            params["promoted_from_noop"] = True
            action.action_type = ACTION_HIDE_DELEGATE
            action.warnings = [
                warning
                for warning in action.warnings
                if not (
                    "hide delegate" in str(warning).lower()
                    and (
                        "richer semantic edits" in str(warning).lower()
                        or "not simulated" in str(warning).lower()
                        or "mapped to noop" in str(warning).lower()
                    )
                )
            ]

    @staticmethod
    def _promote_polymorphism_noops(actions: List[RefactoringAction]) -> None:
        """Recover plans normalized before polymorphism support was added."""

        for action in actions:
            if action.action_type != ACTION_NOOP:
                continue
            source_refactoring = str(action.source_refactoring or "").strip().lower()
            warnings = " ".join(str(item) for item in action.warnings).lower()
            if (
                source_refactoring != "replace conditional with polymorphism"
                and "replace conditional with polymorphism" not in warnings
                and "replace_conditional_with_polymorphism" not in warnings
            ):
                continue

            params = action.parameters
            legacy_step = params.get("legacy_step")
            legacy_step = legacy_step if isinstance(legacy_step, dict) else {}
            legacy_params = legacy_step.get("parameters")
            legacy_params = legacy_params if isinstance(legacy_params, dict) else {}
            legacy_target = legacy_step.get("target")
            legacy_target = legacy_target if isinstance(legacy_target, dict) else {}

            def first_text(*values: Any) -> str:
                return next(
                    (
                        value.strip()
                        for value in values
                        if isinstance(value, str) and value.strip()
                    ),
                    "",
                )

            lines = legacy_target.get("lines")
            if not isinstance(lines, (list, tuple)):
                lines = legacy_params.get("lines")
            params["method"] = first_text(
                params.get("method"),
                params.get("method_name"),
                legacy_target.get("method"),
                legacy_target.get("function"),
                legacy_params.get("method"),
                legacy_params.get("method_name"),
            )
            params["source_class"] = first_text(
                params.get("source_class"),
                legacy_target.get("class"),
                legacy_params.get("source_class"),
                legacy_params.get("class_name"),
            )
            params["source_file"] = first_text(
                params.get("source_file"),
                legacy_target.get("file"),
                legacy_target.get("source_file"),
                legacy_params.get("source_file"),
            )
            params["base_class_name"] = first_text(
                params.get("base_class_name"),
                legacy_params.get("base_class_name"),
            )
            if isinstance(lines, (list, tuple)) and lines:
                params.setdefault("start_line", lines[0])
                params.setdefault("source_line", lines[0])
                params.setdefault("end_line", lines[-1])
            params["promoted_from_noop"] = True
            action.action_type = ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM
            action.warnings = [
                warning
                for warning in action.warnings
                if not (
                    "replace conditional with polymorphism" in str(warning).lower()
                    or "replace_conditional_with_polymorphism" in str(warning).lower()
                    or "mapped to noop" in str(warning).lower()
                )
            ]

    @classmethod
    def _resolve_hide_delegate_source_files(
        cls,
        actions: List[RefactoringAction],
        file_entries: List[SourceFileContract],
    ) -> None:
        """Recover incomplete legacy Python Hide Delegate targets from typed code."""

        from .transformers import python_hide_delegate

        expanded_actions: list[RefactoringAction] = []
        for action in actions:
            if action.action_type != ACTION_HIDE_DELEGATE:
                continue
            params = action.parameters
            required = ("source_class", "delegate_member", "delegated_member")
            if all(str(params.get(key) or "").strip() for key in required):
                continue
            configured_file = cls._action_source_file(action)
            candidates = [
                entry
                for entry in file_entries
                if (
                    ((entry.language or "").strip().lower() == "python")
                    or entry.file_name.lower().endswith(".py")
                )
                and (
                    not configured_file
                    or cls._file_matches(
                        action_source_file=configured_file,
                        file_name=entry.file_name,
                    )
                )
            ]
            matches: list[tuple[SourceFileContract, dict[str, Any]]] = []
            failures: list[tuple[str, str]] = []
            for entry in candidates:
                source_line = params.get("source_line")
                source_line = int(source_line) if isinstance(source_line, (int, float)) else None
                resolution = python_hide_delegate.resolve_hide_delegate_target(
                    entry.source_code,
                    source_class=str(params.get("source_class") or ""),
                    delegate_member=str(params.get("delegate_member") or ""),
                    delegated_member=str(params.get("delegated_member") or ""),
                    new_method_name=str(params.get("new_method_name") or ""),
                    method_name=str(
                        params.get("method")
                        or params.get("method_name")
                        or ""
                    ),
                    source_line=source_line,
                )
                if resolution.get("status") == "success":
                    matches.append((entry, resolution))
                else:
                    failures.append((
                        str(resolution.get("status") or "review_required"),
                        str(resolution.get("reason") or "HIDE_DELEGATE_TARGET_NOT_FOUND"),
                    ))
            if len(matches) == 1:
                entry, resolution = matches[0]
                resolved_targets = resolution.get("targets")
                if not isinstance(resolved_targets, list) or not resolved_targets:
                    resolved_targets = [resolution]

                def apply_target(target_params: Dict[str, Any], target: Dict[str, Any]) -> None:
                    for key in required:
                        target_params[key] = str(target[key])
                    target_params["new_method_name"] = str(target["new_method_name"])
                    target_params["source_file"] = entry.file_name
                    target_params["hide_delegate_target_resolution"] = str(resolution["strategy"])
                    target_params["source_resolution_status"] = "success"
                    target_params.pop("source_resolution_error", None)

                apply_target(params, resolved_targets[0])
                for target in resolved_targets[1:]:
                    split_action = RefactoringAction(
                        action_type=ACTION_HIDE_DELEGATE,
                        parameters=dict(params),
                        source_step_id=action.source_step_id,
                        source_refactoring=action.source_refactoring,
                        warnings=list(action.warnings),
                    )
                    apply_target(split_action.parameters, target)
                    split_action.parameters["split_from_legacy_hide_delegate"] = True
                    expanded_actions.append(split_action)
            else:
                failure_reasons = [reason for _, reason in failures]
                params["source_resolution_error"] = (
                    "AMBIGUOUS_HIDE_DELEGATE_FILE"
                    if len(matches) > 1
                    else (
                        failure_reasons[0]
                        if len(set(failure_reasons)) == 1 and failure_reasons
                        else "HIDE_DELEGATE_TARGET_NOT_FOUND"
                    )
                )
                params["source_resolution_status"] = (
                    "review_required"
                    if len(matches) > 1
                    or any(status == "review_required" for status, _ in failures)
                    else "not_applicable"
                )
        actions.extend(expanded_actions)

    @classmethod
    def _resolve_inline_class_source_files(
        cls,
        actions: List[RefactoringAction],
        file_entries: List[SourceFileContract],
    ) -> None:
        """Resolve explicit Inline Class targets before semantic fallback."""

        import ast
        from .transformers import python_transformers

        for action in actions:
            if action.action_type != ACTION_INLINE_PYTHON_CLASS:
                continue
            params = action.parameters
            configured_file = cls._action_source_file(action)
            python_entries = [
                entry
                for entry in file_entries
                if (
                    ((entry.language or "").strip().lower() == "python")
                    or entry.file_name.lower().endswith(".py")
                )
            ]
            candidate_entries = [
                entry
                for entry in python_entries
                if (
                    not configured_file
                    or cls._file_matches(
                        action_source_file=configured_file,
                        file_name=entry.file_name,
                    )
                )
            ]

            requested = str(
                params.get("qualified_class_name")
                or params.get("class_to_inline")
                or params.get("target_class")
                or ""
            ).strip()
            explicit_matches: list[tuple[SourceFileContract, dict[str, Any]]] = []
            parse_failures = 0
            if requested:
                # A path in an RDP plan can be stale even when its explicit
                # class symbol is valid.  Recover that symbol across the
                # imported Python workspace before declaring the action stale.
                # This runs before local Lazy Class detection by design.
                explicit_candidates = candidate_entries or python_entries
                for entry in explicit_candidates:
                    resolution = python_transformers.resolve_inline_class_target(
                        entry.source_code,
                        class_to_inline=requested,
                    )
                    if resolution.get("status") == "success":
                        explicit_matches.append((entry, resolution))
                    elif resolution.get("reason") == "SOURCE_PARSE_FAILED":
                        parse_failures += 1
                    elif resolution.get("reason") in {
                        "DUPLICATE_EXPLICIT_CLASS_TARGET",
                        "AMBIGUOUS_QUALIFIED_INLINE_CLASS_TARGET",
                    }:
                        params["source_resolution_error"] = str(resolution["reason"])
                        params["source_resolution_status"] = "review_required"
                        explicit_matches = []
                        break

            if len(explicit_matches) == 1:
                entry, resolution = explicit_matches[0]
                params["source_file"] = entry.file_name
                params["class_to_inline"] = str(resolution["class_to_inline"])
                params["target_class"] = params["class_to_inline"]
                if resolution.get("qualified_class_name"):
                    params["qualified_class_name"] = str(
                        resolution["qualified_class_name"]
                    )
                params["requested_target"] = {
                    "class_to_inline": requested,
                    "source_file": entry.file_name,
                }
                params["target_resolution"] = "explicit_plan_target"
                params["inline_target_resolution"] = "explicit_plan_target"
                params["source_resolution_status"] = "success"
                params.pop("source_resolution_error", None)
                params.pop("source_file_resolution_error", None)
                params.pop("not_applicable_to_source", None)
                params.pop("not_applicable_reason", None)
                params.pop("not_applicable_action_type", None)
                continue
            if len(explicit_matches) > 1:
                params["source_resolution_error"] = "AMBIGUOUS_EXPLICIT_CLASS_FILE"
                params["source_resolution_status"] = "review_required"
                continue
            if params.get("source_resolution_error") == "DUPLICATE_EXPLICIT_CLASS_TARGET":
                continue

            matches: list[tuple[SourceFileContract, dict[str, Any]]] = []
            failures: list[str] = []
            for entry in candidate_entries:
                resolution = python_transformers.resolve_inline_class_target(
                    entry.source_code,
                    # The explicit name was not present. Semantic recovery is
                    # deliberately independent of that stale/malformed value.
                    class_to_inline="",
                )
                if resolution.get("status") == "success":
                    matches.append((entry, resolution))
                else:
                    failures.append(str(resolution.get("reason") or "INLINE_CLASS_TARGET_NOT_FOUND"))

            if len(matches) != 1:
                params["source_resolution_error"] = (
                    "AMBIGUOUS_INLINE_CLASS_FILE"
                    if len(matches) > 1
                    else (
                        "SOURCE_PARSE_FAILED"
                        if parse_failures and parse_failures == len(candidate_entries)
                        else (
                            "TARGET_CLASS_NOT_FOUND"
                            if requested
                            else (
                                failures[0]
                                if len(set(failures)) == 1 and failures
                                else "INLINE_CLASS_TARGET_NOT_FOUND"
                            )
                        )
                    )
                )
                params["source_resolution_status"] = (
                    "review_required"
                    if len(matches) > 1 or params["source_resolution_error"] == "SOURCE_PARSE_FAILED"
                    else "not_applicable"
                )
                continue

            entry, resolution = matches[0]
            params["source_file"] = entry.file_name
            params["class_to_inline"] = str(resolution["class_to_inline"])
            params["target_class"] = params["class_to_inline"]
            params["requested_target"] = {
                "class_to_inline": requested or params["class_to_inline"],
                "source_file": entry.file_name,
            }
            if requested and requested != params["class_to_inline"]:
                params["requested_class_to_inline"] = requested
            strategy = str(resolution.get("target_resolution") or resolution.get("strategy") or "python_ast_semantic_recovery")
            params["target_resolution"] = strategy
            params["inline_target_resolution"] = strategy
            params["source_resolution_status"] = "success"
            params.pop("source_resolution_error", None)

    @classmethod
    def _resolve_move_method_source_files(
        cls,
        actions: List[RefactoringAction],
        file_entries: List[SourceFileContract],
    ) -> None:
        """Resolve Move Method targets against actual Python source.

        The resolver is project-aware: when no source file is provided it scans
        candidate Python files and accepts the target only when exactly one file
        contains an unambiguous Feature-Envy move.
        """

        from .transformers import python_transformers

        for action in actions:
            if action.action_type != ACTION_MOVE_PYTHON_METHOD:
                continue

            params = action.parameters or {}
            configured_file = cls._action_source_file(action)
            candidate_entries = [
                entry
                for entry in file_entries
                if (
                    ((entry.language or "").strip().lower() == "python")
                    or entry.file_name.lower().endswith(".py")
                )
                and (
                    not configured_file
                    or cls._file_matches(
                        action_source_file=configured_file,
                        file_name=entry.file_name,
                    )
                )
            ]

            if configured_file and not candidate_entries:
                params["source_resolution_error"] = "SOURCE_FILE_TARGET_MISMATCH"
                continue

            source_line = params.get("source_line")
            if not isinstance(source_line, (int, float)):
                target_lines = params.get("target_lines")
                source_line = (
                    target_lines[0]
                    if isinstance(target_lines, list)
                    and target_lines
                    and isinstance(target_lines[0], (int, float))
                    else None
                )
            source_line = int(source_line) if isinstance(source_line, (int, float)) else None

            matches: list[tuple[SourceFileContract, dict[str, Any]]] = []
            failures: list[tuple[str, str]] = []
            non_applicable_resolutions: list[dict[str, Any]] = []
            for entry in candidate_entries:
                resolution = python_transformers.resolve_move_method_target(
                    entry.source_code,
                    method_name=str(
                        params.get("method") or params.get("source_method") or ""
                    ),
                    source_class=str(params.get("source_class") or ""),
                    destination_class=str(params.get("destination_class") or ""),
                    destination_parameter=str(params.get("destination_parameter") or ""),
                    source_line=source_line,
                    allow_unique_inference=params.get("promoted_from_noop") is True,
                )
                if resolution.get("status") == "success":
                    matches.append((entry, resolution))
                else:
                    failures.append((
                        str(resolution.get("status") or "review_required"),
                        str(resolution.get("reason") or "MOVE_METHOD_TARGET_NOT_FOUND"),
                    ))
                    if resolution.get("status") == "not_applicable":
                        non_applicable_resolutions.append({
                            **resolution,
                            "source_file": entry.file_name,
                        })

            if len(matches) != 1:
                failure_reasons = [reason for _, reason in failures]
                params["source_resolution_error"] = (
                    "AMBIGUOUS_MOVE_METHOD_FILE"
                    if len(matches) > 1
                    else (
                        failure_reasons[0]
                        if len(set(failure_reasons)) == 1 and failure_reasons
                        else "MOVE_METHOD_TARGET_NOT_FOUND"
                    )
                )
                params["source_resolution_status"] = (
                    "review_required"
                    if len(matches) > 1
                    or any(status == "review_required" for status, _ in failures)
                    else "not_applicable"
                )
                if params["source_resolution_status"] == "not_applicable" and non_applicable_resolutions:
                    resolved = non_applicable_resolutions[0]
                    params["target_kind"] = str(
                        resolved.get("target_kind") or "CLASS_METHOD"
                    )
                    params["suggested_refactoring"] = str(
                        resolved.get("suggested_refactoring") or ""
                    )
                    params["source_class_resolved"] = bool(
                        resolved.get("source_class_resolved", False)
                    )
                    params["destination_class_resolved"] = bool(
                        resolved.get("destination_class_resolved", False)
                    )
                    params["move_method_target_resolution"] = resolved
                continue

            entry, resolution = matches[0]
            requested = {
                "method": str(
                    params.get("method") or params.get("source_method") or ""
                ),
                "source_class": str(params.get("source_class") or ""),
                "destination_class": str(params.get("destination_class") or ""),
                "destination_parameter": str(params.get("destination_parameter") or ""),
            }
            params["source_file"] = entry.file_name
            params["method"] = str(resolution["method"])
            params["source_method"] = str(resolution["method"])
            params["source_class"] = str(resolution["source_class"])
            params["destination_class"] = str(resolution["destination_class"])
            params["destination_parameter"] = str(resolution["destination_parameter"])
            params["move_target_resolution"] = "python_ast_semantic_recovery"
            params["source_resolution_status"] = "success"
            params["requested_move_target"] = requested
            params.pop("source_resolution_error", None)

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

            semantic_matches: list[tuple[SourceFileContract, str, str, bool]] = []
            ambiguous_within_file = False
            for entry in candidates:
                count = cls._extract_method_target_count(
                    entry,
                    method_name=method_name,
                    source_class=source_class,
                    signature=signature,
                )
                if count == 1:
                    semantic_matches.append((entry, method_name, source_class, False))
                elif count > 1:
                    ambiguous_within_file = True
                else:
                    recovered_target = cls._recover_python_extract_method_target(
                        entry,
                        method_name=method_name,
                        source_class=source_class,
                        signature=signature,
                        parameters=params,
                    )
                    if recovered_target is not None:
                        recovered_method, recovered_class = recovered_target
                        semantic_matches.append(
                            (entry, recovered_method, recovered_class, True)
                        )

            if len(semantic_matches) == 1 and not ambiguous_within_file:
                resolved_entry, resolved_method, resolved_class, recovered = semantic_matches[0]
                params["source_file"] = resolved_entry.file_name
                params["method"] = resolved_method
                params["source_class"] = resolved_class
                if recovered:
                    # RDP sometimes supplies a Python file/module name in the
                    # class field, or a stale method label while the source
                    # range still points inside one real function. The AST is
                    # authoritative for this recovery, never a filename guess.
                    params.pop("method_signature", None)
                    params.pop("function_signature", None)
                    params.pop("signature", None)
                    params["method_target_resolution"] = (
                        "python_ast_semantic_recovery"
                    )
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

    @staticmethod
    def _recover_python_extract_method_target(
        file_entry: SourceFileContract,
        *,
        method_name: str,
        source_class: str,
        signature: str,
        parameters: Dict[str, Any],
    ) -> tuple[str, str] | None:
        """Recover one real Python routine from stale RDP class/name metadata.

        Recovery is intentionally restricted to an unambiguous AST match. It
        first checks the requested routine name without the accidental module
        name-as-class constraint, then uses an RDP range only when exactly one
        supported top-level/class method contains that range.
        """

        language = (file_entry.language or "").strip().lower()
        if language != "python" and not file_entry.file_name.lower().endswith(".py"):
            return None
        try:
            import ast

            tree = ast.parse(file_entry.source_code)
        except SyntaxError:
            return None

        candidates: list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                candidates.append((node.name, "", node))
            elif isinstance(node, ast.ClassDef):
                candidates.extend(
                    (member.name, node.name, member)
                    for member in node.body
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                )

        # Do not trust a file stem/module label as a Python class. A routine
        # name remains a safe identity if it occurs exactly once in this file.
        named = [item for item in candidates if item[0] == method_name]
        if len(named) == 1:
            return named[0][0], named[0][1]

        def as_line(value: Any) -> int | None:
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
            return None

        start_line = as_line(
            parameters.get("start_line")
            or parameters.get("source_line")
            or parameters.get("line")
        )
        end_line = as_line(parameters.get("end_line")) or start_line
        if start_line is None or end_line is None:
            return None

        def is_substantial(
            item: tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef],
        ) -> bool:
            node = item[2]
            statement_count = sum(
                isinstance(descendant, ast.stmt)
                and descendant is not node
                and not isinstance(descendant, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                for descendant in ast.walk(node)
            )
            loc = int(getattr(node, "end_lineno", node.lineno) or node.lineno) - node.lineno + 1
            return statement_count >= 4 and loc >= 10

        enclosing = [
            item
            for item in candidates
            if is_substantial(item)
            and int(getattr(item[2], "lineno", 0) or 0) <= start_line
            and int(getattr(item[2], "end_lineno", item[2].lineno) or item[2].lineno) >= end_line
        ]
        if len(enclosing) == 1:
            return enclosing[0][0], enclosing[0][1]

        # Smell locations sometimes include a module docstring or the whole
        # file, so no function encloses both endpoints. Select by overlap only
        # when one routine is clearly dominant over every other routine.
        overlaps: list[tuple[int, tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef]]] = []
        for item in candidates:
            if not is_substantial(item):
                continue
            node_start = int(getattr(item[2], "lineno", 0) or 0)
            node_end = int(getattr(item[2], "end_lineno", node_start) or node_start)
            overlap = max(0, min(node_end, end_line) - max(node_start, start_line) + 1)
            if overlap:
                overlaps.append((overlap, item))
        overlaps.sort(key=lambda entry: entry[0], reverse=True)
        if overlaps and (
            len(overlaps) == 1
            or overlaps[0][0] >= max(3, overlaps[1][0] * 2)
        ):
            winner = overlaps[0][1]
            return winner[0], winner[1]

        # Final recovery for plans with no usable line metadata: a file may
        # contain one real business routine plus tiny entry-point wrappers.
        # Require exactly one substantial routine so this cannot silently pick
        # between multiple legitimate methods.
        substantial = [item for item in candidates if is_substantial(item)]
        if len(substantial) == 1:
            return substantial[0][0], substantial[0][1]
        return None

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
                elif language == "c" or lower_name.endswith((".c", ".h")):
                    if c_transformers.c_function_definition_count(entry.source_code, method) == 1:
                        matches.append((entry, cls._file_stem(entry.file_name)))

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
                lower_name = entry.file_name.lower()
                if lower_name.endswith(".java"):
                    language = "java"
                elif lower_name.endswith(".py"):
                    language = "python"
                elif lower_name.endswith((".c", ".h")):
                    language = "c"
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

    @classmethod
    def _mark_unresolved_legacy_actions(
        cls,
        actions: List[RefactoringAction],
    ) -> None:
        """Mark targetless compatibility actions without removing plan intent."""

        for action in actions:
            params = action.parameters
            resolution_error = str(params.get("source_resolution_error") or "").strip()
            resolution_status = str(params.get("source_resolution_status") or "").strip().lower()
            promoted = params.get("promoted_from_noop") is True
            targetless_bare_except = (
                action.action_type == ACTION_NARROW_EXCEPTION_HANDLER
                and not str(params.get("original_exception_type") or "").strip()
                and not str(
                    params.get("source_method")
                    or params.get("method")
                    or params.get("method_name")
                    or ""
                ).strip()
                and not isinstance(params.get("source_line"), (int, float))
            )
            if targetless_bare_except:
                params["unresolved_legacy_target"] = True
                params["unresolved_action_type"] = action.action_type
                params["unresolved_reason"] = "BARE_EXCEPT_TARGET_MISSING_FROM_PLAN"
                params["unresolved_status"] = "review_required"
                action.warnings = [
                    warning
                    for warning in action.warnings
                    if "mapped to noop" not in str(warning).lower()
                ]
                continue
            stale_extract_method = False
            if action.action_type == ACTION_EXTRACT_METHOD and resolution_error:
                source_file = cls._action_source_file(action)
                file_stem = cls._file_stem(source_file)
                method = str(
                    params.get("method")
                    or params.get("method_name")
                    or params.get("function")
                    or params.get("function_name")
                    or ""
                ).strip()
                source_class = str(
                    params.get("source_class")
                    or params.get("class_name")
                    or params.get("module_name")
                    or ""
                ).strip()

                def normalize(value: str) -> str:
                    return "".join(
                        character.lower()
                        for character in value
                        if character.isalnum()
                    )

                stale_extract_method = bool(file_stem) and (
                    not method
                    or normalize(method) == normalize(file_stem)
                    or normalize(source_class) == normalize(file_stem)
                )

            if not resolution_error or not (
                promoted
                or stale_extract_method
                or resolution_status in {"not_applicable", "review_required"}
            ):
                continue

            params["unresolved_legacy_target"] = True
            params["unresolved_action_type"] = action.action_type
            params["unresolved_reason"] = resolution_error
            params["unresolved_status"] = resolution_status or "not_applicable"
            action.warnings = [
                warning
                for warning in action.warnings
                if not (
                    "richer semantic edits" in str(warning).lower()
                    or "not simulated" in str(warning).lower()
                    or "mapped to noop" in str(warning).lower()
                )
            ]

    @staticmethod
    def _apply_local_target_recovery(
        *,
        plan_actions: List[RefactoringAction],
        local_actions: List[RefactoringAction],
    ) -> List[RefactoringAction]:
        """Recover stale legacy targets or classify them as not applicable.

        RDP occasionally emits compatibility actions whose target is derived
        from the filename rather than from a real method/class in the current
        source.  Those actions must not be executed against a different source
        node and must not be surfaced as transformation failures.

        If SCTVA's local detector can prove a target for the same refactoring
        type, the planned action is repaired with that semantic target.
        Otherwise the compatibility action is preserved as plan evidence but
        marked ``not_applicable_to_source``.  The transformation engine records
        it as ``status=not_applicable`` without producing a safety warning.
        """

        remaining = list(local_actions)
        for planned in plan_actions:
            if planned.parameters.get("unresolved_legacy_target") is not True:
                continue

            # A local symbol detector may repair a stale method/class target,
            # but it must never choose between ambiguous or absent source
            # files.  Keep the original action as review evidence and let the
            # engine apply zero replacements.
            if planned.parameters.get("source_file_resolution_error"):
                continue

            match_index = next(
                (
                    index
                    for index, detected in enumerate(remaining)
                    if detected.action_type == planned.action_type
                ),
                None,
            )

            if match_index is None:
                params = planned.parameters
                reason = str(
                    params.get("unresolved_reason")
                    or params.get("source_resolution_error")
                    or "TARGET_NOT_FOUND_IN_SOURCE"
                )
                params["not_applicable_to_source"] = True
                params["not_applicable_reason"] = reason
                params["not_applicable_action_type"] = planned.action_type
                existing_resolution = params.get("move_method_target_resolution")
                if isinstance(existing_resolution, dict):
                    # Preserve the AST decision made by the language-specific
                    # resolver.  Replacing it with a generic stale-target
                    # marker loses useful classification such as MODULE_FUNCTION
                    # and MOVE_FUNCTION in the final safety report.
                    params["target_resolution"] = existing_resolution
                else:
                    params["target_resolution"] = {
                        "status": "not_applicable",
                        "strategy": "stale_rdp_target_guard",
                        "reason": reason,
                    }
                params.pop("unresolved_legacy_target", None)

                # Compatibility warnings from old adapters are implementation
                # details, not safety failures for the current source.
                planned.warnings = [
                    warning
                    for warning in planned.warnings
                    if not (
                        "richer semantic edits" in str(warning).lower()
                        or "not simulated" in str(warning).lower()
                        or "mapped to noop" in str(warning).lower()
                    )
                ]
                continue

            detected = remaining.pop(match_index)
            requested = dict(planned.parameters)
            planned.parameters.clear()
            planned.parameters.update(detected.parameters)
            planned.parameters["target_resolution"] = {
                "status": "success",
                "strategy": "sctva_local_semantic_recovery",
                "requested": requested,
            }
            planned.warnings = [
                *detected.warnings,
                "SCTVA recovered the missing RDP target from the current source file.",
            ]

        return remaining

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
            if str(metadata.get("status") or "").lower() == "not_applicable":
                metadata["final_checks"] = {
                    "plan_compliance": "NOT_APPLICABLE",
                    "structural_refactoring": "NOT_APPLICABLE",
                    "behavior_preservation": "PASS" if behavioral_passed else "FAIL",
                    "full_api_preservation": "NOT_APPLICABLE",
                    "state_compatibility": "NOT_APPLICABLE",
                    "single_state_owner": "NOT_APPLICABLE",
                    "large_class_reduction": "NOT_APPLICABLE",
                    "invariant_preservation": "PASS" if invariant_passed else "FAIL",
                }
                metadata["behavioral_safety"] = metadata["final_checks"][
                    "behavior_preservation"
                ]
                metadata["final_status"] = "NOT_APPLICABLE"
                metadata["final_decision"] = "NOT_APPLICABLE"
                continue
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
            if str(metadata.get("status") or "").lower() == "not_applicable":
                metadata["final_checks"] = {
                    "plan_compliance": "NOT_APPLICABLE",
                    "extract_method_structural_validation": "NOT_APPLICABLE",
                    "long_method_reduction": "NOT_APPLICABLE",
                    "behavior_preservation": "PASS" if behavioral_passed else "FAIL",
                    "compilation_syntax_validation": "PASS" if syntax_passed else "FAIL",
                    "invariant_preservation": "PASS" if invariant_passed else "FAIL",
                    "no_severe_new_smell": "NOT_APPLICABLE",
                }
                metadata["syntax"] = metadata["final_checks"]["compilation_syntax_validation"]
                metadata["behavior"] = metadata["final_checks"]["behavior_preservation"]
                metadata["smell_reduction"] = "NOT_APPLICABLE"
                metadata["final_status"] = "NOT_APPLICABLE"
                metadata["final_decision"] = "NOT_APPLICABLE"
                continue
            internal = metadata.get("validation") or {}
            checks = {
                "plan_compliance": "PASS" if metadata.get("plan_compliance") == "PASS" else "FAIL",
                "extract_method_structural_validation": (
                    "PASS"
                    if structural_passed
                    and internal.get("target_resolution") == "PASS"
                    and internal.get("data_flow") == "PASS"
                    and internal.get("structural") == "PASS"
                    and internal.get("scope_validation", "PASS") == "PASS"
                    and internal.get("compilation_validation", "PASS") == "PASS"
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
            if str(entry.metadata.get("status") or "").lower() == "not_applicable":
                entry.metadata["final_decision"] = "NOT_APPLICABLE"
            elif rollback_occurred:
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
        dead_code_validation: List[Dict[str, Any]] | None = None,
    ) -> None:
        """Make skipped or unsafe dead-code actions explicit in the report."""

        validation_iter = iter(dead_code_validation or [])
        for entry in transformation_log:
            if entry.action_type != ACTION_REMOVE_DEAD_CODE:
                continue

            validation = next(validation_iter, None) if entry.replacements_count > 0 else None
            if isinstance(validation, dict):
                validation_status = str(validation.get("status") or "").upper()
                entry.metadata["dead_code_validation"] = validation_status
                for key in (
                    "original_function_count",
                    "transformed_function_count",
                    "expected_removed",
                ):
                    if key in validation:
                        entry.metadata[key] = validation[key]
                if not entry.metadata.get("target"):
                    entry.metadata["target"] = str(validation.get("target") or "")
                ledger = entry.metadata.get("dead_code_removal_ledger_entry")
                if isinstance(ledger, dict):
                    ledger["validation_result"] = validation_status

            # A planner step can incorrectly point at a live/referenced method.
            # The transformation engine records that as NOT_APPLICABLE.  This
            # is a successful safety decision, not a failed transformation, so
            # do not overwrite it as REVIEW_REQUIRED during finalization.
            if (
                str(entry.metadata.get("status") or "").lower() == "not_applicable"
                or entry.metadata.get("dead_code_target_status") == "live"
            ):
                entry.metadata["checks"] = {
                    "target_was_proven_dead": False,
                    "live_target_preserved": True,
                    "syntax_validation": syntax_passed,
                    "structural_validation": structural_passed,
                    "behavior_preservation": behavioral_passed,
                    "invariant_preservation": invariant_passed,
                }
                entry.metadata["final_checks"] = {
                    "target_was_proven_dead": "NOT_APPLICABLE",
                    "live_target_preserved": "PASS",
                    "syntax_validation": "PASS" if syntax_passed else "FAIL",
                    "structural_validation": "PASS" if structural_passed else "FAIL",
                    "behavior_preservation": "PASS" if behavioral_passed else "FAIL",
                    "invariant_preservation": "PASS" if invariant_passed else "FAIL",
                }
                entry.metadata["status"] = "not_applicable"
                entry.metadata["final_decision"] = "NOT_APPLICABLE"
                continue

            if str(entry.metadata.get("status") or "").lower() == "already_handled":
                entry.metadata["checks"] = {
                    "target_already_handled": True,
                    "syntax_validation": syntax_passed,
                    "structural_validation": structural_passed,
                    "behavior_preservation": behavioral_passed,
                    "invariant_preservation": invariant_passed,
                }
                entry.metadata["final_checks"] = {
                    key: "PASS" if value else "FAIL"
                    for key, value in entry.metadata["checks"].items()
                }
                entry.metadata["final_decision"] = "ALREADY_HANDLED"
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
                entry.metadata["target_removed"] = True
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

    def _resolve_c_global_variable_plan_actions(
        self,
        actions: List[RefactoringAction],
        *,
        file_entry: SourceFileContract,
        fallback_language: str,
    ) -> None:
        """Resolve malformed C Encapsulate Variable planner targets safely.

        Some RDP plans describe each C Global Variable action with the generic
        values ``variable``, ``get_variable`` and ``set_variable``.  Passing
        those strings directly to the C transformer inevitably produces
        GLOBAL_DECLARATION_NOT_FOUND_OR_AMBIGUOUS.

        SCTVA already has a conservative local C Global Variable detector.  We
        reuse that detector here and pair unresolved planner actions with the
        proven mutable scalar globals in declaration order *only when the
        mapping is unambiguous*.  This happens before local actions are merged,
        which also prevents duplicate detector actions from being appended.
        """

        language = (file_entry.language or fallback_language or "").strip().lower()
        if language != "c" or file_entry.source_mode != "raw" or not actions:
            return

        encapsulate_actions = [
            action
            for action in actions
            if action.action_type in {
                ACTION_ENCAPSULATE_C_VARIABLE,
                ACTION_ENCAPSULATE_VARIABLE,
            }
            or str(action.source_refactoring or "").strip().lower()
            in {"encapsulate variable", "global variable"}
        ]
        if not encapsulate_actions:
            return

        detected = [
            action
            for action in self.local_refactor_detector.detect(
                language="c",
                file_name=file_entry.file_name,
                source_code=file_entry.source_code,
                existing_actions=[],
            )
            if action.action_type == ACTION_ENCAPSULATE_C_VARIABLE
        ]
        if not detected:
            return

        detected.sort(
            key=lambda action: (
                int(action.parameters.get("source_line") or 10**9),
                str(action.parameters.get("variable_name") or ""),
            )
        )
        candidates_by_name = {
            str(action.parameters.get("variable_name") or "").strip(): action
            for action in detected
            if str(action.parameters.get("variable_name") or "").strip()
        }

        generic_names = {
            "variable",
            "global",
            "global_variable",
            "globalvariable",
            "var",
            "value",
        }
        resolved_names: set[str] = set()
        unresolved: list[tuple[int, RefactoringAction, str]] = []

        for order, action in enumerate(encapsulate_actions):
            params = action.parameters
            requested_name = str(
                params.get("variable_name")
                or params.get("variable")
                or ""
            ).strip()
            candidate = candidates_by_name.get(requested_name)
            if candidate is None:
                unresolved.append((order, action, requested_name))
                continue

            detected_params = candidate.parameters
            action.action_type = ACTION_ENCAPSULATE_C_VARIABLE
            params["source_file"] = file_entry.file_name
            params["source_line"] = detected_params.get("source_line")
            if not str(params.get("getter_name") or "").strip() or str(
                params.get("getter_name")
            ).strip().lower() in {"get_variable", "get_global", "get_var"}:
                params["getter_name"] = detected_params.get("getter_name")
            if not str(params.get("setter_name") or "").strip() or str(
                params.get("setter_name")
            ).strip().lower() in {"set_variable", "set_global", "set_var"}:
                params["setter_name"] = detected_params.get("setter_name")
            params["target_resolution"] = {
                "status": "success",
                "strategy": "exact_source_global",
                "requested_variable_name": requested_name,
                "variable_name": requested_name,
                "source_line": detected_params.get("source_line"),
            }
            resolved_names.add(requested_name)

        if not unresolved:
            return

        available = [
            candidate
            for candidate in detected
            if str(candidate.parameters.get("variable_name") or "").strip()
            not in resolved_names
        ]

        # The safe fallback for generic placeholders is declaration-order
        # pairing only when the cardinalities match exactly.  The planner's
        # line numbers can be offset by comments/imports, so nearest-line
        # matching alone is not reliable for this case.
        if len(unresolved) != len(available):
            return

        unresolved.sort(
            key=lambda item: (
                int(item[1].parameters.get("source_line") or 10**9),
                item[0],
            )
        )
        available.sort(
            key=lambda action: int(action.parameters.get("source_line") or 10**9)
        )

        for (_, action, requested_name), candidate in zip(unresolved, available):
            params = action.parameters
            detected_params = candidate.parameters
            resolved_name = str(detected_params.get("variable_name") or "").strip()
            if not resolved_name:
                continue

            # Do not silently remap a concrete, different C identifier.  This
            # fallback is intended for known planner placeholders (or an empty
            # target) only.
            if requested_name and requested_name.lower() not in generic_names:
                continue

            requested_getter = str(params.get("getter_name") or "").strip()
            requested_setter = str(params.get("setter_name") or "").strip()
            action.action_type = ACTION_ENCAPSULATE_C_VARIABLE
            params["requested_variable_name"] = requested_name
            params["variable_name"] = resolved_name
            params["source_file"] = file_entry.file_name
            params["source_line"] = detected_params.get("source_line")
            params["getter_name"] = (
                str(detected_params.get("getter_name") or f"get_{resolved_name}")
                if not requested_getter
                or requested_getter.lower() in {"get_variable", "get_global", "get_var"}
                else requested_getter
            )
            params["setter_name"] = (
                str(detected_params.get("setter_name") or f"set_{resolved_name}")
                if not requested_setter
                or requested_setter.lower() in {"set_variable", "set_global", "set_var"}
                else requested_setter
            )
            params["target_resolution"] = {
                "status": "success",
                "strategy": "declaration_order_placeholder_recovery",
                "requested_variable_name": requested_name,
                "variable_name": resolved_name,
                "source_line": detected_params.get("source_line"),
            }

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
        if language == "python" and detected_actions:
            # RDP and SCTVA's local detector can identify the same bare
            # handler using different line numbers after prior refactorings.
            # Resolve both against this AST and retain the RDP action only.
            from .transformers import python_transformers

            planned_bare_targets: set[tuple[str, int]] = set()
            for planned in existing_actions:
                if planned.action_type != ACTION_NARROW_EXCEPTION_HANDLER:
                    continue
                params = planned.parameters or {}
                if str(params.get("original_exception_type") or "").strip():
                    continue
                raw_line = params.get("source_line")
                resolved = python_transformers.resolve_bare_exception_handler(
                    file_entry.source_code,
                    source_line=int(raw_line) if isinstance(raw_line, (int, float)) else None,
                    source_class=str(
                        params.get("source_class") or params.get("class_name") or ""
                    ).strip(),
                    source_method=str(
                        params.get("source_method") or params.get("method") or ""
                    ).strip(),
                    handler_name=str(params.get("handler_name") or "").strip(),
                    target_exception_type=str(
                        params.get("target_exception_type") or ""
                    ).strip(),
                    require_specific_exception=False,
                )
                if resolved.get("status") == "success":
                    planned_bare_targets.add((
                        str(resolved.get("qualified_source_method") or ""),
                        int(resolved.get("resolved_handler_line") or 0),
                    ))

            if planned_bare_targets:
                retained_actions: list[RefactoringAction] = []
                for detected in detected_actions:
                    if (
                        detected.action_type != ACTION_NARROW_EXCEPTION_HANDLER
                        or detected.source_refactoring != "SCTVA Internal Analysis"
                        or str(
                            (detected.parameters or {}).get("original_exception_type") or ""
                        ).strip()
                    ):
                        retained_actions.append(detected)
                        continue
                    params = detected.parameters or {}
                    raw_line = params.get("source_line")
                    resolved = python_transformers.resolve_bare_exception_handler(
                        file_entry.source_code,
                        source_line=int(raw_line) if isinstance(raw_line, (int, float)) else None,
                        source_class=str(
                            params.get("source_class") or params.get("class_name") or ""
                        ).strip(),
                        source_method=str(
                            params.get("source_method") or params.get("method") or ""
                        ).strip(),
                        handler_name=str(params.get("handler_name") or "").strip(),
                        target_exception_type=str(params.get("target_exception_type") or "").strip(),
                        require_specific_exception=False,
                    )
                    identity = (
                        str(resolved.get("qualified_source_method") or ""),
                        int(resolved.get("resolved_handler_line") or 0),
                    )
                    if resolved.get("status") != "success" or identity not in planned_bare_targets:
                        retained_actions.append(detected)
                detected_actions = retained_actions
        # Only the actions already scoped to this file are authoritative here.
        # Using every action from the request leaks refactoring types from other
        # files into the current file when a multi-file request is processed.
        planned_types = {
            action.action_type
            for action in existing_actions
        }
        planned_types.discard(ACTION_NOOP)

        # Move Method needs relationship analysis rather than a simple smell
        # threshold.  Reuse the same conservative resolver as the transformer
        # when a legacy RDP action lost its method/class target.
        if (
            language == "python"
            and ACTION_MOVE_PYTHON_METHOD in planned_types
            and not any(action.action_type == ACTION_MOVE_PYTHON_METHOD for action in detected_actions)
        ):
            from .transformers import python_transformers

            stale_move = next(
                (
                    action
                    for action in existing_actions
                    if action.action_type == ACTION_MOVE_PYTHON_METHOD
                    and action.parameters.get("unresolved_legacy_target") is True
                ),
                None,
            )
            source_line = stale_move.parameters.get("source_line") if stale_move else None
            source_line = int(source_line) if isinstance(source_line, (int, float)) else None
            resolution = python_transformers.resolve_move_method_target(
                file_entry.source_code,
                source_line=source_line,
            )
            if resolution.get("status") == "success":
                detected_actions.append(RefactoringAction(
                    action_type=ACTION_MOVE_PYTHON_METHOD,
                    source_refactoring="Move Method",
                    parameters={
                        "source_file": file_entry.file_name,
                        "method": str(resolution["method"]),
                        "source_class": str(resolution["source_class"]),
                        "destination_class": str(resolution["destination_class"]),
                        "destination_parameter": str(resolution["destination_parameter"]),
                        "move_target_resolution": "local_python_feature_envy_analysis",
                    },
                ))
        if not planned_types:
            return self._deduplicate_internal_actions(
                file_name=file_entry.file_name,
                existing_actions=existing_actions,
                detected_actions=detected_actions,
            )

        # The dedicated polymorphism transformer performs its own AST target
        # recovery.  Do not append a second locally detected action when RDP
        # already requested this refactoring, otherwise the second action
        # would run after the conditional has already been replaced.
        if ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM in planned_types:
            detected_actions = [
                action
                for action in detected_actions
                if action.action_type != ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM
            ]

        # A non-empty RDP plan is authoritative.  SCTVA may supplement a
        # requested action type (for example, locate additional proven-dead
        # Python statements), but it must not introduce unrelated constants,
        # string rewrites, or smell refactorings that the plan did not ask for.
        planned_literals: set[Any] = set()
        for action in existing_actions:
            if action.action_type not in {"extract_constant", "introduce_constant"}:
                continue
            if "literal_value" in action.parameters:
                planned_literals.add(action.parameters["literal_value"])
            values = action.parameters.get("literal_values")
            if isinstance(values, list):
                planned_literals.update(values)

        detected_actions = [
            action
            for action in detected_actions
            if action.action_type in planned_types
            and (
                action.action_type not in {"extract_constant", "introduce_constant"}
                or not planned_literals
                or action.parameters.get("literal_value") in planned_literals
            )
        ]
        return self._deduplicate_internal_actions(
            file_name=file_entry.file_name,
            existing_actions=existing_actions,
            detected_actions=detected_actions,
        )

    @staticmethod
    def _canonical_refactoring_family(action: RefactoringAction) -> str:
        """Normalize display and technical action names for target deduplication."""

        for value in (action.action_type, action.source_refactoring):
            normalized = re.sub(
                r"[^a-z0-9]+",
                "_",
                str(value or "").strip().lower(),
            ).strip("_")
            if normalized in {
                "extract_method",
                "extract_function",
                "extract_c_method",
                "extract_java_method",
                "extract_python_method",
            }:
                return ACTION_EXTRACT_METHOD
        return str(action.action_type or "").strip().lower()

    @staticmethod
    def _action_target_method(action: RefactoringAction) -> str:
        """Get the normalized routine name from modern or legacy action data."""

        params = action.parameters or {}
        target = params.get("target")
        target = target if isinstance(target, dict) else {}
        for key in (
            "source_method",
            "method",
            "method_name",
            "function",
            "function_name",
        ):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            value = target.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @classmethod
    def _dedup_file_identity(cls, action: RefactoringAction, file_name: str) -> str:
        """Resolve only exact/suffix path aliases; never merge sibling files."""

        action_path = cls._normalize_path(cls._action_source_file(action))
        current_path = cls._normalize_path(file_name)
        if not action_path:
            return current_path
        if (
            action_path == current_path
            or current_path.endswith(f"/{action_path}")
            or action_path.endswith(f"/{current_path}")
        ):
            return current_path
        return action_path

    @classmethod
    def _internal_action_deduplication_key(
        cls,
        action: RefactoringAction,
        *,
        file_name: str,
    ) -> tuple[str, str, str] | None:
        family = cls._canonical_refactoring_family(action)
        method = cls._action_target_method(action)
        if family != ACTION_EXTRACT_METHOD or not method:
            return None
        return (cls._dedup_file_identity(action, file_name), family, method)

    @classmethod
    def _deduplicate_internal_actions(
        cls,
        *,
        file_name: str,
        existing_actions: List[RefactoringAction],
        detected_actions: List[RefactoringAction],
    ) -> List[RefactoringAction]:
        """Remove duplicate local actions without suppressing new SCTVA findings."""

        planned_keys = {
            key
            for action in existing_actions
            if (key := cls._internal_action_deduplication_key(
                action,
                file_name=file_name,
            )) is not None
        }
        retained: List[RefactoringAction] = []
        seen_local_keys: set[tuple[str, str, str]] = set()

        for action in detected_actions:
            key = cls._internal_action_deduplication_key(action, file_name=file_name)
            if key is not None and (key in planned_keys or key in seen_local_keys):
                continue
            if key is not None:
                seen_local_keys.add(key)
            retained.append(action)
        return retained

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
        logs_by_action_index = {
            entry.action_index: entry for entry in transformation_log
        }
        for index, action in enumerate(actions, start=1):
            log_entry = logs_by_action_index.get(index)
            if log_entry is None:
                continue
            satisfied_inline_class = (
                action.action_type == ACTION_INLINE_PYTHON_CLASS
                and str(log_entry.metadata.get("reason") or "")
                == "SMELL_RESOLVED_BY_PRIOR_REFACTORING"
                and str(log_entry.metadata.get("plan_compliance") or "") == "PASS"
            )
            if log_entry.replacements_count <= 0 and not satisfied_inline_class:
                continue

            effective_type = str(
                log_entry.metadata.get("reclassified_action_type")
                or action.action_type
            ).strip()

            # Plain noops are ignored.  A noop that was safely reclassified by
            # the engine (for example legacy Move Method) must be validated as
            # the operation that was actually performed.
            if effective_type == ACTION_NOOP:
                continue

            effective_parameters = log_entry.metadata.get("effective_action_parameters")
            if isinstance(effective_parameters, dict):
                validation_parameters = dict(effective_parameters)
                validation_parameters["_sctva_action_index"] = index
                validation_parameters["applied_transformation_metadata"] = dict(
                    log_entry.metadata
                )
                effective_actions.append(
                    RefactoringAction(
                        action_type=effective_type,
                        parameters=validation_parameters,
                        source_step_id=action.source_step_id,
                        source_refactoring=action.source_refactoring,
                        warnings=list(action.warnings),
                    )
                )
                continue

            if effective_type == action.action_type:
                validation_parameters = dict(action.parameters or {})
                validation_parameters["_sctva_action_index"] = index
                validation_parameters["applied_transformation_metadata"] = dict(
                    log_entry.metadata
                )
                effective_actions.append(
                    RefactoringAction(
                        action_type=effective_type,
                        parameters=validation_parameters,
                        source_step_id=action.source_step_id,
                        source_refactoring=action.source_refactoring,
                        warnings=list(action.warnings),
                    )
                )
                continue

            validation_parameters = dict(action.parameters or {})
            validation_parameters["_sctva_action_index"] = index
            validation_parameters["applied_transformation_metadata"] = dict(
                log_entry.metadata
            )
            effective_actions.append(
                RefactoringAction(
                    action_type=effective_type,
                    parameters=validation_parameters,
                    source_step_id=action.source_step_id,
                    source_refactoring=action.source_refactoring,
                    warnings=list(action.warnings),
                )
            )
        return effective_actions

    def _attempt_selective_java_parameter_object_replay(
        self,
        *,
        request: SCTVARequestContract,
        file_entry: SourceFileContract,
        actions: List[RefactoringAction],
        project_files: List[Dict[str, Any]],
        structural_step: ValidationStepResult,
        rollback_occurred: bool,
    ) -> tuple[Any, ...] | None:
        """Replay independent accepted Java actions after a proven PO failure.

        This is deliberately narrow: it only removes an action that the
        Parameter Object structural validator identified as failing.  If the
        remaining sequence cannot pass all validation stages, SCTVA keeps the
        existing whole-file rollback rather than guessing at dependencies.
        """
        language = (file_entry.language or request.language).strip().lower()
        if not rollback_occurred or language != "java":
            return None
        if any(action.parameters.get("coordinated_project_transaction_id") for action in actions):
            return None
        checks = list(structural_step.details.get("parameter_object_validation") or [])
        failed_indexes = [
            int(item.get("action_index"))
            for item in checks
            if item.get("passed") is False and isinstance(item.get("action_index"), int)
        ]
        if not failed_indexes:
            return None
        parameter_indexes = [
            index for index, action in enumerate(actions, start=1)
            if action.action_type == ACTION_INTRODUCE_JAVA_PARAMETER_OBJECT
        ]
        if len(parameter_indexes) < 2:
            return None

        for failed_index in failed_indexes:
            retained = [
                copy.deepcopy(action)
                for index, action in enumerate(actions, start=1)
                if index != failed_index
            ]
            if not retained:
                continue
            candidate, logs, warnings = self.transformer.apply_actions(
                language=language,
                source_code=file_entry.source_code,
                actions=retained,
                strict_mode=request.execution_options.strict_mode,
                project_source_files=project_files,
                current_file_name=file_entry.file_name,
                repository_complete=bool(request.source_files),
                behavior_tests=request.refactoring_plan.behavior_tests,
            )
            effective = self._actions_with_effective_replacements(retained, logs)
            syntax = self.syntax_validator.validate(
                language=language,
                source_code=candidate,
                require_compilation=request.execution_options.require_compilation,
                timeout_seconds=request.execution_options.timeout_seconds,
            )
            structural = self.structural_validator.validate(
                language=language,
                original_code=file_entry.source_code,
                transformed_code=candidate,
                actions=effective,
            )
            behavioral = self.behavioral_validator.validate(
                language=language,
                original_code=file_entry.source_code,
                transformed_code=candidate,
                behavior_tests=request.refactoring_plan.behavior_tests,
                enable_behavior_tests=request.execution_options.enable_behavior_tests,
                actions=effective,
                strict_mode=request.execution_options.strict_mode,
                project_source_files=project_files,
                current_file_name=file_entry.file_name,
                structural_validation_passed=structural.passed,
            )
            invariant = self.invariant_miner.mine(
                language=language,
                behavioral_step=behavioral,
                actions=effective,
                strict_mode=request.execution_options.strict_mode,
            )
            if all(step.passed for step in (syntax, structural, behavioral, invariant)):
                for entry in logs:
                    entry.metadata.setdefault("selective_replay", True)
                    entry.metadata.setdefault("selectively_rolled_back_action", failed_index)
                return candidate, logs, warnings, effective, syntax, structural, behavioral, invariant
        return None

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
