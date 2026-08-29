from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.contracts import RefactoringAction, SCTVARequestContract
from sctva.integration.planner_adapter import PlannerAdapter
from sctva.transformers import python_transformers
from sctva.transformers.engine import TransformationEngine
from sctva.validators.structural_validator import StructuralValidator


UMS_MODEL = '''from mysql.connector import Error
from Model.database import connection

class Model:
    def __init__(self):
        self.error = Error()
        self.conn = connection()

    def admin_validate(self, em, pw):
        cur = self.conn.cursor()
        try:
            sql = "SELECT admin_id, email, password FROM ums_admin WHERE email = %s AND password = %s"
            cur.execute(sql, (em, pw,))
            row = cur.fetchone()
            aid = row[0]
            email = row[1]
            password = row[2]
            return aid, email, password
        except:
            return "Incorrect email and password"

    def user_validate(self, em, pw):
        cur = self.conn.cursor()
        try:
            sql = "SELECT user_id, email, password FROM ums_user WHERE email = %s AND password = %s"
            cur.execute(sql, (em, pw,))
            row = cur.fetchone()
            uid = row[0]
            email = row[1]
            password = row[2]
            return uid, email, password
        except:
            return "Sorry!", "Your Email and password is Wrong."
'''


def test_ums_cursor_created_before_try_proves_mysql_error():
    for method in ("admin_validate", "user_validate"):
        resolution = python_transformers.resolve_bare_exception_handler(
            UMS_MODEL,
            source_class="Model",
            source_method=method,
        )
        assert resolution["status"] == "success"
        assert resolution["replacement_exception"] == "(Error, IndexError, TypeError)"
        assert resolution["exception_resolution_strategy"] == "import_and_try_body_context"
        assert resolution["qualified_source_method"] == f"Model.{method}"


def test_target_only_resolution_does_not_require_exception_type_proof():
    source = '''class Service:
    def load(self):
        try:
            dependency()
        except:
            return None
'''
    target = python_transformers.resolve_bare_exception_handler(
        source,
        source_class="Service",
        source_method="load",
        require_specific_exception=False,
    )
    assert target["status"] == "success"
    assert target["qualified_source_method"] == "Service.load"

    full = python_transformers.resolve_bare_exception_handler(
        source,
        source_class="Service",
        source_method="load",
    )
    assert full["status"] == "review_required"
    assert full["reason"] == "SPECIFIC_EXCEPTION_TYPE_NOT_PROVEN"


def test_planner_adapter_preserves_bare_except_method_and_class():
    normalized = PlannerAdapter().normalize_plan({
        "plan_id": "ums-bare-except",
        "steps": [{
            "step_id": 1,
            "refactoring": "Replace Bare Except with Specific Exception",
            "target": {
                "file": "UMS/Model/model.py",
                "class": "Model",
                "method": "admin_validate",
                "lines": [20],
            },
            "parameters": {},
        }],
    })
    action = normalized["actions"][0]
    assert action["action_type"] == "narrow_exception_handler"
    assert action["parameters"]["source_class"] == "Model"
    assert action["parameters"]["class_name"] == "Model"
    assert action["parameters"]["source_method"] == "admin_validate"
    assert action["parameters"]["method"] == "admin_validate"
    assert action["parameters"]["source_file"] == "UMS/Model/model.py"


def test_two_ums_bare_except_actions_apply_and_structurally_validate():
    actions = [
        RefactoringAction(
            action_type="replace_bare_except",
            parameters={
                "source_file": "UMS/Model/model.py",
                "source_class": "Model",
                "method": "admin_validate",
            },
        ),
        RefactoringAction(
            action_type="replace_bare_except",
            parameters={
                "source_file": "UMS/Model/model.py",
                "source_class": "Model",
                "method": "user_validate",
            },
        ),
    ]
    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="python",
        source_code=UMS_MODEL,
        actions=actions,
        strict_mode=True,
        current_file_name="UMS/Model/model.py",
    )

    assert transformed.count("except (Error, IndexError, TypeError):") == 2
    assert "except:" not in transformed
    assert warnings == []
    assert [entry.metadata["final_decision"] for entry in logs] == ["PASS", "PASS"]

    structural = StructuralValidator().validate(
        language="python",
        original_code=UMS_MODEL,
        transformed_code=transformed,
        actions=actions,
    )
    assert structural.passed is True
    assert len(structural.details["exception_handler_validation"]) == 2
    assert all(item["passed"] for item in structural.details["exception_handler_validation"])


def test_legacy_targetless_bare_actions_are_recovered_from_local_handlers_without_duplicates():
    request = SCTVARequestContract.from_dict({
        "request_id": "legacy-bare-recovery",
        "language": "python",
        "source_files": [{
            "file_name": "UMS/Model/model.py",
            "source_code": UMS_MODEL,
            "language": "python",
        }],
        "refactoring_plan": {
            "plan_id": "legacy-bare-recovery",
            "actions": [
                {
                    "action_type": "noop",
                    "source_refactoring": "Replace Bare Except with Specific Exception",
                    "parameters": {"source_file": "UMS/Model/model.py"},
                    "warnings": [
                        "Unsupported refactoring 'Replace Bare Except with Specific Exception' mapped to noop."
                    ],
                },
                {
                    "action_type": "noop",
                    "source_refactoring": "Replace Bare Except with Specific Exception",
                    "parameters": {"source_file": "UMS/Model/model.py"},
                    "warnings": [
                        "Unsupported refactoring 'Replace Bare Except with Specific Exception' mapped to noop."
                    ],
                },
            ],
        },
        "execution_options": {"enable_sctva_auto_refactoring": True},
    })

    agent = SafeCodeTransformationValidationAgent()
    plan_actions = list(request.refactoring_plan.actions)
    agent._mark_unresolved_legacy_actions(plan_actions)
    local_actions = agent._local_actions_for_file(
        request=request,
        file_entry=request.source_files[0],
        existing_actions=plan_actions,
    )
    remaining = agent._apply_local_target_recovery(
        plan_actions=plan_actions,
        local_actions=local_actions,
    )

    assert remaining == []
    assert [action.parameters["source_method"] for action in plan_actions] == [
        "admin_validate",
        "user_validate",
    ]
    assert all("mapped to noop" not in " ".join(action.warnings).lower() for action in plan_actions)
