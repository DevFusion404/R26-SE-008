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
"""

from __future__ import annotations

import logging
from typing import List

from .models import CodeSmell

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
