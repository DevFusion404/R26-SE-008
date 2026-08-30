from __future__ import annotations

from sctva.transformers.java_extract_class import _parse_java_class
from sctva.transformers.java_extract_method import _resolve_targets_with_diagnostics


SPRING_CONTROLLER = '''
package example;

public class AdminController {
    @GetMapping("/admin/products/update")
    public String updateproduct(
        @RequestParam("pid") int id,
        Model model
    ) {
        int first = id + 1;
        int second = first + 2;
        int third = second + 3;
        System.out.println(third);
        return "productsUpdate";
    }
}
'''


def test_parameter_annotation_is_not_parsed_as_method_name():
    model = _parse_java_class(SPRING_CONTROLLER, "AdminController")

    assert model is not None
    assert [method.name for method in model.methods] == ["updateproduct"]
    assert model.methods[0].parameters == ["id", "model"]


def test_extract_method_resolves_annotated_spring_method_by_class_and_name():
    matches, resolution = _resolve_targets_with_diagnostics(
        SPRING_CONTROLLER,
        "updateproduct",
        "AdminController",
        "",
    )

    assert len(matches) == 1
    assert matches[0][1].name == "updateproduct"
    assert resolution["status"] == "success"
    assert resolution["strategy"] == "current_ast_unique_class_method"


def test_annotated_signature_resolves_correct_overload():
    source = SPRING_CONTROLLER.replace(
        "\n}\n",
        '''
    public String updateproduct(String value) {
        return value;
    }
}
''',
    )

    matches, resolution = _resolve_targets_with_diagnostics(
        source,
        "updateproduct",
        "AdminController",
        'public String updateproduct(@RequestParam("pid") int id, Model model)',
    )

    assert len(matches) == 1
    assert resolution["status"] == "success"
    assert resolution["strategy"] in {
        "current_ast_exact_class_method_signature",
        "current_ast_class_method_parameter_types",
    }


def test_true_overload_without_signature_remains_ambiguous():
    source = SPRING_CONTROLLER.replace(
        "\n}\n",
        '''
    public String updateproduct(String value) {
        return value;
    }
}
''',
    )

    matches, resolution = _resolve_targets_with_diagnostics(
        source,
        "updateproduct",
        "AdminController",
        "",
    )

    assert len(matches) == 2
    assert resolution["status"] == "failed"
    assert resolution["reason"] == "AMBIGUOUS_OVERLOADED_METHOD_TARGET"
