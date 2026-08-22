from sctva.transformers.java_extract_class import apply_extract_class as apply_java_extract_class
from sctva.transformers.c_extract_class import apply_extract_component
from sctva.validators.syntax_validator import SyntaxValidator


JAVA_LIBRARY_SOURCE = '''import java.util.ArrayList;
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
'''


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
    java_utilities = "\n".join(
        f"    public int utility{index}() {{ return enabled ? {index} : 0; }}"
        for index in range(1, 18)
    )
    java_source = JAVA_LIBRARY_SOURCE.rsplit("}", 1)[0] + java_utilities + "\n}\n"
    _, java_count, java_metadata = apply_java_extract_class(
        java_source,
        source_class="LibraryManager",
        new_class_name="NoticeBoard",
        methods_to_extract=["addNotice", "latestNotice", "noticeCount"],
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
