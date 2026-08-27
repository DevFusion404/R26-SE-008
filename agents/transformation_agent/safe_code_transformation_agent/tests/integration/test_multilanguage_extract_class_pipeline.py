from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.integration.planner_adapter import PlannerAdapter


JAVA_SOURCE = '''import java.util.ArrayList;
import java.util.List;

public class LibraryManager {
    private final List<String> notices = new ArrayList<>();
    private boolean enabled = true;
    public void addNotice(String text) { notices.add(text); }
    public String latestNotice() { return notices.isEmpty() ? null : notices.get(notices.size() - 1); }
    public int noticeCount() { return notices.size(); }
    public boolean isEnabled() { return enabled; }
    public void disable() { enabled = false; }
}
'''


C_SOURCE = '''static int notice_count = 0;
static int enabled = 1;
void add_notice(void) { notice_count++; }
int latest_notice(void) { return notice_count; }
int clear_notices(void) { notice_count = 0; return notice_count; }
int is_enabled(void) { return enabled; }
void disable(void) { enabled = 0; }
'''


NESTED_LARGE_LIBRARY_SOURCE = '''import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

class LargeClassLibrarySystem {
    static class LibraryManager {
        private final List<String> books = new ArrayList<>();
        private final Set<String> members = new HashSet<>();
        private final Map<String, String> loans = new HashMap<>();
        final Map<String, Double> fines = new HashMap<>();
        final List<String> notices = new ArrayList<>();

        void addBook(String id) { books.add(id); }
        void removeBook(String id) { books.remove(id); }
        boolean hasBook(String id) { return books.contains(id); }
        int bookCount() { return books.size(); }
        boolean hasBooks() { return !books.isEmpty(); }

        void addMember(String id) { members.add(id); }
        boolean hasMember(String id) { return members.contains(id); }
        int memberCount() { return members.size(); }
        boolean hasMembers() { return !members.isEmpty(); }

        void borrowBook(String member, String book) { loans.put(book, member); }
        void returnBook(String book) { loans.remove(book); }
        String borrowerOf(String book) { return loans.get(book); }
        int loanCount() { return loans.size(); }
        boolean hasLoans() { return !loans.isEmpty(); }

        void addFine(String id, double amount) {
            fines.put(id, fines.getOrDefault(id, 0.0) + amount);
        }
        void payFine(String id, double amount) {
            fines.put(id, Math.max(0.0, fines.getOrDefault(id, 0.0) - amount));
        }
        double fineBalance(String id) { return fines.getOrDefault(id, 0.0); }

        void addNotice(String text) { notices.add(text); }
        String latestNotice() {
            return notices.isEmpty() ? null : notices.get(notices.size() - 1);
        }

        String summary() {
            double totalFines = fines.values().stream().mapToDouble(Double::doubleValue).sum();
            return "Library summary: books=" + books.size()
                    + ", members=" + members.size()
                    + ", loans=" + loans.size()
                    + ", fines=" + totalFines;
        }
        String status() {
            return "active=" + (!books.isEmpty() || !members.isEmpty() || !loans.isEmpty());
        }
    }

    public static void main(String[] args) {
        LibraryManager m = new LibraryManager();
        m.addBook("B001");
        m.addBook("B002");
        m.addMember("M001");
        m.borrowBook("M001", "B001");
        m.addFine("M001", 25);
        m.addNotice("Library closes at 5 PM today.");
        System.out.println(m.summary());
        System.out.println(m.latestNotice());
    }
}
'''


def _execution_options() -> dict[str, object]:
    return {
        "strict_mode": True,
        "enable_behavior_tests": True,
        "timeout_seconds": 10,
        "require_compilation": False,
        "enable_sctva_auto_refactoring": False,
    }


def test_java_extract_class_runs_through_full_agent_pipeline():
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "java_extract_class_pipeline",
        "language": "java",
        "source_files": [{
            "file_name": "src/LibraryManager.java",
            "source_code": JAVA_SOURCE,
            "language": "java",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "java_extract_class_plan",
            "actions": [{
                "action_type": "extract_java_class",
                "parameters": {
                    "source_class": "LibraryManager",
                    "new_class_name": "NoticeBoard",
                    "methods_to_extract": ["addNotice", "latestNotice", "noticeCount"],
                    "fields_to_extract": ["notices"],
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _execution_options(),
    })

    metadata = result["safety_report"]["transformation_log"][0]["metadata"]
    assert result["safety_report"]["transformation_log"][0]["action_type"] == "extract_java_class"
    assert result["success"] is True
    assert result["rollback_occurred"] is False
    assert result["transformation_applied"] is True
    assert result["file_name"] == "src/LibraryManager.java"
    assert metadata["language"] == "java"
    assert metadata["final_decision"] == "PASS"
    assert metadata["final_checks"]["plan_compliance"] == "PASS"
    assert metadata["final_checks"]["structural_refactoring"] == "PASS"
    assert metadata["final_checks"]["behavior_preservation"] == "PASS"
    assert metadata["final_checks"]["full_api_preservation"] == "PASS"
    assert metadata["final_checks"]["state_compatibility"] == "PASS"
    assert metadata["final_checks"]["single_state_owner"] == "PASS"
    assert metadata["final_checks"]["large_class_reduction"] == "PASS"


def test_c_extract_component_runs_through_full_agent_pipeline():
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "c_extract_component_pipeline",
        "language": "c",
        "source_files": [{
            "file_name": "src/notices.c",
            "source_code": C_SOURCE,
            "language": "c",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "c_extract_component_plan",
            "actions": [{
                "action_type": "extract_c_component",
                "parameters": {
                    "new_component_name": "NoticeState",
                    "functions_to_extract": ["add_notice", "latest_notice", "clear_notices"],
                    "globals_to_extract": ["notice_count"],
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _execution_options(),
    })

    metadata = result["safety_report"]["transformation_log"][0]["metadata"]
    assert result["safety_report"]["transformation_log"][0]["action_type"] == "extract_c_component"
    assert result["success"] is True
    assert result["rollback_occurred"] is False
    assert result["transformation_applied"] is True
    assert result["file_name"] == "src/notices.c"
    assert metadata["language"] == "c"
    assert metadata["refactoring"] == "Extract Component"
    assert metadata["sctva_action"] == "extract_c_component"
    assert metadata["final_decision"] == "PASS"
    assert metadata["final_checks"]["plan_compliance"] == "PASS"
    assert metadata["final_checks"]["structural_refactoring"] == "PASS"
    assert metadata["final_checks"]["behavior_preservation"] == "PASS"
    assert metadata["final_checks"]["full_api_preservation"] == "PASS"
    assert metadata["final_checks"]["state_compatibility"] == "PASS"
    assert metadata["final_checks"]["single_state_owner"] == "PASS"
    assert metadata["final_checks"]["large_class_reduction"] == "PASS"


def test_java_extract_class_resolves_numbered_filename_to_declared_class():
    file_name = "02_large_class_library_system.java"
    normalized_plan = PlannerAdapter().normalize_plan({
        "plan_id": "java_numbered_file_extract_class",
        "steps": [{
            "step_id": "extract-library-state",
            "smell": "Large Class",
            "refactoring": "Extract Class",
            "target": {"file": file_name},
            "parameters": {
                "new_class_name": "NoticeBoard",
                "methods_to_extract": ["addNotice", "latestNotice", "noticeCount"],
                "fields_to_extract": ["notices"],
            },
        }],
    })
    inferred = normalized_plan["actions"][0]["parameters"]
    assert normalized_plan["actions"][0]["action_type"] == "extract_java_class"
    assert inferred["source_class"] == "02_large_class_library_system"
    assert inferred["source_class_origin"] == "file_stem_fallback"

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "java_numbered_file_extract_class",
        "language": "java",
        "source_files": [{
            "file_name": file_name,
            "source_code": JAVA_SOURCE,
            "language": "java",
            "source_mode": "raw",
        }],
        "refactoring_plan": normalized_plan,
        "execution_options": _execution_options(),
    })

    metadata = result["safety_report"]["transformation_log"][0]["metadata"]
    assert result["success"] is True
    assert result["rollback_occurred"] is False
    assert result["transformation_applied"] is True
    assert metadata["source_class"] == "LibraryManager"
    assert metadata["requested_source_class"] == "02_large_class_library_system"
    assert metadata["source_class_resolution"] == "parsed_class_and_member_identity"
    assert metadata["final_decision"] == "PASS"
    assert "final class NoticeBoard" in result["refactored_code"]


def test_java_extract_class_resolves_frontend_file_stem_without_origin_metadata():
    file_name = "02_large_class_library_system.java"
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "java_frontend_file_stem_extract_class",
        "language": "java",
        "source_files": [{
            "file_name": file_name,
            "source_code": JAVA_SOURCE,
            "language": "java",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "java_frontend_file_stem_plan",
            "actions": [{
                "action_type": "extract_java_class",
                "parameters": {
                    "source_file": file_name,
                    "source_class": "02_large_class_library_system",
                    "new_class_name": "NoticeBoard",
                    "methods_to_extract": ["addNotice", "latestNotice", "noticeCount"],
                    "fields_to_extract": ["notices"],
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _execution_options(),
    })

    log = result["safety_report"]["transformation_log"][0]
    assert result["transformation_applied"] is True
    assert log["metadata"]["source_class"] == "LibraryManager"
    assert log["metadata"]["requested_source_class"] == "02_large_class_library_system"
    assert log["metadata"]["source_class_resolution"] == "parsed_class_and_member_identity"
    assert "SOURCE_FILE_CLASS_MISMATCH" not in " ".join(log["warnings"])


def test_java_extract_class_keeps_explicit_wrong_class_as_mismatch():
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "java_explicit_wrong_class",
        "language": "java",
        "source_files": [{
            "file_name": "LibraryManager.java",
            "source_code": JAVA_SOURCE,
            "language": "java",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "java_explicit_wrong_class_plan",
            "actions": [{
                "action_type": "extract_java_class",
                "parameters": {
                    "source_file": "LibraryManager.java",
                    "source_class": "WrongClass",
                    "source_class_origin": "rdp_explicit",
                    "new_class_name": "NoticeBoard",
                },
            }],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _execution_options(),
    })

    log = result["safety_report"]["transformation_log"][0]
    assert result["transformation_applied"] is False
    assert "SOURCE_FILE_CLASS_MISMATCH" in " ".join(log["warnings"])


def test_nested_java_large_class_extracts_fines_and_notices_with_constants():
    file_name = "02_large_class_library_system.java"
    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "nested_java_large_class",
        "language": "java",
        "source_files": [{
            "file_name": file_name,
            "source_code": NESTED_LARGE_LIBRARY_SOURCE,
            "language": "java",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "nested_java_large_class_plan",
            "actions": [
                {
                    "action_type": "introduce_constant",
                    "parameters": {
                        "source_file": file_name,
                        "literal_value": 25,
                        "constant_name": "CONSTANT_25",
                    },
                },
                {
                    "action_type": "introduce_constant",
                    "parameters": {
                        "source_file": file_name,
                        "literal_value": 5,
                        "constant_name": "CONSTANT_5",
                    },
                },
                {
                    "action_type": "extract_java_class",
                    "parameters": {
                        "source_file": file_name,
                        "source_class": "LibraryManager",
                        "source_class_origin": "rdp_explicit",
                        "new_class_name": "LibraryManagerHelper",
                    },
                },
            ],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": _execution_options(),
    })

    transformed = result["refactored_code"]
    extract_log = next(
        entry for entry in result["safety_report"]["transformation_log"]
        if entry["action_type"] == "extract_java_class"
    )
    metadata = extract_log["metadata"]

    assert result["success"] is True, result
    assert result["rollback_occurred"] is False
    assert "static class LibraryManagerHelper" in transformed
    assert metadata["fields_moved"] == ["fines", "notices"]
    assert metadata["methods_moved"] == [
        "addFine", "payFine", "fineBalance", "addNotice", "latestNotice",
    ]
    assert metadata["responsibilities_moved"] == 2
    assert metadata["after_metrics"]["implementation_method_count"] == 16
    assert metadata["validation"]["large_class_reduction"] == "PASS"
    assert [item["action_type"] for item in metadata["prior_transformations"]] == [
        "introduce_constant",
        "introduce_constant",
    ]
    assert metadata["source_states"]["repository_original_code"] == "immutable"
    assert metadata["source_states"]["action_input_code"] == "current_working_source"
    assert metadata["source_states"]["candidate_output_code"] == (
        "temporary_until_accepted"
    )
    assert metadata["source_states"]["action_input_length"] != metadata[
        "source_states"
    ]["repository_original_length"]
    assert len(metadata["candidate_evaluations"]) > 1
    assert sum(item["selected"] is True for item in metadata["candidate_evaluations"]) == 1
    assert "final Map<String, Double> fines" not in _source_class_text(
        transformed, "LibraryManager"
    )
    assert "final List<String> notices" not in _source_class_text(
        transformed, "LibraryManager"
    )
    assert "private static final int CONSTANT_25 = 25;" in transformed
    assert "m.addFine(\"M001\", CONSTANT_25);" in transformed
    assert "private static final int CONSTANT_5 = 5;" in transformed
    assert '"Library closes at " + CONSTANT_5 + " PM today."' in transformed
    java_results = result["validation"]["behavioral"]["details"]["java_results"]
    assert java_results
    assert {item["original_target_method"] for item in java_results} == {
        "addFine", "payFine", "fineBalance", "addNotice", "latestNotice",
    }
    assert all(
        item["original_fingerprint"].get("exception_type") != "NoSuchMethodException"
        and item["transformed_fingerprint"].get("exception_type") != "NoSuchMethodException"
        for item in java_results
    )


def _source_class_text(source: str, class_name: str) -> str:
    marker = f"class {class_name} {{"
    start = source.index(marker)
    open_brace = source.index("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"Unbalanced class {class_name}")
