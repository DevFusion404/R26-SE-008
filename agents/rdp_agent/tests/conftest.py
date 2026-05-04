"""
Shared pytest configuration and fixtures for RDP Agent tests.
"""

import pytest
import json
from pathlib import Path


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
