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
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return "0"
    return str(value)


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
        text = re.sub(r"[^A-Za-z0-9_]", "_", text).strip("_")
        return f"MAGIC_NUMBER_{text}"
    if isinstance(value, str):
        return f"MAGIC_STRING_{_sanitize_identifier(value[:24])}"
    return "MAGIC_VALUE"


def _normalize_constant_name(constant_name: Optional[str], literal_value: Any) -> str:
    if not constant_name:
        return _constant_name_from_value(literal_value)

    cleaned = _sanitize_identifier(str(constant_name))
    if cleaned in {"EXTRACTED_CONSTANT", "MAGIC_CONSTANT", "CONSTANT", "VALUE_CONSTANT"}:
        return _constant_name_from_value(literal_value)
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


def apply_rename_symbol(source_code: str, old_name: str, new_name: str) -> Tuple[str, int]:
    pattern = rf"\b{re.escape(old_name)}\b"
    transformed, count = re.subn(pattern, new_name, source_code)
    return transformed, count


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
    if replacements > 0:
        transformed = _insert_define(transformed, preferred_name, literal_value)
    return transformed, replacements


def apply_replace_literal(source_code: str, old_literal: Any, new_literal: Any) -> Tuple[str, int]:
    old_text = _to_c_literal(old_literal)
    new_text = _to_c_literal(new_literal)
    return source_code.replace(old_text, new_text), source_code.count(old_text)


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


def apply_remove_dead_code(source_code: str, method_name: str, class_name: Optional[str] = None) -> Tuple[str, int]:
    if not method_name:
        raise ValueError("remove_dead_code requires 'method_name'.")

    pattern = re.compile(
        rf"(?ms)^[ \t]*(?:[A-Za-z_][A-Za-z0-9_\s\*]*?\s+)+{re.escape(method_name)}\s*\([^;{{}}]*\)\s*\{{"
    )
    match = pattern.search(source_code)
    if not match:
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
    "apply_remove_dead_code",
    "apply_rename_symbol",
    "apply_replace_literal",
]