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
class ExecutionOptions:
    """Runtime options for validation strictness and performance."""

    strict_mode: bool = True
    enable_behavior_tests: bool = True
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    require_compilation: bool = False
    rollback_on_behavior_failure: bool = True

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ExecutionOptions":
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ContractValidationError("Field 'execution_options' must be an object.")

        timeout = data.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if not isinstance(timeout, int) or timeout <= 0:
            raise ContractValidationError("Field 'execution_options.timeout_seconds' must be a positive integer.")

        return cls(
            strict_mode=bool(data.get("strict_mode", True)),
            enable_behavior_tests=bool(data.get("enable_behavior_tests", True)),
            timeout_seconds=timeout,
            require_compilation=bool(data.get("require_compilation", False)),
            rollback_on_behavior_failure=bool(data.get("rollback_on_behavior_failure", True)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strict_mode": self.strict_mode,
            "enable_behavior_tests": self.enable_behavior_tests,
            "timeout_seconds": self.timeout_seconds,
            "require_compilation": self.require_compilation,
        }


@dataclass
class SCTVARequestContract:
    """Top-level request payload."""

    request_id: str
    language: str
    source_code: str
    refactoring_plan: RefactoringPlanContract
    execution_options: ExecutionOptions = field(default_factory=ExecutionOptions)

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
        if not isinstance(source_code, str) or not source_code.strip():
            raise ContractValidationError("Field 'source_code' must be a non-empty string.")

        plan = RefactoringPlanContract.from_dict(data.get("refactoring_plan", {}))
        options = ExecutionOptions.from_dict(data.get("execution_options"))

        return cls(
            request_id=request_id,
            language=language,
            source_code=source_code,
            refactoring_plan=plan,
            execution_options=options,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "language": self.language,
            "source_code": self.source_code,
            "refactoring_plan": self.refactoring_plan.to_dict(),
            "execution_options": self.execution_options.to_dict(),
        }
