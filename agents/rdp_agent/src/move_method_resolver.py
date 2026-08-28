"""RDP-side validation for Python Feature-Envy Move Method planning.

The planner must only emit Move Method when repository source proves a real
class method can be moved to another real class.  This module intentionally
does not invent destination classes from filenames or module names.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import CodeSmell


_PYTHON_LANGUAGES = {"python", "py", "python3"}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _norm_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip()


def _path_basename(value: Any) -> str:
    return PurePath(_norm_path(value)).name


def _path_stem(value: Any) -> str:
    name = _path_basename(value)
    return name.rsplit(".", 1)[0] if "." in name else name


def _norm_symbol(value: Any) -> str:
    return str(value or "").strip()


def _symbol_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _safe_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == value:
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _lines_start(lines: Any) -> Optional[int]:
    if isinstance(lines, list) and lines:
        return _safe_int(lines[0])
    return None


def _is_synthetic_target(value: str, class_names: Iterable[str] = ()) -> bool:
    if not value:
        return False
    if value in set(class_names):
        return False
    return bool(re.search(r"Target$", value))


def _is_placeholder_symbol(value: str, source_file: str = "") -> bool:
    if not value:
        return True
    lowered = value.lower()
    if lowered in {"unknown", "null", "none", "n/a", "na"}:
        return True
    stem = _path_stem(source_file)
    return bool(stem and value in {stem, _path_basename(source_file)})


def _annotation_class_name(annotation: ast.AST | None, class_names: set[str]) -> str:
    if isinstance(annotation, ast.Name) and annotation.id in class_names:
        return annotation.id
    if isinstance(annotation, ast.Attribute) and annotation.attr in class_names:
        return annotation.attr
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        value = annotation.value.strip("'\"")
        if value in class_names:
            return value
    return ""


@dataclass
class _PythonFileSymbols:
    file_name: str
    source_code: str
    tree: ast.Module
    classes: Dict[str, ast.ClassDef]
    module_functions: Dict[str, ast.AST]
    parents: Dict[ast.AST, ast.AST] = field(default_factory=dict)
    instance_types: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_source(cls, file_name: str, source_code: str) -> "_PythonFileSymbols | None":
        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return None

        classes = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
        }
        module_functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }

        symbols = cls(
            file_name=_norm_path(file_name),
            source_code=source_code,
            tree=tree,
            classes=classes,
            module_functions=module_functions,
            parents=parents,
        )
        symbols.instance_types = symbols._collect_instance_types()
        return symbols

    def _collect_instance_types(self) -> Dict[str, str]:
        class_names = set(self.classes)
        found: Dict[str, str] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                class_name = _call_class_name(node.value, class_names)
                if not class_name:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        found[target.id] = class_name
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                class_name = _annotation_class_name(node.annotation, class_names)
                if class_name:
                    found[node.target.id] = class_name
                elif isinstance(node.value, ast.Call):
                    class_name = _call_class_name(node.value, class_names)
                    if class_name:
                        found[node.target.id] = class_name
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for argument in node.args.args:
                    class_name = _annotation_class_name(argument.annotation, class_names)
                    if class_name:
                        found[argument.arg] = class_name
        return found

    def matches_file(self, requested: str) -> bool:
        requested = _norm_path(requested)
        if not requested:
            return True
        return requested == self.file_name or _path_basename(requested) == _path_basename(self.file_name)

    def source_class_instances(self, source_class: str) -> set[str]:
        known = {
            name
            for name, class_name in self.instance_types.items()
            if class_name == source_class
        }
        return known


def _call_class_name(call: ast.Call, class_names: set[str]) -> str:
    if isinstance(call.func, ast.Name) and call.func.id in class_names:
        return call.func.id
    if isinstance(call.func, ast.Attribute) and call.func.attr in class_names:
        return call.func.attr
    return ""


class MoveMethodPlanResolver:
    """Resolve and validate Move Method plan targets from repository source."""

    def __init__(self, source_files: Sequence[Dict[str, Any]] | None = None) -> None:
        self.files: List[_PythonFileSymbols] = []
        for entry in source_files or []:
            if not isinstance(entry, dict):
                continue
            language = str(entry.get("language") or "").lower().strip()
            file_name = _norm_path(
                entry.get("file_name")
                or entry.get("file_path")
                or entry.get("relative_path")
                or entry.get("file")
                or entry.get("path")
            )
            if language and language not in _PYTHON_LANGUAGES and not file_name.endswith(".py"):
                continue
            if not file_name.endswith(".py"):
                continue
            source_code = entry.get("source_code")
            if not isinstance(source_code, str):
                source_code = entry.get("content") if isinstance(entry.get("content"), str) else ""
            if not source_code:
                continue
            symbols = _PythonFileSymbols.from_source(file_name, source_code)
            if symbols is not None:
                self.files.append(symbols)

    @classmethod
    def from_quality_report(cls, report: Any) -> "MoveMethodPlanResolver":
        source_files = getattr(report, "source_files", None)
        if not isinstance(source_files, list):
            source_files = []
        return cls(source_files)

    @property
    def available(self) -> bool:
        return bool(self.files)

    def describe(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "python_files": len(self.files),
            "classes": sum(len(item.classes) for item in self.files),
            "module_functions": sum(len(item.module_functions) for item in self.files),
        }

    def resolve(self, smell: CodeSmell, candidate: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """Return a success dict only for a valid class-to-class move."""

        location = smell.location or {}
        source_file = _norm_path(
            location.get("file")
            or location.get("source_file")
            or (candidate or {}).get("source_file")
        )
        language = str(location.get("language") or "").lower().strip()
        if language and language not in _PYTHON_LANGUAGES and not source_file.endswith(".py"):
            return self._reject(
                "not_applicable",
                "MOVE_METHOD_REQUIRES_PYTHON_SOURCE",
                final_decision="NOT_RECOMMENDED",
            )

        if not self.files:
            return self._reject(
                "review_required",
                "MOVE_METHOD_REQUIRES_REPOSITORY_AST",
                final_decision="REVIEW_REQUIRED",
            )

        requested_method = _norm_symbol(
            location.get("method")
            or location.get("source_method")
            or location.get("entity")
            or (candidate or {}).get("method")
            or (candidate or {}).get("source_method")
        )
        requested_source = _norm_symbol(
            location.get("source_class")
            or location.get("class")
            or (candidate or {}).get("source_class")
        )
        requested_destination = _norm_symbol(
            location.get("destination_class")
            or (candidate or {}).get("destination_class")
            or self._destination_hint_from_details(smell.details or "")
        )
        source_line = (
            _safe_int(location.get("source_line"))
            or _lines_start(location.get("lines"))
            or _safe_int(getattr(smell, "line", None))
        )

        candidate_files = [item for item in self.files if item.matches_file(source_file)]
        if source_file and not candidate_files:
            return self._reject(
                "review_required",
                "SOURCE_FILE_NOT_FOUND",
                final_decision="REVIEW_REQUIRED",
                source_file=source_file,
            )
        if not candidate_files:
            candidate_files = list(self.files)

        module_guard = self._module_function_guard(
            candidate_files,
            requested_method=requested_method,
            source_line=source_line,
            requested_source=requested_source,
            requested_destination=requested_destination,
        )
        if module_guard:
            return module_guard

        class_names_by_file = {
            item.file_name: set(item.classes)
            for item in candidate_files
        }
        all_candidate_classes = set().union(*class_names_by_file.values()) if candidate_files else set()
        requested_source_placeholder = _is_placeholder_symbol(requested_source, source_file)
        requested_method_placeholder = _is_placeholder_symbol(requested_method, source_file)
        synthetic_destination = _is_synthetic_target(requested_destination, all_candidate_classes)

        if requested_source and not requested_source_placeholder and requested_source not in all_candidate_classes:
            return self._reject(
                "review_required",
                "SOURCE_CLASS_NOT_FOUND",
                final_decision="REVIEW_REQUIRED",
                requested_source_class=requested_source,
            )

        if (
            requested_destination
            and not synthetic_destination
            and requested_destination not in all_candidate_classes
        ):
            return self._reject(
                "review_required",
                "NO_VALID_DESTINATION_CLASS",
                final_decision="REVIEW_REQUIRED",
                requested_destination_class=requested_destination,
            )

        candidates: List[Dict[str, Any]] = []
        blocked_reasons: List[str] = []
        for file_symbols in candidate_files:
            file_candidates, file_blocked = self._candidates_in_file(
                file_symbols,
                requested_method=requested_method,
                requested_source="" if requested_source_placeholder else requested_source,
                requested_destination="" if synthetic_destination else requested_destination,
                requested_method_placeholder=requested_method_placeholder,
                source_line=source_line,
            )
            candidates.extend(file_candidates)
            blocked_reasons.extend(file_blocked)

        if not candidates:
            reason = "NO_VALID_DESTINATION_CLASS"
            if "SOURCE_METHOD_NOT_FOUND" in blocked_reasons:
                reason = "SOURCE_METHOD_NOT_FOUND"
            elif "SOURCE_AND_DESTINATION_CLASS_MATCH" in blocked_reasons:
                reason = "SOURCE_AND_DESTINATION_CLASS_MATCH"
            elif "CALL_SITE_REWRITE_FAILED" in blocked_reasons:
                reason = "CALL_SITE_REWRITE_FAILED"
            elif "AMBIGUOUS_DESTINATION_CLASS" in blocked_reasons:
                reason = "AMBIGUOUS_MOVE_METHOD_TARGET"
            elif synthetic_destination:
                reason = "NO_VALID_DESTINATION_CLASS"
            return self._reject(
                "review_required",
                reason,
                final_decision="REVIEW_REQUIRED",
                requested_method=requested_method,
                requested_source_class=requested_source,
                requested_destination_class=requested_destination,
                blocked_reasons=sorted(set(blocked_reasons)),
            )

        selected = self._select_candidate(
            candidates,
            requested_method=requested_method,
            requested_source="" if requested_source_placeholder else requested_source,
            requested_destination="" if synthetic_destination else requested_destination,
            source_line=source_line,
        )
        if selected is None:
            return self._reject(
                "review_required",
                "AMBIGUOUS_MOVE_METHOD_TARGET",
                final_decision="REVIEW_REQUIRED",
                candidate_count=len(candidates),
                requested_method=requested_method,
                requested_source_class=requested_source,
                requested_destination_class=requested_destination,
            )

        return {
            "status": "success",
            "final_decision": "RECOMMENDED",
            "reason": "VALID_CLASS_TO_CLASS_MOVE_METHOD",
            **selected,
            "requested_method": requested_method,
            "requested_source_class": requested_source,
            "requested_destination_class": requested_destination,
            "synthetic_destination_rejected": bool(synthetic_destination),
        }

    def validate_plan(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Re-check the final plan parameters before RDP emits a step."""

        smell = CodeSmell(
            id=str(parameters.get("smell_id") or "move_method_plan_validation"),
            type="Feature Envy",
            location={
                "file": parameters.get("source_file"),
                "language": "python",
                "class": parameters.get("source_class"),
                "method": parameters.get("source_method") or parameters.get("method"),
                "destination_class": parameters.get("destination_class"),
                "source_line": parameters.get("source_line"),
            },
            metrics={},
            severity="medium",
            details="",
        )
        resolved = self.resolve(smell, candidate=parameters)
        if resolved.get("status") != "success":
            return resolved

        expected = {
            "source_class": _norm_symbol(parameters.get("source_class")),
            "method": _norm_symbol(parameters.get("source_method") or parameters.get("method")),
            "destination_class": _norm_symbol(parameters.get("destination_class")),
        }
        for key, value in expected.items():
            if resolved.get(key) != value:
                return self._reject(
                    "review_required",
                    "MOVE_METHOD_PLAN_TARGET_MISMATCH",
                    final_decision="REVIEW_REQUIRED",
                    expected=expected,
                    resolved={
                        "source_class": resolved.get("source_class"),
                        "method": resolved.get("method"),
                        "destination_class": resolved.get("destination_class"),
                    },
                )
        return resolved

    @staticmethod
    def _destination_hint_from_details(details: str) -> str:
        text = str(details or "")
        if not text:
            return ""
        quoted = re.search(
            r"(?:destination|target|to|class)\s+(?:class\s+)?['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]",
            text,
            re.IGNORECASE,
        )
        if quoted:
            return quoted.group(1)
        unquoted = re.search(
            r"(?:destination|target|to)\s+(?:class\s+)?([A-Za-z_][A-Za-z0-9_]*)",
            text,
            re.IGNORECASE,
        )
        return unquoted.group(1) if unquoted else ""

    def _module_function_guard(
        self,
        files: Sequence[_PythonFileSymbols],
        *,
        requested_method: str,
        source_line: Optional[int],
        requested_source: str,
        requested_destination: str,
    ) -> Dict[str, Any] | None:
        for file_symbols in files:
            if requested_method and requested_method in file_symbols.module_functions:
                function = file_symbols.module_functions[requested_method]
                return self._reject(
                    "not_applicable",
                    "MODULE_LEVEL_FUNCTION_IS_NOT_MOVE_METHOD_TARGET",
                    final_decision="NOT_RECOMMENDED",
                    source_file=file_symbols.file_name,
                    method=requested_method,
                    lineno=getattr(function, "lineno", None),
                    requested_source_class=requested_source,
                    requested_destination_class=requested_destination,
                )
            if source_line is None:
                continue
            matches = [
                function
                for function in file_symbols.module_functions.values()
                if _node_contains_line(function, source_line)
            ]
            if len(matches) == 1:
                function = matches[0]
                return self._reject(
                    "not_applicable",
                    "MODULE_LEVEL_FUNCTION_IS_NOT_MOVE_METHOD_TARGET",
                    final_decision="NOT_RECOMMENDED",
                    source_file=file_symbols.file_name,
                    method=getattr(function, "name", requested_method),
                    lineno=getattr(function, "lineno", None),
                    requested_method=requested_method,
                    requested_source_class=requested_source,
                    requested_destination_class=requested_destination,
                )
        return None

    def _candidates_in_file(
        self,
        file_symbols: _PythonFileSymbols,
        *,
        requested_method: str,
        requested_source: str,
        requested_destination: str,
        requested_method_placeholder: bool,
        source_line: Optional[int],
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        class_names = set(file_symbols.classes)
        if len(class_names) < 2:
            return [], ["NO_VALID_DESTINATION_CLASS"]

        candidates: List[Dict[str, Any]] = []
        blocked: List[str] = []
        source_method_seen = False
        for owner_name, owner_node in file_symbols.classes.items():
            if requested_source and owner_name != requested_source:
                continue

            for method in owner_node.body:
                if not isinstance(method, ast.FunctionDef):
                    continue
                if requested_method and not requested_method_placeholder and method.name == requested_method:
                    source_method_seen = True
                elif requested_method and not requested_method_placeholder:
                    continue

                if source_line is not None and requested_method_placeholder:
                    if not _node_contains_line(method, source_line):
                        continue
                    source_method_seen = True

                if not self._method_shape_is_movable(method):
                    blocked.append("SOURCE_METHOD_NOT_MOVABLE")
                    continue

                analysis = self._feature_envy_analysis(method)
                if analysis.get("status") != "success":
                    blocked.append(str(analysis.get("reason") or "NO_FEATURE_ENVY_EVIDENCE"))
                    continue

                destination_parameter = str(analysis["destination_parameter"])
                destination_candidates = self._destination_candidates(
                    file_symbols,
                    owner_name=owner_name,
                    method=method,
                    destination_parameter=destination_parameter,
                    requested_destination=requested_destination,
                )

                if requested_destination and requested_destination == owner_name:
                    blocked.append("SOURCE_AND_DESTINATION_CLASS_MATCH")
                    continue
                destination_candidates.discard(owner_name)
                if requested_destination:
                    destination_candidates = {
                        item for item in destination_candidates if item == requested_destination
                    }

                if not destination_candidates:
                    blocked.append("NO_VALID_DESTINATION_CLASS")
                    continue
                if len(destination_candidates) > 1:
                    blocked.append("AMBIGUOUS_DESTINATION_CLASS")
                    continue

                destination_class = next(iter(destination_candidates))
                call_site_status = self._validate_call_sites(
                    file_symbols,
                    source_method=method,
                    source_class=owner_name,
                    method_name=method.name,
                    destination_parameter=destination_parameter,
                )
                if call_site_status.get("status") != "success":
                    blocked.append("CALL_SITE_REWRITE_FAILED")
                    continue

                candidates.append({
                    "source_file": file_symbols.file_name,
                    "source_class": owner_name,
                    "method": method.name,
                    "source_method": method.name,
                    "destination_class": destination_class,
                    "destination_parameter": destination_parameter,
                    "feature_envy_accesses": int(analysis["feature_envy_accesses"]),
                    "source_self_accesses": int(analysis["source_self_accesses"]),
                    "lineno": int(getattr(method, "lineno", 0) or 0),
                    "end_lineno": int(getattr(method, "end_lineno", getattr(method, "lineno", 0)) or 0),
                    "call_sites_checked": int(call_site_status.get("call_sites_checked", 0)),
                    "call_sites_rewritable": True,
                    "destination_evidence": sorted(analysis.get("destination_evidence", [])),
                })

        if requested_method and not source_method_seen:
            blocked.append("SOURCE_METHOD_NOT_FOUND")
        return candidates, blocked

    @staticmethod
    def _method_shape_is_movable(method: ast.FunctionDef) -> bool:
        if method.name == "__init__":
            return False
        if method.decorator_list or method.args.posonlyargs or method.args.vararg:
            return False
        if len(method.args.args) < 2:
            return False
        return method.args.args[0].arg == "self"

    @staticmethod
    def _feature_envy_analysis(method: ast.FunctionDef) -> Dict[str, Any]:
        parameter_names = [argument.arg for argument in method.args.args[1:]]
        attribute_counts = {name: 0 for name in parameter_names}
        self_accesses = 0
        reassigned: set[str] = set()
        nested_scope = False

        for node in ast.walk(method):
            if node is not method and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
            ):
                nested_scope = True
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id in attribute_counts:
                    attribute_counts[node.value.id] += 1
                elif node.value.id == "self":
                    self_accesses += 1
            if isinstance(node, ast.Name) and node.id in attribute_counts:
                if isinstance(node.ctx, (ast.Store, ast.Del)):
                    reassigned.add(node.id)

        if nested_scope:
            return {"status": "review_required", "reason": "NESTED_SCOPE_UNSUPPORTED"}
        highest = max(attribute_counts.values(), default=0)
        envied = [
            name
            for name, count in attribute_counts.items()
            if count == highest and count > 0
        ]
        if len(envied) != 1 or highest < 2 or highest <= self_accesses:
            return {"status": "review_required", "reason": "NO_FEATURE_ENVY_EVIDENCE"}
        if envied[0] in reassigned:
            return {"status": "review_required", "reason": "DESTINATION_PARAMETER_REASSIGNED"}
        return {
            "status": "success",
            "destination_parameter": envied[0],
            "feature_envy_accesses": highest,
            "source_self_accesses": self_accesses,
            "destination_evidence": ["external_attribute_access"],
        }

    def _destination_candidates(
        self,
        file_symbols: _PythonFileSymbols,
        *,
        owner_name: str,
        method: ast.FunctionDef,
        destination_parameter: str,
        requested_destination: str,
    ) -> set[str]:
        class_names = set(file_symbols.classes)
        candidates: set[str] = set()
        argument = next(
            (arg for arg in method.args.args[1:] if arg.arg == destination_parameter),
            None,
        )
        if argument is not None:
            annotation_target = _annotation_class_name(argument.annotation, class_names)
            if annotation_target and annotation_target != owner_name:
                candidates.add(annotation_target)

        parameter_key = _symbol_key(destination_parameter)
        for class_name in class_names:
            if class_name == owner_name:
                continue
            class_key = _symbol_key(class_name)
            if parameter_key == class_key or parameter_key.rstrip("s") == class_key.rstrip("s"):
                candidates.add(class_name)

        candidates.update(
            self._call_destination_classes(
                file_symbols,
                method_name=method.name,
                destination_parameter=destination_parameter,
            )
        )

        if requested_destination and requested_destination in candidates:
            return {requested_destination}
        return candidates

    @staticmethod
    def _call_destination_classes(
        file_symbols: _PythonFileSymbols,
        *,
        method_name: str,
        destination_parameter: str,
    ) -> set[str]:
        class_names = set(file_symbols.classes)
        inferred: set[str] = set()
        for node in ast.walk(file_symbols.tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == method_name
            ):
                continue
            expression: ast.AST | None = None
            if node.args:
                expression = node.args[0]
            else:
                keyword = next((item for item in node.keywords if item.arg == destination_parameter), None)
                if keyword is not None:
                    expression = keyword.value
            if isinstance(expression, ast.Call):
                class_name = _call_class_name(expression, class_names)
                if class_name:
                    inferred.add(class_name)
            elif isinstance(expression, ast.Name):
                class_name = file_symbols.instance_types.get(expression.id)
                if class_name:
                    inferred.add(class_name)
        return inferred

    @staticmethod
    def _validate_call_sites(
        file_symbols: _PythonFileSymbols,
        *,
        source_method: ast.FunctionDef,
        source_class: str,
        method_name: str,
        destination_parameter: str,
    ) -> Dict[str, Any]:
        checked = 0
        known_source_instances = file_symbols.source_class_instances(source_class)
        for attribute in ast.walk(file_symbols.tree):
            if not isinstance(attribute, ast.Attribute) or attribute.attr != method_name:
                continue
            if _is_descendant(attribute, source_method, file_symbols.parents):
                continue
            parent = file_symbols.parents.get(attribute)
            if not isinstance(parent, ast.Call) or parent.func is not attribute:
                return {"status": "review_required", "reason": "METHOD_REFERENCE_UNSUPPORTED"}

        for node in ast.walk(file_symbols.tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != method_name or _is_descendant(node, source_method, file_symbols.parents):
                continue
            if not _receiver_is_source_instance(
                node.func.value,
                known_instances=known_source_instances,
                source_class=source_class,
                parents=file_symbols.parents,
            ):
                return {"status": "review_required", "reason": "UNRESOLVED_DIRECT_CALL_SITE"}
            destination_expression = _call_destination_argument(node, destination_parameter)
            if destination_expression is None:
                return {"status": "review_required", "reason": "DIRECT_CALL_SITE_CANNOT_BE_REWRITTEN"}
            checked += 1
        return {"status": "success", "call_sites_checked": checked}

    @staticmethod
    def _select_candidate(
        candidates: Sequence[Dict[str, Any]],
        *,
        requested_method: str,
        requested_source: str,
        requested_destination: str,
        source_line: Optional[int],
    ) -> Dict[str, Any] | None:
        def only(items: Sequence[Dict[str, Any]]) -> Dict[str, Any] | None:
            return items[0] if len(items) == 1 else None

        if requested_method and requested_source and requested_destination:
            exact = [
                item for item in candidates
                if item["method"] == requested_method
                and item["source_class"] == requested_source
                and item["destination_class"] == requested_destination
            ]
            selected = only(exact)
            if selected:
                return selected

        if source_line is not None:
            line_matches = [
                item for item in candidates
                if item["lineno"] <= source_line <= item["end_lineno"]
            ]
            selected = only(line_matches)
            if selected:
                return selected

        if requested_method and requested_source:
            source_method = [
                item for item in candidates
                if item["method"] == requested_method
                and item["source_class"] == requested_source
            ]
            selected = only(source_method)
            if selected:
                return selected

        if requested_method:
            method_matches = [item for item in candidates if item["method"] == requested_method]
            selected = only(method_matches)
            if selected:
                return selected

        if requested_source:
            source_matches = [item for item in candidates if item["source_class"] == requested_source]
            selected = only(source_matches)
            if selected:
                return selected

        return only(candidates)

    @staticmethod
    def _reject(status: str, reason: str, **extra: Any) -> Dict[str, Any]:
        return {
            "status": status,
            "reason": reason,
            **extra,
        }


def _node_contains_line(node: ast.AST, line: int) -> bool:
    start = int(getattr(node, "lineno", 0) or 0)
    end = int(getattr(node, "end_lineno", start) or start)
    return start <= line <= end


def _is_descendant(node: ast.AST, ancestor: ast.AST, parents: Dict[ast.AST, ast.AST]) -> bool:
    current: ast.AST | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = parents.get(current)
    return False


def _receiver_is_source_instance(
    receiver: ast.AST,
    *,
    known_instances: set[str],
    source_class: str,
    parents: Dict[ast.AST, ast.AST],
) -> bool:
    if isinstance(receiver, ast.Name):
        if receiver.id in known_instances:
            return True
        if receiver.id != "self":
            return False
        owner = parents.get(receiver)
        while owner is not None and not isinstance(owner, ast.ClassDef):
            owner = parents.get(owner)
        return isinstance(owner, ast.ClassDef) and owner.name == source_class
    return (
        isinstance(receiver, ast.Call)
        and isinstance(receiver.func, ast.Name)
        and receiver.func.id == source_class
    )


def _call_destination_argument(call: ast.Call, destination_parameter: str) -> ast.AST | None:
    if call.args:
        if isinstance(call.args[0], ast.Starred):
            return None
        return call.args[0]
    destination_keywords = [
        keyword for keyword in call.keywords if keyword.arg == destination_parameter
    ]
    if len(destination_keywords) != 1:
        return None
    return destination_keywords[0].value

