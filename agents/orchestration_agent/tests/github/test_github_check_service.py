"""
services/github_check_service.py
================================
The "RefactorIQ / DIWO" Check Run: creation on the PR head SHA, lifecycle
updates, failure reporting, and how real CUQA findings become a summary, a
findings table and line annotations within GitHub's limits.
"""

import pytest

from services.github_app_service import GitHubAPIError
from services.github_check_service import (
    ANNOTATIONS_PER_REQUEST, MAX_ANNOTATIONS, MAX_LISTED_SMELLS, build_annotations,
    build_findings_output, create_diwo_check, failure_output, publish_result,
    superseded_output, update_diwo_check, workflow_details_url,
)
from tests.github.conftest import HEAD_SHA, INSTALLATION_ID, make_response

JOB = {"id": "ghjob_1", "repo_owner": "octo-org", "repo_name": "demo",
       "repo_full_name": "octo-org/demo", "pr_number": 7, "head_sha": HEAD_SHA,
       "installation_id": INSTALLATION_ID, "head_repo_full_name": "octo-org/demo"}


def smell(i=0, file="src/app.py", lines=(10, 20), line=10, severity="high", type_="LongMethod",
          message="too long"):
    return {"id": f"{file}:{line}:{i}", "type": type_, "severity": severity, "message": message,
            "line": line, "relative_path": file,
            "location": {"file": file, "class": "App", "method": "run", "lines": list(lines)}}


# ── Check Run API calls ─────────────────────────────────────────────────────

def test_check_run_is_created_on_the_head_sha(app_service, fake_github):
    run = create_diwo_check(app_service, INSTALLATION_ID, "octo-org", "demo", HEAD_SHA,
                            external_id="ghjob_1")
    assert run["id"] == 5001
    create = [c for c in fake_github.calls if c["method"] == "POST"
              and c["path"].endswith("/check-runs")][0]
    assert create["path"] == "/repos/octo-org/demo/check-runs"
    assert create["json"] == {"name": "RefactorIQ / DIWO", "head_sha": HEAD_SHA,
                              "status": "queued", "external_id": "ghjob_1"}


def test_check_run_moves_through_its_lifecycle(app_service, fake_github):
    run = create_diwo_check(app_service, INSTALLATION_ID, "octo-org", "demo", HEAD_SHA)
    update_diwo_check(app_service, INSTALLATION_ID, "octo-org", "demo", run["id"],
                      status="in_progress")
    update_diwo_check(app_service, INSTALLATION_ID, "octo-org", "demo", run["id"],
                      conclusion="neutral", title="T", summary="S",
                      details_url="https://x.example/?diwo_workflow=wf_1")

    created, started, finished = fake_github.check_runs[run["id"]]
    assert created["status"] == "queued"
    assert started["status"] == "in_progress" and "started_at" in started
    assert finished["status"] == "completed" and finished["conclusion"] == "neutral"
    assert "completed_at" in finished
    assert finished["details_url"] == "https://x.example/?diwo_workflow=wf_1"
    assert finished["output"] == {"title": "T", "summary": "S"}


def test_failed_check_run_creation_raises(app_service, fake_github):
    fake_github.on("POST", r"/repos/[^/]+/[^/]+/check-runs",
                   lambda call: make_response(422, {"message": "No commit found for SHA"}))
    with pytest.raises(GitHubAPIError) as info:
        create_diwo_check(app_service, INSTALLATION_ID, "octo-org", "demo", HEAD_SHA)
    assert info.value.status == 422 and info.value.retryable is False
    assert "Check Run creation failed" in info.value.message


def test_missing_checks_permission_is_reported(app_service, fake_github):
    fake_github.on("POST", r"/repos/[^/]+/[^/]+/check-runs",
                   lambda call: make_response(403, {"message": "Resource not accessible by integration"}))
    with pytest.raises(GitHubAPIError) as info:
        create_diwo_check(app_service, INSTALLATION_ID, "octo-org", "demo", HEAD_SHA)
    assert (info.value.status, info.value.retryable) == (403, False)


@pytest.mark.parametrize("kwargs", [{"status": "done"}, {"conclusion": "passed"},
                                    {"annotations": [{}] * 51, "title": "t", "summary": "s"}])
def test_invalid_updates_are_refused_before_calling_github(app_service, fake_github, kwargs):
    with pytest.raises(ValueError):
        update_diwo_check(app_service, INSTALLATION_ID, "o", "r", 1, **kwargs)
    assert fake_github.calls == []


# ── Annotations ─────────────────────────────────────────────────────────────

def test_annotations_carry_location_level_and_explanation():
    [annotation], unmappable = build_annotations([smell()])
    assert unmappable == 0
    assert annotation == {"path": "src/app.py", "start_line": 10, "end_line": 20,
                          "annotation_level": "warning", "title": "S001 LongMethod (high)",
                          "message": "too long\n\nIn: run"}


@pytest.mark.parametrize("bad", [
    smell(file="../etc/passwd"), smell(file="/abs/path.py"), smell(file="C:/x.py"),
    smell(file="a//b.py"), smell(file=""), smell(lines=(0, 0), line=0),
    smell(lines=("x", None), line=None),
])
def test_unmappable_locations_are_not_annotated(bad):
    annotations, unmappable = build_annotations([bad])
    assert annotations == [] and unmappable == 1


def test_inverted_line_ranges_are_clamped():
    [annotation], _ = build_annotations([smell(lines=(30, 5))])
    assert (annotation["start_line"], annotation["end_line"]) == (30, 30)


def test_low_severity_is_a_notice():
    [annotation], _ = build_annotations([smell(severity="low")])
    assert annotation["annotation_level"] == "notice"


def test_annotation_total_is_capped():
    annotations, _ = build_annotations([smell(i, line=i + 1, lines=(i + 1, i + 1))
                                        for i in range(MAX_ANNOTATIONS + 30)])
    assert len(annotations) == MAX_ANNOTATIONS


def test_more_than_fifty_annotations_are_sent_in_batches(app_service, fake_github):
    run = create_diwo_check(app_service, INSTALLATION_ID, "octo-org", "demo", HEAD_SHA)
    annotations, _ = build_annotations([smell(i, line=i + 1, lines=(i + 1, i + 1))
                                        for i in range(120)])
    publish_result(app_service, JOB, run["id"], conclusion="neutral",
                   output={"title": "t", "summary": "s"}, annotations=annotations)
    updates = fake_github.check_runs[run["id"]][1:]
    assert [len(u["output"]["annotations"]) for u in updates] == [50, 50, 20]
    assert updates[0]["conclusion"] == "neutral"
    assert all("conclusion" not in u for u in updates[1:])
    assert ANNOTATIONS_PER_REQUEST == 50


# ── Output text ─────────────────────────────────────────────────────────────

def test_findings_output_reports_only_what_cuqa_found():
    result = {"smells": [smell(0), smell(1, file="src/b.py", severity="low", type_="DeadCode")],
              "report": {"summary": {"average_quality_score": 71.5, "total_lines_of_code": 300}},
              "files_analyzed": 50, "files_found": 80, "workflow_id": "wf_1"}
    output = build_findings_output(JOB, result, "https://x.example/?diwo_workflow=wf_1")
    assert output["title"] == "2 code smells in 2 files — developer review required"
    summary = output["summary"]
    assert "| Detected code smells | 2 |" in summary
    assert "| Affected files | 2 |" in summary
    assert "50 of 80 supported source files" in summary
    assert "| Average quality score (CUQA) | 71.5 |" in summary
    assert "**src/app.py** — 1 finding(s): high 1 · medium 0 · low 0" in summary
    assert "| LongMethod | 1 | high | run | 10 | S001 |" in summary
    assert "| DeadCode | 1 | low | run | 10 | S002 |" in summary
    assert "(https://x.example/?diwo_workflow=wf_1)" in summary
    for claim in ("safe", "applied", "validated", "accepted"):
        assert f"is {claim}" not in summary.lower()


def test_findings_output_without_a_frontend_url_names_the_workflow():
    output = build_findings_output(JOB, {"smells": [smell()], "report": {}, "workflow_id": "wf_9"},
                                   None)
    assert "wf_9" in output["summary"] and "DIWO_FRONTEND_URL" in output["summary"]


def test_repository_text_cannot_break_the_markdown():
    hostile = smell(message="x | y ` [click](https://evil) <img>", file="src/a|b.py")
    text = build_findings_output(JOB, {"smells": [hostile], "report": {}}, None)["text"]
    row = text.splitlines()[4]                       # after heading, blank, header, rule
    assert row.count(" | ") == 5, "a | in the message must not add a table column"
    assert "[click](https" not in text and "<img>" not in text


def test_long_findings_lists_are_truncated():
    smells = [smell(i, line=i + 1, lines=(i + 1, i + 1)) for i in range(MAX_LISTED_SMELLS + 5)]
    output = build_findings_output(JOB, {"smells": smells, "report": {}}, None)
    assert "and 5 more" in output["text"]
    assert len(output["text"]) < 65535


def test_failure_and_superseded_outputs():
    failed = failure_output(JOB, "CUQA agent is not reachable")
    assert failed["title"] == "Analysis failed" and "not reachable" in failed["summary"]
    superseded = superseded_output(JOB, "b" * 40)
    assert superseded["title"] == "Superseded by a newer commit"
    assert "bbbbbbb" in superseded["summary"]


@pytest.mark.parametrize("url,expected", [
    ("https://refactoriq.example.org/", "https://refactoriq.example.org/?diwo_workflow=wf_1"),
    ("http://localhost:5173", "http://localhost:5173/?diwo_workflow=wf_1"),
    ("", None), ("ftp://x", None), ("javascript:alert(1)", None),
])
def test_details_url_comes_only_from_configuration(monkeypatch, url, expected):
    monkeypatch.setenv("DIWO_FRONTEND_URL", url)
    assert workflow_details_url("wf_1") == expected
