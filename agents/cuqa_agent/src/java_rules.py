"""
java_rules.py
-------------
Rule-based Java smells decided on the CUQA Java metrics.

The metrics come from ml_detector/java_features.py, the extractor the trained
smell model also reads. A rule here is a fixed threshold, or a fixed
combination of thresholds taken from the literature, on those metrics. The
model learns its own combination from labelled data. Because both read the
same measurements, the training scripts can score these rules on the labelled
datasets exactly as they run in CUQA.

Rules (entity, severity):
    ComplexMethod   method  medium  cyclomatic complexity > 10 (McCabe, 1976)
    DeepNesting     method  high    control-flow nesting depth > 3 (same limit as the C rule)
    FeatureEnvy     method  medium  after the Feature Envy strategy of Lanza and Marinescu (2006)
    DataClass       class   low     after the Data Class strategy of Lanza and Marinescu (2006)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

try:
    from ml_detector.java_features import JavaFeatureError, extract_entities
except ImportError:
    try:
        from .ml_detector.java_features import JavaFeatureError, extract_entities
    except ImportError:
        from agents.cuqa_agent.src.ml_detector.java_features import JavaFeatureError, extract_entities

COMPLEX_METHOD_CC_LIMIT = 10
DEEP_NESTING_LIMIT = 3

# Lanza and Marinescu describe their thresholds in words ("few", "one third",
# "high"). The numbers CUQA uses for them:
FEW = 5                 # Feature Envy: foreign data used, and providers it comes from
ONE_THIRD = 1 / 3
DATA_CLASS_FEW = 2      # public attributes + accessors
DATA_CLASS_MANY = 5
WMC_HIGH = 31
WMC_VERY_HIGH = 47


def foreign_data_accesses(f: dict) -> float:
    """ATFD: accessor calls on other objects plus direct reads of their fields."""
    return f.get("n_foreign_accessor_calls", 0) + f.get("n_foreign_field_access", 0)


def locality_of_attribute_accesses(f: dict) -> float:
    """LAA: share of the attribute accesses that go to the method's own class."""
    own = f.get("n_own_field_access", 0)
    total = own + foreign_data_accesses(f)
    return own / total if total else 1.0


def is_complex_method(f: dict) -> bool:
    return f.get("cyclomatic_complexity", 1) > COMPLEX_METHOD_CC_LIMIT


def is_deeply_nested(f: dict) -> bool:
    return f.get("max_nesting", 0) > DEEP_NESTING_LIMIT


def is_feature_envy(f: dict) -> bool:
    """Uses many attributes of other classes, few of its own, from only a few classes."""
    return (foreign_data_accesses(f) > FEW
            and locality_of_attribute_accesses(f) < ONE_THIRD
            and f.get("n_distinct_foreign_targets", 0) <= FEW)


def data_class_accessors(f: dict) -> float:
    """NOPA + NOAM: public attributes plus getters and setters."""
    return f.get("n_public_fields", 0) + f.get("n_getters", 0) + f.get("n_setters", 0)


def is_data_class(f: dict) -> bool:
    """Exposes data through accessors or public fields and has little behaviour of its own."""
    if f.get("is_interface") or f.get("is_enum"):
        return False
    exposed = data_class_accessors(f)
    wmc = f.get("wmc", 0)
    return (f.get("weight_of_class", 1.0) < ONE_THIRD
            and ((exposed > DATA_CLASS_FEW and wmc < WMC_HIGH)
                 or (exposed > DATA_CLASS_MANY and wmc < WMC_VERY_HIGH)))


@dataclass(frozen=True)
class MetricRule:
    smell_type: str
    entity: str                                  # "method" | "class"
    severity: str
    applies: Callable[[dict], bool]
    message: Callable[[str, dict], str]


METRIC_RULES: tuple[MetricRule, ...] = (
    MetricRule(
        "ComplexMethod", "method", "medium", is_complex_method,
        lambda name, f: (f"Method '{name}' has cyclomatic complexity {int(f['cyclomatic_complexity'])} "
                         f"(>{COMPLEX_METHOD_CC_LIMIT}) — consider Decompose Conditional or Extract Method"),
    ),
    MetricRule(
        "DeepNesting", "method", "high", is_deeply_nested,
        lambda name, f: (f"Control-flow nesting depth in method '{name}' is {int(f['max_nesting'])} "
                         f"(>{DEEP_NESTING_LIMIT}) — consider Replace Nested Conditional with Guard Clauses"),
    ),
    MetricRule(
        "FeatureEnvy", "method", "medium", is_feature_envy,
        lambda name, f: (f"Method '{name}' uses {int(foreign_data_accesses(f))} attributes of other objects "
                         f"and {int(f.get('n_own_field_access', 0))} of its own class — consider Move Method"),
    ),
    MetricRule(
        "DataClass", "class", "low", is_data_class,
        lambda name, f: (f"Class '{name}' exposes {int(data_class_accessors(f))} accessors and public fields "
                         f"but has little behaviour of its own (weight of class {f.get('weight_of_class', 0):.2f}) "
                         "— consider Move Method into it or Encapsulate Field"),
    ),
)


def _rule_smell(rule: MetricRule, entity) -> dict:
    f = entity.features
    smell = {
        "type": rule.smell_type,
        "message": rule.message(entity.name, f),
        "line": entity.start_line,
        "severity": rule.severity,
        "entity": entity.name,
        "start_line": entity.start_line,
        "end_line": entity.end_line,
    }
    if entity.kind == "method":
        smell["parameter_count"] = int(f.get("n_params", 0))
        smell["cyclomatic_complexity"] = int(f.get("cyclomatic_complexity", 1))
        if rule.smell_type == "DeepNesting":
            smell["nesting_depth"] = int(f.get("max_nesting", 0))
    else:
        smell["method_count"] = int(f.get("n_methods", 0))
    return smell


def detect_metric_smells(source: str) -> list[dict]:
    """Apply every metric rule to the classes and methods of a Java file.

    Methods without a body (abstract, interface) and enums, interfaces and
    annotation types are not checked. Returns [] when the file cannot be parsed.
    """
    try:
        entities = extract_entities(source)
    except JavaFeatureError:
        return []
    smells = []
    for entity in entities:
        if entity.kind == "method" and not entity.features.get("has_body", 1):
            continue
        if entity.kind == "class" and entity.type_kind != "class":
            continue
        for rule in METRIC_RULES:
            if rule.entity == entity.kind and rule.applies(entity.features):
                smells.append(_rule_smell(rule, entity))
    return smells
