"""
unit/test_cuqa_normalizer.py
----------------------------
Agent 1 ingestion: a CUQA quality report becomes the DIWO smell model, and a
developer's selection narrows it back down for the RDP agent.

The filter is the most important function in the backend. If it leaks, the RDP
agent plans refactorings for smells the developer explicitly rejected.
"""

import pytest

from domain.cuqa_normalizer import (
    build_report_from_smells, cuqa_report_to_smells, derive_target_name,
    detect_primary_language, filter_cuqa_report, normalize_cuqa_report,
    summarize_files,
)


@pytest.mark.unit
class TestNormalizeCuqaReport:
    def test_accepts_the_raw_envelope(self, cuqa_report):
        report = normalize_cuqa_report({"type": "repository", "report": cuqa_report})
        assert report["report_type"] == "repository"
        assert len(report["files"]) == 2
        assert report["source"] == "cuqa"

    def test_accepts_a_bare_repository_report(self, cuqa_report):
        report = normalize_cuqa_report(cuqa_report)
        assert len(report["files"]) == 2

    def test_wraps_a_single_file_report_into_the_repository_shape(self):
        report = normalize_cuqa_report({
            "type": "file",
            "report": {"file": "Order.java", "relative_path": "src/Order.java",
                       "language": "java", "metrics": {"lines_of_code": 10},
                       "code_smells": [], "quality_score": 90},
        })
        assert len(report["files"]) == 1
        assert report["report_type"] == "file"

    def test_guarantees_every_file_field_the_ui_reads(self, cuqa_report):
        # The frontend renders these without defensive checks, so the
        # normalizer has to promise them.
        for entry in normalize_cuqa_report(cuqa_report)["files"]:
            for field in ("relative_path", "language", "metrics",
                          "code_smells", "smell_summary", "quality_score"):
                assert field in entry, f"{field} missing from normalized file entry"

    def test_backslash_paths_are_normalized_to_forward_slashes(self):
        report = normalize_cuqa_report({"files": [
            {"relative_path": "src\\util\\Helper.java", "code_smells": []},
        ]})
        assert report["files"][0]["relative_path"] == "src/util/Helper.java"

    @pytest.mark.parametrize("raw,expected", [
        ("HIGH", "high"), ("Medium", "medium"), ("low", "low"),
        ("critical", "low"), ("", "low"), (None, "low"), (42, "low"),
    ])
    def test_unknown_severities_coerce_to_low_rather_than_being_dropped(self, raw, expected):
        report = normalize_cuqa_report({"files": [
            {"relative_path": "a.py", "code_smells": [{"type": "X", "severity": raw}]},
        ]})
        assert report["files"][0]["code_smells"][0]["severity"] == expected

    def test_a_non_dict_payload_is_rejected(self):
        with pytest.raises(ValueError):
            # Deliberately the wrong type - rejecting it is the contract.
            normalize_cuqa_report(["not", "a", "report"])  # type: ignore[arg-type]

    def test_summary_is_rebuilt_when_the_agent_omits_it(self):
        report = normalize_cuqa_report({"files": [
            {"relative_path": "a.py", "quality_score": 80,
             "metrics": {"lines_of_code": 10},
             "code_smells": [{"type": "X", "severity": "high"}]},
        ]})
        assert report["summary"]["total_code_smells"] == 1
        assert report["summary"]["smell_severity"]["high"] == 1


@pytest.mark.unit
class TestCuqaReportToSmells:
    def test_id_format_is_path_line_index(self, cuqa_report):
        smells = cuqa_report_to_smells(cuqa_report)
        assert smells[0]["id"] == "src/Order.java:10:0"
        assert smells[1]["id"] == "src/Order.java:60:1"
        assert smells[2]["id"] == "src/util/Helper.java:5:0"

    def test_ids_are_unique(self, cuqa_report):
        smells = cuqa_report_to_smells(cuqa_report)
        assert len({s["id"] for s in smells}) == len(smells)

    def test_class_level_smells_take_the_entity_as_the_class(self, cuqa_report):
        large_class = cuqa_report_to_smells(cuqa_report)[1]
        assert large_class["location"]["class"] == "Order"
        assert large_class["location"]["method"] is None

    def test_method_level_smells_take_the_entity_as_the_method(self, cuqa_report):
        long_method = cuqa_report_to_smells(cuqa_report)[0]
        assert long_method["location"]["method"] == "calculateTotal"

    def test_line_range_uses_start_and_end_when_present(self, cuqa_report):
        assert cuqa_report_to_smells(cuqa_report)[0]["location"]["lines"] == [10, 130]

    def test_per_smell_metrics_are_carried_through(self, cuqa_report):
        assert cuqa_report_to_smells(cuqa_report)[0]["metrics"]["cyclomatic_complexity"] == 32

    def test_a_report_with_no_files_yields_no_smells(self):
        assert cuqa_report_to_smells({"files": []}) == []
        assert cuqa_report_to_smells({}) == []


@pytest.mark.unit
class TestFilterCuqaReport:
    """The Stage 1 -> Stage 2 hand-off. A leak here is a correctness bug."""

    def test_only_the_selected_smell_survives(self, cuqa_report):
        filtered = filter_cuqa_report(cuqa_report, ["src/Order.java:10:0"])
        kept = [s for f in filtered["files"] for s in f["code_smells"]]
        assert len(kept) == 1
        assert kept[0]["type"] == "LongMethod"

    def test_every_analysed_file_is_still_present(self, cuqa_report):
        # The developer has to be able to see what was excluded, so files are
        # not dropped here - only later, when the RDP payload is built.
        filtered = filter_cuqa_report(cuqa_report, ["src/Order.java:10:0"])
        assert len(filtered["files"]) == 2

    def test_surviving_smells_keep_all_their_own_fields(self, cuqa_report):
        filtered = filter_cuqa_report(cuqa_report, ["src/Order.java:10:0"])
        smell = filtered["files"][0]["code_smells"][0]
        assert smell["entity"] == "calculateTotal"
        assert smell["start_line"] == 10
        assert smell["cyclomatic_complexity"] == 32

    def test_files_keep_their_metrics_and_quality_score(self, cuqa_report):
        filtered = filter_cuqa_report(cuqa_report, ["src/Order.java:10:0"])
        assert filtered["files"][0]["quality_score"] == 62.0
        assert filtered["files"][0]["metrics"]["lines_of_code"] == 240

    def test_the_report_is_marked_as_filtered(self, cuqa_report):
        assert filter_cuqa_report(cuqa_report, ["src/Order.java:10:0"])["filtered"] is True

    def test_summary_is_recomputed_not_carried_over(self, cuqa_report):
        filtered = filter_cuqa_report(cuqa_report, ["src/Order.java:10:0"])
        summary = filtered["summary"]
        assert summary["total_code_smells"] == 1
        assert summary["selected_count"] == 1
        assert summary["excluded_count"] == 2
        assert summary["selected_smell_ids"] == ["src/Order.java:10:0"]

    def test_selected_file_count_ignores_files_left_empty(self, cuqa_report):
        filtered = filter_cuqa_report(cuqa_report, ["src/Order.java:10:0"])
        assert filtered["summary"]["selected_file_count"] == 1

    def test_an_empty_selection_keeps_everything(self, cuqa_report):
        # `selected` being falsy means "no filter", which is what makes the
        # unfiltered report and a filtered-to-all report agree.
        filtered = filter_cuqa_report(cuqa_report, [])
        kept = [s for f in filtered["files"] for s in f["code_smells"]]
        assert len(kept) == 3

    def test_selecting_every_id_keeps_every_smell(self, cuqa_report):
        every = [s["id"] for s in cuqa_report_to_smells(cuqa_report)]
        filtered = filter_cuqa_report(cuqa_report, every)
        assert filtered["summary"]["selected_count"] == 3
        assert filtered["summary"]["excluded_count"] == 0

    def test_ids_match_the_ones_cuqa_report_to_smells_assigns(self, cuqa_report):
        # The two functions compute ids independently; if they ever disagree a
        # selection made in the UI resolves to nothing server-side.
        flattened = {s["id"] for s in cuqa_report_to_smells(cuqa_report)}
        filtered = filter_cuqa_report(cuqa_report, list(flattened))
        assert set(filtered["summary"]["selected_smell_ids"]) == flattened


@pytest.mark.unit
class TestLanguageAndTarget:
    def test_primary_language_is_the_most_common(self, cuqa_report):
        assert detect_primary_language(cuqa_report) == "java"

    def test_unknown_languages_do_not_win(self):
        report = {"files": [{"language": "unknown"}, {"language": "unknown"},
                            {"language": "python"}]}
        assert detect_primary_language(report) == "python"

    def test_language_defaults_to_java_when_nothing_is_known(self):
        assert detect_primary_language({"files": []}) == "java"

    def test_target_prefers_the_repository_name(self, cuqa_report):
        assert derive_target_name(cuqa_report) == "demo-repo"

    def test_target_falls_back_to_the_single_file_analysed(self):
        assert derive_target_name(
            {"files": [{"relative_path": "src/Only.java"}]}) == "src/Only.java"

    def test_target_falls_back_to_a_placeholder_for_a_nameless_repo(self):
        assert derive_target_name({"files": [{}, {}]}) == "cuqa_workspace"


@pytest.mark.unit
class TestSummarizeFiles:
    def test_totals_add_up(self, cuqa_report):
        summary = summarize_files(cuqa_report["files"])
        assert summary["files_analyzed"] == 2
        assert summary["total_code_smells"] == 3
        assert summary["total_lines_of_code"] == 280

    def test_average_quality_ignores_files_without_a_score(self):
        summary = summarize_files([
            {"quality_score": 60, "code_smells": []},
            {"quality_score": 80, "code_smells": []},
            {"code_smells": []},
        ])
        assert summary["average_quality_score"] == 70.0

    def test_no_files_does_not_divide_by_zero(self):
        assert summarize_files([])["average_quality_score"] == 0


@pytest.mark.unit
class TestBuildReportFromSmells:
    """The fallback used when a workflow was seeded from a client smell list."""

    def test_groups_smells_back_under_their_files(self, smells):
        report = build_report_from_smells(smells, "demo")
        assert len(report["files"]) == 2

    def test_filtering_keeps_the_file_but_drops_the_smell(self, smells):
        report = build_report_from_smells(smells, "demo",
                                          selected_ids=["src/Order.java:10:0"])
        by_path = {f["relative_path"]: f for f in report["files"]}
        assert len(by_path["src/Order.java"]["code_smells"]) == 1
        assert by_path["src/util/Helper.java"]["code_smells"] == []
