from sctva.agent import SafeCodeTransformationValidationAgent


def test_python_safe_pipeline_passes_without_rollback():
    agent = SafeCodeTransformationValidationAgent()
    payload = {
        "request_id": "int_001",
        "language": "python",
        "source_code": "def f(x):\n    return x * 10\n",
        "refactoring_plan": {
            "plan_id": "plan_int_001",
            "actions": [
                {
                    "action_type": "extract_constant",
                    "parameters": {"literal_value": 10, "constant_name": "FACTOR"},
                }
            ],
            "behavior_tests": [
                {"name": "mul", "call": "f", "args": [2], "expected": 20}
            ],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
        },
    }

    result = agent.execute(payload)
    assert result["rollback_occurred"] is False
    assert result["success"] is True


def test_python_extract_method_action_runs_through_contract():
    agent = SafeCodeTransformationValidationAgent()
    payload = {
        "request_id": "int_extract_001",
        "language": "python",
        "source_code": "def f(x):\n    total = x + 1\n    return total\n",
        "refactoring_plan": {
            "plan_id": "plan_extract_001",
            "actions": [
                {
                    "action_type": "extract_method",
                    "parameters": {
                        "method": "f",
                        "new_method_name": "f_core",
                        "start_line": 3,
                        "end_line": 3,
                    },
                }
            ],
            "behavior_tests": [{"name": "value", "call": "f", "args": [2], "expected": 3}],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
        },
    }

    result = agent.execute(payload)
    assert result["rollback_occurred"] is False
    assert result["success"] is True
    assert "def f_core()" in result["refactored_code"]


def test_zero_replacement_is_not_reported_as_success():
    agent = SafeCodeTransformationValidationAgent()
    payload = {
        "request_id": "int_no_change_001",
        "language": "python",
        "source_code": "def f(x):\n    return x + 1\n",
        "refactoring_plan": {
            "plan_id": "plan_no_change_001",
            "actions": [
                {
                    "action_type": "rename_symbol",
                    "parameters": {"old_name": "missing_name", "new_name": "renamed_name"},
                }
            ],
            "behavior_tests": [{"name": "value", "call": "f", "args": [2], "expected": 3}],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
        },
    }

    result = agent.execute(payload)

    assert result["success"] is False
    assert result["rollback_occurred"] is False
    assert result["transformation_applied"] is False
    assert result["total_replacements"] == 0
    assert result["confidence_score"] is None
    assert result["confidence_applicable"] is False
    assert result["validation_score"] == 1.0
    assert result["safety_report"]["summary"] == (
        "Transformation not applied; source code remained unchanged."
    )


def test_sctva_internal_refactoring_runs_for_raw_source_files():
    agent = SafeCodeTransformationValidationAgent()
    source = (
        "import java.sql.PreparedStatement;\n"
        "class OrderDao {\n"
        "    void addOrder(java.sql.Connection connection) throws Exception {\n"
        "        PreparedStatement pst = connection.prepareStatement(\"insert into `order` \" +\n"
        "                \"values (?)\");\n"
        "        pst.executeUpdate();\n"
        "    }\n"
        "}\n"
    )
    payload = {
        "request_id": "int_sctva_internal_001",
        "language": "java",
        "source_files": [
            {
                "file_name": "src/main/java/dao/OrderDao.java",
                "language": "java",
                "source_code": source,
                "source_mode": "raw",
            }
        ],
        "refactoring_plan": {
            "plan_id": "plan_sctva_internal_001",
            "actions": [],
            "behavior_tests": [],
            "metadata": {"source_agent": "rdp_agent"},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": True,
        },
    }

    result = agent.execute(payload)

    assert result["rollback_occurred"] is False
    assert result["transformation_applied"] is True
    assert result["success"] is True
    assert "private static final String" in result["refactored_code"]
    assert "prepareStatement(SCTVA_SQL_ORDERDAO_JAVA_4)" in result["refactored_code"]


def test_unproven_dead_code_line_is_skipped_without_rollback():
    agent = SafeCodeTransformationValidationAgent()
    payload = {
        "request_id": "int_dead_code_guard_001",
        "language": "python",
        "source_code": "def test_value():\n    assert 1 == 1\n",
        "refactoring_plan": {
            "plan_id": "plan_dead_code_guard_001",
            "actions": [
                {
                    "action_type": "remove_dead_code",
                    "parameters": {"source_line": 2},
                }
            ],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
        },
    }

    result = agent.execute(payload)

    assert result["rollback_occurred"] is False
    assert result["transformation_applied"] is False
    assert result["confidence_score"] is None
    assert result["validation_score"] == 1.0
    assert all(step["passed"] for step in result["validation"].values())
    warnings = result["safety_report"]["transformation_log"][0]["warnings"]
    assert any("could not prove" in warning for warning in warnings)


def test_reconstructed_cuqa_source_reports_placeholder_warning():
    agent = SafeCodeTransformationValidationAgent()
    payload = {
        "request_id": "int_reconstructed_source_001",
        "language": "c",
        "source_files": [
            {
                "file_name": "examples/ini_dump.c",
                "language": "c",
                "source_code": "/* Reconstructed from CUQA AST for examples/ini_dump.c */\nint dumper(void) {\n    return 0;\n}\n",
                "source_mode": "ast_reconstructed",
                "origin": "cuqa",
            }
        ],
        "refactoring_plan": {
            "plan_id": "plan_reconstructed_source_001",
            "actions": [
                {
                    "action_type": "introduce_constant",
                    "parameters": {
                        "literal_value": 0,
                        "constant_name": "MAGIC_NUMBER_0",
                        "source_file": "examples/ini_dump.c",
                        "source_line": 3,
                    },
                }
            ],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
        },
    }

    result = agent.execute(payload)

    assert result["rollback_occurred"] is False
    assert result["transformation_applied"] is False
    assert result["confidence_score"] is None
    assert result["validation_score"] == 1.0
    assert result["source_mode"] == "ast_reconstructed"
    assert "MAGIC_NUMBER_0" not in result["refactored_code"]
    messages = result["safety_report"]["human_messages"]
    assert any("reconstructed placeholder source" in message for message in messages)


def test_python_behavior_change_rolls_back():
    agent = SafeCodeTransformationValidationAgent()
    payload = {
        "request_id": "int_002",
        "language": "python",
        "source_code": "def f():\n    return 1\n",
        "refactoring_plan": {
            "plan_id": "plan_int_002",
            "actions": [
                {"action_type": "replace_literal", "parameters": {"old_literal": 1, "new_literal": 2}}
            ],
            "behavior_tests": [{"name": "value", "call": "f", "expected": 1}],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
        },
    }

    result = agent.execute(payload)
    assert result["rollback_occurred"] is True
    assert result["success"] is False


def test_language_guard_rejects_non_supported():
    agent = SafeCodeTransformationValidationAgent()
    payload = {
        "request_id": "int_003",
        "language": "javascript",
        "source_code": "function x(){return 1;}",
        "refactoring_plan": {"plan_id": "p", "actions": [], "behavior_tests": [], "metadata": {}},
    }

    try:
        agent.execute(payload)
    except Exception as exc:
        assert "Unsupported language" in str(exc)
    else:
        assert False, "Expected unsupported language error"


def test_c_safe_pipeline_passes_without_rollback():
    agent = SafeCodeTransformationValidationAgent()
    payload = {
        "request_id": "int_c_001",
        "language": "c",
        "source_code": "#include <stdio.h>\ndouble ratio(void) { return 0.12; }\n",
        "refactoring_plan": {
            "plan_id": "plan_c_001",
            "actions": [
                {
                    "action_type": "introduce_constant",
                    "parameters": {"literal_value": 0.12, "constant_name": "EXTRACTED_CONSTANT"},
                }
            ],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
        },
    }

    result = agent.execute(payload)
    assert result["rollback_occurred"] is False
    assert result["success"] is True


def test_c_syntax_error_rolls_back():
    agent = SafeCodeTransformationValidationAgent()
    payload = {
        "request_id": "int_c_002",
        "language": "c",
        "source_code": "int value(void) { return 1; }\n",
        "refactoring_plan": {
            "plan_id": "plan_c_002",
            "actions": [{"action_type": "inject_syntax_error", "parameters": {}}],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
        },
    }

    result = agent.execute(payload)
    assert result["rollback_occurred"] is True
    assert result["success"] is False
