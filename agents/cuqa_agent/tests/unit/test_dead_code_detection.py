"""
unit/test_dead_code_detection.py
---------------------------------
Unit tests for the three new dead-code-related detectors added to report_generator.py:

1. build_repo_name_index()  + cross-file DeadCode suppression
2. UnreachableCode  (_detect_unreachable_code)
3. UnusedVariable   (_detect_unused_variables)
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import (
    generate_file_report,
    generate_repo_report,
    build_repo_name_index,
)


def _smells_of(report: dict, smell_type: str) -> list[dict]:
    return [s for s in report.get("code_smells", []) if s["type"] == smell_type]


# ---------------------------------------------------------------------------
# 1.  build_repo_name_index  &  cross-file DeadCode suppression
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCrossFileDeadCode:

    def test_index_collects_load_context_names(self):
        src_b = "from fileA import my_func\nmy_func()"
        index = build_repo_name_index([("fileB.py", src_b)])
        assert "my_func" in index

    def test_index_collects_attribute_accesses(self):
        src = "import utils\nresult = utils.helper()"
        index = build_repo_name_index([("main.py", src)])
        assert "helper" in index

    def test_index_collects_importfrom_names(self):
        src = "from helpers import format_data, validate"
        index = build_repo_name_index([("app.py", src)])
        assert "format_data" in index
        assert "validate" in index

    def test_index_skips_syntax_errors(self):
        index = build_repo_name_index([("bad.py", "def (broken")])
        assert isinstance(index, set)

    def test_index_collects_aliased_imports(self):
        src = "import numpy as np\nnp.array([1, 2, 3])"
        index = build_repo_name_index([("calc.py", src)])
        assert "numpy" in index
        assert "np" in index

    def test_function_used_in_other_file_not_flagged_as_dead(self):
        src_a = "def helper():\n    return 42\n"
        src_b = "from fileA import helper\nresult = helper()\n"
        sources = [("fileA.py", src_a), ("fileB.py", src_b)]
        repo_index = build_repo_name_index(sources)
        report_a = generate_file_report(src_a, "fileA.py", repo_ref_index=repo_index)
        dead = _smells_of(report_a, "DeadCode")
        assert all(s["entity"] != "helper" for s in dead), (
            "helper is imported in fileB.py - must NOT be flagged as DeadCode"
        )

    def test_genuinely_unreferenced_function_still_flagged(self):
        src_a = "def orphan():\n    return 0\n\ndef used():\n    return 1\n"
        src_b = "from fileA import used\nused()\n"
        sources = [("fileA.py", src_a), ("fileB.py", src_b)]
        repo_index = build_repo_name_index(sources)
        report_a = generate_file_report(src_a, "fileA.py", repo_ref_index=repo_index)
        dead = _smells_of(report_a, "DeadCode")
        assert any(s["entity"] == "orphan" for s in dead)
        assert all(s["entity"] != "used" for s in dead)

    def test_generate_repo_report_with_sources_removes_false_positives(self):
        src_a = "def exported_func():\n    return True\n"
        src_b = "from fileA import exported_func\nexported_func()\n"
        report_a = generate_file_report(src_a, "fileA.py")
        report_b = generate_file_report(src_b, "fileB.py")
        sources = [("fileA.py", src_a), ("fileB.py", src_b)]
        repo = generate_repo_report([report_a, report_b], sources=sources)
        file_a_result = next(r for r in repo["files"] if r.get("file") == "fileA.py")
        dead = _smells_of(file_a_result, "DeadCode")
        assert all(s["entity"] != "exported_func" for s in dead)

    def test_single_file_mode_flags_unused_function(self):
        src = "def lonely():\n    return 0\n"
        report = generate_file_report(src, "single.py")
        dead = _smells_of(report, "DeadCode")
        assert any(s["entity"] == "lonely" for s in dead)

    def test_dunder_functions_never_flagged(self):
        src = (
            "class MyClass:\n"
            "    def __init__(self):\n"
            "        self.x = 0\n"
            "    def __str__(self):\n"
            "        return str(self.x)\n"
        )
        report = generate_file_report(src, "model.py")
        dead = _smells_of(report, "DeadCode")
        assert not any(s["entity"].startswith("__") for s in dead)

    def test_test_prefixed_functions_never_flagged(self):
        src = "def test_something():\n    assert True\n"
        report = generate_file_report(src, "test_example.py")
        dead = _smells_of(report, "DeadCode")
        assert not any(s["entity"] == "test_something" for s in dead)

    def test_clean_exported_module_zero_dead_code_smells(self):
        helpers_src = "def compute(x):\n    return x * 2\n"
        main_src = "from helpers import compute\nresult = compute(5)\n"
        sources = [("helpers.py", helpers_src), ("main.py", main_src)]
        repo_index = build_repo_name_index(sources)
        report_helpers = generate_file_report(
            helpers_src, "helpers.py", repo_ref_index=repo_index
        )
        dead = _smells_of(report_helpers, "DeadCode")
        assert dead == [], f"compute() is imported in main.py - expected zero DeadCode but got: {dead}"


# ---------------------------------------------------------------------------
# 2.  UnreachableCode
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestUnreachableCode:

    def test_statement_after_return_is_flagged(self):
        src = "def foo():\n    return 1\n    x = 2\n"
        report = generate_file_report(src, "test.py")
        smells = _smells_of(report, "UnreachableCode")
        assert len(smells) >= 1
        assert smells[0]["entity"] == "foo"

    def test_statement_after_raise_is_flagged(self):
        src = "def bar():\n    raise ValueError('oops')\n    return 0\n"
        report = generate_file_report(src, "test.py")
        assert len(_smells_of(report, "UnreachableCode")) >= 1

    def test_multiple_unreachable_statements_all_flagged(self):
        src = "def multi():\n    return True\n    x = 1\n    y = 2\n    z = 3\n"
        report = generate_file_report(src, "test.py")
        assert len(_smells_of(report, "UnreachableCode")) >= 3

    def test_normal_code_before_return_not_flagged(self):
        src = "def normal():\n    x = 1\n    y = x + 1\n    return y\n"
        report = generate_file_report(src, "test.py")
        assert _smells_of(report, "UnreachableCode") == []

    def test_return_in_if_branch_does_not_flag_else(self):
        src = (
            "def branched(x):\n"
            "    if x > 0:\n"
            "        return x\n"
            "    else:\n"
            "        return -x\n"
        )
        report = generate_file_report(src, "test.py")
        assert _smells_of(report, "UnreachableCode") == []

    def test_smell_disappears_after_fixing(self):
        # Dirty: unreachable code present
        src_dirty = "def fn():\n    return 1\n    x = 2\n"
        dirty = generate_file_report(src_dirty, "test.py")
        assert len(_smells_of(dirty, "UnreachableCode")) >= 1

        # Clean: dead statement removed
        src_clean = "def fn():\n    return 1\n"
        clean = generate_file_report(src_clean, "test.py")
        assert _smells_of(clean, "UnreachableCode") == []

    def test_severity_is_low(self):
        src = "def fn():\n    return 0\n    print('dead')\n"
        report = generate_file_report(src, "test.py")
        smells = _smells_of(report, "UnreachableCode")
        assert all(s["severity"] == "low" for s in smells)

    def test_category_is_dispensables(self):
        src = "def fn():\n    return 0\n    print('dead')\n"
        report = generate_file_report(src, "test.py")
        smells = _smells_of(report, "UnreachableCode")
        assert all(s.get("category") == "Dispensables" for s in smells)


# ---------------------------------------------------------------------------
# 3.  UnusedVariable
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestUnusedVariable:

    def test_assigned_never_read_is_flagged(self):
        src = "def fn():\n    unused = 42\n    return 0\n"
        report = generate_file_report(src, "test.py")
        smells = _smells_of(report, "UnusedVariable")
        assert any(s["variable_name"] == "unused" for s in smells)

    def test_assigned_and_read_not_flagged(self):
        src = "def fn():\n    x = 10\n    return x\n"
        report = generate_file_report(src, "test.py")
        assert not any(s.get("variable_name") == "x" for s in _smells_of(report, "UnusedVariable"))

    def test_parameter_not_flagged(self):
        src = "def fn(param):\n    return 0\n"
        report = generate_file_report(src, "test.py")
        assert not any(s.get("variable_name") == "param" for s in _smells_of(report, "UnusedVariable"))

    def test_underscore_throwaway_not_flagged(self):
        src = "def fn():\n    _ = some_call()\n    return 0\n"
        report = generate_file_report(src, "test.py")
        assert not any(s.get("variable_name") == "_" for s in _smells_of(report, "UnusedVariable"))

    def test_augmented_assignment_not_flagged(self):
        src = "def counter():\n    x = 0\n    x += 1\n    return x\n"
        report = generate_file_report(src, "test.py")
        assert not any(s.get("variable_name") == "x" for s in _smells_of(report, "UnusedVariable"))

    def test_multiple_unused_vars_all_flagged(self):
        src = "def fn():\n    a = 1\n    b = 2\n    c = 3\n    return 0\n"
        report = generate_file_report(src, "test.py")
        names = {s["variable_name"] for s in _smells_of(report, "UnusedVariable")}
        assert {"a", "b", "c"}.issubset(names)

    def test_clean_function_no_unused_vars(self):
        src = (
            "def fn(items):\n"
            "    total = 0\n"
            "    for item in items:\n"
            "        total += item\n"
            "    return total\n"
        )
        report = generate_file_report(src, "test.py")
        assert _smells_of(report, "UnusedVariable") == []

    def test_smell_disappears_after_fixing(self):
        src_dirty = "def fn():\n    dead = 99\n    return 0\n"
        dirty = generate_file_report(src_dirty, "test.py")
        assert len(_smells_of(dirty, "UnusedVariable")) >= 1

        src_clean = "def fn():\n    return 0\n"
        clean = generate_file_report(src_clean, "test.py")
        assert _smells_of(clean, "UnusedVariable") == []

    def test_severity_is_low(self):
        src = "def fn():\n    x = 5\n    return 0\n"
        report = generate_file_report(src, "test.py")
        assert all(s["severity"] == "low" for s in _smells_of(report, "UnusedVariable"))

    def test_category_is_dispensables(self):
        src = "def fn():\n    x = 5\n    return 0\n"
        report = generate_file_report(src, "test.py")
        assert all(s.get("category") == "Dispensables" for s in _smells_of(report, "UnusedVariable"))

    def test_annotated_assignment_unused_flagged(self):
        src = "def fn():\n    x: int = 5\n    return 0\n"
        report = generate_file_report(src, "test.py")
        assert any(s.get("variable_name") == "x" for s in _smells_of(report, "UnusedVariable"))

    def test_bare_annotation_not_flagged(self):
        src = "def fn():\n    x: int\n    return 0\n"
        report = generate_file_report(src, "test.py")
        assert not any(s.get("variable_name") == "x" for s in _smells_of(report, "UnusedVariable"))
