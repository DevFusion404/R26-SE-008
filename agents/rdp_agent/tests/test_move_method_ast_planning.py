import sys
from pathlib import Path
import pytest

# Add pythonpath
sys.path.insert(0, r"D:\SLIIT\YEAR 4 SEM 1\IT4010\R26-SE-008")
sys.path.insert(0, r"D:\SLIIT\YEAR 4 SEM 1\IT4010\R26-SE-008\agents\rdp_agent")

from agents.rdp_agent.src.models import CodeSmell, QualityReport
from agents.rdp_agent.src.move_method_resolver import MoveMethodPlanResolver
from agents.rdp_agent.src.pipeline import RDPAgent, generate_plan_from_dict


MODULE_FUNCTION_PY = """
import os

def time():
    return 12345
"""

REAL_CLASSES_PY = """
class TaxService:
    def __init__(self):
        self.rate = 0.05

class OrderService:
    def calculate_tax(self, target: TaxService):
        return target.rate * 100
"""

SOURCECLASS_LITERAL_PY = """
class TargetClass:
    def __init__(self):
        self.data = 42

class SourceClass:
    def process_item(self, target: TargetClass):
        return target.data * 2
"""

SINGLE_CLASS_NO_DEST_PY = """
class OrderService:
    def calculate_tax(self, rate):
        return rate * 100
"""


def test_module_level_function_no_move_method_plan():
    """1. Module-level function -> no Move Method plan step generated."""
    report_dict = {
        "target": "utils.py",
        "language": "python",
        "source_files": [{
            "file_name": "utils.py",
            "source_code": MODULE_FUNCTION_PY,
            "language": "python",
        }],
        "smells": [{
            "id": "smell_001",
            "type": "Feature Envy",
            "severity": "medium",
            "location": {
                "file": "utils.py",
                "method": "time",
                "lines": [4, 5],
            },
            "details": "Function time() exhibits feature envy",
            "metrics": {},
        }],
    }
    plan = generate_plan_from_dict(report_dict)
    assert len(plan["steps"]) == 0, f"Expected 0 steps for module-level function, got {len(plan['steps'])}"


def test_module_level_function_target_kind_classification():
    """2. Module-level function -> target_kind = MODULE_FUNCTION, reason = TARGET_IS_MODULE_FUNCTION."""
    resolver = MoveMethodPlanResolver([{
        "file_name": "utils.py",
        "source_code": MODULE_FUNCTION_PY,
        "language": "python",
    }])
    smell = CodeSmell(
        id="smell_001",
        type="Feature Envy",
        location={"file": "utils.py", "method": "time", "lines": [4, 5]},
        metrics={},
        severity="medium",
    )
    res = resolver.resolve(smell)
    assert res["target_kind"] == "MODULE_FUNCTION"
    assert res["source_class"] is None
    assert res["source_method"] == "time"
    assert res["destination_class"] is None
    assert res["move_method_applicable"] is False
    assert res["reason"] == "TARGET_IS_MODULE_FUNCTION"
    assert res["suggested_refactoring"] == "Move Function"


def test_no_class_in_ast_source_class_null():
    """3. No class in AST -> source_class = null, Move Method not applicable."""
    resolver = MoveMethodPlanResolver([{
        "file_name": "utils.py",
        "source_code": MODULE_FUNCTION_PY,
        "language": "python",
    }])
    smell = CodeSmell(
        id="smell_001",
        type="Feature Envy",
        location={"file": "utils.py", "class": "NonExistentClass", "method": "time", "lines": [4, 5]},
        metrics={},
        severity="medium",
    )
    res = resolver.resolve(smell)
    assert res["source_class"] is None
    assert res["move_method_applicable"] is False


def test_missing_destination_class_not_generated():
    """4. Missing destination class -> Move Method not generated, no fake Helper class invented."""
    report_dict = {
        "target": "order_service.py",
        "language": "python",
        "source_files": [{
            "file_name": "order_service.py",
            "source_code": SINGLE_CLASS_NO_DEST_PY,
            "language": "python",
        }],
        "smells": [{
            "id": "smell_002",
            "type": "Feature Envy",
            "severity": "medium",
            "location": {
                "file": "order_service.py",
                "class": "OrderService",
                "method": "calculate_tax",
                "lines": [2, 4],
            },
            "details": "Feature envy in calculate_tax",
            "metrics": {},
        }],
    }
    plan = generate_plan_from_dict(report_dict)
    assert len(plan["steps"]) == 0, f"Expected 0 steps when no destination class exists, got {len(plan['steps'])}"


def test_real_source_and_destination_classes_valid_json():
    """5. Real source + destination classes -> valid Move Method JSON with all contract fields."""
    report_dict = {
        "target": "order_service.py",
        "language": "python",
        "source_files": [{
            "file_name": "order_service.py",
            "source_code": REAL_CLASSES_PY,
            "language": "python",
        }],
        "smells": [{
            "id": "smell_003",
            "type": "Feature Envy",
            "severity": "medium",
            "location": {
                "file": "order_service.py",
                "class": "OrderService",
                "method": "calculate_tax",
                "destination_class": "TaxService",
                "lines": [6, 8],
            },
            "details": "Feature envy toward TaxService",
            "metrics": {},
        }],
    }
    plan = generate_plan_from_dict(report_dict)
    assert len(plan["steps"]) == 1
    step = plan["steps"][0]
    assert step["refactoring"] == "Move Method"
    assert step["step_id"] == 1
    assert step["smell_id"] == "smell_003"
    
    # Verify parameters
    params = step["parameters"]
    assert params["source_file"] == "order_service.py"
    assert params["source_class"] == "OrderService"
    assert params["source_method"] == "calculate_tax"
    assert params["method"] == "calculate_tax"
    assert params["destination_class"] == "TaxService"
    assert params["destination_parameter"] == "target"
    assert params["source_line"] == 7

    # Verify target dict
    target = step["target"]
    assert target["file"] == "order_service.py"
    assert target["class"] == "OrderService"
    assert target["method"] == "calculate_tax"
    assert target["lines"] == [6, 8]

    # Verify planning evidence
    evidence = params["move_method_planning_evidence"]
    assert evidence["source_class_exists"] is True
    assert evidence["destination_class_exists"] is True
    assert evidence["method_belongs_to_source_class"] is True
    assert evidence["source_and_destination_differ"] is True


def test_real_class_literally_named_sourceclass():
    """6. A real class actually named SourceClass must still work."""
    report_dict = {
        "target": "custom_service.py",
        "language": "python",
        "source_files": [{
            "file_name": "custom_service.py",
            "source_code": SOURCECLASS_LITERAL_PY,
            "language": "python",
        }],
        "smells": [{
            "id": "smell_004",
            "type": "Feature Envy",
            "severity": "medium",
            "location": {
                "file": "custom_service.py",
                "class": "SourceClass",
                "method": "process_item",
                "destination_class": "TargetClass",
                "lines": [6, 8],
            },
            "details": "Feature envy toward TargetClass",
            "metrics": {},
        }],
    }
    plan = generate_plan_from_dict(report_dict)
    assert len(plan["steps"]) == 1
    step = plan["steps"][0]
    assert step["parameters"]["source_class"] == "SourceClass"
    assert step["parameters"]["destination_class"] == "TargetClass"
    assert step["parameters"]["move_method_planning_evidence"]["source_class_exists"] is True
    assert step["parameters"]["move_method_planning_evidence"]["destination_class_exists"] is True


def test_planning_evidence_matches_ast_facts():
    """7. Planning evidence must accurately match actual AST facts."""
    resolver = MoveMethodPlanResolver([{
        "file_name": "order_service.py",
        "source_code": REAL_CLASSES_PY,
        "language": "python",
    }])
    smell = CodeSmell(
        id="smell_005",
        type="Feature Envy",
        location={
            "file": "order_service.py",
            "class": "OrderService",
            "method": "calculate_tax",
            "destination_class": "TaxService",
            "lines": [6, 8],
        },
        metrics={},
        severity="medium",
    )
    res = resolver.resolve(smell)
    assert res["status"] == "success"
    assert res["source_class_exists"] is True
    assert res["destination_class_exists"] is True
    assert res["method_belongs_to_source_class"] is True
    assert res["source_and_destination_differ"] is True


def test_java_move_method_planning_preserves_valid_classes():
    """8. Existing Java valid Move Method planning remains supported."""
    report_dict = {
        "target": "OrderProcessor.java",
        "language": "java",
        "smells": [{
            "id": "smell_java_01",
            "type": "Feature Envy",
            "severity": "medium",
            "location": {
                "file": "OrderProcessor.java",
                "language": "java",
                "class": "OrderProcessor",
                "method": "calculateDiscount",
                "destination_class": "DiscountCalculator",
                "lines": [20, 35],
            },
            "details": "Method calculateDiscount envies DiscountCalculator",
            "metrics": {},
        }],
    }
    plan = generate_plan_from_dict(report_dict)
    assert len(plan["steps"]) == 1
    step = plan["steps"][0]
    assert step["refactoring"] == "Move Method"
    assert step["parameters"]["source_class"] == "OrderProcessor"
    assert step["parameters"]["destination_class"] == "DiscountCalculator"
