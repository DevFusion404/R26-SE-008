"""
conftest.py
-----------
Global pytest fixtures for the CUQA Agent test suite.

Provides:
  - Workspace isolation (reset _workspace between tests)
  - FastAPI TestClient
  - ZIP creation helpers
  - Temporary repository factories
  - Common source code snippets used across many tests
"""

import io
import os
import sys
import shutil
import zipfile
import tempfile
import importlib
from pathlib import Path
from typing import Generator, Callable

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Make CUQA src importable from tests
# ---------------------------------------------------------------------------
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# ---------------------------------------------------------------------------
# Import production modules (after path setup)
# ---------------------------------------------------------------------------
# pyrefly: ignore [missing-import]
import main as cuqa_main   # noqa: E402 — must be after sys.path update


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_workspace():
    """
    Reset the in-memory workspace before AND after every test.

    The _workspace module-level dict in main.py must be clean
    between tests to prevent cross-test contamination.
    """
    # Store original state
    original = dict(cuqa_main._workspace)

    # Reset before test
    cuqa_main._workspace.update({
        "root": None,
        "source": None,
        "repo_name": None,
        "files": [],
    })

    yield

    # Cleanup any temp dir created during the test
    root = cuqa_main._workspace.get("root")
    if root and os.path.exists(root):
        shutil.rmtree(root, ignore_errors=True)

    # Restore original state (paranoia — mostly the blank state above)
    cuqa_main._workspace.update(original)


# ---------------------------------------------------------------------------
# FastAPI TestClient
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> TestClient:
    """Return a FastAPI TestClient bound to the CUQA app."""
    return TestClient(cuqa_main.app)


# ---------------------------------------------------------------------------
# Temporary directory helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_repo(tmp_path: Path):
    """Return a tmp_path subdirectory pre-configured as a fake repository root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


# ---------------------------------------------------------------------------
# ZIP creation helpers
# ---------------------------------------------------------------------------

def make_zip_bytes(files: dict[str, str | bytes], top_dir: str | None = None) -> bytes:
    """
    Build an in-memory ZIP from a dict of {relative_path: content}.

    Args:
        files:   Mapping from member path (string) to file content (str or bytes).
        top_dir: If given, prefix all member paths with this directory name.

    Returns:
        Raw ZIP bytes.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for path, content in files.items():
            arcname = f"{top_dir}/{path}" if top_dir else path
            if isinstance(content, str):
                content = content.encode("utf-8")
            z.writestr(arcname, content)
    return buf.getvalue()


def make_zip_file(files: dict[str, str | bytes], dest: Path,
                  top_dir: str | None = None) -> Path:
    """Write a ZIP to *dest* and return its path."""
    dest.write_bytes(make_zip_bytes(files, top_dir=top_dir))
    return dest


@pytest.fixture
def make_zip():
    """Fixture that exposes make_zip_bytes as a callable."""
    return make_zip_bytes


# ---------------------------------------------------------------------------
# Common Python source snippets
# ---------------------------------------------------------------------------

PYTHON_CLEAN = '''\
"""A well-formed, smell-free Python module."""


def add(a: int, b: int) -> int:
    return a + b


def subtract(a: int, b: int) -> int:
    return a - b


class Calculator:
    """Simple calculator."""

    def multiply(self, a: int, b: int) -> int:
        return a * b
'''

PYTHON_LONG_METHOD = '''\
def long_function():
    x = 1
    x = 2
    x = 3
    x = 4
    x = 5
    x = 6
    x = 7
    x = 8
    x = 9
    x = 10
    x = 11
    x = 12
    x = 13
    x = 14
    x = 15
    x = 16
    x = 17
    x = 18
    x = 19
    x = 20
    x = 21
    x = 22
    x = 23
    x = 24
    x = 25
    x = 26
    x = 27
    x = 28
    x = 29
    x = 30
    return x
'''  # body_lines = 31 -> triggers LongMethod

JAVA_CLEAN = '''\
public class Clean {
    public int add(int a, int b) {
        return a + b;
    }
}
'''

C_CLEAN = '''\
#include <stdio.h>

int add(int a, int b) {
    return a + b;
}

int main(void) {
    printf("%d\\n", add(1, 2));
    return 0;
}
'''

C_UNSAFE = '''\
#include <stdio.h>
#include <string.h>

void copy_name(char *dest, char *src) {
    strcpy(dest, src);
}

void read_input(char *buf) {
    gets(buf);
}
'''


# ---------------------------------------------------------------------------
# Repository factory fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def load_python_repo(client, tmp_path, make_zip):
    """
    Upload a minimal Python repository and return the response JSON.
    Also ensures the workspace is loaded for follow-on API calls.
    """
    files = {"src/main.py": PYTHON_CLEAN, "src/utils.py": PYTHON_CLEAN}
    zb = make_zip(files, top_dir="myrepo")
    resp = client.post(
        "/api/upload-zip",
        files={"file": ("myrepo.zip", zb, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def load_c_repo(client, tmp_path, make_zip):
    """Upload a minimal C repository and return the response JSON."""
    files = {"src/main.c": C_CLEAN, "src/utils.h": "#pragma once\nint add(int,int);\n"}
    zb = make_zip(files, top_dir="crepo")
    resp = client.post(
        "/api/upload-zip",
        files={"file": ("crepo.zip", zb, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()
