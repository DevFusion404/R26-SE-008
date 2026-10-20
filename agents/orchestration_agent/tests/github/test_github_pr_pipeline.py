"""
services/github_pr_pipeline.py — snapshot checks, isolation and CUQA handling
=============================================================================
The pieces the worker tests exercise end to end, tested on their own edges:
which archives are accepted as "this exact commit", how CUQA's reply is
validated and normalized, and what a failed or empty analysis leaves behind.
"""

import io
import os
import zipfile

import pytest

from clients.cuqa_client import CUQAError
from services import github_pr_pipeline as pipeline
from services.github_pr_pipeline import PipelineError, isolated_workspace, verify_snapshot
from tests.github.conftest import HEAD_SHA, cuqa_payload, make_snapshot_zip

JOB = {"id": "ghjob_test", "delivery_id": "d-1", "repo_owner": "octo-org", "repo_name": "demo",
       "repo_full_name": "octo-org/demo", "pr_number": 7, "head_sha": HEAD_SHA,
       "installation_id": 987654}


def write(tmp_path, data, name="snap.zip"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def zip_of(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return buffer.getvalue()


# ── Exact-commit verification ───────────────────────────────────────────────

def test_github_shaped_snapshot_for_the_commit_is_accepted(tmp_path):
    path = write(tmp_path, make_snapshot_zip())
    assert verify_snapshot(path, "octo-org", "demo", HEAD_SHA) == f"octo-org-demo-{HEAD_SHA[:7]}"


def test_longer_sha_prefix_in_the_root_name_is_accepted(tmp_path):
    path = write(tmp_path, make_snapshot_zip(top=f"octo-org-demo-{HEAD_SHA[:12]}"))
    verify_snapshot(path, "octo-org", "demo", HEAD_SHA)


@pytest.mark.parametrize("data,fragment", [
    (make_snapshot_zip(sha="c" * 40), "does not match commit"),
    (make_snapshot_zip(top="octo-org-demo-aaa"), "does not match commit"),
    (zip_of({"a/x.py": "1", "b/y.py": "2"}), "single root directory"),
    (zip_of({"root/../../evil.py": "1"}), "unsafe paths"),
    (zip_of({"/root/abs.py": "1"}), "unsafe paths"),
    (zip_of({}), "empty"),
], ids=["other-commit", "short-prefix", "two-roots", "dotdot", "absolute", "empty"])
def test_wrong_or_unsafe_snapshots_are_rejected(tmp_path, data, fragment):
    with pytest.raises(PipelineError, match=fragment):
        verify_snapshot(write(tmp_path, data), "octo-org", "demo", HEAD_SHA)


def test_a_corrupt_archive_is_retryable(tmp_path):
    with pytest.raises(PipelineError) as info:
        verify_snapshot(write(tmp_path, b"<html>rate limited</html>"), "octo-org", "demo", HEAD_SHA)
    assert info.value.retryable is True


def test_too_many_entries_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_ARCHIVE_ENTRIES", 2)
    with pytest.raises(PipelineError, match="entries"):
        verify_snapshot(write(tmp_path, make_snapshot_zip()), "octo-org", "demo", HEAD_SHA)


def test_oversized_uncompressed_content_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(PipelineError, match="uncompressed"):
        verify_snapshot(write(tmp_path, make_snapshot_zip()), "octo-org", "demo", HEAD_SHA)


# ── Workspace isolation ─────────────────────────────────────────────────────

def test_workspaces_are_distinct_and_always_removed():
    with isolated_workspace("ghjob_a") as first, isolated_workspace("ghjob_a") as second:
        assert first != second and first.is_dir() and second.is_dir()
    assert not first.exists() and not second.exists()

    with pytest.raises(RuntimeError):
        with isolated_workspace("ghjob_b") as third:
            raise RuntimeError("analysis crashed")
    assert not third.exists()


def test_workspace_names_cannot_escape_the_temp_dir():
    with isolated_workspace("../../etc/passwd") as path:
        assert ".." not in path.name and os.sep not in path.name


# ── CUQA reply handling ─────────────────────────────────────────────────────

def test_cuqa_reply_for_a_different_snapshot_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "analyze_snapshot",
                        lambda *a, **k: cuqa_payload(top_level_dir="someone-else-1234567"))
    with pytest.raises(PipelineError, match="different snapshot"):
        pipeline.run_cuqa(write(tmp_path, make_snapshot_zip()), "octo-org-demo-aaaaaaa", JOB)


@pytest.mark.parametrize("status,retryable", [(503, True), (504, True), (500, True),
                                              (400, False), (422, False)])
def test_cuqa_errors_are_classified(tmp_path, monkeypatch, status, retryable):
    def fail(*args, **kwargs):
        raise CUQAError("boom", status=status)
    monkeypatch.setattr(pipeline, "analyze_snapshot", fail)
    with pytest.raises(PipelineError) as info:
        pipeline.run_cuqa(write(tmp_path, make_snapshot_zip()), "top", JOB)
    assert info.value.retryable is retryable
    assert info.value.message.startswith("CUQA analysis failed")


def test_cuqa_is_called_with_the_repository_name_and_snapshot(tmp_path, monkeypatch):
    seen = {}

    def analyze(zip_path, filename, repo_name, timeout=300):
        seen.update(filename=filename, repo_name=repo_name, timeout=timeout)
        return cuqa_payload(top_level_dir="octo-org-demo-aaaaaaa")
    monkeypatch.setattr(pipeline, "analyze_snapshot", analyze)
    monkeypatch.setenv("GITHUB_CUQA_TIMEOUT_SECONDS", "45")
    pipeline.run_cuqa(write(tmp_path, make_snapshot_zip()), "octo-org-demo-aaaaaaa", JOB)
    assert seen == {"filename": "octo-org-demo-aaaaaaa.zip", "repo_name": "octo-org/demo",
                    "timeout": 45}


def test_normalization_uses_the_existing_diwo_helpers(app, app_service, fake_github, fake_cuqa):
    """Smell ids, locations and categories come from cuqa_report_to_smells."""
    from db import github_job_repository as jobs
    from db import github_pr_workflow_repository as prs

    with app.app_context():
        job, _ = jobs.enqueue_pr_job({**{k: JOB[k] for k in JOB if k != "id"},
                                      "event": "pull_request", "action": "opened"})
        mapping = prs.get_or_create_mapping(job)
        result = pipeline.run_pr_analysis(app_service, job, mapping)

    assert result["status"] == "completed"
    ids = [s["id"] for s in result["smells"]]
    assert ids == ["src/app.py:1:0", "src/app.py:0:1", "src/util.py:1:0"]
    long_method = result["smells"][0]
    assert long_method["category"] == "Bloaters"
    assert long_method["severity"] == "high"
    assert long_method["metrics"]["quality_score"] == 64.0
    assert result["report"]["summary"]["total_code_smells"] == 3
    assert result["report"]["files"][0]["smell_summary"] == {"high": 1, "medium": 0, "low": 1}


def test_unexpected_cuqa_shape_is_a_clear_failure(app, app_service, fake_github, monkeypatch):
    from db import github_job_repository as jobs
    from db import github_pr_workflow_repository as prs
    monkeypatch.setattr(pipeline, "analyze_snapshot", lambda *a, **k: ["not", "an", "object"])

    with app.app_context():
        job, _ = jobs.enqueue_pr_job({**{k: JOB[k] for k in JOB if k != "id"},
                                      "event": "pull_request", "action": "opened"})
        with pytest.raises(PipelineError, match="Unexpected CUQA response shape"):
            pipeline.run_pr_analysis(app_service, job, prs.get_or_create_mapping(job))
