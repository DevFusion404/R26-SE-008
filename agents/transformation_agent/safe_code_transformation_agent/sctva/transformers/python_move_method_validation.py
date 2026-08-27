"""Shared semantic validation for Python Feature-Envy Move Method.

The transformer validates the move immediately, while the structural validator
runs only after all plan steps have completed.  Keeping the semantic proof in
one small module prevents those two stages from disagreeing about an otherwise
safe receiver migration such as ``student.score`` -> ``self.score``.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import re
from typing import Any, Dict


class _MoveMethodNormalizer(ast.NodeTransformer):
    """Normalize only SCTVA's expected Move Method follow-up changes."""

    _SCTVA_CONSTANT_NAME = re.compile(
        r"^(?:CONSTANT_|MAGIC_|EXTRACTED_CONSTANT)[A-Za-z0-9_]*$"
    )

    def __init__(
        self,
        *,
        destination_parameter: str = "",
        constant_values: Dict[str, Any] | None = None,
    ) -> None:
        self.destination_parameter = destination_parameter
        self.constant_values = dict(constant_values or {})

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if self.destination_parameter and node.id == self.destination_parameter:
            return ast.copy_location(ast.Name(id="self", ctx=node.ctx), node)
        if (
            self._SCTVA_CONSTANT_NAME.match(node.id)
            and node.id in self.constant_values
        ):
            return ast.copy_location(
                ast.Constant(value=self.constant_values[node.id]), node
            )
        return node


def validate_python_move_method(
    *,
    original_code: str,
    transformed_code: str,
    method_name: str,
    source_class: str,
    destination_class: str,
    destination_parameter: str = "",
    action_evidence: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return strict structural evidence for one Python Move Method action.

    With action-time evidence, this proves the actual move at the instant it
    occurred and then checks the final program still preserves the essential
    Move Method shape.  Without evidence it performs the same complete
    semantic comparison directly against the final source, which keeps direct
    validator use and regression tests strict.
    """

    if not method_name or not source_class or not destination_class:
        return {"passed": False, "reason": "missing_move_method_targets"}
    try:
        before_tree = ast.parse(original_code)
        after_tree = ast.parse(transformed_code)
    except SyntaxError:
        return {"passed": False, "reason": "parse_failed"}

    before_source = _top_level_class(before_tree, source_class)
    after_source = _top_level_class(after_tree, source_class)
    after_destination = _top_level_class(after_tree, destination_class)
    if before_source is None or after_destination is None:
        return {"passed": False, "reason": "source_or_destination_class_not_found"}

    original_method = _class_method(before_source, method_name)
    moved_method = _class_method(after_destination, method_name)
    if original_method is None or moved_method is None:
        return {"passed": False, "reason": "source_or_destination_method_not_found"}
    if len(original_method.args.args) < 2:
        return {"passed": False, "reason": "original_destination_parameter_not_found"}

    resolved_parameter = destination_parameter or original_method.args.args[1].arg
    expected_body = normalized_method_body_dump(
        original_method,
        destination_parameter=resolved_parameter,
        constant_values=_sctva_module_constant_values(before_tree),
    )
    final_body = normalized_method_body_dump(
        moved_method,
        constant_values=_sctva_module_constant_values(after_tree),
    )
    signature_migration_valid = signature_migrated(
        original_method,
        moved_method,
        destination_parameter=resolved_parameter,
    )
    moved_body_is_real_logic = method_has_real_logic(moved_method)
    # A later Inline Class cleanup may remove the now-empty source class.  That
    # is a valid stronger form of source-method removal, provided the moved
    # method and its rewritten calls remain in the destination class.
    source_class_removed = after_source is None
    source_method_removed = source_class_removed or _class_method(after_source, method_name) is None
    old_parameter_remaining = any(
        isinstance(node, ast.Name) and node.id == resolved_parameter
        for node in ast.walk(moved_method)
    )
    original_destination_accesses = sum(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == resolved_parameter
        for node in ast.walk(original_method)
    )
    moved_self_accesses = sum(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        for node in ast.walk(moved_method)
    )
    before_calls = _external_method_call_count(before_tree, original_method, method_name)
    after_calls = _external_method_call_count(after_tree, moved_method, method_name)
    known_source_instances = _known_class_instances(before_tree, source_class)
    stale_source_calls = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method_name
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in known_source_instances
        for node in ast.walk(after_tree)
    )

    evidence_status = _verify_action_evidence(
        action_evidence=action_evidence,
        expected_body=expected_body,
    )
    action_time_equivalence = (
        evidence_status["passed"]
        if evidence_status["provided"]
        else expected_body == final_body
    )
    checks = {
        "requested_source_method_existed_before": True,
        "method_now_exists_in_destination_class": True,
        "method_removed_from_source_class": source_method_removed,
        "actual_method_logic_moved": (
            action_time_equivalence
            and signature_migration_valid
            and moved_body_is_real_logic
        ),
        "destination_object_references_changed_to_self": (
            not old_parameter_remaining
            and moved_self_accesses >= original_destination_accesses
        ),
        "relevant_direct_call_sites_updated": (
            after_calls >= before_calls and not stale_source_calls
        ),
        "logic_not_duplicated": source_method_removed,
        "source_class_responsibility_reduced": source_method_removed,
        "python_syntax_valid": True,
    }
    return {
        "passed": all(checks.values()),
        "language": "python",
        "method": method_name,
        "source_class": source_class,
        "destination_class": destination_class,
        "before_direct_call_sites": before_calls,
        "after_direct_call_sites": after_calls,
        "source_class_removed_after": source_class_removed,
        "logic_equivalence": "PASS" if action_time_equivalence else "FAIL",
        "receiver_normalization": (
            "PASS"
            if not old_parameter_remaining and moved_self_accesses >= original_destination_accesses
            else "FAIL"
        ),
        "signature_migration": "PASS" if signature_migration_valid else "FAIL",
        "validation_evidence": evidence_status,
        "checks": checks,
    }


def create_python_move_method_evidence(
    *,
    original_code: str,
    transformed_code: str,
    method_name: str,
    source_class: str,
    destination_class: str,
    destination_parameter: str,
) -> Dict[str, Any]:
    """Create verified action-time evidence for later final validation."""

    result = validate_python_move_method(
        original_code=original_code,
        transformed_code=transformed_code,
        method_name=method_name,
        source_class=source_class,
        destination_class=destination_class,
        destination_parameter=destination_parameter,
    )
    if not result.get("passed"):
        return {"status": "FAIL", "reason": "MOVE_METHOD_SEMANTIC_VALIDATION_FAILED", "checks": result.get("checks", {})}

    before_tree = ast.parse(original_code)
    after_tree = ast.parse(transformed_code)
    original_method = _class_method(_top_level_class(before_tree, source_class), method_name)
    moved_method = _class_method(_top_level_class(after_tree, destination_class), method_name)
    assert original_method is not None and moved_method is not None
    original_body = normalized_method_body_dump(
        original_method,
        destination_parameter=destination_parameter,
        constant_values=_sctva_module_constant_values(before_tree),
    )
    post_move_body = normalized_method_body_dump(
        moved_method,
        constant_values=_sctva_module_constant_values(after_tree),
    )
    return {
        "status": "PASS",
        "method": method_name,
        "source_class": source_class,
        "destination_class": destination_class,
        "destination_parameter": destination_parameter,
        "original_normalized_body": original_body,
        "post_move_normalized_body": post_move_body,
        "original_body_hash": _body_hash(original_body),
        "post_move_body_hash": _body_hash(post_move_body),
        "logic_equivalence": "PASS",
        "receiver_normalization": "PASS",
        "signature_migration": "PASS",
    }


def normalized_method_body_dump(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    destination_parameter: str = "",
    constant_values: Dict[str, Any] | None = None,
) -> str:
    normalized = _MoveMethodNormalizer(
        destination_parameter=destination_parameter,
        constant_values=constant_values,
    ).visit(copy.deepcopy(method))
    assert isinstance(normalized, (ast.FunctionDef, ast.AsyncFunctionDef))
    return ast.dump(
        ast.fix_missing_locations(ast.Module(body=list(normalized.body), type_ignores=[])),
        include_attributes=False,
    )


def signature_migrated(
    original_method: ast.FunctionDef | ast.AsyncFunctionDef,
    moved_method: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    destination_parameter: str,
) -> bool:
    def arg_names(args: ast.arguments) -> list[str]:
        return [arg.arg for arg in [*args.posonlyargs, *args.args]]

    expected_args = [name for name in arg_names(original_method.args) if name != destination_parameter]
    if expected_args != arg_names(moved_method.args):
        return False
    if bool(original_method.args.vararg) != bool(moved_method.args.vararg):
        return False
    if bool(original_method.args.kwarg) != bool(moved_method.args.kwarg):
        return False
    if [arg.arg for arg in original_method.args.kwonlyargs] != [arg.arg for arg in moved_method.args.kwonlyargs]:
        return False
    if len(original_method.args.defaults) != len(moved_method.args.defaults):
        return False
    if len([item for item in original_method.args.kw_defaults if item is not None]) != len([item for item in moved_method.args.kw_defaults if item is not None]):
        return False
    if isinstance(original_method, ast.AsyncFunctionDef) != isinstance(moved_method, ast.AsyncFunctionDef):
        return False
    if original_method.returns is None or moved_method.returns is None:
        return original_method.returns is None and moved_method.returns is None
    return ast.dump(original_method.returns, include_attributes=False) == ast.dump(moved_method.returns, include_attributes=False)


def method_has_real_logic(method: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = list(method.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    return bool(body) and not all(
        isinstance(statement, ast.Pass)
        or (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and statement.value.value is Ellipsis)
        for statement in body
    )


def _verify_action_evidence(*, action_evidence: Dict[str, Any] | None, expected_body: str) -> Dict[str, Any]:
    if not isinstance(action_evidence, dict) or not action_evidence:
        return {"provided": False, "passed": False, "reason": "not_provided"}
    original_body = str(action_evidence.get("original_normalized_body") or "")
    post_move_body = str(action_evidence.get("post_move_normalized_body") or "")
    valid = (
        action_evidence.get("status") == "PASS"
        and original_body == expected_body
        and post_move_body == original_body
        and str(action_evidence.get("original_body_hash") or "") == _body_hash(original_body)
        and str(action_evidence.get("post_move_body_hash") or "") == _body_hash(post_move_body)
        and action_evidence.get("logic_equivalence") == "PASS"
        and action_evidence.get("receiver_normalization") == "PASS"
        and action_evidence.get("signature_migration") == "PASS"
    )
    return {
        "provided": True,
        "passed": valid,
        "reason": "action_time_semantic_proof" if valid else "invalid_action_time_semantic_proof",
    }


def _body_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _top_level_class(tree: ast.Module, name: str) -> ast.ClassDef | None:
    matches = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name]
    return matches[0] if len(matches) == 1 else None


def _class_method(owner: ast.ClassDef | None, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    if owner is None:
        return None
    matches = [node for node in owner.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    return matches[0] if len(matches) == 1 else None


def _sctva_module_constant_values(tree: ast.Module) -> Dict[str, Any]:
    constant_name = re.compile(r"^(?:CONSTANT_|MAGIC_|EXTRACTED_CONSTANT)[A-Za-z0-9_]*$")
    values: Dict[str, Any] = {}
    for statement in tree.body:
        target_name = ""
        value_node: ast.AST | None = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            target_name, value_node = statement.targets[0].id, statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            target_name, value_node = statement.target.id, statement.value
        if not target_name or value_node is None or not constant_name.match(target_name):
            continue
        try:
            value = ast.literal_eval(value_node)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            continue
        if isinstance(value, (str, bytes, int, float, complex, bool, type(None))):
            values[target_name] = value
    return values


def _external_method_call_count(tree: ast.Module, method: ast.AST, method_name: str) -> int:
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != method_name:
            continue
        current: ast.AST | None = node
        while current is not None and current is not method:
            current = parents.get(current)
        if current is None:
            count += 1
    return count


def _known_class_instances(tree: ast.Module, class_name: str) -> set[str]:
    return {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == class_name
        for target in node.targets
        if isinstance(target, ast.Name)
    }
