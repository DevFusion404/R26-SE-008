"""
integration/test_cuqa_rdp_contract.py
--------------------------------------
CRITICAL CONTRACT TEST: Validates that CUQA Agent output JSON can be safely
translated and consumed by the RDP Agent's _translate_cuqa_to_rdp() function.
"""

import sys
from pathlib import Path
import pytest

# Add RDP agent root to path so we can import _translate_cuqa_to_rdp from app.py
RDP_DIR = Path(__file__).resolve().parent.parent.parent.parent / "rdp_agent"
if str(RDP_DIR) not in sys.path:
    sys.path.insert(0, str(RDP_DIR))

# pyrefly: ignore [missing-import]
from report_generator import generate_file_report, generate_repo_report

try:
    # pyrefly: ignore [missing-import]
    from app import _translate_cuqa_to_rdp
    RDP_AVAILABLE = True
except ImportError:
    RDP_AVAILABLE = False


@pytest.mark.integration
class TestCUQARDPContract:
    def test_rdp_translator_importable(self):
        assert RDP_AVAILABLE, "Could not import _translate_cuqa_to_rdp from agents/rdp_agent/app.py"

    def test_all_python_smells_consumed_by_rdp(self):
        if not RDP_AVAILABLE:
            pytest.skip("RDP agent app.py not importable")

        # Generate sample CUQA report containing all Python smell types
        py_sources = [
            "def fn(a,b,c,d,e,f): pass", # TooManyParameters
            "class C:\n    def m(self, o):\n        return o.a.b.c", # MessageChains
            "def broken():\n    try:\n        x = 1\n    except:\n        pass\n", # BareExcept
            "x = 999\n", # MagicNumber
        ]
        file_reports = [generate_file_report(src, f"f{i}.py") for i, src in enumerate(py_sources)]
        cuqa_repo_report = generate_repo_report(file_reports)

        # Translate using RDP's function
        rdp_report = _translate_cuqa_to_rdp(cuqa_repo_report)

        assert "target" in rdp_report
        assert "smells" in rdp_report
        assert "metrics_summary" in rdp_report
        assert len(rdp_report["smells"]) > 0

        for smell in rdp_report["smells"]:
            assert "id" in smell
            assert "type" in smell
            assert "location" in smell
            assert "metrics" in smell
            assert "severity" in smell
            assert "details" in smell

    def test_all_c_smells_consumed_by_rdp(self):
        if not RDP_AVAILABLE:
            pytest.skip("RDP agent app.py not importable")

        c_source = '''\
#include <stdio.h>
#include <string.h>

int g_counter = 0;

void fn(char *b, char *s, int a, int c, int d, int e, int f) {
    if (a) { if (c) { if (d) { if (e) { if (f) {} } } } }
    strcpy(b, s);
    int x = 888;
}
'''
        file_report = generate_file_report(c_source, "smelly.c")
        cuqa_repo_report = generate_repo_report([file_report])

        rdp_report = _translate_cuqa_to_rdp(cuqa_repo_report)

        assert "target" in rdp_report
        assert len(rdp_report["smells"]) > 0

    def test_polyglot_cuqa_report_translated_by_rdp(self):
        if not RDP_AVAILABLE:
            pytest.skip("RDP agent app.py not importable")

        py_rep = generate_file_report("x = 999\n", "main.py")
        java_rep = generate_file_report("public class Foo {}\n", "Foo.java")
        c_rep = generate_file_report("int main() { return 0; }\n", "main.c")

        cuqa_repo_report = generate_repo_report([py_rep, java_rep, c_rep])
        cuqa_repo_report["summary"]["repo_name"] = "polyglot_test"

        rdp_report = _translate_cuqa_to_rdp(cuqa_repo_report)

        assert rdp_report["target"] in ["main.py", "Foo.java", "main.c", "polyglot_test"]
        assert rdp_report["metrics_summary"]["total_files"] == 3
