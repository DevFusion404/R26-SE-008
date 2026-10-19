"""
unit/test_ml_detector.py
-------------------------
Unit tests for the ML smell detector and its integration into the quality report.
"""

import pytest
# pyrefly: ignore [missing-import]
import report_generator
# pyrefly: ignore [missing-import]
from report_generator import generate_file_report, generate_repo_report
# pyrefly: ignore [missing-import]
from ml_detector import detector
# pyrefly: ignore [missing-import]
from ml_detector.java_features import JAVALANG_AVAILABLE
# pyrefly: ignore [missing-import]
from ml_detector.model import SmellModelError

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(not JAVALANG_AVAILABLE, reason="javalang not installed"),
]

CLEAN_JAVA = '''\
public class Greeter {
    private final String name;

    public Greeter(String name) { this.name = name; }

    public String greet() { return "Hello " + name; }
}
'''


def _long_method_java(branches: int = 60) -> str:
    """A class with one very long, branch-heavy method."""
    body = []
    for i in range(branches):
        body.append(f"        if (input > {i} && flag{i % 3}) {{")
        body.append(f"            total += helper{i % 5}(input, {i});")
        body.append(f"            for (int k{i} = 0; k{i} < {i}; k{i}++) {{ total -= k{i}; }}")
        body.append("        }")
    return (
        "public class Calculator {\n"
        "    private boolean flag0, flag1, flag2;\n"
        "    public int compute(int input) {\n"
        "        int total = 0;\n"
        + "\n".join(body) + "\n"
        "        return total;\n"
        "    }\n"
        + "".join(f"    private int helper{i}(int a, int b) {{ return a + b; }}\n" for i in range(5))
        + "}\n"
    )


class TestDetectSmells:
    def test_clean_class_has_no_ml_smells(self):
        result = detector.detect_smells(CLEAN_JAVA)
        assert result["status"] == "ok"
        assert result["smells"] == []
        assert result["entities_scored"] == 3  # class, constructor, method
        assert result["model_version"]

    def test_long_method_is_detected(self):
        result = detector.detect_smells(_long_method_java())
        found = [s for s in result["smells"] if s["type"] == "LongMethod"]
        assert [s["entity"] for s in found] == ["compute"]
        smell = found[0]
        assert smell["detected_by"] == "ml"
        assert smell["severity"] == "high"
        assert 0.0 < smell["confidence"] <= 1.0
        assert smell["start_line"] == smell["line"] == 3
        assert smell["end_line"] > smell["start_line"]
        assert smell["cyclomatic_complexity"] > 100
        assert smell["evidence"]

    def test_short_helpers_are_not_flagged(self):
        result = detector.detect_smells(_long_method_java())
        flagged = {s["entity"] for s in result["smells"] if s["type"] == "LongMethod"}
        assert not any(name.startswith("helper") for name in flagged)

    def test_malformed_source_reports_parse_failure(self):
        result = detector.detect_smells("public class Broken { void x( { ")
        assert result["status"] == "parse_failed"
        assert result["smells"] == []

    def test_missing_model_is_reported_not_raised(self, monkeypatch):
        def fail():
            raise SmellModelError("model file not found")
        monkeypatch.setattr(detector, "load_model", fail)
        result = detector.detect_smells(CLEAN_JAVA)
        assert result["status"] == "model_unavailable"
        assert result["smells"] == []
        assert detector.is_available() is False
        assert detector.model_info()["available"] is False

    def test_unexpected_extractor_error_is_contained(self, monkeypatch):
        def boom(source):
            raise ValueError("unexpected")
        monkeypatch.setattr(detector, "extract_entities", boom)
        result = detector.detect_smells(CLEAN_JAVA)
        assert result["status"] == "analysis_failed"
        assert result["smells"] == []

    def test_enums_are_not_scored(self):
        result = detector.detect_smells("enum Color { RED, GREEN; }")
        assert result["status"] == "ok"
        assert result["entities_scored"] == 0

    def test_experimental_smells_are_off_by_default(self):
        info = detector.model_info()
        disabled = {name for name, s in info["smells"].items() if not s["enabled"]}
        assert detector.learned_smell_types() == set(info["smells"]) - disabled
        assert detector.learned_smell_types(include_experimental=True) == set(info["smells"])


class TestDetectionMode:
    def test_default_is_rules(self, monkeypatch):
        monkeypatch.delenv("CUQA_SMELL_DETECTOR", raising=False)
        assert detector.resolve_mode() == "rules"

    def test_environment_variable(self, monkeypatch):
        monkeypatch.setenv("CUQA_SMELL_DETECTOR", "Hybrid")
        assert detector.resolve_mode() == "hybrid"

    def test_explicit_request_wins_over_environment(self, monkeypatch):
        monkeypatch.setenv("CUQA_SMELL_DETECTOR", "hybrid")
        assert detector.resolve_mode("ml") == "ml"

    def test_unknown_value_falls_back_to_rules(self, monkeypatch):
        monkeypatch.setenv("CUQA_SMELL_DETECTOR", "neural")
        assert detector.resolve_mode() == "rules"


class TestMerge:
    RULE_LONG = {"type": "LongMethod", "entity": "compute", "line": 4, "severity": "high"}
    RULE_MAGIC = {"type": "MagicNumber", "entity": None, "line": 9, "severity": "low"}
    ML_LONG = {"type": "LongMethod", "entity": "compute", "line": 3, "start_line": 3,
               "end_line": 40, "severity": "high", "detected_by": "ml", "confidence": 0.9}
    ML_DATA = {"type": "DataClass", "entity": "Dto", "line": 50, "start_line": 50,
               "end_line": 70, "severity": "low", "detected_by": "ml", "confidence": 0.7}

    def test_hybrid_folds_agreement_into_one_entry(self):
        merged = detector.merge_with_rule_smells(
            [self.RULE_LONG, self.RULE_MAGIC], [self.ML_LONG, self.ML_DATA], "hybrid"
        )
        assert [s["type"] for s in merged] == ["LongMethod", "MagicNumber", "DataClass"]
        assert merged[0]["detected_by"] == "rule+ml"
        assert merged[0]["confidence"] == 0.9
        assert merged[1]["detected_by"] == "rule"
        assert merged[2]["detected_by"] == "ml"

    def test_hybrid_keeps_same_named_method_outside_the_span_separate(self):
        other = dict(self.RULE_LONG, line=200)
        merged = detector.merge_with_rule_smells([other], [self.ML_LONG], "hybrid")
        assert [s["detected_by"] for s in merged] == ["rule", "ml"]

    def test_ml_mode_replaces_learned_types_only(self):
        merged = detector.merge_with_rule_smells(
            [self.RULE_LONG, self.RULE_MAGIC], [self.ML_DATA], "ml"
        )
        # the model did not flag compute, so the rule's LongMethod is dropped
        assert [s["type"] for s in merged] == ["MagicNumber", "DataClass"]

    def test_inputs_are_not_modified(self):
        rule = dict(self.RULE_LONG)
        detector.merge_with_rule_smells([rule], [self.ML_LONG], "hybrid")
        assert rule == self.RULE_LONG


class TestReportIntegration:
    def test_default_mode_leaves_rule_output_unchanged(self, monkeypatch):
        monkeypatch.delenv("CUQA_SMELL_DETECTOR", raising=False)
        source = _long_method_java()
        with_ml = generate_file_report(source, "Calculator.java")

        monkeypatch.setattr(report_generator, "_ml_detector", None)
        without_ml = generate_file_report(source, "Calculator.java")

        assert with_ml["detection_mode"] == "rules"
        assert with_ml["ml_analysis"]["status"] == "ok"
        assert "ml_analysis" not in without_ml
        for key in without_ml:
            assert with_ml[key] == without_ml[key]

    def test_off_mode_skips_the_model_completely(self, monkeypatch):
        source = _long_method_java()
        off = generate_file_report(source, "Calculator.java", detector="off")

        monkeypatch.setattr(report_generator, "_ml_detector", None)
        before_ml = generate_file_report(source, "Calculator.java")

        assert off == before_ml
        assert "ml_analysis" not in off

    def test_off_mode_does_not_call_the_model(self, monkeypatch):
        def fail(source):
            raise AssertionError("the model must not run in off mode")
        monkeypatch.setattr(detector, "detect_smells", fail)
        monkeypatch.setenv("CUQA_SMELL_DETECTOR", "off")
        report = generate_file_report(CLEAN_JAVA, "Greeter.java")
        assert "detection_mode" not in report

    def test_ml_block_carries_categories(self):
        report = generate_file_report(_long_method_java(), "Calculator.java")
        smell = report["ml_analysis"]["smells"][0]
        assert smell["category"] == "Bloaters"
        assert smell["category_priority"] == "critical"

    def test_hybrid_mode_merges_into_code_smells(self):
        report = generate_file_report(_long_method_java(), "Calculator.java", detector="hybrid")
        assert report["detection_mode"] == "hybrid"
        long_methods = [s for s in report["code_smells"] if s["type"] == "LongMethod"]
        assert len(long_methods) == 1  # rule and model agree on compute()
        assert long_methods[0]["detected_by"] == "rule+ml"
        assert all("detected_by" in s for s in report["code_smells"])

    def test_ml_mode_keeps_unlearned_rule_smells(self):
        report = generate_file_report(_long_method_java(), "Calculator.java", detector="ml")
        assert report["detection_mode"] == "ml"
        by_source = {s["type"]: s["detected_by"] for s in report["code_smells"]}
        assert by_source["LongMethod"] == "ml"
        assert by_source["MagicNumber"] == "rule"

    def test_environment_variable_selects_mode(self, monkeypatch):
        monkeypatch.setenv("CUQA_SMELL_DETECTOR", "hybrid")
        report = generate_file_report(_long_method_java(), "Calculator.java")
        assert report["detection_mode"] == "hybrid"

    def test_unparsable_java_falls_back_to_rules(self):
        report = generate_file_report("public class Broken { void x( { ", "Broken.java", detector="ml")
        assert report["detection_mode"] == "rules"
        assert report["ml_analysis"]["status"] == "parse_failed"

    def test_python_and_c_reports_are_untouched(self):
        for source, name in (("def f():\n    return 1\n", "a.py"), ("int f(void) { return 1; }\n", "a.c")):
            report = generate_file_report(source, name, detector="hybrid")
            assert "ml_analysis" not in report
            assert "detection_mode" not in report

    def test_repository_summary_counts_ml_smells(self):
        reports = [
            generate_file_report(_long_method_java(), "Calculator.java"),
            generate_file_report(CLEAN_JAVA, "Greeter.java"),
            generate_file_report("def f():\n    return 1\n", "a.py"),
        ]
        summary = generate_repo_report(reports)["summary"]
        assert summary["ml_code_smells"]["files_scored"] == 2
        assert summary["ml_code_smells"]["by_type"]["LongMethod"] == 1
        assert summary["ml_code_smells"]["total"] == sum(summary["ml_code_smells"]["by_type"].values())

    def test_repository_summary_without_java_has_no_ml_block(self):
        reports = [generate_file_report("def f():\n    return 1\n", "a.py")]
        assert "ml_code_smells" not in generate_repo_report(reports)["summary"]
