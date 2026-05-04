#!/usr/bin/env python3
"""
Refactoring Decision & Planning Agent (RDP Agent)
==================================================

Part of a multi-agent refactoring system. This agent receives a quality report
(JSON) from a Code Understanding Agent, analyzes code smells, selects optimal
refactorings, sequences them respecting dependencies, and produces a structured
refactoring plan (JSON) for a Safe Transformation Agent.

Usage:
    python rdp_agent.py --input quality_report.json --output refactoring_plan.json
    python rdp_agent.py --input quality_report.json --output plan.json --config config.yaml

Author: Refactoring Decision Agent Prototype
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------

logger = logging.getLogger("rdp_agent")


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the RDP Agent.

    Args:
        level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class CodeSmell:
    """Represents a single code smell detected by the Code Understanding Agent.

    Attributes:
        id: Unique identifier for the smell (e.g., ``smell_001``).
        type: Category of the smell (e.g., ``Long Method``).
        location: Dictionary with ``class``, ``method``, and ``lines`` keys.
        metrics: Dictionary of quantitative metrics (e.g., LOC, complexity).
        severity: One of ``low``, ``medium``, ``high``, ``critical``.
        details: Optional free-text description or additional context.
    """

    id: str
    type: str
    location: Dict[str, Any]
    metrics: Dict[str, Any]
    severity: str
    details: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary."""
        d = asdict(self)
        if d.get("details") is None:
            d.pop("details", None)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CodeSmell":
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with CodeSmell fields.

        Returns:
            A new ``CodeSmell`` instance.
        """
        return cls(
            id=data["id"],
            type=data["type"],
            location=data["location"],
            metrics=data.get("metrics", {}),
            severity=data.get("severity", "medium"),
            details=data.get("details"),
        )


@dataclass
class QualityReport:
    """Report produced by the Code Understanding Agent.

    Attributes:
        target: File or module being analyzed (e.g., ``OrderProcessor.java``).
        smells: List of detected :class:`CodeSmell` instances.
        metrics_summary: Aggregate metrics for the target (e.g., total LOC).
        file_name: Optional file name from the first agent (used as fallback
                   for ``target`` when the report uses ``file_name`` instead).
    """

    target: str
    smells: List[CodeSmell]
    metrics_summary: Dict[str, Any] = field(default_factory=dict)
    file_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary."""
        d: Dict[str, Any] = {
            "target": self.target,
            "smells": [s.to_dict() for s in self.smells],
            "metrics_summary": self.metrics_summary,
        }
        if self.file_name is not None:
            d["file_name"] = self.file_name
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QualityReport":
        """Deserialize from a dictionary.

        The ``target`` field is resolved with a fallback chain:
        ``target`` → ``file_name`` → ``"unknown"``.

        Args:
            data: Dictionary with QualityReport fields.

        Returns:
            A new ``QualityReport`` instance.
        """
        smells = [CodeSmell.from_dict(s) for s in data.get("smells", [])]
        target = data.get("target") or data.get("file_name", "unknown")
        return cls(
            target=target,
            smells=smells,
            metrics_summary=data.get("metrics_summary", {}),
            file_name=data.get("file_name"),
        )


@dataclass
class RefactoringStep:
    """A single step in the refactoring plan.

    Attributes:
        step_id: Ordinal position within the plan.
        smell_id: ID of the code smell this step addresses.
        refactoring: Name of the refactoring technique.
        target: Dictionary identifying the target (class, method, etc.).
        parameters: Additional parameters for the transformation agent.
        explanation: Human-readable rationale for this step.
    """

    step_id: int
    smell_id: str
    refactoring: str
    target: Dict[str, Any]
    parameters: Dict[str, Any]
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RefactoringStep":
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with RefactoringStep fields.

        Returns:
            A new ``RefactoringStep`` instance.
        """
        return cls(
            step_id=data["step_id"],
            smell_id=data["smell_id"],
            refactoring=data["refactoring"],
            target=data.get("target", {}),
            parameters=data.get("parameters", {}),
            explanation=data.get("explanation", ""),
        )


@dataclass
class RefactoringPlan:
    """Complete refactoring plan to be consumed by the Safe Transformation Agent.

    Attributes:
        plan_id: Unique identifier for the plan.
        target: File or module being refactored.
        steps: Ordered list of :class:`RefactoringStep` instances.
        summary: Human-readable summary of the entire plan.
    """

    plan_id: str
    target: str
    steps: List[RefactoringStep]
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "plan_id": self.plan_id,
            "target": self.target,
            "steps": [s.to_dict() for s in self.steps],
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RefactoringPlan":
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with RefactoringPlan fields.

        Returns:
            A new ``RefactoringPlan`` instance.
        """
        steps = [RefactoringStep.from_dict(s) for s in data.get("steps", [])]
        return cls(
            plan_id=data["plan_id"],
            target=data["target"],
            steps=steps,
            summary=data.get("summary", ""),
        )


# ---------------------------------------------------------------------------
# Refactoring Catalog
# ---------------------------------------------------------------------------

# Default catalog mapping smell types → candidate refactorings.
# Each candidate is a dict with: name, complexity, risk, impact, preconditions.

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
}


# ---------------------------------------------------------------------------
# Dependency Graph
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
# Precondition Checker
# ---------------------------------------------------------------------------


def check_preconditions(preconditions: List[str], smell: CodeSmell) -> bool:
    """Evaluate whether a candidate's preconditions are satisfied for a smell.

    Preconditions are simple heuristic checks based on the smell's metrics,
    location, and type.  They are intentionally lenient — if the data needed
    to evaluate a check is missing, the check passes (open-world assumption).

    Args:
        preconditions: List of precondition tag strings.
        smell: The code smell to check against.

    Returns:
        ``True`` if **all** preconditions are satisfied, ``False`` otherwise.
    """
    for pc in preconditions:
        if not _evaluate_precondition(pc, smell):
            logger.debug(
                "Precondition '%s' failed for smell %s (%s)",
                pc,
                smell.id,
                smell.type,
            )
            return False
    return True


def _evaluate_precondition(precondition: str, smell: CodeSmell) -> bool:
    """Evaluate a single precondition string against a smell.

    Args:
        precondition: Tag identifying the check to perform.
        smell: The code smell context.

    Returns:
        ``True`` if the precondition is satisfied or cannot be evaluated.
    """
    metrics = smell.metrics
    location = smell.location

    if precondition == "has_code_block":
        # Satisfied if we have a line range with more than one line
        lines = location.get("lines", [])
        if isinstance(lines, list) and len(lines) >= 2:
            return (lines[1] - lines[0]) > 1
        return True  # cannot evaluate → assume OK

    if precondition == "has_temp_variables":
        # Heuristic: long methods likely have temps
        loc = metrics.get("lines_of_code", 0)
        return loc > 10

    if precondition == "has_multiple_parameters":
        param_count = metrics.get("parameter_count", None)
        if param_count is not None:
            return param_count >= 3
        return True  # cannot evaluate → assume OK

    if precondition == "has_multiple_responsibilities":
        # Heuristic: high method count or high LOC indicates this
        method_count = metrics.get("method_count", None)
        loc = metrics.get("lines_of_code", 0)
        if method_count is not None:
            return method_count >= 5
        return loc > 50

    if precondition == "has_external_field_access":
        ext = metrics.get("external_field_accesses", None)
        if ext is not None:
            return ext >= 2
        # Feature Envy smell itself implies this
        return smell.type == "Feature Envy" or True

    if precondition == "has_parent_class":
        return bool(location.get("parent_class") or location.get("superclass"))

    if precondition == "has_thin_class":
        loc = metrics.get("lines_of_code", 0)
        method_count = metrics.get("method_count", 0)
        if loc > 0:
            return loc < 50 or method_count <= 3
        return True

    if precondition == "has_type_checking":
        cc = metrics.get("cyclomatic_complexity", 0)
        return cc >= 3

    if precondition == "has_primitive_fields":
        return bool(metrics.get("primitive_field_count", 0) >= 2) or True

    if precondition == "has_computable_parameter":
        return True  # heuristic; always allow

    if precondition == "has_chain_calls":
        chain_len = metrics.get("chain_length", 0)
        return chain_len >= 3 if chain_len else True

    # Unknown precondition → pass by default
    logger.warning("Unknown precondition '%s'; assuming satisfied.", precondition)
    return True


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

RATING_MAP: Dict[str, int] = {"low": 1, "medium": 2, "high": 3}


def score_candidate(
    candidate: Dict[str, Any],
    smell: CodeSmell,
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """Score a refactoring candidate for a given smell.

    The score is a weighted sum:
        score = w_c * (4 - complexity) + w_r * (4 - risk) + w_i * impact

    Lower complexity and risk are better (inverted), higher impact is better.

    Args:
        candidate: Refactoring candidate dictionary.
        smell: The code smell being addressed (unused here but available for
               future smell-aware scoring adjustments).
        weights: Optional dict with ``complexity_weight``, ``risk_weight``,
                 and ``impact_weight``.  Defaults to 0.2 / 0.4 / 0.4.

    Returns:
        Numeric score (higher is better).
    """
    if weights is None:
        weights = {}

    w_c = weights.get("complexity_weight", 0.2)
    w_r = weights.get("risk_weight", 0.4)
    w_i = weights.get("impact_weight", 0.4)

    complexity = RATING_MAP.get(candidate.get("complexity", "medium"), 2)
    risk = RATING_MAP.get(candidate.get("risk", "medium"), 2)
    impact = RATING_MAP.get(candidate.get("impact", "medium"), 2)

    score = w_c * (4 - complexity) + w_r * (4 - risk) + w_i * impact
    return score


# ---------------------------------------------------------------------------
# Candidate Selection
# ---------------------------------------------------------------------------


def select_best_candidate(
    smell: CodeSmell,
    catalog: Dict[str, List[Dict[str, Any]]],
    weights: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    """Select the best refactoring candidate for a smell.

    1. Retrieve candidates from the catalog.
    2. Filter those whose preconditions are satisfied.
    3. Score the remaining candidates and return the highest-scoring one.

    Args:
        smell: A detected code smell.
        catalog: Refactoring catalog mapping smell types → candidates.
        weights: Optional scoring weights.

    Returns:
        The best candidate dict, or ``None`` if no candidate applies.
    """
    candidates = catalog.get(smell.type, [])
    if not candidates:
        logger.info("No catalog entry for smell type '%s' (smell %s).", smell.type, smell.id)
        return None

    # Filter by preconditions
    viable = [c for c in candidates if check_preconditions(c.get("preconditions", []), smell)]
    if not viable:
        logger.info(
            "All candidates for smell %s (%s) failed preconditions.",
            smell.id,
            smell.type,
        )
        return None

    # Score and pick best
    scored = [(score_candidate(c, smell, weights), c) for c in viable]
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


# ---------------------------------------------------------------------------
# Dependency-Aware Sequencing
# ---------------------------------------------------------------------------

SEVERITY_ORDER: Dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


def sequence_steps(
    selections: List[Tuple[CodeSmell, Dict[str, Any]]],
    dependencies: Optional[Dict[str, List[str]]] = None,
    severity_order: Optional[Dict[str, int]] = None,
) -> List[Tuple[CodeSmell, Dict[str, Any]]]:
    """Order selected refactorings respecting dependencies.

    Uses a greedy algorithm:
      1. Identify items whose dependency prerequisites are satisfied.
      2. Among those, pick the one with the highest severity.
      3. Repeat until all items are placed.
      4. If a deadlock occurs (circular deps), force the highest-severity
         item with a warning.

    Args:
        selections: List of ``(smell, candidate)`` tuples.
        dependencies: Dependency graph (refactoring name → prerequisite names).
        severity_order: Mapping of severity strings to numeric priority.

    Returns:
        Ordered list of ``(smell, candidate)`` tuples.
    """
    if dependencies is None:
        dependencies = DEFAULT_DEPENDENCIES
    if severity_order is None:
        severity_order = SEVERITY_ORDER

    remaining = list(selections)
    ordered: List[Tuple[CodeSmell, Dict[str, Any]]] = []
    applied_names: set[str] = set()

    # Set of all refactoring names in the current plan (for relevance check)
    all_selected_names = {c["name"] for _, c in selections}

    while remaining:
        # Find items whose deps are satisfied
        ready = []
        for item in remaining:
            smell, candidate = item
            deps = dependencies.get(candidate["name"], [])
            # A dep is satisfied if it's already applied OR not in the plan
            satisfied = all(
                dep in applied_names or dep not in all_selected_names
                for dep in deps
            )
            if satisfied:
                ready.append(item)

        if not ready:
            # Deadlock — force the highest-severity item
            remaining.sort(
                key=lambda x: severity_order.get(x[0].severity, 0),
                reverse=True,
            )
            forced = remaining[0]
            logger.warning(
                "Dependency deadlock detected. Forcing '%s' for smell %s.",
                forced[1]["name"],
                forced[0].id,
            )
            ready = [forced]

        # Pick highest severity among ready items
        ready.sort(
            key=lambda x: severity_order.get(x[0].severity, 0),
            reverse=True,
        )
        chosen = ready[0]
        ordered.append(chosen)
        applied_names.add(chosen[1]["name"])
        remaining.remove(chosen)

    return ordered


# ---------------------------------------------------------------------------
# Explanation Generator
# ---------------------------------------------------------------------------


def generate_explanation(smell: CodeSmell, candidate: Dict[str, Any]) -> str:
    """Generate a human-readable explanation for a refactoring step.

    Args:
        smell: The code smell being addressed.
        candidate: The chosen refactoring candidate.

    Returns:
        Explanation string.
    """
    target_str = _format_target(smell.location)
    smell_type = smell.type
    ref_name = candidate["name"]
    impact = candidate.get("impact", "medium")
    risk = candidate.get("risk", "medium")
    complexity = candidate.get("complexity", "medium")

    # Base explanation
    explanation = (
        f"{ref_name} on {target_str} to address {smell_type} smell. "
        f"Expected {impact} impact with {risk} risk and {complexity} complexity."
    )

    # Smell-specific enrichment
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


def _format_target(location: Dict[str, Any]) -> str:
    """Format a location dictionary as a readable string.

    Args:
        location: Dictionary with ``class`` and/or ``method`` keys.

    Returns:
        Formatted string like ``OrderProcessor.calculateTotal``.
    """
    cls = location.get("class", "")
    method = location.get("method", "")
    if cls and method:
        return f"{cls}.{method}"
    return cls or method or "unknown"


# ---------------------------------------------------------------------------
# Configuration Loader
# ---------------------------------------------------------------------------


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load configuration from a YAML or JSON file.

    Args:
        config_path: Path to the configuration file.  If ``None``, returns
                     default configuration values.

    Returns:
        Configuration dictionary.
    """
    defaults: Dict[str, Any] = {
        "weights": {
            "complexity_weight": 0.2,
            "risk_weight": 0.4,
            "impact_weight": 0.4,
        },
        "severity_order": {
            "critical": 4,
            "high": 3,
            "medium": 2,
            "low": 1,
        },
        "log_level": "INFO",
    }

    if config_path is None:
        return defaults

    if not os.path.isfile(config_path):
        logger.warning("Config file '%s' not found; using defaults.", config_path)
        return defaults

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            if config_path.endswith((".yaml", ".yml")):
                try:
                    import yaml  # type: ignore[import-untyped]

                    user_config = yaml.safe_load(f) or {}
                except ImportError:
                    logger.warning(
                        "PyYAML not installed; cannot read YAML config. Using defaults."
                    )
                    return defaults
            else:
                user_config = json.load(f)

        # Merge with defaults
        merged = {**defaults, **user_config}
        if "weights" in user_config:
            merged["weights"] = {**defaults["weights"], **user_config["weights"]}
        if "severity_order" in user_config:
            merged["severity_order"] = {
                **defaults["severity_order"],
                **user_config["severity_order"],
            }
        return merged

    except Exception as exc:
        logger.error("Error loading config '%s': %s. Using defaults.", config_path, exc)
        return defaults


# ---------------------------------------------------------------------------
# Plan Builder Helpers
# ---------------------------------------------------------------------------


def _generate_plan_id() -> str:
    """Generate a unique plan ID based on the current timestamp.

    Returns:
        Plan ID string like ``plan_20250321_001``.
    """
    now = datetime.now()
    return f"plan_{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}"


def _build_parameters(candidate: Dict[str, Any], smell: CodeSmell) -> Dict[str, Any]:
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

    if name == "Extract Method":
        lines = location.get("lines", [])
        if isinstance(lines, list) and len(lines) == 2:
            params["source_lines"] = lines
        params["new_method_name"] = f"extracted_{location.get('method', 'block')}"

    elif name == "Move Method":
        params["source_class"] = location.get("class", "")
        params["method"] = location.get("method", "")
        # Feature Envy target: use details or infer
        if smell.details:
            params["destination_class"] = smell.details
        else:
            params["destination_class"] = "<inferred_target_class>"

    elif name == "Extract Class":
        params["source_class"] = location.get("class", "")
        params["new_class_name"] = f"{location.get('class', '')}Helper"

    elif name == "Extract Subclass":
        params["source_class"] = location.get("class", "")
        params["new_subclass_name"] = f"{location.get('class', '')}Subtype"

    elif name == "Introduce Parameter Object":
        params["method"] = location.get("method", "")
        params["parameter_object_name"] = f"{location.get('method', '')}Params"

    elif name == "Replace Conditional with Polymorphism":
        params["source_class"] = location.get("class", "")
        params["method"] = location.get("method", "")

    elif name == "Pull Up Method":
        params["source_class"] = location.get("class", "")
        params["method"] = location.get("method", "")
        params["target_class"] = location.get("parent_class", "<parent>")

    elif name == "Inline Class":
        params["class_to_inline"] = location.get("class", "")

    elif name == "Replace Temp with Query":
        params["method"] = location.get("method", "")

    elif name == "Collapse Hierarchy":
        params["source_class"] = location.get("class", "")
        params["parent_class"] = location.get("parent_class", "<parent>")

    elif name == "Replace Data Value with Object":
        params["source_class"] = location.get("class", "")

    elif name == "Hide Delegate":
        params["source_class"] = location.get("class", "")

    elif name == "Remove Dead Code":
        params["source_class"] = location.get("class", "")
        params["method"] = location.get("method", "")

    elif name == "Rename Method":
        params["source_class"] = location.get("class", "")
        params["method"] = location.get("method", "")
        params["new_name"] = f"descriptive_{location.get('method', 'method')}"

    elif name == "Replace Parameter with Method Call":
        params["method"] = location.get("method", "")

    return params


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

    refactoring_names = list(dict.fromkeys(s.refactoring for s in steps))
    addressed = len(steps)
    names_str = ", ".join(refactoring_names)

    return (
        f"{addressed}-step plan addressing {addressed} of {smells_count} "
        f"detected smells in {target}. Refactorings applied: {names_str}."
    )


# ---------------------------------------------------------------------------
# Main Orchestration
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
      4. Sequence the selected refactorings respecting dependencies.
      5. Build and save the ``RefactoringPlan`` as JSON.

    Args:
        input_json_path: Path to the quality report JSON file.
        output_json_path: Path where the refactoring plan JSON will be saved.
        config_path: Optional path to a YAML/JSON configuration file.

    Returns:
        The generated ``RefactoringPlan`` object.
    """
    # ---- Load configuration ----
    config = load_config(config_path)
    setup_logging(config.get("log_level", "INFO"))
    weights = config.get("weights", {})
    severity_order = config.get("severity_order", SEVERITY_ORDER)

    logger.info("Loading quality report from '%s'.", input_json_path)

    # ---- Load input ----
    with open(input_json_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    report = QualityReport.from_dict(raw)
    logger.info(
        "Parsed report for '%s' with %d smells.", report.target, len(report.smells)
    )

    # ---- Select best candidate for each smell ----
    catalog = DEFAULT_CATALOG  # could be overridden via config in future
    selections: List[Tuple[CodeSmell, Dict[str, Any]]] = []

    for smell in report.smells:
        best = select_best_candidate(smell, catalog, weights)
        if best is not None:
            selections.append((smell, best))
        else:
            logger.info("Skipping smell %s (%s): no viable refactoring.", smell.id, smell.type)

    logger.info(
        "Selected %d refactoring(s) out of %d smell(s).",
        len(selections),
        len(report.smells),
    )

    # ---- Sequence ----
    ordered = sequence_steps(
        selections,
        dependencies=DEFAULT_DEPENDENCIES,
        severity_order=severity_order,
    )

    # ---- Build plan ----
    plan_id = _generate_plan_id()
    steps: List[RefactoringStep] = []

    for idx, (smell, candidate) in enumerate(ordered, start=1):
        step = RefactoringStep(
            step_id=idx,
            smell_id=smell.id,
            refactoring=candidate["name"],
            target={
                k: v
                for k, v in smell.location.items()
                if k in ("class", "method")
            },
            parameters=_build_parameters(candidate, smell),
            explanation=generate_explanation(smell, candidate),
        )
        steps.append(step)

    plan = RefactoringPlan(
        plan_id=plan_id,
        target=report.target,
        steps=steps,
        summary=_build_summary(steps, report.target, len(report.smells)),
    )

    # ---- Save output ----
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
    weights = config.get("weights", {})
    severity_order = config.get("severity_order", SEVERITY_ORDER)

    report = QualityReport.from_dict(data)
    logger.info(
        "Parsed report for '%s' with %d smells.", report.target, len(report.smells)
    )

    catalog = DEFAULT_CATALOG
    selections: List[Tuple[CodeSmell, Dict[str, Any]]] = []

    for smell in report.smells:
        best = select_best_candidate(smell, catalog, weights)
        if best is not None:
            selections.append((smell, best))
        else:
            logger.info("Skipping smell %s (%s): no viable refactoring.", smell.id, smell.type)

    ordered = sequence_steps(
        selections,
        dependencies=DEFAULT_DEPENDENCIES,
        severity_order=severity_order,
    )

    plan_id = _generate_plan_id()
    steps: List[RefactoringStep] = []

    for idx, (smell, candidate) in enumerate(ordered, start=1):
        step = RefactoringStep(
            step_id=idx,
            smell_id=smell.id,
            refactoring=candidate["name"],
            target={
                k: v
                for k, v in smell.location.items()
                if k in ("class", "method")
            },
            parameters=_build_parameters(candidate, smell),
            explanation=generate_explanation(smell, candidate),
        )
        steps.append(step)

    plan = RefactoringPlan(
        plan_id=plan_id,
        target=report.target,
        steps=steps,
        summary=_build_summary(steps, report.target, len(report.smells)),
    )

    return plan.to_dict()


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse command-line arguments and run the plan generation pipeline."""
    parser = argparse.ArgumentParser(
        description="Refactoring Decision & Planning Agent (RDP Agent)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python rdp_agent.py --input quality_report.json "
            "--output refactoring_plan.json\n"
            "  python rdp_agent.py -i report.json -o plan.json -c config.yaml"
        ),
    )
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="Path to the quality report JSON (from Code Understanding Agent).",
    )
    parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="Path for the output refactoring plan JSON.",
    )
    parser.add_argument(
        "--config",
        "-c",
        default=None,
        help="Optional path to a YAML/JSON configuration file.",
    )
    args = parser.parse_args()

    plan = generate_plan(args.input, args.output, args.config)
    print(f"\n✅ Plan '{plan.plan_id}' generated with {len(plan.steps)} step(s).")
    print(f"   Summary: {plan.summary}")
    print(f"   Output:  {args.output}\n")


if __name__ == "__main__":
    main()
