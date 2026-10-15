from sctva.analysis import LocalRefactorDetector
from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.contracts import RefactoringAction
from sctva.transformers import c_transformers
from sctva.transformers.engine import TransformationEngine


def test_c_per_action_checkpoint_accepts_ifdef_else_entry_points():
    source = '''#include <stddef.h>
#ifdef _WIN32
int WINAPI WinMain(void) {
#else
int main(void) {
#endif
  int width = 480;
  return width > 0 ? 0 : 1;
}
'''
    action = RefactoringAction(
        action_type="introduce_constant",
        parameters={
            "literal_value": 480,
            "constant_name": "WINDOW_WIDTH",
            "source_line": 7,
        },
    )

    transformed, logs, warnings = TransformationEngine().apply_actions(
        language="c",
        source_code=source,
        actions=[action],
        strict_mode=True,
    )

    assert logs[0].replacements_count == 1
    assert "#define WINDOW_WIDTH 480" in transformed
    assert "int width = WINDOW_WIDTH;" in transformed
    assert not any("Unclosed delimiter" in warning for warning in warnings)


def test_c_header_struct_array_extent_is_safe_introduce_constant_target():
    source = '''/* license */
#ifndef WEBVIEW_TYPES_H
#define WEBVIEW_TYPES_H

typedef struct {
  char version_number[32];
  char pre_release[48];
} webview_version_info_t;

#endif
'''
    transformed, replacements, metadata = c_transformers.apply_introduce_constant(
        source,
        32,
        "WEBVIEW_VERSION_NUMBER_CAPACITY",
        6,
    )

    assert replacements == 1
    assert metadata["status"] == "success"
    assert metadata["target_context"] == "C_ARRAY_EXTENT"
    assert "#define WEBVIEW_VERSION_NUMBER_CAPACITY 32" in transformed
    assert "char version_number[WEBVIEW_VERSION_NUMBER_CAPACITY];" in transformed
    assert transformed.index("#define WEBVIEW_TYPES_H") < transformed.index(
        "#define WEBVIEW_VERSION_NUMBER_CAPACITY 32"
    )


def test_c_local_and_parameter_array_bounds_remain_conservative():
    parameter_source = "int f(char userID[15]) { return userID[0]; }\n"
    local_source = "void f(void) { char filename[20]; filename[0] = '\\0'; }\n"

    assert c_transformers.analyze_extract_constant_target(parameter_source, 15)[
        "reason"
    ] == "TARGET_IN_C_TYPE_OR_SIGNATURE_CONTEXT"
    assert c_transformers.analyze_extract_constant_target(local_source, 20)[
        "reason"
    ] == "TARGET_IN_C_TYPE_OR_SIGNATURE_CONTEXT"


def test_c_enum_numeric_initializer_is_already_symbolic_not_rewritten():
    source = '''typedef enum {
  WEBVIEW_ERROR_INVALID_ARGUMENT = -2,
  WEBVIEW_ERROR_OK = 0
} webview_error_t;
'''
    transformed, replacements, metadata = c_transformers.apply_introduce_constant(
        source,
        -2,
        "INVALID_ARGUMENT_VALUE",
        2,
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "already_handled"
    assert metadata["reason"] == "ALREADY_SYMBOLIC_ENUM_VALUE"


def test_c_stale_line_enum_value_is_still_classified_as_already_symbolic():
    source = '''#ifndef WEBVIEW_ERRORS_H
#define WEBVIEW_ERRORS_H

typedef enum {
  WEBVIEW_ERROR_MISSING_DEPENDENCY = -5,
  WEBVIEW_ERROR_OK = 0
} webview_error_t;

#endif
'''

    transformed, replacements, metadata = c_transformers.apply_introduce_constant(
        source,
        -5,
        "THRESHOLD_LIMIT_NEG_5",
        source_line=12,
        reference_source_code=source,
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "already_handled"
    assert metadata["reason"] == "ALREADY_SYMBOLIC_ENUM_VALUE"


def test_c_existing_macro_value_is_already_symbolic_not_rewritten():
    source = '''#ifndef WEBVIEW_VERSION_H
#define WEBVIEW_VERSION_H
#define WEBVIEW_VERSION_MINOR 12
#endif
'''
    transformed, replacements, metadata = c_transformers.apply_introduce_constant(
        source,
        12,
        "VERSION_MINOR_VALUE",
        3,
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "already_handled"
    assert metadata["reason"] == "ALREADY_SYMBOLIC_MACRO_VALUE"


def test_embedded_javascript_identifier_is_not_a_c_global_variable():
    source = '''static const char html[] = "\\
<script type=\\"module\\">\\n\\
  const getElements = ids => Object.assign({}, ...ids);\\n\\
  const ui = getElements([]);\\n\\
</script>";

int main(void) { return 0; }
'''

    analysis = c_transformers.analyze_c_global_variable_target(
        source, "getElements"
    )
    assert analysis["status"] == "not_applicable"
    assert analysis["reason"] == "TARGET_ONLY_IN_C_STRING_OR_COMMENT"

    transformed, replacements, metadata = c_transformers.apply_encapsulate_c_variable(
        source,
        variable_name="getElements",
    )
    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "not_applicable"


def test_local_c_magic_number_detector_skips_enum_and_macro_but_keeps_struct_array_extent():
    source = '''#define VERSION_MINOR 12

typedef enum {
  ERROR_CODE = 422
} error_t;

typedef struct {
  char name[32];
} item_t;
'''
    actions = LocalRefactorDetector().detect(
        language="c",
        file_name="types.h",
        source_code=source,
        existing_actions=[],
    )
    literal_values = [
        action.parameters.get("literal_value")
        for action in actions
        if action.action_type == "introduce_constant"
    ]

    assert 32 in literal_values
    assert 12 not in literal_values
    assert 422 not in literal_values


def test_c_header_repeated_array_sizes_survive_line_drift_between_actions():
    original = '''#ifndef WEBVIEW_TYPES_H
#define WEBVIEW_TYPES_H

typedef struct {
  char version_number[32];
  char pre_release[48];
  char build_metadata[48];
} webview_version_info_t;

#endif
'''
    current = original
    for value, line, name in (
        (32, 5, "WEBVIEW_VERSION_NUMBER_CAPACITY"),
        (48, 6, "WEBVIEW_PRE_RELEASE_CAPACITY"),
        (48, 7, "WEBVIEW_BUILD_METADATA_CAPACITY"),
    ):
        current, replacements, metadata = c_transformers.apply_introduce_constant(
            current,
            value,
            name,
            line,
            reference_source_code=original,
        )
        assert replacements == 1, metadata
        assert metadata["status"] == "success"

    assert "version_number[WEBVIEW_VERSION_NUMBER_CAPACITY]" in current
    assert "pre_release[WEBVIEW_PRE_RELEASE_CAPACITY]" in current
    assert "build_metadata[WEBVIEW_BUILD_METADATA_CAPACITY]" in current


def test_c_header_stale_line_can_resolve_cohesive_repeated_array_extents():
    source = '''#ifndef WEBVIEW_TYPES_H
#define WEBVIEW_TYPES_H

typedef struct {
  char pre_release[48];
  char build_metadata[48];
} webview_version_info_t;

#endif
'''

    transformed, replacements, metadata = c_transformers.apply_introduce_constant(
        source,
        48,
        "WEBVIEW_VERSION_LABEL_CAPACITY",
        source_line=24,
        reference_source_code=source,
    )

    assert replacements == 2
    assert metadata["status"] == "success"
    assert metadata["target_resolution"] == "stale_line_cohesive_array_extent_group"
    assert "char pre_release[WEBVIEW_VERSION_LABEL_CAPACITY];" in transformed
    assert "char build_metadata[WEBVIEW_VERSION_LABEL_CAPACITY];" in transformed


def test_c_stale_line_keeps_unrelated_repeated_literals_ambiguous():
    source = "int first(void) { return 48; }\nint second(void) { return 48; }\n"

    transformed, replacements, metadata = c_transformers.apply_introduce_constant(
        source,
        48,
        "VALUE_48",
        source_line=99,
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "C_LITERAL_TARGET_AMBIGUOUS"


def test_c_already_symbolic_header_target_is_not_reported_as_not_applied():
    result = SafeCodeTransformationValidationAgent().execute(
        {
            "request_id": "already_symbolic_header",
            "language": "c",
            "source_code": "#ifndef VERSION_H\n#define VERSION_H\n#define VERSION_MINOR 12\n#endif\n",
            "refactoring_plan": {
                "plan_id": "already_symbolic_header_plan",
                "actions": [{
                    "action_type": "introduce_constant",
                    "parameters": {
                        "literal_value": 12,
                        "constant_name": "VERSION_MINOR_VALUE",
                        "source_line": 3,
                    },
                }],
                "behavior_tests": [],
            },
            "execution_options": {
                "strict_mode": True,
                "enable_behavior_tests": True,
                "require_compilation": False,
                "enable_sctva_auto_refactoring": False,
            },
        }
    )

    assert result["status"] == "ALREADY_HANDLED"
    assert result["application_status"] == "ALREADY_HANDLED"
    assert result["transformation_applied"] is False
