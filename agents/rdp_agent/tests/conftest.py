"""
Shared pytest configuration and fixtures for RDP Agent tests.
"""
import sys
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Path shim: make "rdp_agent" importable when running from the rdp_agent root.
# The package lives in src/ but tests import it as "rdp_agent".
# ---------------------------------------------------------------------------
_here = Path(__file__).parent.parent  # agents/rdp_agent/
_src = _here / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

# Create an "rdp_agent" alias in sys.modules pointing to the "src" package.
import importlib, types
if "rdp_agent" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "rdp_agent", str(_src / "__init__.py"),
        submodule_search_locations=[str(_src)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rdp_agent"] = mod
    spec.loader.exec_module(mod)

import pytest
import json


@pytest.fixture
def sample_quality_report():
    """Provides a sample quality report for testing."""
    return {
        "file": "example.py",
        "smells": [
            {
                "type": "long_method",
                "severity": "high",
                "line": 42,
                "message": "Method 'process_data' is too long (150 lines)"
            },
            {
                "type": "duplicate_code",
                "severity": "medium",
                "line": 65,
                "message": "Code block duplicated in 2 locations"
            }
        ]
    }


@pytest.fixture
def temp_output_dir(tmp_path):
    """Provides a temporary directory for test outputs."""
    return tmp_path / "rdp_outputs"
