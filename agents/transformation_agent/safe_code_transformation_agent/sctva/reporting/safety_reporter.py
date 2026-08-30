"""Safety reporting utilities for SCTVA executions."""

from __future__ import annotations

from typing import Dict, List

from ..models import SafetyReport, TransformationLogEntry, ValidationStepResult


class SafetyReporter:
    """Builds machine-readable and human-readable safety reports."""

    def build(
        self,
        *,
        rollback_occurred: bool,
        rollback_reason: str,
        transformation_log: List[TransformationLogEntry],
        validation_steps: List[ValidationStepResult],
        extra_warnings: List[str],
        transformation_applied: bool = True,
        not_applicable: bool = False,
    ) -> SafetyReport:
        validation_timestamps: Dict[str, Dict[str, object]] = {}
        risk_flags: List[str] = []
        human_messages: List[str] = []

        if not transformation_applied and not rollback_occurred:
            if not_applicable:
                human_messages.append(
                    "No source-code change was required; all executable actions were safely classified as not applicable or already satisfied."
                )
            else:
                risk_flags.append("transformation_not_applied")
                human_messages.append(
                    "No source-code change was applied; the requested transformation was not accepted."
                )

        for step in validation_steps:
            validation_timestamps[step.name] = {
                "started_at": step.started_at,
                "finished_at": step.finished_at,
                "duration_ms": step.duration_ms,
            }
            if not step.passed:
                risk_flags.append(f"validation_failed:{step.name}")
                human_messages.append(f"{step.name.title()} validation failed: {step.message}")

            if step.name == "invariant":
                details = step.details or {}
                summary = details.get("summary") or details.get("invariant_summary") or step.message
                if summary:
                    human_messages.append(f"Invariant mining: {summary}")

                preserved = details.get("preserved_invariants") or []
                violated = details.get("violated_invariants") or []

                if preserved:
                    human_messages.append(
                        f"Preserved invariants: {', '.join(item.get('name', '?') for item in preserved)}"
                    )

                if violated:
                    human_messages.append(
                        f"Violated invariants: {', '.join(item.get('name', '?') for item in violated)}"
                    )

            # Add behavioral fingerprint summary when available
            if step.name == "behavioral":
                details = step.details or {}
                fp = details.get("fingerprints")
                if isinstance(fp, list):
                    total = details.get("total_tests", len(fp))
                    passed = details.get("passed_tests", sum(1 for e in fp if e.get("comparison", {}).get("matched")))
                    human_messages.append(f"Behavioral fingerprinting: {passed}/{total} tests matched.")
                    # Include first failure reasons as short hints
                    failures = details.get("failures", [])
                    if failures:
                        human_messages.append(f"Behavioral failures: {failures[:3]}")

        for warning in extra_warnings:
            if warning:
                risk_flags.append("transformation_warning")
                human_messages.append(warning)

        if rollback_occurred:
            summary = "Transformation rejected and rolled back to original source code."
            if rollback_reason:
                human_messages.append(f"Rollback reason: {rollback_reason}")
        elif not transformation_applied and not_applicable:
            summary = "Transformation not applicable; source code safely remained unchanged."
        elif not transformation_applied:
            summary = "Transformation not applied; source code remained unchanged."
        else:
            summary = "Transformation accepted after all safety checks."

        return SafetyReport(
            summary=summary,
            rollback_reason=rollback_reason,
            transformation_log=transformation_log,
            validation_timestamps=validation_timestamps,
            risk_flags=sorted(set(risk_flags)),
            human_messages=human_messages,
        )
