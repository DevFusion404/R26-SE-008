"""
integration/test_source_snapshot.py
-----------------------------------
The hosted-app failure: several users share one CUQA workspace, another
user's upload replaced the repository between smell selection and
transformation, and every planned file came back "missing" (422).

The selected files' source is now kept on the workflow at smell selection, and
the transformation reads it from there.
"""

import pytest

from services import source_snapshot


def _workspace(repo_files):
    """A fake CUQA workspace that holds exactly `repo_files`."""
    def read(file_paths, timeout=60):
        present = [p for p in file_paths if p in repo_files]
        return {
            "files": [{"file_name": p, "source_code": repo_files[p], "language": "java",
                       "source_mode": "workspace"} for p in present],
            "missing": [p for p in file_paths if p not in repo_files],
            "imported": len(present),
            "total": len(file_paths),
            "origin": "cuqa_workspace",
        }
    return read


@pytest.fixture
def original_repo(smells):
    files = {s["location"]["file"] for s in smells}
    return {path: f"// original source of {path}\nclass X {{}}\n" for path in files}


def _select_and_approve(client, wf_id, smells):
    response = client.post(f"/api/workflows/{wf_id}/select-smells",
                           json={"selected_ids": [s["id"] for s in smells]})
    assert response.status_code == 200, response.get_data(as_text=True)
    plan = client.get(f"/api/workflows/{wf_id}").get_json()["plan"]
    decisions = {str(step["step_id"]): "approve" for step in plan["steps"]}
    response = client.post(f"/api/workflows/{wf_id}/plan-decision",
                           json={"decision": "approve", "decisions": decisions})
    assert response.status_code == 200, response.get_data(as_text=True)


def test_transform_survives_another_users_upload(client, make_workflow, smells, original_repo,
                                                 stub_sctva_execute, monkeypatch):
    wf_id = make_workflow()

    # Smell selection: CUQA still holds the developer's repository.
    monkeypatch.setattr(source_snapshot, "fetch_workspace_sources", _workspace(original_repo))
    _select_and_approve(client, wf_id, smells)

    # Another user uploads a different project: the shared workspace no longer
    # has any of the planned files.
    monkeypatch.setattr(source_snapshot, "fetch_workspace_sources", _workspace({}))

    response = client.post(f"/api/workflows/{wf_id}/transform", json={})

    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    # None of the selected files is missing. (The offline test plan can also
    # name a module-level placeholder that no workspace holds.)
    assert not set(body["sources"]["missing"]) & set(original_repo)
    # SCTVA received the ORIGINAL source kept at selection.
    sent = stub_sctva_execute["execute"][-1]["source_files"]
    assert sent and all(f["source_code"].startswith("// original source of") for f in sent)


def test_without_a_snapshot_the_live_read_is_unchanged(client, make_workflow, smells,
                                                       stub_sctva_execute, monkeypatch):
    """Snapshot could not be taken (CUQA unreachable): transform reads live, as before."""
    wf_id = make_workflow()

    def unreachable(*args, **kwargs):
        raise RuntimeError("CUQA down")

    monkeypatch.setattr(source_snapshot, "fetch_workspace_sources", unreachable)
    _select_and_approve(client, wf_id, smells)          # selection is not blocked

    response = client.post(f"/api/workflows/{wf_id}/transform", json={})

    assert response.status_code == 200, response.get_data(as_text=True)
    # Served by the stubbed live reader in transformation_service.
    assert stub_sctva_execute["sources"], "the transform fell back to the live read"


def test_snapshot_is_logged(client, make_workflow, smells, original_repo, monkeypatch):
    wf_id = make_workflow()
    monkeypatch.setattr(source_snapshot, "fetch_workspace_sources", _workspace(original_repo))
    client.post(f"/api/workflows/{wf_id}/select-smells",
                json={"selected_ids": [s["id"] for s in smells]})

    logs = client.get(f"/api/workflows/{wf_id}/audit-logs").get_json()
    saved = [row for row in logs if row["action"] == "source_snapshot_saved"]
    assert saved
    details = saved[0].get("details") or saved[0].get("details_json")
    assert "source_code" not in str(details)            # the log records counts, not code
