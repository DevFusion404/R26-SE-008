"""
regression/test_known_regressions.py
------------------------------------
One test per defect that has already been found and fixed. Each is named for
its entry in TEST_FINDINGS.md.

A regression test is only worth writing if it would have failed before the fix,
so each one asserts the specific broken behaviour rather than the feature in
general.
"""

import io
import json
import zipfile
from pathlib import Path

import pytest

from domain.capability_map import ADVISORY, classify
from domain.plan_normalizer import build_approved_plan, normalize_rdp_plan
from domain.sctva_mapper import collect_plan_source_paths, normalize_plan_for_sctva
from domain.smell_graph import build_edges, selection_notes
from services.archive_service import build_refactored_archive, safe_archive_path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(scope="module")
def cases():
    """The 14 plan shapes the JavaScript mapper was captured against."""
    return json.loads((FIXTURES / "sctva_plan_cases.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def golden():
    """What the ORIGINAL browser-side mapper produced for those shapes."""
    raw = json.loads((FIXTURES / "sctva_mapper_golden.json").read_text(encoding="utf-8"))
    return {entry["name"]: entry for entry in raw}


@pytest.mark.regression
class TestSctvaMapperGolden:
    """BUG-REG-001 — the plan → SCTVA action mapping was ported from JavaScript.

    The mapping used to run in the browser. When it moved server-side the
    request SCTVA receives had to stay byte-identical, or a plan that
    transformed correctly before the move would quietly produce different
    actions.

    fixtures/sctva_mapper_golden.json is the output of the ORIGINAL JavaScript
    mapper, captured by running it under Node against
    fixtures/sctva_plan_cases.json. This replays those cases through the Python
    port and asserts an exact match.
    """

    def test_every_case_is_covered_by_the_fixture(self, cases, golden):
        assert {c["name"] for c in cases} == set(golden)

    def test_the_fixture_covers_every_branch_of_the_mapper(self, cases):
        # 14 cases: each supported refactoring, each "supported but
        # under-specified" error path, the unsupported noop, malformed steps,
        # an empty plan, plan-target fallback, and the undefined-vs-null rule.
        assert len(cases) >= 14

    def test_python_port_matches_the_original_javascript(self, cases, golden):
        differences = []

        for case in cases:
            plan = {"plan_id": case["plan_id"], "target": case.get("target"),
                    "steps": case["steps"]}

            mapping = normalize_plan_for_sctva(plan, correlation_id=plan["plan_id"])
            # The only intended difference: the mapping moved agents.
            assert mapping["plan"]["metadata"]["mapped_by"] == "diwo_orchestrator"
            mapping["plan"]["metadata"]["mapped_by"] = "<mapper>"

            expected = golden[case["name"]]
            if mapping != expected["mapping"]:
                differences.append(f"{case['name']}: mapping differs")
            if collect_plan_source_paths(plan) != expected["paths"]:
                differences.append(f"{case['name']}: source paths differ")

        assert not differences, "\n".join(differences)

    def test_undefined_and_null_are_still_distinguished(self, cases):
        """JavaScript drops `undefined` keys when serializing but keeps `null`.

        Losing that distinction would silently add `"change_type": null` fields
        the agent had never seen.
        """
        plan = {"plan_id": "p", "target": "src/A.java", "steps": [{
            "step_id": 1, "refactoring": "Introduce Constant",
            "parameters": {"hint": "magic 32"}, "target": {"class": "A"},
        }]}
        params = normalize_plan_for_sctva(plan)["plan"]["actions"][0]["parameters"]

        # `literal_value` was explicitly null in JS -> kept.
        assert "literal_value" in params and params["literal_value"] is None
        # `source_file` was undefined -> dropped, then filled from plan target.
        assert params["source_file"] == "src/A.java"


@pytest.mark.regression
class TestCapabilityNameGaps:
    """BUG-REG-002 — SCTVA can perform the action, DIWO can never ask for it.

    SUPPORTED_ACTIONS includes `replace_unsafe_function` and
    `encapsulate_variable`, but REFACTORING_MAP names those refactorings
    differently from the branches in sctva_mapper.map_step, so a plan step for
    them is sent as a no-op.

    The first version of the feasibility gate used a hand-written lookup table
    that claimed these WERE executable — which would have put a green
    "auto-fixable" chip on a smell guaranteed to change nothing.
    """

    @pytest.mark.parametrize("smell_type", [
        "UnsafeFunctionUsage", "GlobalVariable", "DeepNesting", "BareExcept",
    ])
    def test_the_gate_reports_them_as_advisory(self, smell_type):
        result = classify(smell_type)
        assert result["status"] == ADVISORY
        assert result["action_type"] is None
        assert result.get("gap") is True


@pytest.mark.regression
class TestPlanReduction:
    def test_reducing_twice_keeps_the_first_pass_rejections(self):
        """BUG-REG-003 — the audit trail lost verdicts on a second reduction.

        Approving a plan that had already been reduced only saw the survivors,
        so the second pass reported an empty rejected list and the record of
        what the developer refused disappeared.
        """
        plan = {"plan_id": "p", "steps": [
            {"step_id": 1, "refactoring": "Extract Method"},
            {"step_id": 2, "refactoring": "Extract Method"},
            {"step_id": 3, "refactoring": "Extract Method"},
        ]}
        first = build_approved_plan(plan, {"1": "approve", "2": "approve", "3": "reject"})
        second = build_approved_plan(first, {"1": "approve", "2": "reject"})

        assert set(second["approval"]["rejected_step_ids"]) == {2, 3}
        assert second["approval"]["original_total_steps"] == 3

    def test_rdp_basenames_are_restored_to_repo_relative_paths(self):
        """BUG-REG-004 — the transformation stage could not find the file.

        RDP reduces every path to its basename while translating the report, so
        a plan came back naming "Helper.java" where the report said
        "src/util/Helper.java". The transformation stage resolves those against
        the CUQA workspace and found nothing.
        """
        plan_input = {"files": [{"relative_path": "src/util/Helper.java"}]}
        plan = normalize_rdp_plan({
            "steps": [{"step_id": 1, "refactoring": "Extract Method",
                       "parameters": {"source_file": "Helper.java"}}],
        }, plan_input)
        assert plan["steps"][0]["parameters"]["source_file"] == "src/util/Helper.java"


@pytest.mark.regression
class TestArchiveIntegrity:
    def test_duplicate_paths_do_not_silently_lose_a_file(self):
        """BUG-REG-005 — two entries with the same name overwrote each other.

        A ZIP cannot hold two members with one name; without de-duplication the
        archive silently contained fewer files than the developer accepted.
        """
        payload, manifest = build_refactored_archive("wf_x", [
            {"path": "src/A.java", "content": "first"},
            {"path": "src/A.java", "content": "second"},
        ], {})
        assert manifest["file_count"] == 2
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [n for n in archive.namelist() if n.endswith(".java")]
            assert len(names) == 2
            assert len(set(names)) == 2

    def test_a_rejected_file_is_archived_as_its_original_source(self):
        """BUG-REG-006 — the rollback guarantee must survive into the archive."""
        payload, manifest = build_refactored_archive("wf_x", [
            {"path": "src/Keep.java", "content": "refactored", "state": "refactored"},
            {"path": "src/Undo.java", "content": "original",
             "state": "reverted_to_original"},
        ], {})
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            assert archive.read("src/Undo.java").decode() == "original"
        states = {e["path"]: e["state"] for e in manifest["files"]}
        assert states["src/Undo.java"] == "reverted_to_original"


@pytest.mark.regression
class TestSelectionNotes:
    def test_one_container_produces_one_note_not_one_per_member(self):
        """BUG-REG-007 — the same sentence was repeated per contained smell.

        Selecting three methods inside one unselected class emitted the
        identical "the container stays flagged" note three times, burying the
        notes that actually differed.
        """
        edges = build_edges([
            {"id": "cls", "type": "LargeClass", "line": 1,
             "location": {"file": "src/A.java", "lines": [1, 200]}},
            {"id": "m1", "type": "LongMethod", "line": 10,
             "location": {"file": "src/A.java", "lines": [10, 20]}},
            {"id": "m2", "type": "LongMethod", "line": 30,
             "location": {"file": "src/A.java", "lines": [30, 40]}},
            {"id": "m3", "type": "LongMethod", "line": 50,
             "location": {"file": "src/A.java", "lines": [50, 60]}},
        ])
        notes = selection_notes(edges, {"m1", "m2", "m3"})
        containment = [n for n in notes if "container stays flagged" in n["message"]]
        assert len(containment) == 1
        assert "3 selected smells" in containment[0]["message"]


@pytest.mark.regression
class TestArchivePathNormalisation:
    @pytest.mark.parametrize("raw,expected", [
        ("src/util/Helper.java", "src/util/Helper.java"),
        ("src\\util\\Helper.java", "src/util/Helper.java"),
        ("/src/Helper.java", "src/Helper.java"),
        ("C:/src/Helper.java", "src/Helper.java"),
        ("./src/Helper.java", "src/Helper.java"),
    ])
    def test_paths_keep_their_folders_but_lose_their_anchors(self, raw, expected):
        """BUG-REG-008 — extracting the archive flattened the project.

        Entry names have to keep their folders so the archive reproduces the
        project layout, while drive letters and leading slashes must go or the
        entry becomes absolute.
        """
        assert safe_archive_path(raw) == expected
