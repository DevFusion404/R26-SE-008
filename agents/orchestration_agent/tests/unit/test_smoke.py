import pytest

@pytest.mark.unit
def test_conftest_wires_up(client, workflow_id):
    assert workflow_id.startswith("wf_")
    assert client.get(f"/api/workflows/{workflow_id}").status_code == 200
