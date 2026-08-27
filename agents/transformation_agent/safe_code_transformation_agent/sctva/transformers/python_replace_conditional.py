"""Conservative Python Replace Conditional with Polymorphism refactoring."""

from __future__ import annotations

import ast
import copy
import io
import re
import textwrap
import tokenize
from dataclasses import dataclass
from typing import Any


@dataclass
class _ConditionalTarget:
    function: ast.FunctionDef
    owner_class: str
    chain: ast.If
    conditions: list[ast.expr]
    bodies: list[list[ast.stmt]]
    mode: str
    outputs: list[str]


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for match in re.finditer("\n", source):
        offsets.append(match.end())
    return offsets


def _offset(offsets: list[int], line: int, column: int) -> int:
    return offsets[line - 1] + column


def _node_span(source: str, node: ast.AST) -> tuple[int, int]:
    offsets = _line_offsets(source)
    return (
        _offset(offsets, int(node.lineno), int(node.col_offset)),
        _offset(offsets, int(node.end_lineno), int(node.end_col_offset)),
    )


def _class_owner(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.ClassDef):
            return current.name
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return ""
        current = parents.get(current)
    return ""


def _top_level_functions(tree: ast.Module) -> list[tuple[ast.FunctionDef, str]]:
    routines: list[tuple[ast.FunctionDef, str]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            routines.append((node, ""))
        elif isinstance(node, ast.ClassDef):
            routines.extend(
                (item, node.name)
                for item in node.body
                if isinstance(item, ast.FunctionDef)
            )
    return routines


def _split_chain(node: ast.If) -> tuple[list[ast.expr], list[list[ast.stmt]]] | None:
    conditions: list[ast.expr] = []
    bodies: list[list[ast.stmt]] = []
    current = node
    while True:
        conditions.append(current.test)
        bodies.append(current.body)
        if len(current.orelse) == 1 and isinstance(current.orelse[0], ast.If):
            current = current.orelse[0]
            continue
        if not current.orelse:
            return None
        bodies.append(current.orelse)
        break
    if len(conditions) < 2:
        return None
    return conditions, bodies


def _contains_unsafe_control_flow(nodes: list[ast.AST]) -> bool:
    unsafe = (
        ast.AsyncFunctionDef,
        ast.Await,
        ast.Break,
        ast.ClassDef,
        ast.Continue,
        ast.FunctionDef,
        ast.Global,
        ast.Lambda,
        ast.NamedExpr,
        ast.Nonlocal,
        ast.Yield,
        ast.YieldFrom,
    )
    return any(isinstance(item, unsafe) for node in nodes for item in ast.walk(node))


def _safe_terminal_body(body: list[ast.stmt]) -> bool:
    return bool(body) and isinstance(body[-1], (ast.Return, ast.Raise)) and not _contains_unsafe_control_flow(body)


def _simple_assignment_outputs(body: list[ast.stmt]) -> list[str] | None:
    """Return branch-local names when every statement is a simple assignment."""

    if not body or _contains_unsafe_control_flow(body):
        return None
    outputs: list[str] = []

    def add_target(target: ast.AST) -> bool:
        if isinstance(target, ast.Name):
            if target.id not in outputs:
                outputs.append(target.id)
            return True
        if isinstance(target, (ast.Tuple, ast.List)):
            return all(add_target(item) for item in target.elts)
        return False

    for statement in body:
        if isinstance(statement, ast.Assign):
            if not all(add_target(target) for target in statement.targets):
                return None
        elif isinstance(statement, ast.AnnAssign):
            if statement.value is None or not add_target(statement.target):
                return None
        elif isinstance(statement, ast.AugAssign):
            if not add_target(statement.target):
                return None
        else:
            return None
    return outputs or None


def _chain_comment_present(source: str, chain: ast.If) -> bool:
    lines = source.splitlines()
    segment = textwrap.dedent(
        "\n".join(lines[int(chain.lineno) - 1 : int(chain.end_lineno)])
    )
    try:
        return any(
            token.type == tokenize.COMMENT
            for token in tokenize.generate_tokens(io.StringIO(segment).readline)
        )
    except (IndentationError, tokenize.TokenError):
        return True


def _candidate_targets(tree: ast.Module, source: str) -> tuple[list[_ConditionalTarget], list[str]]:
    targets: list[_ConditionalTarget] = []
    rejected_reasons: list[str] = []
    for function, owner_class in _top_level_functions(tree):
        for statement in function.body:
            if not isinstance(statement, ast.If):
                continue
            split = _split_chain(statement)
            if split is None:
                continue
            conditions, bodies = split
            if _contains_unsafe_control_flow(list(conditions)):
                rejected_reasons.append("UNSAFE_CONDITIONAL_EXPRESSION")
                continue
            if all(_safe_terminal_body(body) for body in bodies):
                mode = "terminal"
                outputs: list[str] = []
            else:
                branch_outputs = [_simple_assignment_outputs(body) for body in bodies]
                if (
                    any(outputs is None for outputs in branch_outputs)
                    or len({tuple(outputs or []) for outputs in branch_outputs}) != 1
                ):
                    rejected_reasons.append("NON_TERMINAL_BRANCH_BEHAVIOR")
                    continue
                mode = "assignment_outputs"
                outputs = list(branch_outputs[0] or [])
            if _chain_comment_present(source, statement):
                rejected_reasons.append("CONDITIONAL_COMMENTS_REQUIRE_CST_REVIEW")
                continue
            targets.append(_ConditionalTarget(
                function=function,
                owner_class=owner_class,
                chain=statement,
                conditions=conditions,
                bodies=bodies,
                mode=mode,
                outputs=outputs,
            ))
    return targets, rejected_reasons


def _normalize_symbol(value: str) -> str:
    return "".join(character.lower() for character in str(value or "") if character.isalnum())


def _target_score(
    target: _ConditionalTarget,
    *,
    method_name: str,
    source_class: str,
    source_line: int | None,
    start_line: int | None,
    end_line: int | None,
) -> int:
    score = 0
    if method_name and target.function.name == method_name:
        score += 100
    elif method_name and _normalize_symbol(target.function.name) == _normalize_symbol(method_name):
        score += 80
    if source_class and target.owner_class == source_class:
        score += 60
    lines = [value for value in (source_line, start_line, end_line) if isinstance(value, int) and value > 0]
    for line in lines:
        if int(target.chain.lineno) <= line <= int(target.chain.end_lineno):
            score += 50
        elif int(target.function.lineno) <= line <= int(target.function.end_lineno):
            score += 25
    return score


def resolve_target(
    source_code: str,
    *,
    method_name: str = "",
    source_class: str = "",
    source_line: int | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    """Resolve one safe conditional dispatch chain from Python AST evidence."""

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {"status": "review_required", "reason": "SOURCE_PARSE_FAILED"}

    targets, rejected_reasons = _candidate_targets(tree, source_code)
    if not targets:
        return {
            "status": "review_required" if rejected_reasons else "not_applicable",
            "reason": (
                rejected_reasons[0]
                if len(set(rejected_reasons)) == 1 and rejected_reasons
                else "NO_SAFE_POLYMORPHIC_CONDITIONAL_TARGET"
            ),
        }

    scored = [
        (
            _target_score(
                target,
                method_name=method_name,
                source_class=source_class,
                source_line=source_line,
                start_line=start_line,
                end_line=end_line,
            ),
            target,
        )
        for target in targets
    ]
    best_score = max(score for score, _ in scored)
    selected = [target for score, target in scored if score == best_score]
    if len(selected) != 1:
        return {
            "status": "review_required",
            "reason": "AMBIGUOUS_POLYMORPHIC_CONDITIONAL_TARGET",
            "candidate_count": len(selected),
        }

    target = selected[0]
    return {
        "status": "success",
        "method": target.function.name,
        "source_class": target.owner_class,
        "source_line": int(target.chain.lineno),
        "start_line": int(target.function.lineno),
        "end_line": int(target.function.end_lineno),
        "branch_count": len(target.bodies),
        "mode": target.mode,
        "outputs": target.outputs,
        "target": target,
        "strategy": (
            "explicit_python_ast_target"
            if method_name == target.function.name
            else "python_ast_semantic_recovery"
        ),
    }


def _assigned_names(nodes: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for statement in nodes:
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)
    return names


def _argument_names(function: ast.FunctionDef) -> list[str]:
    names = [argument.arg for argument in [*function.args.posonlyargs, *function.args.args]]
    if function.args.vararg:
        names.append(function.args.vararg.arg)
    names.extend(argument.arg for argument in function.args.kwonlyargs)
    if function.args.kwarg:
        names.append(function.args.kwarg.arg)
    return names


def _dependencies(target: _ConditionalTarget) -> tuple[list[str], str]:
    chain_index = target.function.body.index(target.chain)
    before = target.function.body[:chain_index]
    argument_names = _argument_names(target.function)
    known_before = set(argument_names) | _assigned_names(before)
    all_function_locals = set(argument_names) | _assigned_names(target.function.body)
    chain_nodes: list[ast.AST] = [*target.conditions]
    for body in target.bodies:
        chain_nodes.extend(body)
    loaded = {
        node.id
        for root in chain_nodes
        for node in ast.walk(root)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    stored_in_chain = {
        node.id
        for root in chain_nodes
        for node in ast.walk(root)
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    }
    unavailable_locals = (loaded & all_function_locals) - known_before - stored_in_chain
    if unavailable_locals:
        return [], "LOCAL_DEPENDENCY_NOT_AVAILABLE_BEFORE_CONDITIONAL"

    ordered = [name for name in argument_names if name in loaded]
    ordered.extend(sorted((known_before & loaded) - set(ordered)))
    return ordered, ""


def _pascal_case(value: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", str(value or ""))
    return "".join(part[:1].upper() + part[1:] for part in parts) or "Conditional"


def _valid_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""))


def _method_arguments(receiver: str, dependencies: list[str]) -> ast.arguments:
    return ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg=receiver), *[ast.arg(arg=name) for name in dependencies]],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )


def _strategy_classes(
    target: _ConditionalTarget,
    *,
    base_class_name: str,
    strategy_class_names: list[str],
    dependencies: list[str],
) -> list[ast.ClassDef]:
    receiver = "_strategy_self"
    while receiver in dependencies:
        receiver = f"_{receiver}"

    base = ast.ClassDef(
        name=base_class_name,
        bases=[],
        keywords=[],
        decorator_list=[],
        body=[
            ast.FunctionDef(
                name="matches",
                args=_method_arguments(receiver, dependencies),
                body=[ast.Raise(exc=ast.Call(func=ast.Name(id="NotImplementedError", ctx=ast.Load()), args=[], keywords=[]), cause=None)],
                decorator_list=[],
                returns=None,
                type_comment=None,
            ),
            ast.FunctionDef(
                name="execute",
                args=_method_arguments(receiver, dependencies),
                body=[ast.Raise(exc=ast.Call(func=ast.Name(id="NotImplementedError", ctx=ast.Load()), args=[], keywords=[]), cause=None)],
                decorator_list=[],
                returns=None,
                type_comment=None,
            ),
        ],
    )
    classes = [base]
    for index, (class_name, body) in enumerate(zip(strategy_class_names, target.bodies)):
        condition: ast.expr = (
            copy.deepcopy(target.conditions[index])
            if index < len(target.conditions)
            else ast.Constant(value=True)
        )
        execute_body = copy.deepcopy(body)
        if target.mode == "assignment_outputs":
            output_expression: ast.expr
            if len(target.outputs) == 1:
                output_expression = ast.Name(id=target.outputs[0], ctx=ast.Load())
            else:
                output_expression = ast.Tuple(
                    elts=[ast.Name(id=name, ctx=ast.Load()) for name in target.outputs],
                    ctx=ast.Load(),
                )
            execute_body.append(ast.Return(value=output_expression))
        classes.append(ast.ClassDef(
            name=class_name,
            bases=[ast.Name(id=base_class_name, ctx=ast.Load())],
            keywords=[],
            decorator_list=[],
            body=[
                ast.FunctionDef(
                    name="matches",
                    args=_method_arguments(receiver, dependencies),
                    body=[ast.Return(value=condition)],
                    decorator_list=[],
                    returns=None,
                    type_comment=None,
                ),
                ast.FunctionDef(
                    name="execute",
                    args=_method_arguments(receiver, dependencies),
                    body=execute_body,
                    decorator_list=[],
                    returns=None,
                    type_comment=None,
                ),
            ],
        ))
    return classes


def _dispatch_statement(
    *,
    strategy_class_names: list[str],
    dependencies: list[str],
    occupied_names: set[str],
    mode: str,
    outputs: list[str],
) -> ast.For:
    strategy_variable = "_sctva_strategy"
    while strategy_variable in occupied_names:
        strategy_variable = f"_{strategy_variable}"
    arguments = [ast.Name(id=name, ctx=ast.Load()) for name in dependencies]
    strategy_ref = ast.Name(id=strategy_variable, ctx=ast.Load())
    execute_call = ast.Call(
        func=ast.Attribute(value=copy.deepcopy(strategy_ref), attr="execute", ctx=ast.Load()),
        args=copy.deepcopy(arguments),
        keywords=[],
    )
    if mode == "assignment_outputs":
        assignment_target: ast.expr
        if len(outputs) == 1:
            assignment_target = ast.Name(id=outputs[0], ctx=ast.Store())
        else:
            assignment_target = ast.Tuple(
                elts=[ast.Name(id=name, ctx=ast.Store()) for name in outputs],
                ctx=ast.Store(),
            )
        dispatch_body: list[ast.stmt] = [
            ast.Assign(targets=[assignment_target], value=execute_call),
            ast.Break(),
        ]
    else:
        dispatch_body = [ast.Return(value=execute_call)]
    return ast.For(
        target=ast.Name(id=strategy_variable, ctx=ast.Store()),
        iter=ast.Tuple(
            elts=[
                ast.Call(func=ast.Name(id=name, ctx=ast.Load()), args=[], keywords=[])
                for name in strategy_class_names
            ],
            ctx=ast.Load(),
        ),
        body=[ast.If(
            test=ast.Call(
                func=ast.Attribute(value=copy.deepcopy(strategy_ref), attr="matches", ctx=ast.Load()),
                args=copy.deepcopy(arguments),
                keywords=[],
            ),
            body=dispatch_body,
            orelse=[],
        )],
        orelse=[],
        type_comment=None,
    )


def apply_replace_conditional_with_polymorphism(
    source_code: str,
    *,
    method_name: str = "",
    source_class: str = "",
    source_line: int | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    base_class_name: str = "",
) -> tuple[str, int, dict[str, Any]]:
    """Replace one terminal Python conditional chain with strategy subclasses."""

    resolution = resolve_target(
        source_code,
        method_name=method_name,
        source_class=source_class,
        source_line=source_line,
        start_line=start_line,
        end_line=end_line,
    )
    if resolution.get("status") != "success":
        return source_code, 0, resolution
    target: _ConditionalTarget = resolution["target"]

    dependencies, dependency_error = _dependencies(target)
    if dependency_error:
        return source_code, 0, {"status": "review_required", "reason": dependency_error}

    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return source_code, 0, {"status": "review_required", "reason": "SOURCE_PARSE_FAILED"}
    existing_names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    base_class_name = base_class_name or f"_Sctva{_pascal_case(target.function.name)}PolymorphicCase"
    if not _valid_identifier(base_class_name):
        return source_code, 0, {"status": "review_required", "reason": "INVALID_POLYMORPHIC_BASE_CLASS_NAME"}
    if base_class_name in existing_names:
        return source_code, 0, {"status": "not_applicable", "reason": "POLYMORPHISM_ALREADY_APPLIED"}

    strategy_names = [
        f"{base_class_name}Case{index + 1}"
        for index in range(len(target.conditions))
    ]
    strategy_names.append(f"{base_class_name}DefaultCase")
    if existing_names & set(strategy_names):
        return source_code, 0, {"status": "review_required", "reason": "POLYMORPHIC_CLASS_NAME_COLLISION"}

    classes = _strategy_classes(
        target,
        base_class_name=base_class_name,
        strategy_class_names=strategy_names,
        dependencies=dependencies,
    )
    class_source = ast.unparse(ast.fix_missing_locations(ast.Module(body=classes, type_ignores=[]))) + "\n\n"
    occupied_names = _assigned_names(target.function.body) | set(_argument_names(target.function))
    dispatch = _dispatch_statement(
        strategy_class_names=strategy_names,
        dependencies=dependencies,
        occupied_names=occupied_names,
        mode=target.mode,
        outputs=target.outputs,
    )
    dispatch_source = ast.unparse(ast.fix_missing_locations(dispatch))
    indentation = " " * int(target.chain.col_offset)
    dispatch_source = "\n".join(
        f"{indentation}{line}" if line else line
        for line in dispatch_source.splitlines()
    )

    offsets = _line_offsets(source_code)
    # Replace from the physical line start because ``dispatch_source`` already
    # carries the function/class indentation. Keeping the original leading
    # spaces as well would double-indent the first generated ``for`` line.
    chain_start = _offset(offsets, int(target.chain.lineno), 0)
    chain_end = _offset(offsets, int(target.chain.end_lineno), int(target.chain.end_col_offset))
    insertion_node: ast.AST = target.function
    if target.owner_class:
        owner = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == target.owner_class
            ),
            None,
        )
        if owner is None:
            return source_code, 0, {
                "status": "review_required",
                "reason": "SOURCE_CLASS_NOT_FOUND_DURING_INSERTION",
            }
        insertion_node = owner
    decorator_lines = [int(item.lineno) for item in getattr(insertion_node, "decorator_list", [])]
    insertion_line = min([int(insertion_node.lineno), *decorator_lines])
    insertion_offset = _offset(offsets, insertion_line, 0)

    transformed = source_code[:chain_start] + dispatch_source + source_code[chain_end:]
    transformed = transformed[:insertion_offset] + class_source + transformed[insertion_offset:]
    try:
        ast.parse(transformed)
        compile(transformed, "<sctva-polymorphism>", "exec")
    except (SyntaxError, ValueError, TypeError) as exc:
        return source_code, 0, {
            "status": "review_required",
            "reason": "GENERATED_POLYMORPHISM_SYNTAX_FAILED",
            "diagnostic": str(exc),
        }

    effective_parameters = {
        "method": target.function.name,
        "source_class": target.owner_class,
        "source_line": int(target.chain.lineno),
        "base_class_name": base_class_name,
        "strategy_class_names": strategy_names,
        "branch_count": len(target.bodies),
        "dependencies": dependencies,
        "original_chain_fingerprint": ast.dump(target.chain, include_attributes=False),
        "mode": target.mode,
        "outputs": target.outputs,
    }
    return transformed, 2, {
        "status": "success",
        "method": target.function.name,
        "source_class": target.owner_class,
        "base_class_name": base_class_name,
        "strategy_class_names": strategy_names,
        "branch_count": len(target.bodies),
        "dependencies": dependencies,
        "mode": target.mode,
        "outputs": target.outputs,
        "target_resolution": resolution["strategy"],
        "effective_action_parameters": effective_parameters,
    }


def validate_transformation(
    original_code: str,
    transformed_code: str,
    *,
    method_name: str,
    source_class: str,
    base_class_name: str,
    strategy_class_names: list[str],
    source_line: int | None = None,
    mode: str = "terminal",
    outputs: list[str] | None = None,
) -> dict[str, Any]:
    """Prove that the conditional logic moved into real strategy subclasses."""

    try:
        before_tree = ast.parse(original_code)
        after_tree = ast.parse(transformed_code)
    except SyntaxError:
        return {"passed": False, "reason": "syntax_parse_failed"}

    before_resolution = resolve_target(
        original_code,
        method_name=method_name,
        source_class=source_class,
        source_line=source_line,
    )
    if before_resolution.get("status") != "success":
        return {"passed": False, "reason": "original_conditional_target_not_found"}
    original_target: _ConditionalTarget = before_resolution["target"]

    def find_function(tree: ast.Module) -> ast.FunctionDef | None:
        for function, owner in _top_level_functions(tree):
            if function.name == method_name and owner == source_class:
                return function
        return None

    before_function = find_function(before_tree)
    after_function = find_function(after_tree)
    classes = {
        node.name: node
        for node in after_tree.body
        if isinstance(node, ast.ClassDef)
    }
    base = classes.get(base_class_name)
    strategies = [classes.get(name) for name in strategy_class_names]

    def method(node: ast.ClassDef | None, name: str) -> ast.FunctionDef | None:
        if node is None:
            return None
        return next(
            (item for item in node.body if isinstance(item, ast.FunctionDef) and item.name == name),
            None,
        )

    dispatch_found = False
    if after_function is not None:
        for node in ast.walk(after_function):
            if not isinstance(node, ast.For):
                continue
            constructor_names = {
                item.func.id
                for item in ast.walk(node.iter)
                if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
            }
            calls = {
                item.func.attr
                for item in ast.walk(node)
                if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)
            }
            if set(strategy_class_names) <= constructor_names and {"matches", "execute"} <= calls:
                dispatch_found = True
                break

    original_body_fingerprints = [
        ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False)
        for body in original_target.bodies
    ]
    moved_body_fingerprints = []
    for strategy in strategies:
        execute = method(strategy, "execute")
        if execute is not None:
            execute_body = execute.body
            if mode == "assignment_outputs" and execute_body and isinstance(execute_body[-1], ast.Return):
                execute_body = execute_body[:-1]
            moved_body_fingerprints.append(
                ast.dump(ast.Module(body=execute_body, type_ignores=[]), include_attributes=False)
            )

    original_if_count = sum(isinstance(node, ast.If) for node in ast.walk(original_target.function))
    transformed_if_count = (
        sum(isinstance(node, ast.If) for node in ast.walk(after_function))
        if after_function is not None
        else original_if_count
    )
    original_chain_fingerprint = ast.dump(original_target.chain, include_attributes=False)
    transformed_function_dump = (
        ast.dump(after_function, include_attributes=False)
        if after_function is not None
        else ""
    )
    checks = {
        "target_function_preserved": before_function is not None and after_function is not None,
        "public_signature_preserved": (
            before_function is not None
            and after_function is not None
            and ast.dump(before_function.args, include_attributes=False)
            == ast.dump(after_function.args, include_attributes=False)
        ),
        "base_strategy_created": base is not None and method(base, "matches") is not None and method(base, "execute") is not None,
        "all_strategy_subclasses_created": all(
            strategy is not None
            and any(isinstance(parent, ast.Name) and parent.id == base_class_name for parent in strategy.bases)
            for strategy in strategies
        ),
        "branch_behavior_moved": original_body_fingerprints == moved_body_fingerprints,
        "polymorphic_dispatch_added": dispatch_found,
        "original_conditional_chain_removed": original_chain_fingerprint not in transformed_function_dump,
        "conditional_complexity_reduced": transformed_if_count < original_if_count,
        "python_syntax_valid": True,
    }
    return {
        "passed": all(checks.values()),
        "method": method_name,
        "source_class": source_class,
        "base_class_name": base_class_name,
        "strategy_class_names": strategy_class_names,
        "before_if_count": original_if_count,
        "after_if_count": transformed_if_count,
        "checks": checks,
    }


__all__ = [
    "apply_replace_conditional_with_polymorphism",
    "resolve_target",
    "validate_transformation",
]
