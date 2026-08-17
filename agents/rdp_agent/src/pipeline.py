"""
RDP Agent Pipeline
===================

Main orchestration module that wires all components together and provides
the top-level :class:`RDPAgent` class plus convenience functions.

The pipeline follows the 8-step agent workflow:
    1. Interpret problems → :class:`ProblemInterpreter`
    2. Map smells → techniques → :class:`RefactoringKnowledgeBase`
    3. Generate candidates → :class:`CandidateGenerator`
    3b. Predict impact → :class:`ImpactPredictor`
    3c. ML scoring → :class:`MLScorer` (CodeBERT)
    4. Evaluate strategies → :class:`DecisionEngine`
    5. Analyze dependencies → :class:`DependencyAnalyzer`
    6. Determine order → :class:`DependencyAnalyzer`
    7. Generate plan → :class:`PlanGenerator`
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from .models import CodeSmell, QualityReport, RefactoringPlan
from .knowledge_base import RefactoringKnowledgeBase
from .problem_interpreter import ProblemInterpreter
from .decision_engine import DecisionEngine
from .candidate_generator import CandidateGenerator
from .dependency_analyzer import DependencyAnalyzer, SEVERITY_ORDER
from .plan_generator import PlanGenerator
from .impact_predictor import ImpactPredictor
from .ml_scorer import MLScorer
from .config import load_config, setup_logging

logger = logging.getLogger("rdp_agent.pipeline")


# ---------------------------------------------------------------------------
# RDP Agent (orchestrator)
# ---------------------------------------------------------------------------


class RDPAgent:
    """Central orchestrator that wires all pipeline components.

    This class provides a clean, object-oriented interface to the full
    refactoring planning pipeline. Components can be replaced or extended
    by passing custom instances to the constructor.

    Args:
        knowledge_base: Refactoring knowledge base (catalog + dependencies).
        interpreter: Problem interpreter for precondition checks.
        engine: Decision engine for candidate scoring.
        candidate_generator: Candidate generator (auto-built if not provided).
        dependency_analyzer: Dependency analyzer (auto-built if not provided).
        plan_generator: Plan generator (auto-built if not provided).
        impact_predictor: Impact predictor for estimating quality-metric
                          changes (auto-built if not provided).
        ml_scorer: CodeBERT-based ML scorer for embedding-based prediction
                   (auto-built if not provided and ML is enabled).
        ml_config: ML scoring configuration dict with ``enabled``,
                   ``model_name``, and ``ml_prediction_weight`` keys.
    """

    def __init__(
        self,
        knowledge_base: Optional[RefactoringKnowledgeBase] = None,
        interpreter: Optional[ProblemInterpreter] = None,
        engine: Optional[DecisionEngine] = None,
        candidate_generator: Optional[CandidateGenerator] = None,
        dependency_analyzer: Optional[DependencyAnalyzer] = None,
        plan_generator: Optional[PlanGenerator] = None,
        impact_predictor: Optional[ImpactPredictor] = None,
        ml_scorer: Optional[MLScorer] = None,
        severity_order: Optional[Dict[str, int]] = None,
        weights: Optional[Dict[str, float]] = None,
        ml_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        # Core components — use defaults if not provided
        self.knowledge_base = knowledge_base or RefactoringKnowledgeBase()
        self.interpreter = interpreter or ProblemInterpreter()
        self.engine = engine or DecisionEngine(weights=weights)

        # Impact prediction module (step 3b)
        self.impact_predictor = impact_predictor or ImpactPredictor()

        # ML scoring module (step 3c — CodeBERT)
        self._ml_config = ml_config or {}
        ml_enabled = self._ml_config.get("enabled", True)
        if ml_scorer is not None:
            self.ml_scorer = ml_scorer
        elif ml_enabled:
            model_name = self._ml_config.get(
                "model_name", "microsoft/codebert-base"
            )
            self.ml_scorer = MLScorer(model_name=model_name)
        else:
            self.ml_scorer = None

        # Composite components — auto-wire from core if not provided
        self.candidate_generator = candidate_generator or CandidateGenerator(
            knowledge_base=self.knowledge_base,
            interpreter=self.interpreter,
            engine=self.engine,
        )
        self.dependency_analyzer = dependency_analyzer or DependencyAnalyzer(
            knowledge_base=self.knowledge_base,
            severity_order=severity_order,
        )
        self.plan_generator = plan_generator or PlanGenerator()

    def process_report(self, report: QualityReport) -> RefactoringPlan:
        """Process a quality report through the full pipeline.

        Args:
            report: Parsed quality report from the Code Understanding Agent.

        Returns:
            A complete :class:`RefactoringPlan`.
        """
        result = self.process_report_with_trace(report)
        return result["plan_object"]

    def process_report_with_trace(
        self, report: QualityReport
    ) -> Dict[str, Any]:
        """Process a quality report and capture intermediate results from
        every pipeline module for visualization and debugging.

        Args:
            report: Parsed quality report from the Code Understanding Agent.

        Returns:
            Dictionary with keys:
                - ``plan_object``: The :class:`RefactoringPlan` instance.
                - ``plan``: The plan as a plain dict.
                - ``trace``: Detailed trace of each pipeline step.
        """
        logger.info(
            "Processing report for '%s' with %d smell(s).",
            report.target,
            len(report.smells),
        )

        trace: Dict[str, Any] = {
            "input_summary": {
                "target": report.target,
                "total_smells": len(report.smells),
                "smells": [
                    {
                        "id": s.id,
                        "type": s.type,
                        "severity": s.severity,
                        "location": s.location,
                        "metrics": s.metrics,
                    }
                    for s in report.smells
                ],
            },
            "problem_interpretation": [],
            "candidate_generation": [],
            "impact_prediction": [],
            "ml_prediction": [],
            "mcda_selection": [],
            "dependency_analysis": {},
            "plan_generation": {},
        }

        # Check ML scorer availability once
        ml_available = (
            self.ml_scorer is not None
            and self.ml_scorer.is_available()
        )

        # ----- STEP 1: Problem Interpretation -----
        # Build structured understanding of all problems before generating candidates
        problem_interpretation = self.interpreter.interpret_problems(
            report.smells, report.target
        )
        trace["problem_interpretation"] = problem_interpretation.to_dict()

        # ----- Steps 2-7: For each smell, generate + predict + decide -----
        selections: List[Tuple[CodeSmell, Dict[str, Any]]] = []

        for smell in report.smells:
            smell_trace: Dict[str, Any] = {
                "smell_id": smell.id,
                "smell_type": smell.type,
                "severity": smell.severity,
            }

            # Step 2: Get candidates from knowledge base
            all_candidates = self.knowledge_base.get_candidates(smell.type)
            smell_trace["candidates_from_catalog"] = len(all_candidates)

            # Step 1 & 3: Filter by preconditions
            viable_candidates = []
            candidate_details = []
            for c in all_candidates:
                preconditions = c.get("preconditions", [])
                passed = self.interpreter.check_preconditions(
                    preconditions, smell
                )

                if passed:
                    viable_candidates.append(c)

                candidate_details.append(
                    {
                        "name": c["name"],
                        "complexity": c.get("complexity", "medium"),
                        "risk": c.get("risk", "medium"),
                        "impact": c.get("impact", "medium"),
                        "preconditions": preconditions,
                        "preconditions_met": passed,
                    }
                )

            # ----- Step 3b: Impact prediction for viable candidates -----
            impact_trace: Dict[str, Any] = {
                "smell_id": smell.id,
                "smell_type": smell.type,
                "predictions": [],
            }

            impact_map: Dict[str, Any] = {}  # refactoring name → ImpactPrediction
            for c in viable_candidates:
                prediction = self.impact_predictor.predict(smell, c)
                impact_map[c["name"]] = prediction
                impact_trace["predictions"].append(prediction.to_dict())

            trace["impact_prediction"].append(impact_trace)

            # ----- Step 3c: ML scoring (CodeBERT) for viable candidates -----
            ml_trace: Dict[str, Any] = {
                "smell_id": smell.id,
                "smell_type": smell.type,
                "ml_available": ml_available,
                "predictions": [],
            }

            ml_map: Dict[str, Any] = {}  # refactoring name → MLPrediction
            if ml_available:
                for c in viable_candidates:
                    ml_pred = self.ml_scorer.predict(smell, c)
                    ml_map[c["name"]] = ml_pred
                    ml_trace["predictions"].append(ml_pred.to_dict())

            trace["ml_prediction"].append(ml_trace)

            # ----- Step 4b: MCDA Scoring (Multi-Criteria Decision Making) -----
            mcda_trace: Dict[str, Any] = {
                "smell_id": smell.id,
                "smell_type": smell.type,
                "predictions": [],
            }

            mcda_map: Dict[str, Any] = {}  # refactoring name → MCDA scores
            for c in viable_candidates:
                # Use neutral dependency score (0.5) for now
                # Can be enhanced with actual dependency graph analysis
                dependency_score = 0.5
                mcda_scores = self.engine.score_candidate_mcda(
                    c, smell, dependency_score
                )
                mcda_map[c["name"]] = mcda_scores
                mcda_trace["predictions"].append({
                    "refactoring": c["name"],
                    **mcda_scores
                })

            trace["mcda_selection"].append(mcda_trace)

            # ----- Step 4: Score candidates using MCDA -----
            for cd in candidate_details:
                if cd["preconditions_met"]:
                    # Find the matching candidate dict for scoring
                    matching_candidate = next(
                        (vc for vc in viable_candidates if vc["name"] == cd["name"]),
                        None,
                    )
                    if matching_candidate and cd["name"] in impact_map:
                        # Use ML-enhanced scoring if available
                        if cd["name"] in ml_map and ml_map[cd["name"]].confidence > 0:
                            cd["score"] = round(
                                self.engine.score_candidate_with_ml(
                                    matching_candidate,
                                    smell,
                                    impact_map[cd["name"]],
                                    ml_map[cd["name"]],
                                ),
                                2,
                            )
                            cd["scoring_method"] = "ml_enhanced"
                        else:
                            cd["score"] = round(
                                self.engine.score_candidate_with_impact(
                                    matching_candidate,
                                    smell,
                                    impact_map[cd["name"]],
                                ),
                                2,
                            )
                            cd["scoring_method"] = "impact_aware"
                    else:
                        cd["score"] = round(
                            self.engine.score_candidate(matching_candidate or {}, smell),
                            2,
                        )
                        cd["scoring_method"] = "base"
                else:
                    cd["score"] = None
                    cd["scoring_method"] = None

            smell_trace["candidates"] = candidate_details

            # Select best using MCDA scoring
            if viable_candidates:
                scored = []
                for vc in viable_candidates:
                    if vc["name"] in mcda_map:
                        # Use MCDA final score as primary criterion
                        s = mcda_map[vc["name"]]["final_score"]
                        scored.append((s, vc))
                
                if scored:
                    scored.sort(key=lambda x: x[0], reverse=True)
                    best_score, best = scored[0]
                    selections.append((smell, best))
                    smell_trace["selected"] = best["name"]
                    smell_trace["selected_score"] = round(best_score, 3)
                    smell_trace["scoring_method"] = "mcda"
                    smell_trace["mcda_details"] = mcda_map.get(best["name"], {})
                else:
                    smell_trace["selected"] = None
                    smell_trace["selected_score"] = None
                    smell_trace["scoring_method"] = None
            else:
                smell_trace["selected"] = None
                smell_trace["selected_score"] = None
                smell_trace["scoring_method"] = None

            trace["candidate_generation"].append(smell_trace)

        logger.info(
            "Selected %d refactoring(s) out of %d smell(s).",
            len(selections),
            len(report.smells),
        )

        try:
            ordered = self.dependency_analyzer.sequence_steps(selections)
        except ValueError as exc:
            logger.error("Plan generation failed due to circular dependencies: %s", exc)
            trace["dependency_analysis"] = {
                "error": str(exc),
                "rules_applied": {},
                "order_before": [
                    {"smell_id": s.id, "refactoring": c["name"]}
                    for s, c in selections
                ],
                "order_after": [],
                "reordered": False,
            }
            return {
                "plan_object": None,
                "plan": {
                    "error": str(exc),
                    "smells_with_circular_deps": [
                        {"smell_id": s.id, "refactoring": c["name"]}
                        for s, c in selections
                    ],
                },
                "trace": trace,
            }

        # Capture post-sequence order
        post_order = [
            {"smell_id": s.id, "refactoring": c["name"]}
            for s, c in ordered
        ]

        # Capture pre-sequence order
        pre_order = [
            {"smell_id": s.id, "refactoring": c["name"]}
            for s, c in selections
        ]

        # Collect relevant dependency rules
        dependency_rules = {}
        for _, c in selections:
            deps = self.knowledge_base.get_dependencies(c["name"])
            if deps:
                dependency_rules[c["name"]] = deps

        trace["dependency_analysis"] = {
            "rules_applied": dependency_rules,
            "order_before": pre_order,
            "order_after": post_order,
            "reordered": pre_order != post_order,
        }


        # ----- Step 7: Generate plan -----
        plan = self.plan_generator.build_plan(
            target=report.target,
            ordered_selections=ordered,
            total_smells=len(report.smells),
        )

        trace["plan_generation"] = {
            "plan_id": plan.plan_id,
            "total_steps": len(plan.steps),
            "smells_addressed": len(plan.steps),
            "smells_skipped": len(report.smells) - len(plan.steps),
            "summary": plan.summary,
        }

        return {
            "plan_object": plan,
            "plan": plan.to_dict(),
            "trace": trace,
        }


# ---------------------------------------------------------------------------
# Convenience Functions (backward-compatible)
# ---------------------------------------------------------------------------


def generate_plan(
    input_json_path: str,
    output_json_path: str,
    config_path: Optional[str] = None,
) -> RefactoringPlan:
    """Main orchestration function: analyse a quality report and produce a plan.

    Workflow:
      1. Load and parse the input quality report JSON.
      2. Load configuration (optional).
      3. For each smell, select the best refactoring candidate.
      3b. Predict impact of each candidate on quality metrics.
      3c. ML scoring via CodeBERT embeddings.
      4. Sequence the selected refactorings respecting dependencies.
      5. Build and save the ``RefactoringPlan`` as JSON.

    Args:
        input_json_path: Path to the quality report JSON file.
        output_json_path: Path where the refactoring plan JSON will be saved.
        config_path: Optional path to a YAML/JSON configuration file.

    Returns:
        The generated ``RefactoringPlan`` object.
    """
    # Load configuration
    config = load_config(config_path)
    setup_logging(config.get("log_level", "INFO"))

    logger.info("Loading quality report from '%s'.", input_json_path)

    # Load input
    with open(input_json_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    report = QualityReport.from_dict(raw)

    # Build and run agent
    agent = RDPAgent(
        weights=config.get("weights", {}),
        severity_order=config.get("severity_order", SEVERITY_ORDER),
        ml_config=config.get("ml_scoring", {}),
    )
    plan = agent.process_report(report)

    # Save output
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(plan.to_dict(), f, indent=2, ensure_ascii=False)

    logger.info("Refactoring plan saved to '%s'.", output_json_path)
    return plan


def generate_plan_from_dict(
    data: Dict[str, Any],
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a refactoring plan from an in-memory quality report dictionary.

    Convenience wrapper used by the web UI so that callers do not need to
    create temporary files.

    Args:
        data: Parsed JSON dictionary of the quality report.
        config_path: Optional path to a YAML/JSON configuration file.

    Returns:
        The generated refactoring plan as a plain dictionary.
    """
    config = load_config(config_path)
    setup_logging(config.get("log_level", "INFO"))

    report = QualityReport.from_dict(data)

    agent = RDPAgent(
        weights=config.get("weights", {}),
        severity_order=config.get("severity_order", SEVERITY_ORDER),
        ml_config=config.get("ml_scoring", {}),
    )
    plan = agent.process_report(report)

    return plan.to_dict()
