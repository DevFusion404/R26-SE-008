"""Input contracts and validation for SCTVA requests."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .constants import (
    DEFAULT_TIMEOUT_SECONDS,
    SUPPORTED_ACTIONS,
    SUPPORTED_LANGUAGES,
)


class ContractValidationError(ValueError):
    """Raised when input payload violates the expected schema."""


def normalize_move_method_parameters(action: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Move Method fields from every supported RDP/SCTVA shape.

    RDP plans normally store semantic targets under ``step.parameters`` while
    some orchestration layers flatten or partially normalize those steps before
    SCTVA receives them.  Older compatibility actions can also retain the raw
    planner step under ``legacy_step``/``rdp_step``.  Resolve all of those
    locations into one canonical parameter dictionary *before* AST resolution.

    Planner evidence is never authoritative about symbol existence; this
    function only preserves the requested values.  The Python AST resolver is
    still the source of truth for whether the requested class/method actually
    exists.
    """

    parameters = action.get("parameters")
    normalized = dict(parameters) if isinstance(parameters, dict) else {}
    raw_target = action.get("target")
    target = dict(raw_target) if isinstance(raw_target, dict) else {}

    def as_dict(value: Any) -> Dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    # Compatibility/orchestration layers sometimes keep the original RDP step
    # rather than its fields.  Search these containers before declaring a target
    # missing.  Also inspect copies nested inside the existing parameters dict.
    raw_steps: List[Dict[str, Any]] = []
    for key in ("legacy_step", "rdp_step", "planner_step", "raw_step", "source_step"):
        for container in (action, normalized):
            candidate = container.get(key) if isinstance(container, dict) else None
            if isinstance(candidate, dict):
                raw_steps.append(candidate)

    step_parameters = [as_dict(step.get("parameters")) for step in raw_steps]
    step_targets = [as_dict(step.get("target")) for step in raw_steps]

    def first_text(*values: Any) -> str:
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    source_file = first_text(
        action.get("source_file"),
        normalized.get("source_file"),
        target.get("source_file"),
        target.get("file"),
        *(params.get("source_file") for params in step_parameters),
        *(step_target.get("source_file") for step_target in step_targets),
        *(step_target.get("file") for step_target in step_targets),
    )
    source_class = first_text(
        action.get("source_class"),
        normalized.get("source_class"),
        target.get("source_class"),
        target.get("class"),
        *(params.get("source_class") for params in step_parameters),
        *(params.get("class_name") for params in step_parameters),
        *(step_target.get("source_class") for step_target in step_targets),
        *(step_target.get("class") for step_target in step_targets),
    )
    source_method = first_text(
        action.get("source_method"),
        action.get("method"),
        normalized.get("source_method"),
        normalized.get("method"),
        target.get("source_method"),
        target.get("method"),
        target.get("function"),
        target.get("name"),
        *(params.get("source_method") for params in step_parameters),
        *(params.get("method") for params in step_parameters),
        *(params.get("method_name") for params in step_parameters),
        *(step_target.get("source_method") for step_target in step_targets),
        *(step_target.get("method") for step_target in step_targets),
        *(step_target.get("function") for step_target in step_targets),
    )
    destination_class = first_text(
        action.get("destination_class"),
        normalized.get("destination_class"),
        target.get("destination_class"),
        target.get("target_class"),
        target.get("destination_type"),
        *(params.get("destination_class") for params in step_parameters),
        *(params.get("target_class") for params in step_parameters),
        *(params.get("destination_type") for params in step_parameters),
        *(step_target.get("destination_class") for step_target in step_targets),
        *(step_target.get("target_class") for step_target in step_targets),
    )
    destination_parameter = first_text(
        action.get("destination_parameter"),
        normalized.get("destination_parameter"),
        target.get("destination_parameter"),
        target.get("parameter"),
        *(params.get("destination_parameter") for params in step_parameters),
        *(step_target.get("destination_parameter") for step_target in step_targets),
        *(step_target.get("parameter") for step_target in step_targets),
    )

    if source_file:
        normalized["source_file"] = source_file
    if source_class:
        normalized["source_class"] = source_class
    if source_method:
        normalized["method"] = source_method
        normalized["source_method"] = source_method
    if destination_class:
        normalized["destination_class"] = destination_class
    if destination_parameter:
        normalized["destination_parameter"] = destination_parameter

    if not isinstance(normalized.get("source_line"), (int, float)):
        line_sources: List[Dict[str, Any]] = [action, target]
        line_sources.extend(step_parameters)
        line_sources.extend(step_targets)
        for source in line_sources:
            value = source.get("source_line") or source.get("line")
            if isinstance(value, (int, float)):
                normalized["source_line"] = int(value)
                break
            lines = source.get("lines")
            if isinstance(lines, list) and lines and isinstance(lines[0], (int, float)):
                normalized["source_line"] = int(lines[0])
                break

    # Preserve the complete planner range as a hint.  ``source_line`` is the
    # canonical single-line hint used by the resolver, but retaining the
    # original range is important for diagnostics and for composed plans where
    # line numbers shift after an earlier accepted action.
    existing_target_lines = normalized.get("target_lines")
    if not isinstance(existing_target_lines, list) or not any(
        isinstance(value, (int, float)) for value in existing_target_lines
    ):
        line_sources: List[Dict[str, Any]] = [action, target]
        line_sources.extend(step_parameters)
        line_sources.extend(step_targets)
        for source in line_sources:
            lines = source.get("target_lines") or source.get("lines")
            if isinstance(lines, list):
                numeric_lines = [
                    int(value) for value in lines
                    if isinstance(value, (int, float))
                ]
                if numeric_lines:
                    normalized["target_lines"] = numeric_lines
                    break

    # Keep the planner step identity in the action parameters as well as on
    # the action envelope.  This lets the request boundary pair original and
    # normalized actions even when an orchestration layer rebuilt the action.
    if normalized.get("source_step_id") is None:
        for source in [action, *raw_steps]:
            value = source.get("source_step_id") or source.get("step_id")
            if value is not None:
                normalized["source_step_id"] = value
                break

    # Keep the original requested planner values for diagnostics.  They must
    # never silently become empty strings when the raw step actually supplied
    # them.  The AST resolver may later mark them unresolved/not applicable.
    if source_class:
        normalized.setdefault("requested_source_class", source_class)
    if source_method:
        normalized.setdefault("requested_source_method", source_method)
        normalized.setdefault("requested_method", source_method)
    if destination_class:
        normalized.setdefault("requested_destination_class", destination_class)

    return normalized


@dataclass
class RefactoringAction:
    """Single transformation action instruction."""

    action_type: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    source_step_id: Optional[int] = None
    source_refactoring: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Accept planner display names at the agent boundary while keeping one
        # canonical action vocabulary internally.  In particular, an RDP
        # action named ``Remove Dead Code`` must not be rejected before it can
        # reach the language transformer.
        self.action_type = re.sub(
            r"[\s-]+",
            "_",
            str(self.action_type).strip().lower(),
        )
        # RDP has used both the technical action name and the display name for
        # this safety refactoring.  Keep a single internal operation so direct
        # API callers do not lose the action before it reaches the transformer.
        if self.action_type in {
            "replace_bare_except",
            "replace_bare_except_with_specific_exception",
            "replace_bare_except_with_specific_exceptions",
        }:
            self.action_type = "narrow_exception_handler"
        if self.action_type in {
            "replace_nested_conditional_with_guard_clause",
            "replace_conditional_with_guard_clauses",
            "simplify_conditional_loop",
            "guard_clause",
            "guard_clauses",
        }:
            self.action_type = "replace_nested_conditional_with_guard_clauses"
        if self.action_type not in SUPPORTED_ACTIONS:
            raise ContractValidationError(
                f"Unsupported action_type '{self.action_type}'. Supported: {sorted(SUPPORTED_ACTIONS)}"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RefactoringAction":
        if not isinstance(data, dict):
            raise ContractValidationError("Each action must be an object.")
        if "action_type" not in data:
            raise ContractValidationError("Action is missing required field 'action_type'.")

        parameters = data.get("parameters", {})
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            raise ContractValidationError("Action field 'parameters' must be an object.")
        parameters = dict(parameters)

        normalized_action_type = re.sub(
            r"[\s-]+", "_", str(data.get("action_type") or "").strip().lower()
        )
        if normalized_action_type == "move_python_method":
            parameters = normalize_move_method_parameters(data)
        source_refactoring = str(data.get("source_refactoring") or "").strip().lower()
        bare_except_refactorings = {
            "replace bare except with specific exception",
            "replace bare except with specific exceptions",
            "replace bare except",
            "replace_bare_except",
            "replace_bare_except_with_specific_exception",
        }
        guard_clause_refactorings = {
            "replace nested conditional with guard clauses",
            "replace nested conditional with guard clause",
            "replace_conditional_with_guard_clauses",
            "guard clauses",
            "guard clause",
        }
        if normalized_action_type == "noop" and source_refactoring in bare_except_refactorings:
            # Recover plans normalized by an older PlannerAdapter before the
            # request reaches SCTVA.  The engine still decides whether a
            # specific exception is provable from the current source AST.
            data = dict(data)
            data["action_type"] = "narrow_exception_handler"
            parameters.setdefault("original_exception_type", "")
            parameters.setdefault("exception_smell", "bare_except")
            parameters["promoted_from_noop"] = True

            legacy_step = parameters.get("legacy_step")
            legacy_step = legacy_step if isinstance(legacy_step, dict) else {}
            legacy_params = legacy_step.get("parameters")
            legacy_params = legacy_params if isinstance(legacy_params, dict) else {}
            legacy_target = legacy_step.get("target")
            legacy_target = legacy_target if isinstance(legacy_target, dict) else {}
            legacy_location = legacy_step.get("location")
            legacy_location = legacy_location if isinstance(legacy_location, dict) else {}
            direct_target = data.get("target")
            direct_target = direct_target if isinstance(direct_target, dict) else {}

            def first_text(*values: Any) -> str:
                for value in values:
                    if isinstance(value, str) and value.strip():
                        return value.strip()
                return ""

            method = first_text(
                parameters.get("source_method"),
                parameters.get("method"),
                legacy_target.get("method"),
                legacy_target.get("function"),
                legacy_params.get("source_method"),
                legacy_params.get("method"),
                direct_target.get("method"),
                direct_target.get("function"),
            )
            source_class = first_text(
                parameters.get("source_class"),
                parameters.get("class_name"),
                legacy_target.get("class"),
                legacy_params.get("source_class"),
                legacy_params.get("class_name"),
                direct_target.get("class"),
            )
            source_file = first_text(
                parameters.get("source_file"),
                legacy_target.get("file"),
                legacy_target.get("source_file"),
                legacy_params.get("source_file"),
                legacy_location.get("file"),
                legacy_location.get("source_file"),
                direct_target.get("file"),
                direct_target.get("source_file"),
                data.get("source_file"),
            )
            source_line = parameters.get("source_line")
            if not isinstance(source_line, (int, float)):
                for source in (legacy_target, legacy_params, legacy_location, direct_target, data):
                    value = source.get("source_line") if isinstance(source, dict) else None
                    if isinstance(value, (int, float)):
                        source_line = int(value)
                        break
                    lines = source.get("lines") if isinstance(source, dict) else None
                    if isinstance(lines, list) and lines and isinstance(lines[0], (int, float)):
                        source_line = int(lines[0])
                        break

            if method:
                parameters["method"] = method
                parameters["source_method"] = method
            if source_class:
                parameters["class_name"] = source_class
                parameters["source_class"] = source_class
            if source_file:
                parameters["source_file"] = source_file
            if isinstance(source_line, (int, float)):
                parameters["source_line"] = int(source_line)
        if normalized_action_type == "noop" and source_refactoring in guard_clause_refactorings:
            data = dict(data)
            data["action_type"] = "replace_nested_conditional_with_guard_clauses"
            parameters["promoted_from_noop"] = True
            target = data.get("target") if isinstance(data.get("target"), dict) else {}
            legacy = parameters.get("legacy_step") if isinstance(parameters.get("legacy_step"), dict) else {}
            legacy_params = legacy.get("parameters") if isinstance(legacy.get("parameters"), dict) else {}
            legacy_target = legacy.get("target") if isinstance(legacy.get("target"), dict) else {}
            for key in (
                "method", "source_method", "function", "target_function",
                "source_file", "source_line", "start_line", "end_line",
                "target_lines", "target_range", "source_step_id",
            ):
                if parameters.get(key) not in (None, ""):
                    continue
                for container in (target, legacy_params, legacy_target, legacy, data):
                    value = container.get(key) if isinstance(container, dict) else None
                    if value not in (None, ""):
                        parameters[key] = value
                        break
            method = str(
                parameters.get("source_method")
                or parameters.get("method")
                or parameters.get("function")
                or parameters.get("target_function")
                or ""
            ).strip()
            if method:
                parameters["method"] = method
                parameters["source_method"] = method
                parameters["target_function"] = method
            source_file = str(
                parameters.get("source_file")
                or target.get("source_file")
                or target.get("file")
                or legacy_params.get("source_file")
                or legacy_target.get("source_file")
                or legacy_target.get("file")
                or legacy.get("source_file")
                or legacy.get("file")
                or ""
            ).strip()
            if source_file:
                parameters["source_file"] = source_file
            target_lines = parameters.get("target_lines") or parameters.get("lines")
            if not isinstance(target_lines, (list, tuple)):
                target_lines = legacy_target.get("lines") or target.get("lines")
            if isinstance(target_lines, (list, tuple)):
                parameters["target_lines"] = list(target_lines)
                if parameters.get("source_line") in (None, "") and target_lines:
                    first_line = target_lines[0]
                    if isinstance(first_line, (int, float)) or (
                        isinstance(first_line, str) and first_line.strip().isdigit()
                    ):
                        parameters["source_line"] = int(first_line)
        if normalized_action_type in {"inline_class", "inline_python_class"} or (
            normalized_action_type == "noop" and source_refactoring == "inline class"
        ):
            raw_target = data.get("target")
            target = raw_target if isinstance(raw_target, dict) else {}
            requested_target = parameters.get("requested_target")
            requested_target = (
                requested_target if isinstance(requested_target, dict) else {}
            )
            target_class = str(
                parameters.get("class_to_inline")
                or parameters.get("target_class")
                or parameters.get("source_class")
                or parameters.get("class_name")
                or requested_target.get("class_to_inline")
                or target.get("class")
                or data.get("class_to_inline")
                or data.get("target_class")
                or ""
            ).strip()
            source_file = str(
                parameters.get("source_file")
                or requested_target.get("source_file")
                or target.get("source_file")
                or data.get("source_file")
                or ""
            ).strip()
            parameters["class_to_inline"] = target_class
            parameters["target_class"] = target_class
            if source_file:
                parameters["source_file"] = source_file
            parameters["requested_target"] = {
                "class_to_inline": target_class,
                "source_file": source_file,
            }
            if not target_class:
                parameters["target_resolution_error"] = "INLINE_CLASS_TARGET_MISSING"

        warnings = data.get("warnings", [])
        if not isinstance(warnings, list):
            raise ContractValidationError("Action field 'warnings' must be a list when provided.")
        if data["action_type"] == "replace_nested_conditional_with_guard_clauses":
            warnings = [
                warning for warning in warnings
                if not (
                    "guard clause" in str(warning).lower()
                    and (
                        "unsupported" in str(warning).lower()
                        or "mapped to noop" in str(warning).lower()
                    )
                )
            ]

        return cls(
            action_type=data["action_type"],
            parameters=parameters,
            source_step_id=data.get("source_step_id"),
            source_refactoring=data.get("source_refactoring"),
            warnings=[str(w) for w in warnings],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type,
            "parameters": self.parameters,
            "source_step_id": self.source_step_id,
            "source_refactoring": self.source_refactoring,
            "warnings": self.warnings,
        }


@dataclass
class RefactoringPlanContract:
    """Normalized plan accepted by SCTVA."""

    plan_id: str
    actions: List[RefactoringAction]
    behavior_tests: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RefactoringPlanContract":
        if not isinstance(data, dict):
            raise ContractValidationError("Field 'refactoring_plan' must be an object.")

        plan_id = str(data.get("plan_id", "")).strip()
        if not plan_id:
            raise ContractValidationError("Field 'refactoring_plan.plan_id' is required.")

        actions_raw = data.get("actions")
        if not isinstance(actions_raw, list):
            raise ContractValidationError("Field 'refactoring_plan.actions' must be a list.")

        actions = [RefactoringAction.from_dict(item) for item in actions_raw]

        behavior_tests = data.get("behavior_tests", [])
        if behavior_tests is None:
            behavior_tests = []
        if not isinstance(behavior_tests, list):
            raise ContractValidationError("Field 'refactoring_plan.behavior_tests' must be a list.")

        metadata = data.get("metadata", {})
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ContractValidationError("Field 'refactoring_plan.metadata' must be an object.")

        return cls(
            plan_id=plan_id,
            actions=actions,
            behavior_tests=behavior_tests,
            metadata=metadata,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "actions": [a.to_dict() for a in self.actions],
            "behavior_tests": self.behavior_tests,
            "metadata": self.metadata,
        }


@dataclass
class SourceFileContract:
    """Source file content for multi-file transformations."""

    file_name: str
    source_code: str
    language: Optional[str] = None
    source_mode: str = "raw"
    origin: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, index: int) -> "SourceFileContract":
        if not isinstance(data, dict):
            raise ContractValidationError("Each item in 'source_files' must be an object.")

        file_name = str(data.get("file_name") or data.get("name") or f"file_{index}").strip()
        if not file_name:
            raise ContractValidationError("Each source file must include a non-empty file_name.")

        source_code = data.get("source_code", "")
        if not isinstance(source_code, str) or not source_code.strip():
            raise ContractValidationError("Each source file must include non-empty source_code.")

        language = data.get("language")
        if language is not None:
            language = str(language).strip().lower()
            if language and language not in SUPPORTED_LANGUAGES:
                raise ContractValidationError(
                    f"Unsupported language '{language}' in source_files. Supported: {sorted(SUPPORTED_LANGUAGES)}"
                )

        source_mode = str(data.get("source_mode") or data.get("sourceMode") or "raw").strip().lower()
        origin = str(data.get("origin") or "").strip().lower()

        return cls(
            file_name=file_name,
            source_code=source_code,
            language=language or None,
            source_mode=source_mode or "raw",
            origin=origin,
        )

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "file_name": self.file_name,
            "source_code": self.source_code,
            "source_mode": self.source_mode,
        }
        if self.language:
            payload["language"] = self.language
        if self.origin:
            payload["origin"] = self.origin
        return payload


@dataclass
class ExecutionOptions:
    """Runtime options for validation strictness and performance."""

    strict_mode: bool = True
    enable_behavior_tests: bool = True
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    require_compilation: bool = False
    rollback_on_behavior_failure: bool = True
    enable_sctva_auto_refactoring: bool = True
    max_parallel_files: int = 0

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ExecutionOptions":
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ContractValidationError("Field 'execution_options' must be an object.")

        timeout = data.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if not isinstance(timeout, int) or timeout <= 0:
            raise ContractValidationError("Field 'execution_options.timeout_seconds' must be a positive integer.")

        try:
            max_parallel_files = int(data.get("max_parallel_files", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError(
                "Field 'execution_options.max_parallel_files' must be a non-negative integer."
            ) from exc
        if max_parallel_files < 0:
            raise ContractValidationError(
                "Field 'execution_options.max_parallel_files' must be a non-negative integer."
            )

        return cls(
            strict_mode=bool(data.get("strict_mode", True)),
            enable_behavior_tests=bool(data.get("enable_behavior_tests", True)),
            timeout_seconds=timeout,
            require_compilation=bool(data.get("require_compilation", False)),
            rollback_on_behavior_failure=bool(data.get("rollback_on_behavior_failure", True)),
            enable_sctva_auto_refactoring=bool(data.get("enable_sctva_auto_refactoring", True)),
            max_parallel_files=max_parallel_files,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strict_mode": self.strict_mode,
            "enable_behavior_tests": self.enable_behavior_tests,
            "timeout_seconds": self.timeout_seconds,
            "require_compilation": self.require_compilation,
            "rollback_on_behavior_failure": self.rollback_on_behavior_failure,
            "enable_sctva_auto_refactoring": self.enable_sctva_auto_refactoring,
            "max_parallel_files": self.max_parallel_files,
        }


@dataclass
class SCTVARequestContract:
    """Top-level request payload."""

    request_id: str
    language: str
    refactoring_plan: RefactoringPlanContract
    execution_options: ExecutionOptions = field(default_factory=ExecutionOptions)
    source_code: str = ""
    source_files: List[SourceFileContract] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SCTVARequestContract":
        if not isinstance(data, dict):
            raise ContractValidationError("Request payload must be a JSON object.")

        request_id = str(data.get("request_id", "")).strip()
        if not request_id:
            raise ContractValidationError("Field 'request_id' is required.")

        language = str(data.get("language", "")).strip().lower()
        if language not in SUPPORTED_LANGUAGES:
            raise ContractValidationError(
                f"Unsupported language '{language}'. Supported languages: {sorted(SUPPORTED_LANGUAGES)}"
            )

        source_code = data.get("source_code", "")
        source_files_raw = data.get("source_files")
        source_files: List[SourceFileContract] = []

        if source_files_raw is not None:
            if not isinstance(source_files_raw, list):
                raise ContractValidationError("Field 'source_files' must be a list when provided.")
            source_files = [
                SourceFileContract.from_dict(item, index=idx)
                for idx, item in enumerate(source_files_raw, start=1)
            ]

        if not source_files:
            if not isinstance(source_code, str) or not source_code.strip():
                raise ContractValidationError(
                    "Field 'source_code' must be a non-empty string when 'source_files' is not provided."
                )

        if source_files and not isinstance(source_code, str):
            source_code = ""

        if not source_files and isinstance(source_code, str) and not source_code.strip():
            raise ContractValidationError(
                "Field 'source_code' must be a non-empty string when 'source_files' is not provided."
            )

        plan = RefactoringPlanContract.from_dict(data.get("refactoring_plan", {}))
        options = ExecutionOptions.from_dict(data.get("execution_options"))

        return cls(
            request_id=request_id,
            language=language,
            source_code=str(source_code or ""),
            source_files=source_files,
            refactoring_plan=plan,
            execution_options=options,
        )

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "request_id": self.request_id,
            "language": self.language,
            "refactoring_plan": self.refactoring_plan.to_dict(),
            "execution_options": self.execution_options.to_dict(),
        }
        if self.source_files:
            payload["source_files"] = [item.to_dict() for item in self.source_files]
        else:
            payload["source_code"] = self.source_code
        return payload
