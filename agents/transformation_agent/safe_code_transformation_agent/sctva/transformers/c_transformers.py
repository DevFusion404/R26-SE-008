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


def _mask_c_non_code(source: str) -> str:
    """Keep C token positions while masking comments and literals."""

    masked = list(source)
    index = 0
    state = "code"
    while index < len(source):
        char = source[index]
        nxt = source[index + 1] if index + 1 < len(source) else ""
        if state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                masked[index] = " "
        elif state == "block_comment":
            if char == "*" and nxt == "/":
                masked[index] = masked[index + 1] = " "
                index += 1
                state = "code"
            elif char != "\n":
                masked[index] = " "
        elif state in {"string", "char"}:
            if char == "\\":
                masked[index] = " "
                if index + 1 < len(source):
                    masked[index + 1] = " "
                    index += 1
            elif (state == "string" and char == '"') or (state == "char" and char == "'"):
                masked[index] = " "
                state = "code"
            elif char != "\n":
                masked[index] = " "
        elif char == "/" and nxt == "/":
            masked[index] = masked[index + 1] = " "
            index += 1
            state = "line_comment"
        elif char == "/" and nxt == "*":
            masked[index] = masked[index + 1] = " "
            index += 1
            state = "block_comment"
        elif char == '"':
            masked[index] = " "
            state = "string"
        elif char == "'":
            masked[index] = " "
            state = "char"
        index += 1
    return "".join(masked)


def _find_matching_brace(source: str, start_idx: int) -> Optional[int]:
    source = _mask_c_non_code(source)
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


def _line_for_c_index(source: str, index: int) -> int:
    return source.count("\n", 0, max(0, index)) + 1


def _c_brace_depth(source: str, index: int) -> int:
    masked = _mask_c_non_code(source[:index])
    return masked.count("{") - masked.count("}")


def _remove_c_span(source: str, start: int, end: int) -> Tuple[str, int]:
    if start < 0 or end <= start or end > len(source):
        return source, 0
    # Remove a following newline only when the selected statement owns it.
    if end < len(source) and source[end] == "\r":
        end += 1
    if end < len(source) and source[end] == "\n":
        end += 1
    return source[:start] + source[end:], 1


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


def _remove_proven_c_false_branch(source_code: str, source_line: int) -> Tuple[str, int]:
    masked = _mask_c_non_code(source_code)
    pattern = re.compile(r"\bif\s*\(\s*0\s*\)\s*\{")
    for match in pattern.finditer(masked):
        brace_index = masked.find("{", match.start(), match.end())
        end_brace = _find_matching_brace(masked, brace_index)
        if end_brace is None:
            continue
        start_line = _line_for_c_index(source_code, match.start())
        end_line = _line_for_c_index(source_code, end_brace)
        if not start_line <= source_line <= end_line:
            continue
        # An else branch has observable behavior and needs a CST move, so retain
        # it for manual review rather than deleting a valid execution path.
        if re.match(r"\s*else\b", masked[end_brace + 1 :]):
            continue
        statement_start = source_code.rfind("\n", 0, match.start()) + 1
        return _remove_c_span(source_code, statement_start, end_brace + 1)
    return source_code, 0


def _remove_proven_c_unreachable_statement(source_code: str, source_line: int) -> Tuple[str, int]:
    masked = _mask_c_non_code(source_code)
    terminators = re.compile(r"\b(?:return|break|continue|goto\s+[A-Za-z_][A-Za-z0-9_]*)\b")
    for match in terminators.finditer(masked):
        terminator_end = masked.find(";", match.end())
        if terminator_end == -1:
            continue
        next_start = terminator_end + 1
        while next_start < len(masked) and masked[next_start].isspace():
            next_start += 1
        if next_start >= len(masked) or masked[next_start] in "}#{":
            continue
        depth = _c_brace_depth(source_code, match.start())
        if _c_brace_depth(source_code, next_start) != depth:
            continue
        statement_end = masked.find(";", next_start)
        next_brace = min(
            [index for index in (masked.find("{", next_start), masked.find("}", next_start)) if index != -1],
            default=-1,
        )
        if statement_end == -1 or (next_brace != -1 and next_brace < statement_end):
            continue
        start_line = _line_for_c_index(source_code, next_start)
        end_line = _line_for_c_index(source_code, statement_end)
        if start_line <= source_line <= end_line:
            return _remove_c_span(source_code, next_start, statement_end + 1)
    return source_code, 0


def _remove_proven_unused_c_static_function(source_code: str, method_name: str) -> Tuple[str, int]:
    masked = _mask_c_non_code(source_code)
    pattern = re.compile(
        rf"(?ms)^[ \t]*(?:[A-Za-z_][A-Za-z0-9_\s\*]*?\s+)+{re.escape(method_name)}\s*\([^;{{}}]*\)\s*\{{"
    )
    matches = list(pattern.finditer(masked))
    if len(matches) != 1 or len(re.findall(rf"\b{re.escape(method_name)}\b", masked)) != 1:
        return source_code, 0
    match = matches[0]
    signature = masked[match.start() : match.end()]
    if not re.search(r"\bstatic\b", signature):
        return source_code, 0
    brace_index = masked.rfind("{", match.start(), match.end())
    method_end = _find_matching_brace(masked, brace_index)
    if method_end is None:
        return source_code, 0
    region = source_code[match.start() : method_end + 1]
    if re.search(r"(?m)^\s*#", region):
        return source_code, 0
    statement_start = source_code.rfind("\n", 0, match.start()) + 1
    return _remove_c_span(source_code, statement_start, method_end + 1)


def apply_remove_dead_code(
    source_code: str,
    method_name: str,
    class_name: Optional[str] = None,
    source_line: Optional[int] = None,
) -> Tuple[str, int]:
    if not method_name and source_line is None:
        raise ValueError("remove_dead_code requires 'method_name' or 'source_line'.")

    if not method_name and source_line is not None:
        for remover in (
            _remove_proven_c_false_branch,
            _remove_proven_c_unreachable_statement,
            _remove_proven_unused_c_declaration,
        ):
            transformed, replacements = remover(source_code, source_line)
            if replacements:
                return transformed, replacements
        return source_code, 0

    return _remove_proven_unused_c_static_function(source_code, method_name)


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
    """Backward-compatible entry point for semantic C extraction."""

    from .c_extract_method import _resolve_targets
    from .c_extract_method import apply_extract_method as apply_semantic_extract_method

    source_offset = sum(
        len(line)
        for line in source_code.splitlines(keepends=True)[: max(0, start_line - 1)]
    )
    candidates = []
    for name_match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", source_code):
        for candidate in _resolve_targets(source_code, name_match.group(1), ""):
            if candidate.start <= source_offset <= candidate.end:
                candidates.append(candidate)
    if not candidates:
        return source_code, 0
    transformed, replacements, _metadata = apply_semantic_extract_method(
        source_code,
        new_method_name=new_method_name,
        method_name=candidates[0].name,
        start_line=start_line,
        end_line=end_line,
    )
    return transformed, replacements


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
    """Replace a known unsafe C function call with its safe equivalent.

    Handles special two-step rewrite for ``scanf`` → ``fgets`` + ``sscanf``:
    a temporary char buffer is introduced and the original format string and
    variable list are preserved so program behaviour is unchanged.
    """
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
            if unsafe_function == "scanf" and safe_alternative == "fgets" and len(args) >= 1:
                # scanf("%d", &var)  →  char _buf[256]; fgets(_buf, sizeof(_buf), stdin); sscanf(_buf, "%d", &var)
                replacements += 1
                fmt = args[0]  # e.g. "%d" or "%f"
                rest = ", ".join(args[1:])  # e.g. &price
                # Preserve the leading indent of the original line so the
                # two-statement expansion keeps the same column alignment.
                leading = re.match(r"^(\s*)", match.string[match.pos:])
                indent = leading.group(1) if leading else ""
                buf_decl = "char _scanf_buf[256]"
                fgets_call = f"fgets(_scanf_buf, sizeof(_scanf_buf), stdin)"
                if rest:
                    sscanf_call = f"sscanf(_scanf_buf, {fmt}, {rest})"
                else:
                    sscanf_call = f"sscanf(_scanf_buf, {fmt})"
                return f"{buf_decl};\n{indent}{fgets_call};\n{indent}{sscanf_call}"

            replacements += 1
            return f"{safe_alternative}({match.group(1)})"

        updated = call_pattern.sub(replace_call, line)
        lines[index - 1] = updated

    return "".join(lines), replacements


def _mask_c_comments_and_strings(source_code: str) -> str:
    """Mask comments and literals while retaining offsets and line breaks."""

    masked = list(source_code)
    index = 0
    state = "code"
    while index < len(masked):
        char = masked[index]
        nxt = masked[index + 1] if index + 1 < len(masked) else ""
        if state == "code":
            if char == "/" and nxt == "/":
                masked[index] = masked[index + 1] = " "
                index += 2
                state = "line_comment"
                continue
            if char == "/" and nxt == "*":
                masked[index] = masked[index + 1] = " "
                index += 2
                state = "block_comment"
                continue
            if char == '"':
                masked[index] = " "
                index += 1
                state = "string"
                continue
            if char == "'":
                masked[index] = " "
                index += 1
                state = "char"
                continue
        elif state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                masked[index] = " "
        elif state == "block_comment":
            if char == "*" and nxt == "/":
                masked[index] = masked[index + 1] = " "
                index += 2
                state = "code"
                continue
            if char != "\n":
                masked[index] = " "
        else:
            if char == "\\":
                masked[index] = " "
                if index + 1 < len(masked) and masked[index + 1] != "\n":
                    masked[index + 1] = " "
                index += 2
                continue
            if (state == "string" and char == '"') or (state == "char" and char == "'"):
                masked[index] = " "
                state = "code"
            elif char != "\n":
                masked[index] = " "
        index += 1
    return "".join(masked)


def _c_global_declaration_match(source_code: str, variable_name: str) -> tuple[re.Match[str] | None, str]:
    """Find one deliberately small scalar C global declaration."""

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable_name or ""):
        return None, "INVALID_VARIABLE_NAME"
    type_part = (
        r"(?:(?:const|volatile|unsigned|signed|short|long)\s+)*"
        r"(?:char|int|float|double|_Bool|size_t)"
        r"(?:\s*\*+\s*(?:const\s*)?)*"
    )
    declaration = re.compile(
        rf"(?m)^(?P<indent>[ \t]*)(?P<storage>static\s+|extern\s+)?"
        rf"(?P<type>{type_part})\s+{re.escape(variable_name)}"
        rf"(?P<array>\s*\[[^\]\n]*\])?\s*(?P<initializer>=\s*[^;\n{{}}]+)?\s*;"
    )
    matches = list(declaration.finditer(source_code))
    if len(matches) != 1:
        array_declaration = re.search(
            rf"(?m)^\s*(?:static\s+|extern\s+)?[^;\n]*\b{re.escape(variable_name)}\s*\[",
            source_code,
        )
        if array_declaration:
            return None, "GLOBAL_ARRAY_UNSUPPORTED"
        return None, "GLOBAL_DECLARATION_NOT_FOUND_OR_AMBIGUOUS"
    match = matches[0]
    if match.group("storage") and match.group("storage").strip() == "extern":
        return None, "EXTERN_GLOBAL_UNSUPPORTED"
    if "volatile" in str(match.group("type") or "").split():
        return None, "VOLATILE_GLOBAL_UNSUPPORTED"
    if match.group("array"):
        return None, "GLOBAL_ARRAY_UNSUPPORTED"
    return match, ""


def _c_function_body_ranges(masked_code: str) -> list[tuple[int, int]]:
    """Return balanced brace ranges; global references must be inside one."""

    ranges: list[tuple[int, int]] = []
    stack: list[int] = []
    for index, char in enumerate(masked_code):
        if char == "{":
            stack.append(index)
        elif char == "}" and stack:
            start = stack.pop()
            ranges.append((start + 1, index))
    return ranges


def _c_statement_bounds(masked_code: str, position: int) -> tuple[int, int]:
    start = max(
        masked_code.rfind(";", 0, position),
        masked_code.rfind("{", 0, position),
        masked_code.rfind("}", 0, position),
    ) + 1
    end = masked_code.find(";", position)
    return start, end


def _c_replace_identifier(
    source_code: str,
    masked_code: str,
    *,
    start: int,
    end: int,
    variable_name: str,
    replacement: str,
) -> str:
    pattern = re.compile(rf"\b{re.escape(variable_name)}\b")
    return pattern.sub(replacement, source_code[start:end])


def _c_access_edits(
    source_code: str,
    masked_code: str,
    *,
    declaration: re.Match[str],
    variable_name: str,
    getter_name: str,
    setter_name: str,
    read_only: bool,
) -> tuple[list[tuple[int, int, str]], bool, str]:
    """Build non-overlapping edits for simple whole-statement scalar access."""

    function_ranges = _c_function_body_ranges(masked_code)
    occurrences = list(re.finditer(rf"\b{re.escape(variable_name)}\b", masked_code))
    edits: list[tuple[int, int, str]] = []
    write_ranges: list[tuple[int, int]] = []
    writable = False

    for occurrence in occurrences:
        start, end = occurrence.span()
        if declaration.start() <= start < declaration.end():
            continue
        if any(range_start <= start < range_end for range_start, range_end in write_ranges):
            continue
        if not any(range_start <= start < range_end for range_start, range_end in function_ranges):
            return [], False, "GLOBAL_REFERENCE_OUTSIDE_FUNCTION"
        before = masked_code[max(0, start - 16):start]
        after = masked_code[end:end + 3]
        if "&" in before[-3:] or after.lstrip().startswith("["):
            return [], False, "ADDRESS_OR_ARRAY_ACCESS_UNSUPPORTED"
        # Only '.' and the complete '->' token are C member access.  A plain
        # comparison such as ``quantity > total_stock`` must not be rejected
        # merely because the character immediately before the identifier is
        # '>'.
        if re.search(r"(?:\.|->)\s*$", before):
            return [], False, "MEMBER_ACCESS_UNSUPPORTED"

        statement_start, statement_end = _c_statement_bounds(masked_code, start)
        if statement_end < 0:
            return [], False, "UNTERMINATED_GLOBAL_ACCESS"
        statement_mask = masked_code[statement_start:statement_end].strip()
        source_prefix = source_code[statement_start:start]
        direct_increment = re.fullmatch(
            rf"(?:\+\+\s*{re.escape(variable_name)}|{re.escape(variable_name)}\s*\+\+)",
            statement_mask,
        )
        direct_decrement = re.fullmatch(
            rf"(?:--\s*{re.escape(variable_name)}|{re.escape(variable_name)}\s*--)",
            statement_mask,
        )
        direct_assignment = re.fullmatch(
            rf"{re.escape(variable_name)}\s*(?P<operator>=|\+=|-=)\s*(?P<value>.+)",
            statement_mask,
            re.DOTALL,
        )

        if direct_increment or direct_decrement:
            if read_only:
                return [], False, "WRITE_TO_READ_ONLY_GLOBAL"
            operator = "+" if direct_increment else "-"
            edits.append((
                statement_start,
                statement_end + 1,
                f"{source_prefix}{setter_name}({getter_name}() {operator} 1);",
            ))
            write_ranges.append((statement_start, statement_end + 1))
            writable = True
            continue

        if direct_assignment:
            if read_only:
                return [], False, "WRITE_TO_READ_ONLY_GLOBAL"
            operator = direct_assignment.group("operator")
            operator_match = re.match(
                r"\s*(?P<operator>=|\+=|-=)",
                masked_code[end:statement_end],
            )
            if operator_match is None:
                return [], False, "ASSIGNMENT_OPERATOR_UNSUPPORTED"
            value_start = end + operator_match.end()
            value = _c_replace_identifier(
                source_code,
                masked_code,
                start=value_start,
                end=statement_end,
                variable_name=variable_name,
                replacement=f"{getter_name}()",
            ).strip()
            if not value:
                return [], False, "ASSIGNMENT_VALUE_UNSUPPORTED"
            replacement_value = value if operator == "=" else f"{getter_name}() {operator[0]} ({value})"
            edits.append((
                statement_start,
                statement_end + 1,
                f"{source_prefix}{setter_name}({replacement_value});",
            ))
            write_ranges.append((statement_start, statement_end + 1))
            writable = True
            continue

        # Assignment or ++/-- inside an expression (for example a for-loop or
        # ``total = counter++``) has value semantics that setters cannot safely
        # preserve without a full C AST. Refuse it instead of changing behavior.
        if re.match(r"\s*(?:=|\+=|-=|\+\+|--)", after) or before.rstrip().endswith(("++", "--")):
            return [], False, "COMPLEX_WRITE_CONTEXT_UNSUPPORTED"
        edits.append((start, end, f"{getter_name}()"))

    ordered = sorted(edits, key=lambda item: (item[0], item[1]))
    previous_end = -1
    for start, end, _ in ordered:
        if start < previous_end:
            return [], False, "OVERLAPPING_GLOBAL_ACCESS_EDITS"
        previous_end = end
    return edits, writable, ""


def apply_encapsulate_c_variable(
    source_code: str,
    *,
    variable_name: str,
    getter_name: str = "",
    setter_name: str = "",
) -> tuple[str, int, dict[str, Any]]:
    """Encapsulate one proven-safe scalar C global using accessors.

    This deliberately supports only a single translation unit and simple
    scalar access patterns. Every other shape is returned as
    ``review_required`` with the original source unchanged.
    """

    def review(reason: str) -> tuple[str, int, dict[str, Any]]:
        return source_code, 0, {
            "status": "review_required",
            "reason": reason,
            "variable_name": variable_name,
        }

    declaration, declaration_error = _c_global_declaration_match(source_code, variable_name)
    if declaration is None:
        return review(declaration_error)
    if any(
        re.match(rf"\s*#.*\b{re.escape(variable_name)}\b", line)
        for line in source_code.splitlines()
    ):
        return review("PREPROCESSOR_DEPENDENT_GLOBAL_UNSUPPORTED")

    type_name = " ".join(str(declaration.group("type") or "").split())
    read_only = "const" in type_name.split()
    getter_name = getter_name or f"get_{variable_name}"
    setter_name = setter_name or f"set_{variable_name}"
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or "") for name in (getter_name, setter_name)):
        return review("INVALID_ACCESSOR_NAME")
    if re.search(rf"\b(?:{re.escape(getter_name)}|{re.escape(setter_name)})\s*\(", source_code):
        return review("ACCESSOR_NAME_COLLISION")

    masked = _mask_c_comments_and_strings(source_code)
    edits, writable, access_error = _c_access_edits(
        source_code,
        masked,
        declaration=declaration,
        variable_name=variable_name,
        getter_name=getter_name,
        setter_name=setter_name,
        read_only=read_only,
    )
    if access_error:
        return review(access_error)
    if not edits:
        return review("NO_GLOBAL_ACCESS_FOUND")

    initializer = str(declaration.group("initializer") or "").strip()
    static_declaration = (
        f"{declaration.group('indent') or ''}static {type_name} {variable_name}"
        f" {initializer}".rstrip()
        + ";"
    )
    # Avoid a second space before an initializer while preserving its text.
    static_declaration = static_declaration.replace("  =", " =")
    accessor = (
        f"\n\n{type_name} {getter_name}(void) {{\n"
        f"    return {variable_name};\n"
        f"}}\n"
    )
    if writable:
        accessor += (
            f"\nvoid {setter_name}({type_name} value) {{\n"
            f"    {variable_name} = value;\n"
            f"}}\n"
        )
    edits.extend([
        (declaration.start(), declaration.end(), static_declaration + accessor),
    ])
    transformed = source_code
    for start, end, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        transformed = f"{transformed[:start]}{replacement}{transformed[end:]}"
    return transformed, len(edits), {
        "status": "success",
        "variable_name": variable_name,
        "getter_name": getter_name,
        "setter_name": setter_name if writable else "",
        "read_only": read_only,
        "writable": writable,
        "global_became_static": True,
        "effective_action_parameters": {
            "variable_name": variable_name,
            "getter_name": getter_name,
            "setter_name": setter_name if writable else "",
        },
    }


def apply_encapsulate_variable(
    source_code: str,
    variable_name: str,
    getter_name: str,
    setter_name: str,
) -> Tuple[str, int]:
    """Backward-compatible wrapper for the legacy C action name."""

    transformed, replacements, metadata = apply_encapsulate_c_variable(
        source_code,
        variable_name=variable_name,
        getter_name=getter_name,
        setter_name=setter_name,
    )
    # Preserve the legacy wrapper contract: callers that explicitly supplied
    # a setter name expect that compatibility accessor even when this file has
    # no current writes.  The dedicated action keeps its stricter minimal-API
    # behavior.
    if (
        replacements > 0
        and metadata.get("status") == "success"
        and setter_name
        and not re.search(rf"\bvoid\s+{re.escape(setter_name)}\s*\(", transformed)
    ):
        declaration, _ = _c_global_declaration_match(source_code, variable_name)
        if declaration is not None:
            type_name = " ".join(str(declaration.group("type") or "").split())
            transformed = (
                transformed.rstrip()
                + f"\n\nvoid {setter_name}({type_name} value) {{\n"
                + f"    {variable_name} = value;\n"
                + "}\n"
            )
    # The legacy API reports one logical refactoring, while the dedicated C
    # implementation reports its declaration/access edit count.
    return transformed, int(replacements > 0 and transformed != source_code)


def _c_accessor_span(masked_code: str, accessor_name: str) -> tuple[int, int] | None:
    match = re.search(rf"\b{re.escape(accessor_name)}\s*\([^)]*\)\s*\{{", masked_code)
    if not match:
        return None
    opening = masked_code.find("{", match.start(), match.end())
    if opening < 0:
        return None
    depth = 0
    for index in range(opening, len(masked_code)):
        if masked_code[index] == "{":
            depth += 1
        elif masked_code[index] == "}":
            depth -= 1
            if depth == 0:
                return match.start(), index + 1
    return None


def _c_numeric_literal_value(text: str) -> float | int | None:
    value = str(text or "").strip()
    if not value:
        return None
    # Strip common scalar suffixes without trying to evaluate arbitrary C.
    value = re.sub(r"(?i)(?:u|l|f)+$", "", value).strip()
    try:
        if re.fullmatch(r"[-+]?\d+", value):
            return int(value, 10)
        if re.fullmatch(
            r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?",
            value,
        ):
            return float(value)
    except ValueError:
        return None
    return None


def _c_initializer_semantically_equal(
    original_initializer: str,
    transformed_initializer: str,
    transformed_code: str,
) -> bool:
    """Compare a literal initializer with a later Introduce Constant macro.

    Structural validation runs after all plan actions.  A valid sequence can
    therefore change ``= 100`` into ``= CONSTANT_100`` after Encapsulate
    Variable has already succeeded.  Treat the two as equal only when the
    macro expands to the same simple scalar literal.
    """

    before = str(original_initializer or "").strip()
    after = str(transformed_initializer or "").strip()
    if before == after:
        return True
    before_expr = before[1:].strip() if before.startswith("=") else before
    after_expr = after[1:].strip() if after.startswith("=") else after
    before_value = _c_numeric_literal_value(before_expr)
    after_value = _c_numeric_literal_value(after_expr)
    if before_value is not None and after_value is not None:
        return float(before_value) == float(after_value)
    if before_value is None or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", after_expr):
        return False
    macro = re.search(
        rf"(?m)^\s*#\s*define\s+{re.escape(after_expr)}\s+(?P<value>[^\s/]+)",
        transformed_code,
    )
    if macro is None:
        return False
    macro_value = _c_numeric_literal_value(macro.group("value"))
    return macro_value is not None and float(before_value) == float(macro_value)


def validate_c_encapsulated_variable(
    original_code: str,
    transformed_code: str,
    *,
    variable_name: str,
    getter_name: str,
    setter_name: str,
) -> dict[str, Any]:
    """Validate that one C scalar global is no longer directly exposed."""

    before, before_error = _c_global_declaration_match(original_code, variable_name)
    after, after_error = _c_global_declaration_match(transformed_code, variable_name)
    if before is None or after is None:
        return {
            "passed": False,
            "reason": before_error or after_error or "GLOBAL_DECLARATION_NOT_FOUND",
            "checks": {"target_global_existed_before": before is not None},
        }
    original_type = " ".join(str(before.group("type") or "").split())
    transformed_type = " ".join(str(after.group("type") or "").split())
    read_only = "const" in original_type.split()
    original_masked = _mask_c_comments_and_strings(original_code)
    _, writable, access_error = _c_access_edits(
        original_code,
        original_masked,
        declaration=before,
        variable_name=variable_name,
        getter_name=getter_name,
        setter_name=setter_name,
        read_only=read_only,
    )
    if access_error:
        return {
            "passed": False,
            "reason": f"original_access_not_safe:{access_error}",
            "checks": {"target_global_existed_before": True},
        }

    transformed_masked = _mask_c_comments_and_strings(transformed_code)
    allowed_spans = [span for span in (
        _c_accessor_span(transformed_masked, getter_name),
        _c_accessor_span(transformed_masked, setter_name) if writable else None,
    ) if span]
    direct_accesses = []
    for match in re.finditer(rf"\b{re.escape(variable_name)}\b", transformed_masked):
        if after.start() <= match.start() < after.end():
            continue
        if any(start <= match.start() < end for start, end in allowed_spans):
            continue
        direct_accesses.append(match.start())
    getter_exists = _c_accessor_span(transformed_masked, getter_name) is not None
    setter_exists = _c_accessor_span(transformed_masked, setter_name) is not None
    checks = {
        "target_global_existed_before": True,
        "global_is_static_after": bool(str(after.group("storage") or "").strip() == "static"),
        "getter_exists": getter_exists,
        "setter_exists_when_writable": (not writable) or setter_exists,
        "direct_unsafe_accesses_replaced": not direct_accesses,
        # ``_c_global_declaration_match`` above only succeeds for exactly one
        # scalar definition. Accessor bodies are intentionally excluded from
        # this declaration-level guarantee.
        "no_duplicate_global_declaration": True,
        "original_type_preserved": original_type == transformed_type,
        "original_initializer_preserved": _c_initializer_semantically_equal(
            str(before.group("initializer") or ""),
            str(after.group("initializer") or ""),
            transformed_code,
        ),
    }
    return {
        "passed": all(checks.values()),
        "variable_name": variable_name,
        "getter_name": getter_name,
        "setter_name": setter_name if writable else "",
        "writable": writable,
        "direct_access_count": len(direct_accesses),
        "checks": checks,
    }


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
    "apply_encapsulate_c_variable",
    "validate_c_encapsulated_variable",
    "apply_remove_dead_code",
    "apply_rename_symbol",
    "apply_replace_literal",
    "apply_normalize_multiline_statement",
]
