import os
import pytest
from agents.cuqa_agent.src.report_generator import _analyze_c_smells
from agents.rdp_agent.src.pipeline import RDPAgent
from sctva.agent import SafeCodeTransformationValidationAgent


def test_cuqa_too_many_parameters_calculate_fine():
    source = """
#include <stdio.h>

int calculate_fine(int days, int driver_id, int speed, int limit, int vehicle_type, int court_code) {
    return days * 10 + (speed - limit) * 5;
}

int main() {
    printf("%d\n", calculate_fine(5, 101, 80, 60, 1, 404));
    return 0;
}
"""
    smells = _analyze_c_smells(source, "calculate_fine.c")
    too_many_params = [s for s in smells if s["type"] == "TooManyParameters"]
    assert len(too_many_params) == 1
    assert too_many_params[0]["entity"] == "calculate_fine"


def test_cuqa_unused_static_function_dead_code():
    source = """
#include <stdio.h>

static int unused_helper(int x) {
    return x * 42;
}

int main() {
    printf("Hello World\n");
    return 0;
}
"""
    smells = _analyze_c_smells(source, "static_test.c")
    dead_code = [s for s in smells if s["type"] == "DeadCode"]
    assert len(dead_code) == 1
    assert dead_code[0]["entity"] == "unused_helper"


def test_rdp_duplicate_code_one_shared_helper():
    report = {
        "target": "duplicate_test.c",
        "smells": [
            {
                "id": "smell_dup1",
                "type": "DuplicateCode",
                "severity": "medium",
                "location": {
                    "file": "duplicate_test.c",
                    "method": "process_a",
                    "duplicate_group": ["process_a", "process_b"],
                    "lines": [10, 20],
                },
                "metrics": {"lines_of_code": 25},
            },
            {
                "id": "smell_dup2",
                "type": "DuplicateCode",
                "severity": "medium",
                "location": {
                    "file": "duplicate_test.c",
                    "method": "process_b",
                    "duplicate_group": ["process_a", "process_b"],
                    "lines": [30, 40],
                },
                "metrics": {"lines_of_code": 25},
            },
        ],
        "metrics_summary": {},
    }
    rdp = RDPAgent()
    plan = rdp.generate_plan(report).to_dict()
    extract_steps = [s for s in plan["steps"] if s["refactoring"] == "Extract Method"]
    assert len(extract_steps) == 1
    assert extract_steps[0]["parameters"].get("is_shared_helper") is True
    assert extract_steps[0]["smell_id"] == "smell_dup1"
    assert extract_steps[0]["target"]["smell_type"] == "DuplicateCode"


def test_sctva_no_unapproved_actions_when_plan_supplied():
    source = """
#include <stdio.h>
#include <string.h>

void process(char *dst, char *src) {
    strcpy(dst, src);
}
"""
    req = {
        "request_id": "test_unapproved_actions",
        "language": "c",
        "source_files": [{
            "file_name": "example.c",
            "source_code": source,
            "language": "c",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "plan_1",
            "actions": [
                {
                    "action_type": "replace_unsafe_function",
                    "parameters": {
                        "unsafe_function": "strcpy",
                        "safe_alternative": "strncpy",
                        "source_line": 6,
                    },
                }
            ],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": False,
            "timeout_seconds": 5,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": True,
        },
    }
    res = SafeCodeTransformationValidationAgent().execute(req)
    assert res["success"] is False
    assert res.get("status") in ("REVIEW_REQUIRED", "FAILED", "PARTIAL_SUCCESS")
    file_res = res.get("file_results", [{}])[0]
    log = file_res.get("safety_report", {}).get("transformation_log", []) if isinstance(file_res, dict) else []
    assert len(log) <= 1


def test_sctva_workspace_files_preserves_all_files():
    source1 = "int main() { return 0; }\n"
    readme = "# Test Project\n"
    makefile = "all:\n\tgcc main.c\n"

    req = {
        "request_id": "test_workspace_preservation",
        "language": "c",
        "source_files": [
            {"file_name": "main.c", "source_code": source1, "language": "c", "source_mode": "raw"},
            {"file_name": "README.md", "source_code": readme, "language": "markdown", "source_mode": "raw"},
            {"file_name": "Makefile", "source_code": makefile, "language": "makefile", "source_mode": "raw"},
        ],
        "refactoring_plan": {
            "plan_id": "plan_empty",
            "actions": [],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": False,
            "timeout_seconds": 5,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
        },
    }
    res = SafeCodeTransformationValidationAgent().execute(req)
    ws_files = res.get("transformed_workspace_files", [])
    names = [f["file_name"] for f in ws_files]
    assert "main.c" in names
    assert "README.md" in names
    assert "Makefile" in names
