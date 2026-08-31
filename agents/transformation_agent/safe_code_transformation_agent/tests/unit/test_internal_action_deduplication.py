from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.analysis.local_refactor_detector import LocalRefactorDetector
from sctva.contracts import RefactoringAction


def _action(
    *,
    action_type: str = "extract_method",
    source_refactoring: str = "Extract Method",
    source_file: str = "ELECTION.H",
    method: str = "deleteIllegalVote",
) -> RefactoringAction:
    return RefactoringAction(
        action_type=action_type,
        source_refactoring=source_refactoring,
        parameters={"source_file": source_file, "source_method": method},
    )


def test_internal_extract_method_is_skipped_when_rdp_already_targets_routine():
    agent = SafeCodeTransformationValidationAgent()
    planned = _action()
    detected = _action(source_refactoring="SCTVA Internal Analysis")

    assert agent._deduplicate_internal_actions(
        file_name="src/ELECTION.H",
        existing_actions=[planned],
        detected_actions=[detected],
    ) == []


def test_local_c_detector_skips_rdp_extract_method_despite_stale_line_range():
    source = "void deleteIllegalVote(void) {\n" + "    audit_vote();\n" * 35 + "}\n"
    planned = RefactoringAction(
        action_type="extract_method",
        source_refactoring="Extract Method",
        parameters={
            "source_file": "ELECTION.H",
            "source_method": "deleteIllegalVote",
            "start_line": 4,
            "end_line": 12,
        },
    )

    detected = LocalRefactorDetector().detect(
        language="c",
        file_name="ELECTION.H",
        source_code=source,
        existing_actions=[planned],
    )

    assert not any(action.action_type == "extract_method" for action in detected)


def test_duplicate_internal_extract_method_actions_are_collapsed():
    agent = SafeCodeTransformationValidationAgent()
    first = _action(source_refactoring="SCTVA Internal Analysis")
    second = _action(source_refactoring="SCTVA Internal Analysis")

    retained = agent._deduplicate_internal_actions(
        file_name="ELECTION.H",
        existing_actions=[],
        detected_actions=[first, second],
    )

    assert retained == [first]


def test_same_routine_in_different_files_is_not_deduplicated():
    agent = SafeCodeTransformationValidationAgent()
    planned = _action(source_file="elections/ELECTION.H")
    detected = _action(
        source_file="archive/ELECTION.H",
        source_refactoring="SCTVA Internal Analysis",
    )

    retained = agent._deduplicate_internal_actions(
        file_name="archive/ELECTION.H",
        existing_actions=[planned],
        detected_actions=[detected],
    )

    assert retained == [detected]


def test_extract_function_and_extract_method_share_a_deduplication_family():
    agent = SafeCodeTransformationValidationAgent()
    planned = _action(source_refactoring="Extract Function")
    detected = _action(source_refactoring="SCTVA Internal Analysis")

    assert agent._deduplicate_internal_actions(
        file_name="ELECTION.H",
        existing_actions=[planned],
        detected_actions=[detected],
    ) == []


def test_different_refactoring_family_or_target_is_retained():
    agent = SafeCodeTransformationValidationAgent()
    planned = _action()
    different_method = _action(
        source_refactoring="SCTVA Internal Analysis",
        method="loadElectionInfoFromFile",
    )
    different_family = _action(
        action_type="remove_dead_code",
        source_refactoring="Remove Dead Code",
        method="deleteIllegalVote",
    )

    retained = agent._deduplicate_internal_actions(
        file_name="ELECTION.H",
        existing_actions=[planned],
        detected_actions=[different_method, different_family],
    )

    assert retained == [different_method, different_family]


def test_local_c_detector_never_classifies_nested_if_as_a_function():
    nested_body = "\n".join(
        f"            total += {value};" for value in range(36)
    )
    source = f'''int main(void) {{
    int total = 0;
    if (total == 0) {{
        if (total < 10) {{
{nested_body}
        }}
    }}
    return total;
}}
'''

    detected = LocalRefactorDetector().detect(
        language="c",
        file_name="Server.c",
        source_code=source,
        existing_actions=[],
    )
    methods = [
        action.parameters.get("method")
        for action in detected
        if action.action_type == "extract_method"
    ]

    assert methods == ["main"]
    assert "if" not in methods
