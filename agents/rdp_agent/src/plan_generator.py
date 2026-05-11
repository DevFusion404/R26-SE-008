"""
Plan Generator
===============

Generates the final structured, machine-executable refactoring plan.
This module implements Step 7 of the agent pipeline.

Responsibilities:
    - Generate human-readable explanations for each refactoring step.
    - Build transformation parameters for the Safe Transformation Agent.
    - Assemble the complete :class:`RefactoringPlan` with ordered steps.
    - Produce a plan summary.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Tuple

from .models import CodeSmell, RefactoringStep, RefactoringPlan

logger = logging.getLogger("rdp_agent.plan_generator")


class PlanGenerator:
    """Assembles a complete refactoring plan from ordered selections.

    This is the final stage of the pipeline: it takes the ordered
    ``(smell, candidate)`` tuples and produces a fully specified
    :class:`RefactoringPlan` ready for the transformation agent.
    """

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def build_plan(
        self,
        target: str,
        ordered_selections: List[Tuple[CodeSmell, Dict[str, Any]]],
        total_smells: int,
    ) -> RefactoringPlan:
        """Build a complete refactoring plan.

        Args:
            target: File or module name being refactored.
            ordered_selections: Dependency-ordered ``(smell, candidate)`` tuples.
            total_smells: Total number of smells in the input report.

        Returns:
            A fully populated :class:`RefactoringPlan`.
        """
        plan_id = self._generate_plan_id()
        steps: List[RefactoringStep] = []

        for idx, (smell, candidate) in enumerate(ordered_selections, start=1):
            # Build target dict — skip "unknown" or empty values
            target_dict = {
                k: v
                for k, v in smell.location.items()
                if k in ("class", "method") and v and v != "unknown"
            }
            step = RefactoringStep(
                step_id=idx,
                smell_id=smell.id,
                refactoring=candidate["name"],
                target=target_dict,
                parameters=self._build_parameters(candidate, smell),
                explanation=self.generate_explanation(smell, candidate),
            )
            steps.append(step)

        summary = self._build_summary(steps, target, total_smells)

        plan = RefactoringPlan(
            plan_id=plan_id,
            target=target,
            steps=steps,
            summary=summary,
        )

        logger.info(
            "Built plan '%s' with %d step(s) for '%s'.",
            plan_id,
            len(steps),
            target,
        )
        return plan

    # -----------------------------------------------------------------
    # Explanation Generation
    # -----------------------------------------------------------------

    def generate_explanation(
        self, smell: CodeSmell, candidate: Dict[str, Any]
    ) -> str:
        """Generate a human-readable explanation for a refactoring step.

        Args:
            smell: The code smell being addressed.
            candidate: The chosen refactoring candidate.

        Returns:
            Explanation string.
        """
        target_str = self._format_target(smell.location)
        smell_type = smell.type
        ref_name = candidate["name"]
        impact = candidate.get("impact", "medium")
        risk = candidate.get("risk", "medium")
        complexity = candidate.get("complexity", "medium")

        # Base explanation
        explanation = (
            f"{ref_name} on {target_str} to address {smell_type} smell. "
            f"Expected {impact} impact with {risk} risk and "
            f"{complexity} complexity."
        )

        # Smell-specific metric enrichment
        loc = smell.metrics.get("lines_of_code")
        cc = smell.metrics.get("cyclomatic_complexity")
        mc = smell.metrics.get("method_count")
        lines = smell.location.get("lines", [])

        details_parts: List[str] = []
        if loc:
            details_parts.append(f"{loc} lines of code")
        if cc:
            details_parts.append(f"cyclomatic complexity {cc}")
        if mc:
            details_parts.append(f"{mc} methods")
        if isinstance(lines, list) and len(lines) == 2:
            details_parts.append(f"lines {lines[0]}-{lines[1]}")

        if details_parts:
            explanation += f" Metrics: {', '.join(details_parts)}."

        return explanation

    # -----------------------------------------------------------------
    # Internal Helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _generate_plan_id() -> str:
        """Generate a unique plan ID based on the current timestamp."""
        now = datetime.now()
        return f"plan_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}"

    @staticmethod
    def _format_target(location: Dict[str, Any]) -> str:
        """Format a location dictionary as a readable string.

        Args:
            location: Dictionary with ``class`` and/or ``method`` keys.

        Returns:
            Formatted string like ``OrderProcessor.calculateTotal``.
        """
        cls = location.get("class", "")
        method = location.get("method", "")
        # Treat "unknown" as absent — don't show it in explanations
        if method in ("unknown", None):
            method = ""
        if cls and method:
            return f"{cls}.{method}"
        return cls or method or "(module level)"

    @staticmethod
    def _clean(location: Dict[str, Any], key: str, fallback: str = "") -> str:
        """Get a location value, returning fallback if it is 'unknown' or empty."""
        val = location.get(key, "")
        return val if val and val != "unknown" else fallback

    @staticmethod
    def _build_parameters(
        candidate: Dict[str, Any], smell: CodeSmell
    ) -> Dict[str, Any]:
        """Build the parameters dictionary for a refactoring step.

        Infers sensible defaults based on the candidate name and smell context.

        Args:
            candidate: The chosen refactoring candidate.
            smell: The code smell being addressed.

        Returns:
            Parameters dictionary for the Safe Transformation Agent.
        """
        params: Dict[str, Any] = {}
        name = candidate["name"]
        location = smell.location

        def _loc(key: str, fallback: str = "") -> str:
            """Return location[key] if meaningful, else fallback."""
            val = location.get(key, "")
            return val if val and val != "unknown" else fallback

        if name == "Extract Method":
            lines = location.get("lines", [])
            if isinstance(lines, list) and len(lines) == 2:
                params["source_lines"] = lines
            method = _loc("method")
            params["new_method_name"] = f"extracted_{method}" if method else "extracted_block"

        elif name == "Introduce Constant":
            # Pull the magic number value from details if available
            params["source_file"] = location.get("file", "")
            params["source_line"] = location.get("lines", [None])[0]
            if smell.details:
                params["hint"] = smell.details

        elif name == "Move Method":
            params["source_class"] = _loc("class")
            method = _loc("method")
            if method:
                params["method"] = method
            params["destination_class"] = smell.details or "<inferred_target_class>"

        elif name == "Extract Class":
            cls = _loc("class")
            params["source_class"] = cls
            params["new_class_name"] = f"{cls}Helper" if cls else "ExtractedClass"

        elif name == "Extract Subclass":
            cls = _loc("class")
            params["source_class"] = cls
            params["new_subclass_name"] = f"{cls}Subtype" if cls else "ExtractedSubtype"

        elif name == "Introduce Parameter Object":
            method = _loc("method")
            params["method"] = method
            params["parameter_object_name"] = f"{method}Params" if method else "ParamObject"

        elif name == "Replace Conditional with Polymorphism":
            params["source_class"] = _loc("class")
            method = _loc("method")
            if method:
                params["method"] = method

        elif name == "Pull Up Method":
            params["source_class"] = _loc("class")
            method = _loc("method")
            if method:
                params["method"] = method
            params["target_class"] = location.get("parent_class", "<parent>")

        elif name == "Inline Class":
            params["class_to_inline"] = _loc("class")

        elif name == "Replace Temp with Query":
            method = _loc("method")
            if method:
                params["method"] = method

        elif name == "Collapse Hierarchy":
            params["source_class"] = _loc("class")
            params["parent_class"] = location.get("parent_class", "<parent>")

        elif name == "Replace Data Value with Object":
            params["source_class"] = _loc("class")

        elif name == "Hide Delegate":
            params["source_class"] = _loc("class")

        elif name == "Remove Dead Code":
            params["source_class"] = _loc("class")
            method = _loc("method")
            if method:
                params["method"] = method

        elif name == "Rename Method":
            params["source_class"] = _loc("class")
            method = _loc("method")
            if method:
                params["method"] = method
                params["new_name"] = f"descriptive_{method}"

        elif name == "Replace Parameter with Method Call":
            method = _loc("method")
            if method:
                params["method"] = method

        return params


    @staticmethod
    def _build_summary(
        steps: List[RefactoringStep], target: str, smells_count: int
    ) -> str:
        """Build a human-readable summary for the plan.

        Args:
            steps: List of refactoring steps.
            target: Target file/module name.
            smells_count: Total number of smells in the input report.

        Returns:
            Summary string.
        """
        if not steps:
            return f"No applicable refactorings found for {target}."

        refactoring_names = list(
            dict.fromkeys(s.refactoring for s in steps)
        )
        addressed = len(steps)
        names_str = ", ".join(refactoring_names)

        return (
            f"{addressed}-step plan addressing {addressed} of "
            f"{smells_count} detected smells in {target}. "
            f"Refactorings applied: {names_str}."
        )
