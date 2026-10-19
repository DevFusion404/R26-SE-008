"""
unit/test_ml_features.py
-------------------------
Unit tests for the Java feature extractor used by the ML smell detector.
"""

import pytest
# pyrefly: ignore [missing-import]
from ml_detector.java_features import (
    CLASS_FEATURES,
    METHOD_FEATURES,
    JAVALANG_AVAILABLE,
    JavaFeatureError,
    extract_entities,
    feature_vector,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(not JAVALANG_AVAILABLE, reason="javalang not installed"),
]

ORDER_SERVICE = '''\
package shop;

import java.util.List;

/** Service documentation. */
@Deprecated
public class OrderService extends BaseService implements Auditable {
    private static final int LIMIT = 3;
    private int total;
    private Repo repo;

    @SuppressWarnings("unchecked")
    public OrderService(Repo repo) { this.repo = repo; }

    @Override
    public
    int getTotal() { return total; }

    public void setTotal(int total) { this.total = total; }

    void process(Order order, List<String> tags) {
        // comment only line
        if (order.getAmount() > LIMIT && this.total < 10) {
            order.customer().address().city();
            audit();
        } else if (total == 2) {
            Math.max(1, 2);
        }
        for (String tag : tags) {
            order.note = tag.trim();
        }
        repo.save(order);
    }

    void audit() { }

    static class Line { int qty; }
}
'''


def _by_name(entities, name, kind):
    return next(e for e in entities if e.name == name and e.kind == kind)


class TestEntityExtraction:
    def test_classes_and_methods_are_found(self):
        entities = extract_entities(ORDER_SERVICE)
        found = {(e.kind, e.name) for e in entities}
        assert ("class", "OrderService") in found
        assert ("class", "Line") in found
        assert ("method", "process") in found
        assert ("method", "getTotal") in found
        assert ("method", "OrderService") in found  # constructor

    def test_span_starts_at_annotation_and_ends_at_closing_brace(self):
        entities = extract_entities(ORDER_SERVICE)
        cls = _by_name(entities, "OrderService", "class")
        assert cls.start_line == 6  # @Deprecated, not the javadoc line above it
        assert cls.end_line == len(ORDER_SERVICE.splitlines())

        getter = _by_name(entities, "getTotal", "method")
        assert (getter.start_line, getter.end_line) == (15, 17)

    def test_parent_and_type_kind(self):
        entities = extract_entities(ORDER_SERVICE)
        assert _by_name(entities, "process", "method").parent == "OrderService"
        assert _by_name(entities, "Line", "class").parent == "OrderService"
        assert _by_name(entities, "OrderService", "method").type_kind == "constructor"

    def test_feature_names_match_the_declared_lists(self):
        entities = extract_entities(ORDER_SERVICE)
        assert set(_by_name(entities, "process", "method").features) == set(METHOD_FEATURES)
        assert set(_by_name(entities, "OrderService", "class").features) == set(CLASS_FEATURES)

    def test_interface_and_enum_kinds(self):
        src = "interface Shape { double area(); }\nenum Color { RED, GREEN; }\n"
        kinds = {e.name: e.type_kind for e in extract_entities(src) if e.kind == "class"}
        assert kinds == {"Shape": "interface", "Color": "enum"}

    def test_fingerprint_only_on_request(self):
        plain = extract_entities(ORDER_SERVICE)
        hashed = extract_entities(ORDER_SERVICE, with_fingerprint=True)
        assert all(e.fingerprint == "" for e in plain)
        assert all(len(e.fingerprint) == 20 for e in hashed)


class TestMethodFeatures:
    def test_complexity_and_nesting(self):
        process = _by_name(extract_entities(ORDER_SERVICE), "process", "method").features
        # 1 + if + && + else-if + for
        assert process["cyclomatic_complexity"] == 5
        # the else-if is the same decision level, not a deeper one
        assert process["max_nesting"] == 1
        assert process["n_loops"] == 1
        assert process["n_params"] == 2

    def test_size_counts_code_and_comment_lines_separately(self):
        process = _by_name(extract_entities(ORDER_SERVICE), "process", "method").features
        assert process["loc"] == 13
        assert process["comment_lines"] == 1
        assert process["sloc"] == 12

    def test_own_and_foreign_access(self):
        process = _by_name(extract_entities(ORDER_SERVICE), "process", "method").features
        assert process["n_own_calls"] == 1                # audit()
        assert process["n_calls_on_params"] == 2          # order.getAmount(), order.customer()
        assert process["n_calls_on_locals"] == 1          # tag.trim()
        assert process["n_calls_on_fields"] == 1          # repo.save()
        assert process["n_calls_on_types"] == 1           # Math.max()
        assert process["n_foreign_field_access"] == 1     # order.note
        assert process["n_foreign_accessor_calls"] == 1   # order.getAmount()
        assert process["max_chain_length"] == 3           # customer().address().city()
        assert 0.0 < process["foreign_access_ratio"] < 1.0

    def test_parameter_shadowing_a_field_is_not_an_own_access(self):
        setter = _by_name(extract_entities(ORDER_SERVICE), "setTotal", "method").features
        assert setter["n_own_field_access"] == 1  # this.total, the bare `total` is the parameter

    def test_method_without_body(self):
        src = "abstract class A { abstract int size(); }"
        size = _by_name(extract_entities(src), "size", "method").features
        assert size["has_body"] == 0
        assert size["cyclomatic_complexity"] == 1


class TestClassFeatures:
    def test_member_counts(self):
        cls = _by_name(extract_entities(ORDER_SERVICE), "OrderService", "class").features
        assert cls["n_methods"] == 4
        assert cls["n_constructors"] == 1
        assert cls["n_fields"] == 3
        assert cls["n_inner_types"] == 1
        assert cls["has_superclass"] == 1
        assert cls["n_interfaces"] == 1
        assert cls["file_n_imports"] == 1

    def test_accessors(self):
        cls = _by_name(extract_entities(ORDER_SERVICE), "OrderService", "class").features
        assert cls["n_getters"] == 1
        assert cls["n_setters"] == 1
        assert cls["accessor_ratio"] == pytest.approx(0.5)

    def test_wmc_is_the_sum_of_method_complexities(self):
        cls = _by_name(extract_entities(ORDER_SERVICE), "OrderService", "class").features
        # constructor 1 + getTotal 1 + setTotal 1 + process 5 + audit 1
        assert cls["wmc"] == 9
        assert cls["max_cc"] == 5

    def test_cohesion_of_a_pure_data_class(self):
        src = (
            "class Point {\n"
            "    private int x; private int y;\n"
            "    int getX() { return x; }\n"
            "    void setX(int v) { x = v; }\n"
            "    int getY() { return y; }\n"
            "}\n"
        )
        cls = _by_name(extract_entities(src), "Point", "class").features
        assert cls["accessor_ratio"] == pytest.approx(1.0)
        assert cls["n_non_accessor_methods"] == 0
        # getX/setX share x, getY shares nothing with them: 1 of 3 pairs
        assert cls["tcc"] == pytest.approx(1 / 3)
        assert cls["methods_using_fields_ratio"] == pytest.approx(1.0)

    def test_nested_flag(self):
        entities = extract_entities(ORDER_SERVICE)
        assert _by_name(entities, "Line", "class").features["is_nested"] == 1
        assert _by_name(entities, "OrderService", "class").features["is_nested"] == 0


class TestRobustness:
    def test_malformed_source_raises_feature_error(self):
        with pytest.raises(JavaFeatureError):
            extract_entities("public class Broken { void x( { ")

    def test_empty_source_returns_no_entities(self):
        assert extract_entities("") == []

    def test_very_long_expression_does_not_crash_the_interpreter(self):
        terms = " + ".join(f'"s{i}"' for i in range(3000))
        src = f"class Big {{ String build() {{ return {terms}; }} }}"
        try:
            entities = extract_entities(src)
        except JavaFeatureError:
            return  # the parser gave up cleanly, which is acceptable
        assert _by_name(entities, "build", "method").features["n_literals"] == 3000

    def test_feature_vector_follows_the_requested_order(self):
        process = _by_name(extract_entities(ORDER_SERVICE), "process", "method")
        vector = feature_vector(process, ["n_params", "missing_feature", "n_loops"])
        assert vector == [2.0, 0.0, 1.0]
