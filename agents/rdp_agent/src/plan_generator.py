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
from .move_method_resolver import MoveMethodPlanResolver

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
        move_method_resolver: MoveMethodPlanResolver | None = None,
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

        step_idx = 1
        for idx, (smell, candidate) in enumerate(ordered_selections, start=1):
            if candidate["name"] == "Move Method":
                resolution = candidate.get("_move_method_resolution")
                if not isinstance(resolution, dict) and move_method_resolver:
                    resolution = move_method_resolver.resolve(smell, candidate)
                if not isinstance(resolution, dict) or resolution.get("status") != "success":
                    reason = (
                        resolution.get("reason")
                        if isinstance(resolution, dict)
                        else "MOVE_METHOD_REQUIRES_REPOSITORY_AST"
                    )
                    logger.warning(
                        "Skipping invalid Move Method step for smell %s: %s.",
                        smell.id,
                        reason,
                    )
                    validation_warnings.append(
                        f"Smell {smell.id} (Move Method): REVIEW_REQUIRED reason={reason}."
                    )
                    continue
                params = self._build_move_method_parameters_from_resolution(
                    resolution,
                    smell,
                )
                if move_method_resolver:
                    final_check = move_method_resolver.validate_plan(params)
                    if final_check.get("status") != "success":
                        reason = final_check.get("reason") or "NO_VALID_DESTINATION_CLASS"
                        logger.warning(
                            "Skipping Move Method step for smell %s after final validation: %s.",
                            smell.id,
                            reason,
                        )
                        validation_warnings.append(
                            f"Smell {smell.id} (Move Method): REVIEW_REQUIRED reason={reason}."
                        )
                        continue
            else:
                params = self._build_parameters(candidate, smell)

            # Reject invalid Move Method if prerequisites (source_class, method, destination_class) are missing
            if candidate["name"] == "Move Method":
                if not params.get("source_class") or not params.get("method") or not params.get("destination_class"):
                    logger.warning("Skipping invalid Move Method step for smell %s: prerequisites missing.", smell.id)
                    validation_warnings.append(f"Smell {smell.id} (Move Method): Prerequisites missing (invalid Move Method step rejected).")
                    continue

            target_dict = {
                k: v
                for k, v in smell.location.items()
                if k in ("class", "method", "lines", "file", "entity", "duplicate_group") and v and v != "unknown"
            }
            if candidate["name"] == "Move Method":
                target_dict.update({
                    "file": params["source_file"],
                    "class": params["source_class"],
                    "method": params["source_method"],
                })
            target_dict["smell_type"] = smell.type
            target_dict["smell_id"] = smell.id
            params["smell_type"] = smell.type
            params["smell_id"] = smell.id

            warnings = self._validate_step_parameters(candidate["name"], params, target_dict)
            for w in warnings:
                logger.warning("Step %d (%s): %s", step_idx, candidate["name"], w)
                validation_warnings.append(f"Step {step_idx} ({candidate['name']}): {w}")

            step = RefactoringStep(
                step_id=step_idx,
                smell_id=smell.id,
                refactoring=candidate["name"],
                target=target_dict,
                parameters=params,
                explanation=self.generate_explanation(smell, candidate),
            )
            steps.append(step)
            step_idx += 1

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

    @staticmethod
    def _build_move_method_parameters_from_resolution(
        resolution: Dict[str, Any],
        smell: CodeSmell,
    ) -> Dict[str, Any]:
        """Build SCTVA-facing Move Method parameters from proven AST evidence."""

        source_line = resolution.get("lineno")
        source_method = resolution.get("source_method") or resolution["method"]
        params: Dict[str, Any] = {
            "source_file": resolution["source_file"],
            "source_class": resolution["source_class"],
            "method": resolution["method"],
            "source_method": source_method,
            "destination_class": resolution["destination_class"],
            "destination_parameter": resolution.get("destination_parameter", ""),
            "smell_type": smell.type,
            "smell_id": smell.id,
            "move_method_planning_evidence": {
                "source_class_exists": True,
                "method_belongs_to_source_class": True,
                "destination_class_exists": True,
                "source_and_destination_differ": (
                    resolution["source_class"] != resolution["destination_class"]
                ),
                "dependencies_can_be_mapped": True,
                "call_sites_can_be_updated": bool(
                    resolution.get("call_sites_rewritable")
                ),
                "destination_parameter": resolution.get("destination_parameter", ""),
                "feature_envy_accesses": resolution.get("feature_envy_accesses"),
                "source_self_accesses": resolution.get("source_self_accesses"),
                "call_sites_checked": resolution.get("call_sites_checked", 0),
            },
        }
        if source_line is not None:
            params["source_line"] = int(source_line)
        if source_line is not None and resolution.get("end_lineno") is not None:
            params["source_lines"] = [
                int(source_line),
                int(resolution["end_lineno"]),
            ]
        return params

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
        cls = location.get("class", "") or ""
        method = location.get("method", "") or ""
        source_file = location.get("file", "") or ""
        base_file = source_file.split("/")[-1].split("\\")[-1].replace(".py", "").replace(".java", "").replace(".c", "").replace(".h", "") if source_file else ""

        if cls in ("unknown", base_file):
            cls = ""
        if method in ("unknown", base_file):
            method = ""

        if cls and method:
            return f"{cls}.{method}"
        return cls or method or source_file or "(module level)"

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
        source_file = location.get("file", "")
        base_file = source_file.split("/")[-1].split("\\")[-1].replace(".py", "").replace(".java", "").replace(".c", "").replace(".h", "") if source_file else ""

        def _loc(key: str, fallback: str = "") -> str:
            """Return location[key] if meaningful and not equal to base filename, else fallback."""
            val = location.get(key, "")
            if val and val != "unknown" and val != base_file:
                return val
            return fallback

        def _source_context() -> str:
            """Return the best available source container (class > module > base_file).

            This prevents ``source_class: null`` in plans for Python/C module-level
            functions that have no enclosing class.  The transformation agent uses
            this value to locate the function in the source file.

            Priority:
              1. ``location["class"]``  — real class name (Java / OOP Python)
              2. ``location["module"]`` — module name injected by _translate_cuqa_to_rdp
                                          for Python/C module-level functions
              3. ``base_file``           — filename without extension as last resort
            """
            cls = _loc("class")
            if cls:
                return cls
            module = location.get("module", "")
            if module and module != "unknown":
                return module
            return base_file

        # Add source_file to all refactoring types (always include filename for traceability)
        if source_file and source_file != "unknown":
            params["source_file"] = source_file

        if name == "Extract Method":
            lines = location.get("lines", [])
            if isinstance(lines, list) and len(lines) == 2:
                params["source_lines"] = lines
            elif isinstance(lines, list) and len(lines) >= 1:
                params["source_line"] = lines[0]
            method = _loc("method")
            dup_group = location.get("duplicate_group") or getattr(smell, "duplicate_group", None)
            if dup_group and isinstance(dup_group, list):
                params["duplicate_group"] = dup_group
                clean_group = [g for g in dup_group if isinstance(g, str)]
                group_name = "_".join(clean_group[:3])
                params["new_method_name"] = f"shared_helper_{group_name}" if group_name else "shared_helper"
                params["is_shared_helper"] = True
            elif method:
                params["target_method"] = method
                params["new_method_name"] = f"extracted_{method}"
            else:
                params["new_method_name"] = "extracted_block"

        elif name == "Introduce Constant":
            params["source_line"] = location.get("lines", [None])[0]
            if smell.details:
                params["hint"] = smell.details

            var_ctx = getattr(smell, "variable_context", None) or location.get("variable_context")
            _num_match = re.search(r"[-+]?\d+(?:\.\d+)?", smell.details or "")
            _raw_value = _num_match.group(0) if _num_match else ""

            ctx_str = (var_ctx or "").lower()
            details_str = (smell.details or "").lower()

            if "timeout" in ctx_str or "timeout" in details_str or "delay" in ctx_str:
                const_name = "DEFAULT_TIMEOUT_MS"
            elif "port" in ctx_str or "port" in details_str:
                const_name = "DEFAULT_PORT"
            elif any(k in ctx_str or k in details_str for k in ("buf", "buffer", "size", "capacity", "limit")):
                const_name = "DEFAULT_BUFFER_SIZE"
            elif "retry" in ctx_str or "attempts" in ctx_str:
                const_name = "MAX_RETRY_COUNT"
            elif var_ctx:
                clean_ctx = re.sub(r"[^A-Za-z0-9_]+", "", var_ctx).upper()
                if _raw_value and _raw_value in ("100", "1000", "500", "255"):
                    const_name = f"MAX_{clean_ctx}"
                else:
                    const_name = f"DEFAULT_{clean_ctx}_LIMIT"
            elif _raw_value:
                val_int = int(float(_raw_value)) if _raw_value.replace(".", "").isdigit() else 0
                if val_int in (1024, 2048, 4096, 512, 256):
                    const_name = "DEFAULT_BUFFER_SIZE"
                elif val_int in (80, 443, 8080, 5000, 8000):
                    const_name = "DEFAULT_PORT"
                else:
                    const_name = f"THRESHOLD_LIMIT_{_raw_value.replace('-', 'NEG_').replace('.', '_')}"
            else:
                const_name = "DEFAULT_CONSTANT_VALUE"

            params["constant_name"] = const_name

        elif name == "Move Method":
            src_ctx = _source_context()
            method = _loc("method")
            base_file_no_ext = base_file.split(".")[0]

            # Reject module names or files treated as source_class
            if not src_ctx or src_ctx in (base_file, base_file_no_ext, "unknown", "null"):
                return {}

            if not method or method in ("unknown", "null"):
                return {}

            destination = None
            if smell.details:
                _class_m = re.search(r"(?:class|of|to|target)\s+'([A-Z]\w+)'", smell.details, re.IGNORECASE)
                if _class_m:
                    found_name = _class_m.group(1)
                    if found_name != method and found_name != src_ctx:
                        destination = found_name

            if not destination and location.get("destination_class"):
                destination = location.get("destination_class")

            # Do NOT invent fake destination classes such as book_managerTarget!
            if not destination or destination in ("unknown", "null"):
                return {}

            params["source_class"] = src_ctx
            params["method"] = method
            params["destination_class"] = destination

        elif name in ("Replace Bare Except with Specific Exception", "Replace Bare Except"):
            params["source_file"] = location.get("file", base_file)
            method = _loc("method")
            if method:
                params["method"] = method
            params["replacement_exception"] = "Exception"

        elif name == "Extract Class":
            src_ctx = _source_context()
            params["source_class"] = src_ctx
            params["new_class_name"] = f"{src_ctx}Helper" if src_ctx else "ExtractedClass"

        elif name == "Extract Subclass":
            src_ctx = _source_context()
            params["source_class"] = src_ctx
            params["new_subclass_name"] = f"{src_ctx}Subtype" if src_ctx else "ExtractedSubtype"

        elif name == "Introduce Parameter Object":
            method = _loc("method")
            params["method"] = method
            params["parameter_object_name"] = f"{method}Params" if method else "ParamObject"

        elif name == "Replace Conditional with Polymorphism":
            params["source_class"] = _source_context()
            method = _loc("method")
            if method:
                params["method"] = method

        elif name == "Pull Up Method":
            params["source_class"] = _source_context()
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
            params["source_class"] = _source_context()
            params["parent_class"] = location.get("parent_class", "<parent>")

        elif name == "Replace Data Value with Object":
            params["source_class"] = _source_context()

        elif name == "Hide Delegate":
            params["source_class"] = _source_context()
            method = _loc("method")
            if method:
                params["method"] = method

        elif name == "Remove Dead Code":
            params["source_class"] = _source_context()
            method = _loc("method")
            if method:
                params["method"] = method

        elif name == "Rename Method":
            params["source_class"] = _source_context()
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
            # Safe alternative hint based on common C unsafe functions.
            # For scanf, the idiomatic safe pattern is fgets() to read into a
            # buffer, followed by sscanf() to parse — the transformer handles
            # this two-step expansion from the single "fgets" key.
            safe_alternatives = {
                "gets":    "fgets",
                "strcpy":  "strncpy",
                "strcat":  "strncat",
                "sprintf": "snprintf",
                "scanf":   "fgets",
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
