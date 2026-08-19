"""
performance/test_repository_scaling.py
----------------------------------------
Performance tests for repository scaling up to 100+ files.
"""

import time
import pytest
# pyrefly: ignore [missing-import]
from report_generator import generate_file_report, generate_repo_report


@pytest.mark.performance
class TestRepositoryScaling:
    def test_performance_100_files(self):
        start_time = time.time()
        file_reports = []

        for i in range(100):
            source = f"def func_{i}():\n    return {i} * 2\n"
            rep = generate_file_report(source, f"file_{i}.py")
            file_reports.append(rep)

        repo_report = generate_repo_report(file_reports)
        elapsed = time.time() - start_time

        assert repo_report["summary"]["files_analyzed"] == 100
        # Print performance benchmark
        print(f"\n[PERFORMANCE BENCHMARK] Processed 100 files in {elapsed:.3f} seconds.")
        # Generous upper bound to avoid flaky CI failures
        assert elapsed < 10.0
