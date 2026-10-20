"""
Feature A — compact, grouped CUQA reporting and verified annotations
====================================================================
services/github_report_service.py and the findings output built from it.
Every number in these reports comes from the (fake) CUQA findings; nothing
is hard-coded in the service.
"""

import pytest

from services.github_check_service import (MAX_ANNOTATIONS, MAX_SUMMARY, build_annotations,
                                           build_findings_output)
from services.github_report_service import (compress_ids, display_ids, group_findings,
                                            resolve_display_ids, verify_location,
                                            verify_locations_in_snapshot)
from tests.github.conftest import HEAD_SHA, SNAPSHOT_FILES, make_snapshot_zip

JOB = {"repo_owner": "octo-org", "repo_name": "demo", "repo_full_name": "octo-org/demo",
       "pr_number": 7, "head_sha": HEAD_SHA, "installation_id": 1}
APP_LINES = SNAPSHOT_FILES["src/app.py"].splitlines()      # x{i} = {i} sits on line i + 2


def smell(i, type_="MagicNumber", file="src/app.py", line=None, lines=None, severity="low",
          message=None, method=None, klass="app", entity=None):
    line = line if line is not None else i + 2
    return {"id": f"{file}:{line}:{i}", "type": type_, "severity": severity,
            "message": message if message is not None else f"Magic number {i}",
            "line": line, "entity": entity, "relative_path": file,
            "location": {"file": file, "class": klass, "method": method,
                         "lines": list(lines or (line, line))}}


# ── Stable ids ──────────────────────────────────────────────────────────────

def test_display_ids_follow_stored_order():
    smells = [smell(1), smell(2), smell(3)]
    assert list(display_ids(smells).values()) == ["S001", "S002", "S003"]


def test_display_ids_resolve_strictly():
    smells = [smell(1), smell(2)]
    ids, unknown = resolve_display_ids(smells, ["S001", "s2", "S2", "S099", "LongMethod", "*"])
    assert ids == [smells[0]["id"], smells[1]["id"]]
    assert unknown == ["S099", "LongMethod", "*"]


def test_ids_are_compressed_into_ranges():
    assert compress_ids(["S001", "S002", "S003", "S005", "S007", "S008"]) == "S001–S003, S005, S007–S008"
    long = compress_ids([f"S{i:03d}" for i in range(1, 40, 2)], limit=3)
    assert long.endswith("(20 total)")


# ── Grouping ────────────────────────────────────────────────────────────────

def findings():
    repeated = [smell(i) for i in range(1, 13)]                    # 12 MagicNumbers
    return repeated + [
        smell(20, "LongMethod", line=1, lines=(1, 61), severity="high",
              message="long_function is 61 lines", method="long_function"),
        smell(21, "TooManyParameters", line=1, severity="medium", message="too many",
              method="long_function"),
        smell(22, "DeadCode", file="src/util.py", line=1, severity="low",
              message="helper is never called", method="helper"),
    ]


def test_findings_are_grouped_by_file_then_type_by_severity():
    files = group_findings(findings())
    assert [f["file"] for f in files] == ["src/app.py", "src/util.py"]
    app_groups = files[0]["groups"]
    assert [(g["type"], g["count"], g["severity"]) for g in app_groups] == [
        ("LongMethod", 1, "high"), ("TooManyParameters", 1, "medium"), ("MagicNumber", 12, "low")]
    assert files[0]["counts"] == {"high": 1, "medium": 1, "low": 12}
    assert app_groups[2]["ids"][:2] == ["S001", "S002"]


def test_repeated_findings_are_one_row_with_every_id_kept():
    output = build_findings_output(JOB, {"smells": findings(), "report": {}}, None)
    summary = output["summary"]
    assert "| MagicNumber | 12 | low | app | 3, 4, 5, 6, 7, … (+7) | S001–S012 |" in summary
    assert summary.count("| MagicNumber |") == 1
    # The detailed text still lists every individual finding with its own id.
    for i in range(1, 13):
        assert f"| S{i:03d} | MagicNumber |" in output["text"]


def test_summary_reports_real_totals():
    result = {"smells": findings(), "files_analyzed": 2, "files_found": 2,
              "report": {"summary": {"total_lines_of_code": 275, "average_quality_score": 46.0}}}
    summary = build_findings_output(JOB, result, None)["summary"]
    assert "| Detected code smells | 15 |" in summary
    assert "| Affected files | 2 |" in summary
    assert "| Files analysed | 2 |" in summary
    assert "| Lines of code analysed | 275 |" in summary
    assert "high 1 · medium 1 · low 13" in summary
    assert "| Average quality score (CUQA) | 46.0 |" in summary
    assert "| Developer review | Required" in summary
    assert "/refactoriq select" in summary


def test_high_and_medium_files_come_first():
    smells = [smell(1, file="z_low.py")] + [smell(2, "LongMethod", file="a_high.py", severity="high",
                                                  method="m")]
    assert [f["file"] for f in group_findings(smells)] == ["a_high.py", "z_low.py"]


def test_large_reports_stay_within_github_limits():
    smells = [smell(i, file=f"pkg/mod_{i % 60}.py") for i in range(2000)]
    output = build_findings_output(JOB, {"smells": smells, "report": {}}, None)
    assert len(output["summary"]) <= MAX_SUMMARY
    assert "more files" in output["summary"]
    assert len(output["text"]) < 65535 and "more; see the DIWO workflow" in output["text"]


# ── Verified locations ──────────────────────────────────────────────────────

def test_magic_number_on_its_line_is_kept():
    assert verify_location(smell(5), APP_LINES) == {"start": 7, "end": 7}


def test_magic_number_off_by_one_is_corrected():
    assert verify_location(smell(5, line=8), APP_LINES) == {"start": 7, "end": 7}


def test_magic_number_not_present_is_not_annotated():
    result = verify_location(smell(5, line=40, message="Magic number 999"), APP_LINES)
    assert "unmapped" in result and "999" in result["unmapped"]


def test_method_smell_must_sit_on_its_declaration():
    long_method = smell(0, "LongMethod", line=1, lines=(1, 61), method="long_function")
    assert verify_location(long_method, APP_LINES) == {"start": 1, "end": 61}
    shifted = smell(0, "LongMethod", line=3, lines=(3, 63), method="long_function")
    assert verify_location(shifted, APP_LINES) == {"start": 1, "end": 61}, \
        "the range moves with the corrected start and is clamped to the file"
    wrong = smell(0, "LongMethod", line=30, lines=(30, 40), method="long_function")
    assert "unmapped" in verify_location(wrong, APP_LINES)


@pytest.mark.parametrize("bad,fragment", [
    (smell(1, line=0, lines=(0, 0)), "file-level"),
    (smell(1, line=500, lines=(500, 500)), "beyond the end"),
])
def test_impossible_lines_are_not_annotated(bad, fragment):
    assert fragment in verify_location(bad, APP_LINES)["unmapped"]


def test_locations_are_checked_against_the_commit_archive(tmp_path):
    zip_path = tmp_path / "snap.zip"
    zip_path.write_bytes(make_snapshot_zip())
    top = f"octo-org-demo-{HEAD_SHA[:7]}"
    good, missing = smell(5), smell(6, file="src/deleted.py")
    result = verify_locations_in_snapshot(zip_path, top, [good, missing])
    assert result["locations"][good["id"]] == {"start": 7, "end": 7}
    assert "not found in the analysed commit" in result["locations"][missing["id"]]["unmapped"]
    assert result["line_counts"]["src/app.py"] == len(APP_LINES)


def test_annotations_use_verified_locations_and_keep_unmapped_findings():
    smells = [smell(5, line=8), smell(6, line=40, message="Magic number 999")]
    report = {"snapshot": {"locations": {smells[0]["id"]: {"start": 7, "end": 7},
                                         smells[1]["id"]: {"unmapped": "literal not found"}}}}
    annotations, unmapped = build_annotations(smells, report)
    assert [(a["start_line"], a["title"]) for a in annotations] == [(7, "S001 MagicNumber (low)")]
    assert unmapped == 1
    text = build_findings_output(JOB, {"smells": smells, "report": report}, None)["text"]
    assert "| S002 |" in text, "an unannotated finding is still reported"


def test_high_severity_is_annotated_before_the_cap_fills():
    smells = [smell(i, file=f"m{i}.py", line=1) for i in range(MAX_ANNOTATIONS + 20)]
    smells.append(smell(999, "LongMethod", file="zz.py", line=1, severity="high", method="m"))
    annotations, _ = build_annotations(smells)
    assert len(annotations) == MAX_ANNOTATIONS
    assert any(a["path"] == "zz.py" for a in annotations)
