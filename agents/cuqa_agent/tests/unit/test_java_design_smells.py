"""
unit/test_java_design_smells.py
-------------------------------
Unit tests for the Java rules added next to Long Method / Large Class:
the metric rules in java_rules.py and the tree rules in report_generator.py.
"""

import pytest
# pyrefly: ignore [missing-import]
import java_rules
# pyrefly: ignore [missing-import]
from report_generator import generate_file_report
# pyrefly: ignore [missing-import]
from java_ast_parser import JAVALANG_AVAILABLE

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(not JAVALANG_AVAILABLE, reason="javalang not installed"),
]


def _smells(source: str, smell_type: str, name: str = "Sample.java") -> list[dict]:
    report = generate_file_report(source, name, detector="off")
    return [s for s in report["code_smells"] if s["type"] == smell_type]


def _method(body: str, params: str = "int a", extra: str = "") -> str:
    return f"public class Sample {{\n{extra}\n    public int run({params}) {{\n{body}\n    }}\n}}\n"


class TestMetricRules:
    def test_complex_method_over_ten(self):
        branches = "\n".join(f"        if (a == {i}) {{ return {i}; }}" for i in range(11))
        found = _smells(_method(branches + "\n        return 0;"), "ComplexMethod")
        assert [(s["entity"], s["cyclomatic_complexity"], s["detected_by"]) for s in found] == [("run", 12, "rule")]

    def test_ten_is_not_complex(self):
        branches = "\n".join(f"        if (a == {i}) {{ return {i}; }}" for i in range(9))
        assert _smells(_method(branches + "\n        return 0;"), "ComplexMethod") == []

    def test_deep_nesting(self):
        body = ("        for (int i = 0; i < a; i++) {\n"
                "            if (i > 1) { while (a > 0) { if (a > 2) { a--; } } }\n"
                "        }\n        return a;")
        found = _smells(_method(body), "DeepNesting")
        assert [(s["entity"], s["nesting_depth"]) for s in found] == [("run", 4)]

    def test_else_if_chain_is_not_nesting(self):
        body = ("        if (a == 1) { return 1; } else if (a == 2) { return 2; }\n"
                "        else if (a == 3) { return 3; } else if (a == 4) { return 4; }\n        return 0;")
        assert _smells(_method(body), "DeepNesting") == []

    def test_feature_envy(self):
        body = "        return o.getA() + o.getB() + o.getC() + o.getD() + o.getE() + o.getF();"
        found = _smells(_method(body, params="Order o"), "FeatureEnvy")
        assert [s["entity"] for s in found] == ["run"]

    def test_method_using_its_own_fields_is_not_envious(self):
        body = ("        return o.getA() + o.getB() + o.getC() + o.getD() + o.getE() + o.getF()"
                " + x + y + z + w + v + u;")
        extra = "    private int x, y, z, w, v, u;"
        assert _smells(_method(body, params="Order o", extra=extra), "FeatureEnvy") == []

    def test_data_class(self):
        source = (
            "public class Point {\n"
            "    private int x; private int y;\n"
            "    public int getX() { return x; }\n    public int getY() { return y; }\n"
            "    public void setX(int x) { this.x = x; }\n    public void setY(int y) { this.y = y; }\n"
            "}\n"
        )
        found = _smells(source, "DataClass", "Point.java")
        assert [(s["entity"], s["method_count"]) for s in found] == [("Point", 4)]

    def test_class_with_behaviour_is_not_a_data_class(self):
        source = (
            "public class Account {\n    private int balance;\n"
            "    public int getBalance() { return balance; }\n"
            "    public void deposit(int v) { if (v > 0) { balance += v; } }\n"
            "    public void withdraw(int v) { if (v <= balance) { balance -= v; } }\n"
            "    public boolean isEmpty() { return balance == 0; }\n}\n"
        )
        assert _smells(source, "DataClass", "Account.java") == []

    def test_unparsable_source_gives_nothing(self):
        assert java_rules.detect_metric_smells("public class Broken { void x( { ") == []


class TestTreeRules:
    def test_getter_chain_is_a_message_chain(self):
        found = _smells(_method("        return o.getCustomer().getAddress().getZip();", params="Order o"),
                        "MessageChains")
        assert [(s["entity"], s["chain_length"]) for s in found] == [("run", 3)]

    def test_fluent_calls_are_not_message_chains(self):
        body = ('        return names.stream().filter(s -> !s.isEmpty()).map(String::trim)'
                '.findFirst().orElse("").length();')
        assert _smells(_method(body, params="java.util.List<String> names"), "MessageChains") == []

    def test_switch_with_five_labels(self):
        cases = "\n".join(f"            case {i}: return {i};" for i in range(1, 6))
        body = f"        switch (a) {{\n{cases}\n            default: return 0;\n        }}"
        assert [s["entity"] for s in _smells(_method(body), "SwitchStatements")] == ["run"]

    def test_switch_with_four_labels_is_fine(self):
        cases = "\n".join(f"            case {i}: return {i};" for i in range(1, 5))
        body = f"        switch (a) {{\n{cases}\n            default: return 0;\n        }}"
        assert _smells(_method(body), "SwitchStatements") == []

    def test_long_else_if_chain_is_reported_once(self):
        chain = " else ".join(f"if (a > {i}) {{ return {i}; }}" for i in range(5))
        found = _smells(_method(f"        {chain}\n        return 0;"), "SwitchStatements")
        assert len(found) == 1
        assert "4 else-if branches" in found[0]["message"]

    def test_unused_private_members_are_dead_code(self):
        source = (
            "public class Sample {\n"
            "    private static final long serialVersionUID = 1L;\n"
            "    private int unused;\n    private int used;\n"
            "    public int run() { helper(); Runnable r = this::byReference; return used; }\n"
            "    private void helper() { }\n    private void byReference() { }\n"
            "    private void neverCalled() { }\n"
            "    private void readObject(java.io.ObjectInputStream in) { }\n"
            "}\n"
        )
        assert sorted(s["entity"] for s in _smells(source, "DeadCode")) == ["neverCalled", "unused"]

    def test_lombok_fields_are_not_dead_code(self):
        source = "@Data\npublic class Dto {\n    private String name;\n}\n"
        assert _smells(source, "DeadCode", "Dto.java") == []

    def test_duplicate_bodies(self):
        body = "        log(a); store(a); notify(a);"
        source = (
            "public class Sample {\n"
            f"    public void save(int a) {{\n{body}\n    }}\n"
            f"    public void update(int a) {{\n{body}\n    }}\n"
            "    void log(int a) { }\n    void store(int a) { }\n    void notify(int a) { }\n}\n"
        )
        found = _smells(source, "DuplicateCode")
        assert [s["duplicate_group"] for s in found] == [["save", "update"]]

    def test_data_clumps(self):
        source = (
            "public class Sample {\n"
            "    void create(String name, String email, int age) { }\n"
            "    void change(String name, String email, int age, boolean admin) { }\n}\n"
        )
        found = _smells(source, "DataClumps")
        assert [s["clump_parameters"] for s in found] == [["age", "email", "name"]]

    def test_public_static_mutable_field(self):
        source = (
            "public class Sample {\n    public static int counter = 0;\n"
            "    public static final int LIMIT = 3;\n    private static int hidden;\n}\n"
            "interface Settings { int SIZE = 4; }\n"
        )
        assert [s["entity"] for s in _smells(source, "GlobalVariable")] == ["counter"]
