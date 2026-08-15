from sctva.integration.planner_adapter import PlannerAdapter


def test_planner_adapter_maps_extract_method_to_extract_action_when_range_exists():
    adapter = PlannerAdapter()
    planner_output = {
        "plan_id": "p1",
        "steps": [
            {
                "step_id": 1,
                "refactoring": "Extract Method",
                "target": {"method": "calculate"},
                "parameters": {
                    "new_method_name": "calculate_core",
                    "start_line": 4,
                    "end_line": 5,
                },
            }
        ],
    }

    normalized = adapter.normalize_plan(planner_output, correlation_id="corr1")
    assert normalized["actions"][0]["action_type"] == "extract_method"
    assert normalized["actions"][0]["parameters"]["new_method_name"] == "calculate_core"
    assert normalized["actions"][0]["parameters"]["start_line"] == 4
    assert normalized["actions"][0]["parameters"]["end_line"] == 5


def test_planner_adapter_does_not_simulate_move_method_with_rename():
    adapter = PlannerAdapter()
    planner_output = {
        "plan_id": "p2",
        "steps": [
            {
                "step_id": 1,
                "refactoring": "Move Method",
                "target": {"method": "a"},
                "parameters": {},
            }
        ],
    }

    normalized = adapter.normalize_plan(planner_output)
    assert normalized["actions"][0]["action_type"] == "noop"
    assert normalized["actions"][0]["parameters"]["reason"] == "malformed_step"


def test_planner_adapter_does_not_simulate_extract_class_with_rename():
    adapter = PlannerAdapter()
    planner_output = {
        "plan_id": "p3",
        "steps": [
            {
                "step_id": 1,
                "refactoring": "Extract Class",
                "target": {"class": "OrderProcessor"},
                "parameters": {"new_class_name": "OrderProcessorHelper"},
            }
        ],
    }

    normalized = adapter.normalize_plan(planner_output)
    assert normalized["actions"][0]["action_type"] == "noop"
    assert normalized["actions"][0]["parameters"]["reason"] == "malformed_step"


def test_planner_adapter_maps_replace_unsafe_function():
    adapter = PlannerAdapter()
    planner_output = {
        "plan_id": "p_unsafe",
        "steps": [
            {
                "step_id": 1,
                "refactoring": "Replace Unsafe Function",
                "target": {"file": "smelly.c", "method": "gets", "lines": [55]},
                "parameters": {
                    "unsafe_function": "gets",
                    "safe_alternative": "fgets",
                    "source_file": "smelly.c",
                    "source_line": 55,
                },
            }
        ],
    }

    normalized = adapter.normalize_plan(planner_output)
    action = normalized["actions"][0]
    assert action["action_type"] == "replace_unsafe_function"
    assert action["parameters"]["unsafe_function"] == "gets"
    assert action["parameters"]["safe_alternative"] == "fgets"


def test_planner_adapter_maps_line_only_dead_code():
    adapter = PlannerAdapter()
    planner_output = {
        "plan_id": "p_dead",
        "steps": [
            {
                "step_id": 1,
                "refactoring": "Remove Dead Code",
                "target": {"file": "demo.py", "lines": [12]},
                "parameters": {"source_file": "demo.py"},
            }
        ],
    }

    normalized = adapter.normalize_plan(planner_output)
    action = normalized["actions"][0]
    assert action["action_type"] == "remove_dead_code"
    assert action["parameters"]["source_line"] == 12


def test_planner_adapter_maps_encapsulate_variable():
    adapter = PlannerAdapter()
    planner_output = {
        "plan_id": "p_global",
        "steps": [
            {
                "step_id": 1,
                "refactoring": "Encapsulate Variable",
                "target": {"file": "smelly.c", "lines": [7]},
                "parameters": {
                    "variable_name": "counter",
                    "getter_name": "get_counter",
                    "setter_name": "set_counter",
                    "source_file": "smelly.c",
                },
            }
        ],
    }

    normalized = adapter.normalize_plan(planner_output)
    action = normalized["actions"][0]
    assert action["action_type"] == "encapsulate_variable"
    assert action["parameters"]["variable_name"] == "counter"


def test_planner_adapter_maps_fault_injection_to_fault_injection_action():
    adapter = PlannerAdapter()
    planner_output = {
        "plan_id": "p4",
        "steps": [
            {
                "step_id": 2,
                "refactoring": "Fault Injection - Change Return Value",
                "target": {"class": "OrderProcessor", "method": "calculateTotal"},
                "parameters": {
                    "change_type": "wrong_behavior_test",
                    "original_logic": "return total;",
                    "faulty_logic": "return total + 1;",
                    "purpose": "This step is intentionally added only to verify that behavioral fingerprinting detects behavior mismatch after transformation.",
                },
            }
        ],
    }

    normalized = adapter.normalize_plan(planner_output)
    assert normalized["actions"][0]["action_type"] == "fault_injection"
    assert normalized["actions"][0]["parameters"]["original_logic"] == "return total;"
    assert normalized["actions"][0]["parameters"]["faulty_logic"] == "return total + 1;"
