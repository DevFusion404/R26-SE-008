"""Rollback decision logic."""

from __future__ import annotations

from typing import Iterable, Tuple

from ..models import ValidationStepResult


class RollbackManager:
    """Determines whether rollback is required and why."""

    def evaluate(self, validation_steps: Iterable[ValidationStepResult], rollback_on_behavior_failure: bool = True) -> Tuple[bool, str]:
        # If rollback_on_behavior_failure is False, ignore behavioral validation failures for rollback decision
        failed = [
            step.name
            for step in validation_steps
            if (not step.passed) and (rollback_on_behavior_failure or step.name != "behavioral")
        ]
        if not failed:
            return False, ""
        return True, f"Validation failed at stage(s): {', '.join(failed)}"
