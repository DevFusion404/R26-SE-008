"""
security/test_path_traversal.py
--------------------------------
CRITICAL SECURITY TESTS.

The Orchestration Agent is the only agent in R26-SE-008 that WRITES to the
developer's filesystem: it builds ZIP archives from client-supplied paths and
checks refactored source into a git working copy. Both take the path from the
request body, so both are directly reachable path-traversal surfaces.

Two defences are covered here:

  archive_service.safe_archive_path   normalises entry names so extracting an
                                      archive cannot escape its own folder
                                      (the "Zip Slip" class of bug)

  git_service.apply_and_push          refuses to write outside the repository
                                      root, however the path is spelled
"""

import io
import zipfile
from pathlib import Path

import pytest

from services.archive_service import build_refactored_archive, safe_archive_path

TRAVERSAL_PATHS = [
    "../evil.java",
    "../../evil.java",
    "../../../etc/passwd",
    "src/../../evil.java",
    "..\\..\\windows\\system32\\calc.exe",
    "....//....//evil.java",
    "/etc/passwd",
    "C:\\Windows\\System32\\drivers\\etc\\hosts",
    "//server/share/evil.java",
]


@pytest.mark.security
class TestArchivePathNormalisation:
    """Zip Slip: an entry name must never resolve outside the extraction root."""

    @pytest.mark.parametrize("hostile", TRAVERSAL_PATHS)
    def test_no_traversal_survives_normalisation(self, hostile):
        safe = safe_archive_path(hostile)
        assert not safe.startswith("/"), f"{hostile!r} stayed absolute: {safe!r}"
        assert not safe.startswith("\\"), f"{hostile!r} stayed absolute: {safe!r}"
        assert ".." not in safe.split("/"), f"{hostile!r} kept a parent segment: {safe!r}"
        assert ":" not in safe, f"{hostile!r} kept a drive letter: {safe!r}"

    @pytest.mark.parametrize("hostile", TRAVERSAL_PATHS)
    def test_the_normalised_path_stays_inside_a_root(self, hostile, tmp_path):
        # The property that actually matters: joining the result to a root and
        # resolving it must not leave that root.
        root = tmp_path / "extract"
        root.mkdir()
        target = (root / safe_archive_path(hostile)).resolve()
        assert root.resolve() == target or root.resolve() in target.parents

    def test_an_empty_or_useless_path_gets_a_fallback_name(self):
        assert safe_archive_path("") == "file"
        assert safe_archive_path(None) == "file"
        assert safe_archive_path("../..") == "file"
        assert safe_archive_path("///") == "file"

    def test_legitimate_folders_are_preserved(self):
        # The defence must not flatten the project: extracting has to reproduce
        # src/util/Helper.java, not dump Helper.java at the root.
        assert safe_archive_path("src/util/Helper.java") == "src/util/Helper.java"

    @pytest.mark.parametrize("hostile", TRAVERSAL_PATHS)
    def test_a_built_archive_contains_no_escaping_member(self, hostile):
        payload, _ = build_refactored_archive(
            "wf_sec", [{"path": hostile, "content": "payload"}], {})
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for name in archive.namelist():
                assert not name.startswith("/")
                assert ".." not in name.split("/")

    def test_extracting_a_hostile_archive_writes_only_inside_the_target(self, tmp_path):
        payload, _ = build_refactored_archive("wf_sec", [
            {"path": "../../escaped.java", "content": "nope"},
            {"path": "src/Fine.java", "content": "fine"},
        ], {})

        target = tmp_path / "out"
        target.mkdir()
        canary = tmp_path / "escaped.java"

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            archive.extractall(target)

        assert not canary.exists(), "a member escaped the extraction directory"
        written = [p for p in target.rglob("*") if p.is_file()]
        assert all(target.resolve() in p.resolve().parents for p in written)


@pytest.mark.security
class TestGitApplyPathTraversal:
    """apply-and-push writes client-supplied paths into a real repository."""

    @pytest.fixture
    def repo(self, tmp_path):
        """A minimal git repository to write into."""
        import subprocess
        path = tmp_path / "repo"
        (path / "src").mkdir(parents=True)
        (path / "src" / "Order.java").write_text("class Order {}", encoding="utf-8")

        def git(*args):
            return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True)

        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "T")
        git("add", "-A")
        git("commit", "-qm", "initial")
        return path

    @pytest.fixture(autouse=True)
    def no_desktop_launch(self, monkeypatch):
        """Never open GitHub Desktop from a test."""
        from services import git_service
        monkeypatch.setattr(git_service, "open_github_desktop",
                            lambda path: (False, "stubbed in tests"))

    @pytest.mark.parametrize("hostile", [
        "../escaped.java",
        "../../escaped.java",
        "src/../../escaped.java",
        "../../../etc/passwd",
    ])
    def test_traversal_is_refused_with_400(self, client, repo, hostile, tmp_path):
        response = client.post("/api/diwo/apply-and-push", json={
            "files": [{"path": hostile, "after": "owned"}],
            "branch_name": "sec/test",
            "repository_path": str(repo),
            "commit": False,
            "push": False,
        })
        assert response.status_code == 400
        assert "traversal" in response.get_json()["error"].lower()

    @pytest.mark.parametrize("hostile", [
        "../escaped.java",
        "../../escaped.java",
    ])
    def test_and_nothing_is_written_outside_the_repository(self, client, repo, hostile,
                                                           tmp_path):
        before = {p for p in tmp_path.rglob("*") if p.is_file()}

        client.post("/api/diwo/apply-and-push", json={
            "files": [{"path": hostile, "after": "owned"}],
            "branch_name": "sec/test",
            "repository_path": str(repo),
            "commit": False, "push": False,
        })

        after = {p for p in tmp_path.rglob("*") if p.is_file()}
        created_outside = {
            p for p in (after - before)
            if repo.resolve() not in p.resolve().parents
        }
        assert not created_outside, f"files written outside the repo: {created_outside}"

    def test_a_leading_slash_is_treated_as_repo_relative_not_absolute(self, client, repo):
        # "/etc/passwd" must land at <repo>/etc/passwd, never at the real one.
        response = client.post("/api/diwo/apply-and-push", json={
            "files": [{"path": "/etc/passwd", "after": "harmless"}],
            "branch_name": "sec/test",
            "repository_path": str(repo),
            "commit": False, "push": False,
        })
        assert response.status_code == 200
        assert (repo / "etc" / "passwd").exists()

    def test_legitimate_nested_paths_are_still_written(self, client, repo):
        response = client.post("/api/diwo/apply-and-push", json={
            "files": [{"path": "src/util/New.java", "after": "class New {}"}],
            "branch_name": "sec/test",
            "repository_path": str(repo),
            "commit": False, "push": False,
        })
        assert response.status_code == 200
        assert (repo / "src" / "util" / "New.java").read_text(encoding="utf-8") == "class New {}"

    def test_a_sibling_directory_sharing_a_prefix_is_not_writable(self, client, repo, tmp_path):
        # "/repo-backup" shares a string prefix with "/repo"; a naive
        # startswith() containment check would let it through.
        sibling = tmp_path / "repo-backup"
        sibling.mkdir()
        response = client.post("/api/diwo/apply-and-push", json={
            "files": [{"path": "../repo-backup/owned.java", "after": "owned"}],
            "branch_name": "sec/test",
            "repository_path": str(repo),
            "commit": False, "push": False,
        })
        assert response.status_code == 400
        assert not (sibling / "owned.java").exists()


@pytest.mark.security
class TestWorkspaceSourceRequests:
    """The workspace reader takes paths from the request body too."""

    def test_traversal_paths_are_forwarded_verbatim_not_resolved_locally(self, client):
        # DIWO does not read the filesystem here - it proxies to SCTVA, which
        # owns the workspace and does its own containment. The orchestrator
        # must not "helpfully" resolve the path against its own cwd first.
        response = client.post("/api/workspace/sources",
                               json={"file_paths": ["../../../etc/passwd"]})
        # SCTVA is stubbed unreachable, so this is a clean 503, never a local read.
        assert response.status_code == 503
        assert "not reachable" in response.get_json()["error"]

    def test_a_non_list_is_rejected_before_any_agent_call(self, client):
        response = client.post("/api/workspace/sources", json={"file_paths": "../../etc"})
        assert response.status_code == 400
