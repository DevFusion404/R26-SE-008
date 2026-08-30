from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.models import TransformationLogEntry
from sctva.transformers.java_extract_class import apply_extract_class as apply_java_extract_class
from sctva.transformers.c_extract_class import apply_extract_component
from sctva.validators.syntax_validator import SyntaxValidator


def _make_java_large(source: str) -> str:
    """Keep legacy extraction fixtures above the production large-class gate."""
    padding = "\n".join(
        f"    int __sctva_padding_{index}() {{ return {index}; }}"
        for index in range(20)
    )
    return source.rsplit("}", 1)[0] + padding + "\n}\n"


JAVA_LIBRARY_SOURCE = _make_java_large('''import java.util.ArrayList;
import java.util.List;

public class LibraryManager {
    private final List<String> notices = new ArrayList<>();
    private boolean enabled = true;

    public void addNotice(String text) {
        notices.add(text);
    }

    public String latestNotice() {
        return notices.isEmpty() ? null : notices.get(notices.size() - 1);
    }

    public int noticeCount() {
        return notices.size();
    }

    public boolean isEnabled() {
        return enabled;
    }

    public void disable() {
        enabled = false;
    }
}
''')


JAVA_SETTER_SHADOW_SOURCE = _make_java_large('''public class CustomerRecord {
    private String name;
    private int accountId;

    public CustomerRecord(String name, int accountId) {
        this.name = name;
        this.accountId = accountId;
    }

    public String getName() { return this.name; }
    public void setName(String name) { this.name = name; }
    public int getAccountId() { return accountId; }
    public void setAccountId(int accountId) { this.accountId = accountId; }
    public String summary() { return getName() + ":" + getAccountId(); }
}
''')


def test_java_extract_class_non_large_source_is_not_applicable():
    source = '''class SmallRecord {
    private String value;

    String getValue() { return value; }
}
'''

    transformed, replacements, metadata = apply_java_extract_class(
        source,
        source_file="SmallRecord.java",
        current_file_name="SmallRecord.java",
        source_class="SmallRecord",
        new_class_name="RecordState",
        methods_to_extract=["getValue"],
        fields_to_extract=["value"],
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "not_applicable"
    assert metadata["success"] is True
    assert metadata["reason"] == "SOURCE_CLASS_NOT_LARGE_ENOUGH"
    assert metadata["large_class_before"]["detected"] is False
    assert metadata["replacements_count"] == 0


C_NOTICE_SOURCE = '''#include <stddef.h>

static int notice_count = 0;
static int enabled = 1;

void add_notice(void) {
    notice_count++;
}

int latest_notice(void) {
    return notice_count;
}

int clear_notices(void) {
    notice_count = 0;
    return notice_count;
}

int is_enabled(void) {
    return enabled;
}

void disable(void) {
    enabled = 0;
}
'''


def test_java_extract_class_preserves_api_moves_state_and_reduces_source_class():
    transformed, replacements, metadata = apply_java_extract_class(
        JAVA_LIBRARY_SOURCE,
        source_file="LibraryManager.java",
        current_file_name="LibraryManager.java",
        source_class="LibraryManager",
        new_class_name="NoticeBoard",
        methods_to_extract=["addNotice", "latestNotice", "noticeCount"],
        fields_to_extract=["notices"],
        project_source_files=[{
            "file_name": "LibraryManager.java",
            "source_code": JAVA_LIBRARY_SOURCE,
            "language": "java",
        }],
        repository_complete=True,
    )

    assert replacements == 1
    assert metadata["status"] == "success"
    assert "final class NoticeBoard" in transformed
    assert "private final List<String> notices = new ArrayList<>();" in transformed
    assert "private final NoticeBoard _noticeBoard = new NoticeBoard();" in transformed
    assert "return _noticeBoard.latestNotice();" in transformed
    assert "_noticeBoard.addNotice(text);" in transformed
    assert metadata["validation"]["full_api_preservation"] == "PASS"
    assert metadata["validation"]["state_compatibility"] == "PASS"
    assert metadata["validation"]["single_state_owner"] == "PASS"
    assert metadata["after_metrics"]["implementation_method_count"] < metadata["before_metrics"]["implementation_method_count"]
    assert metadata["after_metrics"]["owned_field_count"] < metadata["before_metrics"]["owned_field_count"]
    assert SyntaxValidator().validate(
        language="java",
        source_code=transformed,
        require_compilation=False,
        timeout_seconds=5,
    ).passed is True


def test_java_extract_class_supports_setter_shadowing_constructor_and_external_api():
    caller = '''class CustomerCaller {
    void update(CustomerRecord item) {
        item.setName("Maya");
        System.out.println(item.getName());
    }
}
'''
    transformed, replacements, metadata = apply_java_extract_class(
        JAVA_SETTER_SHADOW_SOURCE,
        source_file="src/model/CustomerRecord.java",
        current_file_name="src/model/CustomerRecord.java",
        source_class="CustomerRecord",
        new_class_name="CustomerIdentity",
        methods_to_extract=["getName", "setName"],
        fields_to_extract=["name"],
        project_source_files=[
            {
                "file_name": "src/model/CustomerRecord.java",
                "source_code": JAVA_SETTER_SHADOW_SOURCE,
                "language": "java",
            },
            {
                "file_name": "src/app/CustomerCaller.java",
                "source_code": caller,
                "language": "java",
            },
        ],
        repository_complete=True,
    )

    assert replacements == 1
    assert metadata["status"] == "success"
    assert metadata["dependency_analysis"]["unsupported_method_dependencies"] == []
    setter_symbols = next(
        value
        for key, value in metadata["dependency_analysis"]["symbol_ownership"].items()
        if key.startswith("setName@")
    )
    assert setter_symbols["parameters"] == ["name"]
    assert setter_symbols["qualified_fields"] == ["name"]
    assert setter_symbols["shadowed_field_names"] == ["name"]
    assert "_customerIdentity.name = name;" in transformed
    assert "_customerIdentity.setName(name);" in transformed
    assert "return _customerIdentity.getName();" in transformed
    assert transformed.count("String name;") == 1
    assert metadata["validation"]["constructor_initialization"] == "PASS"
    assert metadata["validation"]["repository_references"] == "PASS"
    assert SyntaxValidator().validate(
        language="java",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_java_extract_class_preserves_multiple_constructor_chaining_and_local_shadowing():
    source = JAVA_SETTER_SHADOW_SOURCE.replace(
        "    public CustomerRecord(String name, int accountId) {",
        '''    public CustomerRecord() {
        this("unknown", 0);
    }

    public CustomerRecord(String name, int accountId) {''',
    ).replace(
        "    public String summary() { return getName() + \":\" + getAccountId(); }",
        '''    public String summary() {
        String name = "prefix";
        return name + getName() + ":" + getAccountId();
    }''',
    )
    transformed, replacements, metadata = apply_java_extract_class(
        source,
        source_class="CustomerRecord",
        new_class_name="CustomerIdentity",
        methods_to_extract=["getName", "setName"],
        fields_to_extract=["name"],
        repository_complete=True,
    )

    assert replacements == 1
    assert metadata["status"] == "success"
    assert 'this("unknown", 0);' in transformed
    assert '_customerIdentity.name = name;' in transformed
    assert 'String name = "prefix";' in transformed
    assert 'return name + getName()' in transformed
    assert "_customerIdentity._customerIdentity" not in transformed


def test_java_extract_class_reviews_constructor_bound_final_state_precisely():
    source = JAVA_SETTER_SHADOW_SOURCE.replace(
        "private String name;",
        "private final String name;",
    ).replace(
        "public void setName(String name) { this.name = name; }",
        "public boolean hasName() { return this.name != null; }",
    )
    transformed, replacements, metadata = apply_java_extract_class(
        source,
        source_class="CustomerRecord",
        new_class_name="CustomerIdentity",
        methods_to_extract=["getName", "hasName"],
        fields_to_extract=["name"],
        repository_complete=True,
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "FINAL_CONSTRUCTOR_STATE_REQUIRES_HELPER_CONSTRUCTOR"
    assert metadata["dependency_analysis"]["constructor_bound_final_fields"] == ["name"]


def test_java_extract_class_reviews_reflection_sensitive_member_usage():
    reflection = '''class CustomerReflection {
    void inspect() throws Exception {
        CustomerRecord.class.getDeclaredField("name");
    }
}
'''
    transformed, replacements, metadata = apply_java_extract_class(
        JAVA_SETTER_SHADOW_SOURCE,
        source_class="CustomerRecord",
        new_class_name="CustomerIdentity",
        methods_to_extract=["getName", "setName"],
        fields_to_extract=["name"],
        project_source_files=[
            {"file_name": "CustomerRecord.java", "source_code": JAVA_SETTER_SHADOW_SOURCE},
            {"file_name": "CustomerReflection.java", "source_code": reflection},
        ],
        repository_complete=True,
    )

    assert transformed == JAVA_SETTER_SHADOW_SOURCE
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "REFLECTION_SENSITIVE_DEPENDENCY"
    assert metadata["dependency_analysis"]["reflection_sensitive_files"] == [
        "CustomerReflection.java"
    ]


def test_java_extract_class_reviews_static_state_instead_of_moving_it_unsafely():
    source = JAVA_SETTER_SHADOW_SOURCE.replace(
        "private String name;",
        "private static String name;",
    )
    transformed, replacements, metadata = apply_java_extract_class(
        source,
        source_class="CustomerRecord",
        new_class_name="CustomerIdentity",
        methods_to_extract=["getName", "setName"],
        fields_to_extract=["name"],
        repository_complete=True,
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "STATIC_STATE_EXTRACTION_UNSUPPORTED"


def test_java_extract_class_reviews_unresolved_inherited_member_dependency():
    source = '''class NamedBase {
    protected String inheritedName;
}

class CustomerRecord extends NamedBase {
    private String name;
    private int accountId;
    String combinedName() { return this.inheritedName + this.name; }
    void setName(String name) { this.name = name; }
    int getAccountId() { return accountId; }
    void setAccountId(int accountId) { this.accountId = accountId; }
}
'''
    source = _make_java_large(source)
    transformed, replacements, metadata = apply_java_extract_class(
        source,
        source_class="CustomerRecord",
        new_class_name="CustomerIdentity",
        methods_to_extract=["combinedName", "setName"],
        fields_to_extract=["name"],
        repository_complete=True,
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "UNSUPPORTED_METHOD_DEPENDENCY"
    assert "combinedName:inherited_or_unresolved_member:inheritedName" in (
        metadata["dependency_analysis"]["unsupported_method_dependencies"]
    )


def test_c_extract_component_preserves_functions_and_moves_static_state_once():
    transformed, replacements, metadata = apply_extract_component(
        C_NOTICE_SOURCE,
        source_file="notices.c",
        current_file_name="notices.c",
        source_class="notices",
        new_class_name="NoticeState",
        methods_to_extract=["add_notice", "latest_notice", "clear_notices"],
        fields_to_extract=["notice_count"],
        project_source_files=[{
            "file_name": "notices.c",
            "source_code": C_NOTICE_SOURCE,
            "language": "c",
        }],
        repository_complete=True,
    )

    assert replacements == 1
    assert metadata["status"] == "success"
    assert "typedef struct NoticeState" in transformed
    assert "static NoticeState _notice_state" in transformed
    assert "static void NoticeState_add_notice(NoticeState *state)" in transformed
    assert "NoticeState_add_notice(&_notice_state);" in transformed
    assert "state->notice_count++" in transformed
    assert metadata["validation"]["full_api_preservation"] == "PASS"
    assert metadata["validation"]["state_compatibility"] == "PASS"
    assert metadata["validation"]["single_state_owner"] == "PASS"
    assert metadata["after_metrics"]["implementation_method_count"] < metadata["before_metrics"]["implementation_method_count"]
    assert metadata["after_metrics"]["owned_field_count"] < metadata["before_metrics"]["owned_field_count"]
    assert SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_java_extract_class_rejects_direct_public_field_state_api():
    source = JAVA_LIBRARY_SOURCE.replace(
        "private final List<String> notices",
        "public final List<String> notices",
    )
    transformed, replacements, metadata = apply_java_extract_class(
        source,
        source_class="LibraryManager",
        new_class_name="NoticeBoard",
        methods_to_extract=["addNotice", "latestNotice", "noticeCount"],
        fields_to_extract=["notices"],
        repository_complete=True,
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "DIRECT_FIELD_API_CANNOT_BE_FORWARDED_SAFELY"


def test_java_extract_class_moves_unreferenced_package_private_state():
    source = JAVA_LIBRARY_SOURCE.replace(
        "private final List<String> notices",
        "final List<String> notices",
    )
    transformed, replacements, metadata = apply_java_extract_class(
        source,
        source_class="LibraryManager",
        new_class_name="NoticeBoard",
        methods_to_extract=["addNotice", "latestNotice", "noticeCount"],
        fields_to_extract=["notices"],
        project_source_files=[{
            "file_name": "LibraryManager.java",
            "source_code": source,
            "language": "java",
        }],
        repository_complete=True,
    )

    assert replacements == 1
    assert metadata["status"] == "success"
    assert metadata["dependency_analysis"]["package_private_fields"] == ["notices"]
    assert metadata["dependency_analysis"]["externally_used_fields"] == []
    assert metadata["compatibility"]["direct_public_field_compatibility"] == (
        "NOT_REQUIRED_NO_EXTERNAL_REFERENCES"
    )
    assert "final List<String> notices = new ArrayList<>();" in transformed


def test_java_extract_class_rejects_externally_used_package_private_state():
    source = JAVA_LIBRARY_SOURCE.replace(
        "private final List<String> notices",
        "final List<String> notices",
    )
    caller = """class Caller {
    void update(LibraryManager manager) { manager.notices.add("external"); }
}
"""
    transformed, replacements, metadata = apply_java_extract_class(
        source,
        source_class="LibraryManager",
        new_class_name="NoticeBoard",
        methods_to_extract=["addNotice", "latestNotice", "noticeCount"],
        fields_to_extract=["notices"],
        project_source_files=[
            {"file_name": "LibraryManager.java", "source_code": source, "language": "java"},
            {"file_name": "Caller.java", "source_code": caller, "language": "java"},
        ],
        repository_complete=True,
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "DIRECT_FIELD_API_CANNOT_BE_FORWARDED_SAFELY"
    assert metadata["dependency_analysis"]["externally_used_fields"] == ["notices"]


def test_c_extract_component_rejects_externally_linked_global_state():
    source = C_NOTICE_SOURCE.replace("static int notice_count", "int notice_count")
    transformed, replacements, metadata = apply_extract_component(
        source,
        source_file="notices.c",
        current_file_name="notices.c",
        source_class="notices",
        new_class_name="NoticeState",
        methods_to_extract=["add_notice", "latest_notice", "clear_notices"],
        fields_to_extract=["notice_count"],
        repository_complete=True,
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "EXTERNAL_GLOBAL_API_CANNOT_BE_FORWARDED_SAFELY"


def test_java_and_c_extract_class_are_idempotent():
    java_first, java_count, _ = apply_java_extract_class(
        JAVA_LIBRARY_SOURCE,
        source_class="LibraryManager",
        new_class_name="NoticeBoard",
        methods_to_extract=["addNotice", "latestNotice", "noticeCount"],
        fields_to_extract=["notices"],
    )
    java_second, java_second_count, java_metadata = apply_java_extract_class(
        java_first,
        source_class="LibraryManager",
        new_class_name="NoticeBoard",
        methods_to_extract=["addNotice", "latestNotice", "noticeCount"],
        fields_to_extract=["notices"],
    )

    c_first, c_count, _ = apply_extract_component(
        C_NOTICE_SOURCE,
        source_file="notices.c",
        current_file_name="notices.c",
        source_class="notices",
        new_class_name="NoticeState",
        methods_to_extract=["add_notice", "latest_notice", "clear_notices"],
        fields_to_extract=["notice_count"],
    )
    c_second, c_second_count, c_metadata = apply_extract_component(
        c_first,
        source_file="notices.c",
        current_file_name="notices.c",
        source_class="notices",
        new_class_name="NoticeState",
        methods_to_extract=["add_notice", "latest_notice", "clear_notices"],
        fields_to_extract=["notice_count"],
    )

    assert java_count == 1
    assert java_second == java_first
    assert java_second_count == 0
    assert java_metadata["status"] == "already_applied"
    assert c_count == 1
    assert c_second == c_first
    assert c_second_count == 0
    assert c_metadata["status"] == "already_applied"


def test_java_and_c_large_class_metrics_stop_triggering_after_extraction():
    java_source = _java_large_pipeline_source(
        class_name="LibraryManager",
        utility_count=18,
    )
    _, java_count, java_metadata = apply_java_extract_class(
        java_source,
        source_class="LibraryManager",
        new_class_name="NoticeBoard",
        methods_to_extract=["addNotice", "noticeCount", "hasNotices"],
        fields_to_extract=["notices"],
    )

    c_utilities = "\n".join(
        f"int utility_{index}(void) {{ return enabled ? {index} : 0; }}"
        for index in range(1, 18)
    )
    c_source = C_NOTICE_SOURCE + c_utilities + "\n"
    _, c_count, c_metadata = apply_extract_component(
        c_source,
        source_file="notices.c",
        current_file_name="notices.c",
        source_class="notices",
        new_class_name="NoticeState",
        methods_to_extract=["add_notice", "latest_notice", "clear_notices"],
        fields_to_extract=["notice_count"],
    )

    assert java_count == 1
    assert java_metadata["large_class_before"]["detected"] is True
    assert java_metadata["large_class_after"]["detected"] is False
    assert java_metadata["validation"]["large_class_reduction"] == "PASS"
    assert c_count == 1
    assert c_metadata["large_class_before"]["detected"] is True
    assert c_metadata["large_class_after"]["detected"] is False
    assert c_metadata["validation"]["large_class_reduction"] == "PASS"


def _java_large_pipeline_source(class_name="PipelineManager", utility_count=21):
    utilities = "\n".join(
        f"    int utility{index}() {{ return primary + {index}; }}"
        for index in range(utility_count)
    )
    return f'''class {class_name} {{
    private int primary;
    private int notices;
{utilities}
    void addNotice() {{ notices++; }}
    int noticeCount() {{ return notices; }}
    boolean hasNotices() {{ return notices > 0; }}
}}
'''


def test_java_extract_class_uses_current_action_metrics_and_preserves_prior_edits():
    original = _java_large_pipeline_source(utility_count=20)
    current = _java_large_pipeline_source(utility_count=21).replace(
        "class PipelineManager {",
        "class PipelineManager {\n    // accepted-prior-transformation",
    )
    transformed, replacements, metadata = apply_java_extract_class(
        current,
        source_class="PipelineManager",
        new_class_name="NoticeState",
        methods_to_extract=["addNotice", "noticeCount", "hasNotices"],
        fields_to_extract=["notices"],
        repository_original_code=original,
        prior_transformations=[{
            "action_type": "rename_method",
            "replacements_count": 1,
            "status": "success",
        }],
        repository_complete=True,
    )

    assert replacements == 1
    assert "// accepted-prior-transformation" in transformed
    assert metadata["before_metrics"]["implementation_method_count"] == 24
    assert metadata["repository_original_metrics"]["implementation_method_count"] == 23
    assert metadata["source_states"]["action_input_length"] == len(current)
    assert metadata["source_states"]["candidate_output_length"] == len(transformed)
    assert metadata["large_class_reduction_status"] == "REDUCED"
    assert metadata["large_class_after"]["detected"] is True


def test_java_extract_class_is_not_applicable_when_prior_actions_resolved_smell():
    original = _java_large_pipeline_source(utility_count=22)
    current = _java_large_pipeline_source(utility_count=2)
    transformed, replacements, metadata = apply_java_extract_class(
        current,
        source_class="PipelineManager",
        new_class_name="NoticeState",
        methods_to_extract=["addNotice", "noticeCount", "hasNotices"],
        fields_to_extract=["notices"],
        repository_original_code=original,
        prior_transformations=[{
            "action_type": "extract_method",
            "replacements_count": 1,
            "status": "success",
        }],
        repository_complete=True,
    )

    assert transformed == current
    assert replacements == 0
    assert metadata["status"] == "not_applicable"
    assert metadata["final_decision"] == "NOT_APPLICABLE"
    assert metadata["reason"] == "SOURCE_CLASS_NOT_LARGE_ENOUGH"
    assert metadata["repository_original_large_class"]["detected"] is True
    assert metadata["large_class_before"]["detected"] is False


def test_java_extract_class_evaluates_later_candidate_after_unsafe_first_candidate():
    source = '''class CandidateHost {
    private static int globalState;
    private int notices;
    private int primary;
    void globalUp() { globalState++; }
    int globalValue() { return globalState; }
    void addNotice() { notices++; }
    int noticeCount() { return notices; }
    boolean hasNotices() { return notices > 0; }
    int primaryValue() { return primary; }
    }
    '''
    source = _make_java_large(source)
    transformed, replacements, metadata = apply_java_extract_class(
        source,
        source_class="CandidateHost",
        new_class_name="NoticeState",
        repository_complete=True,
    )

    assert replacements == 1
    assert "final class NoticeState" in transformed
    evaluations = metadata["candidate_evaluations"]
    assert evaluations[0]["failure_reason"] == "STATIC_STATE_EXTRACTION_UNSUPPORTED"
    assert any(item["selected"] for item in evaluations[1:])
    assert metadata["fields_moved"] == ["notices"]


def test_java_extract_class_returns_generic_review_after_all_candidates_fail():
    source = '''class UnsafeCandidateHost {
    private static int globalState;
    private int primary;
    void globalUp() { globalState++; }
    int globalValue() { return globalState; }
    int primaryValue() { return primary; }
    }
    '''
    source = _make_java_large(source)
    transformed, replacements, metadata = apply_java_extract_class(
        source,
        source_class="UnsafeCandidateHost",
        new_class_name="GlobalState",
        repository_complete=True,
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "NO_SAFE_MEANINGFUL_EXTRACT_CLASS_CANDIDATE"
    assert metadata["candidate_evaluations"][0]["failure_reason"] == (
        "STATIC_STATE_EXTRACTION_UNSUPPORTED"
    )


def test_java_extract_class_re_resolves_stale_class_name_from_current_members():
    source = _java_large_pipeline_source(
        class_name="CurrentPipelineManager",
        utility_count=2,
    )
    source = _make_java_large(source)
    transformed, replacements, metadata = apply_java_extract_class(
        source,
        source_class="OldPipelineManager",
        new_class_name="NoticeState",
        methods_to_extract=["addNotice", "noticeCount", "hasNotices"],
        fields_to_extract=["notices"],
        repository_complete=True,
    )

    assert replacements == 1
    assert "class CurrentPipelineManager" in transformed
    assert metadata["source_class"] == "CurrentPipelineManager"
    assert metadata["current_class_resolution"]["strategy"] == (
        "current_ast_member_identity_recovery"
    )


def test_failed_java_extract_class_candidate_keeps_current_pre_action_source():
    current = '''class CurrentState {
    // accepted-prior-transformation
    private final String name;
    CurrentState(String name) { this.name = name; }
    String getName() { return name; }
    boolean hasName() { return name != null; }
    int keepResponsibility() { return 1; }
    }
    '''
    current = _make_java_large(current)
    transformed, replacements, metadata = apply_java_extract_class(
        current,
        source_class="CurrentState",
        new_class_name="IdentityState",
        methods_to_extract=["getName", "hasName"],
        fields_to_extract=["name"],
        repository_original_code=current.replace(
            "// accepted-prior-transformation\n    ",
            "",
        ),
        prior_transformations=[{
            "action_type": "introduce_constant",
            "replacements_count": 1,
            "status": "success",
        }],
        repository_complete=True,
    )

    assert replacements == 0
    assert transformed == current
    assert "// accepted-prior-transformation" in transformed
    assert metadata["reason"] == "FINAL_CONSTRUCTOR_STATE_REQUIRES_HELPER_CONSTRUCTOR"


def test_extract_class_final_audit_preserves_not_applicable_decision():
    entry = TransformationLogEntry(
        action_index=1,
        action_type="extract_java_class",
        replacements_count=0,
        metadata={
            "status": "not_applicable",
            "reason": "SMELL_ALREADY_RESOLVED_BY_PRIOR_TRANSFORMATIONS",
        },
    )

    SafeCodeTransformationValidationAgent._finalize_extract_class_logs(
        [entry],
        syntax_passed=True,
        structural_passed=True,
        behavioral_passed=True,
        invariant_passed=True,
        rollback_occurred=False,
    )

    assert entry.metadata["status"] == "not_applicable"
    assert entry.metadata["final_decision"] == "NOT_APPLICABLE"
    assert entry.metadata["final_checks"]["large_class_reduction"] == (
        "NOT_APPLICABLE"
    )


ORDER_LARGE_CLASS_REGRESSION_SOURCE = '''public class Order {
    private String type;
    private int quantity;
    private String fullName;
    private String phoneNo;
    private String email;
    private String notes;
    private String pickUpAddress;
    private String deliveryAddress;
    private int orderid;

    public String getType() { return type; }
    public int getQuantity() { return quantity; }
    public String getFullName() { return fullName; }
    public String getPhoneNo() { return phoneNo; }
    public String getEmail() { return email; }
    public String getNotes() { return notes; }
    public String getPickUpAddress() { return pickUpAddress; }
    public String getDeliveryAddress() { return deliveryAddress; }
    public int getOrderid() { return orderid; }

    public void setType(String type) { this.type = type; }
    public void setQuantity(int quantity) { this.quantity = quantity; }
    public void setFullName(String fullName) { this.fullName = fullName; }
    public void setPhoneNo(String phoneNo) { this.phoneNo = phoneNo; }
    public void setEmail(String email) { this.email = email; }
    public void setNotes(String notes) { this.notes = notes; }
    public void setPickUpAddress(String pickUpAddress) { this.pickUpAddress = pickUpAddress; }
    public void setDeliveryAddress(String deliveryAddress) { this.deliveryAddress = deliveryAddress; }
    public void setOrderid(int orderid) { this.orderid = orderid; }

    @Override
    public String toString() {
        return "Order [orderid=" + orderid + ", type=" + type + ", quantity=" + quantity
                + ", fullName=" + fullName + ", phoneNo=" + phoneNo + ", email=" + email
                + ", notes=" + notes + ", pickUpAddress=" + pickUpAddress
                + ", deliveryAddress=" + deliveryAddress + "]";
    }
}
'''


def test_java_order_like_extract_class_accepts_meaningful_reduction_even_if_smell_remains():
    transformed, replacements, metadata = apply_java_extract_class(
        ORDER_LARGE_CLASS_REGRESSION_SOURCE,
        source_file="Order.java",
        current_file_name="src/main/java/Model/Order.java",
        source_class="Order",
        new_class_name="OrderHelper",
        repository_complete=True,
    )

    assert replacements == 1
    assert metadata["status"] == "success"
    assert metadata["reason"] == "extract_class_applied"
    assert metadata["plan_compliance"] == "PASS"
    assert metadata["large_class_before"]["detected"] is True
    # This is the critical regression: a safe meaningful reduction is valid
    # even if the source still touches the Large Class threshold afterwards.
    assert metadata["large_class_after"]["detected"] is True
    assert metadata["large_class_reduction_status"] == "REDUCED"
    assert metadata["smell_reduced"] is True
    assert metadata["validation"]["large_class_reduction"] == "PASS"
    assert metadata["implementation_revision"] == (
        "java_extract_class_precondition_v13_20260829"
    )
    assert "class OrderHelper" in transformed
    assert metadata["candidate_evaluations"]
    assert any(item["selected"] for item in metadata["candidate_evaluations"])
