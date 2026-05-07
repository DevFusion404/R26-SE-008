"""
Refactoring Knowledge Base
===========================

Maps code smell types to candidate refactoring techniques and defines
dependency relationships between refactorings. This module serves as the
extensible knowledge layer of the agent — new smell types or refactoring
rules can be added by extending the catalog or subclassing
:class:`RefactoringKnowledgeBase`.

Key concepts:
    - **Catalog**: smell type → list of candidate refactorings, each with
      complexity, risk, impact, and preconditions.
    - **Dependencies**: refactoring name → list of prerequisite refactoring
      names that should be applied first when both appear in the same plan.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("rdp_agent.knowledge_base")


# ---------------------------------------------------------------------------
# Default Catalog
# ---------------------------------------------------------------------------

# Maps smell types → candidate refactorings. Each candidate is a dict with:
#   name, complexity, risk, impact, preconditions.

DEFAULT_CATALOG: Dict[str, List[Dict[str, Any]]] = {
    "Long Method": [
        {
            "name": "Extract Method",
            "complexity": "low",
            "risk": "low",
            "impact": "high",
            "preconditions": ["has_code_block"],
        },
        {
            "name": "Replace Temp with Query",
            "complexity": "medium",
            "risk": "low",
            "impact": "medium",
            "preconditions": ["has_temp_variables"],
        },
        {
            "name": "Introduce Parameter Object",
            "complexity": "medium",
            "risk": "medium",
            "impact": "medium",
            "preconditions": ["has_multiple_parameters"],
        },
    ],
    "God Class": [
        {
            "name": "Extract Class",
            "complexity": "high",
            "risk": "medium",
            "impact": "high",
            "preconditions": ["has_multiple_responsibilities"],
        },
        {
            "name": "Extract Subclass",
            "complexity": "high",
            "risk": "high",
            "impact": "high",
            "preconditions": ["has_multiple_responsibilities"],
        },
    ],
    "Feature Envy": [
        {
            "name": "Move Method",
            "complexity": "low",
            "risk": "medium",
            "impact": "high",
            "preconditions": ["has_external_field_access"],
        },
    ],
    "Duplicate Code": [
        {
            "name": "Extract Method",
            "complexity": "low",
            "risk": "low",
            "impact": "high",
            "preconditions": ["has_code_block"],
        },
        {
            "name": "Pull Up Method",
            "complexity": "medium",
            "risk": "medium",
            "impact": "high",
            "preconditions": ["has_parent_class"],
        },
    ],
    "Data Clumps": [
        {
            "name": "Introduce Parameter Object",
            "complexity": "medium",
            "risk": "low",
            "impact": "medium",
            "preconditions": ["has_multiple_parameters"],
        },
        {
            "name": "Extract Class",
            "complexity": "medium",
            "risk": "medium",
            "impact": "medium",
            "preconditions": ["has_multiple_responsibilities"],
        },
    ],
    "Shotgun Surgery": [
        {
            "name": "Move Method",
            "complexity": "medium",
            "risk": "medium",
            "impact": "high",
            "preconditions": ["has_external_field_access"],
        },
        {
            "name": "Inline Class",
            "complexity": "medium",
            "risk": "high",
            "impact": "medium",
            "preconditions": ["has_thin_class"],
        },
    ],
    "Switch Statements": [
        {
            "name": "Replace Conditional with Polymorphism",
            "complexity": "high",
            "risk": "medium",
            "impact": "high",
            "preconditions": ["has_type_checking"],
        },
    ],
    # ---- Extended catalog (inspired by Martin Fowler) ----
    "Lazy Class": [
        {
            "name": "Inline Class",
            "complexity": "low",
            "risk": "low",
            "impact": "medium",
            "preconditions": ["has_thin_class"],
        },
        {
            "name": "Collapse Hierarchy",
            "complexity": "medium",
            "risk": "medium",
            "impact": "medium",
            "preconditions": ["has_parent_class"],
        },
    ],
    "Speculative Generality": [
        {
            "name": "Collapse Hierarchy",
            "complexity": "medium",
            "risk": "low",
            "impact": "medium",
            "preconditions": ["has_parent_class"],
        },
        {
            "name": "Remove Dead Code",
            "complexity": "low",
            "risk": "low",
            "impact": "low",
            "preconditions": [],
        },
    ],
    "Primitive Obsession": [
        {
            "name": "Replace Data Value with Object",
            "complexity": "medium",
            "risk": "low",
            "impact": "medium",
            "preconditions": ["has_primitive_fields"],
        },
        {
            "name": "Introduce Parameter Object",
            "complexity": "medium",
            "risk": "low",
            "impact": "medium",
            "preconditions": ["has_multiple_parameters"],
        },
    ],
    "Long Parameter List": [
        {
            "name": "Introduce Parameter Object",
            "complexity": "medium",
            "risk": "low",
            "impact": "high",
            "preconditions": ["has_multiple_parameters"],
        },
        {
            "name": "Replace Parameter with Method Call",
            "complexity": "low",
            "risk": "low",
            "impact": "medium",
            "preconditions": ["has_computable_parameter"],
        },
    ],
    "Message Chains": [
        {
            "name": "Hide Delegate",
            "complexity": "low",
            "risk": "low",
            "impact": "medium",
            "preconditions": ["has_chain_calls"],
        },
    ],
    "Comments": [
        {
            "name": "Extract Method",
            "complexity": "low",
            "risk": "low",
            "impact": "medium",
            "preconditions": ["has_code_block"],
        },
        {
            "name": "Rename Method",
            "complexity": "low",
            "risk": "low",
            "impact": "low",
            "preconditions": [],
        },
    ],
    "Magic Numbers": [
        {
            "name": "Extract Method",
            "complexity": "low",
            "risk": "low",
            "impact": "medium",
            "preconditions": ["has_magic_numbers"],
        },
        {
            "name": "Introduce Constant",
            "complexity": "low",
            "risk": "low",
            "impact": "high",
            "preconditions": ["has_magic_numbers"],
        },
    ],
    "Inappropriate Intimacy": [
        {
            "name": "Move Method",
            "complexity": "medium",
            "risk": "medium",
            "impact": "high",
            "preconditions": ["has_external_field_access"],
        },
        {
            "name": "Extract Method",
            "complexity": "low",
            "risk": "low",
            "impact": "medium",
            "preconditions": ["has_code_block"],
        },
        {
            "name": "Introduce Facade",
            "complexity": "high",
            "risk": "medium",
            "impact": "high",
            "preconditions": ["has_multiple_dependencies"],
        },
    ],
    "Dead Code": [
        {
            "name": "Remove Dead Code",
            "complexity": "low",
            "risk": "low",
            "impact": "low",
            "preconditions": [],
        },
        {
            "name": "Inline Class",
            "complexity": "low",
            "risk": "low",
            "impact": "low",
            "preconditions": ["has_thin_class"],
        },
    ],
}


# ---------------------------------------------------------------------------
# Default Dependency Graph
# ---------------------------------------------------------------------------

# Maps a refactoring name → list of refactoring names that should be applied
# *before* it, when both appear in the same plan.

DEFAULT_DEPENDENCIES: Dict[str, List[str]] = {
    "Extract Class": ["Extract Method"],
    "Extract Subclass": ["Extract Method", "Extract Class"],
    "Move Method": ["Extract Method"],
    "Pull Up Method": ["Extract Method"],
    "Inline Class": ["Move Method"],
    "Collapse Hierarchy": ["Extract Method"],
    "Replace Conditional with Polymorphism": ["Extract Method"],
}


# ---------------------------------------------------------------------------
# RefactoringKnowledgeBase
# ---------------------------------------------------------------------------


class RefactoringKnowledgeBase:
    """Central knowledge store for refactoring rules and dependencies.

    This class encapsulates the catalog of smell-to-refactoring mappings and
    the dependency graph between refactorings. It is designed to be extensible:
    pass custom ``catalog`` or ``dependencies`` dicts, or subclass to add
    dynamic lookup logic.

    Args:
        catalog: Optional custom catalog. Defaults to :data:`DEFAULT_CATALOG`.
        dependencies: Optional custom dependency graph.
                      Defaults to :data:`DEFAULT_DEPENDENCIES`.
    """

    def __init__(
        self,
        catalog: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        dependencies: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        self.catalog = catalog if catalog is not None else DEFAULT_CATALOG
        self.dependencies = (
            dependencies if dependencies is not None else DEFAULT_DEPENDENCIES
        )

    def get_candidates(self, smell_type: str) -> List[Dict[str, Any]]:
        """Retrieve candidate refactorings for a given smell type.

        Args:
            smell_type: The code smell category (e.g., ``"Long Method"``).

        Returns:
            List of candidate dictionaries, or an empty list if the smell
            type is not in the catalog.
        """
        candidates = self.catalog.get(smell_type, [])
        if not candidates:
            logger.info("No catalog entry for smell type '%s'.", smell_type)
        return candidates

    def get_dependencies(self, refactoring_name: str) -> List[str]:
        """Retrieve prerequisite refactorings for a given refactoring.

        Args:
            refactoring_name: Name of the refactoring (e.g., ``"Extract Class"``).

        Returns:
            List of prerequisite refactoring names.
        """
        return self.dependencies.get(refactoring_name, [])

    def get_all_supported_smells(self) -> List[str]:
        """Return a list of all smell types supported by this catalog.

        Returns:
            Sorted list of smell type strings.
        """
        return sorted(self.catalog.keys())
