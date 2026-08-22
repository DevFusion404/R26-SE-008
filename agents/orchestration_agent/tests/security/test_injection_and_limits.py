"""
security/test_injection_and_limits.py
--------------------------------------
Injection surfaces and resource-exhaustion guards.

The Orchestration Agent takes a workflow id straight out of the URL and uses
it in SQL, in filenames and in git arguments. It also builds ZIP archives from
whatever `files` a client sends. Both are places where a hostile or merely
malformed request must be refused rather than absorbed.
"""

import pytest

from config import MAX_ARCHIVE_BYTES, MAX_ARCHIVE_FILES
from services.archive_service import archive_path, build_refactored_archive

SQL_INJECTION_IDS = [
    "wf_x'; DROP TABLE workflows;--",
    "wf_x' OR '1'='1",
    "wf_x\"; DELETE FROM audit_logs; --",
    "'; UPDATE workflows SET status='completed'; --",
    "wf_x' UNION SELECT * FROM feedback_entries --",
]


@pytest.mark.security
class TestSqlInjection:
    """Every query is parameterised; these prove it end to end."""

    @pytest.mark.parametrize("hostile_id", SQL_INJECTION_IDS)
    def test_a_hostile_workflow_id_is_a_miss_not_an_error(self, client, hostile_id):
        response = client.get(f"/api/workflows/{hostile_id}")
        assert response.status_code == 404
        assert response.get_json()["error"] == "Workflow not found."

    @pytest.mark.parametrize("hostile_id", SQL_INJECTION_IDS)
    def test_and_the_tables_survive(self, client, hostile_id, workflow_id):
        client.get(f"/api/workflows/{hostile_id}")
        client.get(f"/api/workflows/{hostile_id}/audit-logs")
        client.post(f"/api/workflows/{hostile_id}/complete", json={})

        # The real workflow is still there and still readable.
        assert client.get(f"/api/workflows/{workflow_id}").status_code == 200
        assert client.get("/api/workflows").status_code == 200

    def test_injection_through_the_selection_body_is_inert(self, client, workflow_id):
        response = client.post(f"/api/workflows/{workflow_id}/select-smells", json={
            "selected_ids": ["'; DROP TABLE workflows; --"],
        })
        # Resolves to no known smell, so it is refused as an empty selection.
        assert response.status_code == 400
        assert client.get("/api/workflows").status_code == 200

    def test_injection_through_feedback_text_is_stored_not_executed(self, client, smells,
                                                                    make_workflow):
        wf_id = make_workflow()
        response = client.post(f"/api/workflows/{wf_id}/select-smells", json={
            "selected_ids": [smells[0]["id"]],
            "feedback": {"reason": "'; DROP TABLE feedback_entries; --"},
        })
        assert response.status_code == 200
        # The table still exists and the text round-trips as data.
        exported = client.get("/api/feedback/export").get_json()
        assert exported["count"] >= 1


@pytest.mark.security
class TestGitArgumentInjection:
    """Branch names reach git as argv entries, never through a shell."""

    @pytest.fixture
    def repo(self, tmp_path):
        import subprocess
        path = tmp_path / "repo"
        path.mkdir()
        (path / "a.txt").write_text("x", encoding="utf-8")
        for args in (("init", "-q"), ("config", "user.email", "t@t"),
                     ("config", "user.name", "T"), ("add", "-A"),
                     ("commit", "-qm", "initial")):
            subprocess.run(["git", *args], cwd=path, capture_output=True, text=True)
        return path

    @pytest.fixture(autouse=True)
    def no_desktop_launch(self, monkeypatch):
        from services import git_service
        monkeypatch.setattr(git_service, "open_github_desktop",
                            lambda path: (False, "stubbed in tests"))

    @pytest.mark.parametrize("hostile_branch", [
        "main; rm -rf /",
        "main && echo pwned",
        "main`whoami`",
        "main$(whoami)",
        "main | cat /etc/passwd",
        "--upload-pack=touch /tmp/pwned",
    ])
    def test_a_hostile_branch_name_cannot_run_a_second_command(self, client, repo,
                                                               hostile_branch, tmp_path):
        before = {p for p in tmp_path.rglob("*") if p.is_file()}

        response = client.post("/api/diwo/apply-and-push", json={
            "files": [{"path": "a.txt", "after": "changed"}],
            "branch_name": hostile_branch,
            "repository_path": str(repo),
            "commit": False, "push": False,
        })

        # git either refuses the ref name or treats it as one literal argument.
        # Either is fine; what matters is that no shell ran it.
        assert response.status_code in (200, 500)

        after = {p for p in tmp_path.rglob("*") if p.is_file()}
        surprises = {p.name for p in (after - before)} - {"a.txt"}
        assert "pwned" not in " ".join(surprises)

    def test_an_empty_branch_name_is_refused_before_git_is_touched(self, client, repo):
        response = client.post("/api/diwo/apply-and-push", json={
            "files": [{"path": "a.txt", "after": "x"}],
            "branch_name": "   ",
            "repository_path": str(repo),
        })
        assert response.status_code == 400
        assert "Branch name required" in response.get_json()["error"]

    def test_a_repository_path_that_is_not_a_repo_is_refused(self, client, tmp_path):
        response = client.post("/api/diwo/apply-and-push", json={
            "files": [{"path": "a.txt", "after": "x"}],
            "branch_name": "b",
            "repository_path": str(tmp_path / "not-a-repo"),
        })
        assert response.status_code == 400
        assert "Not a git repository" in response.get_json()["error"]


@pytest.mark.security
class TestResourceExhaustion:
    """A malformed payload must not be able to exhaust memory or disk."""

    def test_too_many_files_is_refused(self):
        files = [{"path": f"src/F{i}.java", "content": "x"}
                 for i in range(MAX_ARCHIVE_FILES + 1)]
        with pytest.raises(ValueError, match="Too many files"):
            build_refactored_archive("wf_x", files, {})

    def test_the_limit_itself_is_allowed(self):
        files = [{"path": f"src/F{i}.java", "content": "x"}
                 for i in range(MAX_ARCHIVE_FILES)]
        _, manifest = build_refactored_archive("wf_x", files, {})
        assert manifest["file_count"] == MAX_ARCHIVE_FILES

    def test_too_many_bytes_is_refused(self):
        # Ten files of a tenth the cap each, plus one, trips the byte guard
        # without needing MAX_ARCHIVE_FILES entries.
        chunk = "x" * (MAX_ARCHIVE_BYTES // 10)
        files = [{"path": f"src/F{i}.java", "content": chunk} for i in range(11)]
        with pytest.raises(ValueError, match="exceeds"):
            build_refactored_archive("wf_x", files, {})

    def test_the_byte_guard_counts_encoded_length_not_character_count(self):
        # A multi-byte character must not slip past a len()-based check: this
        # payload is under the cap by CHARACTER count and over it by BYTES.
        chunk = "\u00e9" * (MAX_ARCHIVE_BYTES // 2 + 1)   # 2 bytes each in UTF-8
        assert len(chunk) <= MAX_ARCHIVE_BYTES
        assert len(chunk.encode("utf-8")) > MAX_ARCHIVE_BYTES
        with pytest.raises(ValueError, match="exceeds"):
            build_refactored_archive("wf_x", [{"path": "a.java", "content": chunk}], {})

    def test_a_payload_exactly_on_the_byte_limit_is_allowed(self):
        # The guard is `> MAX`, so the limit itself is inside the contract.
        chunk = "x" * MAX_ARCHIVE_BYTES
        _, manifest = build_refactored_archive(
            "wf_x", [{"path": "a.java", "content": chunk}], {})
        assert manifest["file_count"] == 1

    def test_an_archive_of_nothing_is_refused_rather_than_written_empty(self):
        with pytest.raises(ValueError, match="content"):
            build_refactored_archive("wf_x", [], {})
        with pytest.raises(ValueError, match="content"):
            build_refactored_archive("wf_x", [{"path": "a.java", "content": ""}], {})

    def test_a_non_list_files_payload_is_refused(self):
        with pytest.raises(ValueError, match="must be a list"):
            # Deliberately the wrong type - the runtime guard is what is
            # under test, so the annotation is bypassed on purpose.
            build_refactored_archive("wf_x", "not-a-list", {})  # type: ignore[arg-type]

    def test_a_huge_selection_body_does_not_crash_the_endpoint(self, client, workflow_id):
        response = client.post(f"/api/workflows/{workflow_id}/selection-impact",
                               json={"selected_ids": [f"ghost-{i}" for i in range(20000)]})
        assert response.status_code == 200
        assert response.get_json()["summary"]["selected_count"] == 0


@pytest.mark.security
class TestArchiveFilenameSafety:
    @pytest.mark.parametrize("hostile_id", [
        "../../etc/passwd", "wf_x/../../escape", "..", "/absolute",
    ])
    def test_a_hostile_workflow_id_cannot_steer_the_archive_file(self, hostile_id):
        # archive_path() interpolates the workflow id into a filename.
        from config import ARCHIVES_DIR
        resolved = archive_path(hostile_id).resolve()
        root = ARCHIVES_DIR.resolve()
        assert root == resolved.parent or root in resolved.parents, (
            f"{hostile_id!r} produced {resolved}, outside {root}"
        )


@pytest.mark.security
class TestErrorDisclosure:
    def test_an_unknown_workflow_does_not_leak_a_filesystem_path(self, client):
        body = client.get("/api/workflows/wf_missing").get_data(as_text=True)
        assert "C:\\" not in body and "/home/" not in body

    def test_agent_errors_name_the_agent_url_but_no_secret(self, client):
        # The URL is deliberately included so the developer knows what to
        # start; nothing else about the environment should appear.
        body = client.get("/api/cuqa/status").get_data(as_text=True)
        assert "SECRET" not in body.upper()
        assert "PASSWORD" not in body.upper()

    def test_a_malformed_body_does_not_return_a_stack_trace(self, client, workflow_id):
        response = client.post(f"/api/workflows/{workflow_id}/plan-decision",
                               data="{not json", content_type="application/json")
        assert response.status_code == 400
        assert "Traceback" not in response.get_data(as_text=True)
