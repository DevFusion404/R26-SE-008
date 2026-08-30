"""Conservative text-based Java transformers for mock-safe execution.

This module mirrors the Python transformer behavior where possible:
- Introduce Constant generates stable names like CONSTANT_NUMBER_6.
- Generic names like EXTRACTED_CONSTANT are normalized to value-based names.
- Constants are inserted into the class body before use.
- Replacements avoid touching existing constant declarations.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple


_TYPE_DECL_RE = re.compile(
    r"\b(class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b[^{;]*\{"
)
_STATIC_FINAL_RE = re.compile(
    r"\bstatic\s+final\b[^;=]*\b([A-Za-z_][A-Za-z0-9_]*)\b\s*="
)


def _to_java_literal(value: Any) -> str:
    if isinstance(value, str):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _sanitize_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())

    if not cleaned:
        cleaned = "VALUE"

    if cleaned[0].isdigit():
        cleaned = f"N_{cleaned}"

    return cleaned.upper()


def _constant_name_from_value(value: Any) -> str:
    if isinstance(value, bool):
        return f"CONSTANT_BOOL_{str(value).upper()}"

    if value is None:
        return "CONSTANT_NONE"

    if isinstance(value, int):
        if value < 0:
            return f"CONSTANT_NUMBER_NEG_{abs(value)}"
        return f"CONSTANT_NUMBER_{value}"

    if isinstance(value, float):
        text = str(value).replace("-", "NEG_").replace(".", "_")
        return f"CONSTANT_NUMBER_{_sanitize_identifier(text)}"

    if isinstance(value, str):
        short = value[:24]
        return f"CONSTANT_STRING_{_sanitize_identifier(short)}"

    return "CONSTANT_VALUE"


def _normalize_legacy_magic_name(cleaned: str, literal_value: Any) -> str:
    if not cleaned.startswith("MAGIC_"):
        return cleaned
    if cleaned.startswith(("MAGIC_NUMBER_", "MAGIC_STRING_", "MAGIC_BOOL_")) or cleaned in {
        "MAGIC_NONE",
        "MAGIC_VALUE",
    }:
        return _constant_name_from_value(literal_value)
    return f"CONSTANT_{cleaned[len('MAGIC_'):]}"


def _normalize_constant_name(
    constant_name: Optional[str],
    literal_value: Any,
) -> str:
    if not constant_name:
        return _constant_name_from_value(literal_value)

    cleaned = _sanitize_identifier(str(constant_name))

    generic_names = {
        "EXTRACTED_CONSTANT",
        "MAGIC_CONSTANT",
        "CONSTANT",
        "VALUE_CONSTANT",
    }

    if cleaned in generic_names:
        return _constant_name_from_value(literal_value)

    cleaned = _normalize_legacy_magic_name(cleaned, literal_value)

    if isinstance(literal_value, str) and cleaned.startswith(("MAGIC_NUMBER_", "CONSTANT_NUMBER_")):
        return _constant_name_from_value(literal_value)

    return cleaned


def _class_constant_names(source_code: str) -> set[str]:
    return {match.group(1) for match in _STATIC_FINAL_RE.finditer(source_code)}


def _has_class_constant(source_code: str, constant_name: str) -> bool:
    return constant_name in _class_constant_names(source_code)


def _unique_constant_name(source_code: str, preferred_name: str) -> str:
    existing = _class_constant_names(source_code)

    if preferred_name not in existing:
        return preferred_name

    index = 2
    while f"{preferred_name}_{index}" in existing:
        index += 1

    return f"{preferred_name}_{index}"


def _java_type_for_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "int" if -(2**31) <= value <= (2**31 - 1) else "long"
    if isinstance(value, float):
        return "double"
    if isinstance(value, str):
        return "String"
    if value is None:
        return "Object"
    return "Object"


def _literal_pattern(literal_value: Any) -> re.Pattern[str]:
    literal_text = _to_java_literal(literal_value)
    escaped = re.escape(literal_text)

    if isinstance(literal_value, (int, float)) or literal_text in {"true", "false", "null"}:
        return re.compile(rf"(?<![A-Za-z0-9_.]){escaped}(?![A-Za-z0-9_.])")

    return re.compile(escaped)


def _mask_java_comments_and_literals(source_code: str) -> str:
    result: list[str] = []
    state = "code"
    index = 0

    while index < len(source_code):
        char = source_code[index]
        nxt = source_code[index + 1] if index + 1 < len(source_code) else ""

        if state == "code":
            if char == "/" and nxt == "/":
                result.extend("  ")
                state = "line_comment"
                index += 2
                continue
            if char == "/" and nxt == "*":
                result.extend("  ")
                state = "block_comment"
                index += 2
                continue
            if char == '"':
                result.append(" ")
                state = "string"
                index += 1
                continue
            if char == "'":
                result.append(" ")
                state = "char"
                index += 1
                continue
            result.append(char)
            index += 1
            continue

        if state == "line_comment":
            result.append("\n" if char == "\n" else " ")
            if char == "\n":
                state = "code"
            index += 1
            continue

        if state == "block_comment":
            if char == "*" and nxt == "/":
                result.extend("  ")
                state = "code"
                index += 2
                continue
            result.append("\n" if char == "\n" else " ")
            index += 1
            continue

        quote = '"' if state == "string" else "'"
        if char == "\\":
            result.append(" ")
            if nxt:
                result.append("\n" if nxt == "\n" else " ")
            index += 2
            continue

        result.append("\n" if char == "\n" else " ")
        if char == quote:
            state = "code"
        index += 1

    return "".join(result)


def _iter_java_string_literals(source_code: str) -> list[tuple[str, int, int, int]]:
    literals: list[tuple[str, int, int, int]] = []
    state = "code"
    index = 0
    start = 0
    value: list[str] = []

    while index < len(source_code):
        char = source_code[index]
        nxt = source_code[index + 1] if index + 1 < len(source_code) else ""

        if state == "code":
            if char == "/" and nxt == "/":
                state = "line_comment"
                index += 2
                continue
            if char == "/" and nxt == "*":
                state = "block_comment"
                index += 2
                continue
            if char == '"':
                state = "string"
                start = index
                value = []
            index += 1
            continue

        if state == "line_comment":
            if char == "\n":
                state = "code"
            index += 1
            continue

        if state == "block_comment":
            if char == "*" and nxt == "/":
                state = "code"
                index += 2
                continue
            index += 1
            continue

        if char == "\\":
            if nxt:
                value.append(_decode_java_string_escape(nxt))
                index += 2
                continue
            index += 1
            continue

        if char == '"':
            line_no = source_code.count("\n", 0, start) + 1
            literals.append(("".join(value), line_no, start, index + 1))
            state = "code"
            index += 1
            continue

        value.append(char)
        index += 1

    return literals


def _decode_java_string_escape(char: str) -> str:
    escapes = {
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "b": "\b",
        "f": "\f",
        "\\": "\\",
        '"': '"',
        "'": "'",
    }
    return escapes.get(char, f"\\{char}")


def _coerce_numeric_string_literal(
    source_code: str,
    literal_value: Any,
    source_line: Optional[int],
) -> Any:
    if source_line is None or not isinstance(literal_value, (int, float)):
        return literal_value

    literal_text = str(literal_value)
    for value, line_no, _start, _end in _iter_java_string_literals(source_code):
        if line_no == source_line and value.strip() == literal_text:
            return value

    global_matches = [
        value
        for value, _line_no, _start, _end in _iter_java_string_literals(source_code)
        if value.strip() == literal_text
    ]
    if len(global_matches) == 1:
        return global_matches[0]

    return literal_value


def _line_contains_literal_inside_java_string(
    source_code: str,
    literal_value: Any,
    source_line: Optional[int],
) -> bool:
    if source_line is None or not isinstance(literal_value, (int, float)):
        return False

    literal_text = str(literal_value)
    return any(
        line_no == source_line and literal_text in value
        for value, line_no, _start, _end in _iter_java_string_literals(source_code)
    )


def _should_skip_replacement(line: str, constant_name: str) -> bool:
    stripped = line.strip()

    if not stripped:
        return True

    if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
        return True

    if "static final" in stripped:
        return True

    if re.search(rf"\b{re.escape(constant_name)}\b\s*=", stripped):
        return True

    if re.match(r"[A-Z0-9_]+\s*=", stripped):
        return True

    return False


def _replace_literal(
    source_code: str,
    literal_value: Any,
    constant_name: str,
    source_line: Optional[int] = None,
) -> Tuple[str, int]:
    pattern = _literal_pattern(literal_value)
    lines = source_code.splitlines(keepends=True)
    replacements = 0

    if not isinstance(literal_value, str):
        masked_lines = _mask_java_comments_and_literals(source_code).splitlines(keepends=True)
        for index, line in enumerate(lines, start=1):
            if source_line is not None and index != source_line:
                continue

            if _should_skip_replacement(line, constant_name):
                continue

            masked_line = masked_lines[index - 1] if index <= len(masked_lines) else ""
            matches = list(pattern.finditer(masked_line))
            if not matches:
                continue

            updated = line
            for match in reversed(matches):
                updated = updated[: match.start()] + constant_name + updated[match.end() :]

            replacements += len(matches)
            lines[index - 1] = updated

        return "".join(lines), replacements

    for index, line in enumerate(lines, start=1):
        if source_line is not None and index != source_line:
            continue

        if _should_skip_replacement(line, constant_name):
            continue

        updated, count = pattern.subn(constant_name, line)
        if count:
            replacements += count
            lines[index - 1] = updated

    return "".join(lines), replacements


def _numeric_literal_occurrences(
    source_code: str,
    literal_value: Any,
    *,
    source_line: Optional[int] = None,
) -> list[dict[str, int]]:
    """Return eligible Java numeric-literal occurrences outside text/comments.

    This deliberately operates on the comment/string-masked source so digits in
    embedded HTML/CSS, URLs, entities, comments, and character literals are not
    treated as Java Magic Numbers. Existing ``static final`` declarations are
    excluded because they are already named constants rather than smells.
    """

    if (
        not isinstance(literal_value, (int, float))
        or isinstance(literal_value, bool)
    ):
        return []

    pattern = _literal_pattern(literal_value)
    masked_lines = _mask_java_comments_and_literals(source_code).splitlines(keepends=True)
    raw_lines = source_code.splitlines(keepends=True)
    occurrences: list[dict[str, int]] = []
    for line_no, masked_line in enumerate(masked_lines, start=1):
        if source_line is not None and line_no != source_line:
            continue
        raw_line = raw_lines[line_no - 1] if line_no <= len(raw_lines) else ""
        if "static final" in raw_line:
            continue
        for match in pattern.finditer(masked_line):
            occurrences.append(
                {
                    "line": line_no,
                    "column": match.start() + 1,
                }
            )
    return occurrences


def _numeric_string_occurrences(
    source_code: str,
    literal_value: Any,
    *,
    source_line: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Describe occurrences of a number inside Java string literals.

    These are useful as diagnostics for upstream false-positive Magic Number
    findings, but are never eligible targets for ``Introduce Constant``.
    """

    if (
        not isinstance(literal_value, (int, float))
        or isinstance(literal_value, bool)
    ):
        return []

    literal_text = str(literal_value)
    matches: list[dict[str, Any]] = []
    for value, line_no, _start, _end in _iter_java_string_literals(source_code):
        if source_line is not None and line_no != source_line:
            continue
        if literal_text not in value:
            continue
        matches.append({"line": line_no, "string_value": value})
    return matches


def _numeric_constants_for_literal(
    source_code: str,
    literal_value: Any,
) -> list[str]:
    """Return Java constant names whose initializer is exactly this number."""

    if (
        not isinstance(literal_value, (int, float))
        or isinstance(literal_value, bool)
    ):
        return []

    literal_text = re.escape(str(literal_value))
    pattern = re.compile(
        rf"\bstatic\s+final\s+[A-Za-z_][A-Za-z0-9_<>\[\].?]*\s+"
        rf"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*{literal_text}\s*;"
    )
    return [match.group("name") for match in pattern.finditer(source_code)]


def apply_introduce_constant(
    source_code: str,
    literal_value: Any,
    constant_name: Optional[str] = None,
    source_line: Optional[int] = None,
    *,
    reference_source_code: Optional[str] = None,
) -> Tuple[str, int, dict[str, Any]]:
    """Safely apply Java Introduce Constant with target diagnostics.

    ``reference_source_code`` should be the original file text before sequential
    transformations. It allows an RDP/CUQA line target to be classified even
    after earlier constant insertions shifted the current working line numbers.

    Numeric values are eligible only when they exist as real Java numeric
    literals outside strings/comments. Numbers that exist solely inside HTML,
    CSS, URLs, entities, or any other Java string literal are safely classified
    as ``not_applicable`` rather than rewritten or reported as failures.
    """

    reference = reference_source_code if reference_source_code is not None else source_code
    metadata: dict[str, Any] = {
        "refactoring": "Introduce Constant",
        "language": "java",
        "target_literal": literal_value,
        "requested_source_line": source_line,
        "status": "success",
        "reason": "introduce_constant_applied",
    }

    is_numeric = (
        isinstance(literal_value, (int, float))
        and not isinstance(literal_value, bool)
    )

    if not is_numeric:
        transformed, replacements = apply_extract_constant(
            source_code,
            literal_value,
            constant_name,
            source_line,
        )
        metadata["target_context"] = "NON_NUMERIC_LITERAL"
        metadata["eligible_occurrences_before"] = replacements
        if replacements <= 0:
            metadata.update(
                {
                    "status": "not_applicable",
                    "reason": "TARGET_LITERAL_NOT_FOUND",
                    "final_status": "NOT_APPLICABLE",
                    "final_decision": "NOT_APPLICABLE",
                }
            )
        return transformed, replacements, metadata

    reference_line_code = _numeric_literal_occurrences(
        reference, literal_value, source_line=source_line
    ) if source_line is not None else []
    reference_line_strings = _numeric_string_occurrences(
        reference, literal_value, source_line=source_line
    ) if source_line is not None else []
    reference_code = _numeric_literal_occurrences(reference, literal_value)
    reference_strings = _numeric_string_occurrences(reference, literal_value)

    metadata.update(
        {
            "reference_eligible_occurrences": len(reference_code),
            "reference_string_occurrences": len(reference_strings),
            "requested_line_eligible_occurrences": len(reference_line_code),
            "requested_line_string_occurrences": len(reference_line_strings),
        }
    )

    # A line-scoped upstream Magic Number target that points into a string is a
    # false positive. Never fall back to a different numeric expression in the
    # file, because that could refactor the wrong program behavior.
    if source_line is not None and reference_line_strings and not reference_line_code:
        metadata.update(
            {
                "status": "not_applicable",
                "reason": "TARGET_NOT_JAVA_NUMERIC_LITERAL",
                "target_context": "STRING_LITERAL",
                "final_status": "NOT_APPLICABLE",
                "final_decision": "NOT_APPLICABLE",
            }
        )
        return source_code, 0, metadata

    # If the original file contains no eligible Java numeric literal at all,
    # the plan target is not a valid Introduce Constant operation. This catches
    # HTML/CSS/string digits even when the upstream action omitted a line.
    if not reference_code:
        metadata.update(
            {
                "status": "not_applicable",
                "reason": (
                    "TARGET_NOT_JAVA_NUMERIC_LITERAL"
                    if reference_strings
                    else "TARGET_LITERAL_NOT_FOUND"
                ),
                "target_context": (
                    "STRING_LITERAL" if reference_strings else "NOT_FOUND"
                ),
                "final_status": "NOT_APPLICABLE",
                "final_decision": "NOT_APPLICABLE",
            }
        )
        return source_code, 0, metadata

    current_code_occurrences = _numeric_literal_occurrences(source_code, literal_value)
    current_constants = set(_numeric_constants_for_literal(source_code, literal_value))
    reference_constants = set(_numeric_constants_for_literal(reference, literal_value))
    constants_added_during_run = sorted(current_constants - reference_constants)
    normalized_name = _normalize_constant_name(constant_name, literal_value)
    requested_constant_already_exists = normalized_name in current_constants
    metadata["eligible_occurrences_before"] = len(current_code_occurrences)
    if constants_added_during_run:
        metadata["constants_added_by_prior_actions"] = constants_added_during_run
    if requested_constant_already_exists:
        metadata["existing_requested_constant"] = normalized_name

    if not current_code_occurrences:
        metadata.update(
            {
                "status": "not_applicable",
                "reason": (
                    "TARGET_ALREADY_REFACTORED_BY_PREVIOUS_ACTION"
                    if constants_added_during_run
                    else "TARGET_NO_LONGER_PRESENT"
                ),
                "target_context": "JAVA_NUMERIC_LITERAL",
                "final_status": "NOT_APPLICABLE",
                "final_decision": "NOT_APPLICABLE",
            }
        )
        return source_code, 0, metadata

    # Reuse only the exact requested constant name. Reusing an arbitrary
    # pre-existing constant merely because it has the same numeric value can
    # couple unrelated domain concepts and is therefore unsafe.
    preferred_name = (
        normalized_name
        if requested_constant_already_exists
        else _unique_constant_name(source_code, normalized_name)
    )

    # Sequential constant insertions shift original line numbers. Use the
    # current line only when it still contains the requested literal; otherwise
    # recover against the remaining eligible code occurrences.
    current_line_occurrences = _numeric_literal_occurrences(
        source_code, literal_value, source_line=source_line
    ) if source_line is not None else []
    effective_line = source_line if current_line_occurrences else None
    metadata["target_resolution"] = (
        "current_source_line" if effective_line is not None else "current_numeric_literal_scan"
    )

    transformed, replacements = _replace_literal(
        source_code, literal_value, preferred_name, effective_line
    )
    if replacements > 0 and not requested_constant_already_exists and not _has_class_constant(
        transformed, preferred_name
    ):
        transformed = _insert_class_constant(
            transformed, preferred_name, literal_value
        )

    metadata.update(
        {
            "constant_name": preferred_name,
            "target_context": "JAVA_NUMERIC_LITERAL",
            "eligible_occurrences_after": len(
                _numeric_literal_occurrences(transformed, literal_value)
            ),
            "reused_existing_constant": bool(requested_constant_already_exists),
        }
    )
    if replacements <= 0:
        metadata.update(
            {
                "status": "not_applicable",
                "reason": "TARGET_NO_LONGER_PRESENT",
                "final_status": "NOT_APPLICABLE",
                "final_decision": "NOT_APPLICABLE",
            }
        )
    else:
        metadata["final_status"] = "PASS"
        metadata["final_decision"] = "ACCEPT"

    return transformed, replacements, metadata


def _replace_numeric_inside_java_string(
    source_code: str,
    literal_value: Any,
    constant_name: str,
    source_line: Optional[int],
) -> Tuple[str, int]:
    """Turn text containing a planned number into equivalent concatenation."""

    if not isinstance(literal_value, (int, float)) or isinstance(literal_value, bool):
        return source_code, 0

    literal_text = str(literal_value)
    number_pattern = re.compile(
        rf"(?<![A-Za-z0-9_.]){re.escape(literal_text)}(?![A-Za-z0-9_.])"
    )
    candidates: list[tuple[int, int, int, re.Match[str]]] = []
    for decoded, line_no, start, end in _iter_java_string_literals(source_code):
        if decoded.strip() == literal_text:
            # A value such as password "1234" is a String constant, not a
            # numeric expression. Existing string-constant handling owns it.
            continue
        raw_content = source_code[start + 1:end - 1]
        match = number_pattern.search(raw_content)
        if match:
            candidates.append((line_no, start, end, match))

    line_candidates = [item for item in candidates if item[0] == source_line]
    if line_candidates:
        selected = line_candidates[0] if len(line_candidates) == 1 else None
    else:
        masked = _mask_java_comments_and_literals(source_code)
        selected = candidates[0] if len(candidates) == 1 and not number_pattern.search(masked) else None
    if selected is None:
        return source_code, 0

    _line_no, start, end, match = selected
    raw_content = source_code[start + 1:end - 1]
    before = raw_content[:match.start()]
    after = raw_content[match.end():]
    expression_parts: list[str] = []
    if before:
        expression_parts.append(f'"{before}"')
    expression_parts.append(constant_name)
    if after:
        expression_parts.append(f'"{after}"')
    expression = " + ".join(expression_parts)
    return source_code[:start] + expression + source_code[end:], 1


def _insert_class_constant(
    source_code: str,
    constant_name: str,
    literal_value: Any,
) -> str:
    if _has_class_constant(source_code, constant_name):
        return source_code

    match = _TYPE_DECL_RE.search(source_code)
    if not match:
        return source_code

    kind = match.group(1)
    insert_idx = match.end()

    indent = "    "
    indent_match = re.search(r"\n([ \t]*)\S", source_code[insert_idx:])
    if indent_match:
        indent = indent_match.group(1) or indent

    access = "public" if kind == "interface" else "private"
    literal_text = _to_java_literal(literal_value)
    type_name = _java_type_for_literal(literal_value)

    declaration = f"\n{indent}{access} static final {type_name} {constant_name} = {literal_text};\n"
    return source_code[:insert_idx] + declaration + source_code[insert_idx:]


def _insert_class_constant_expression(
    source_code: str,
    constant_name: str,
    expression: str,
    *,
    type_name: str = "String",
) -> str:
    if _has_class_constant(source_code, constant_name):
        return source_code

    match = _TYPE_DECL_RE.search(source_code)
    if not match:
        return source_code

    insert_idx = match.end()
    indent = "    "
    indent_match = re.search(r"\n([ \t]*)\S", source_code[insert_idx:])
    if indent_match:
        indent = indent_match.group(1) or indent

    cleaned_expression = expression.strip()
    declaration = f"\n{indent}private static final {type_name} {constant_name} = {cleaned_expression};\n"
    return source_code[:insert_idx] + declaration + source_code[insert_idx:]


def _source_line_in_span(source_code: str, start_idx: int, end_idx: int, source_line: Optional[int]) -> bool:
    if source_line is None:
        return True
    start_line = source_code[:start_idx].count("\n") + 1
    end_line = source_code[:end_idx].count("\n") + 1
    return start_line <= source_line <= end_line


def _replace_identifier_outside_literals(source_code: str, old_name: str, new_name: str) -> Tuple[str, int]:
    result: list[str] = []
    count = 0
    i = 0
    state = "code"
    old_len = len(old_name)

    while i < len(source_code):
        char = source_code[i]
        nxt = source_code[i + 1] if i + 1 < len(source_code) else ""

        if state == "code":
            if char == "/" and nxt == "/":
                state = "line_comment"
                result.append(char)
                result.append(nxt)
                i += 2
                continue
            if char == "/" and nxt == "*":
                state = "block_comment"
                result.append(char)
                result.append(nxt)
                i += 2
                continue
            if char == '"':
                state = "string"
                result.append(char)
                i += 1
                continue
            if char == "'":
                state = "char"
                result.append(char)
                i += 1
                continue
            before = source_code[i - 1] if i > 0 else ""
            after = source_code[i + old_len] if i + old_len < len(source_code) else ""
            if (
                source_code.startswith(old_name, i)
                and not (before.isalnum() or before == "_")
                and not (after.isalnum() or after == "_")
            ):
                result.append(new_name)
                count += 1
                i += old_len
                continue
            result.append(char)
            i += 1
            continue

        result.append(char)
        if state == "line_comment" and char == "\n":
            state = "code"
        elif state == "block_comment" and char == "*" and nxt == "/":
            result.append(nxt)
            i += 1
            state = "code"
        elif state in {"string", "char"}:
            if char == "\\" and nxt:
                result.append(nxt)
                i += 1
            elif (state == "string" and char == '"') or (state == "char" and char == "'"):
                state = "code"
        i += 1

    return "".join(result), count


def apply_rename_symbol(source_code: str, old_name: str, new_name: str) -> Tuple[str, int]:
    transformed, count = _replace_identifier_outside_literals(source_code, old_name, new_name)
    return transformed, count


def apply_rename_method(
    source_code: str,
    old_name: str,
    new_name: str,
    *,
    source_class: str = "",
    parameter_types: Optional[list[str]] = None,
) -> Tuple[str, int, dict[str, Any]]:
    """Rename one Java method declaration and its statically safe call sites.

    This is intentionally narrower than ``rename_symbol``.  Java overloads and
    duplicate declarations require type resolution across call sites, so SCTVA
    returns ``review_required`` unless the target method can be proven unique in
    the current source file or matched by an explicit signature.
    """

    metadata: dict[str, Any] = {
        "refactoring": "Rename Method",
        "language": "java",
        "old_name": old_name,
        "new_name": new_name,
        "source_class": source_class,
        "plan_compliance": "UNKNOWN",
    }
    if not _is_java_identifier(old_name) or not _is_java_identifier(new_name):
        return source_code, 0, {**metadata, "status": "review_required", "reason": "INVALID_METHOD_NAME"}
    if old_name == new_name:
        return source_code, 0, {**metadata, "status": "already_applied", "reason": "METHOD_NAME_UNCHANGED"}

    try:
        from .java_extract_class import _parse_java_class, declared_class_names
    except Exception as exc:  # pragma: no cover - defensive import guard
        return source_code, 0, {
            **metadata,
            "status": "review_required",
            "reason": "JAVA_METHOD_MODEL_UNAVAILABLE",
            "error": str(exc),
        }

    class_names = sorted(declared_class_names(source_code))
    if source_class:
        if source_class not in class_names:
            return source_code, 0, {
                **metadata,
                "status": "not_applicable",
                "reason": "SOURCE_CLASS_NOT_FOUND",
            }
        class_names = [source_class]

    method_records: list[tuple[str, Any, list[str]]] = []
    for class_name in class_names:
        model = _parse_java_class(source_code, class_name)
        if model is None:
            continue
        for method in model.methods:
            if method.name != old_name or method.is_constructor:
                continue
            method_records.append((class_name, method, _java_method_parameter_types(method)))

    requested_types = [_normalize_java_type(item) for item in (parameter_types or []) if str(item).strip()]
    if requested_types:
        method_records = [
            record for record in method_records
            if [_normalize_java_type(item) for item in record[2]] == requested_types
        ]
        metadata["parameter_types"] = requested_types

    if not method_records:
        return source_code, 0, {
            **metadata,
            "status": "not_applicable",
            "reason": "METHOD_TARGET_NOT_FOUND",
        }
    if len(method_records) > 1:
        return source_code, 0, {
            **metadata,
            "status": "review_required",
            "reason": "AMBIGUOUS_METHOD_OVERLOAD" if source_class else "AMBIGUOUS_METHOD_TARGET",
            "candidates": [
                {"class_name": class_name, "parameter_types": types}
                for class_name, _method, types in method_records
            ],
        }

    resolved_class, target_method, resolved_types = method_records[0]
    target_model = _parse_java_class(source_code, resolved_class)
    if target_model is None:
        return source_code, 0, {**metadata, "status": "review_required", "reason": "SOURCE_CLASS_PARSE_FAILED"}
    if re.search(r"@\s*Override\b", target_method.header):
        return source_code, 0, {**metadata, "status": "review_required", "reason": "OVERRIDE_METHOD_REQUIRES_HIERARCHY_UPDATE"}
    if any(method.name == new_name and method is not target_method for method in target_model.methods):
        return source_code, 0, {**metadata, "status": "review_required", "reason": "METHOD_NAME_COLLISION"}

    transformed, replacements = _replace_java_method_call_names(source_code, old_name, new_name)
    if replacements <= 0:
        return source_code, 0, {**metadata, "status": "review_required", "reason": "NO_METHOD_REFERENCES_RENAMED"}

    verification = validate_java_rename_method(
        source_code,
        transformed,
        old_name=old_name,
        new_name=new_name,
        source_class=resolved_class,
        parameter_types=resolved_types,
    )
    status = "success" if verification.get("passed") else "review_required"
    reason = "RENAMED_METHOD_AND_CALL_SITES" if verification.get("passed") else verification.get("reason", "VALIDATION_FAILED")
    return transformed, replacements, {
        **metadata,
        "status": status,
        "reason": reason,
        "source_class": resolved_class,
        "parameter_types": resolved_types,
        "declaration_renamed": verification.get("declaration_renamed", False),
        "old_declaration_removed": verification.get("old_declaration_removed", False),
        "new_declaration_present": verification.get("new_declaration_present", False),
        "call_sites_updated": verification.get("call_sites_updated", False),
        "replacements": replacements,
        "plan_compliance": "PASS" if verification.get("passed") else "REVIEW_REQUIRED",
    }


def validate_java_rename_method(
    original_code: str,
    transformed_code: str,
    *,
    old_name: str,
    new_name: str,
    source_class: str = "",
    parameter_types: Optional[list[str]] = None,
) -> dict[str, Any]:
    try:
        from .java_extract_class import _parse_java_class, declared_class_names
    except Exception as exc:  # pragma: no cover - defensive import guard
        return {"passed": False, "reason": "java_method_model_unavailable", "error": str(exc)}

    class_names = [source_class] if source_class else sorted(declared_class_names(original_code))
    requested_types = [_normalize_java_type(item) for item in (parameter_types or []) if str(item).strip()]

    original_matches: list[tuple[str, Any, list[str]]] = []
    transformed_new_matches: list[tuple[str, Any, list[str]]] = []
    transformed_old_matches: list[tuple[str, Any, list[str]]] = []
    for class_name in class_names:
        original_model = _parse_java_class(original_code, class_name)
        transformed_model = _parse_java_class(transformed_code, class_name)
        if original_model is None or transformed_model is None:
            continue
        for method in original_model.methods:
            types = _java_method_parameter_types(method)
            if method.name == old_name and (not requested_types or [_normalize_java_type(item) for item in types] == requested_types):
                original_matches.append((class_name, method, types))
        for method in transformed_model.methods:
            types = _java_method_parameter_types(method)
            normalized = [_normalize_java_type(item) for item in types]
            if method.name == new_name and (not requested_types or normalized == requested_types):
                transformed_new_matches.append((class_name, method, types))
            if method.name == old_name and (not requested_types or normalized == requested_types):
                transformed_old_matches.append((class_name, method, types))

    declaration_renamed = len(original_matches) == 1 and len(transformed_new_matches) == 1
    old_declaration_removed = not transformed_old_matches
    new_declaration_present = bool(transformed_new_matches)
    comments_strings_preserved = _java_literals_and_comments_preserved(original_code, transformed_code, old_name, new_name)
    remaining_old_invocations = _count_java_method_references(transformed_code, old_name)
    new_invocations = _count_java_method_references(transformed_code, new_name)
    call_sites_updated = remaining_old_invocations == 0 and new_invocations > 0
    passed = (
        declaration_renamed
        and old_declaration_removed
        and new_declaration_present
        and call_sites_updated
        and comments_strings_preserved
    )
    reason = "java_rename_method_passed" if passed else "java_rename_method_failed"
    return {
        "passed": passed,
        "reason": reason,
        "source_class": source_class,
        "old_name": old_name,
        "new_name": new_name,
        "parameter_types": requested_types,
        "declaration_renamed": declaration_renamed,
        "old_declaration_removed": old_declaration_removed,
        "new_declaration_present": new_declaration_present,
        "call_sites_updated": call_sites_updated,
        "comments_strings_preserved": comments_strings_preserved,
        "remaining_old_invocations": remaining_old_invocations,
        "new_invocations": new_invocations,
        "original_matches": len(original_matches),
        "transformed_new_matches": len(transformed_new_matches),
        "transformed_old_matches": len(transformed_old_matches),
    }


def _is_java_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", str(value or "")))


def _normalize_java_type(type_name: Any) -> str:
    text = str(type_name or "").strip()
    text = re.sub(r"\bfinal\b", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def _java_method_parameter_types(method: Any) -> list[str]:
    header = str(getattr(method, "header", "") or "")
    name = str(getattr(method, "name", "") or "")
    match = re.search(rf"\b{re.escape(name)}\s*\((?P<params>[^()]*)\)\s*$", header, re.DOTALL)
    if not match:
        return []
    return [type_name for type_name, _param_name in _split_params(match.group("params"))]


def _replace_java_method_call_names(source_code: str, old_name: str, new_name: str) -> Tuple[str, int]:
    masked = _mask_java_comments_and_literals(source_code)
    spans: list[tuple[int, int]] = []

    for match in re.finditer(rf"\b{re.escape(old_name)}\b(?=\s*\()", masked):
        spans.append((match.start(), match.end()))

    for match in re.finditer(rf"::\s*({re.escape(old_name)})\b", masked):
        spans.append((match.start(1), match.end(1)))

    if not spans:
        return source_code, 0

    merged = sorted(set(spans), reverse=True)
    transformed = source_code
    for start, end in merged:
        transformed = transformed[:start] + new_name + transformed[end:]
    return transformed, len(merged)


def _count_java_method_references(source_code: str, method_name: str) -> int:
    masked = _mask_java_comments_and_literals(source_code)
    calls = len(re.findall(rf"\b{re.escape(method_name)}\b(?=\s*\()", masked))
    refs = len(re.findall(rf"::\s*{re.escape(method_name)}\b", masked))
    return calls + refs


def _java_literals_and_comments_preserved(
    original_code: str,
    transformed_code: str,
    old_name: str,
    new_name: str,
) -> bool:
    def protected_fragments(source: str) -> list[str]:
        fragments: list[str] = []
        state = "code"
        start = 0
        index = 0
        while index < len(source):
            char = source[index]
            nxt = source[index + 1] if index + 1 < len(source) else ""
            if state == "code":
                if char == "/" and nxt in {"/", "*"}:
                    state = "line_comment" if nxt == "/" else "block_comment"
                    start = index
                    index += 2
                    continue
                if char in {'"', "'"}:
                    state = "string" if char == '"' else "char"
                    start = index
                    index += 1
                    continue
                index += 1
                continue
            if state == "line_comment":
                if char == "\n":
                    fragments.append(source[start:index])
                    state = "code"
                index += 1
                continue
            if state == "block_comment":
                if char == "*" and nxt == "/":
                    fragments.append(source[start:index + 2])
                    state = "code"
                    index += 2
                    continue
                index += 1
                continue
            quote = '"' if state == "string" else "'"
            if char == "\\":
                index += 2
                continue
            if char == quote:
                fragments.append(source[start:index + 1])
                state = "code"
            index += 1
        if state != "code":
            fragments.append(source[start:])
        return fragments

    return protected_fragments(transformed_code) == protected_fragments(original_code)


def apply_replace_literal(
    source_code: str,
    old_literal: Any,
    new_literal: Any,
    source_line: Optional[int] = None,
) -> Tuple[str, int]:
    old_text = _to_java_literal(old_literal)
    new_text = _to_java_literal(new_literal)
    lines = source_code.splitlines(keepends=True)
    replacements = 0
    for index, line in enumerate(lines, start=1):
        if source_line is not None and index != source_line:
            continue
        updated, count = line.replace(old_text, new_text), line.count(old_text)
        if count:
            lines[index - 1] = updated
            replacements += count
    return "".join(lines), replacements


def apply_normalize_multiline_statement(
    source_code: str,
    *,
    source_line: Optional[int] = None,
    constant_name: str = "SCTVA_EXTRACTED_VALUE",
    normalization: str = "",
) -> Tuple[str, int]:
    preferred_name = _sanitize_identifier(constant_name or "SCTVA_EXTRACTED_VALUE")
    unique_name = _unique_constant_name(source_code, preferred_name)
    string_literal = r'"(?:\\.|[^"\\])*"'

    patterns = []
    if normalization in {"", "java_prepare_statement_sql"}:
        patterns.append(
            re.compile(
                rf"""
                (?P<prefix>\bPreparedStatement\s+[A-Za-z_][A-Za-z0-9_]*\s*=
                \s*[A-Za-z_][A-Za-z0-9_.]*\s*\.prepareStatement\s*\()
                (?P<expr>\s*{string_literal}(?:\s*\+\s*{string_literal})+\s*)
                (?P<suffix>\)\s*;)
                """,
                re.VERBOSE | re.DOTALL,
            )
        )

    patterns.append(
        re.compile(
            rf"""
            (?P<prefix>\b(?:final\s+)?String\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*)
            (?P<expr>\s*{string_literal}(?:\s*\+\s*{string_literal})+\s*)
            (?P<suffix>;)
            """,
            re.VERBOSE | re.DOTALL,
        )
    )

    for allow_line_fallback in (False, True):
        for pattern in patterns:
            for match in pattern.finditer(source_code):
                expr = match.group("expr")
                if "\n" not in expr:
                    continue
                if not allow_line_fallback and not _source_line_in_span(source_code, match.start(), match.end(), source_line):
                    continue
    
                transformed = (
                    source_code[: match.start("expr")]
                    + unique_name
                    + source_code[match.end("expr") :]
                )
                transformed = _insert_class_constant_expression(
                    transformed,
                    unique_name,
                    expr,
                    type_name="String",
                )
                return transformed, 1

        if source_line is None:
            break

    return source_code, 0


def _find_java_method_span_by_name(
    source_code: str,
    method_name: str,
) -> Optional[Tuple[int, int]]:
    if not method_name:
        return None
    pattern = re.compile(
        rf"(?m)^([ \t]*)(?:(?:public|private|protected|static|final|synchronized|native)\s+)*"
        rf"[A-Za-z_][A-Za-z0-9_<>\[\].?]*\s+"
        rf"{re.escape(method_name)}\s*\([^;{{}}]*\)\s*\{{"
    )
    for match in pattern.finditer(source_code):
        brace_idx = source_code.find("{", match.end() - 1)
        method_end = _find_matching_brace(source_code, brace_idx)
        if method_end is None:
            continue
        start_line = source_code[: match.start()].count("\n") + 1
        end_line = source_code[:method_end].count("\n") + 1
        return start_line, end_line
    return None


def _selected_mentions_method_signature(selected: list[str], method_name: str) -> bool:
    if not method_name:
        return False
    selected_text = "".join(selected)
    return bool(
        re.search(
            rf"\b{re.escape(method_name)}\s*\([^;{{}}]*\)\s*\{{",
            selected_text,
        )
    )


def _extract_java_method_by_name(
    source_code: str,
    new_method_name: str,
    method_name: str,
) -> Tuple[str, int]:
    span = _find_java_method_span_by_name(source_code, method_name)
    if not span:
        return source_code, 0
    return _extract_full_java_method(source_code, new_method_name, span[0], span[1])


def apply_extract_constant(
    source_code: str,
    literal_value: Any,
    constant_name: Optional[str] = None,
    source_line: Optional[int] = None,
) -> Tuple[str, int]:
    original_literal_value = literal_value
    literal_value = _coerce_numeric_string_literal(source_code, literal_value, source_line)
    protected_string_literal = _line_contains_literal_inside_java_string(
        source_code,
        original_literal_value,
        source_line,
    )
    normalized_name = _normalize_constant_name(constant_name, literal_value)
    preferred_name = _unique_constant_name(source_code, normalized_name)

    transformed, replacements = _replace_literal(
        source_code,
        literal_value,
        preferred_name,
        source_line,
    )
    strict_numeric_name = (
        isinstance(original_literal_value, (int, float))
        and not isinstance(original_literal_value, bool)
        and normalized_name == f"CONSTANT_{str(original_literal_value).replace('-', 'NEG_').replace('.', '_')}"
    )
    if replacements == 0 and strict_numeric_name:
        transformed, replacements = _replace_numeric_inside_java_string(
            source_code,
            original_literal_value,
            preferred_name,
            source_line,
        )
    if replacements == 0 and source_line is not None and not protected_string_literal:
        transformed, replacements = _replace_literal(
            source_code,
            literal_value,
            preferred_name,
            None,
        )

    if replacements > 0 and not _has_class_constant(transformed, preferred_name):
        transformed = _insert_class_constant(
            transformed,
            preferred_name,
            literal_value,
        )

    return transformed, replacements


def _find_matching_brace(source: str, start_idx: int) -> Optional[int]:
    depth = 0
    for idx in range(start_idx, len(source)):
        char = source[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return idx
    return None


def _find_class_span(source: str, class_name: str) -> Optional[Tuple[int, int]]:
    match = re.search(rf"\bclass\s+{re.escape(class_name)}\b", source)
    if not match:
        return None
    brace_idx = source.find("{", match.end())
    if brace_idx < 0:
        return None
    end_idx = _find_matching_brace(source, brace_idx)
    if end_idx is None:
        return None
    return brace_idx, end_idx


def apply_narrow_exception_handler(
    source_code: str,
    *,
    source_line: Optional[int] = None,
    original_exception_type: str = "",
    target_exception_type: str = "",
    handler_name: str = "",
) -> Tuple[str, int]:
    """Replace one broad Java catch type with a proven concrete type."""

    if not _valid_java_exception_type(target_exception_type):
        return source_code, 0

    catch_re = re.compile(
        r"\bcatch\s*\(\s*(?P<final>final\s+)?(?P<type>Exception|Throwable)\s+"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\)"
    )
    candidates = []
    for match in catch_re.finditer(source_code):
        if original_exception_type and match.group("type") != original_exception_type:
            continue
        if handler_name and match.group("name") != handler_name:
            continue
        candidates.append(match)

    if source_line is not None:
        line_matches = [
            match for match in candidates
            if source_code.count("\n", 0, match.start()) + 1 == source_line
        ]
        if line_matches:
            candidates = line_matches
    if len(candidates) != 1:
        return source_code, 0

    match = candidates[0]
    replacement = (
        "catch ("
        f"{match.group('final') or ''}{target_exception_type} {match.group('name')}"
        ")"
    )
    return source_code[:match.start()] + replacement + source_code[match.end():], 1


def _valid_java_exception_type(value: str) -> bool:
    names = [part.strip() for part in value.split("|") if part.strip()]
    if not names or any(name in {"Exception", "Throwable"} for name in names):
        return False
    return all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name) for name in names)


def apply_remove_dead_code(
    source_code: str,
    method_name: str,
    class_name: Optional[str] = None,
    source_line: Optional[int] = None,
) -> Tuple[str, int]:
    if not method_name and source_line is None:
        raise ValueError("remove_dead_code requires 'method_name' or 'source_line'.")

    if not method_name and source_line is not None:
        return _remove_proven_unused_java_declaration(source_code, source_line)

    scope_start = 0
    scope_end = len(source_code)

    if class_name:
        class_span = _find_class_span(source_code, class_name)
        if not class_span:
            return source_code, 0
        scope_start, scope_end = class_span

    scope = source_code[scope_start:scope_end]

    pattern = re.compile(
        rf"(?:(?:public|private|protected|static|final|native|synchronized|abstract)\s+)*"
        rf"[\w\<\>\[\],\s]+\b{re.escape(method_name)}\s*\([^)]*\)\s*\{{",
        re.MULTILINE,
    )

    match = pattern.search(scope)
    if not match:
        return source_code, 0

    signature = scope[match.start():match.end()]
    if "private" not in signature.split():
        return source_code, 0
    if len(re.findall(rf"\b{re.escape(method_name)}\b", source_code)) != 1:
        return source_code, 0

    method_start = scope_start + match.start()
    brace_idx = scope_start + match.end() - 1
    method_end = _find_matching_brace(source_code, brace_idx)
    if method_end is None:
        return source_code, 0

    before = source_code[:method_start].rstrip()
    after = source_code[method_end + 1 :].lstrip()
    return f"{before}\n{after}", 1


def _is_literal_java_initializer(value: str) -> bool:
    cleaned = value.strip()
    if not cleaned:
        return True
    if re.search(r"\bnew\b|\w+\s*\(", cleaned):
        return False
    without_literals = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', "", cleaned)
    without_keywords = re.sub(r"\b(?:true|false|null)\b", "", without_literals)
    return not re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\b", without_keywords)


def _remove_proven_unused_java_declaration(source_code: str, source_line: int) -> Tuple[str, int]:
    lines = source_code.splitlines(keepends=True)
    if source_line <= 0 or source_line > len(lines):
        return source_code, 0

    line = lines[source_line - 1]
    match = re.match(
        r"^\s*(?:final\s+)?[A-Za-z_][A-Za-z0-9_<>,.?\[\]]*\s+"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*(.*?))?;\s*(?://.*)?$",
        line.rstrip("\r\n"),
    )
    if not match:
        return source_code, 0

    variable_name = match.group(1)
    initializer = match.group(2) or ""
    if not _is_literal_java_initializer(initializer):
        return source_code, 0
    if len(re.findall(rf"\b{re.escape(variable_name)}\b", source_code)) != 1:
        return source_code, 0

    return "".join(lines[: source_line - 1] + lines[source_line:]), 1


def _class_insert_before_final_brace(source_code: str, source_index: int) -> Optional[int]:
    best: Optional[Tuple[int, int]] = None
    for match in _TYPE_DECL_RE.finditer(source_code):
        brace_idx = match.end() - 1
        end_idx = _find_matching_brace(source_code, brace_idx)
        if end_idx is None:
            continue
        if brace_idx <= source_index <= end_idx:
            span_size = end_idx - brace_idx
            if best is None or span_size < best[0]:
                best = (span_size, end_idx)
    return best[1] if best else None


def _split_params(params_raw: str) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = []
    for raw in params_raw.split(","):
        cleaned = raw.strip()
        if not cleaned:
            continue
        tokens = cleaned.split()
        if len(tokens) < 2:
            continue
        name = tokens[-1].replace("...", "").replace("[]", "").strip()
        type_name = " ".join(tokens[:-1]).replace("final ", "").strip()
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name) and type_name:
            params.append((type_name, name))
    return params


def _infer_java_method_context(source_code: str, source_index: int) -> tuple[str, list[tuple[str, str]], int]:
    prefix = source_code[:source_index]
    method_match = None
    pattern = re.compile(
        r"(?:(?:public|private|protected|static|final|synchronized|native)\s+)*"
        r"([A-Za-z_][A-Za-z0-9_<>\[\].?]*)\s+"
        r"[A-Za-z_][A-Za-z0-9_]*\s*\(([^;{}]*)\)\s*\{",
        re.MULTILINE,
    )
    for match in pattern.finditer(prefix):
        method_match = match
    if not method_match:
        return "Object", [], 0
    return method_match.group(1), _split_params(method_match.group(2)), method_match.end()


def _java_local_variables(source_code: str, start_idx: int, end_idx: int) -> list[tuple[str, str]]:
    declarations: list[tuple[str, str]] = []
    body_prefix = source_code[start_idx:end_idx]
    pattern = re.compile(
        r"\b(?:final\s+)?([A-Za-z_][A-Za-z0-9_<>\[\].?]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|;)"
    )
    for match in pattern.finditer(body_prefix):
        type_name, name = match.group(1), match.group(2)
        if type_name in {"return", "if", "for", "while", "switch", "catch"}:
            continue
        declarations.append((type_name, name))
    return declarations


def _referenced_java_variables(selected_source: str, candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    identifiers = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", selected_source))
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for type_name, name in candidates:
        if name in identifiers and name not in seen:
            ordered.append((type_name, name))
            seen.add(name)
    return ordered


def apply_extract_method(
    source_code: str,
    new_method_name: str,
    start_line: int,
    end_line: int,
    method_name: Optional[str] = None,
) -> Tuple[str, int]:
    """Backward-compatible entry point for semantic Java extraction."""

    from .java_extract_method import apply_extract_method as apply_semantic_extract_method
    from .java_extract_method import _resolve_targets

    resolved_name = str(method_name or "").strip()
    if not resolved_name:
        source_offset = sum(
            len(line)
            for line in source_code.splitlines(keepends=True)[: max(0, start_line - 1)]
        )
        candidates = []
        # Empty-name semantic lookup is intentionally unsupported; inspect
        # lexical names only to locate the enclosing method for legacy callers.
        for name_match in re.finditer(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", source_code):
            candidate_name = name_match.group(1)
            for _model, candidate in _resolve_targets(source_code, candidate_name, "", ""):
                if candidate.start <= source_offset <= candidate.end:
                    candidates.append(candidate)
        if not candidates:
            return source_code, 0
        resolved_name = candidates[0].name
    transformed, replacements, _metadata = apply_semantic_extract_method(
        source_code,
        new_method_name=new_method_name,
        method_name=resolved_name,
        start_line=start_line,
        end_line=end_line,
    )
    return transformed, replacements


def _extract_full_java_method(
    source_code: str,
    new_method_name: str,
    start_line: int,
    end_line: int,
) -> Tuple[str, int]:
    lines = source_code.splitlines(keepends=True)
    selected = lines[start_line - 1 : end_line]
    selected_text = "".join(selected)
    signature = re.search(
        r"(?ms)^([ \t]*)((?:(?:public|private|protected|static|final|synchronized|native)\s+)*)"
        r"([A-Za-z_][A-Za-z0-9_<>\[\].?]*)\s+"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([^;{}]*)\)\s*\{",
        selected_text,
    )
    if not signature:
        return source_code, 0

    open_brace = selected_text.find("{", signature.end() - 1)
    close_brace = selected_text.rfind("}")
    if open_brace < 0 or close_brace <= open_brace:
        return source_code, 0

    method_indent = signature.group(1)
    modifiers = signature.group(2)
    return_type = signature.group(3)
    method_name = signature.group(4)
    params_raw = signature.group(5).strip()
    params = _split_params(params_raw)
    call_args = ", ".join(name for _, name in params)
    body_text = selected_text[open_brace + 1 : close_brace]
    if not body_text.strip():
        return source_code, 0

    body_lines = body_text.splitlines(keepends=True)
    body_indent = "    "
    for line in body_lines:
        if line.strip():
            body_indent = re.match(r"[ \t]*", line).group(0) or f"{method_indent}    "
            break

    call = (
        f"{body_indent}{new_method_name}({call_args});\n"
        if return_type == "void"
        else f"{body_indent}return {new_method_name}({call_args});\n"
    )
    original_method = (
        f"{method_indent}{modifiers}{return_type} {method_name}({params_raw}) {{\n"
        f"{call}"
        f"{method_indent}}}\n"
    )
    helper = (
        f"\n{method_indent}private {return_type} {new_method_name}({params_raw}) {{"
        f"{body_text}"
        f"{method_indent}}}\n"
    )
    return "".join(lines[: start_line - 1] + [original_method, helper] + lines[end_line:]), 1


def apply_inject_syntax_error(source_code: str) -> Tuple[str, int]:
    broken = source_code + "\npublic void __sctva_broken( {\n"
    return broken, 1


def apply_fault_injection(source_code: str, original_logic: str, faulty_logic: str) -> Tuple[str, int]:
    if not original_logic:
        raise ValueError("fault_injection requires 'original_logic'.")
    if faulty_logic is None:
        raise ValueError("fault_injection requires 'faulty_logic'.")

    if original_logic not in source_code:
        return source_code, 0

    return source_code.replace(original_logic, faulty_logic, 1), 1


def apply_fault_injection_java(source_code: str, original_logic: str, faulty_logic: str) -> Tuple[str, int]:
    """Backward-compatible alias for callers that expect a Java-specific name."""
    return apply_fault_injection(source_code, original_logic, faulty_logic)
