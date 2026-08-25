"""Owned-composition Inline Class transformer for Python.

This module handles the common Fowler-style Inline Class shape where a tiny
helper object is owned by exactly one other class, for example::

    class CustomerContact:
        def __init__(self, phone):
            self.phone = phone

        def formatted_phone(self):
            return f"Phone: {self.phone}"

    class Customer:
        def __init__(self, phone):
            self.contact = CustomerContact(phone)

The safe transformation is::

    class Customer:
        def __init__(self, phone):
            self.phone = phone

        def formatted_phone(self):
            return f"Phone: {self.phone}"

The implementation is intentionally conservative.  If ownership, constructor
mapping, dynamic usage, inheritance, naming, or call-site rewriting is
ambiguous, it returns ``review_required`` without changing source code.
"""

from __future__ import annotations

import ast
import copy
import re
import textwrap
from typing import Any, Sequence, Tuple


def _review(
    source_code: str,
    *,
    class_to_inline: str,
    reason: str,
    **metadata: Any,
) -> Tuple[str, int, dict[str, Any]]:
    return source_code, 0, {
        "status": "review_required",
        "reason": reason,
        "class_to_inline": class_to_inline,
        **metadata,
    }


def _not_applicable(
    source_code: str,
    *,
    class_to_inline: str,
    reason: str,
) -> Tuple[str, int, dict[str, Any]]:
    return source_code, 0, {
        "status": "not_applicable",
        "reason": reason,
        "class_to_inline": class_to_inline,
    }


def _line_offsets(source_code: str) -> list[int]:
    offsets = [0]
    for match in re.finditer(r"\n", source_code):
        offsets.append(match.end())
    return offsets


def _position_offset(
    line_offsets: Sequence[int],
    line: int | None,
    column: int | None,
) -> int:
    if not isinstance(line, int) or not isinstance(column, int) or line <= 0:
        return -1
    if line > len(line_offsets):
        return -1
    return line_offsets[line - 1] + column


def _line_end_offset(
    source_code: str,
    line_offsets: Sequence[int],
    line: int | None,
) -> int:
    if not isinstance(line, int) or line <= 0:
        return -1
    return line_offsets[line] if line < len(line_offsets) else len(source_code)


def _edits_do_not_overlap(edits: Sequence[tuple[int, int, str]]) -> bool:
    ordered = sorted(edits, key=lambda item: (item[0], item[1]))
    previous_end = -1
    for start, end, _ in ordered:
        if start < 0 or end < start or start < previous_end:
            return False
        previous_end = max(previous_end, end)
    return True


def _apply_edits(
    source_code: str,
    edits: Sequence[tuple[int, int, str]],
) -> str:
    transformed = source_code
    for start, end, replacement in sorted(
        edits,
        key=lambda item: item[0],
        reverse=True,
    ):
        transformed = f"{transformed[:start]}{replacement}{transformed[end:]}"
    return transformed


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _is_descendant(
    node: ast.AST,
    ancestor: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    current: ast.AST | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = parents.get(current)
    return False


def _class_method(
    owner: ast.ClassDef,
    name: str,
) -> ast.FunctionDef | None:
    matches = [
        item
        for item in owner.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    ]
    return matches[0] if len(matches) == 1 else None


def _strip_docstring(body: Sequence[ast.stmt]) -> list[ast.stmt]:
    statements = list(body)
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        return statements[1:]
    return statements


def _constructor_model(
    constructor: ast.FunctionDef | None,
) -> tuple[list[str], dict[str, ast.AST], dict[str, ast.AST], str]:
    """Return constructor params, defaults, and ``self.field`` expressions."""

    if constructor is None:
        return [], {}, {}, "CONSTRUCTOR_REQUIRED_FOR_OWNED_INLINE"

    args = constructor.args
    if (
        constructor.decorator_list
        or args.posonlyargs
        or args.vararg
        or args.kwarg
        or args.kwonlyargs
        or not args.args
        or args.args[0].arg != "self"
    ):
        return [], {}, {}, "CONSTRUCTOR_SIGNATURE_UNSUPPORTED"

    parameters = [argument.arg for argument in args.args[1:]]
    defaults: dict[str, ast.AST] = {}
    if args.defaults:
        default_parameter_names = parameters[-len(args.defaults):]
        defaults = {
            name: copy.deepcopy(value)
            for name, value in zip(default_parameter_names, args.defaults)
        }

    fields: dict[str, ast.AST] = {}
    parameter_names = set(parameters)

    for statement in _strip_docstring(constructor.body):
        if not (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Attribute)
            and isinstance(statement.targets[0].value, ast.Name)
            and statement.targets[0].value.id == "self"
            and isinstance(statement.targets[0].ctx, ast.Store)
        ):
            return [], {}, {}, "CONSTRUCTOR_STATE_UNSUPPORTED"

        field_name = statement.targets[0].attr
        if field_name in fields:
            return [], {}, {}, "DUPLICATE_CONSTRUCTOR_FIELD"

        # Permit parameter-derived/literal expressions, but reject calls,
        # attribute reads, comprehensions, lambdas, etc.  Inline Class must not
        # duplicate hidden side effects from the helper constructor.
        for node in ast.walk(statement.value):
            if isinstance(
                node,
                (
                    ast.Call,
                    ast.Attribute,
                    ast.Subscript,
                    ast.Lambda,
                    ast.ListComp,
                    ast.SetComp,
                    ast.DictComp,
                    ast.GeneratorExp,
                    ast.Await,
                    ast.Yield,
                    ast.YieldFrom,
                ),
            ):
                return [], {}, {}, "CONSTRUCTOR_FIELD_EXPRESSION_UNSAFE"
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in parameter_names:
                    return [], {}, {}, "CONSTRUCTOR_FIELD_EXTERNAL_DEPENDENCY"

        fields[field_name] = copy.deepcopy(statement.value)

    if not fields:
        return [], {}, {}, "NO_CONSTRUCTOR_FIELDS"

    return parameters, defaults, fields, ""


def _bind_constructor_arguments(
    *,
    call: ast.Call,
    parameters: Sequence[str],
    defaults: dict[str, ast.AST],
) -> tuple[dict[str, ast.AST], str]:
    if any(keyword.arg is None for keyword in call.keywords):
        return {}, "CONSTRUCTOR_STAR_ARGUMENTS_UNSUPPORTED"
    if len(call.args) > len(parameters):
        return {}, "CONSTRUCTOR_ARGUMENT_COUNT_MISMATCH"

    bound: dict[str, ast.AST] = {}
    for name, value in zip(parameters, call.args):
        bound[name] = copy.deepcopy(value)

    for keyword in call.keywords:
        if keyword.arg not in parameters:
            return {}, "CONSTRUCTOR_UNKNOWN_KEYWORD"
        if keyword.arg in bound:
            return {}, "CONSTRUCTOR_DUPLICATE_ARGUMENT"
        bound[keyword.arg] = copy.deepcopy(keyword.value)

    for name in parameters:
        if name not in bound and name in defaults:
            bound[name] = copy.deepcopy(defaults[name])

    missing = [name for name in parameters if name not in bound]
    if missing:
        return {}, "CONSTRUCTOR_MISSING_ARGUMENT"

    return bound, ""


class _ParameterSubstituter(ast.NodeTransformer):
    def __init__(self, values: dict[str, ast.AST]) -> None:
        self.values = values

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if isinstance(node.ctx, ast.Load) and node.id in self.values:
            return ast.copy_location(copy.deepcopy(self.values[node.id]), node)
        return node


def _render_bound_field_expression(
    expression: ast.AST,
    bound: dict[str, ast.AST],
) -> str:
    rewritten = _ParameterSubstituter(bound).visit(copy.deepcopy(expression))
    ast.fix_missing_locations(rewritten)
    try:
        return ast.unparse(rewritten)
    except Exception:
        return ""


def _method_safety_error(
    method: ast.FunctionDef,
    *,
    field_names: set[str],
    method_names: set[str],
) -> str:
    args = method.args
    if (
        method.decorator_list
        or args.posonlyargs
        or args.vararg
        or args.kwarg
        or args.kwonlyargs
        or not args.args
        or args.args[0].arg != "self"
    ):
        return "METHOD_SIGNATURE_UNSUPPORTED"

    parents = _parents(method)
    for node in ast.walk(method):
        if node is not method and isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
        ):
            return "NESTED_SCOPE_UNSUPPORTED"

        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "self" and node.attr not in field_names | method_names:
                return "SOURCE_INSTANCE_DEPENDENCY"

        if isinstance(node, ast.Name) and node.id == "self":
            parent = parents.get(node)
            if not (
                isinstance(parent, ast.Attribute)
                and parent.value is node
                and parent.attr in field_names | method_names
            ):
                return "SOURCE_INSTANCE_DEPENDENCY"

    return ""


def _owner_self_fields(owner: ast.ClassDef) -> set[str]:
    fields: set[str] = set()
    for node in ast.walk(owner):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and isinstance(node.ctx, ast.Store)
        ):
            fields.add(node.attr)
    return fields


def _owner_method_names(owner: ast.ClassDef) -> set[str]:
    return {
        node.name
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _find_owned_constructions(
    *,
    tree: ast.Module,
    target_class: ast.ClassDef,
) -> list[dict[str, Any]]:
    constructions: list[dict[str, Any]] = []
    for owner in [node for node in tree.body if isinstance(node, ast.ClassDef)]:
        if owner is target_class:
            continue
        constructor = _class_method(owner, "__init__")
        if constructor is None:
            continue
        for statement in ast.walk(constructor):
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            value = getattr(statement, "value", None)
            if not (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == target_class.name
            ):
                continue

            target: ast.AST | None = None
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target = statement.targets[0]
            elif isinstance(statement, ast.AnnAssign):
                target = statement.target

            if not (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and isinstance(target.ctx, ast.Store)
            ):
                continue

            constructions.append(
                {
                    "owner_class": owner,
                    "owner_constructor": constructor,
                    "owner_attribute": target.attr,
                    "statement": statement,
                    "call": value,
                }
            )
    return constructions


def _class_reference_is_allowed(
    *,
    node: ast.Name,
    target_class: ast.ClassDef,
    construction_call: ast.Call,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    if _is_descendant(node, target_class, parents):
        return True
    parent = parents.get(node)
    return parent is construction_call and construction_call.func is node


def _owner_attribute_usage_error(
    *,
    tree: ast.Module,
    target_class: ast.ClassDef,
    owner_attribute: str,
    field_names: set[str],
    method_names: set[str],
    construction_statement: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> str:
    allowed_members = field_names | method_names
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr != owner_attribute:
            continue
        if _is_descendant(node, target_class, parents):
            continue
        if _is_descendant(node, construction_statement, parents):
            continue

        outer = parents.get(node)
        if not (
            isinstance(outer, ast.Attribute)
            and outer.value is node
            and outer.attr in allowed_members
        ):
            return "OWNER_ATTRIBUTE_ESCAPES_OR_IS_USED_AS_OBJECT"
    return ""


def _method_source(
    source_code: str,
    method: ast.FunctionDef,
) -> str:
    lines = source_code.splitlines(keepends=True)
    if method.lineno <= 0 or method.end_lineno is None:
        return ""
    selected = lines[method.lineno - 1:method.end_lineno]
    if not selected:
        return ""
    indent = " " * int(method.col_offset or 0)
    normalized: list[str] = []
    for line in selected:
        if line.strip() and line.startswith(indent):
            normalized.append(line[len(indent):])
        else:
            normalized.append(line)
    return "".join(normalized).rstrip()


def _chain_rewrite_edits(
    *,
    source_code: str,
    tree: ast.Module,
    target_class: ast.ClassDef,
    construction_statement: ast.AST,
    owner_attribute: str,
    field_names: set[str],
    method_names: set[str],
    line_offsets: Sequence[int],
    parents: dict[ast.AST, ast.AST],
) -> tuple[list[tuple[int, int, str]], int, str]:
    edits: list[tuple[int, int, str]] = []
    updated = 0

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == owner_attribute
            and node.attr in field_names | method_names
        ):
            continue
        if _is_descendant(node, target_class, parents):
            continue
        if _is_descendant(node, construction_statement, parents):
            continue

        base_expression = node.value.value
        try:
            base_text = ast.unparse(base_expression)
        except Exception:
            return [], 0, "ATTRIBUTE_REWRITE_FAILED"

        replacement = f"{base_text}.{node.attr}"
        start = _position_offset(line_offsets, node.lineno, node.col_offset)
        end = _position_offset(line_offsets, node.end_lineno, node.end_col_offset)
        if start < 0 or end < start:
            return [], 0, "ATTRIBUTE_REWRITE_POSITION_FAILED"
        edits.append((start, end, replacement))
        updated += 1

    return edits, updated, ""


def apply_owned_inline_class(
    source_code: str,
    *,
    class_to_inline: str,
    preferred_destination_class: str = "",
    preferred_owner_attribute: str = "",
) -> Tuple[str, int, dict[str, Any]]:
    """Inline a tiny helper class into its unique owning Python class.

    ``not_applicable`` means the target is not the owned-composition pattern;
    callers may safely try another Inline Class strategy.  ``review_required``
    means the owned pattern was found but cannot be rewritten safely and must
    not fall back to a looser transformation.
    """

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(class_to_inline or "")):
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="INVALID_CLASS_TARGET",
        )

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="SOURCE_PARSE_FAILED",
        )

    target_classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_to_inline
    ]
    if len(target_classes) != 1:
        return _not_applicable(
            source_code,
            class_to_inline=class_to_inline,
            reason="TARGET_CLASS_NOT_FOUND_OR_NOT_UNIQUE",
        )
    target_class = target_classes[0]

    if target_class.bases or target_class.keywords or target_class.decorator_list:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="INHERITANCE_OR_METACLASS_UNSUPPORTED",
        )

    methods = [
        node
        for node in target_class.body
        if isinstance(node, ast.FunctionDef)
    ]
    constructor = next((item for item in methods if item.name == "__init__"), None)
    business_methods = [item for item in methods if item.name != "__init__"]

    if not business_methods or len(business_methods) > 3:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="CLASS_RESPONSIBILITY_NOT_SMALL",
        )

    allowed_members = set(methods)
    for member in target_class.body:
        if member in allowed_members:
            continue
        if (
            isinstance(member, ast.Expr)
            and isinstance(member.value, ast.Constant)
            and isinstance(member.value.value, str)
        ):
            continue
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="CLASS_MEMBER_UNSUPPORTED",
        )

    parameters, defaults, field_expressions, constructor_error = _constructor_model(constructor)
    if constructor_error:
        # A no-argument/literal-state helper may still be handled by the legacy
        # module-function Inline Class strategy.
        if constructor_error in {
            "CONSTRUCTOR_REQUIRED_FOR_OWNED_INLINE",
            "NO_CONSTRUCTOR_FIELDS",
        }:
            return _not_applicable(
                source_code,
                class_to_inline=class_to_inline,
                reason=constructor_error,
            )
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason=constructor_error,
        )

    field_names = set(field_expressions)
    method_names = {method.name for method in business_methods}

    for method in business_methods:
        error = _method_safety_error(
            method,
            field_names=field_names,
            method_names=method_names,
        )
        if error:
            return _review(
                source_code,
                class_to_inline=class_to_inline,
                reason=error,
            )

    constructions = _find_owned_constructions(
        tree=tree,
        target_class=target_class,
    )
    if preferred_destination_class:
        constructions = [
            item
            for item in constructions
            if item["owner_class"].name == preferred_destination_class
        ]
    if preferred_owner_attribute:
        constructions = [
            item
            for item in constructions
            if item["owner_attribute"] == preferred_owner_attribute
        ]

    if not constructions:
        return _not_applicable(
            source_code,
            class_to_inline=class_to_inline,
            reason="NO_UNIQUE_OWNER_COMPOSITION",
        )
    if len(constructions) != 1:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="AMBIGUOUS_OWNER_COMPOSITION",
        )

    construction = constructions[0]
    owner_class: ast.ClassDef = construction["owner_class"]
    owner_attribute = str(construction["owner_attribute"])
    construction_statement: ast.AST = construction["statement"]
    construction_call: ast.Call = construction["call"]

    bound, bind_error = _bind_constructor_arguments(
        call=construction_call,
        parameters=parameters,
        defaults=defaults,
    )
    if bind_error:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason=bind_error,
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    owner_fields = _owner_self_fields(owner_class)
    owner_methods = _owner_method_names(owner_class)
    field_collisions = sorted(field_names & owner_fields)
    method_collisions = sorted(method_names & owner_methods)
    if field_collisions:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="DESTINATION_FIELD_COLLISION",
            collisions=field_collisions,
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )
    if method_collisions:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="DESTINATION_METHOD_COLLISION",
            collisions=method_collisions,
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    parents = _parents(tree)

    # The helper class must not escape its owner relationship.  A second
    # construction, assignment to a local variable, isinstance check, type
    # alias, decorator, or other reference makes ownership ambiguous.
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Name)
            and node.id == class_to_inline
            and isinstance(node.ctx, ast.Load)
        ):
            continue
        if not _class_reference_is_allowed(
            node=node,
            target_class=target_class,
            construction_call=construction_call,
            parents=parents,
        ):
            return _review(
                source_code,
                class_to_inline=class_to_inline,
                reason="DYNAMIC_OR_EXTERNAL_CLASS_REFERENCE",
                destination_class=owner_class.name,
                owner_attribute=owner_attribute,
            )

    usage_error = _owner_attribute_usage_error(
        tree=tree,
        target_class=target_class,
        owner_attribute=owner_attribute,
        field_names=field_names,
        method_names=method_names,
        construction_statement=construction_statement,
        parents=parents,
    )
    if usage_error:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason=usage_error,
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    method_sources = [
        _method_source(source_code, method)
        for method in business_methods
    ]
    if not all(method_sources):
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="METHOD_RENDER_FAILED",
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    field_assignment_lines: list[str] = []
    indent = " " * int(getattr(construction_statement, "col_offset", 0) or 0)
    for field_name, expression in field_expressions.items():
        rendered_expression = _render_bound_field_expression(expression, bound)
        if not rendered_expression:
            return _review(
                source_code,
                class_to_inline=class_to_inline,
                reason="CONSTRUCTOR_FIELD_BINDING_FAILED",
                destination_class=owner_class.name,
                owner_attribute=owner_attribute,
            )
        field_assignment_lines.append(
            f"{indent}self.{field_name} = {rendered_expression}\n"
        )

    line_offsets = _line_offsets(source_code)
    edits: list[tuple[int, int, str]] = []

    class_start = _position_offset(line_offsets, target_class.lineno, 0)
    class_end = _line_end_offset(source_code, line_offsets, target_class.end_lineno)
    edits.append((class_start, class_end, ""))

    construction_start = _position_offset(
        line_offsets,
        construction_statement.lineno,
        0,
    )
    construction_end = _line_end_offset(
        source_code,
        line_offsets,
        construction_statement.end_lineno,
    )
    edits.append(
        (
            construction_start,
            construction_end,
            "".join(field_assignment_lines),
        )
    )

    insertion_offset = _line_end_offset(
        source_code,
        line_offsets,
        owner_class.end_lineno,
    )
    rendered_methods = "\n\n".join(
        textwrap.indent(method_source, "    ")
        for method_source in method_sources
    )
    edits.append((insertion_offset, insertion_offset, f"\n{rendered_methods}\n"))

    chain_edits, updated_accesses, chain_error = _chain_rewrite_edits(
        source_code=source_code,
        tree=tree,
        target_class=target_class,
        construction_statement=construction_statement,
        owner_attribute=owner_attribute,
        field_names=field_names,
        method_names=method_names,
        line_offsets=line_offsets,
        parents=parents,
    )
    if chain_error:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason=chain_error,
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )
    edits.extend(chain_edits)

    if not _edits_do_not_overlap(edits):
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="OVERLAPPING_INLINE_EDITS",
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    transformed = _apply_edits(source_code, edits)
    try:
        transformed_tree = ast.parse(transformed)
        compile(transformed, "<sctva-owned-inline-class>", "exec")
    except (SyntaxError, ValueError, TypeError):
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="TRANSFORMED_SOURCE_PARSE_FAILED",
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    if any(
        isinstance(node, ast.ClassDef) and node.name == class_to_inline
        for node in transformed_tree.body
    ):
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="TARGET_CLASS_STILL_PRESENT",
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    unresolved_class_refs = [
        node
        for node in ast.walk(transformed_tree)
        if isinstance(node, ast.Name) and node.id == class_to_inline
    ]
    if unresolved_class_refs:
        return _review(
            source_code,
            class_to_inline=class_to_inline,
            reason="UNRESOLVED_CLASS_REFERENCE_AFTER_INLINE",
            destination_class=owner_class.name,
            owner_attribute=owner_attribute,
        )

    return transformed, len(edits), {
        "status": "success",
        "inline_mode": "owner_class",
        "class_to_inline": class_to_inline,
        "destination_class": owner_class.name,
        "owner_attribute": owner_attribute,
        "inlined_methods": [method.name for method in business_methods],
        "inlined_fields": sorted(field_names),
        "removed_instantiations": 1,
        "updated_owner_member_accesses": updated_accesses,
    }