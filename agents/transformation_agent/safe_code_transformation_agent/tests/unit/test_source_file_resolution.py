"""Conservative Python Message Chains -> Hide Delegate refactoring."""

from __future__ import annotations

import ast
import copy
import re
from typing import Any


def _review(source: str, reason: str, **details: Any) -> tuple[str, int, dict[str, Any]]:
    return source, 0, {"status": "review_required", "reason": reason, **details}


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for match in re.finditer("\n", source):
        offsets.append(match.end())
    return offsets


def _offset(offsets: list[int], line: int, column: int) -> int:
    return offsets[line - 1] + column


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _inside(node: ast.AST, ancestor: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    current: ast.AST | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = parents.get(current)
    return False


def _customer_names(tree: ast.Module, source_class: str) -> set[str]:
    """Collect names whose owner type is explicit in this module."""

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                if isinstance(argument.annotation, ast.Name) and argument.annotation.id == source_class:
                    names.add(argument.arg)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name) and node.value.func.id == source_class:
                names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if isinstance(node.annotation, ast.Name) and node.annotation.id == source_class:
                names.add(node.target.id)
    return names


def resolve_hide_delegate_target(
    source_code: str,
    *,
    source_class: str = "",
    delegate_member: str = "",
    delegated_member: str = "",
    new_method_name: str = "",
    method_name: str = "",
    source_line: int | None = None,
) -> dict[str, Any]:
    """Recover one unambiguous Python Hide Delegate target from AST evidence.

    RDP plans can contain repository/file-derived owner names (for example
    ``jarvis``) or can omit the delegate/delegated members entirely.  Those
    values are treated as hints, not proof.  SCTVA validates the real class and
    client message chain in the source before returning an executable target.

    ``method_name`` and ``source_line`` are optional narrowing hints preserved
    from the original planner step.  They prevent a large source file from
    recovering an unrelated message chain elsewhere in the module.
    """

    requested_source_class = str(source_class or "").strip()
    requested_method = str(method_name or "").strip()

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {"status": "review_required", "reason": "SOURCE_PARSE_FAILED"}

    parents = _parents(tree)
    classes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and not node.bases
        and not node.keywords
        and not node.decorator_list
    }
    module_functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    if not classes:
        # This is the exact Jarvis-style failure mode: RDP may call the module
        # name a "source_class", but there is no class owner in the source.
        # Hide Delegate is therefore structurally not applicable; fabricating a
        # class would be a different refactoring (Extract Class/Move Function).
        if requested_method and requested_method in module_functions:
            return {
                "status": "not_applicable",
                "reason": "MODULE_LEVEL_FUNCTION_HAS_NO_HIDE_DELEGATE_OWNER",
                "method": requested_method,
                "requested_source_class": requested_source_class,
            }
        return {
            "status": "not_applicable",
            "reason": "HIDE_DELEGATE_REQUIRES_CLASS_OWNER",
            "requested_source_class": requested_source_class,
        }

    # If the planner names a class that does not exist, do not use that stale
    # value as a hard filter.  Search the actual classes and accept recovery
    # only when source evidence yields one unambiguous owner/delegate relation.
    source_class_is_stale = bool(
        requested_source_class and requested_source_class not in classes
    )
    effective_source_filter = (
        requested_source_class
        if requested_source_class in classes
        else ""
    )

    def containing_function(node: ast.AST) -> ast.AST | None:
        current = parents.get(node)
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return current
            current = parents.get(current)
        return None

    def line_in_function(function: ast.AST | None) -> bool:
        if source_line is None or function is None:
            return True
        start = int(getattr(function, "lineno", 0) or 0)
        end = int(getattr(function, "end_lineno", start) or start)
        return start <= source_line <= end

    # Infer simple instance names so unannotated call sites can still establish
    # ownership, e.g. ``customer = Customer(...)``.
    instance_types: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name) and node.value.func.id in classes:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        instance_types[target.id] = node.value.func.id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in classes
            ):
                instance_types[node.target.id] = value.func.id
            elif isinstance(node.annotation, ast.Name) and node.annotation.id in classes:
                instance_types[node.target.id] = node.annotation.id

    # Infer parameter owner types from simple module-level call sites.  This is
    # important for legacy code where report functions often omit type hints:
    # ``def report(order): ...`` followed by ``report(order_instance)``.
    function_parameter_types: dict[tuple[str, str], set[str]] = {}
    module_function_nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        function = module_function_nodes.get(node.func.id)
        if function is None:
            continue
        positional_params = [
            *getattr(function.args, "posonlyargs", []),
            *function.args.args,
        ]
        for index, expression in enumerate(node.args):
            if index >= len(positional_params):
                break
            parameter = positional_params[index].arg
            inferred = ""
            if (
                isinstance(expression, ast.Call)
                and isinstance(expression.func, ast.Name)
                and expression.func.id in classes
            ):
                inferred = expression.func.id
            elif isinstance(expression, ast.Name):
                inferred = instance_types.get(expression.id, "")
            if inferred:
                function_parameter_types.setdefault(
                    (function.name, parameter), set()
                ).add(inferred)

    def owner_names_for_class(class_name: str) -> set[str]:
        names = set(_customer_names(tree, class_name))
        names.update(
            name
            for name, inferred_class in instance_types.items()
            if inferred_class == class_name
        )
        for (function_name, parameter), inferred_classes in function_parameter_types.items():
            if inferred_classes == {class_name}:
                names.add(parameter)
        return names

    candidates: set[tuple[str, str, str, bool]] = set()
    for class_name, owner in classes.items():
        if effective_source_filter and class_name != effective_source_filter:
            continue

        constructor = next(
            (
                node
                for node in owner.body
                if isinstance(node, ast.FunctionDef) and node.name == "__init__"
            ),
            None,
        )
        if constructor is None:
            continue

        owned_members: set[str] = set()
        for statement in constructor.body:
            if isinstance(statement, ast.Assign):
                targets = statement.targets
            elif isinstance(statement, ast.AnnAssign):
                targets = [statement.target]
            else:
                continue
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    owned_members.add(target.attr)

        typed_owner_names = owner_names_for_class(class_name)
        if not typed_owner_names:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Attribute):
                continue
            delegate = node.value
            if not isinstance(delegate.value, ast.Name):
                continue
            if delegate.value.id not in typed_owner_names:
                continue
            if delegate.attr not in owned_members:
                continue
            if delegate_member and delegate.attr != delegate_member:
                continue
            if delegated_member and node.attr != delegated_member:
                continue
            if _inside(node, owner, parents):
                continue

            function = containing_function(node)
            if requested_method and (
                function is None or getattr(function, "name", "") != requested_method
            ):
                continue
            if not line_in_function(function):
                continue

            parent = parents.get(node)
            is_call = isinstance(parent, ast.Call) and parent.func is node
            if is_call and (parent.args or parent.keywords):
                continue
            candidates.add((class_name, delegate.attr, node.attr, is_call))

    if not candidates:
        if requested_method and requested_method in module_functions:
            return {
                "status": "not_applicable",
                "reason": "NO_CLASS_OWNED_DELEGATE_CHAIN_IN_TARGET_FUNCTION",
                "method": requested_method,
                "requested_source_class": requested_source_class,
            }
        return {
            "status": "not_applicable",
            "reason": "HIDE_DELEGATE_TARGET_NOT_FOUND",
            "requested_source_class": requested_source_class,
        }

    owner_delegates = {(item[0], item[1]) for item in candidates}
    if len(owner_delegates) != 1:
        return {
            "status": "review_required",
            "reason": "AMBIGUOUS_HIDE_DELEGATE_TARGET",
            "candidate_count": len(candidates),
        }

    targets = []
    for resolved_class, resolved_delegate, resolved_member, is_call in sorted(candidates):
        resolved_name = new_method_name or (
            resolved_member
            if is_call and resolved_member.startswith("get_")
            else f"get_{resolved_member}"
        )
        targets.append({
            "source_class": resolved_class,
            "delegate_member": resolved_delegate,
            "delegated_member": resolved_member,
            "new_method_name": resolved_name,
            "delegated_member_is_call": is_call,
        })

    if len({target["new_method_name"] for target in targets}) != len(targets):
        return {
            "status": "review_required",
            "reason": "AMBIGUOUS_HIDE_DELEGATE_FORWARDER_NAME",
        }

    first_target = targets[0]
    return {
        "status": "success",
        **first_target,
        "targets": targets,
        "strategy": (
            "stale_source_class_semantic_recovery"
            if source_class_is_stale
            else "typed_python_message_chain_recovery"
        ),
        "requested_source_class": requested_source_class,
        "requested_method": requested_method,
    }

def _method_is_equivalent(
    method: ast.FunctionDef,
    delegate_member: str,
    delegated_member: str,
    delegated_is_call: bool,
) -> bool:
    if method.decorator_list or len(method.args.args) != 1 or method.args.args[0].arg != "self":
        return False
    body = list(method.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return False
    expected: ast.expr = ast.Attribute(
        value=ast.Attribute(value=ast.Name(id="self", ctx=ast.Load()), attr=delegate_member, ctx=ast.Load()),
        attr=delegated_member,
        ctx=ast.Load(),
    )
    if delegated_is_call:
        expected = ast.Call(func=expected, args=[], keywords=[])
    return ast.dump(body[0].value, include_attributes=False) == ast.dump(expected, include_attributes=False)


def apply_hide_delegate(
    source_code: str,
    *,
    source_class: str,
    delegate_member: str,
    delegated_member: str,
    new_method_name: str = "",
) -> tuple[str, int, dict[str, Any]]:
    """Add a Python forwarding method and shorten statically proven chains."""

    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or "") for value in (source_class, delegate_member, delegated_member)):
        return _review(source_code, "INVALID_HIDE_DELEGATE_TARGET")
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return _review(source_code, "SOURCE_PARSE_FAILED")
    if any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in {"getattr", "setattr", "hasattr", "delattr"}
        for node in ast.walk(tree)
    ):
        return _review(source_code, "DYNAMIC_ATTRIBUTE_ACCESS_UNSUPPORTED")

    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == source_class]
    if len(classes) != 1:
        return _review(source_code, "SOURCE_CLASS_NOT_FOUND_OR_AMBIGUOUS")
    owner = classes[0]
    if owner.bases or owner.keywords or owner.decorator_list:
        return _review(source_code, "INHERITANCE_OR_METACLASS_UNSUPPORTED")
    constructor = next((node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"), None)
    if constructor is None or not any(
        isinstance(node, (ast.Assign, ast.AnnAssign))
        and isinstance(getattr(node, "target", None) if isinstance(node, ast.AnnAssign) else (node.targets[0] if len(node.targets) == 1 else None), ast.Attribute)
        and isinstance((getattr(node, "target", None) if isinstance(node, ast.AnnAssign) else node.targets[0]).value, ast.Name)
        and (getattr(node, "target", None) if isinstance(node, ast.AnnAssign) else node.targets[0]).value.id == "self"
        and (getattr(node, "target", None) if isinstance(node, ast.AnnAssign) else node.targets[0]).attr == delegate_member
        for node in constructor.body
    ):
        return _review(source_code, "DELEGATE_MEMBER_OWNERSHIP_NOT_PROVEN")

    new_method_name = new_method_name or (
        delegated_member if delegated_member.startswith("get_") else f"get_{delegated_member}"
    )
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", new_method_name):
        return _review(source_code, "INVALID_NEW_METHOD_NAME")
    parents = _parents(tree)
    customer_names = _customer_names(tree, source_class)
    if not customer_names:
        return _review(source_code, "CLIENT_OWNER_TYPE_NOT_PROVEN")

    chain_nodes: list[tuple[ast.AST, ast.Name, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr != delegated_member:
            continue
        delegate = node.value
        if not (
            isinstance(delegate, ast.Attribute)
            and delegate.attr == delegate_member
            and isinstance(delegate.value, ast.Name)
            and delegate.value.id in customer_names
        ):
            continue
        if _inside(node, owner, parents):
            continue
        parent = parents.get(node)
        is_call = isinstance(parent, ast.Call) and parent.func is node
        if is_call and (parent.args or parent.keywords):
            return _review(source_code, "DELEGATED_CALL_ARGUMENTS_UNSUPPORTED")
        if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            return _review(source_code, "DELEGATED_WRITE_UNSUPPORTED")
        chain_nodes.append((parent if is_call else node, delegate.value, is_call))
    if not chain_nodes:
        return _review(source_code, "MESSAGE_CHAIN_NOT_FOUND")
    call_kinds = {item[2] for item in chain_nodes}
    if len(call_kinds) != 1:
        return _review(source_code, "MIXED_FIELD_AND_METHOD_DELEGATION_UNSUPPORTED")
    delegated_is_call = call_kinds.pop()

    same_named = [node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == new_method_name]
    if len(same_named) > 1:
        return _review(source_code, "DUPLICATE_OWNER_METHOD_NAME")
    create_method = not same_named
    if same_named and not _method_is_equivalent(same_named[0], delegate_member, delegated_member, delegated_is_call):
        return _review(source_code, "OWNER_METHOD_NAME_COLLISION")

    offsets = _line_offsets(source_code)
    edits: list[tuple[int, int, str]] = []
    for chain, base, is_call in chain_nodes:
        base_text = ast.unparse(base)
        replacement = f"{base_text}.{new_method_name}()"
        edits.append((
            _offset(offsets, chain.lineno, chain.col_offset),
            _offset(offsets, chain.end_lineno, chain.end_col_offset),
            replacement,
        ))
    if create_method:
        owner_end = offsets[owner.end_lineno] if owner.end_lineno < len(offsets) else len(source_code)
        indent = " " * (owner.col_offset + 4)
        delegate_access = f"self.{delegate_member}.{delegated_member}"
        target = f"{delegate_access}()" if delegated_is_call else delegate_access
        method_text = f"\n{indent}def {new_method_name}(self):\n{indent}    return {target}\n"
        edits.append((owner_end, owner_end, method_text))
    ordered = sorted(edits, key=lambda item: (item[0], item[1]))
    if any(right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
        return _review(source_code, "OVERLAPPING_MESSAGE_CHAIN_EDITS")
    transformed = source_code
    for start, end, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        transformed = f"{transformed[:start]}{replacement}{transformed[end:]}"
    try:
        ast.parse(transformed)
        compile(transformed, "<sctva-hide-delegate>", "exec")
    except (SyntaxError, TypeError, ValueError):
        return _review(source_code, "TRANSFORMED_SOURCE_PARSE_FAILED")
    return transformed, len(edits), {
        "status": "success",
        "language": "python",
        "source_class": source_class,
        "delegate_member": delegate_member,
        "delegated_member": delegated_member,
        "new_method_name": new_method_name,
        "delegated_member_is_call": delegated_is_call,
        "created_forwarder": create_method,
        "updated_call_sites": len(chain_nodes),
        "effective_action_parameters": {
            "source_class": source_class,
            "delegate_member": delegate_member,
            "delegated_member": delegated_member,
            "new_method_name": new_method_name,
            "delegated_member_is_call": delegated_is_call,
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
    delegated_member_is_call: bool = False,
) -> dict[str, Any]:
    """Action-specific proof that a Python message chain was truly hidden."""

    try:
        before = ast.parse(original_code)
        after = ast.parse(transformed_code)
    except SyntaxError:
        return {"passed": False, "reason": "parse_failed"}
    before_owner = next((node for node in before.body if isinstance(node, ast.ClassDef) and node.name == source_class), None)
    after_owner = next((node for node in after.body if isinstance(node, ast.ClassDef) and node.name == source_class), None)
    if before_owner is None or after_owner is None:
        return {"passed": False, "reason": "source_class_missing"}

    def chains(tree: ast.AST, owner: ast.ClassDef) -> int:
        parents = _parents(tree)
        count = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr != delegated_member:
                continue
            if not isinstance(node.value, ast.Attribute) or node.value.attr != delegate_member:
                continue
            if _inside(node, owner, parents):
                continue
            parent = parents.get(node)
            if delegated_member_is_call != (isinstance(parent, ast.Call) and parent.func is node):
                continue
            count += 1
        return count

    methods = [node for node in after_owner.body if isinstance(node, ast.FunctionDef) and node.name == new_method_name]
    checks = {
        "original_message_chain_existed": chains(before, before_owner) > 0,
        "forwarding_method_added_or_preserved": len(methods) == 1,
        "forwarder_targets_correct_delegate": bool(methods) and _method_is_equivalent(
            methods[0], delegate_member, delegated_member, delegated_member_is_call
        ),
        "client_message_chain_shortened": chains(after, after_owner) == 0,
        "matching_call_sites_updated": sum(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == new_method_name
            for node in ast.walk(after)
        ) >= chains(before, before_owner),
        "delegate_member_preserved": any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr == delegate_member
            for node in ast.walk(after_owner)
        ),
        "no_duplicate_forwarder": len(methods) == 1,
        "python_syntax_valid": True,
    }
    return {"passed": all(checks.values()), "language": "python", "checks": checks}