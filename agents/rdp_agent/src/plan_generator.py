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
import re
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
        validation_warnings: List[str] = []

        for idx, (smell, candidate) in enumerate(ordered_selections, start=1):
            # Build target dict — skip "unknown" or empty values
            target_dict = {
                k: v
                for k, v in smell.location.items()
                if k in ("class", "method", "lines", "file") and v and v != "unknown"
            }
            params = self._build_parameters(candidate, smell)

            # CRITICAL #5: validate required parameters are present
            warnings = self._validate_step_parameters(candidate["name"], params, target_dict)
            for w in warnings:
                logger.warning("Step %d (%s): %s", idx, candidate["name"], w)
                validation_warnings.append(f"Step {idx} ({candidate['name']}): {w}")

            step = RefactoringStep(
                step_id=idx,
                smell_id=smell.id,
                refactoring=candidate["name"],
                target=target_dict,
                parameters=params,
                explanation=self.generate_explanation(smell, candidate),
            )
            steps.append(step)

        summary = self._build_summary(steps, target, total_smells, validation_warnings)

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
        # C-specific: nesting depth
        nesting_depth = smell.metrics.get("nesting_depth")
        if nesting_depth:
            details_parts.append(f"nesting depth {nesting_depth}")
        # Handle both single-line [line] and range [start, end] formats
        if isinstance(lines, list) and len(lines) >= 2:
            details_parts.append(f"lines {lines[0]}-{lines[1]}")
        elif isinstance(lines, list) and len(lines) == 1:
            details_parts.append(f"line {lines[0]}")

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

        # Add source_file to all refactoring types (always include filename for traceability)
        source_file = location.get("file", "")
        if source_file and source_file != "unknown":
            params["source_file"] = source_file

        if name == "Extract Method":
            lines = location.get("lines", [])
            if isinstance(lines, list) and len(lines) == 2:
                params["source_lines"] = lines
            elif isinstance(lines, list) and len(lines) >= 1:
                params["source_line"] = lines[0]
            method = _loc("method")
            params["new_method_name"] = f"extracted_{method}" if method else "extracted_block"

        elif name == "Introduce Constant":
            params["source_line"] = location.get("lines", [None])[0]
            if smell.details:
                params["hint"] = smell.details

            # Generate a meaningful constant_name so the transformation agent does NOT
            # fall back to the ugly MAGIC_NUMBER_65 / MAGIC_NUMBER_75 pattern.
            # Strategy:
            #   1. Extract the numeric value from the smell message ("Magic number 65" → 65)
            #   2. Use the method/entity name to build a domain prefix
            #   3. Combine: e.g. "calculate_grade" + 65 → "GRADE_THRESHOLD_65"
            #   4. Fall back to "CONSTANT_{value}" — still better than MAGIC_NUMBER_{value}
            _num_match = re.search(r"[-+]?\d+(?:\.\d+)?", smell.details or "")
            _raw_value = _num_match.group(0) if _num_match else None
            _entity = (_loc("method") or _loc("class") or "").strip()

            if _raw_value is not None:
                _val_str = _raw_value.replace("-", "NEG_").replace(".", "_")

                if _entity and _entity not in ("unknown", ""):
                    # Turn snake_case/camelCase entity into SCREAMING_SNAKE prefix.
                    # e.g. "calculate_grade" → "GRADE"
                    # e.g. "checkStudentMarks"  → "STUDENT_MARKS"
                    _words = re.sub(r"([A-Z])", r"_\1", _entity)   # camelCase split
                    _words = re.sub(r"[^A-Za-z0-9]+", "_", _words)  # non-alnum → _
                    _parts = [w.upper() for w in _words.split("_") if len(w) > 2]
                    # Take last 1-2 meaningful words as prefix (most domain-specific)
                    _prefix = "_".join(_parts[-2:]) if _parts else ""
                    if _prefix:
                        params["constant_name"] = f"{_prefix}_{_val_str}"
                    else:
                        params["constant_name"] = f"THRESHOLD_{_val_str}"
                else:
                    params["constant_name"] = f"CONSTANT_{_val_str}"


        elif name == "Move Method":
            cls = _loc("class")
            params["source_class"] = cls
            method = _loc("method")
            if method:
                params["method"] = method
            # HIGH #4: try to infer destination from smell details message,
            # then fall back to a descriptive name so it isn't a raw placeholder
            if smell.details:
                _dest_m = re.search(r"'([A-Za-z_]\w+)'", smell.details)
                destination = _dest_m.group(1) if _dest_m else None
            else:
                destination = None
            params["destination_class"] = destination or f"{cls}Target" if cls else "TargetClass"

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

        # ---- C-specific refactoring parameters ----
        elif name == "Replace Unsafe Function":
            # Provide the unsafe function name and a hint for the safe alternative
            unsafe_fn = _loc("method") or location.get("entity", "")
            if unsafe_fn:
                params["unsafe_function"] = unsafe_fn
            lines = location.get("lines", [])
            if lines:
                params["source_line"] = lines[0] if isinstance(lines, list) else lines
            # Safe alternative hint based on common C unsafe functions
            safe_alternatives = {
                "gets":    "fgets",
                "strcpy":  "strncpy",
                "strcat":  "strncat",
                "sprintf": "snprintf",
                "scanf":   "sscanf / fgets",
            }
            if unsafe_fn in safe_alternatives:
                params["safe_alternative"] = safe_alternatives[unsafe_fn]
            params["source_file"] = location.get("file", "")

        elif name == "Encapsulate Variable":
            var_name = _loc("method") or location.get("entity", "variable")
            params["variable_name"] = var_name
            params["getter_name"] = f"get_{var_name}"
            params["setter_name"] = f"set_{var_name}"
            params["source_file"] = location.get("file", "")

        return params


    @staticmethod
    def _build_summary(
        steps: List[RefactoringStep], target: str, smells_count: int,
        validation_warnings: List[str] = None,
    ) -> str:
        """Build a human-readable summary for the plan.

        Args:
            steps: List of refactoring steps.
            target: Target file/module name.
            smells_count: Total number of smells in the input report.
            validation_warnings: Optional list of parameter validation warnings.

        Returns:
            Summary string.
        """
        if not steps:
            skipped = smells_count
            return (
                f"No applicable refactorings found for {target}. "
                f"{skipped} smell(s) detected — all were either unknown types, "
                f"failed precondition checks, or had no viable candidates. "
                f"Check the trace for detail on each skipped smell."
            )

        refactoring_names = list(
            dict.fromkeys(s.refactoring for s in steps)
        )
        addressed = len(steps)
        skipped = smells_count - addressed
        names_str = ", ".join(refactoring_names)

        summary = (
            f"{addressed}-step plan addressing {addressed} of "
            f"{smells_count} detected smells in {target}. "
            f"Refactorings applied: {names_str}."
        )
        if skipped > 0:
            summary += f" {skipped} smell(s) skipped (see trace for details)."
        if validation_warnings:
            summary += f" {len(validation_warnings)} parameter warning(s) logged."
        return summary

    @staticmethod
    def _validate_step_parameters(
        refactoring_name: str,
        params: Dict[str, Any],
        target_dict: Dict[str, Any],
    ) -> List[str]:
        """Validate that required parameters are present for a refactoring step.

        Checks required fields per refactoring type. Returns a list of warning
        strings (empty list = no issues). Does NOT raise — warnings are logged
        so valid partial plans are still returned.

        Args:
            refactoring_name: The refactoring being applied.
            params: The parameters dict built by _build_parameters.
            target_dict: The target location dict for the step.

        Returns:
            List of warning strings describing missing required fields.
        """
        warnings: List[str] = []

        # Required params per refactoring type
        REQUIRED: Dict[str, List[str]] = {
            "Extract Method":                    ["source_file", "new_method_name"],
            "Move Method":                       ["source_class", "destination_class"],
            "Extract Class":                     ["source_class", "new_class_name"],
            "Extract Subclass":                  ["source_class", "new_subclass_name"],
            "Introduce Parameter Object":        ["method", "parameter_object_name"],
            "Replace Unsafe Function":           ["source_file"],
            "Encapsulate Variable":              ["variable_name", "getter_name", "setter_name"],
        }

        required_fields = REQUIRED.get(refactoring_name, [])
        for field in required_fields:
            val = params.get(field)
            if not val or val in ("unknown", "<inferred_target_class>", "<parent>"):
                warnings.append(
                    f"missing or placeholder value for required parameter '{field}'"
                )

        # For Extract Method: warn if no source_lines/source_line
        if refactoring_name == "Extract Method":
            if not params.get("source_lines") and not params.get("source_line"):
                warnings.append(
                    "no source_lines or source_line — "
                    "Transformation Agent will not know what code to extract. "
                    "CUQA should provide start_line/end_line for this smell."
                )

        return warnings
