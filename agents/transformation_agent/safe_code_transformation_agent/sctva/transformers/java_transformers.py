"""Conservative text-based Java transformers for mock-safe execution.

This module mirrors the Python transformer behavior where possible:
- Introduce Constant generates stable names like MAGIC_NUMBER_6.
- Generic names like EXTRACTED_CONSTANT are normalized to value-based names.
- Constants are inserted into the class body before use.
- Replacements avoid touching existing constant declarations.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple


_TYPE_DECL_RE = re.compile(r"\b(class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{")
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
        return f"MAGIC_BOOL_{str(value).upper()}"

    if value is None:
        return "MAGIC_NONE"

    if isinstance(value, int):
        if value < 0:
            return f"MAGIC_NUMBER_NEG_{abs(value)}"
        return f"MAGIC_NUMBER_{value}"

    if isinstance(value, float):
        text = str(value).replace("-", "NEG_").replace(".", "_")
        return f"MAGIC_NUMBER_{_sanitize_identifier(text)}"

    if isinstance(value, str):
        short = value[:24]
        return f"MAGIC_STRING_{_sanitize_identifier(short)}"

    return "MAGIC_VALUE"


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


def apply_rename_symbol(source_code: str, old_name: str, new_name: str) -> Tuple[str, int]:
    pattern = rf"\b{re.escape(old_name)}\b"
    transformed, count = re.subn(pattern, new_name, source_code)
    return transformed, count


def apply_replace_literal(source_code: str, old_literal: Any, new_literal: Any) -> Tuple[str, int]:
    old_text = _to_java_literal(old_literal)
    new_text = _to_java_literal(new_literal)
    return source_code.replace(old_text, new_text), source_code.count(old_text)


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


def apply_remove_dead_code(source_code: str, method_name: str, class_name: Optional[str] = None) -> Tuple[str, int]:
    if not method_name:
        raise ValueError("remove_dead_code requires 'method_name'.")

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

    method_start = scope_start + match.start()
    brace_idx = scope_start + match.end() - 1
    method_end = _find_matching_brace(source_code, brace_idx)
    if method_end is None:
        return source_code, 0

    before = source_code[:method_start].rstrip()
    after = source_code[method_end + 1 :].lstrip()
    return f"{before}\n{after}", 1


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
