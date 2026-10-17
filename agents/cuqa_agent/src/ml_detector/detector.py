"""
detector.py
-----------
Runs the trained smell model on Java source code.

This is the learned counterpart of the rule-based Java detector in
report_generator.py. It extracts features for every class and method, asks the
model for a probability per smell, and reports the entities whose probability
passes the threshold that was tuned during training.

Detection modes (environment variable CUQA_SMELL_DETECTOR or the `detector`
argument of generate_file_report):
    rules   Rule-based smells only. ML results are attached separately under
            "ml_analysis". This is the default and keeps existing output unchanged.
    hybrid  Rule-based smells plus the smells found by the model.
    ml      The model replaces the rules for the smell types it has learned.
            Smell types it has not learned still come from the rules.
    off     The model is not run at all. Output and speed are exactly as before
            the model was added.

Only Java is supported because the training data (MLCQ) is Java only.
"""

from __future__ import annotations

import os

from .java_features import JAVALANG_AVAILABLE, JavaFeatureError, extract_entities
from .model import SmellModelError, load_model

MODE_RULES = "rules"
MODE_ML = "ml"
MODE_HYBRID = "hybrid"
MODE_OFF = "off"
VALID_MODES = (MODE_RULES, MODE_HYBRID, MODE_ML, MODE_OFF)

# Enums and annotation types do not occur in the training data.
_SCORED_TYPE_KINDS = {"class", "interface", "method", "constructor"}

_ENTITY_LABEL = {"class": "Class", "interface": "Interface", "method": "Method", "constructor": "Constructor"}


def resolve_mode(requested: str | None = None) -> str:
    """Pick the detection mode from the request, then the environment, then the default."""
    mode = (requested or os.getenv("CUQA_SMELL_DETECTOR") or MODE_RULES).strip().lower()
    return mode if mode in VALID_MODES else MODE_RULES


def _include_experimental() -> bool:
    return os.getenv("CUQA_ML_INCLUDE_EXPERIMENTAL", "").strip().lower() in ("1", "true", "yes")


def is_available() -> bool:
    """True when both the parser and the model file can be used."""
    if not JAVALANG_AVAILABLE:
        return False
    try:
        load_model()
    except SmellModelError:
        return False
    return True


def model_info() -> dict:
    """Describe the loaded model, or explain why it is unavailable."""
    if not JAVALANG_AVAILABLE:
        return {"available": False, "detail": "javalang is not installed"}
    try:
        model = load_model()
    except SmellModelError as exc:
        return {"available": False, "detail": str(exc)}
    return {"available": True, **model.describe()}


def learned_smell_types(include_experimental: bool | None = None) -> set[str]:
    """Smell types the loaded model reports."""
    if include_experimental is None:
        include_experimental = _include_experimental()
    try:
        model = load_model()
    except SmellModelError:
        return set()
    return {
        smell_type for smell_type, clf in model.classifiers.items()
        if clf.enabled or include_experimental
    }


def _build_smell(clf, entity, probability: float) -> dict:
    label = _ENTITY_LABEL.get(entity.type_kind, "Entity")
    smell = {
        "type": clf.smell_type,
        "message": (
            f"{label} '{entity.name}' is predicted to be a {clf.display_name} "
            f"(model confidence {probability:.0%})"
        ),
        "line": entity.start_line,
        "severity": clf.severity,
        "entity": entity.name,
        "start_line": entity.start_line,
        "end_line": entity.end_line,
        "detected_by": "ml",
        "confidence": round(probability, 3),
        "evidence": {name: entity.features.get(name) for name in clf.evidence_features},
    }
    if entity.kind == "method":
        smell["parameter_count"] = entity.features.get("n_params", 0)
        smell["cyclomatic_complexity"] = entity.features.get("cyclomatic_complexity", 1)
    else:
        smell["method_count"] = entity.features.get("n_methods", 0)
    return smell


def detect_smells(source: str, include_experimental: bool | None = None) -> dict:
    """Score every class and method of a Java file with the trained model.

    Never raises. When the model cannot run, "status" says why and "smells" is empty.

    Returns:
        dict with keys status ("ok" | "model_unavailable" | "parse_failed" |
        "analysis_failed"), model_version, entities_scored and smells.
    """
    result = {"status": "ok", "model_version": None, "entities_scored": 0, "smells": []}
    if include_experimental is None:
        include_experimental = _include_experimental()

    if not JAVALANG_AVAILABLE:
        return {**result, "status": "model_unavailable", "detail": "javalang is not installed"}
    try:
        model = load_model()
    except SmellModelError as exc:
        return {**result, "status": "model_unavailable", "detail": str(exc)}
    result["model_version"] = model.version

    classifiers = {
        kind: model.for_entity(kind, include_disabled=include_experimental)
        for kind in ("class", "method")
    }
    smells: list[dict] = []
    scored = 0
    try:
        for entity in extract_entities(source):
            if entity.type_kind not in _SCORED_TYPE_KINDS:
                continue
            scored += 1
            for clf in classifiers[entity.kind]:
                probability = clf.predict_proba(entity.features)
                if probability >= clf.threshold:
                    smells.append(_build_smell(clf, entity, probability))
    except JavaFeatureError as exc:
        return {**result, "status": "parse_failed", "detail": str(exc)}
    except Exception as exc:
        # The ML pass is an addition to the report and must never take it down.
        return {**result, "status": "analysis_failed", "detail": type(exc).__name__}

    result["entities_scored"] = scored
    result["smells"] = smells
    return result


def merge_with_rule_smells(rule_smells: list[dict], ml_smells: list[dict], mode: str) -> list[dict]:
    """Combine rule-based and model-based smells for the 'hybrid' and 'ml' modes.

    hybrid: every rule smell is kept. A model smell on the same entity and of the
            same type is folded into the rule smell instead of being listed twice.
    ml:     rule smells of a type the model has learned are dropped, the model
            decides those. All other rule smells are kept.
    """
    if mode == MODE_ML:
        learned = learned_smell_types()
        merged = [dict(s, detected_by="rule") for s in rule_smells if s.get("type") not in learned]
        return merged + [dict(s) for s in ml_smells]

    merged = [dict(s, detected_by="rule") for s in rule_smells]
    for ml_smell in ml_smells:
        match = None
        for rule_smell in merged:
            line = rule_smell.get("line")
            if (rule_smell.get("type") == ml_smell["type"]
                    and rule_smell.get("entity") == ml_smell["entity"]
                    and line is not None
                    and ml_smell["start_line"] <= line <= ml_smell["end_line"]):
                match = rule_smell
                break
        if match is None:
            merged.append(dict(ml_smell))
        else:
            match["detected_by"] = "rule+ml"
            match["confidence"] = ml_smell["confidence"]
    return merged
