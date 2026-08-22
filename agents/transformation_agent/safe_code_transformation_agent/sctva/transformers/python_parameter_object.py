"""Python Introduce Parameter Object refactoring implemented with LibCST."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any, Dict, Sequence

import libcst as cst
from libcst.metadata import MetadataWrapper, ParentNodeProvider


@dataclass(frozen=True)
class PythonParameter:
    name: str
    annotation: str
    default: str | None


def apply_introduce_parameter_object(
    source_code: str,
    *,
    method: str,
    parameter_object_name: str,
    source_class: str = "",
    source_file: str = "",
    current_file_name: str = "",
    parameter_name: str = "params",
    project_source_files: Sequence[Any] | None = None,
    source_resolution_error: str = "",
) -> tuple[str, int, Dict[str, Any]]:
    metadata: Dict[str, Any] = {
        "refactoring": "Introduce Parameter Object",
        "language": "python",
        "method": method,
        "source_class": source_class,
        "parameter_object_name": parameter_object_name,
        "parameter_name": parameter_name,
        "source_file": source_file or current_file_name,
        "plan_compliance": "FAIL",
    }
    if source_resolution_error:
        return _review(source_code, source_resolution_error, metadata)
    error = _validate_identifiers(method, parameter_object_name, parameter_name)
    if error:
        return _review(source_code, error, metadata)
    if source_file and current_file_name and not _paths_match(source_file, current_file_name):
        return _review(source_code, "SOURCE_FILE_MISMATCH", metadata)

    try:
        tree = ast.parse(source_code)
        module = cst.parse_module(source_code)
    except (SyntaxError, cst.ParserSyntaxError):
        return _review(source_code, "SOURCE_PARSE_FAILED", metadata)

    existing_class = next(
        (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == parameter_object_name),
        None,
    )
    targets = _find_targets(tree, method=method, source_class=source_class)
    if len(targets) != 1:
        reason = "TARGET_NOT_FOUND" if not targets else "AMBIGUOUS_TARGET_FUNCTION"
        return _review(source_code, reason, metadata)
    target, owner = targets[0]
    if existing_class is not None:
        return _review(source_code, "PARAMETER_OBJECT_ALREADY_EXISTS_WITH_LONG_SIGNATURE", metadata)

    params_or_error = _python_parameters(source_code, target, owner=owner)
    if isinstance(params_or_error, str):
        return _review(source_code, params_or_error, metadata)
    receiver, parameters = params_or_error
    if len(parameters) < 2:
        return _review(source_code, "PARAMETER_COUNT_NOT_REDUCIBLE", metadata)
    if parameter_name in {item.name for item in parameters} or parameter_name == receiver:
        return _review(source_code, "PARAMETER_NAME_COLLISION", metadata)
    if _has_unsafe_nested_shadowing(target, {item.name for item in parameters}):
        return _review(source_code, "NESTED_SCOPE_PARAMETER_SHADOWING", metadata)
    if _has_ambiguous_same_name_declaration(tree, target, method):
        return _review(source_code, "AMBIGUOUS_SAME_NAME_CALL_TARGET", metadata)
    external_callers = _python_external_callers(
        method,
        project_source_files,
        current_file_name=current_file_name or source_file,
    )
    if external_callers:
        metadata["unresolved_external_callers"] = external_callers
        return _review(source_code, "CROSS_FILE_CALL_SITES_REQUIRE_COORDINATED_EDIT", metadata)

    transformer = _IntroduceParameterObjectTransformer(
        method=method,
        source_class=owner,
        parameter_object_name=parameter_object_name,
        parameter_name=parameter_name,
        receiver=receiver,
        moved_names={item.name for item in parameters},
    )
    try:
        transformed_module = MetadataWrapper(module).visit(transformer)
    except Exception as exc:
        metadata["internal_error"] = str(exc)
        return _review(source_code, "CST_TRANSFORMATION_FAILED", metadata)

    if transformer.target_count != 1:
        return _review(source_code, "TARGET_NOT_FOUND", metadata)
    class_source = _parameter_class_source(parameter_object_name, parameters)
    transformed_module = _insert_parameter_class(
        transformed_module,
        class_source=class_source,
        add_dataclass_import=not _has_dataclass_import(tree),
    )
    transformed = transformed_module.code

    validation = _validate_python_result(
        source_code,
        transformed,
        method=method,
        source_class=owner,
        parameter_object_name=parameter_object_name,
        parameter_name=parameter_name,
        original_parameters=parameters,
        original_call_count=transformer.call_sites_updated,
    )
    metadata.update({
        "parameters_moved": [item.name for item in parameters],
        "before_parameter_count": len(parameters),
        "after_parameter_count": 1,
        "call_sites_updated": transformer.call_sites_updated,
        "validation": validation,
    })
    if "FAIL" in validation.values():
        return _review(source_code, "STRUCTURAL_POSTCONDITION_FAILED", metadata)
    metadata.update({"status": "success", "reason": "parameter_object_introduced", "plan_compliance": "PASS"})
    return transformed, 1, metadata


class _IntroduceParameterObjectTransformer(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (ParentNodeProvider,)

    def __init__(
        self,
        *,
        method: str,
        source_class: str,
        parameter_object_name: str,
        parameter_name: str,
        receiver: str,
        moved_names: set[str],
    ) -> None:
        self.method = method
        self.source_class = source_class
        self.parameter_object_name = parameter_object_name
        self.parameter_name = parameter_name
        self.receiver = receiver
        self.moved_names = moved_names
        self.class_stack: list[str] = []
        self.active_target = 0
        self.target_count = 0
        self.call_sites_updated = 0

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self.class_stack.append(node.name.value)
        return True

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        self.class_stack.pop()
        return updated_node

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        owner = self.class_stack[-1] if self.class_stack else ""
        if node.name.value == self.method and owner == self.source_class:
            self.active_target += 1
            self.target_count += 1
        return True

    def leave_FunctionDef(
        self,
        original_node: cst.FunctionDef,
        updated_node: cst.FunctionDef,
    ) -> cst.FunctionDef:
        owner = self.class_stack[-1] if self.class_stack else ""
        if original_node.name.value != self.method or owner != self.source_class:
            return updated_node
        retained: list[cst.Param] = []
        if self.receiver:
            retained.append(cst.Param(name=cst.Name(self.receiver)))
        retained.append(cst.Param(
            name=cst.Name(self.parameter_name),
            annotation=cst.Annotation(cst.Name(self.parameter_object_name)),
        ))
        self.active_target -= 1
        return updated_node.with_changes(params=cst.Parameters(params=retained))

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.BaseExpression:
        if not self.active_target or original_node.value not in self.moved_names:
            return updated_node
        parent = self.get_metadata(ParentNodeProvider, original_node, None)
        if isinstance(parent, cst.Attribute) and parent.attr is original_node:
            return updated_node
        if isinstance(parent, cst.Arg) and parent.keyword is original_node:
            return updated_node
        if isinstance(parent, (cst.Param, cst.Annotation, cst.ImportAlias)):
            return updated_node
        return cst.Attribute(value=cst.Name(self.parameter_name), attr=cst.Name(original_node.value))

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.Call:
        if not _python_call_matches(original_node.func, self.method):
            return updated_node
        if (
            len(updated_node.args) == 1
            and isinstance(updated_node.args[0].value, cst.Call)
            and isinstance(updated_node.args[0].value.func, cst.Name)
            and updated_node.args[0].value.func.value == self.parameter_object_name
        ):
            return updated_node
        self.call_sites_updated += 1
        parameter_object_call = cst.Call(
            func=cst.Name(self.parameter_object_name),
            args=list(updated_node.args),
        )
        return updated_node.with_changes(args=[cst.Arg(value=parameter_object_call)])


def _find_targets(
    tree: ast.Module,
    *,
    method: str,
    source_class: str,
) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    found: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not source_class and node.name == method:
            found.append((node, ""))
        if isinstance(node, ast.ClassDef):
            if source_class and node.name != source_class:
                continue
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method:
                    found.append((child, node.name))
    return found


def _python_parameters(
    source: str,
    target: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    owner: str,
) -> tuple[str, list[PythonParameter]] | str:
    args = target.args
    if args.vararg or args.kwarg or args.kwonlyargs:
        return "VARIADIC_OR_KEYWORD_ONLY_PARAMETERS_UNSUPPORTED"
    positional = [*args.posonlyargs, *args.args]
    receiver = ""
    if owner and positional and positional[0].arg in {"self", "cls"}:
        receiver = positional.pop(0).arg
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    result: list[PythonParameter] = []
    for item, default in zip(positional, defaults):
        annotation = ast.get_source_segment(source, item.annotation) if item.annotation else "object"
        default_source = ast.get_source_segment(source, default) if default is not None else None
        result.append(PythonParameter(item.arg, annotation or "object", default_source))
    return receiver, result


def _parameter_class_source(name: str, parameters: Sequence[PythonParameter]) -> str:
    fields = []
    for item in parameters:
        default = f" = {item.default}" if item.default is not None else ""
        fields.append(f"    {item.name}: {item.annotation}{default}")
    return "@dataclass\nclass " + name + ":\n" + "\n".join(fields) + "\n"


def _insert_parameter_class(
    module: cst.Module,
    *,
    class_source: str,
    add_dataclass_import: bool,
) -> cst.Module:
    additions: list[cst.BaseStatement] = []
    if add_dataclass_import:
        additions.append(cst.parse_statement("from dataclasses import dataclass\n"))
    additions.append(cst.parse_statement(class_source))
    body = list(module.body)
    insertion = 0
    if body and _is_docstring_statement(body[0]):
        insertion = 1
    while insertion < len(body) and _is_import_statement(body[insertion]):
        insertion += 1
    return module.with_changes(body=[*body[:insertion], *additions, *body[insertion:]])


def _is_docstring_statement(node: cst.BaseStatement) -> bool:
    return bool(
        isinstance(node, cst.SimpleStatementLine)
        and node.body
        and isinstance(node.body[0], cst.Expr)
        and isinstance(node.body[0].value, cst.SimpleString)
    )


def _is_import_statement(node: cst.BaseStatement) -> bool:
    return bool(
        isinstance(node, cst.SimpleStatementLine)
        and node.body
        and all(isinstance(item, (cst.Import, cst.ImportFrom)) for item in node.body)
    )


def _has_dataclass_import(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "dataclasses"
        and any(alias.name == "dataclass" for alias in node.names)
        for node in tree.body
    )


def _has_unsafe_nested_shadowing(
    target: ast.FunctionDef | ast.AsyncFunctionDef,
    moved_names: set[str],
) -> bool:
    for node in ast.walk(target):
        if node is target:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            nested_args = {item.arg for item in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
            if nested_args & moved_names:
                return True
        if isinstance(node, ast.comprehension):
            bound = {item.id for item in ast.walk(node.target) if isinstance(item, ast.Name)}
            if bound & moved_names:
                return True
    return False


def _has_ambiguous_same_name_declaration(
    tree: ast.Module,
    target: ast.AST,
    method: str,
) -> bool:
    declarations = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method
    ]
    return any(node is not target for node in declarations)


def _python_call_matches(function: cst.BaseExpression, method: str) -> bool:
    return (
        isinstance(function, cst.Name) and function.value == method
    ) or (
        isinstance(function, cst.Attribute) and function.attr.value == method
    )


def _python_external_callers(
    method: str,
    project_source_files: Sequence[Any] | None,
    *,
    current_file_name: str,
) -> list[str]:
    callers: list[str] = []
    for item in project_source_files or []:
        file_name = str(item.get("file_name") if isinstance(item, dict) else getattr(item, "file_name", ""))
        if _paths_match(file_name, current_file_name):
            continue
        source = item.get("source_code") if isinstance(item, dict) else getattr(item, "source_code", "")
        try:
            tree = ast.parse(source or "")
        except SyntaxError:
            continue
        if any(
            isinstance(node, ast.Call)
            and (
                isinstance(node.func, ast.Name) and node.func.id == method
                or isinstance(node.func, ast.Attribute) and node.func.attr == method
            )
            for node in ast.walk(tree)
        ):
            callers.append(file_name)
    return sorted(set(callers))


def _validate_python_result(
    original: str,
    transformed: str,
    *,
    method: str,
    source_class: str,
    parameter_object_name: str,
    parameter_name: str,
    original_parameters: Sequence[PythonParameter],
    original_call_count: int,
) -> Dict[str, str]:
    try:
        tree = ast.parse(transformed)
    except SyntaxError:
        return {"syntax": "FAIL", "parameter_object": "FAIL", "signature_reduction": "FAIL", "body_access": "FAIL", "call_sites": "FAIL"}
    targets = _find_targets(tree, method=method, source_class=source_class)
    object_node = next((node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == parameter_object_name), None)
    fields = {
        node.target.id
        for node in (object_node.body if object_node else [])
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    target = targets[0][0] if len(targets) == 1 else None
    expected = {item.name for item in original_parameters}
    after_args = [] if target is None else [item.arg for item in [*target.args.posonlyargs, *target.args.args] if item.arg not in {"self", "cls"}]
    used_original = {
        node.id for node in ast.walk(ast.parse(original))
        if isinstance(node, ast.Name) and node.id in expected and isinstance(node.ctx, ast.Load)
    }
    body_accesses = set()
    if target is not None:
        body_accesses = {
            node.attr for node in ast.walk(target)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == parameter_name
        }
    old_calls = 0
    object_calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not (
            isinstance(node.func, ast.Name) and node.func.id == method
            or isinstance(node.func, ast.Attribute) and node.func.attr == method
        ):
            continue
        if len(node.args) == 1 and isinstance(node.args[0], ast.Call) and isinstance(node.args[0].func, ast.Name) and node.args[0].func.id == parameter_object_name:
            object_calls += 1
        elif len(node.args) + len(node.keywords) > 1:
            old_calls += 1
    return {
        "syntax": "PASS",
        "parameter_object": "PASS" if object_node is not None and expected <= fields else "FAIL",
        "signature_reduction": "PASS" if after_args == [parameter_name] else "FAIL",
        "body_access": "PASS" if used_original <= body_accesses else "FAIL",
        "call_sites": "PASS" if old_calls == 0 and object_calls >= original_call_count else "FAIL",
    }


def _validate_identifiers(method: str, object_name: str, parameter_name: str) -> str:
    if not method or not method.isidentifier():
        return "INVALID_METHOD_NAME"
    if not object_name or not object_name.isidentifier():
        return "INVALID_PARAMETER_OBJECT_NAME"
    if not parameter_name or not parameter_name.isidentifier():
        return "INVALID_PARAMETER_NAME"
    return ""


def _paths_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    a = left.replace("\\", "/").lower().strip()
    b = right.replace("\\", "/").lower().strip()
    return a == b or a.rsplit("/", 1)[-1] == b.rsplit("/", 1)[-1]


def _review(source: str, reason: str, metadata: Dict[str, Any]) -> tuple[str, int, Dict[str, Any]]:
    return source, 0, {**metadata, "status": "review_required", "reason": reason}
