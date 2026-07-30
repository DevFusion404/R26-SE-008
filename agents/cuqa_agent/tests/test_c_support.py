"""
test_c_support.py
-----------------
Unit tests for CUQA Agent C language support.

Tests verify:
  - detect_language() correctly identifies .c and .h files
  - parse_source() works for C source and returns expected schema
  - generate_file_report() returns language = "c"
  - All 7 C code smell rules are detected in smelly.c
  - .c and .h file discovery works (extension filter)
  - Existing Python and Java tests still pass
  - Repository-level report retains the correct structure

Run with:
    cd agents/cuqa_agent
    python -m pytest tests/test_c_support.py -v
"""

import os
import sys
import json
import pytest

# ---------------------------------------------------------------------------
# Make src importable
# ---------------------------------------------------------------------------
SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))

from ast_parser import detect_language, parse_source, SUPPORTED_LANGUAGES
from report_generator import generate_file_report, generate_repo_report

SAMPLES = os.path.join(os.path.dirname(__file__), "sample_c_files")

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _read(filename: str) -> str:
    path = os.path.join(SAMPLES, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ===========================================================================
# 1. Language Detection
# ===========================================================================

class TestDetectLanguage:
    def test_dot_c_returns_c(self):
        assert detect_language("file.c") == "c"

    def test_dot_h_returns_c(self):
        assert detect_language("file.h") == "c"

    def test_dot_py_still_python(self):
        assert detect_language("module.py") == "python"

    def test_dot_java_still_java(self):
        assert detect_language("Main.java") == "java"

    def test_unknown_extension(self):
        assert detect_language("data.xml") == "unknown"

    def test_c_in_supported_languages(self):
        assert ".c" in SUPPORTED_LANGUAGES
        assert ".h" in SUPPORTED_LANGUAGES
        assert SUPPORTED_LANGUAGES[".c"] == "c"
        assert SUPPORTED_LANGUAGES[".h"] == "c"


# ===========================================================================
# 2. Extension Discovery (simulates what _find_source_files does)
# ===========================================================================

class TestExtensionDiscovery:
    def test_c_extension_detected(self):
        """Confirm .c files appear in SAMPLES directory."""
        c_files = [f for f in os.listdir(SAMPLES) if f.endswith(".c")]
        assert len(c_files) >= 2, "Expected at least simple.c and smelly.c"

    def test_h_extension_detected(self):
        """Confirm .h files appear in SAMPLES directory."""
        h_files = [f for f in os.listdir(SAMPLES) if f.endswith(".h")]
        assert len(h_files) >= 1, "Expected at least utils.h"


# ===========================================================================
# 3. AST Parsing
# ===========================================================================

class TestParseSource:
    def test_simple_c_parse(self):
        source = _read("simple.c")
        result = parse_source(source, "simple.c")
        assert result["file"] == "simple.c"
        assert result["language"] == "c"
        assert "ast" in result
        assert result["ast"]["type"] == "TranslationUnit"

    def test_simple_c_has_functions(self):
        source = _read("simple.c")
        result = parse_source(source, "simple.c")
        fn_nodes = [
            child for child in result["ast"].get("children", [])
            if child.get("type") == "FunctionDefinition"
        ]
        assert len(fn_nodes) >= 1, "Expected at least one FunctionDefinition node"

    def test_header_parse(self):
        source = _read("utils.h")
        result = parse_source(source, "utils.h")
        assert result["file"] == "utils.h"
        assert result["language"] == "c"
        assert "ast" in result

    def test_parse_does_not_crash_on_empty_source(self):
        result = parse_source("", "empty.c")
        assert result["language"] == "c"
        assert "ast" in result


# ===========================================================================
# 4. Quality Report Schema
# ===========================================================================

class TestFileReportSchema:
    def test_simple_c_report_language(self):
        source = _read("simple.c")
        report = generate_file_report(source, "simple.c")
        assert report["language"] == "c"

    def test_report_has_required_top_level_keys(self):
        source = _read("simple.c")
        report = generate_file_report(source, "simple.c")
        for key in ("file", "language", "metrics", "code_smells", "smell_summary", "quality_score"):
            assert key in report, f"Missing key: {key}"

    def test_metrics_has_required_fields(self):
        source = _read("simple.c")
        report = generate_file_report(source, "simple.c")
        m = report["metrics"]
        for field in ("filename", "lines_of_code", "blank_lines", "comment_lines",
                      "functions", "classes"):
            assert field in m, f"Missing metric field: {field}"

    def test_c_classes_always_zero(self):
        source = _read("simple.c")
        report = generate_file_report(source, "simple.c")
        assert report["metrics"]["classes"] == 0

    def test_c_extra_metrics_present(self):
        source = _read("simple.c")
        report = generate_file_report(source, "simple.c")
        m = report["metrics"]
        # Optional C-specific fields
        assert "include_count" in m
        assert "global_variables" in m
        assert "estimated_cyclomatic_complexity" in m

    def test_smell_summary_has_severity_keys(self):
        source = _read("simple.c")
        report = generate_file_report(source, "simple.c")
        for key in ("high", "medium", "low"):
            assert key in report["smell_summary"]

    def test_quality_score_in_range(self):
        source = _read("simple.c")
        report = generate_file_report(source, "simple.c")
        assert 0.0 <= report["quality_score"] <= 100.0

    def test_header_report_language(self):
        source = _read("utils.h")
        report = generate_file_report(source, "utils.h")
        assert report["language"] == "c"


# ===========================================================================
# 5. Code Smell Detection
# ===========================================================================

class TestCSmells:
    def setup_method(self):
        source = _read("smelly.c")
        self.report = generate_file_report(source, "smelly.c")
        self.smell_types = {s["type"] for s in self.report["code_smells"]}

    def test_long_function_detected(self):
        assert "LongFunction" in self.smell_types, (
            "Expected LongFunction smell for read_user_input()"
        )

    def test_too_many_parameters_detected(self):
        assert "TooManyParameters" in self.smell_types, (
            "Expected TooManyParameters smell for process_data(7 params)"
        )

    def test_deep_nesting_detected(self):
        assert "DeepNesting" in self.smell_types, (
            "Expected DeepNesting smell (5+ nested ifs)"
        )

    def test_magic_number_detected(self):
        assert "MagicNumber" in self.smell_types, (
            "Expected MagicNumber smell (42, 100, 999, etc.)"
        )

    def test_unsafe_function_detected(self):
        assert "UnsafeFunctionUsage" in self.smell_types, (
            "Expected UnsafeFunctionUsage smell (strcpy / gets)"
        )

    def test_global_variable_detected(self):
        assert "GlobalVariable" in self.smell_types, (
            "Expected GlobalVariable smell (g_counter, g_buffer)"
        )

    def test_smell_entries_have_required_fields(self):
        for smell in self.report["code_smells"]:
            for field in ("type", "message", "severity", "entity"):
                assert field in smell, f"Smell entry missing field '{field}': {smell}"

    def test_severity_values_are_valid(self):
        valid = {"high", "medium", "low"}
        for smell in self.report["code_smells"]:
            assert smell["severity"] in valid, (
                f"Invalid severity '{smell['severity']}' in smell: {smell}"
            )

    def test_no_smells_on_clean_file(self):
        source = _read("simple.c")
        report = generate_file_report(source, "simple.c")
        dangerous = [
            s for s in report["code_smells"]
            if s["type"] in ("LongFunction", "UnsafeFunctionUsage", "TooManyParameters")
        ]
        assert len(dangerous) == 0, (
            f"Unexpected smells on clean file: {dangerous}"
        )

    def test_large_header_file_detected(self):
        """Generate a synthetic > 300-line header and confirm smell is raised."""
        big_header = "// Auto-generated header\n" + "#include <stdio.h>\n" * 310
        report = generate_file_report(big_header, "big.h")
        types = {s["type"] for s in report["code_smells"]}
        assert "LargeHeaderFile" in types


# ===========================================================================
# 6. Repo-level Report Structure
# ===========================================================================

class TestRepoReport:
    def test_repo_report_structure(self):
        reports = []
        for fname in ("simple.c", "smelly.c", "utils.h"):
            source = _read(fname)
            reports.append(generate_file_report(source, fname))

        repo = generate_repo_report(reports)
        assert "summary" in repo
        assert "files" in repo

        s = repo["summary"]
        for key in ("files_analyzed", "total_lines_of_code", "total_code_smells",
                    "smell_severity", "average_quality_score"):
            assert key in s, f"Missing repo summary key: {key}"

    def test_repo_files_count(self):
        reports = [
            generate_file_report(_read("simple.c"), "simple.c"),
            generate_file_report(_read("smelly.c"), "smelly.c"),
        ]
        repo = generate_repo_report(reports)
        assert repo["summary"]["files_analyzed"] == 2

    def test_repo_smell_severity_keys(self):
        reports = [generate_file_report(_read("smelly.c"), "smelly.c")]
        repo = generate_repo_report(reports)
        sev = repo["summary"]["smell_severity"]
        for key in ("high", "medium", "low"):
            assert key in sev


# ===========================================================================
# 7. Existing Python / Java support is unbroken
# ===========================================================================

class TestBackwardCompatibility:
    def test_python_detect_language(self):
        assert detect_language("script.py") == "python"

    def test_java_detect_language(self):
        assert detect_language("Main.java") == "java"

    def test_python_parse_source(self):
        py_source = "def hello():\n    return 42\n"
        result = parse_source(py_source, "hello.py")
        assert result["language"] == "python"
        assert "ast" in result

    def test_java_parse_source(self):
        java_source = (
            "public class Foo {\n"
            "    public void bar() {}\n"
            "}\n"
        )
        result = parse_source(java_source, "Foo.java")
        assert result["language"] == "java"
        assert "ast" in result

    def test_python_report_language(self):
        py_source = "x = 1\n"
        report = generate_file_report(py_source, "x.py")
        assert report["language"] == "python"

    def test_java_report_language(self):
        java_source = "public class A {}\n"
        report = generate_file_report(java_source, "A.java")
        assert report["language"] == "java"
