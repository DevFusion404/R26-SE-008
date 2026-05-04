"""
Candidate Generator
====================

Generates and selects the best refactoring candidate for each detected
code smell. This module combines Steps 2–4 of the agent pipeline:

1. Retrieves candidate refactorings from the knowledge base (Step 2).
2. Filters candidates whose preconditions are satisfied (Step 3).
3. Scores viable candidates using the decision engine and selects the
   best one (Step 4).

The :class:`CandidateGenerator` can be extended by injecting custom
:class:`ProblemInterpreter`, :class:`DecisionEngine`, or
:class:`RefactoringKnowledgeBase` instances.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .models import CodeSmell
from .knowledge_base import RefactoringKnowledgeBase
from .problem_interpreter import ProblemInterpreter
from .decision_engine import DecisionEngine

logger = logging.getLogger("rdp_agent.candidate_generator")


class CandidateGenerator:
    """Selects the best refactoring candidate for each code smell.

    Wires together the knowledge base, problem interpreter, and decision
    engine to produce a single best candidate per smell.

    Args:
        knowledge_base: The refactoring knowledge base to draw candidates from.
        interpreter: The problem interpreter for precondition evaluation.
        engine: The decision engine for scoring and ranking.
    """

    def __init__(
        self,
        knowledge_base: RefactoringKnowledgeBase,
        interpreter: ProblemInterpreter,
        engine: DecisionEngine,
    ) -> None:
        self.knowledge_base = knowledge_base
        self.interpreter = interpreter
        self.engine = engine

    def select_best(self, smell: CodeSmell) -> Optional[Dict[str, Any]]:
        """Select the best refactoring candidate for a given smell.

        Workflow:
            1. Retrieve candidates from the catalog.
            2. Filter candidates whose preconditions are satisfied.
            3. Score and return the highest-scoring candidate.

        Args:
            smell: A detected code smell.

        Returns:
            The best candidate dict, or ``None`` if no candidate applies.
        """
        # Step 2: Retrieve candidates from knowledge base
        candidates = self.knowledge_base.get_candidates(smell.type)
        if not candidates:
            logger.info(
                "No catalog entry for smell type '%s' (smell %s).",
                smell.type,
                smell.id,
            )
            return None

        # Step 3: Filter by preconditions
        viable = [
            c
            for c in candidates
            if self.interpreter.check_preconditions(
                c.get("preconditions", []), smell
            )
        ]
        if not viable:
            logger.info(
                "All candidates for smell %s (%s) failed preconditions.",
                smell.id,
                smell.type,
            )
            return None

        # Step 4: Score and pick best
        scored = [
            (self.engine.score_candidate(c, smell), c) for c in viable
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        best_score, best = scored[0]
        logger.info(
            "Selected '%s' (score=%.2f) for smell %s (%s).",
            best["name"],
            best_score,
            smell.id,
            smell.type,
        )
        return best

    def select_all_viable(
        self, smell: CodeSmell
    ) -> List[Dict[str, Any]]:
        """Return all viable candidates for a smell, sorted by score.

        Useful for debugging or generating alternative strategy reports.

        Args:
            smell: A detected code smell.

        Returns:
            List of candidate dicts sorted by descending score.
        """
        candidates = self.knowledge_base.get_candidates(smell.type)
        viable = [
            c
            for c in candidates
            if self.interpreter.check_preconditions(
                c.get("preconditions", []), smell
            )
        ]
        scored = [
            (self.engine.score_candidate(c, smell), c) for c in viable
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored]
