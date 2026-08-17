"""Conservative text-based C transformers for safe refactoring actions."""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple


_INCLUDE_RE = re.compile(r"^\s*#\s*include\b.*$", re.MULTILINE)
_DEFINE_RE = re.compile(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\b.*$", re.MULTILINE)


def _sanitize_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    if not cleaned:
        cleaned = "VALUE"
    if cleaned[0].isdigit():
        cleaned = f"N_{cleaned}"
    return cleaned.upper()


def _to_c_literal(value: Any) -> str:
    if isinstance(value, str):
        return '"' + _escape_c_string(value) + '"'
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return "0"
    return str(value)


def _escape_c_string(value: str) -> str:
    escaped: list[str] = []
    for char in value:
        if char == "\\":
            escaped.append("\\\\")
        elif char == '"':
            escaped.append('\\"')
        elif char == "\n":
            escaped.append("\\n")
        elif char == "\r":
            escaped.append("\\r")
        elif char == "\t":
            escaped.append("\\t")
        elif char == "\0":
            escaped.append("\\0")
        else:
            escaped.append(char)
    return "".join(escaped)


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
        text = re.sub(r"[^A-Za-z0-9_]", "_", text).strip("_")
        return f"CONSTANT_NUMBER_{text}"
    if isinstance(value, str):
        return f"CONSTANT_STRING_{_sanitize_identifier(value[:24])}"
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


def _normalize_constant_name(constant_name: Optional[str], literal_value: Any) -> str:
    if not constant_name:
        return _constant_name_from_value(literal_value)

    cleaned = _sanitize_identifier(str(constant_name))
    if cleaned in {"EXTRACTED_CONSTANT", "MAGIC_CONSTANT", "CONSTANT", "VALUE_CONSTANT"}:
        return _constant_name_from_value(literal_value)
    cleaned = _normalize_legacy_magic_name(cleaned, literal_value)
    return cleaned


def _existing_define_names(source_code: str) -> set[str]:
    return {match.group(1) for match in _DEFINE_RE.finditer(source_code)}


def _unique_constant_name(source_code: str, preferred_name: str) -> str:
    existing = _existing_define_names(source_code)
    if preferred_name not in existing:
        return preferred_name
    index = 2
    while f"{preferred_name}_{index}" in existing:
        index += 1
    return f"{preferred_name}_{index}"


def _literal_pattern(literal_value: Any) -> re.Pattern[str]:
    literal_text = _to_c_literal(literal_value)
    escaped = re.escape(literal_text)
    if isinstance(literal_value, (int, float)) or literal_text in {"0", "1"}:
        return re.compile(rf"(?<![A-Za-z0-9_.]){escaped}(?![A-Za-z0-9_.])")
    return re.compile(escaped)


def _should_skip_replacement(line: str, constant_name: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
        return True
    if stripped.startswith("#define"):
        return True
    if re.search(rf"\b{re.escape(constant_name)}\b\s*=", stripped):
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


def _insert_define(source_code: str, constant_name: str, literal_value: Any) -> str:
    if constant_name in _existing_define_names(source_code):
        return source_code

    define_line = f"#define {constant_name} {_to_c_literal(literal_value)}\n"
    match = _INCLUDE_RE.search(source_code)
    if match:
        insert_at = match.end()
        tail = source_code[insert_at:]
        next_line = tail.find("\n")
        if next_line >= 0:
            insert_at += next_line + 1
        return source_code[:insert_at] + define_line + source_code[insert_at:]

    return define_line + "\n" + source_code.lstrip()


def _insert_static_string_constant(
    source_code: str,
    constant_name: str,
    expression: str,
) -> str:
    if re.search(rf"\b{re.escape(constant_name)}\b\s*\[?\]?\s*=", source_code):
        return source_code

    declaration = f"static const char {constant_name}[] = {expression.strip()};\n"
    match = _INCLUDE_RE.search(source_code)
    if match:
        insert_at = match.end()
        tail = source_code[insert_at:]
        next_line = tail.find("\n")
        if next_line >= 0:
            insert_at += next_line + 1
        return source_code[:insert_at] + declaration + source_code[insert_at:]

    return declaration + "\n" + source_code.lstrip()


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
                result.extend([char, nxt])
                i += 2
                continue
            if char == "/" and nxt == "*":
                state = "block_comment"
                result.extend([char, nxt])
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
    return _replace_identifier_outside_literals(source_code, old_name, new_name)


def apply_extract_constant(
    source_code: str,
    literal_value: Any,
    constant_name: Optional[str] = None,
    source_line: Optional[int] = None,
) -> Tuple[str, int]:
    normalized_name = _normalize_constant_name(constant_name, literal_value)
    preferred_name = _unique_constant_name(source_code, normalized_name)
    transformed, replacements = _replace_literal(
        source_code,
        literal_value,
        preferred_name,
        source_line,
    )
    if replacements == 0 and source_line is not None:
        transformed, replacements = _replace_literal(
            source_code,
            literal_value,
            preferred_name,
            None,
        )
    if replacements > 0:
        transformed = _insert_define(transformed, preferred_name, literal_value)
    return transformed, replacements


def apply_replace_literal(
    source_code: str,
    old_literal: Any,
    new_literal: Any,
    source_line: Optional[int] = None,
) -> Tuple[str, int]:
    old_text = _to_c_literal(old_literal)
    new_text = _to_c_literal(new_literal)
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
    del normalization
    preferred_name = _unique_constant_name(source_code, _sanitize_identifier(constant_name or "SCTVA_EXTRACTED_VALUE"))
    string_literal = r'"(?:\\.|[^"\\])*"'
    pattern = re.compile(
        rf"""
        (?P<prefix>\b(?:const\s+)?char\s*(?:\*\s*)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*)
        (?P<expr>\s*{string_literal}(?:\s*{string_literal})+\s*)
        (?P<suffix>;)
        """,
        re.VERBOSE | re.DOTALL,
    )

    for match in pattern.finditer(source_code):
        expr = match.group("expr")
        if "\n" not in expr:
            continue
        if not _source_line_in_span(source_code, match.start(), match.end(), source_line):
            continue
        transformed = (
            source_code[: match.start("expr")]
            + preferred_name
            + source_code[match.end("expr") :]
        )
        transformed = _insert_static_string_constant(transformed, preferred_name, expr)
        return transformed, 1

    return source_code, 0


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


def _is_literal_c_initializer(value: str) -> bool:
    cleaned = value.strip()
    if not cleaned:
        return True
    if re.search(r"[A-Za-z_]", cleaned) and not re.fullmatch(
        r"(?:NULL|true|false|sizeof\s*\([^()]*\)|[uUlLfFxXa-fA-F0-9.+'\"\\\s(){}\[\],|&^~!<>*/%?:-])+",
        cleaned,
    ):
        return False
    if re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(", cleaned) and not cleaned.startswith("sizeof"):
        return False
    return True


def _remove_proven_unused_c_declaration(source_code: str, source_line: int) -> Tuple[str, int]:
    lines = source_code.splitlines(keepends=True)
    if source_line <= 0 or source_line > len(lines):
        return source_code, 0

    line = lines[source_line - 1]
    match = re.match(
        r"^\s*(?:(?:const|volatile|unsigned|signed|short|long|static|register)\s+)*"
        r"(?:struct\s+\w+|enum\s+\w+|union\s+\w+|[A-Za-z_]\w*)"
        r"(?:\s*\*+|\s+)([A-Za-z_]\w*)\s*(?:=\s*(.*?))?;\s*(?://.*)?$",
        line.rstrip("\r\n"),
    )
    if not match:
        return source_code, 0

    variable_name = match.group(1)
    initializer = match.group(2) or ""
    if not _is_literal_c_initializer(initializer):
        return source_code, 0
    if len(re.findall(rf"\b{re.escape(variable_name)}\b", source_code)) != 1:
        return source_code, 0

    return "".join(lines[: source_line - 1] + lines[source_line:]), 1


def apply_remove_dead_code(
    source_code: str,
    method_name: str,
    class_name: Optional[str] = None,
    source_line: Optional[int] = None,
) -> Tuple[str, int]:
    if not method_name and source_line is None:
        raise ValueError("remove_dead_code requires 'method_name' or 'source_line'.")

    if not method_name and source_line is not None:
        return _remove_proven_unused_c_declaration(source_code, source_line)

    pattern = re.compile(
        rf"(?ms)^[ \t]*(?:[A-Za-z_][A-Za-z0-9_\s\*]*?\s+)+{re.escape(method_name)}\s*\([^;{{}}]*\)\s*\{{"
    )
    match = pattern.search(source_code)
    if not match:
        return source_code, 0

    signature = source_code[match.start():match.end()]
    if "static" not in signature.split():
        return source_code, 0
    if len(re.findall(rf"\b{re.escape(method_name)}\b", source_code)) != 1:
        return source_code, 0

    method_start = match.start()
    brace_idx = match.end() - 1
    method_end = _find_matching_brace(source_code, brace_idx)
    if method_end is None:
        return source_code, 0

    before = source_code[:method_start].rstrip()
    after = source_code[method_end + 1 :].lstrip()
    if before and after:
        return f"{before}\n{after}", 1
    return f"{before}{after}", 1


def _split_c_params(params_raw: str) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = []
    cleaned_params = params_raw.strip()
    if not cleaned_params or cleaned_params == "void":
        return params
    for raw in cleaned_params.split(","):
        cleaned = raw.strip()
        if not cleaned:
            continue
        match = re.match(r"(.+?)([A-Za-z_][A-Za-z0-9_]*)\s*$", cleaned)
        if not match:
            continue
        type_name = match.group(1).strip()
        name = match.group(2).strip()
        if type_name:
            params.append((type_name, name))
    return params


def _infer_c_method_context(source_code: str, source_index: int) -> tuple[str, list[tuple[str, str]], int]:
    prefix = source_code[:source_index]
    method_match = None
    pattern = re.compile(
        r"(?ms)^[ \t]*((?:[A-Za-z_][A-Za-z0-9_]*\s+|\*\s*)+)"
        r"[A-Za-z_][A-Za-z0-9_]*\s*\(([^;{}]*)\)\s*\{"
    )
    for match in pattern.finditer(prefix):
        method_match = match
    if not method_match:
        return "int", [], 0
    return " ".join(method_match.group(1).split()), _split_c_params(method_match.group(2)), method_match.end()


def _c_local_variables(source_code: str, start_idx: int, end_idx: int) -> list[tuple[str, str]]:
    declarations: list[tuple[str, str]] = []
    body_prefix = source_code[start_idx:end_idx]
    pattern = re.compile(
        r"\b((?:const\s+)?(?:unsigned\s+|signed\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*\*)?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|;)"
    )
    for match in pattern.finditer(body_prefix):
        type_name, name = " ".join(match.group(1).split()), match.group(2)
        if type_name in {"return", "if", "for", "while", "switch"}:
            continue
        declarations.append((type_name, name))
    return declarations


def _referenced_c_variables(selected_source: str, candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    identifiers = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", selected_source))
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for type_name, name in candidates:
        if name in identifiers and name not in seen:
            ordered.append((type_name, name))
            seen.add(name)
    return ordered


def _insert_c_helper_before_function(source_code: str, helper: str, source_index: int) -> str:
    function_start = source_code.rfind("\n", 0, source_index)
    if function_start < 0:
        function_start = 0
    else:
        function_start += 1
    return source_code[:function_start] + helper + "\n" + source_code[function_start:]


def apply_extract_method(
    source_code: str,
    new_method_name: str,
    start_line: int,
    end_line: int,
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
        full_function = _extract_full_c_function(source_code, new_method_name, start_line, end_line)
        if full_function[1]:
            return full_function
        return source_code, 0

    block_indent = min((re.match(r"[ \t]*", line).group(0) for line in meaningful), key=len)
    source_index = sum(len(line) for line in lines[: start_line - 1])
    return_type, params, function_body_start = _infer_c_method_context(source_code, source_index)
    locals_before_selection = _c_local_variables(source_code, function_body_start, source_index)
    helper_params = _referenced_c_variables("".join(selected), [*params, *locals_before_selection])
    helper_signature_params = ", ".join(f"{type_name} {name}" for type_name, name in helper_params) or "void"
    helper_call_args = ", ".join(name for _, name in helper_params)
    helper_body = [
        (f"{block_indent}{line[len(block_indent):]}" if line.startswith(block_indent) else f"{block_indent}{line.lstrip()}")
        for line in selected
    ]
    helper = f"static {return_type} {new_method_name}({helper_signature_params}) {{\n" + "".join(helper_body) + "}\n"
    replacement = [f"{block_indent}return {new_method_name}({helper_call_args});\n"]
    transformed = "".join(lines[: start_line - 1] + replacement + lines[end_line:])
    return _insert_c_helper_before_function(transformed, helper, source_index), 1


def _extract_full_c_function(
    source_code: str,
    new_method_name: str,
    start_line: int,
    end_line: int,
) -> Tuple[str, int]:
    lines = source_code.splitlines(keepends=True)
    selected = lines[start_line - 1 : end_line]
    selected_text = "".join(selected)
    signature = re.search(
        r"(?ms)^([ \t]*)((?:[A-Za-z_][A-Za-z0-9_]*\s+|\*\s*)+)"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([^;{}]*)\)\s*\{",
        selected_text,
    )
    if not signature:
        return source_code, 0

    open_brace = selected_text.find("{", signature.end() - 1)
    close_brace = selected_text.rfind("}")
    if open_brace < 0 or close_brace <= open_brace:
        return source_code, 0

    indent = signature.group(1)
    return_type = " ".join(signature.group(2).split())
    function_name = signature.group(3)
    params_raw = signature.group(4).strip()
    params = _split_c_params(params_raw)
    signature_params = params_raw or "void"
    call_args = ", ".join(name for _, name in params)
    body_text = selected_text[open_brace + 1 : close_brace]
    if not body_text.strip():
        return source_code, 0

    body_indent = "    "
    for line in body_text.splitlines(keepends=True):
        if line.strip():
            body_indent = re.match(r"[ \t]*", line).group(0) or "    "
            break

    call = (
        f"{body_indent}{new_method_name}({call_args});\n"
        if return_type == "void"
        else f"{body_indent}return {new_method_name}({call_args});\n"
    )
    original_function = (
        f"{indent}{return_type} {function_name}({signature_params}) {{\n"
        f"{call}"
        f"{indent}}}\n"
    )
    helper = f"static {return_type} {new_method_name}({signature_params}) {{{body_text}}}\n\n"
    return "".join(lines[: start_line - 1] + [helper, original_function] + lines[end_line:]), 1


def _split_call_args(args_raw: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    depth = 0
    quote = ""
    escape = False
    for char in args_raw:
        if quote:
            current.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            current.append(char)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}" and depth:
            depth -= 1
        if char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if current:
        args.append("".join(current).strip())
    return args


def apply_replace_unsafe_function(
    source_code: str,
    unsafe_function: str,
    safe_alternative: str,
    source_line: Optional[int] = None,
) -> Tuple[str, int]:
    lines = source_code.splitlines(keepends=True)
    replacements = 0
    call_pattern = re.compile(rf"\b{re.escape(unsafe_function)}\s*\((.*?)\)")

    for index, line in enumerate(lines, start=1):
        if source_line is not None and index != source_line:
            continue

        def replace_call(match: re.Match[str]) -> str:
            nonlocal replacements
            args = _split_call_args(match.group(1))
            if unsafe_function == "gets" and safe_alternative == "fgets" and args:
                replacements += 1
                buffer = args[0]
                return f"fgets({buffer}, sizeof({buffer}), stdin)"
            if unsafe_function == "strcpy" and safe_alternative == "strncpy" and len(args) >= 2:
                replacements += 1
                destination, source = args[0], args[1]
                return f"strncpy({destination}, {source}, sizeof({destination}) - 1)"
            if unsafe_function == "strcat" and safe_alternative == "strncat" and len(args) >= 2:
                replacements += 1
                destination, source = args[0], args[1]
                return f"strncat({destination}, {source}, sizeof({destination}) - strlen({destination}) - 1)"
            if unsafe_function == "sprintf" and safe_alternative == "snprintf" and len(args) >= 2:
                replacements += 1
                destination = args[0]
                rest = ", ".join(args[1:])
                return f"snprintf({destination}, sizeof({destination}), {rest})"

            replacements += 1
            return f"{safe_alternative}({match.group(1)})"

        updated = call_pattern.sub(replace_call, line)
        lines[index - 1] = updated

    return "".join(lines), replacements


def apply_encapsulate_variable(
    source_code: str,
    variable_name: str,
    getter_name: str,
    setter_name: str,
) -> Tuple[str, int]:
    if not variable_name:
        raise ValueError("encapsulate_variable requires 'variable_name'.")

    declaration_re = re.compile(
        rf"(?m)^([ \t]*)(?!static\b|extern\b)((?:const\s+)?(?:unsigned\s+|signed\s+)?[A-Za-z_][A-Za-z0-9_]*(?:\s*\*)?)\s+"
        rf"{re.escape(variable_name)}\s*(=\s*[^;]+)?;"
    )
    match = declaration_re.search(source_code)
    if not match:
        return source_code, 0

    indent = match.group(1)
    type_name = " ".join(match.group(2).split())
    initializer = f" {match.group(3).strip()}" if match.group(3) else ""
    static_declaration = f"{indent}static {type_name} {variable_name}{initializer};"
    helper = (
        f"\n{type_name} {getter_name}(void) {{\n"
        f"    return {variable_name};\n"
        f"}}\n\n"
        f"void {setter_name}({type_name} value) {{\n"
        f"    {variable_name} = value;\n"
        f"}}\n"
    )

    transformed = (
        source_code[: match.start()]
        + static_declaration
        + helper
        + source_code[match.end() :]
    )
    return transformed, 1


def apply_inject_syntax_error(source_code: str) -> Tuple[str, int]:
    broken = source_code.rstrip() + "\nint __sctva_broken = ;\n"
    return broken, 1


def apply_fault_injection(source_code: str, original_logic: str, faulty_logic: str) -> Tuple[str, int]:
    if not original_logic:
        raise ValueError("fault_injection requires 'original_logic'.")
    if faulty_logic is None:
        raise ValueError("fault_injection requires 'faulty_logic'.")
    if original_logic not in source_code:
        return source_code, 0
    return source_code.replace(original_logic, faulty_logic, 1), 1


__all__ = [
    "apply_extract_constant",
    "apply_fault_injection",
    "apply_inject_syntax_error",
    "apply_extract_method",
    "apply_replace_unsafe_function",
    "apply_encapsulate_variable",
    "apply_remove_dead_code",
    "apply_rename_symbol",
    "apply_replace_literal",
    "apply_normalize_multiline_statement",
]
