from sctva.integration.planner_adapter import PlannerAdapter


def test_planner_adapter_maps_extract_method_to_rename_symbol():
    adapter = PlannerAdapter()
    planner_output = {
        "plan_id": "p1",
        "steps": [
            {
                "step_id": 1,
                "refactoring": "Extract Method",
                "target": {"method": "calculate"},
                "parameters": {"new_method_name": "calculate_core"},
            }
        ],
    }

    normalized = adapter.normalize_plan(planner_output, correlation_id="corr1")
    assert normalized["actions"][0]["action_type"] == "rename_symbol"


def test_planner_adapter_unsupported_step_becomes_noop():
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
    assert normalized["actions"][0]["action_type"] == "rename_symbol"
    assert normalized["actions"][0]["parameters"]["old_name"] == "a"
    assert normalized["actions"][0]["parameters"]["new_name"].startswith("aIn")


def test_planner_adapter_maps_extract_class_to_rename_symbol():
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
    assert normalized["actions"][0]["action_type"] == "rename_symbol"
    assert normalized["actions"][0]["parameters"]["old_name"] == "OrderProcessor"
    assert normalized["actions"][0]["parameters"]["new_name"] == "OrderProcessorHelper"


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
