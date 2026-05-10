"""
Problem Interpreter
====================

Responsible for interpreting detected code smells and evaluating whether
candidate refactorings are applicable via precondition checks.

Preconditions are simple heuristic checks based on a smell's metrics,
location, and type. They follow an **open-world assumption**: if the data
needed to evaluate a check is missing, the check passes (i.e., we assume
the precondition is satisfied rather than blocking a potentially valid
candidate).

The :class:`ProblemInterpreter` can be extended with new precondition
evaluators by subclassing and overriding :meth:`_evaluate_precondition`.

Problem Interpretation (Step 1)
================================

The interpreter also handles the structured analysis of detected problems:
- Classifies severity based on metrics
- Analyzes metric highlights and risk factors
- Groups problems by type and severity
- Builds an internal model for candidate generation

This creates a **shared language** for all following pipeline steps.
"""

from __future__ import annotations

import logging
from typing import List, Dict, Any, Tuple
from collections import defaultdict

from .models import CodeSmell, ProblemMetricsAnalysis, ProblemGroup, ProblemInterpretation

logger = logging.getLogger("rdp_agent.problem_interpreter")


class ProblemInterpreter:
    """Evaluates preconditions for refactoring candidates against code smells.

    This component forms Step 1 of the agent pipeline: it interprets the
    detected problems and determines which refactoring candidates are
    applicable to each smell.
    """

    def check_preconditions(
        self, preconditions: List[str], smell: CodeSmell
    ) -> bool:
        """Evaluate whether all of a candidate's preconditions are satisfied.

        Args:
            preconditions: List of precondition tag strings
                           (e.g., ``["has_code_block", "has_temp_variables"]``).
            smell: The code smell to check against.

        Returns:
            ``True`` if **all** preconditions are satisfied, ``False`` otherwise.
        """
        for pc in preconditions:
            if not self._evaluate_precondition(pc, smell):
                logger.debug(
                    "Precondition '%s' failed for smell %s (%s)",
                    pc,
                    smell.id,
                    smell.type,
                )
                return False
        return True

    def _evaluate_precondition(
        self, precondition: str, smell: CodeSmell
    ) -> bool:
        """Evaluate a single precondition string against a smell.

        Override this method in subclasses to add custom precondition logic.

        Args:
            precondition: Tag identifying the check to perform.
            smell: The code smell context.

        Returns:
            ``True`` if the precondition is satisfied or cannot be evaluated.
        """
        metrics = smell.metrics
        location = smell.location

        # --- has_code_block ---
        # Satisfied if we have a line range spanning more than one line
        if precondition == "has_code_block":
            lines = location.get("lines", [])
            if isinstance(lines, list) and len(lines) >= 2:
                return (lines[1] - lines[0]) > 1
            return True  # cannot evaluate → assume OK

        # --- has_temp_variables ---
        # Heuristic: long methods likely have temporary variables
        if precondition == "has_temp_variables":
            loc = metrics.get("lines_of_code", 0)
            return loc > 10

        # --- has_multiple_parameters ---
        if precondition == "has_multiple_parameters":
            param_count = metrics.get("parameter_count", None)
            if param_count is not None:
                return param_count >= 3
            return True  # cannot evaluate → assume OK

        # --- has_multiple_responsibilities ---
        # Heuristic: high method count or high LOC indicates this
        if precondition == "has_multiple_responsibilities":
            method_count = metrics.get("method_count", None)
            loc = metrics.get("lines_of_code", 0)
            if method_count is not None:
                return method_count >= 5
            return loc > 50

        # --- has_external_field_access ---
        if precondition == "has_external_field_access":
            ext = metrics.get("external_field_accesses", None)
            if ext is not None:
                return ext >= 2
            # Feature Envy smell itself implies this
            return smell.type == "Feature Envy" or True

        # --- has_parent_class ---
        if precondition == "has_parent_class":
            return bool(
                location.get("parent_class") or location.get("superclass")
            )

        # --- has_thin_class ---
        if precondition == "has_thin_class":
            loc = metrics.get("lines_of_code", 0)
            method_count = metrics.get("method_count", 0)
            if loc > 0:
                return loc < 50 or method_count <= 3
            return True

        # --- has_type_checking ---
        if precondition == "has_type_checking":
            cc = metrics.get("cyclomatic_complexity", 0)
            return cc >= 3

        # --- has_primitive_fields ---
        if precondition == "has_primitive_fields":
            return bool(metrics.get("primitive_field_count", 0) >= 2) or True

        # --- has_computable_parameter ---
        if precondition == "has_computable_parameter":
            return True  # heuristic; always allow

        # --- has_chain_calls ---
        if precondition == "has_chain_calls":
            chain_len = metrics.get("chain_length", 0)
            return chain_len >= 3 if chain_len else True

        # Unknown precondition → pass by default
        logger.warning(
            "Unknown precondition '%s'; assuming satisfied.", precondition
        )
        return True

    # =========================================================================
    # PROBLEM INTERPRETATION (Step 1)
    # =========================================================================
    # These methods build a structured understanding of detected problems.

    def interpret_problems(
        self, smells: List[CodeSmell], target: str
    ) -> ProblemInterpretation:
        """Interpret a list of code smells into a structured problem model.

        This is the main entry point for Step 1 of the pipeline. It:
        1. Analyzes each smell's metrics in detail
        2. Groups problems by type and severity
        3. Builds a comprehensive internal model
        4. Generates recommendations

        Args:
            smells: List of detected CodeSmell instances.
            target: The target file/module being analyzed.

        Returns:
            A ProblemInterpretation object with full analysis.
        """
        logger.info(
            "Interpreting %d smell(s) for target '%s'",
            len(smells),
            target,
        )

        # Step 1a: Analyze metrics for each smell
        metrics_analyses: List[ProblemMetricsAnalysis] = []
        for smell in smells:
            analysis = self.analyze_metrics(smell)
            metrics_analyses.append(analysis)

        # Step 1b: Group smells by type and severity
        groups = self._group_smells(smells, metrics_analyses)

        # Step 1c: Build severity and type summaries
        severity_summary = self._build_severity_summary(metrics_analyses)
        type_summary = self._build_type_summary(smells)

        # Step 1d: Identify critical issues
        critical_issues = self._identify_critical_issues(
            smells, metrics_analyses
        )

        # Step 1e: Generate preliminary recommendations
        recommendations = self._generate_recommendations(
            smells, groups, critical_issues
        )

        # Build and return the full interpretation
        interpretation = ProblemInterpretation(
            target=target,
            total_smells=len(smells),
            metrics_analyses=metrics_analyses,
            problem_groups=groups,
            severity_summary=severity_summary,
            type_summary=type_summary,
            critical_issues=critical_issues,
            recommendations=recommendations,
        )

        logger.info(
            "Problem interpretation complete: %d group(s), %d critical issue(s)",
            len(groups),
            len(critical_issues),
        )
        return interpretation

    def analyze_metrics(self, smell: CodeSmell) -> ProblemMetricsAnalysis:
        """Deeply analyze metrics for a single code smell.

        Examines the raw metrics and determines:
        - How severe the problem truly is (via metric heuristics)
        - What metrics are notably high/low
        - What risk factors are present
        - What characteristics define the problem

        Args:
            smell: The CodeSmell to analyze.

        Returns:
            A ProblemMetricsAnalysis with detailed findings.
        """
        metrics = smell.metrics
        location = smell.location

        # Determine severity level based on metrics
        severity_level = self._classify_severity_from_metrics(smell)

        # Build severity rationale
        severity_rationale = self._build_severity_rationale(
            smell, severity_level
        )

        # Extract metric highlights (notably high/low values)
        metric_highlights = self._extract_metric_highlights(metrics, smell.type)

        # Identify risk factors
        risk_factors = self._identify_risk_factors(metrics, smell.type)

        # Build problem characteristics
        problem_characteristics = self._build_problem_characteristics(
            smell, metrics, location
        )

        analysis = ProblemMetricsAnalysis(
            smell_id=smell.id,
            original_metrics=dict(metrics),
            severity_level=severity_level,
            severity_rationale=severity_rationale,
            metric_highlights=metric_highlights,
            risk_factors=risk_factors,
            problem_characteristics=problem_characteristics,
        )

        logger.debug(
            "Analyzed smell %s (%s): severity=%s, risks=%d",
            smell.id,
            smell.type,
            severity_level,
            len(risk_factors),
        )
        return analysis

    def _classify_severity_from_metrics(self, smell: CodeSmell) -> str:
        """Classify severity level based on metric values and thresholds.

        Uses metric-based heuristics to determine if the reported severity
        is accurate, and potentially adjusts it based on actual metric values.

        Args:
            smell: The CodeSmell to classify.

        Returns:
            Severity level: ``low``, ``medium``, ``high``, or ``critical``.
        """
        metrics = smell.metrics
        reported_severity = smell.severity

        # Calculate a metric-based severity score (0-10 scale)
        severity_score = 0

        # Complexity metrics (high = more severe)
        cc = metrics.get("cyclomatic_complexity", 0)
        if cc >= 15:
            severity_score += 4
        elif cc >= 10:
            severity_score += 2
        elif cc >= 5:
            severity_score += 1

        # Lines of code (high = more severe)
        loc = metrics.get("lines_of_code", 0)
        if loc >= 200:
            severity_score += 3
        elif loc >= 100:
            severity_score += 2
        elif loc >= 50:
            severity_score += 1

        # Coupling (high = more severe)
        coupling = metrics.get("coupling", 0)
        if coupling >= 8:
            severity_score += 3
        elif coupling >= 5:
            severity_score += 1

        # Method count (high = more severe for God Class)
        method_count = metrics.get("method_count", 0)
        if method_count >= 20:
            severity_score += 2
        elif method_count >= 10:
            severity_score += 1

        # Parameter count (high = more severe)
        params = metrics.get("parameter_count", 0)
        if params >= 8:
            severity_score += 2
        elif params >= 5:
            severity_score += 1

        # Map score to severity level
        if severity_score >= 8:
            return "critical"
        elif severity_score >= 6:
            return "high"
        elif severity_score >= 3:
            return "medium"
        else:
            return "low"

    def _build_severity_rationale(
        self, smell: CodeSmell, severity: str
    ) -> str:
        """Build a human-readable rationale for the assigned severity."""
        metrics = smell.metrics
        reasons = []

        cc = metrics.get("cyclomatic_complexity", 0)
        if cc >= 15:
            reasons.append(f"Very high complexity (CC={cc}, >15)")
        elif cc >= 10:
            reasons.append(f"High complexity (CC={cc}, >10)")

        loc = metrics.get("lines_of_code", 0)
        if loc >= 200:
            reasons.append(f"Very long code block (LOC={loc}, >200)")
        elif loc >= 100:
            reasons.append(f"Long code block (LOC={loc}, >100)")

        coupling = metrics.get("coupling", 0)
        if coupling >= 8:
            reasons.append(f"High coupling (depends on {coupling} other classes)")

        if not reasons:
            reasons.append("Detected by Code Understanding Agent")

        return f"[{severity.upper()}] " + "; ".join(reasons)

    def _extract_metric_highlights(
        self, metrics: Dict[str, Any], smell_type: str
    ) -> Dict[str, Any]:
        """Extract metrics that are notably high or low compared to typical."""
        highlights = {}

        # Complexity
        if metrics.get("cyclomatic_complexity", 0) >= 10:
            highlights["cyclomatic_complexity"] = {
                "value": metrics["cyclomatic_complexity"],
                "status": "HIGH",
                "implication": "Multiple execution paths increase bug risk",
            }

        # Lines of code
        if metrics.get("lines_of_code", 0) >= 100:
            highlights["lines_of_code"] = {
                "value": metrics["lines_of_code"],
                "status": "HIGH",
                "implication": "Hard to understand and maintain",
            }

        # Coupling
        if metrics.get("coupling", 0) >= 5:
            highlights["coupling"] = {
                "value": metrics["coupling"],
                "status": "HIGH",
                "implication": "Changes have broad impact",
            }

        # Method count
        if metrics.get("method_count", 0) >= 10:
            highlights["method_count"] = {
                "value": metrics["method_count"],
                "status": "HIGH",
                "implication": "Likely multiple responsibilities",
            }

        # Parameters
        if metrics.get("parameter_count", 0) >= 5:
            highlights["parameter_count"] = {
                "value": metrics["parameter_count"],
                "status": "HIGH",
                "implication": "Function interface is complex",
            }

        return highlights

    def _identify_risk_factors(
        self, metrics: Dict[str, Any], smell_type: str
    ) -> List[str]:
        """Identify risk factors that make this problem harder to fix."""
        risks = []

        cc = metrics.get("cyclomatic_complexity", 0)
        if cc >= 15:
            risks.append("High cyclomatic complexity increases refactoring risk")

        loc = metrics.get("lines_of_code", 0)
        if loc >= 200:
            risks.append("Large code block is difficult to refactor safely")

        coupling = metrics.get("coupling", 0)
        if coupling >= 8:
            risks.append(
                "High coupling means refactoring may break dependent code"
            )

        if "Long Method" in smell_type or "Long Function" in smell_type:
            if loc >= 150:
                risks.append(
                    "Consider extracting smaller methods incrementally"
                )

        if "God Class" in smell_type:
            if metrics.get("method_count", 0) >= 20:
                risks.append("Class has too many responsibilities to fix at once")

        if len(risks) == 0:
            risks.append("Standard refactoring complexity")

        return risks

    def _build_problem_characteristics(
        self,
        smell: CodeSmell,
        metrics: Dict[str, Any],
        location: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build a dictionary describing key characteristics of the problem."""
        chars = {
            "type": smell.type,
            "location": {
                "class": location.get("class", "unknown"),
                "method": location.get("method", "unknown"),
                "lines": location.get("lines", []),
            },
            "size_category": self._categorize_by_size(metrics),
            "complexity_category": self._categorize_by_complexity(metrics),
            "coupling_category": self._categorize_by_coupling(metrics),
        }
        return chars

    def _categorize_by_size(self, metrics: Dict[str, Any]) -> str:
        """Categorize problem size."""
        loc = metrics.get("lines_of_code", 0)
        if loc >= 200:
            return "very_large"
        elif loc >= 100:
            return "large"
        elif loc >= 50:
            return "medium"
        else:
            return "small"

    def _categorize_by_complexity(self, metrics: Dict[str, Any]) -> str:
        """Categorize problem complexity."""
        cc = metrics.get("cyclomatic_complexity", 0)
        if cc >= 15:
            return "very_high"
        elif cc >= 10:
            return "high"
        elif cc >= 5:
            return "moderate"
        else:
            return "low"

    def _categorize_by_coupling(self, metrics: Dict[str, Any]) -> str:
        """Categorize problem coupling."""
        coupling = metrics.get("coupling", 0)
        if coupling >= 8:
            return "very_high"
        elif coupling >= 5:
            return "high"
        elif coupling >= 2:
            return "moderate"
        else:
            return "low"

    def _group_smells(
        self,
        smells: List[CodeSmell],
        analyses: List[ProblemMetricsAnalysis],
    ) -> List[ProblemGroup]:
        """Group smells by type and severity.

        Args:
            smells: List of CodeSmell instances.
            analyses: Corresponding ProblemMetricsAnalysis for each smell.

        Returns:
            List of ProblemGroup instances.
        """
        # Group by (type, severity_level)
        groups_dict: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        smell_by_id = {s.id: s for s in smells}
        analysis_by_id = {a.smell_id: a for a in analyses}

        for smell in smells:
            analysis = analysis_by_id[smell.id]
            key = (smell.type, analysis.severity_level)
            groups_dict[key].append(smell.id)

        # Convert to ProblemGroup instances
        groups = []
        for idx, ((smell_type, severity), smell_ids) in enumerate(
            sorted(groups_dict.items()), start=1
        ):
            # Collect metrics for this group
            group_metrics = defaultdict(list)
            for sid in smell_ids:
                analysis = analysis_by_id[sid]
                for key, val in analysis.original_metrics.items():
                    if isinstance(val, (int, float)):
                        group_metrics[key].append(val)

            # Aggregate metrics (use average)
            collective_metrics = {}
            for key, vals in group_metrics.items():
                collective_metrics[key] = round(sum(vals) / len(vals), 2)

            description = (
                f"{len(smell_ids)} instance(s) of {smell_type} "
                f"(severity: {severity})"
            )

            group = ProblemGroup(
                group_id=f"group_{idx}",
                smell_type=smell_type,
                severity_level=severity,
                smell_ids=smell_ids,
                count=len(smell_ids),
                description=description,
                collective_metrics=collective_metrics,
            )
            groups.append(group)

            logger.debug(
                "Created group '%s': %s",
                group.group_id,
                description,
            )

        return groups

    def _build_severity_summary(
        self, analyses: List[ProblemMetricsAnalysis]
    ) -> Dict[str, int]:
        """Count smells by severity level."""
        summary: Dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for analysis in analyses:
            summary[analysis.severity_level] += 1
        return summary

    def _build_type_summary(self, smells: List[CodeSmell]) -> Dict[str, int]:
        """Count smells by type."""
        summary: Dict[str, int] = {}
        for smell in smells:
            summary[smell.type] = summary.get(smell.type, 0) + 1
        return summary

    def _identify_critical_issues(
        self,
        smells: List[CodeSmell],
        analyses: List[ProblemMetricsAnalysis],
    ) -> List[str]:
        """Identify and describe critically severe issues."""
        critical = []
        analysis_by_id = {a.smell_id: a for a in analyses}

        for smell in smells:
            analysis = analysis_by_id[smell.id]
            if analysis.severity_level == "critical":
                msg = (
                    f"CRITICAL: {smell.type} in {smell.location.get('class', 'unknown')}"
                    f".{smell.location.get('method', 'unknown')} — "
                    f"{analysis.severity_rationale}"
                )
                critical.append(msg)

        return critical

    def _generate_recommendations(
        self,
        smells: List[CodeSmell],
        groups: List[ProblemGroup],
        critical_issues: List[str],
    ) -> List[str]:
        """Generate preliminary recommendations based on problem analysis."""
        recommendations = []

        # Group-level recommendations
        for group in groups:
            if group.count > 5:
                recommendations.append(
                    f"Multiple {group.smell_type} issues detected ({group.count} instances). "
                    f"Consider systematic refactoring strategy."
                )
            if group.severity_level == "critical":
                recommendations.append(
                    f"Address {group.smell_type} issues immediately to reduce risk."
                )

        # Coupling-based recommendation
        high_coupling_smells = [
            s for s in smells if s.metrics.get("coupling", 0) >= 7
        ]
        if high_coupling_smells:
            recommendations.append(
                f"High coupling detected ({len(high_coupling_smells)} smell(s)). "
                f"Plan refactorings carefully to avoid breaking changes."
            )

        # Critical issues recommendation
        if critical_issues:
            recommendations.append(
                f"Found {len(critical_issues)} CRITICAL issue(s). "
                f"Prioritize these for refactoring."
            )

        if not recommendations:
            recommendations.append("Review detected smells before proceeding.")

        return recommendations
