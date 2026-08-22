"""Adapter that maps RDP planner output into SCTVA request payloads."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..constants import (
    ACTION_ENCAPSULATE_VARIABLE,
    ACTION_EXTRACT_C_COMPONENT,
    ACTION_EXTRACT_CLASS,
    ACTION_EXTRACT_JAVA_CLASS,
    ACTION_EXTRACT_METHOD,
    ACTION_EXTRACT_PYTHON_CLASS,
    ACTION_FAULT_INJECTION,
    ACTION_INTRODUCE_JAVA_PARAMETER_OBJECT,
    ACTION_INTRODUCE_PARAMETER_OBJECT,
    ACTION_INTRODUCE_PYTHON_PARAMETER_OBJECT,
    ACTION_NOOP,
    ACTION_NARROW_EXCEPTION_HANDLER,
    ACTION_REPLACE_UNSAFE_FUNCTION,
    EXTRACT_CLASS_ACTIONS,
)


class PlannerAdapterError(ValueError):
    """Raised when planner payload is malformed or unsupported."""


class PlannerAdapter:
    """Validates and normalizes planner output into SCTVA-compatible actions."""

    def normalize_plan(
        self,
        planner_output: Dict[str, Any],
        *,
        correlation_id: Optional[str] = None,
        preserve_step_count: bool = True,
    ) -> Dict[str, Any]:
        if not isinstance(planner_output, dict):
            raise PlannerAdapterError("Planner output must be an object.")

        planner_output = self._unwrap_planner_output(planner_output)

        plan_id = str(planner_output.get("plan_id", "")).strip()
        if not plan_id:
            raise PlannerAdapterError("Planner output missing required field 'plan_id'.")

        steps = planner_output.get("steps")
        if not isinstance(steps, list):
            raise PlannerAdapterError("Planner output field 'steps' must be a list.")

        actions: List[Dict[str, Any]] = []
        warnings: List[str] = []
        malformed_steps: List[int] = []

        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                malformed_steps.append(idx)
                if preserve_step_count:
                    msg = f"Step {idx} is malformed and was mapped to noop."
                    warnings.append(msg)
                    actions.append(
                        {
                            "action_type": ACTION_NOOP,
                            "parameters": {
                                "reason": "malformed_step",
                                "step_index": idx,
                            },
                            "source_step_id": None,
                            "source_refactoring": None,
                            "warnings": [msg],
                        }
                    )
                continue

            try:
                action = self._map_step(step)
            except PlannerAdapterError as exc:
                malformed_steps.append(idx)
                warnings.append(f"Step {idx}: {exc}")
                if preserve_step_count:
                    msg = f"Step {idx} is malformed and was mapped to noop."
                    actions.append(
                        {
                            "action_type": ACTION_NOOP,
                            "parameters": {
                                "reason": "malformed_step",
                                "step_index": idx,
                            },
                            "source_step_id": step.get("step_id"),
                            "source_refactoring": step.get("refactoring"),
                            "warnings": [msg, str(exc)],
                        }
                    )
                continue

            if action is None:
                msg = (
                    f"Step {idx} unsupported refactoring "
                    f"'{step.get('refactoring', 'unknown')}', mapped to noop."
                )
                warnings.append(msg)
                if preserve_step_count:
                    actions.append(
                        {
                            "action_type": ACTION_NOOP,
                            "parameters": {
                                "reason": "unsupported_refactoring",
                                "refactoring": step.get("refactoring"),
                            },
                            "source_step_id": step.get("step_id"),
                            "source_refactoring": step.get("refactoring"),
                            "warnings": [msg],
                        }
                    )
            else:
                actions.append(action)

        if not actions:
            msg = "Planner payload produced zero executable actions; using noop action."
            warnings.append(msg)
            actions.append(
                {
                    "action_type": ACTION_NOOP,
                    "parameters": {
                        "reason": "empty_or_non_actionable_plan",
                    },
                    "source_step_id": None,
                    "source_refactoring": None,
                    "warnings": [msg],
                }
            )

        plan_level_source_file = self._source_file_from_plan(planner_output)
        if plan_level_source_file:
            for action in actions:
                params = action.setdefault("parameters", {})
                if "source_file" not in params:
                    params["source_file"] = plan_level_source_file

        metadata = {
            "source_agent": "rdp_agent",
            "source_plan_id": plan_id,
            "correlation_id": correlation_id,
            "adapter_warnings": warnings,
            "malformed_steps": malformed_steps,
            "planner_metadata": planner_output.get("metadata", {}),
        }

        return {
            "plan_id": plan_id,
            "actions": actions,
            "behavior_tests": planner_output.get("behavior_tests", []),
            "metadata": metadata,
        }

    def build_request_from_rdp(
        self,
        *,
        request_id: str,
        language: str,
        source_code: str,
        planner_output: Dict[str, Any],
        execution_options: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_plan = self.normalize_plan(
            planner_output,
            correlation_id=correlation_id,
        )

        return {
            "request_id": request_id,
            "language": language,
            "source_code": source_code,
            "refactoring_plan": normalized_plan,
            "execution_options": execution_options
            or {
                "strict_mode": True,
                "enable_behavior_tests": True,
                "timeout_seconds": 10,
                "require_compilation": language.lower() in {"java", "c"},
            },
        }

    def _map_step(self, step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        refactoring = str(step.get("refactoring", "")).strip()

        if not refactoring:
            raise PlannerAdapterError("missing 'refactoring' in step")

        ref_key = refactoring.lower()
        params = step.get("parameters") or {}
        target = step.get("target") or {}
        smell_name = str(step.get("smell") or step.get("smell_type") or "").strip()
        smell_key = " ".join(smell_name.lower().replace("_", " ").split())

        if not isinstance(params, dict):
            raise PlannerAdapterError("'parameters' must be an object when provided")

        if not isinstance(target, dict):
            raise PlannerAdapterError("'target' must be an object when provided")

        action: Optional[Dict[str, Any]] = None

        rename_aliases = {
            "rename function",
            "rename method",
            "rename variable",
            "rename class",
            "rename parameter",
            "rename field",
            "rename attribute",
        }

        # IMPORTANT FIX:
        # Accept any custom fault-injection refactoring name.
        # Examples:
        # - Fault Injection
        # - Fault Injection - Change Return Value
        # - Fault Injection - Numeric Range Violation
        # - Fault Injection - Change Logic
        #
        # Before this fix, "Fault Injection - Numeric Range Violation"
        # was mapped to noop, so no wrong code change happened.
        if ref_key.startswith("fault injection"):
            original_logic = params.get("original_logic") or params.get("old_logic")

            if "faulty_logic" in params:
                faulty_logic = params.get("faulty_logic")
            else:
                faulty_logic = params.get("new_logic")

            if not original_logic or faulty_logic is None:
                raise PlannerAdapterError(
                    "fault injection mapping requires original_logic and faulty_logic"
                )

            action = {
                "action_type": ACTION_FAULT_INJECTION,
                "parameters": {
                    "original_logic": str(original_logic),
                    "faulty_logic": str(faulty_logic),
                    "change_type": params.get("change_type"),
                    "purpose": params.get("purpose"),
                    "target_class": target.get("class"),
                    "target_method": target.get("method"),
                },
            }

        elif ref_key in rename_aliases:
            old_name = params.get("old_name") or target.get("method") or target.get("class")
            new_name = params.get("new_name") or params.get("renamed_to")

            if not old_name or not new_name:
                raise PlannerAdapterError("rename step requires old/new names")

            action = {
                "action_type": "rename_symbol",
                "parameters": {
                    "old_name": str(old_name),
                    "new_name": self._safe_identifier(str(new_name)),
                },
            }

        elif ref_key == "extract method":
            target_method = (
                target.get("method")
                or target.get("function")
                or params.get("method")
                or params.get("method_name")
                or params.get("function")
                or params.get("function_name")
                or params.get("source_method")
            )

            if not target_method:
                raise PlannerAdapterError(
                    "extract method mapping requires a semantic method/function target"
                )

            start_line, end_line = self._source_range_from_step(
                step,
                params=params,
                target=target,
            )
            new_name = (
                params.get("new_method_name")
                or params.get("extracted_method_name")
                or params.get("new_function_name")
                or params.get("extracted_function_name")
                or f"{target_method}Core"
            )

            action = {
                "action_type": ACTION_EXTRACT_METHOD,
                "parameters": {
                    "method": str(target_method),
                    "new_method_name": self._safe_identifier(str(new_name)),
                    "start_line": start_line,
                    "end_line": end_line,
                    "source_class": (
                        target.get("class")
                        or params.get("source_class")
                        or params.get("class_name")
                        or params.get("module_name")
                    ),
                    "method_signature": (
                        target.get("signature")
                        or params.get("method_signature")
                        or params.get("function_signature")
                        or params.get("signature")
                    ),
                    "smell": step.get("smell") or step.get("smell_type") or "Long Method",
                },
            }

        elif ref_key in {
            "extract class",
            "extract java class",
            "extract python class",
            "extract c component",
            "extract component",
        }:
            source_file = self._source_file_from_step(step, params=params, target=target)
            explicit_source_class = (
                params.get("source_class")
                or params.get("class_name")
                or target.get("class")
                or params.get("class")
                or params.get("module_name")
            )
            source_class = explicit_source_class
            explicit_new_class_name = (
                params.get("new_class_name")
                or params.get("extracted_class_name")
                or params.get("destination_class")
                or params.get("new_component_name")
            )
            new_class_name = explicit_new_class_name
            if not source_class and source_file:
                source_class = str(source_file).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if not source_class:
                raise PlannerAdapterError(
                    "extract class mapping requires a source class/module or source file"
                )
            if not new_class_name:
                new_class_name = f"{source_class}Helper"

            methods_to_extract = params.get("methods_to_extract") or params.get("functions_to_extract")
            fields_to_extract = params.get("fields_to_extract") or params.get("globals_to_extract")
            required_public_methods = params.get("required_public_methods")
            required_public_fields = params.get("required_public_fields")
            destination_file = (
                params.get("destination_file")
                or params.get("extracted_file")
                or params.get("output_file")
                or "same_file"
            )

            extract_action_type = self._extract_class_action_type(
                source_file=source_file,
                refactoring_key=ref_key,
            )
            action = {
                "action_type": extract_action_type,
                "parameters": {
                    "source_class": str(source_class),
                    "new_class_name": self._safe_identifier(str(new_class_name)),
                    "source_class_origin": (
                        "rdp_explicit" if explicit_source_class else "file_stem_fallback"
                    ),
                    "new_class_name_origin": (
                        "rdp_explicit" if explicit_new_class_name else "generated"
                    ),
                    "methods_to_extract": (
                        [str(item) for item in methods_to_extract]
                        if isinstance(methods_to_extract, list)
                        else []
                    ),
                    "fields_to_extract": (
                        [str(item) for item in fields_to_extract]
                        if isinstance(fields_to_extract, list)
                        else []
                    ),
                    "required_public_methods": (
                        [str(item) for item in required_public_methods]
                        if isinstance(required_public_methods, list)
                        else []
                    ),
                    "required_public_fields": (
                        [str(item) for item in required_public_fields]
                        if isinstance(required_public_fields, list)
                        else []
                    ),
                    "preserve_public_api": bool(params.get("preserve_public_api", True)),
                    "delegation_strategy": str(params.get("delegation_strategy") or "wrapper"),
                    "target_file": str(destination_file),
                    "smell": step.get("smell") or step.get("smell_type") or "Large Class",
                },
            }

        elif ref_key == "move method":
            raise PlannerAdapterError(
                "move method requires coordinated edits to source and destination classes; "
                "SCTVA will not simulate it with a rename"
            )

        elif ref_key == "replace conditional with polymorphism":
            raise PlannerAdapterError(
                "replace conditional with polymorphism requires new strategy/subclass definitions; "
                "SCTVA will not simulate it with a rename"
            )

        elif ref_key == "introduce parameter object":
            method = (
                target.get("method")
                or target.get("function")
                or params.get("method")
                or params.get("method_name")
                or params.get("function")
                or params.get("function_name")
            )
            object_name = (
                params.get("parameter_object_name")
                or params.get("new_class_name")
                or params.get("parameter_class_name")
            )
            if not method or not object_name:
                raise PlannerAdapterError(
                    "introduce parameter object requires method and parameter_object_name"
                )
            source_file = self._source_file_from_step(step, params=params, target=target)
            normalized_file = source_file.replace("\\", "/").lower()
            action_type = ACTION_INTRODUCE_PARAMETER_OBJECT
            if normalized_file.endswith(".java"):
                action_type = ACTION_INTRODUCE_JAVA_PARAMETER_OBJECT
            elif normalized_file.endswith(".py"):
                action_type = ACTION_INTRODUCE_PYTHON_PARAMETER_OBJECT
            action = {
                "action_type": action_type,
                "parameters": {
                    "method": str(method),
                    "parameter_object_name": self._safe_identifier(str(object_name)),
                    "parameter_name": self._safe_identifier(str(params.get("parameter_name") or "params")),
                    "source_class": str(
                        target.get("class")
                        or params.get("source_class")
                        or params.get("class_name")
                        or ""
                    ),
                    "source_class_origin": (
                        "file_stem_fallback"
                        if source_file
                        and str(
                            target.get("class")
                            or params.get("source_class")
                            or params.get("class_name")
                            or ""
                        ).strip().lower()
                        == str(source_file).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
                        else "rdp_explicit"
                    ),
                    "smell": step.get("smell") or step.get("smell_type") or "Long Parameter List",
                },
            }

        elif ref_key in {
            "hide delegate",
            "replace data value with object",
            "inline class",
            "collapse hierarchy",
            "pull up method",
            "replace parameter with method call",
        }:
            raise PlannerAdapterError(
                f"{refactoring} requires semantic multi-location edits; "
                "SCTVA will not simulate it with a rename"
            )

        elif ref_key in {
            "extract constant",
            "replace magic number with symbolic constant",
        }:
            if "literal_value" not in params:
                raise PlannerAdapterError(
                    "extract_constant mapping requires parameters.literal_value"
                )

            action = {
                "action_type": "extract_constant",
                "parameters": {
                    "literal_value": params["literal_value"],
                    "constant_name": params.get("constant_name", "EXTRACTED_CONSTANT"),
                },
            }

        elif ref_key == "introduce constant":
            literal_value = params.get("literal_value") if "literal_value" in params else None
            literal_values = params.get("literal_values") if isinstance(params.get("literal_values"), list) else None
            hint = params.get("hint")

            if literal_value is None and not literal_values and not hint:
                raise PlannerAdapterError(
                    "introduce constant mapping requires literal_value, literal_values, or hint"
                )

            action = {
                "action_type": "introduce_constant",
                "parameters": {
                    "literal_value": literal_value,
                    "literal_values": literal_values,
                    "constant_name": params.get("constant_name", "EXTRACTED_CONSTANT"),
                    "hint": hint,
                    "source_file": params.get("source_file"),
                    "source_line": params.get("source_line"),
                    "target_class": target.get("class") or params.get("source_class"),
                    "target_method": target.get("method") or params.get("method"),
                },
            }

        elif ref_key == "remove dead code" and smell_key in {
            "bare except",
            "bareexcept",
            "exception overreach",
            "broad exception handling",
        }:
            # Some legacy RDP knowledge-base entries recommend Remove Dead
            # Code for exception smells. A live ``except``/``catch`` block is
            # not dead code; route it to the dedicated safe operation instead.
            source_line = self._source_line_from_step(step, params=params, target=target)
            is_bare_except = smell_key in {"bare except", "bareexcept"}
            action = {
                "action_type": ACTION_NARROW_EXCEPTION_HANDLER,
                "parameters": {
                    "source_line": source_line,
                    "method": target.get("method") or params.get("method"),
                    "class_name": target.get("class") or params.get("source_class"),
                    "source_file": target.get("file") or params.get("source_file"),
                    "original_exception_type": "" if is_bare_except else str(
                        params.get("original_exception_type") or "Exception"
                    ),
                    "target_exception_type": str(
                        params.get("target_exception_type")
                        or params.get("narrow_exception_type")
                        or ("Exception" if is_bare_except else "")
                    ),
                    "handler_name": str(
                        params.get("handler_name") or params.get("exception_variable") or ""
                    ),
                    "source_smell": smell_name,
                },
            }

        elif ref_key == "remove dead code":
            method = params.get("method") or target.get("method")
            source_line = self._source_line_from_step(step, params=params, target=target)
            if not method and source_line is None:
                raise PlannerAdapterError(
                    "remove dead code mapping requires parameters.method, target.method, or source line"
                )

            action = {
                "action_type": "remove_dead_code",
                "parameters": {
                    "method": str(method or ""),
                    "class_name": target.get("class") or params.get("source_class"),
                    "source_line": source_line,
                    "source_file": target.get("file") or params.get("source_file"),
                },
            }

        elif ref_key in {
            "refactor to narrow exceptions",
            "narrow exception handling",
            "narrow exception handler",
            "replace broad exception handling",
        }:
            source_line = self._source_line_from_step(step, params=params, target=target)
            original_type = params.get("original_exception_type") or params.get("exception_type") or "Exception"
            target_type = params.get("target_exception_type") or params.get("narrow_exception_type")
            if source_line is None and not target.get("method"):
                raise PlannerAdapterError(
                    "narrow_exception_handler mapping requires a source line or target method"
                )
            action = {
                "action_type": ACTION_NARROW_EXCEPTION_HANDLER,
                "parameters": {
                    "source_line": source_line,
                    "method": target.get("method") or params.get("method"),
                    "class_name": target.get("class") or params.get("source_class"),
                    "source_file": target.get("file") or params.get("source_file"),
                    "original_exception_type": str(original_type),
                    "target_exception_type": str(target_type or ""),
                    "handler_name": str(params.get("handler_name") or params.get("exception_variable") or ""),
                },
            }

        elif ref_key == "replace unsafe function":
            unsafe_function = params.get("unsafe_function") or target.get("method")
            safe_alternative = params.get("safe_alternative")
            if not unsafe_function or not safe_alternative:
                raise PlannerAdapterError(
                    "replace unsafe function mapping requires unsafe_function and safe_alternative"
                )

            action = {
                "action_type": ACTION_REPLACE_UNSAFE_FUNCTION,
                "parameters": {
                    "unsafe_function": str(unsafe_function),
                    "safe_alternative": str(safe_alternative),
                    "source_line": self._source_line_from_step(step, params=params, target=target),
                },
            }

        elif ref_key == "encapsulate variable":
            variable_name = params.get("variable_name") or target.get("variable")
            if not variable_name:
                raise PlannerAdapterError(
                    "encapsulate variable mapping requires parameters.variable_name"
                )

            action = {
                "action_type": ACTION_ENCAPSULATE_VARIABLE,
                "parameters": {
                    "variable_name": str(variable_name),
                    "getter_name": self._safe_identifier(
                        str(params.get("getter_name") or f"get_{variable_name}")
                    ),
                    "setter_name": self._safe_identifier(
                        str(params.get("setter_name") or f"set_{variable_name}")
                    ),
                },
            }

        elif ref_key in {
            "replace literal",
            "replace temp with query",
        }:
            if "old_literal" not in params or "new_literal" not in params:
                raise PlannerAdapterError(
                    "replace_literal mapping requires old_literal/new_literal"
                )

            action = {
                "action_type": "replace_literal",
                "parameters": {
                    "old_literal": params["old_literal"],
                    "new_literal": params["new_literal"],
                },
            }

        elif ref_key == "inject syntax error":
            action = {
                "action_type": "inject_syntax_error",
                "parameters": {},
            }

        if action:
            source_file = self._source_file_from_step(step, params=params, target=target)
            if (
                action.get("action_type") in EXTRACT_CLASS_ACTIONS
                and str(source_file).strip().lower() in {"same_file", "same-source-file", "same source file"}
            ):
                source_file = ""
            if source_file and "source_file" not in action["parameters"]:
                action["parameters"]["source_file"] = source_file
            source_line = self._source_line_from_step(step, params=params, target=target)
            if source_line is not None and "source_line" not in action["parameters"]:
                action["parameters"]["source_line"] = source_line
            action["source_step_id"] = step.get("step_id")
            action["source_refactoring"] = refactoring
            action["warnings"] = []

        return action

    @staticmethod
    def _extract_class_action_type(*, source_file: str, refactoring_key: str) -> str:
        """Map an Extract Class plan to a language-specific SCTVA operation."""

        if refactoring_key == "extract java class":
            return ACTION_EXTRACT_JAVA_CLASS
        if refactoring_key == "extract python class":
            return ACTION_EXTRACT_PYTHON_CLASS
        if refactoring_key in {"extract c component", "extract component"}:
            return ACTION_EXTRACT_C_COMPONENT

        normalized = str(source_file or "").replace("\\", "/").lower()
        if normalized.endswith(".java"):
            return ACTION_EXTRACT_JAVA_CLASS
        if normalized.endswith(".py"):
            return ACTION_EXTRACT_PYTHON_CLASS
        if normalized.endswith((".c", ".h")):
            return ACTION_EXTRACT_C_COMPONENT
        return ACTION_EXTRACT_CLASS

    @classmethod
    def _unwrap_planner_output(cls, planner_output: Dict[str, Any]) -> Dict[str, Any]:
        for key in ("plan", "refactoring_plan", "rdp_sample", "generatedPlan", "latestPlan"):
            wrapped = planner_output.get(key)
            if isinstance(wrapped, dict):
                return cls._unwrap_planner_output(wrapped)

        data = planner_output.get("data")
        if isinstance(data, dict) and isinstance(data.get("plan"), dict):
            return cls._unwrap_planner_output(data["plan"])

        result = planner_output.get("result")
        if isinstance(result, dict) and isinstance(result.get("plan"), dict):
            return cls._unwrap_planner_output(result["plan"])

        return planner_output

    @staticmethod
    def _source_file_from_plan(planner_output: Dict[str, Any]) -> str:
        target = planner_output.get("target")
        if isinstance(target, str) and target.strip():
            return target.strip()
        if not isinstance(target, dict):
            target = {}
        return PlannerAdapter._source_file_from_sources(planner_output, target)

    @staticmethod
    def _source_file_from_step(
        step: Dict[str, Any],
        *,
        params: Dict[str, Any],
        target: Dict[str, Any],
    ) -> str:
        location = step.get("location") or {}
        if not isinstance(location, dict):
            location = {}

        return PlannerAdapter._source_file_from_sources(params, target, location, step)

    @staticmethod
    def _source_file_from_sources(*sources: Dict[str, Any]) -> str:
        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in (
                "source_file",
                "sourceFile",
                "target_file",
                "targetFile",
                "file",
                "file_name",
                "fileName",
                "file_path",
                "filePath",
                "relative_path",
                "relativePath",
            ):
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        return ""

    @staticmethod
    def _source_line_from_step(
        step: Dict[str, Any],
        *,
        params: Dict[str, Any],
        target: Dict[str, Any],
    ) -> Optional[int]:
        location = step.get("location") or {}
        if not isinstance(location, dict):
            location = {}

        for source in (params, target, location, step):
            if not isinstance(source, dict):
                continue

            for key in ("source_line", "sourceLine", "line", "start_line", "startLine"):
                value = source.get(key)
                if isinstance(value, (int, float)):
                    return int(value)
                if isinstance(value, str) and value.strip().isdigit():
                    return int(value.strip())

            for key in ("source_lines", "sourceLines", "lines"):
                values = source.get(key)
                if not isinstance(values, list) or not values:
                    continue
                first = values[0]
                if isinstance(first, (int, float)):
                    return int(first)
                if isinstance(first, str) and first.strip().isdigit():
                    return int(first.strip())

        return None

    @staticmethod
    def _source_range_from_step(
        step: Dict[str, Any],
        *,
        params: Dict[str, Any],
        target: Dict[str, Any],
    ) -> tuple[Optional[int], Optional[int]]:
        location = step.get("location") or {}
        if not isinstance(location, dict):
            location = {}

        start_keys = ("start_line", "startLine", "source_line", "sourceLine", "line")
        end_keys = ("end_line", "endLine", "target_line", "targetLine")

        def as_int(value: Any) -> Optional[int]:
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
            return None

        for source in (params, target, location, step):
            if not isinstance(source, dict):
                continue

            start = next((as_int(source.get(key)) for key in start_keys if as_int(source.get(key)) is not None), None)
            end = next((as_int(source.get(key)) for key in end_keys if as_int(source.get(key)) is not None), None)
            if start is not None and end is not None:
                return (min(start, end), max(start, end))

            for key in ("source_lines", "sourceLines", "lines", "line_range", "lineRange"):
                values = source.get(key)
                if isinstance(values, list) and values:
                    parsed = [as_int(value) for value in values]
                    parsed = [value for value in parsed if value is not None]
                    if len(parsed) >= 2:
                        return (min(parsed), max(parsed))
                    if len(parsed) == 1:
                        return (parsed[0], parsed[0])
                if isinstance(values, dict):
                    nested_start = as_int(values.get("start") or values.get("from"))
                    nested_end = as_int(values.get("end") or values.get("to"))
                    if nested_start is not None and nested_end is not None:
                        return (min(nested_start, nested_end), max(nested_start, nested_end))

        return (None, None)

    @staticmethod
    def _safe_identifier(name: str) -> str:
        cleaned = "".join(
            ch if ch.isalnum() or ch == "_" else "_"
            for ch in name.strip()
        )

        if not cleaned:
            return "RenamedSymbol"

        if cleaned[0].isdigit():
            cleaned = f"R_{cleaned}"

        return cleaned

    @staticmethod
    def _to_pascal_case(value: str) -> str:
        parts = [
            p
            for p in "".join(
                ch if ch.isalnum() else " "
                for ch in value
            ).split()
            if p
        ]

        if not parts:
            return "Target"

        return "".join(p[0].upper() + p[1:] for p in parts)
