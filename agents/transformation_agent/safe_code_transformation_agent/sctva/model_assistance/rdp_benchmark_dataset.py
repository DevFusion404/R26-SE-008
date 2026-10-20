"""Reproducible RDP-aligned datasets for Qwen transformation evaluation."""

from __future__ import annotations

import csv
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .benchmark import ModelBenchmarkCase


RDP_REFACTORINGS: tuple[str, ...] = (
    "Collapse Hierarchy",
    "Encapsulate Variable",
    "Extract Class",
    "Extract Method",
    "Extract Subclass",
    "Hide Delegate",
    "Inline Class",
    "Introduce Constant",
    "Introduce Facade",
    "Introduce Parameter Object",
    "Move Method",
    "Pull Up Method",
    "Remove Dead Code",
    "Rename Method",
    "Replace Bare Except with Specific Exception",
    "Replace Conditional with Polymorphism",
    "Replace Data Value with Object",
    "Replace Nested Conditional with Guard Clauses",
    "Replace Parameter with Method Call",
    "Replace Temp with Query",
    "Replace Unsafe Function",
)


@dataclass(frozen=True)
class _Scenario:
    refactoring: str
    action_by_language: dict[str, str]


# These are RDP techniques, not SCTVA's internal bookkeeping actions such as
# noop, fault injection, or Java parameter-object call-site updates.
_SCENARIOS: tuple[_Scenario, ...] = (
    _Scenario("Collapse Hierarchy", {"java": "collapse_hierarchy"}),
    _Scenario("Encapsulate Variable", {"c": "encapsulate_c_variable"}),
    _Scenario("Extract Class", {
        "java": "extract_java_class", "python": "extract_python_class",
        "c": "extract_c_component",
    }),
    _Scenario("Extract Method", {
        "java": "extract_method", "python": "extract_method", "c": "extract_method",
    }),
    _Scenario("Extract Subclass", {"java": "extract_subclass"}),
    _Scenario("Hide Delegate", {"java": "hide_delegate", "python": "hide_delegate"}),
    _Scenario("Inline Class", {
        "java": "inline_java_class", "python": "inline_python_class",
    }),
    _Scenario("Introduce Constant", {
        "java": "introduce_constant", "python": "introduce_constant", "c": "introduce_constant",
    }),
    _Scenario("Introduce Facade", {"java": "introduce_facade"}),
    _Scenario("Introduce Parameter Object", {
        "java": "introduce_java_parameter_object",
        "python": "introduce_python_parameter_object",
        "c": "introduce_c_parameter_object",
    }),
    _Scenario("Move Method", {
        "java": "move_java_method", "python": "move_python_method", "c": "move_c_function",
    }),
    _Scenario("Pull Up Method", {"java": "pull_up_method"}),
    _Scenario("Remove Dead Code", {
        "java": "remove_dead_code", "python": "remove_dead_code", "c": "remove_dead_code",
    }),
    _Scenario("Rename Method", {"java": "rename_method", "python": "rename_method"}),
    _Scenario("Replace Bare Except with Specific Exception", {
        "python": "narrow_exception_handler",
    }),
    _Scenario("Replace Conditional with Polymorphism", {
        "java": "replace_conditional_with_polymorphism",
        "python": "replace_conditional_with_polymorphism",
    }),
    _Scenario("Replace Data Value with Object", {
        "java": "replace_data_value_with_object",
    }),
    _Scenario("Replace Nested Conditional with Guard Clauses", {
        "python": "replace_nested_conditional_with_guard_clauses",
        "c": "replace_nested_conditional_with_guard_clauses",
    }),
    _Scenario("Replace Parameter with Method Call", {
        "java": "replace_parameter_with_method_call",
        "c": "replace_parameter_with_method_call",
    }),
    _Scenario("Replace Temp with Query", {
        "java": "replace_temp_with_query", "c": "replace_temp_with_query",
    }),
    _Scenario("Replace Unsafe Function", {"c": "replace_unsafe_function"}),
)


def rdp_action_coverage() -> dict[str, tuple[str, ...]]:
    """Return the explicit language coverage for every RDP refactoring."""

    return {
        scenario.refactoring: tuple(sorted(scenario.action_by_language))
        for scenario in _SCENARIOS
    }


def _line_of(source: str, text: str) -> int:
    for number, line in enumerate(source.splitlines(), start=1):
        if text in line:
            return number
    raise ValueError(f"Benchmark source marker not found: {text}")


def _java_source(token: str, literal: int) -> str:
    return f"""class Base{token} {{
    protected int baseValue() {{ return 1; }}
}}

class Helper{token} {{
    int apply(int value) {{ return value * 2; }}
}}

class Account{token} extends Base{token} {{
    private int amount = {literal};
    private final Helper{token} helper = new Helper{token}();

    public int calculate(int input, int tax, int discount) {{
        int subtotal = input + tax;
        int adjusted = subtotal - discount;
        int total = adjusted + baseValue();
        return total;
    }}

    public int process(int value, int tax, int discount, int fee) {{
        return value + tax - discount + fee;
    }}

    public int delegated(int value) {{ return helper.apply(value); }}

    public int fee(String kind) {{
        if ("premium".equals(kind)) {{ return amount * 2; }}
        return amount;
    }}

    public int unused{token}() {{ return -1; }}
}}

class Child{token} extends Base{token} {{
    public int shared{token}(int value) {{ return value + baseValue(); }}
}}
"""


def _python_source(token: str, literal: int) -> str:
    return f"""class Base{token}:
    def base_value(self):
        return 1


class Helper{token}:
    def apply(self, value):
        return value * 2


class Account{token}(Base{token}):
    def __init__(self):
        self.amount = {literal}
        self.helper = Helper{token}()

    def calculate(self, value, tax, discount):
        subtotal = value + tax
        adjusted = subtotal - discount
        total = adjusted + self.base_value()
        return total

    def process(self, value, tax, discount, fee):
        return value + tax - discount + fee

    def delegated(self, value):
        return self.helper.apply(value)

    def category_fee(self, kind):
        if kind == "premium":
            return self.amount * 2
        return self.amount

    def nested(self, value):
        if value > 0:
            if value < 100:
                return value
        return 0

    def parse(self, text):
        try:
            return int(text)
        except:
            return 0

    def unused_{token}(self):
        return -1


class Child{token}(Base{token}):
    def shared_{token}(self, value):
        return value + self.base_value()
"""


def _c_source(token: str, literal: int) -> str:
    return f"""#include <string.h>

int shared_{token} = 1;

int helper_{token}(int value) {{
    return value * 2;
}}

int calculate_{token}(int value, int tax, int discount) {{
    int subtotal = value + tax;
    int adjusted = subtotal - discount;
    if (adjusted > 0) {{
        if (adjusted < 100) {{
            return adjusted + {literal};
        }}
    }}
    return 0;
}}

int process_{token}(int value, int tax, int discount, int fee) {{
    return value + tax - discount + fee;
}}

void copy_{token}(char *destination, const char *source) {{
    strcpy(destination, source);
}}

static int unused_{token}(void) {{
    return -1;
}}
"""


def _source(language: str, token: str, literal: int) -> str:
    if language == "java":
        return _java_source(token, literal)
    if language == "python":
        return _python_source(token, literal)
    return _c_source(token, literal)


def _parameters(
    language: str,
    action_type: str,
    source: str,
    token: str,
    literal: int,
) -> dict[str, Any]:
    account = f"Account{token}"
    helper = f"Helper{token}"
    base = f"Base{token}"
    child = f"Child{token}"
    calculate = f"calculate_{token}" if language == "c" else "calculate"
    process = f"process_{token}" if language == "c" else "process"

    common: dict[str, Any] = {
        "source_method": calculate,
        "method": calculate,
        "source_class": account,
    }
    if action_type == "collapse_hierarchy":
        return {"source_class": child, "parent_class": base}
    if action_type in {"encapsulate_variable", "encapsulate_c_variable"}:
        return {
            "variable_name": f"shared_{token}", "getter_name": f"get_shared_{token}",
            "setter_name": f"set_shared_{token}",
            "source_line": _line_of(source, f"int shared_{token}"),
        }
    if action_type in {"extract_class", "extract_java_class", "extract_python_class"}:
        return {
            "source_class": account, "new_class_name": f"AccountDetails{token}",
            "methods_to_extract": ["delegated"],
        }
    if action_type == "extract_c_component":
        return {
            **common, "destination_file": f"calculation_{token}.c",
            "functions_to_extract": [calculate],
        }
    if action_type == "extract_method":
        start_marker = "int subtotal" if language != "python" else "subtotal ="
        end_marker = f"return adjusted + {literal};" if language == "c" else "return total"
        return {
            **common,
            "start_line": _line_of(source, start_marker),
            "end_line": _line_of(source, end_marker),
            "new_method_name": f"calculate_core_{token}",
        }
    if action_type == "extract_subclass":
        return {"source_class": account, "new_subclass_name": f"PremiumAccount{token}"}
    if action_type == "hide_delegate":
        return {"source_class": account, "method": "delegated", "source_method": "delegated"}
    if action_type in {"inline_class", "inline_java_class", "inline_python_class"}:
        return {"source_class": account, "class_to_inline": helper, "target_class": helper}
    if action_type == "introduce_constant":
        marker = f"return adjusted + {literal};" if language == "c" else (
            f"self.amount = {literal}" if language == "python" else f"private int amount = {literal};"
        )
        params: dict[str, Any] = {
            "literal_value": literal,
            "constant_name": f"DEFAULT_AMOUNT_{token.upper()}",
            "source_line": _line_of(source, marker),
        }
        if language != "c":
            params["source_class"] = account
            params["field_name"] = "amount" if language == "java" else ""
            params["source_method"] = "__init__" if language == "python" else ""
        else:
            params["source_method"] = calculate
        return params
    if action_type == "introduce_facade":
        return {"source_class": account, "facade_class_name": f"AccountFacade{token}"}
    if action_type in {
        "introduce_parameter_object", "introduce_java_parameter_object",
        "introduce_python_parameter_object", "introduce_c_parameter_object",
    }:
        return {
            "source_class": account, "source_method": process, "method": process,
            "parameter_object_name": f"ProcessParams{token}", "parameter_name": "params",
        }
    if action_type in {"move_java_method", "move_python_method"}:
        return {
            "source_class": account, "destination_class": helper,
            "source_method": "delegated", "method": "delegated",
            "destination_parameter": "helper",
        }
    if action_type == "move_c_function":
        return {
            "source_method": f"helper_{token}", "method": f"helper_{token}",
            "destination_file": f"helpers_{token}.c",
        }
    if action_type == "pull_up_method":
        return {
            "source_class": child, "target_class": base, "destination_class": base,
            "source_method": f"shared{token}", "method": f"shared{token}",
        }
    if action_type == "remove_dead_code":
        dead_name = f"unused_{token}" if language != "java" else f"unused{token}"
        return {
            "source_class": account if language != "c" else "",
            "source_method": dead_name, "method": dead_name,
            "target_name": dead_name, "target_kind": "function" if language == "c" else "method",
        }
    if action_type == "rename_method":
        return {
            "source_class": account, "source_method": "calculate", "method": "calculate",
            "old_name": "calculate", "new_name": f"compute_{token.lower()}",
        }
    if action_type == "narrow_exception_handler":
        return {
            "source_class": account, "source_method": "parse", "method": "parse",
            "source_line": _line_of(source, "except:"),
            "target_exception_type": "ValueError", "replacement_exception": "ValueError",
        }
    if action_type == "replace_conditional_with_polymorphism":
        return {"source_class": account, "source_method": "category_fee", "method": "category_fee"}
    if action_type == "replace_data_value_with_object":
        return {"source_class": account, "field_name": "amount", "value_object_name": f"Amount{token}"}
    if action_type in {
        "replace_nested_conditional_with_guard_clauses",
        "replace_conditional_with_guard_clauses",
    }:
        method = f"calculate_{token}" if language == "c" else "nested"
        return {"source_class": account, "source_method": method, "method": method}
    if action_type == "replace_parameter_with_method_call":
        return {
            "source_class": account, "source_method": process, "method": process,
            "parameter_name": "tax", "query_method_name": f"default_tax_{token.lower()}",
            "query_expression": "1",
        }
    if action_type == "replace_temp_with_query":
        return {
            **common, "temp_name": "subtotal",
            "query_method_name": f"query_subtotal_{token.lower()}",
        }
    if action_type == "replace_unsafe_function":
        return {
            "source_method": f"copy_{token}", "method": f"copy_{token}",
            "unsafe_function": "strcpy", "safe_alternative": "strncpy",
            "source_line": _line_of(source, "strcpy("),
        }
    return common


def _negative_parameters(parameters: dict[str, Any], source: str, token: str) -> dict[str, Any]:
    """Create an absent-target counterpart without changing the action type."""

    result = dict(parameters)
    missing = f"Missing{token}"
    name_keys = {
        "source_class", "target_class", "class_name", "class_to_inline",
        "source_method", "target_method", "method", "method_name", "function",
        "function_name", "target_name", "variable_name", "field_name",
        "unsafe_function", "old_name",
    }
    for key in name_keys & set(result):
        if result.get(key):
            result[key] = missing
    if "literal_value" in result:
        result["literal_value"] = 900000 + len(token)
    if "source_line" in result:
        result["source_line"] = len(source.splitlines()) + 100
    if "start_line" in result:
        result["start_line"] = len(source.splitlines()) + 100
    if "end_line" in result:
        result["end_line"] = len(source.splitlines()) + 101
    if "functions_to_extract" in result:
        result["functions_to_extract"] = [missing]
    if "methods_to_extract" in result:
        result["methods_to_extract"] = [missing]
    return result


def generate_rdp_benchmark_cases(
    *,
    seed: int = 26008,
    samples_per_action: int = 1,
    include_negative: bool = True,
    languages: Iterable[str] | None = None,
) -> list[ModelBenchmarkCase]:
    """Generate balanced, reproducible source/action cases for all RDP techniques."""

    if samples_per_action < 1:
        raise ValueError("samples_per_action must be at least 1")
    selected_languages = {
        str(language).strip().lower() for language in (languages or ("java", "python", "c"))
    }
    unsupported_languages = selected_languages - {"java", "python", "c"}
    if unsupported_languages:
        raise ValueError(f"Unsupported benchmark languages: {sorted(unsupported_languages)}")

    rng = random.Random(seed)
    cases: list[ModelBenchmarkCase] = []
    extensions = {"java": "java", "python": "py", "c": "c"}
    for scenario in _SCENARIOS:
        for language, action_type in sorted(scenario.action_by_language.items()):
            if language not in selected_languages:
                continue
            for sample_index in range(1, samples_per_action + 1):
                token = f"R{rng.randrange(1000, 9999)}"
                literal = rng.randrange(120, 980)
                source = _source(language, token, literal)
                params = _parameters(language, action_type, source, token, literal)
                action_slug = action_type.replace("_", "-")
                base_id = f"{language}_{action_type}_{sample_index}_{token.lower()}"
                file_name = f"benchmark/{language}/{action_slug}_{token}.{extensions[language]}"
                cases.append(ModelBenchmarkCase(
                    case_id=f"{base_id}_positive",
                    language=language,
                    file_name=file_name,
                    source_code=source,
                    action_type=action_type,
                    source_refactoring=scenario.refactoring,
                    parameters=params,
                    expected_model_acceptance=True,
                    dataset_split="positive",
                    provenance="rdp_catalog_seeded_synthetic",
                ))
                if include_negative:
                    cases.append(ModelBenchmarkCase(
                        case_id=f"{base_id}_negative",
                        language=language,
                        file_name=file_name,
                        source_code=source,
                        action_type=action_type,
                        source_refactoring=scenario.refactoring,
                        parameters=_negative_parameters(params, source, token),
                        expected_model_acceptance=False,
                        dataset_split="negative",
                        provenance="rdp_catalog_seeded_synthetic",
                    ))
    return cases


def summarize_rdp_csv(path: str | Path) -> dict[str, Any]:
    """Summarize the RDP training CSV used as benchmark-distribution evidence."""

    csv_path = Path(path)
    if not csv_path.exists():
        return {"available": False, "path": str(csv_path)}
    languages: Counter[str] = Counter()
    refactorings: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            languages[str(row.get("language") or "unknown")] += 1
            refactorings[str(row.get("refactoring_suggested") or "unknown")] += 1
            labels[str(row.get("refactoring_applied") or "unknown")] += 1
    return {
        "available": True,
        "path": str(csv_path),
        "rows": sum(languages.values()),
        "language_distribution": dict(sorted(languages.items())),
        "refactoring_distribution": dict(sorted(refactorings.items())),
        "applied_label_distribution": dict(sorted(labels.items())),
        "note": (
            "The RDP CSV supplies metadata distributions but no source code. "
            "Executable benchmark source is generated reproducibly from the RDP catalog."
        ),
    }
