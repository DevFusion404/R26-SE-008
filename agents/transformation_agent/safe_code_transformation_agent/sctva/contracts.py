"""Input contracts and validation for SCTVA requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .constants import (
    DEFAULT_TIMEOUT_SECONDS,
    SUPPORTED_ACTIONS,
    SUPPORTED_LANGUAGES,
)


class ContractValidationError(ValueError):
    """Raised when input payload violates the expected schema."""


@dataclass
class RefactoringAction:
    """Single transformation action instruction."""

    action_type: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    source_step_id: Optional[int] = None
    source_refactoring: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.action_type = str(self.action_type).strip().lower()
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

        warnings = data.get("warnings", [])
        if not isinstance(warnings, list):
            raise ContractValidationError("Action field 'warnings' must be a list when provided.")

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
