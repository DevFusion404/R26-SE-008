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
        "source_code": (
            "def f(x):\n"
            "    subtotal = x + 1\n"
            "    doubled = subtotal * 2\n"
            "    adjusted = doubled + 3\n"
            "    total = adjusted - 1\n"
            "    marker = total\n"
            "    return marker\n"
        ),
        "refactoring_plan": {
            "plan_id": "plan_extract_001",
            "actions": [
                {
                    "action_type": "extract_method",
                    "parameters": {
                        "method": "f",
                        "new_method_name": "f_core",
                    },
                }
            ],
            "behavior_tests": [{"name": "value", "call": "f", "args": [2], "expected": 8}],
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
    assert "\ndef f_core(" in result["refactored_code"]
    assert "    def f_core(" not in result["refactored_code"]


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


def test_java_rename_method_runs_through_pipeline():
    agent = SafeCodeTransformationValidationAgent()
    source = (
        "public class PaymentService {\n"
        "    public int processPayment(int amount) { return amount + 1; }\n"
        "    public int run() { return processPayment(2) + this.processPayment(3); }\n"
        "}\n"
    )
    payload = {
        "request_id": "java_rename_method_001",
        "language": "java",
        "source_code": source,
        "refactoring_plan": {
            "plan_id": "plan_java_rename_method_001",
            "actions": [
                {
                    "action_type": "rename_method",
                    "parameters": {
                        "old_name": "processPayment",
                        "new_name": "calculatePayment",
                        "source_class": "PaymentService",
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

    assert result["success"] is True
    assert result["rollback_occurred"] is False
    assert "int calculatePayment(int amount)" in result["refactored_code"]
    assert "return calculatePayment(2) + this.calculatePayment(3);" in result["refactored_code"]
    log = result["safety_report"]["transformation_log"][0]
    assert log["action_type"] == "rename_method"
    assert log["metadata"]["status"] == "success"
    assert result["validation"]["structural"]["details"]["rename_method_validation"][0]["passed"] is True


def test_python_rename_method_runs_through_pipeline():
    agent = SafeCodeTransformationValidationAgent()
    source = (
        "def calc(x):\n"
        "    return x + 1\n\n"
        "def run(value):\n"
        "    return calc(value)\n"
    )
    payload = {
        "request_id": "python_rename_method_001",
        "language": "python",
        "source_code": source,
        "refactoring_plan": {
            "plan_id": "plan_python_rename_method_001",
            "actions": [
                {
                    "action_type": "rename_method",
                    "parameters": {
                        "old_name": "calc",
                        "new_name": "calculate",
                    },
                }
            ],
            "behavior_tests": [{"name": "value", "call": "run", "args": [2], "expected": 3}],
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

    assert result["success"] is True
    assert result["rollback_occurred"] is False
    assert "def calculate(x):" in result["refactored_code"]
    assert "return calculate(value)" in result["refactored_code"]
    log = result["safety_report"]["transformation_log"][0]
    assert log["action_type"] == "rename_method"
    assert log["metadata"]["status"] == "success"
    assert result["validation"]["structural"]["details"]["rename_method_validation"][0]["passed"] is True


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


def test_extract_class_final_audit_passes_only_after_behavior_validation():
    source = '''class LibraryManager:
    def __init__(self):
        self.notices = []
        self.enabled = True
    def add_notice(self, text): self.notices.append(text)
    def latest_notice(self): return self.notices[-1] if self.notices else None
    def enabled_state(self): return self.enabled
    def disable(self): self.enabled = False
    def utility_a(self): return self.enabled
    def utility_b(self): return self.enabled
'''
    agent = SafeCodeTransformationValidationAgent()
    result = agent.execute({
        "request_id": "extract_class_audit",
        "language": "python",
        "source_files": [{
            "file_name": "manager.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "extract_class_plan",
            "actions": [{
                "action_type": "extract_class",
                "parameters": {
                    "source_file": "manager.py",
                    "source_class": "LibraryManager",
                    "new_class_name": "NoticeBoard",
                    "methods_to_extract": ["add_notice", "latest_notice"],
                    "fields_to_extract": ["notices"],
                },
            }],
            "behavior_tests": [{
                "name": "notice_behavior",
                "expression": "(lambda m: (m.add_notice('x'), m.latest_notice())[1])(LibraryManager())",
            }],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
        },
    })

    log = result["safety_report"]["transformation_log"][0]
    assert result["success"] is True
    assert result["rollback_occurred"] is False
    assert log["metadata"]["final_decision"] == "PASS"
    assert log["metadata"]["final_checks"]["plan_compliance"] == "PASS"
    assert log["metadata"]["final_checks"]["structural_refactoring"] == "PASS"
    assert log["metadata"]["final_checks"]["behavior_preservation"] == "PASS"
    assert log["metadata"]["final_checks"]["large_class_reduction"] == "PASS"


def test_extract_class_large_library_preserves_public_state_and_reduces_raw_size():
    utilities = "\n".join(
        f"    def utility_{index}(self): return self.books.get({index})"
        for index in range(1, 18)
    )
    source = f'''class LibraryManager:
    def __init__(self):
        self.books = {{}}
        self.notices = []
        self.enabled = True
    def add_notice(self, text): self.notices.append(text)
    def latest_notice(self): return self.notices[-1] if self.notices else None
    def enabled_state(self): return self.enabled
    def disable(self): self.enabled = False
{utilities}
'''
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "extract_class_large_library",
        "language": "python",
        "source_files": [{
            "file_name": "library.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "extract_class_large_library_plan",
            "actions": [{
                "action_type": "extract_class",
                "parameters": {
                    "source_file": "library.py",
                    "source_class": "LibraryManager",
                    "new_class_name": "LibraryManagerHelper",
                    "methods_to_extract": ["add_notice", "latest_notice"],
                    "fields_to_extract": ["notices"],
                    "required_public_methods": ["add_notice", "latest_notice"],
                    "required_public_fields": ["notices"],
                },
            }],
            "behavior_tests": [{
                "name": "notice_behavior",
                "expression": "(lambda m: (m.notices.append('x'), m.latest_notice())[1])(LibraryManager())",
            }],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
        },
    })

    metadata = result["safety_report"]["transformation_log"][0]["metadata"]
    namespace: dict[str, object] = {}
    exec(result["refactored_code"], namespace)
    manager = namespace["LibraryManager"]()
    manager.notices.append("state-compatible")

    assert result["success"] is True
    assert result["rollback_occurred"] is False
    assert manager.notices == ["state-compatible"]
    assert manager.latest_notice() == "state-compatible"
    assert metadata["after_metrics"]["method_count"] < metadata["before_metrics"]["method_count"]
    assert metadata["after_metrics"]["loc"] < metadata["before_metrics"]["loc"]
    assert metadata["large_class_after"]["detected"] is False
    assert metadata["final_decision"] == "PASS"
    assert metadata["final_checks"]["plan_compliance"] == "PASS"
    assert metadata["final_checks"]["structural_refactoring"] == "PASS"
    assert metadata["final_checks"]["behavior_preservation"] == "PASS"
    assert metadata["final_checks"]["full_api_preservation"] == "PASS"
    assert metadata["final_checks"]["state_compatibility"] == "PASS"
    assert metadata["final_checks"]["single_state_owner"] == "PASS"
    assert metadata["final_checks"]["large_class_reduction"] == "PASS"


def test_extract_class_behavior_failure_rolls_back_original_source():
    source = '''class LibraryManager:
    def __init__(self):
        self.notices = []
        self.enabled = True
    def add_notice(self, text): self.notices.append(text)
    def latest_notice(self): return self.notices[-1] if self.notices else None
    def enabled_state(self): return self.enabled
    def disable(self): self.enabled = False
    def utility_a(self): return self.enabled
    def utility_b(self): return self.enabled
'''
    agent = SafeCodeTransformationValidationAgent()
    result = agent.execute({
        "request_id": "extract_class_rollback",
        "language": "python",
        "source_files": [{
            "file_name": "manager.py",
            "source_code": source,
            "language": "python",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "extract_class_rollback_plan",
            "actions": [{
                "action_type": "extract_class",
                "parameters": {
                    "source_file": "manager.py",
                    "source_class": "LibraryManager",
                    "new_class_name": "NoticeBoard",
                    "methods_to_extract": ["add_notice", "latest_notice"],
                    "fields_to_extract": ["notices"],
                    "preserve_public_api": False,
                },
            }],
            "behavior_tests": [{
                "name": "notice_behavior",
                "expression": "(lambda m: (m.add_notice('x'), m.latest_notice())[1])(LibraryManager())",
            }],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
        },
    })

    log = result["safety_report"]["transformation_log"][0]
    assert result["success"] is False
    assert result["rollback_occurred"] is True
    assert result["refactored_code"] == source
    assert log["metadata"]["final_decision"] == "ROLLBACK"
    assert log["metadata"]["final_checks"]["behavior_preservation"] == "FAIL"


def test_extract_class_source_file_is_resolved_from_exact_class_identity():
    target_source = '''class TargetManager:
    def __init__(self):
        self.items = []
        self.enabled = True
    def add(self, value): self.items.append(value)
    def count(self): return len(self.items)
    def enabled_state(self): return self.enabled
    def disable(self): self.enabled = False
'''
    unrelated_source = "class OtherManager:\n    pass\n"
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "extract_class_scope_resolution",
        "language": "python",
        "source_files": [
            {"file_name": "unrelated.py", "source_code": unrelated_source, "language": "python"},
            {"file_name": "target.py", "source_code": target_source, "language": "python"},
        ],
        "refactoring_plan": {
            "plan_id": "extract_class_scope_plan",
            "actions": [{
                "action_type": "extract_class",
                "parameters": {
                    "source_class": "TargetManager",
                    "new_class_name": "ItemStore",
                    "methods_to_extract": ["add", "count"],
                    "fields_to_extract": ["items"],
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
        },
    })

    assert result["file_name"] == "target.py"
    assert result["success"] is True
    metadata = result["safety_report"]["transformation_log"][0]["metadata"]
    assert metadata["source_file"] == "target.py"
    assert "class ItemStore:" in result["refactored_code"]


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
