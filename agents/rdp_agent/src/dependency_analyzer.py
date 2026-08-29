"""
Dependency Analyzer
====================

Analyzes dependencies between refactoring operations and determines the
correct execution order. This module implements Steps 5–6 of the agent
pipeline:

- **Step 5**: Identify which refactorings depend on others.
- **Step 6**: Order refactorings using a greedy topological sort with
  severity-based tie-breaking.

Algorithm:
    1. Identify items whose dependency prerequisites are already satisfied
       (or not present in the current plan).
    2. Among those ready items, pick the one with the highest severity.
    3. Repeat until all items are placed.
    4. Safety guarantee: if a circular dependency deadlock is detected,
       a ``ValueError`` is raised and plan generation is aborted — the
       agent will NEVER force an unsafe execution order.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .models import CodeSmell
from .knowledge_base import RefactoringKnowledgeBase

logger = logging.getLogger("rdp_agent.dependency_analyzer")

# Default severity priority mapping (higher = more urgent)
SEVERITY_ORDER: Dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


class DependencyAnalyzer:
    """Analyzes and resolves dependencies between refactoring steps.

    Uses a greedy topological sort combined with severity-based priority
    to produce a valid execution order.

    Args:
        knowledge_base: The knowledge base providing dependency rules.
        severity_order: Optional custom severity priority mapping.
    """

    def __init__(
        self,
        knowledge_base: RefactoringKnowledgeBase,
        severity_order: Optional[Dict[str, int]] = None,
    ) -> None:
        self.knowledge_base = knowledge_base
        self.severity_order = (
            severity_order if severity_order is not None else SEVERITY_ORDER
        )

    def resolve_plan_conflicts(
        self,
        selections: List[Tuple[CodeSmell, Dict[str, Any]]],
    ) -> List[Tuple[CodeSmell, Dict[str, Any]]]:
        """Resolve entity-level refactoring conflicts before building final plan.

        1. If an entity (function, class, or variable) is scheduled for 'Remove Dead Code'
           (or addresses a 'Dead Code' / 'UnreachableCode' smell), ALL other non-deletion
           refactorings ('Move Method', 'Extract Method', 'Introduce Constant', 'Inline Class',
           'Rename Method', etc.) targeting that same entity are pruned. 'Remove Dead Code' takes priority.
        2. Deduplicates duplicate refactorings targeting the exact same entity.

        Args:
            selections: List of (smell, candidate) tuples.

        Returns:
            Filtered list of (smell, candidate) tuples with conflicts resolved.
        """
        if not selections:
            return []

        # 1. Collect entities marked for dead code removal
        dead_entities: set = set()
        for smell, candidate in selections:
            if candidate.get("name") == "Remove Dead Code" or smell.type in ("Dead Code", "DeadCode", "UnreachableCode"):
                ent = (
                    smell.location.get("method")
                    or smell.location.get("class")
                    or smell.location.get("entity")
                )
                if ent and ent != "unknown":
                    dead_entities.add(ent)

        filtered: List[Tuple[CodeSmell, Dict[str, Any]]] = []
        seen_entity_refactorings: set = set()

        for smell, candidate in selections:
            c_name = candidate.get("name", "")
            ent = (
                smell.location.get("method")
                or smell.location.get("class")
                or smell.location.get("entity")
            )

            # If entity is scheduled for deletion as Dead Code, ignore any non-deletion refactorings
            if ent and ent in dead_entities and c_name != "Remove Dead Code" and smell.type not in ("Dead Code", "DeadCode", "UnreachableCode"):
                logger.warning(
                    "Pruned conflicting step '%s' for entity '%s' because the entity is scheduled for Remove Dead Code.",
                    c_name,
                    ent,
                )
                continue

            # Deduplicate identical refactoring on the same entity
            if ent:
                dedup_key = (ent, c_name)
                if dedup_key in seen_entity_refactorings:
                    logger.warning("Deduplicating duplicate refactoring '%s' for entity '%s'.", c_name, ent)
                    continue
                seen_entity_refactorings.add(dedup_key)

            filtered.append((smell, candidate))

        # Deduplicate Duplicate Code groups: generate only ONE shared helper per duplicate_group
        seen_dup_groups: set = set()
        final_filtered: List[Tuple[CodeSmell, Dict[str, Any]]] = []
        for smell, candidate in filtered:
            dup_group = getattr(smell, "duplicate_group", None) or smell.location.get("duplicate_group")
            if dup_group and isinstance(dup_group, list):
                group_key = tuple(sorted(dup_group))
                if group_key in seen_dup_groups:
                    logger.warning("Deduplicating shared helper step for duplicate_group %s.", group_key)
                    continue
                seen_dup_groups.add(group_key)
            final_filtered.append((smell, candidate))

        return final_filtered

    def sequence_steps(
        self,
        selections: List[Tuple[CodeSmell, Dict[str, Any]]],
    ) -> List[Tuple[CodeSmell, Dict[str, Any]]]:
        """Order selected refactorings respecting dependencies.

        Args:
            selections: List of ``(smell, candidate)`` tuples to sequence.

        Returns:
            Ordered list of ``(smell, candidate)`` tuples ready for
            sequential execution.
        """
        if not selections:
            return []

        remaining = list(selections)
        ordered: List[Tuple[CodeSmell, Dict[str, Any]]] = []
        applied_names: set = set()

        # Set of all refactoring names in the current plan (for relevance check)
        all_selected_names = {c["name"] for _, c in selections}

        while remaining:
            # Find items whose deps are satisfied
            ready = []
            for item in remaining:
                smell, candidate = item
                deps = self.knowledge_base.get_dependencies(candidate["name"])
                # A dep is satisfied if it's already applied OR not in the plan
                satisfied = all(
                    dep in applied_names or dep not in all_selected_names
                    for dep in deps
                )
                if satisfied:
                    ready.append(item)

            if not ready:
                # Deadlock — circular dependency detected.
                # DO NOT force execution: running a refactoring before its
                # prerequisites are applied can leave the codebase in an
                # inconsistent, broken state.
                circular_deps = [c["name"] for _, c in remaining]
                logger.error(
                    "Circular dependency deadlock detected in refactoring "
                    "sequence: %s. Plan generation aborted to prevent "
                    "inconsistent code state.",
                    " → ".join(circular_deps),
                )
                raise ValueError(
                    f"Circular dependency deadlock in refactoring plan: "
                    f"{circular_deps}. These refactorings cannot be safely "
                    f"ordered — plan generation aborted."
                )

            # Pick highest severity among ready items
            ready.sort(
                key=lambda x: self.severity_order.get(x[0].severity, 0),
                reverse=True,
            )
            chosen = ready[0]
            ordered.append(chosen)
            applied_names.add(chosen[1]["name"])
            remaining.remove(chosen)

        logger.info(
            "Sequenced %d refactoring step(s) respecting dependencies.",
            len(ordered),
        )
        return ordered
