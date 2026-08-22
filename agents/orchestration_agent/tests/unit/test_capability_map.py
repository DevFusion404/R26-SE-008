"""
unit/test_capability_map.py
---------------------------
The Stage 1 feasibility gate: will selecting this smell change any code?

The gate is DERIVED by running the real sctva_mapper.map_step rather than
kept as a second lookup table, so it cannot drift from the mapper. These tests
pin the answers, including the four smell types whose refactoring name has no
branch in map_step at all — a green "auto-fixable" chip on one of those would
be worse than no chip.
"""

import pytest

from domain.capability_map import (
    ADVISORY, EXECUTABLE, UNKNOWN, classify, classify_all, known_smell_types,
    refactoring_action,
)

#: The smell types the CUQA agent actually emits, read out of its detectors.
#: `GodClass` is deliberately absent: REFACTORING_MAP carries a "God Class"
#: entry, but no CUQA detector produces it, so it can never reach Stage 1.
CUQA_SMELL_TYPES = [
    "BareExcept", "Comments", "DataClumps", "DeadCode", "DeepNesting",
    "DuplicateCode", "FeatureEnvy", "GlobalVariable", "InappropriateIntimacy",
    "LargeClass", "LargeHeaderFile", "LazyClass", "LongFunction", "LongMethod",
    "MagicNumber", "MessageChains", "PrimitiveObsession", "SpeculativeGenerality",
    "SwitchStatements", "TooManyParameters", "UnsafeFunctionUsage",
]


@pytest.mark.unit
class TestExecutableSmells:
    @pytest.mark.parametrize("smell_type,action", [
        ("LongMethod", "extract_method"),
        ("LongFunction", "extract_method"),
        ("DuplicateCode", "extract_method"),
        ("DeadCode", "remove_dead_code"),
        ("MagicNumber", "extract_constant"),
        ("Comments", "rename_symbol"),
    ])
    def test_maps_to_the_action_sctva_will_run(self, smell_type, action):
        result = classify(smell_type)
        assert result["status"] == EXECUTABLE
        assert result["action_type"] == action

    def test_an_executable_result_names_the_parameters_a_step_must_carry(self):
        # EXECUTABLE means "can map", not "will map": a concrete plan step
        # still has to supply the action's arguments.
        result = classify("LongMethod")
        assert result["required_parameters"]


@pytest.mark.unit
class TestAdvisorySmells:
    @pytest.mark.parametrize("smell_type", [
        "LargeClass", "LargeHeaderFile", "DataClumps",       # Extract Class
        "FeatureEnvy", "InappropriateIntimacy",              # Move Method
        "SwitchStatements",                                  # Polymorphism
        "TooManyParameters",                                 # Parameter Object
        "MessageChains",                                     # Hide Delegate
        "PrimitiveObsession",                                # Replace Data Value
        "LazyClass",                                         # Inline Class
        "SpeculativeGenerality",                             # Collapse Hierarchy
    ])
    def test_structurally_unsupported_refactorings_are_advisory(self, smell_type):
        result = classify(smell_type)
        assert result["status"] == ADVISORY
        assert result["action_type"] is None

    def test_an_advisory_result_explains_itself_in_plain_language(self):
        assert "call site" in classify("LargeClass")["reason"]


@pytest.mark.unit
class TestNameGapsBetweenTheTables:
    """The finding: SCTVA can do it, but DIWO can never ask.

    SUPPORTED_ACTIONS includes replace_unsafe_function and
    encapsulate_variable, yet REFACTORING_MAP names those refactorings
    differently from the branches in map_step, so a plan step for them is sent
    as a no-op. The gate must report that honestly rather than promise a fix.
    """

    @pytest.mark.parametrize("smell_type,refactoring", [
        ("UnsafeFunctionUsage", "Replace Unsafe Call with Safe Variant"),
        ("GlobalVariable", "Encapsulate Field"),
        ("DeepNesting", "Replace Nested Conditional with Guard Clauses"),
        ("BareExcept", "Replace Bare Except with Specific Exception"),
    ])
    def test_is_advisory_and_flagged_as_a_gap(self, smell_type, refactoring):
        result = classify(smell_type)
        assert result["status"] == ADVISORY
        assert result["refactoring"] == refactoring
        assert result.get("gap") is True

    def test_the_gap_reason_points_at_the_mapper(self, ):
        assert "action mapper" in classify("UnsafeFunctionUsage")["reason"]

    def test_a_deliberate_exclusion_is_not_flagged_as_a_gap(self):
        # Extract Class is on UNSUPPORTED_REFACTORINGS on purpose; that is a
        # decision, not an oversight, so it carries no `gap` marker.
        assert classify("LargeClass").get("gap") is not True


@pytest.mark.unit
class TestLiveCapabilityProbe:
    def test_an_action_missing_from_the_build_downgrades_to_advisory(self):
        result = classify("LongMethod", supported_actions={"rename_symbol"})
        assert result["status"] == ADVISORY
        assert result["action_type"] == "extract_method"
        assert "does not expose" in result["reason"]

    def test_an_action_present_in_the_build_stays_executable(self):
        assert classify("LongMethod",
                        supported_actions={"extract_method"})["status"] == EXECUTABLE

    def test_no_probe_result_falls_back_to_the_static_tables(self):
        # SCTVA being down must not make everything look unfixable.
        assert classify("LongMethod", supported_actions=None)["status"] == EXECUTABLE


@pytest.mark.unit
class TestCoverageAndUnknowns:
    def test_an_unmapped_smell_type_is_unknown_not_silently_executable(self):
        result = classify("NotARealSmellType")
        assert result["status"] == UNKNOWN
        assert result["action_type"] is None

    @pytest.mark.parametrize("smell_type", CUQA_SMELL_TYPES)
    def test_every_smell_type_cuqa_emits_is_classifiable(self, smell_type):
        # A new CUQA detector shipped without a REFACTORING_MAP entry would
        # otherwise reach Stage 1 as "unknown" and be treated as advisory
        # without anyone noticing.
        assert classify(smell_type)["status"] != UNKNOWN

    def test_no_mapped_type_classifies_as_unknown(self):
        assert not [t for t, c in classify_all().items() if c["status"] == UNKNOWN]

    def test_known_smell_types_is_sorted_and_non_empty(self):
        types = known_smell_types()
        assert types == sorted(types)
        assert len(types) > 30

    def test_classification_is_stable_across_calls(self):
        # refactoring_action() memoises; a cached answer must not differ.
        assert classify("LongMethod") == classify("LongMethod")


@pytest.mark.unit
class TestRefactoringActionProbe:
    def test_returns_the_action_for_a_mappable_refactoring(self):
        assert refactoring_action("Extract Method") == "extract_method"

    def test_returns_none_for_one_with_no_branch(self):
        assert refactoring_action("Replace Unsafe Call with Safe Variant") is None

    def test_is_case_insensitive(self):
        assert refactoring_action("EXTRACT METHOD") == "extract_method"

    def test_an_empty_name_is_not_an_action(self):
        assert refactoring_action("") is None
        assert refactoring_action(None) is None
