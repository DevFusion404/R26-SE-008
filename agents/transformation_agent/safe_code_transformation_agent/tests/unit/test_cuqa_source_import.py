import shutil
import uuid
from pathlib import Path

import pytest

from sctva.integration import api as api_module


@pytest.fixture
def tmp_sctva_dir():
    parent = Path(__file__).resolve().parents[2] / ".sctva_test_tmp"
    root = parent / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
        try:
            parent.rmdir()
        except OSError:
            pass


def test_cuqa_temp_workspace_lookup_finds_safe_relative_file(monkeypatch, tmp_sctva_dir):
    workspace = tmp_sctva_dir / "cuqa_123" / "extracted" / "repo"
    source_file = workspace / "examples" / "ini_dump.c"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("int main(void) { return 0; }\n", encoding="utf-8")

    monkeypatch.setattr(api_module.tempfile, "gettempdir", lambda: str(tmp_sctva_dir))

    found = api_module._find_cuqa_workspace_file("examples/ini_dump.c")

    assert found is not None
    assert found.resolve() == source_file.resolve()


def test_cuqa_temp_workspace_lookup_matches_suffix_with_repo_prefix(monkeypatch, tmp_sctva_dir):
    workspace = tmp_sctva_dir / "cuqa_456" / "extracted" / "Laundry-Management-System-master"
    source_file = workspace / "src" / "main" / "java" / "Model" / "Order.java"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("class Order {}\n", encoding="utf-8")

    monkeypatch.setattr(api_module.tempfile, "gettempdir", lambda: str(tmp_sctva_dir))

    found = api_module._find_cuqa_workspace_file("src/main/java/Model/Order.java")

    assert found is not None
    assert found.resolve() == source_file.resolve()


def test_cuqa_temp_workspace_lookup_rejects_unsafe_paths(monkeypatch, tmp_sctva_dir):
    monkeypatch.setattr(api_module.tempfile, "gettempdir", lambda: str(tmp_sctva_dir))

    assert api_module._safe_relative_source_path("../secret.c") == ""
    assert api_module._find_cuqa_workspace_file("../secret.c") is None
