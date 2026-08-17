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
    if not new_method_name:
        raise ValueError("extract_method requires 'new_method_name'.")
    if start_line <= 0 or end_line < start_line:
        raise ValueError("extract_method requires a valid line range.")

    lines = source_code.splitlines(keepends=True)
    if end_line > len(lines):
        return source_code, 0

    selected = lines[start_line - 1 : end_line]
    meaningful = [line for line in selected if line.strip()]
    if not meaningful or not meaningful[-1].strip().startswith("return"):
        if method_name and _selected_mentions_method_signature(selected, method_name):
            by_name = _extract_java_method_by_name(source_code, new_method_name, method_name)
            if by_name[1]:
                return by_name
        full_method = _extract_full_java_method(source_code, new_method_name, start_line, end_line)
        if full_method[1]:
            return full_method
        if method_name:
            by_name = _extract_java_method_by_name(source_code, new_method_name, method_name)
            if by_name[1]:
                return by_name
        return source_code, 0

    block_indent = min((re.match(r"[ \t]*", line).group(0) for line in meaningful), key=len)
    method_indent = block_indent[:-4] if block_indent.endswith("    ") else block_indent
    source_index = sum(len(line) for line in lines[: start_line - 1])
    return_type, params, method_body_start = _infer_java_method_context(source_code, source_index)
    locals_before_selection = _java_local_variables(source_code, method_body_start, source_index)
    helper_params = _referenced_java_variables("".join(selected), [*params, *locals_before_selection])
    helper_signature_params = ", ".join(f"{type_name} {name}" for type_name, name in helper_params)
    helper_call_args = ", ".join(name for _, name in helper_params)
    insertion_index = _class_insert_before_final_brace(source_code, source_index)
    if insertion_index is None:
        return source_code, 0

    helper_body = [
        (f"{block_indent}{line[len(block_indent):]}" if line.startswith(block_indent) else f"{block_indent}{line.lstrip()}")
        for line in selected
    ]
    helper = (
        f"\n{method_indent}private {return_type} {new_method_name}({helper_signature_params}) {{\n"
        + "".join(helper_body)
        + f"{method_indent}}}\n"
    )
    replacement = [f"{block_indent}return {new_method_name}({helper_call_args});\n"]
    transformed_lines = lines[: start_line - 1] + replacement + lines[end_line:]
    transformed = "".join(transformed_lines)
    if insertion_index > source_index:
        insertion_index += len("".join(replacement)) - len("".join(selected))
    return transformed[:insertion_index] + helper + transformed[insertion_index:], 1


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
