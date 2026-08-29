"""Adapter that maps RDP planner output into SCTVA request payloads."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..constants import (
    ACTION_ENCAPSULATE_C_VARIABLE,
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
    ACTION_INLINE_PYTHON_CLASS,
    ACTION_HIDE_DELEGATE,
    ACTION_MOVE_PYTHON_METHOD,
    ACTION_MOVE_C_FUNCTION,
    ACTION_NOOP,
    ACTION_NARROW_EXCEPTION_HANDLER,
    ACTION_REMOVE_DEAD_CODE,
    ACTION_REPLACE_UNSAFE_FUNCTION,
    ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM,
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
                                # Keep the original target data.  SCTVA can
                                # safely recover a supported semantic action
                                # after an older planner rejects its shape.
                                "legacy_step": step,
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
                                # Retain semantic target details so SCTVA can
                                # recover a now-supported action instead of
                                # silently keeping it as a noop.
                                "legacy_step": step,
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
                                "legacy_step": step,
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
        refactoring = str(
            step.get("refactoring")
            or step.get("action_type")
            or step.get("action")
            or step.get("name")
            or ""
        ).strip()

        if not refactoring:
            raise PlannerAdapterError("missing 'refactoring' in step")

        # RDP payloads use both display labels (``Remove Dead Code``) and
        # canonical action identifiers (``remove_dead_code``). Normalize both
        # into the planner adapter's display-key vocabulary.
        ref_key = " ".join(
            re.sub(r"[_-]+", " ", refactoring.lower()).split()
        )
        params = step.get("parameters") or {}
        target = step.get("target") or {}
        smell_name = str(step.get("smell") or step.get("smell_type") or "").strip()
        smell_key = " ".join(smell_name.lower().replace("_", " ").split())

        if not isinstance(params, dict):
            raise PlannerAdapterError("'parameters' must be an object when provided")

        if isinstance(target, str):
            target = {"function": target}
        if not isinstance(target, dict):
            raise PlannerAdapterError("'target' must be an object or symbol name when provided")

        action: Optional[Dict[str, Any]] = None

        rename_aliases = {
            "rename function",
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

        elif ref_key == "rename method":
            old_name = (
                params.get("method")
                or params.get("method_name")
                or params.get("old_name")
                or target.get("method")
            )
            new_name = (
                params.get("new_method_name")
                or params.get("new_name")
                or params.get("renamed_to")
            )
            source_class = params.get("source_class") or target.get("class")
            source_file = params.get("source_file") or target.get("file") or step.get("source_file")

            if not old_name or not new_name:
                raise PlannerAdapterError("rename method step requires old/new method names")

            action = {
                "action_type": "rename_method",
                "parameters": {
                    "old_name": str(old_name),
                    "new_name": self._safe_identifier(str(new_name)),
                    "source_class": str(source_class or ""),
                    "source_file": str(source_file or ""),
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
            source_file = self._source_file_from_step(
                step,
                params=params,
                target=target,
            )
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
            source_class = self._semantic_class_hint(
                target.get("class")
                or params.get("source_class")
                or params.get("class_name")
                or params.get("module_name"),
                source_file=source_file,
            )

            action = {
                "action_type": ACTION_EXTRACT_METHOD,
                "parameters": {
                    "method": str(target_method),
                    "new_method_name": self._safe_identifier(str(new_name)),
                    "start_line": start_line,
                    "end_line": end_line,
                    "source_class": source_class,
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

        elif ref_key in {"move method", "feature envy"}:
            # Feature-Envy plans from RDP can contain filename-derived placeholders
            # instead of real Python symbols.  Example:
            #   method/source_class = "07_feature_envy_student_report"
            #   destination_class   = "print_student_report"
            # Those values are useful hints but are not sufficient reason to turn
            # the step into noop.  Keep the Move Method action executable and let
            # SCTVA recover the concrete method/classes from the Python AST.
            source_file = self._source_file_from_step(step, params=params, target=target)
            method = (
                target.get("method")
                or target.get("function")
                or params.get("method")
                or params.get("method_name")
                or params.get("source_method")
                or ""
            )
            source_class = self._semantic_class_hint(
                params.get("source_class")
                or params.get("class_name")
                or target.get("class"),
                source_file=source_file,
            )
            destination_class = self._semantic_class_hint(
                params.get("destination_class")
                or params.get("target_class")
                or params.get("destination_type")
                or "",
                source_file=source_file,
            )
            source_line = self._source_line_from_step(step, params=params, target=target)
            raw_lines = target.get("lines")
            target_lines = (
                [int(value) for value in raw_lines if isinstance(value, (int, float))]
                if isinstance(raw_lines, list)
                else []
            )

            normalized_source_file = source_file.replace("\\", "/").lower()
            if normalized_source_file.endswith((".c", ".h")):
                destination_file = str(
                    params.get("destination_file")
                    or params.get("target_file")
                    or target.get("destination_file")
                    or target.get("target_file")
                    or ""
                ).strip()
                if not method or not source_file or not destination_file:
                    raise PlannerAdapterError(
                        "C Move Method is normalized to Move Function and requires method, source_file, and destination_file"
                    )
                action = {
                    "action_type": ACTION_MOVE_C_FUNCTION,
                    "parameters": {
                        "function_name": str(method),
                        "source_file": source_file,
                        "destination_file": destination_file,
                        "header_file": str(params.get("header_file") or ""),
                    },
                }
            elif source_file and not normalized_source_file.endswith(".py"):
                raise PlannerAdapterError(
                    "move method is currently implemented for Python source files only"
                )

            # A source file/line is enough for semantic recovery.  Do not reject a
            # malformed filename-derived identifier here; rejecting it would map
            # the step to noop before SCTVA ever sees the source code.
            if not any(
                str(value or "").strip()
                for value in (method, source_class, destination_class, source_file)
            ) and source_line is None:
                raise PlannerAdapterError(
                    "move method requires a method/class hint, source file, or source line"
                )

            identifier_values = [
                str(value).strip()
                for value in (method, source_class, destination_class)
                if str(value or "").strip()
            ]
            identifiers_valid = bool(identifier_values) and all(
                self._safe_identifier(value) == value
                for value in identifier_values
            )
            targets_complete = bool(method and source_class and destination_class)

            if not action:
                action = {
                "action_type": ACTION_MOVE_PYTHON_METHOD,
                "parameters": {
                    "method": str(method),
                    "source_class": str(source_class),
                    "destination_class": str(destination_class),
                    "destination_parameter": str(params.get("destination_parameter") or ""),
                    "source_file": source_file,
                    "source_line": source_line,
                    "target_lines": target_lines,
                    "semantic_recovery_required": not (
                        targets_complete
                        and identifiers_valid
                        and str(source_class) != str(destination_class)
                    ),
                    "smell": step.get("smell") or step.get("smell_type") or "Feature Envy",
                },
                }

        elif ref_key == "replace conditional with polymorphism":
            method = str(
                target.get("method")
                or target.get("function")
                or params.get("method")
                or params.get("method_name")
                or params.get("target_method")
                or ""
            ).strip()
            source_class = str(
                target.get("class")
                or params.get("source_class")
                or params.get("class_name")
                or ""
            ).strip()
            source_file = self._source_file_from_step(step, params=params, target=target)
            source_line = self._source_line_from_step(step, params=params, target=target)
            start_line, end_line = self._source_range_from_step(
                step,
                params=params,
                target=target,
            )
            if source_file and not source_file.replace("\\", "/").lower().endswith(".py"):
                raise PlannerAdapterError(
                    "replace conditional with polymorphism is currently implemented for Python source files only"
                )
            if not any((method, source_class, source_file, source_line, start_line, end_line)):
                raise PlannerAdapterError(
                    "replace conditional with polymorphism requires a method/class, source file, or source range hint"
                )
            action = {
                "action_type": ACTION_REPLACE_CONDITIONAL_WITH_POLYMORPHISM,
                "parameters": {
                    "method": method,
                    "source_class": source_class,
                    "source_file": source_file,
                    "source_line": source_line,
                    "start_line": start_line,
                    "end_line": end_line,
                    "base_class_name": str(params.get("base_class_name") or "").strip(),
                    "smell": step.get("smell") or step.get("smell_type") or "Switch Statements",
                    "semantic_recovery_required": not bool(method),
                },
            }

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

        elif ref_key == "inline class":
            class_to_inline = (
                params.get("class_to_inline")
                or params.get("target_class")
                or params.get("source_class")
                or params.get("class_name")
                or target.get("class")
            )
            source_file = self._source_file_from_step(step, params=params, target=target)
            source_line = self._source_line_from_step(step, params=params, target=target)
            if not class_to_inline:
                raise PlannerAdapterError(
                    "inline class mapping requires parameters.class_to_inline or target.class"
                )
            if self._safe_identifier(str(class_to_inline)) != str(class_to_inline):
                raise PlannerAdapterError("inline class requires a valid class identifier")
            if source_file and not source_file.replace("\\", "/").lower().endswith(".py"):
                raise PlannerAdapterError(
                    "inline class is currently implemented for Python source files only"
                )
            action = {
                "action_type": ACTION_INLINE_PYTHON_CLASS,
                "parameters": {
                    "class_to_inline": str(class_to_inline),
                    "target_class": str(class_to_inline),
                    "source_file": source_file,
                    "source_line": source_line,
                    "smell": step.get("smell") or step.get("smell_type") or "Lazy Class",
                    "requested_target": {
                        "class_to_inline": str(class_to_inline),
                        "source_file": source_file,
                    },
                },
            }

        elif ref_key == "hide delegate":
            source_class = str(
                params.get("source_class") or target.get("class") or ""
            ).strip()
            delegate_member = str(
                params.get("delegate_member") or params.get("delegate_field") or ""
            ).strip()
            delegated_member = str(
                params.get("delegated_member") or params.get("target_member") or ""
            ).strip()
            new_method_name = str(
                params.get("new_method_name") or params.get("delegate_method_name") or ""
            ).strip()
            source_file = self._source_file_from_step(step, params=params, target=target)
            if not all((source_class, delegate_member, delegated_member)):
                raise PlannerAdapterError(
                    "hide delegate requires source_class, delegate_member, and delegated_member"
                )
            if any(
                self._safe_identifier(value) != value
                for value in (source_class, delegate_member, delegated_member)
            ):
                raise PlannerAdapterError("hide delegate requires valid identifiers")
            normalized_file = source_file.replace("\\", "/").lower()
            if source_file and not normalized_file.endswith((".py", ".java")):
                raise PlannerAdapterError("hide delegate is currently supported for Python and Java source files only")
            action = {
                "action_type": ACTION_HIDE_DELEGATE,
                "parameters": {
                    "source_class": source_class,
                    "delegate_member": delegate_member,
                    "delegated_member": delegated_member,
                    "new_method_name": new_method_name,
                    "source_file": source_file,
                    "source_line": self._source_line_from_step(step, params=params, target=target),
                    "smell": step.get("smell") or step.get("smell_type") or "Message Chains",
                },
            }

        elif ref_key in {
            "replace data value with object",
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
                    "source_method": target.get("method") or params.get("method"),
                    "source_class": target.get("class") or params.get("source_class"),
                    "source_file": target.get("file") or params.get("source_file"),
                    "original_exception_type": "" if is_bare_except else str(
                        params.get("original_exception_type") or "Exception"
                    ),
                    "target_exception_type": str(
                        params.get("target_exception_type")
                        or params.get("narrow_exception_type")
                        or ""
                    ),
                    "handler_name": str(
                        params.get("handler_name") or params.get("exception_variable") or ""
                    ),
                    "source_smell": smell_name,
                },
            }

        elif ref_key == "remove dead code":
            method = (
                params.get("method")
                or params.get("method_name")
                or params.get("function")
                or params.get("function_name")
                or params.get("target_method")
                or params.get("target_function")
                or target.get("method")
                or target.get("function")
                or target.get("target_method")
                or target.get("target_function")
                or target.get("name")
                or target.get("symbol")
                or params.get("name")
                or params.get("symbol")
            )
            source_line = self._source_line_from_step(step, params=params, target=target)
            source_file = self._source_file_from_step(step, params=params, target=target)

            action = {
                "action_type": ACTION_REMOVE_DEAD_CODE,
                "parameters": {
                    "method": str(method or ""),
                    "class_name": self._semantic_class_hint(
                        target.get("class") or params.get("source_class"),
                        source_file=source_file,
                    ) or None,
                    "source_line": source_line,
                    "source_file": source_file,
                },
            }

        elif ref_key in {
            "refactor to narrow exceptions",
            "exception overreach",
            "narrow exception handling",
            "narrow exception handler",
            "replace bare except",
            "replace bare except with specific exception",
            "replace bare except with specific exceptions",
            "replace_bare_except",
            "replace_bare_except_with_specific_exception",
            "replace broad exception handling",
            "replace broad exception with specific exception",
            "replace broad exception with specific exceptions",
            "replace broad exception handler",
        }:
            source_line = self._source_line_from_step(step, params=params, target=target)
            source_file = self._source_file_from_step(step, params=params, target=target)
            source_method = str(
                target.get("method")
                or target.get("function")
                or target.get("name")
                or params.get("source_method")
                or params.get("method")
                or params.get("method_name")
                or params.get("function")
                or params.get("function_name")
                or ""
            ).strip()
            # For exception-handler refactoring an explicit class name that
            # matches the Python filename (for example Model in model.py) is
            # perfectly valid.  Do not apply the stale filename-class heuristic
            # used by some older refactorings here.
            source_class = str(
                target.get("class")
                or params.get("source_class")
                or params.get("class_name")
                or ""
            ).strip()
            is_bare_except = ref_key in {
                "replace bare except",
                "replace bare except with specific exception",
                "replace bare except with specific exceptions",
                "replace_bare_except",
                "replace_bare_except_with_specific_exception",
            }
            original_type = (
                ""
                if is_bare_except
                else params.get("original_exception_type") or params.get("exception_type") or "Exception"
            )
            target_type = params.get("target_exception_type") or params.get("narrow_exception_type")
            if source_line is None and not source_method:
                raise PlannerAdapterError(
                    "narrow_exception_handler mapping requires a source line or target method"
                )
            action = {
                "action_type": ACTION_NARROW_EXCEPTION_HANDLER,
                "parameters": {
                    "source_line": source_line,
                    "method": source_method,
                    "source_method": source_method,
                    "class_name": source_class,
                    "source_class": source_class,
                    "source_file": source_file,
                    "original_exception_type": str(original_type),
                    "target_exception_type": str(target_type or ""),
                    "handler_name": str(params.get("handler_name") or params.get("exception_variable") or ""),
                    "exception_smell": "bare_except" if is_bare_except else "exception_overreach",
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

        elif ref_key in {"encapsulate variable", "global variable"}:
            variable_name = (
                params.get("variable_name")
                or params.get("variable")
                or target.get("variable")
                or target.get("name")
            )
            if not variable_name:
                raise PlannerAdapterError(
                    "encapsulate variable mapping requires parameters.variable_name"
                )

            action = {
                "action_type": ACTION_ENCAPSULATE_C_VARIABLE,
                "parameters": {
                    "variable_name": str(variable_name),
                    "getter_name": self._safe_identifier(
                        str(params.get("getter_name") or f"get_{variable_name}")
                    ),
                    "setter_name": self._safe_identifier(
                        str(params.get("setter_name") or f"set_{variable_name}")
                    ),
                    "source_file": self._source_file_from_step(step, params=params, target=target),
                    # Preserve the planner location hint.  RDP sometimes emits
                    # the placeholder name ``variable`` for C globals; SCTVA
                    # uses this line plus source analysis to recover the real
                    # declaration before transformation.
                    "source_line": self._source_line_from_step(
                        step, params=params, target=target
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
    def _semantic_class_hint(value: Any, *, source_file: str) -> str:
        """Discard legacy Python class hints fabricated from a file name."""

        class_name = str(value or "").strip()
        normalized_file = str(source_file or "").replace("\\", "/")
        if not class_name or not normalized_file.lower().endswith(".py"):
            return class_name

        file_stem = normalized_file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        normalized_class = re.sub(r"[^a-z0-9]", "", class_name.lower())
        normalized_stem = re.sub(r"[^a-z0-9]", "", file_stem.lower())
        if normalized_class in {normalized_stem, f"{normalized_stem}target"}:
            return ""
        return class_name

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
