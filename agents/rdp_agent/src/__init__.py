"""
Refactoring Decision & Planning Agent (RDP Agent)
===================================================

A modular, research-grade implementation of a refactoring planning agent.
This package receives quality reports (JSON) from a Code Understanding Agent,
analyzes code smells, selects optimal refactorings, sequences them respecting
dependencies, and produces structured refactoring plans (JSON) for a
Safe Transformation Agent.

Architecture (8-step pipeline):
    1. **Problem Interpreter** — Interpret detected problems from smells.
    2. **Knowledge Base** — Map smells to refactoring techniques.
    3. **Candidate Generator** — Generate multiple candidate strategies.
    3b. **Impact Predictor** — Predict quality-metric impact of each candidate.
    4. **Decision Engine** — Evaluate via weighted scoring with predicted impacts.
    5. **Dependency Analyzer** — Analyze inter-refactoring dependencies.
    6. **Dependency Analyzer** — Determine execution order (topological sort).
    7. **Plan Generator** — Produce a machine-executable refactoring plan.

Quick start::

    from rdp_agent import generate_plan, generate_plan_from_dict

    # From file
    plan = generate_plan("quality_report.json", "plan.json")

    # From dict
    plan_dict = generate_plan_from_dict(report_data)

    # Using the OOP interface
    from rdp_agent import RDPAgent, QualityReport
    agent = RDPAgent()
    report = QualityReport.from_dict(data)
    plan = agent.process_report(report)
"""

from __future__ import annotations

# --- Data Models ---
from .models import (
    CodeSmell,
    QualityReport,
    RefactoringStep,
    RefactoringPlan,
    ImpactPrediction,
    MLPrediction,
)

# --- Knowledge Base ---
from .knowledge_base import (
    RefactoringKnowledgeBase,
    DEFAULT_CATALOG,
    DEFAULT_DEPENDENCIES,
)

# --- Problem Interpreter ---
from .problem_interpreter import ProblemInterpreter

# --- Decision Engine ---
from .decision_engine import DecisionEngine, RATING_MAP

# --- Candidate Generator ---
from .candidate_generator import CandidateGenerator

# --- Impact Predictor ---
from .impact_predictor import ImpactPredictor, DEFAULT_PREDICTION_RULES

# --- ML Scorer (CodeBERT) ---
from .ml_scorer import MLScorer

# --- Dependency Analyzer ---
from .dependency_analyzer import DependencyAnalyzer, SEVERITY_ORDER

# --- Plan Generator ---
from .plan_generator import PlanGenerator

# --- Configuration ---
from .config import load_config, setup_logging

# --- Pipeline (orchestration) ---
from .pipeline import RDPAgent, generate_plan, generate_plan_from_dict

# --- Evaluation ---
from .evaluator import PlanEvaluation, evaluate_rdp_plan, evaluate_rdp_result

# --- Backward-compatible function aliases ---
from .problem_interpreter import ProblemInterpreter as _PI

# Create a module-level interpreter for backward compatibility
_default_interpreter = _PI()
check_preconditions = _default_interpreter.check_preconditions

# Create a module-level plan generator for backward compatibility
_default_plan_gen = PlanGenerator()
generate_explanation = _default_plan_gen.generate_explanation

# Create a module-level decision engine for backward compatibility
_default_engine = DecisionEngine()


def score_candidate(candidate, smell, weights=None):
    """Backward-compatible scoring function."""
    engine = DecisionEngine(weights=weights) if weights else _default_engine
    return engine.score_candidate(candidate, smell)


def select_best_candidate(smell, catalog, weights=None):
    """Backward-compatible candidate selection function."""
    kb = RefactoringKnowledgeBase(catalog=catalog)
    engine = DecisionEngine(weights=weights)
    gen = CandidateGenerator(kb, _default_interpreter, engine)
    return gen.select_best(smell)


def sequence_steps(selections, dependencies=None, severity_order=None):
    """Backward-compatible sequencing function."""
    kb = RefactoringKnowledgeBase(dependencies=dependencies)
    analyzer = DependencyAnalyzer(kb, severity_order=severity_order)
    return analyzer.sequence_steps(selections)


__all__ = [
    # Models
    "CodeSmell",
    "QualityReport",
    "RefactoringStep",
    "RefactoringPlan",
    "ImpactPrediction",
    "MLPrediction",
    # Components
    "RefactoringKnowledgeBase",
    "ProblemInterpreter",
    "DecisionEngine",
    "CandidateGenerator",
    "ImpactPredictor",
    "MLScorer",
    "DependencyAnalyzer",
    "PlanGenerator",
    # Pipeline
    "RDPAgent",
    "generate_plan",
    "generate_plan_from_dict",
    "PlanEvaluation",
    "evaluate_rdp_plan",
    "evaluate_rdp_result",
    # Config
    "load_config",
    "setup_logging",
    # Constants
    "DEFAULT_CATALOG",
    "DEFAULT_DEPENDENCIES",
    "DEFAULT_PREDICTION_RULES",
    "RATING_MAP",
    "SEVERITY_ORDER",
    # Backward-compatible functions
    "check_preconditions",
    "generate_explanation",
    "score_candidate",
    "select_best_candidate",
    "sequence_steps",
]
