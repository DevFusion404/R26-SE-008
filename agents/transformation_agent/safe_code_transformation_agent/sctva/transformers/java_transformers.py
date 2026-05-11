"""Conservative text-based Java transformers for mock-safe execution."""

from __future__ import annotations

import re
from typing import Any, Tuple


def _to_java_literal(value: Any) -> str:
    if isinstance(value, str):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def apply_rename_symbol(source_code: str, old_name: str, new_name: str) -> Tuple[str, int]:
    pattern = rf"\b{re.escape(old_name)}\b"
    transformed, count = re.subn(pattern, new_name, source_code)
    return transformed, count


def apply_replace_literal(source_code: str, old_literal: Any, new_literal: Any) -> Tuple[str, int]:
    old_text = _to_java_literal(old_literal)
    new_text = _to_java_literal(new_literal)
    return source_code.replace(old_text, new_text), source_code.count(old_text)


def apply_extract_constant(source_code: str, literal_value: Any, constant_name: str) -> Tuple[str, int]:
    literal_text = _to_java_literal(literal_value)
    replacements = source_code.count(literal_text)
    transformed = source_code.replace(literal_text, constant_name)

    class_match = re.search(r"class\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{", transformed)
    if class_match:
        insert_idx = class_match.end()
        declaration = f"\\n    private static final var {constant_name} = {literal_text};\\n"
        transformed = transformed[:insert_idx] + declaration + transformed[insert_idx:]

    return transformed, replacements


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
