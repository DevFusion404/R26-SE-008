"""Conservative Python Message Chains -> Hide Delegate refactoring."""

from __future__ import annotations

import ast
import copy
import re
from collections import Counter
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


def _message_chain_depth(node: ast.AST) -> int:
    if isinstance(node, ast.Attribute):
        return 1 + _message_chain_depth(node.value)
    if isinstance(node, ast.Subscript):
        return 1 + _message_chain_depth(node.value)
    if isinstance(node, ast.Call):
        return _message_chain_depth(node.func)
    return 0


def _module_message_chain_evidence(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[dict[str, Any]]:
    """Describe outermost module-function message chains."""

    return [
        {
            "line": int(getattr(node, "lineno", 0) or 0),
            "end_line": int(getattr(node, "end_lineno", 0) or 0),
            "chain_depth": _message_chain_depth(node),
            "expression": ast.unparse(node),
        }
        for node in _module_message_chain_nodes(function)
    ]


def _module_message_chain_nodes(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.AST]:
    """Return outermost chain expressions without double-counting children."""

    parents = _parents(function)
    candidates: list[ast.AST] = []
    for node in ast.walk(function):
        if not isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
            continue
        depth = _message_chain_depth(node)
        if depth < 2:
            continue
        parent = parents.get(node)
        if (
            isinstance(parent, ast.Attribute) and parent.value is node
        ) or (
            isinstance(parent, ast.Subscript) and parent.value is node
        ) or (
            isinstance(parent, ast.Call) and parent.func is node
        ):
            continue
        candidates.append(node)
    return candidates


def _message_chain_root_name(node: ast.AST) -> str:
    current = node
    while isinstance(current, (ast.Attribute, ast.Subscript, ast.Call)):
        if isinstance(current, ast.Attribute):
            current = current.value
        elif isinstance(current, ast.Subscript):
            current = current.value
        elif isinstance(current.func, ast.Name):
            return current.func.id
        else:
            current = current.func
    return current.id if isinstance(current, ast.Name) else ""


def _message_chain_terminal_member(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return node.func.attr if isinstance(node.func, ast.Attribute) else "call"
    if isinstance(node, ast.Attribute):
        return node.attr
    return "item"


def _message_chain_shape_fingerprint(node: ast.AST) -> str:
    """Fingerprint chain topology while tolerating numeric constants introduced elsewhere."""

    if isinstance(node, ast.Call):
        args = ",".join(_message_chain_shape_fingerprint(arg) for arg in node.args)
        keywords = ",".join(
            f"{keyword.arg or '*'}={_message_chain_shape_fingerprint(keyword.value)}"
            for keyword in node.keywords
        )
        return (
            "Call("
            f"{_message_chain_shape_fingerprint(node.func)}"
            f";args={len(node.args)}[{args}]"
            f";kw={len(node.keywords)}[{keywords}]"
            ")"
        )
    if isinstance(node, ast.Attribute):
        return f"Attr({_message_chain_shape_fingerprint(node.value)}.{node.attr})"
    if isinstance(node, ast.Subscript):
        return (
            "Subscript("
            f"{_message_chain_shape_fingerprint(node.value)}"
            f"[{_message_chain_shape_fingerprint(node.slice)}]"
            ")"
        )
    if isinstance(node, ast.BinOp):
        return (
            f"BinOp({type(node.op).__name__},"
            f"{_message_chain_shape_fingerprint(node.left)},"
            f"{_message_chain_shape_fingerprint(node.right)})"
        )
    if isinstance(node, ast.UnaryOp):
        return f"UnaryOp({type(node.op).__name__},{_message_chain_shape_fingerprint(node.operand)})"
    if isinstance(node, ast.Compare):
        operators = ",".join(type(operator).__name__ for operator in node.ops)
        comparators = ",".join(_message_chain_shape_fingerprint(item) for item in node.comparators)
        return f"Compare({operators},{_message_chain_shape_fingerprint(node.left)};{comparators})"
    if isinstance(node, ast.Tuple):
        return "Tuple(" + ",".join(_message_chain_shape_fingerprint(item) for item in node.elts) + ")"
    if isinstance(node, ast.List):
        return "List(" + ",".join(_message_chain_shape_fingerprint(item) for item in node.elts) + ")"
    if isinstance(node, ast.Slice):
        lower = _message_chain_shape_fingerprint(node.lower) if node.lower else ""
        upper = _message_chain_shape_fingerprint(node.upper) if node.upper else ""
        step = _message_chain_shape_fingerprint(node.step) if node.step else ""
        return f"Slice({lower}:{upper}:{step})"
    if isinstance(node, ast.Name):
        if re.fullmatch(
            r"(?:THRESHOLD_LIMIT|MAGIC_NUMBER|INTRODUCED_CONSTANT|SCTVA_CONSTANT)_[A-Z0-9_]+",
            node.id,
        ):
            return "NumericConstant"
        return f"Name({node.id})"
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, complex)):
            return "NumericConstant"
        return f"Constant({node.value!r})"
    return ast.dump(node, include_attributes=False)


def _module_facade_resolution(
    *,
    module_functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    selected_function: ast.FunctionDef | ast.AsyncFunctionDef | None,
    requested_source_class: str,
    requested_method: str,
    new_method_name: str,
    resolution_strategy: str,
) -> dict[str, Any] | None:
    functions = [selected_function] if selected_function is not None else list(module_functions.values())
    candidates = [
        (function, node)
        for function in functions
        if isinstance(function, ast.FunctionDef)
        for node in _module_message_chain_nodes(function)
        if _message_chain_depth(node) >= 3 and _message_chain_root_name(node)
    ]
    if not candidates:
        return None

    maximum_depth = max(_message_chain_depth(node) for _, node in candidates)
    strongest = [
        (function, node)
        for function, node in candidates
        if _message_chain_depth(node) == maximum_depth
    ]
    if len(strongest) != 1:
        return {
            "status": "review_required",
            "reason": "AMBIGUOUS_MODULE_HIDE_DELEGATE_TARGET",
            "strategy": "unique_deepest_module_chain",
            "candidate_count": len(strongest),
            "maximum_chain_depth": maximum_depth,
        }

    function, node = strongest[0]
    if any(
        isinstance(item, (ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr, ast.Lambda))
        for item in ast.walk(node)
    ):
        return {
            "status": "review_required",
            "reason": "MODULE_HIDE_DELEGATE_CONTROL_FLOW_UNSAFE",
            "strategy": "unique_deepest_module_chain",
        }

    delegate = _message_chain_root_name(node)
    delegated = _message_chain_terminal_member(node)
    forwarder = str(new_method_name or f"get_{delegate}_result").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", forwarder):
        return {
            "status": "review_required",
            "reason": "INVALID_NEW_METHOD_NAME",
            "strategy": "unique_deepest_module_chain",
        }
    if forwarder in module_functions:
        return {
            "status": "review_required",
            "reason": "MODULE_FORWARDER_NAME_COLLISION",
            "strategy": "unique_deepest_module_chain",
        }

    target = {
        "source_class": "__module__",
        "delegate_member": delegate,
        "delegated_member": delegated,
        "new_method_name": forwarder,
        "hide_delegate_mode": "module_facade",
        "source_method": function.name,
        "target_line": int(getattr(node, "lineno", 0) or 0),
        "target_end_line": int(getattr(node, "end_lineno", 0) or 0),
        "message_chain_depth": maximum_depth,
        "message_chain_expression": ast.unparse(node),
        "message_chain_fingerprint": ast.dump(node, include_attributes=False),
    }
    return {
        "status": "success",
        "reason": "MODULE_FACADE_HIDE_DELEGATE_SAFE",
        "strategy": (
            resolution_strategy
            if selected_function is not None
            else "unique_deepest_module_chain"
        ),
        "target_kind": "MODULE_MESSAGE_CHAIN",
        "requested_source_class": requested_source_class,
        "requested_method": requested_method,
        **target,
        "targets": [target],
    }


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
        selected_function = module_functions.get(requested_method)
        resolution_strategy = "requested_module_function"
        if selected_function is None and source_line is not None:
            selected_function = next(
                (
                    function
                    for function in module_functions.values()
                    if int(getattr(function, "lineno", 0) or 0)
                    <= source_line
                    <= int(getattr(function, "end_lineno", 0) or 0)
                ),
                None,
            )
            resolution_strategy = "source_line_enclosing_module_function"

        module_facade = _module_facade_resolution(
            module_functions=module_functions,
            selected_function=selected_function,
            requested_source_class=requested_source_class,
            requested_method=requested_method,
            new_method_name=new_method_name,
            resolution_strategy=resolution_strategy,
        )
        if module_facade is not None:
            return module_facade

        selected_evidence = (
            _module_message_chain_evidence(selected_function)
            if selected_function is not None
            else []
        )
        if selected_evidence:
            # Hide Delegate requires a local owning class on which SCTVA can
            # create the forwarding method. A module function navigating a
            # library/global result (for example a Tensor call chain) needs a
            # different refactoring; manufacturing a class here is unsafe.
            return {
                "status": "not_applicable",
                "reason": "HIDE_DELEGATE_NO_LOCAL_CLASS_OWNER_FOR_MESSAGE_CHAIN",
                "strategy": resolution_strategy,
                "target_kind": "MODULE_FUNCTION",
                "method": selected_function.name,
                "requested_source_class": requested_source_class,
                "requested_method": requested_method,
                "suggested_refactoring": "EXTRACT_METHOD_OR_INTRODUCE_FACADE",
                "message_chain_count": len(selected_evidence),
                "message_chains": selected_evidence,
            }

        if selected_function is None:
            module_evidence = [
                {"method": function.name, **item}
                for function in module_functions.values()
                for item in _module_message_chain_evidence(function)
            ]
            if module_evidence:
                return {
                    "status": "not_applicable",
                    "reason": "HIDE_DELEGATE_NO_LOCAL_CLASS_OWNER_FOR_MESSAGE_CHAIN",
                    "strategy": "current_ast_module_message_chain_scan",
                    "target_kind": "MODULE",
                    "requested_source_class": requested_source_class,
                    "requested_method": requested_method,
                    "suggested_refactoring": "EXTRACT_METHOD_OR_INTRODUCE_FACADE",
                    "message_chain_count": len(module_evidence),
                    "message_chain_methods": sorted({
                        str(item["method"])
                        for item in module_evidence
                    }),
                    "message_chains": module_evidence,
                }

        # RDP may call the module name a "source_class", but there is no class
        # owner in the current AST. Hide Delegate is structurally inapplicable;
        # fabricating one would be Extract Class or Move Function instead.
        if selected_function is not None:
            return {
                "status": "not_applicable",
                "reason": "MODULE_LEVEL_FUNCTION_HAS_NO_HIDE_DELEGATE_OWNER",
                "strategy": resolution_strategy,
                "target_kind": "MODULE_FUNCTION",
                "method": selected_function.name,
                "requested_source_class": requested_source_class,
                "requested_method": requested_method,
                "suggested_refactoring": "EXTRACT_CLASS_OR_MOVE_FUNCTION",
            }
        return {
            "status": "not_applicable",
            "reason": "HIDE_DELEGATE_REQUIRES_CLASS_OWNER",
            "strategy": "current_ast_has_no_class_owner",
            "target_kind": "MODULE",
            "requested_source_class": requested_source_class,
            "requested_method": requested_method,
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
        if not requested_source_class or source_class_is_stale:
            selected_module_function = module_functions.get(requested_method)
            module_resolution_strategy = "requested_module_function"
            if selected_module_function is None and source_line is not None:
                selected_module_function = next(
                    (
                        function
                        for function in module_functions.values()
                        if int(getattr(function, "lineno", 0) or 0)
                        <= source_line
                        <= int(getattr(function, "end_lineno", 0) or 0)
                    ),
                    None,
                )
                module_resolution_strategy = "source_line_enclosing_module_function"
            module_facade = _module_facade_resolution(
                module_functions=module_functions,
                selected_function=selected_module_function,
                requested_source_class=requested_source_class,
                requested_method=requested_method,
                new_method_name=new_method_name,
                resolution_strategy=module_resolution_strategy,
            )
            if module_facade is not None:
                return module_facade
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


def _function_local_names(function: ast.FunctionDef) -> set[str]:
    names = {
        argument.arg
        for argument in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
    }
    if function.args.vararg:
        names.add(function.args.vararg.arg)
    if function.args.kwarg:
        names.add(function.args.kwarg.arg)

    class _LocalCollector(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node is function:
                for statement in node.body:
                    self.visit(statement)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for alias in node.names:
                names.add(alias.asname or alias.name)

    _LocalCollector().visit(function)
    for declaration in ast.walk(function):
        if isinstance(declaration, (ast.Global, ast.Nonlocal)):
            names.difference_update(declaration.names)
    return names


def apply_module_hide_delegate(
    source_code: str,
    *,
    source_method: str,
    delegate_member: str,
    delegated_member: str,
    new_method_name: str,
    message_chain_fingerprint: str = "",
) -> tuple[str, int, dict[str, Any]]:
    """Hide one module-level chain behind a behavior-preserving facade."""

    if not all(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or "")
        for value in (source_method, delegate_member, delegated_member, new_method_name)
    ):
        return _review(source_code, "INVALID_MODULE_HIDE_DELEGATE_TARGET")
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return _review(source_code, "SOURCE_PARSE_FAILED")
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    function = functions.get(source_method)
    if function is None:
        return _review(source_code, "MODULE_HIDE_DELEGATE_FUNCTION_NOT_FOUND")
    if new_method_name in functions:
        return _review(source_code, "MODULE_FORWARDER_NAME_COLLISION")

    candidates = [
        node
        for node in _module_message_chain_nodes(function)
        if _message_chain_depth(node) >= 3
        and _message_chain_root_name(node) == delegate_member
    ]
    exact = [
        node
        for node in candidates
        if ast.dump(node, include_attributes=False) == message_chain_fingerprint
    ] if message_chain_fingerprint else []
    if len(exact) == 1:
        target = exact[0]
        resolution = "normalized_ast_fingerprint"
    else:
        maximum_depth = max((_message_chain_depth(node) for node in candidates), default=0)
        strongest = [
            node for node in candidates
            if _message_chain_depth(node) == maximum_depth
        ]
        if len(strongest) != 1:
            return _review(
                source_code,
                "AMBIGUOUS_MODULE_HIDE_DELEGATE_TARGET",
                candidate_count=len(strongest),
            )
        target = strongest[0]
        resolution = "current_ast_unique_deepest_chain"

    if _message_chain_terminal_member(target) != delegated_member:
        return _review(source_code, "MODULE_HIDE_DELEGATE_MEMBER_CHANGED")
    if any(
        isinstance(node, (ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr, ast.Lambda))
        for node in ast.walk(target)
    ):
        return _review(source_code, "MODULE_HIDE_DELEGATE_CONTROL_FLOW_UNSAFE")

    local_names = _function_local_names(function)
    helper_parameters: list[str] = []
    for node in ast.walk(target):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in local_names
            and node.id not in helper_parameters
        ):
            helper_parameters.append(node.id)

    chain_expression = ast.unparse(target)
    arguments = ", ".join(helper_parameters)
    helper_text = (
        f"def {new_method_name}({arguments}):\n"
        f"    return {chain_expression}\n\n\n"
    )
    replacement = f"{new_method_name}({arguments})"
    offsets = _line_offsets(source_code)
    start = _offset(offsets, target.lineno, target.col_offset)
    end = _offset(offsets, target.end_lineno, target.end_col_offset)
    decorator_lines = [
        int(getattr(decorator, "lineno", function.lineno) or function.lineno)
        for decorator in function.decorator_list
    ]
    insertion_line = min([function.lineno, *decorator_lines])
    insertion = _offset(offsets, insertion_line, 0)
    if insertion > start:
        return _review(source_code, "MODULE_FORWARDER_INSERTION_UNSAFE")

    transformed = source_code
    for edit_start, edit_end, edit_text in sorted(
        ((start, end, replacement), (insertion, insertion, helper_text)),
        key=lambda item: item[0],
        reverse=True,
    ):
        transformed = f"{transformed[:edit_start]}{edit_text}{transformed[edit_end:]}"
    try:
        ast.parse(transformed)
        compile(transformed, "<sctva-module-hide-delegate>", "exec")
    except (SyntaxError, TypeError, ValueError):
        return _review(source_code, "TRANSFORMED_SOURCE_PARSE_FAILED")

    fingerprint = ast.dump(target, include_attributes=False)
    effective = {
        "hide_delegate_mode": "module_facade",
        "source_class": "__module__",
        "source_method": source_method,
        "delegate_member": delegate_member,
        "delegated_member": delegated_member,
        "new_method_name": new_method_name,
        "message_chain_fingerprint": fingerprint,
        "message_chain_expression": chain_expression,
        "message_chain_depth": _message_chain_depth(target),
        "helper_parameters": helper_parameters,
    }
    return transformed, 2, {
        "status": "success",
        "reason": "MODULE_FACADE_HIDE_DELEGATE_SAFE",
        "language": "python",
        "created_forwarder": True,
        "updated_call_sites": 1,
        "target_resolution": resolution,
        **effective,
        "effective_action_parameters": effective,
    }


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


def validate_module_hide_delegate(
    original_code: str,
    transformed_code: str,
    *,
    source_method: str,
    delegate_member: str,
    delegated_member: str,
    new_method_name: str,
    message_chain_fingerprint: str,
    helper_parameters: list[str] | None = None,
) -> dict[str, Any]:
    """Prove a selected module chain moved once behind its facade function."""

    try:
        before = ast.parse(original_code)
        after = ast.parse(transformed_code)
    except SyntaxError:
        return {"passed": False, "reason": "parse_failed"}
    before_function = next(
        (
            node for node in before.body
            if isinstance(node, ast.FunctionDef) and node.name == source_method
        ),
        None,
    )
    after_function = next(
        (
            node for node in after.body
            if isinstance(node, ast.FunctionDef) and node.name == source_method
        ),
        None,
    )
    helpers = [
        node for node in after.body
        if isinstance(node, ast.FunctionDef) and node.name == new_method_name
    ]
    if before_function is None or after_function is None:
        return {"passed": False, "reason": "source_method_missing"}

    before_targets = [
        node
        for node in _module_message_chain_nodes(before_function)
        if ast.dump(node, include_attributes=False) == message_chain_fingerprint
    ]
    selected_chain_fingerprints = {message_chain_fingerprint}
    helper = helpers[0] if len(helpers) == 1 else None
    helper_body = list(helper.body) if helper is not None else []
    helper_return = (
        helper_body[0].value
        if len(helper_body) == 1 and isinstance(helper_body[0], ast.Return)
        else None
    )
    expected_parameters = list(helper_parameters or [])
    actual_parameters = (
        [argument.arg for argument in helper.args.args]
        if helper is not None
        else []
    )
    facade_calls = [
        node
        for node in ast.walk(after_function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == new_method_name
    ]
    call_inputs_preserved = bool(facade_calls) and all(
        [
            argument.id
            for argument in call.args
            if isinstance(argument, ast.Name)
        ] == expected_parameters
        and len(call.args) == len(expected_parameters)
        and not call.keywords
        for call in facade_calls
    )

    def chain_records(function: ast.FunctionDef) -> Counter[str]:
        return Counter(
            _message_chain_shape_fingerprint(node)
            for node in _module_message_chain_nodes(function)
            if ast.dump(node, include_attributes=False) not in selected_chain_fingerprints
        )

    def chain_expressions_by_fingerprint(function: ast.FunctionDef) -> dict[str, str]:
        expressions: dict[str, str] = {}
        for node in _module_message_chain_nodes(function):
            fingerprint = ast.dump(node, include_attributes=False)
            if fingerprint in selected_chain_fingerprints:
                continue
            expressions.setdefault(_message_chain_shape_fingerprint(node), ast.unparse(node))
        return expressions

    before_other = chain_records(before_function)
    after_other = chain_records(after_function)
    before_other_expressions = chain_expressions_by_fingerprint(before_function)
    after_other_expressions = chain_expressions_by_fingerprint(after_function)
    missing_unrelated = list((before_other - after_other).elements())
    extra_unrelated = list((after_other - before_other).elements())
    unrelated_diagnostics = [
        {
            "status": "missing_after_transformation",
            "expression": before_other_expressions.get(fingerprint, ""),
            "normalized_fingerprint": fingerprint,
        }
        for fingerprint in missing_unrelated
    ] + [
        {
            "status": "unexpected_after_transformation",
            "expression": after_other_expressions.get(fingerprint, ""),
            "normalized_fingerprint": fingerprint,
        }
        for fingerprint in extra_unrelated
    ]
    checks = {
        "original_message_chain_existed": len(before_targets) == 1,
        "module_forwarder_added": len(helpers) == 1,
        "forwarder_targets_correct_delegate": (
            helper_return is not None
            and ast.dump(helper_return, include_attributes=False)
            == message_chain_fingerprint
            and _message_chain_root_name(helper_return) == delegate_member
            and _message_chain_terminal_member(helper_return) == delegated_member
        ),
        "client_message_chain_shortened": not any(
            ast.dump(node, include_attributes=False) == message_chain_fingerprint
            for node in _module_message_chain_nodes(after_function)
        ),
        "matching_call_site_updated": len(facade_calls) == 1,
        "evaluation_inputs_preserved": (
            actual_parameters == expected_parameters and call_inputs_preserved
        ),
        "unrelated_message_chains_preserved": not unrelated_diagnostics,
        "no_duplicate_forwarder": len(helpers) == 1,
        "python_syntax_valid": True,
    }
    result = {
        "passed": all(checks.values()),
        "language": "python",
        "hide_delegate_mode": "module_facade",
        "source_method": source_method,
        "delegate_member": delegate_member,
        "delegated_member": delegated_member,
        "new_method_name": new_method_name,
        "checks": checks,
    }
    if unrelated_diagnostics:
        result["unrelated_message_chain_diagnostics"] = unrelated_diagnostics
    return result
