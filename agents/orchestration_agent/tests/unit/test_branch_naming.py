"""
unit/test_branch_naming.py
--------------------------
Branch names for "Push to GitHub": generated from the run id, validated by the
naming rules and by `git check-ref-format --branch`.
"""

import re

import pytest

from services import branch_naming as bn
from services.git_service import run_git


class TestGeneratedName:
    @pytest.mark.parametrize("run_id, expected", [
        ("wf_3f9a1c", "refactoring/diwo-wf-3f9a1c"),
        ("WF_ABC", "refactoring/diwo-wf-abc"),
        ("  run 42 ", "refactoring/diwo-run-42"),
        ("", "refactoring/diwo-run"),
        ("../../x", "refactoring/diwo-x"),
    ])
    def test_from_run_id(self, run_id, expected):
        assert bn.generated_branch_name(run_id) == expected

    def test_later_attempts_take_a_suffix(self):
        assert bn.generated_branch_name("wf_a", 3) == "refactoring/diwo-wf-a-3"

    @pytest.mark.parametrize("run_id", ["wf_3f9a1c", "Weird ID!!", "a" * 200, "ü-ñ", ""])
    def test_only_lowercase_digits_hyphens_and_slashes(self, run_id):
        name = bn.generated_branch_name(run_id, 2)
        assert re.fullmatch(r"[a-z0-9/-]+", name)
        assert bn.validate_branch_name(name, run_git) == []


class TestValidation:
    @pytest.mark.parametrize("name", [
        "refactoring/diwo-wf-1",
        "feature/Login_Page",
        "fix-123",
    ])
    def test_valid(self, name):
        assert bn.validate_branch_name(name, run_git) == []

    @pytest.mark.parametrize("name, fragment", [
        ("", "required"),
        ("has space", "whitespace"),
        ("tab\there", "whitespace"),
        ("a//b", "consecutive slashes"),
        ("a..b", "two consecutive dots"),
        ("trailing/", "end with a slash"),
        ("trailing.", "end with a dot"),
        ("/leading", "start with a slash"),
        ("-leading", "start with a hyphen"),
        ("a~b", "does not allow"),
        ("a^b", "does not allow"),
        ("a:b", "does not allow"),
        ("a?b", "does not allow"),
        ("a*b", "does not allow"),
        ("a[b", "does not allow"),
        ("a\\b", "does not allow"),
        ("a@{b", "@{"),
        ("x.lock", ".lock"),
        ("a/.hidden", "start with a dot"),
        ("@", "@"),
    ])
    def test_invalid_with_a_reason(self, name, fragment):
        errors = bn.validate_branch_name(name, run_git)
        assert errors
        assert any(fragment in e for e in errors), errors

    def test_git_has_the_final_word(self):
        """A name the rules pass but git refuses is still rejected."""
        def refusing_git(args, **kwargs):
            return False, "", "fatal: not a valid branch name"
        assert bn.validate_branch_name("looks/fine", refusing_git)


class TestNextFreeName:
    def test_skips_taken_names(self):
        taken = {"refactoring/diwo-wf-a", "refactoring/diwo-wf-a-2"}
        assert bn.next_free_branch_name("wf_a", lambda n: n in taken) == "refactoring/diwo-wf-a-3"

    def test_unknown_counts_as_free(self):
        assert bn.next_free_branch_name("wf_a", lambda n: None) == "refactoring/diwo-wf-a"
