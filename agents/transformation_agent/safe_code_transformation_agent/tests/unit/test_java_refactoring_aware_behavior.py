from __future__ import annotations

import pytest

from sctva.contracts import RefactoringAction
from sctva.transformers.java_parameter_object import (
    apply_introduce_parameter_object,
)
from sctva.validators.behavioral_validator import BehavioralValidator


ORIGINAL = '''class CustomerService {
    public static String update(String name, String address, String email,
            String mobile, String nic, String username, String password)
            throws java.io.IOException {
        if (name == null) throw new java.io.IOException();
        return name + address + email + mobile + nic + username + password;
    }
}
'''


def _valid_migration() -> tuple[str, RefactoringAction]:
    transformed, replacements, metadata = apply_introduce_parameter_object(
        ORIGINAL,
        method="update",
        source_class="CustomerService",
        parameter_object_name="UpdateParams",
    )
    assert replacements == 1
    assert metadata["status"] == "success"
    return transformed, RefactoringAction(
        action_type="introduce_java_parameter_object",
        parameters={
            "method": "update",
            "source_class": "CustomerService",
            "parameter_object_name": "UpdateParams",
            "applied_transformation_metadata": metadata,
        },
    )


def _static_comparison(transformed: str, action: RefactoringAction | None = None) -> dict:
    validator = BehavioralValidator()
    original_summary = validator._java_static_summary(ORIGINAL)
    transformed_summary = validator._java_static_summary(transformed)
    return validator._compare_java_static_compatibility(
        original_summary,
        transformed_summary,
        [action] if action else [],
        structural_validation_passed=True,
    )


def test_parameter_object_signature_migration_is_behaviorally_compatible():
    transformed, action = _valid_migration()

    comparison = _static_comparison(transformed, action)

    assert comparison["matched"] is True
    assert comparison["signature_change"] == "EXPECTED"
    assert comparison["signature_compatibility"] == "PASS"
    assert comparison["compatibility_reason"] == (
        "INTRODUCE_PARAMETER_OBJECT_MAPPING_PRESERVED"
    )


def test_java_parameter_object_signature_migration_follows_extracted_helper_body():
    original = '''class CustomerService {
    public static String update(String name, String address, String email) {
        String combined = name + address;
        return combined + email;
    }
}
'''
    transformed = '''class CustomerService {
    static class UpdateParams {
        String name;
        String address;
        String email;

        UpdateParams(String name, String address, String email) {
            this.name = name;
            this.address = address;
            this.email = email;
        }
    }

    public static String update(UpdateParams params) {
        return buildUpdate(params);
    }

    private static String buildUpdate(UpdateParams params) {
        String combined = params.name + params.address;
        return combined + params.email;
    }
}
'''
    metadata = {
        "language": "java",
        "method": "update",
        "source_class": "CustomerService",
        "parameter_object_name": "UpdateParams",
        "parameter_name": "params",
        "parameters_moved": ["name", "address", "email"],
        "parameter_types": {
            "name": "String",
            "address": "String",
            "email": "String",
        },
        "status": "success",
        "plan_compliance": "PASS",
        "validation": {
            "syntax": "PASS",
            "parameter_object": "PASS",
            "signature_reduction": "PASS",
            "body_access": "PASS",
            "call_sites": "PASS",
        },
    }
    action = RefactoringAction(
        action_type="introduce_java_parameter_object",
        parameters={
            "method": "update",
            "source_class": "CustomerService",
            "parameter_object_name": "UpdateParams",
            "applied_transformation_metadata": metadata,
        },
    )
    validator = BehavioralValidator()

    comparison = validator._compare_java_static_compatibility(
        validator._java_static_summary(original),
        validator._java_static_summary(transformed),
        [action],
        structural_validation_passed=True,
    )

    assert comparison["matched"] is True
    assert comparison["signature_change"] == "EXPECTED"
    assert comparison["signature_compatibility"] == "PASS"
    assert comparison["expected_signature_migrations"][0]["body_migration"] == "PASS"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda code: code.replace("        String password;\n", ""),
            "PARAMETER_OBJECT_FIELDS_INCOMPLETE_OR_REORDERED",
        ),
        (
            lambda code: code.replace("        String email;", "        Object email;"),
            "PARAMETER_OBJECT_FIELD_TYPES_NOT_PRESERVED",
        ),
        (
            lambda code: code.replace(
                "public static String update(UpdateParams params)",
                "public static Object update(UpdateParams params)",
            ),
            "METHOD_RETURN_TYPE_CHANGED",
        ),
        (
            lambda code: code.replace(
                "params.name + params.address",
                "params.address + params.name",
            ),
            "PARAMETER_OBJECT_BODY_MAPPING_NOT_PRESERVED",
        ),
    ],
)
def test_invalid_parameter_object_migrations_still_fail(mutate, reason):
    transformed, action = _valid_migration()

    comparison = _static_comparison(mutate(transformed), action)

    assert comparison["matched"] is False
    changed = comparison["transformed"]["changed_methods"]
    param_change = next(item for item in changed if item["field"] == "param_types")
    assert param_change["compatibility_reason"] == reason


def test_unapproved_ordinary_signature_change_still_fails():
    transformed = ORIGINAL.replace(
        "String username, String password",
        "String username, Object password",
    )

    comparison = _static_comparison(transformed)

    assert comparison["matched"] is False
    assert comparison["signature_change"] == "UNEXPECTED"
    assert comparison["signature_compatibility"] == "FAIL"


def test_dependency_unavailable_uses_refactoring_aware_static_fallback(monkeypatch):
    transformed, action = _valid_migration()
    validator = BehavioralValidator()
    dependency_failure = {
        "success": False,
        "timeout": False,
        "exception_type": "CompilationError",
        "exception_message_category": "javac_failed",
        "stderr": "error: package missing.dependency does not exist",
    }
    monkeypatch.setattr(
        validator,
        "_run_java_runtime_probe",
        lambda **_kwargs: dict(dependency_failure),
    )

    result = validator.validate(
        language="java",
        original_code=ORIGINAL,
        transformed_code=transformed,
        behavior_tests=[],
        enable_behavior_tests=True,
        actions=[action],
        strict_mode=True,
        structural_validation_passed=True,
    )

    assert result.passed is True
    assert result.details["behavioral_validation_mode"] == (
        "refactoring_aware_static_fallback"
    )
    assert result.details["signature_change"] == "EXPECTED"
    assert result.details["signature_compatibility"] == "PASS"
    assert result.details["compatibility_reason"] == (
        "INTRODUCE_PARAMETER_OBJECT_MAPPING_PRESERVED"
    )
    assert result.details["static_comparison"]["signature_change"] == "EXPECTED"
    assert result.details["runtime_unavailable_reason"] == "missing_java_dependencies"


def test_signature_migration_requires_structural_validation_pass():
    transformed, action = _valid_migration()
    validator = BehavioralValidator()

    comparison = validator._compare_java_static_compatibility(
        validator._java_static_summary(ORIGINAL),
        validator._java_static_summary(transformed),
        [action],
        structural_validation_passed=False,
    )

    assert comparison["matched"] is False
    changed = comparison["transformed"]["changed_methods"]
    param_change = next(item for item in changed if item["field"] == "param_types")
    assert param_change["compatibility_reason"] == "STRUCTURAL_VALIDATION_NOT_PASSED"
