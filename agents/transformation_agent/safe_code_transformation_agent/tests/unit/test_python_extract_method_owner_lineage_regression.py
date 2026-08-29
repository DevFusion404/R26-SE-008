from sctva.contracts import RefactoringAction
from sctva.transformers.python_extract_method import apply_extract_method
from sctva.validators.structural_validator import StructuralValidator


MODEL_SOURCE = '''class Model:
    def __init__(self):
        self.conn = None

    def user_data_show(self, uid):
        cur = self.conn.cursor()
        sql = "SELECT * FROM ums_user WHERE user_id = %s"
        cur.execute(sql, (uid,))
        data = cur.fetchone()
        return data

    def update_user(self, edit_data, uid):
        cur = self.conn.cursor()
        sql = "UPDATE ums_user SET user_name = %s WHERE user_id = %s"
        data = (edit_data[0], uid)
        cur.execute(sql, data)
        self.conn.commit()
        return "Updated Successfully."
'''


def _action(method: str, helper: str, metadata: dict) -> RefactoringAction:
    return RefactoringAction(
        action_type="extract_method",
        parameters={
            "method": method,
            "new_method_name": helper,
            # Reproduce the RDP case where the class owner was omitted.
            "source_class": "",
            "applied_transformation_metadata": metadata,
        },
    )


def test_extract_method_recovers_real_owner_and_persists_lineage_metadata():
    transformed, count, metadata = apply_extract_method(
        MODEL_SOURCE,
        method_name="user_data_show",
        new_method_name="extracted_user_data_show",
        source_class="",
        start_line=6,
        end_line=9,
        smell="Duplicate Code",
    )

    assert count == 1
    assert metadata["status"] == "success"
    assert metadata["source_class"] == "Model"
    assert metadata["source_class_resolution"] == "recovered_from_current_ast"
    assert metadata["qualified_source_method"] == "Model.user_data_show"
    assert metadata["qualified_extracted_method"] == "Model.extracted_user_data_show"
    assert metadata["smell"] == "Duplicate Code"
    assert metadata["effective_action_parameters"]["source_class"] == "Model"
    assert "def extracted_user_data_show" in transformed


def test_structural_validator_uses_applied_owner_lineage_for_two_sequential_extractions():
    first, first_count, first_metadata = apply_extract_method(
        MODEL_SOURCE,
        method_name="user_data_show",
        new_method_name="extracted_user_data_show",
        source_class="",
        start_line=6,
        end_line=9,
        smell="Duplicate Code",
    )
    final_code, second_count, second_metadata = apply_extract_method(
        first,
        method_name="update_user",
        new_method_name="extracted_update_user",
        source_class="",
        smell="Duplicate Code",
    )

    result = StructuralValidator().validate(
        language="python",
        original_code=MODEL_SOURCE,
        transformed_code=final_code,
        actions=[
            _action(
                "user_data_show",
                "extracted_user_data_show",
                first_metadata,
            ),
            _action(
                "update_user",
                "extracted_update_user",
                second_metadata,
            ),
        ],
    )

    assert first_count == 1
    assert second_count == 1
    assert result.passed is True
    checks = result.details["extract_method_validation"]
    assert len(checks) == 2
    assert all(item["passed"] for item in checks)
    assert all(
        item["checks"]["source_and_helper_owner_scope_preserved"]
        for item in checks
    )
    assert checks[0]["source_class"] == "Model"
    assert checks[1]["source_class"] == "Model"


def test_small_simple_method_is_not_applicable_instead_of_review_required():
    source = '''class View:
    def admin_login(self):
        admin_name = input("Enter Your Email: ")
        admin_password = input("Enter the Password: ")
        return admin_name, admin_password
'''

    transformed, count, metadata = apply_extract_method(
        source,
        method_name="admin_login",
        new_method_name="extracted_admin_login",
        source_class="",
        smell="Duplicate Code",
    )

    assert transformed == source
    assert count == 0
    assert metadata["status"] == "not_applicable"
    assert metadata["final_decision"] == "NOT_APPLICABLE"
    assert metadata["reason"] == "METHOD_TOO_SMALL_FOR_USEFUL_EXTRACTION"
    assert metadata["source_class"] == "View"
    assert metadata["smell"] == "Duplicate Code"


def test_small_control_flow_method_remains_review_required_for_safety():
    source = '''def process(value):
    current = value + 1
    if current < 0:
        return 0
    return current
'''

    transformed, count, metadata = apply_extract_method(
        source,
        method_name="process",
        new_method_name="process_core",
    )

    assert transformed == source
    assert count == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "NO_SAFE_COHESIVE_BLOCK"
