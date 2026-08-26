"""Conservative Java Message Chains -> Hide Delegate refactoring."""

from __future__ import annotations

import re
from typing import Any

from .java_extract_class import _mask_c_like, _member_indent, _parse_java_class


_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]*"


def _review(source: str, reason: str, **details: Any) -> tuple[str, int, dict[str, Any]]:
    return source, 0, {"status": "review_required", "reason": reason, **details}


def _java_owner_names(masked: str, source_class: str) -> set[str]:
    names = set()
    declaration = re.compile(rf"\b{re.escape(source_class)}\s+({_IDENTIFIER})\b")
    for match in declaration.finditer(masked):
        names.add(match.group(1))
    return names


def _getter_name(member: str) -> str:
    return member if member.startswith("get") else f"get{member[:1].upper()}{member[1:]}"


def _owner_accessor_for_field(model: Any, delegate_member: str) -> str:
    expected = f"get{delegate_member[:1].upper()}{delegate_member[1:]}"
    methods = model.methods_by_name.get(expected, [])
    for method in methods:
        if not method.parameters and re.fullmatch(rf"\s*return\s+{re.escape(delegate_member)}\s*;\s*", method.body.strip(), re.DOTALL):
            return expected
    return ""


def _equivalent_delegate_method(method: Any, delegate_member: str, delegate_getter: str) -> bool:
    return (
        not method.parameters
        and re.fullmatch(
            rf"\s*return\s+{re.escape(delegate_member)}\s*\.\s*{re.escape(delegate_getter)}\s*\(\s*\)\s*;\s*",
            method.body.strip(),
            re.DOTALL,
        ) is not None
    )


def apply_hide_delegate(
    source_code: str,
    *,
    source_class: str,
    delegate_member: str,
    delegated_member: str,
    new_method_name: str = "",
) -> tuple[str, int, dict[str, Any]]:
    """Introduce one Java forwarding getter and shorten proven client chains."""

    if not all(re.fullmatch(_IDENTIFIER, value or "") for value in (source_class, delegate_member, delegated_member)):
        return _review(source_code, "INVALID_HIDE_DELEGATE_TARGET")
    masked = _mask_c_like(source_code)
    if re.search(r"\b(?:Class\.forName|getMethod|invoke|setAccessible|Proxy\.newProxyInstance)\b", masked):
        return _review(source_code, "REFLECTION_OR_DYNAMIC_PROXY_UNSUPPORTED")
    owner = _parse_java_class(source_code, source_class)
    if owner is None:
        return _review(source_code, "SOURCE_CLASS_NOT_FOUND")
    field = owner.fields.get(delegate_member)
    if field is None:
        return _review(source_code, "DELEGATE_MEMBER_OWNERSHIP_NOT_PROVEN")
    delegate_type = re.sub(r"<.*>", "", field.type_name).strip()
    delegate_model = _parse_java_class(source_code, delegate_type)
    if delegate_model is None:
        return _review(source_code, "DELEGATE_TYPE_NOT_AVAILABLE_IN_SOURCE")
    delegate_getter = _getter_name(delegated_member)
    delegate_methods = [method for method in delegate_model.methods_by_name.get(delegate_getter, []) if not method.parameters]
    if len(delegate_methods) != 1:
        return _review(source_code, "DELEGATED_GETTER_NOT_FOUND_OR_AMBIGUOUS")
    return_type = delegate_methods[0].return_type.strip()
    if not return_type or return_type == "void":
        return _review(source_code, "DELEGATED_GETTER_RETURN_TYPE_UNSUPPORTED")
    new_method_name = new_method_name or delegate_getter
    if not re.fullmatch(_IDENTIFIER, new_method_name):
        return _review(source_code, "INVALID_NEW_METHOD_NAME")

    owner_methods = owner.methods_by_name.get(new_method_name, [])
    if len(owner_methods) > 1:
        return _review(source_code, "DUPLICATE_OWNER_METHOD_NAME")
    create_method = not owner_methods
    if owner_methods and not _equivalent_delegate_method(owner_methods[0], delegate_member, delegate_getter):
        return _review(source_code, "OWNER_METHOD_NAME_COLLISION")

    owner_accessor = _owner_accessor_for_field(owner, delegate_member)
    owner_names = _java_owner_names(masked, source_class)
    if not owner_names:
        return _review(source_code, "CLIENT_OWNER_TYPE_NOT_PROVEN")
    chain_patterns = [
        re.compile(rf"\b(?P<base>{_IDENTIFIER})\s*\.\s*{re.escape(delegate_member)}\s*\.\s*{re.escape(delegate_getter)}\s*\(\s*\)"),
    ]
    if owner_accessor:
        chain_patterns.append(
            re.compile(rf"\b(?P<base>{_IDENTIFIER})\s*\.\s*{re.escape(owner_accessor)}\s*\(\s*\)\s*\.\s*{re.escape(delegate_getter)}\s*\(\s*\)")
        )
    edits: list[tuple[int, int, str]] = []
    for pattern in chain_patterns:
        for match in pattern.finditer(masked):
            if match.group("base") not in owner_names:
                continue
            if owner.start <= match.start() < owner.close_brace:
                continue
            edits.append((match.start(), match.end(), f"{match.group('base')}.{new_method_name}()"))
    if not edits:
        return _review(source_code, "MESSAGE_CHAIN_NOT_FOUND")
    edits.sort(key=lambda item: (item[0], item[1]))
    if any(right[0] < left[1] for left, right in zip(edits, edits[1:])):
        return _review(source_code, "OVERLAPPING_MESSAGE_CHAIN_EDITS")
    if create_method:
        indent = _member_indent(source_code, owner)
        method = (
            f"\n{indent}public {return_type} {new_method_name}() {{\n"
            f"{indent}    return {delegate_member}.{delegate_getter}();\n"
            f"{indent}}}\n"
        )
        edits.append((owner.close_brace, owner.close_brace, method))
    transformed = source_code
    for start, end, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        transformed = f"{transformed[:start]}{replacement}{transformed[end:]}"
    if _parse_java_class(transformed, source_class) is None:
        return _review(source_code, "TRANSFORMED_SOURCE_PARSE_FAILED")
    return transformed, len(edits), {
        "status": "success",
        "language": "java",
        "source_class": source_class,
        "delegate_member": delegate_member,
        "delegated_member": delegated_member,
        "delegate_getter": delegate_getter,
        "new_method_name": new_method_name,
        "created_forwarder": create_method,
        "updated_call_sites": len(edits) - (1 if create_method else 0),
        "effective_action_parameters": {
            "source_class": source_class,
            "delegate_member": delegate_member,
            "delegated_member": delegated_member,
            "new_method_name": new_method_name,
            "delegate_getter": delegate_getter,
        },
    }


def validate_hide_delegate(
    original_code: str,
    transformed_code: str,
    *,
    source_class: str,
    delegate_member: str,
    delegated_member: str,
    new_method_name: str,
    delegate_getter: str = "",
) -> dict[str, Any]:
    """Action-specific structural proof for Java Hide Delegate."""

    before_owner = _parse_java_class(original_code, source_class)
    after_owner = _parse_java_class(transformed_code, source_class)
    if before_owner is None or after_owner is None:
        return {"passed": False, "reason": "source_class_missing"}
    getter = delegate_getter or _getter_name(delegated_member)
    before_masked = _mask_c_like(original_code)
    after_masked = _mask_c_like(transformed_code)
    owner_accessor = _owner_accessor_for_field(before_owner, delegate_member)
    patterns = [rf"\b{_IDENTIFIER}\s*\.\s*{re.escape(delegate_member)}\s*\.\s*{re.escape(getter)}\s*\(\s*\)"]
    if owner_accessor:
        patterns.append(rf"\b{_IDENTIFIER}\s*\.\s*{re.escape(owner_accessor)}\s*\(\s*\)\s*\.\s*{re.escape(getter)}\s*\(\s*\)")
    before_chains = sum(len(re.findall(pattern, before_masked)) for pattern in patterns)
    after_chains = sum(len(re.findall(pattern, after_masked)) for pattern in patterns)
    methods = after_owner.methods_by_name.get(new_method_name, [])
    checks = {
        "original_message_chain_existed": before_chains > 0,
        "forwarding_method_added_or_preserved": len(methods) == 1,
        "forwarder_targets_correct_delegate": bool(methods) and _equivalent_delegate_method(methods[0], delegate_member, getter),
        "client_message_chain_shortened": after_chains == 0,
        "matching_call_sites_updated": len(re.findall(rf"\b{_IDENTIFIER}\s*\.\s*{re.escape(new_method_name)}\s*\(\s*\)", after_masked)) >= before_chains,
        "delegate_member_preserved": delegate_member in after_owner.fields,
        "no_duplicate_forwarder": len(methods) == 1,
        "java_source_parseable": True,
    }
    return {"passed": all(checks.values()), "language": "java", "checks": checks}
