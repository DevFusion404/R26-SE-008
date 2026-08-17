# """Behavioral fingerprinting utilities for Python and Java runtime checks."""

# from __future__ import annotations

# import json
# import multiprocessing as mp
# import math
# import shutil
# import subprocess
# import sys
# import time
# import traceback
# import uuid
# from io import StringIO
# from pathlib import Path
# from typing import Any, Dict, Optional


# INFRASTRUCTURE_EXCEPTION_TYPES = {
#     "CompilationError",
#     "RuntimeUnavailable",
# }

# INFRASTRUCTURE_ERROR_CATEGORIES = {
#     "javac_failed",
#     "java_runtime_unavailable",
#     "java_harness_failed",
#     "java_probe_failed",
#     "no_output",
#     "subprocess_failed",
# }


# def _safe_repr(value: Any, max_length: int = 500) -> str:
#     try:
#         rendered = repr(value)
#     except Exception:  # pragma: no cover - defensive for hostile __repr__
#         rendered = f"<unrepresentable {type(value).__name__}>"
#     if len(rendered) > max_length:
#         return rendered[:max_length] + "...<truncated>"
#     return rendered


# def mine_value_invariants(value: Any) -> Dict[str, Any]:
#     """Infer lightweight runtime invariants for a single observed value."""
#     invariants: Dict[str, Any] = {
#         "type": type(value).__name__,
#         "is_none": value is None,
#     }

#     try:
#         invariants["truthy"] = bool(value)
#     except Exception:
#         invariants["truthy"] = None

#     if isinstance(value, (int, float)) and not isinstance(value, bool):
#         numeric_value = float(value)
#         invariants["numeric"] = {
#             "finite": math.isfinite(numeric_value),
#             "min": numeric_value,
#             "max": numeric_value,
#             "sign": "negative" if numeric_value < 0 else "positive" if numeric_value > 0 else "zero",
#         }

#     if isinstance(value, str):
#         invariants["string"] = {
#             "length": len(value),
#             "empty": value == "",
#             "line_count": value.count("\n") + (1 if value else 0),
#         }

#     if isinstance(value, (list, tuple, set, frozenset, dict, str, bytes, bytearray)):
#         try:
#             invariants["size"] = {
#                 "length": len(value),
#                 "empty": len(value) == 0,
#             }
#         except Exception:
#             pass

#     if isinstance(value, (list, tuple, set, frozenset)) and value:
#         item_types = sorted({type(item).__name__ for item in value})
#         invariants["collection"] = {
#             "item_types": item_types,
#             "homogeneous": len(item_types) == 1,
#         }

#     if isinstance(value, dict):
#         key_types = sorted({type(key).__name__ for key in value.keys()})
#         value_types = sorted({type(item).__name__ for item in value.values()})
#         invariants["mapping"] = {
#             "key_types": key_types,
#             "value_types": value_types,
#             "keys_repr": sorted(_safe_repr(key, max_length=80) for key in value.keys()),
#         }

#     return invariants


# def mine_exception_invariants(exc: BaseException) -> Dict[str, Any]:
#     """Infer exception-pattern invariants from an observed failure."""
#     return {
#         "exception": {
#             "type": type(exc).__name__,
#             "message_category": str(exc)[:200],
#             "has_message": bool(str(exc)),
#         }
#     }


# def _stdout_invariants(stdout: str) -> Dict[str, Any]:
#     return {
#         "stdout": {
#             "length": len(stdout),
#             "line_count": stdout.count("\n") + (1 if stdout else 0),
#             "empty": stdout == "",
#         }
#     }


# def _is_infrastructure_failure(fingerprint: Dict[str, Any]) -> bool:
#     if fingerprint.get("success"):
#         return False
#     return (
#         fingerprint.get("exception_type") in INFRASTRUCTURE_EXCEPTION_TYPES
#         or fingerprint.get("exception_message_category") in INFRASTRUCTURE_ERROR_CATEGORIES
#         or fingerprint.get("reason") in INFRASTRUCTURE_ERROR_CATEGORIES
#     )


# def _runtime_temp_root() -> Path:
#     root = Path(__file__).resolve().parents[2] / ".sctva_runtime"
#     root.mkdir(parents=True, exist_ok=True)
#     return root


# def _make_runtime_temp_dir(prefix: str) -> Path:
#     temp_path = _runtime_temp_root() / f"{prefix}_{uuid.uuid4().hex}"
#     temp_path.mkdir(parents=True, exist_ok=False)
#     return temp_path


# def _execute_python_test_case(source_code: str, test: dict) -> Dict[str, Any]:
#     out = StringIO()
#     try:
#         sys_stdout = sys.stdout
#         sys.stdout = out
#         ns: Dict[str, Any] = {}
#         # Execute the provided source to recreate namespace in child
#         exec(source_code, ns)

#         start = time.perf_counter()
#         if "expression" in test:
#             result = eval(str(test["expression"]), ns)
#         else:
#             fn_name = str(test.get("call"))
#             args = test.get("args", []) or []
#             kwargs = test.get("kwargs", {}) or {}
#             fn = ns.get(fn_name)
#             if not callable(fn):
#                 raise ValueError(f"Callable '{fn_name}' not found")
#             result = fn(*args, **kwargs)

#         duration = int((time.perf_counter() - start) * 1000)
#         sys.stdout = sys_stdout
#         stdout = out.getvalue()
#         fingerprint = {
#             "success": True,
#             "return_value_repr": _safe_repr(result),
#             "return_type": type(result).__name__,
#             "exception_type": None,
#             "exception_message_category": None,
#             "stdout": stdout,
#             "execution_time_ms": duration,
#             "timeout": False,
#             "runtime_error_details": None,
#             "observed_invariants": {
#                 "return": mine_value_invariants(result),
#                 **_stdout_invariants(stdout),
#             },
#         }
#     except Exception as exc:  # pragma: no cover - exercised by tests
#         sys.stdout = sys.__stdout__
#         tb = traceback.format_exc()
#         stdout = out.getvalue() if 'out' in locals() else ""
#         fingerprint = {
#             "success": False,
#             "return_value_repr": None,
#             "return_type": None,
#             "exception_type": type(exc).__name__,
#             "exception_message_category": str(exc)[:200],
#             "stdout": stdout,
#             "execution_time_ms": 0,
#             "timeout": False,
#             "runtime_error_details": tb,
#             "observed_invariants": {
#                 **mine_exception_invariants(exc),
#                 **_stdout_invariants(stdout),
#             },
#         }
#     return fingerprint


# def _runner_callable(pipe, source_code: str, test: dict):
#     fingerprint = _execute_python_test_case(source_code, test)
#     try:
#         pipe.send(fingerprint)
#     except Exception:
#         pass


# class BehaviorFingerprintRunner:
#     """Runs Python callables safely in a subprocess and returns a fingerprint.

#     Notes:
#     - Return values are recorded as repr() to avoid pickling issues.
#     - Execution is time-limited using multiprocessing and a timeout.
#     """

#     def __init__(self, default_timeout_seconds: int = 2) -> None:
#         self.default_timeout_seconds = default_timeout_seconds

#     def run_python_test(self, source_code: str, test: dict, timeout: Optional[float] = None) -> Dict[str, Any]:
#         timeout = timeout or self.default_timeout_seconds

#         try:
#             parent_conn, child_conn = mp.Pipe()
#             proc = mp.Process(target=_runner_callable, args=(child_conn, source_code, test))
#             proc.start()
#         except (OSError, PermissionError):
#             return self._run_python_test_subprocess(source_code, test, timeout)

#         start = time.perf_counter()
#         finished = parent_conn.poll(timeout)
#         if not finished:
#             # timeout - ensure process is terminated
#             try:
#                 proc.terminate()
#             except Exception:
#                 pass
#             proc.join(0.1)
#             execution_time_ms = int((time.perf_counter() - start) * 1000)
#             return {
#                 "success": False,
#                 "return_value_repr": None,
#                 "return_type": None,
#                 "exception_type": "TimeoutError",
#                 "exception_message_category": "timeout",
#                 "stdout": "",
#                 "execution_time_ms": execution_time_ms,
#                 "timeout": True,
#                 "runtime_error_details": "Process terminated due to timeout.",
#                 "observed_invariants": {
#                     "exception": {
#                         "type": "TimeoutError",
#                         "message_category": "timeout",
#                         "has_message": True,
#                     },
#                     **_stdout_invariants(""),
#                 },
#             }

#         try:
#             fingerprint = parent_conn.recv()
#         except EOFError:
#             fingerprint = {
#                 "success": False,
#                 "return_value_repr": None,
#                 "return_type": None,
#                 "exception_type": "RuntimeError",
#                 "exception_message_category": "no_output",
#                 "stdout": "",
#                 "execution_time_ms": int((time.perf_counter() - start) * 1000),
#                 "timeout": False,
#                 "runtime_error_details": "Child process closed without sending result",
#                 "observed_invariants": {
#                     "exception": {
#                         "type": "RuntimeError",
#                         "message_category": "no_output",
#                         "has_message": True,
#                     },
#                     **_stdout_invariants(""),
#                 },
#             }

#         proc.join(0.1)
#         return fingerprint

#     def _run_python_test_subprocess(self, source_code: str, test: dict, timeout: float) -> Dict[str, Any]:
#         start = time.perf_counter()
#         temp_path = _make_runtime_temp_dir("python_fp")
#         try:
#             source_path = temp_path / "source.py.txt"
#             test_path = temp_path / "test.json"
#             result_path = temp_path / "fingerprint.json"

#             source_path.write_text(source_code, encoding="utf-8")
#             test_path.write_text(json.dumps(test), encoding="utf-8")

#             runner = (
#                 "import json, sys\n"
#                 "from pathlib import Path\n"
#                 "from sctva.validators.behavior_fingerprint import _execute_python_test_case\n"
#                 "source = Path(sys.argv[1]).read_text(encoding='utf-8')\n"
#                 "test = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
#                 "fingerprint = _execute_python_test_case(source, test)\n"
#                 "Path(sys.argv[3]).write_text(json.dumps(fingerprint), encoding='utf-8')\n"
#             )

#             try:
#                 completed = subprocess.run(
#                     [sys.executable, "-c", runner, str(source_path), str(test_path), str(result_path)],
#                     cwd=Path(__file__).resolve().parents[2],
#                     capture_output=True,
#                     text=True,
#                     timeout=timeout,
#                 )
#             except subprocess.TimeoutExpired:
#                 execution_time_ms = int((time.perf_counter() - start) * 1000)
#                 return {
#                     "success": False,
#                     "return_value_repr": None,
#                     "return_type": None,
#                     "exception_type": "TimeoutError",
#                     "exception_message_category": "timeout",
#                     "stdout": "",
#                     "execution_time_ms": execution_time_ms,
#                     "timeout": True,
#                     "runtime_error_details": "Process terminated due to timeout.",
#                     "observed_invariants": {
#                         "exception": {
#                             "type": "TimeoutError",
#                             "message_category": "timeout",
#                             "has_message": True,
#                         },
#                         **_stdout_invariants(""),
#                     },
#                 }

#             if completed.returncode != 0:
#                 return {
#                     "success": False,
#                     "return_value_repr": None,
#                     "return_type": None,
#                     "exception_type": "RuntimeError",
#                     "exception_message_category": "subprocess_failed",
#                     "stdout": completed.stdout,
#                     "execution_time_ms": int((time.perf_counter() - start) * 1000),
#                     "timeout": False,
#                     "runtime_error_details": completed.stderr,
#                     "observed_invariants": {
#                         "exception": {
#                             "type": "RuntimeError",
#                             "message_category": "subprocess_failed",
#                             "has_message": True,
#                         },
#                         **_stdout_invariants(completed.stdout),
#                     },
#                 }

#             if not result_path.exists():
#                 return {
#                     "success": False,
#                     "return_value_repr": None,
#                     "return_type": None,
#                     "exception_type": "RuntimeError",
#                     "exception_message_category": "no_output",
#                     "stdout": completed.stdout,
#                     "execution_time_ms": int((time.perf_counter() - start) * 1000),
#                     "timeout": False,
#                     "runtime_error_details": "Subprocess closed without writing fingerprint result",
#                     "observed_invariants": {
#                         "exception": {
#                             "type": "RuntimeError",
#                             "message_category": "no_output",
#                             "has_message": True,
#                         },
#                         **_stdout_invariants(completed.stdout),
#                     },
#                 }

#             fingerprint = json.loads(result_path.read_text(encoding="utf-8"))
#             fingerprint["execution_time_ms"] = int((time.perf_counter() - start) * 1000)
#             return fingerprint
#         finally:
#             shutil.rmtree(temp_path, ignore_errors=True)


# def compare_fingerprints(orig: Dict[str, Any], trans: Dict[str, Any]) -> Dict[str, Any]:
#     """Compare two behavioral fingerprints.

#     Important fix for Java: compilation/setup errors are NOT behavior equivalence.
#     If both versions fail to compile, the validation must fail instead of passing
#     as "same_exception".
#     """
#     critical_setup_errors = {
#         "CompilationError",
#         "RuntimeUnavailable",
#         "HarnessError",
#         "RuntimeError",
#     }

#     if orig.get("timeout") or trans.get("timeout"):
#         if orig.get("timeout") and trans.get("timeout"):
#             return {"matched": False, "reason": "both_timed_out"}
#         if orig.get("timeout"):
#             return {"matched": False, "reason": "original_timed_out"}
#         return {"matched": False, "reason": "transformed_timed_out"}

#     orig_exc = orig.get("exception_type")
#     trans_exc = trans.get("exception_type")

#     if orig_exc in critical_setup_errors or trans_exc in critical_setup_errors:
#         return {
#             "matched": False,
#             "reason": f"setup_or_compilation_error: original={orig_exc}, transformed={trans_exc}",
#         }

#     if orig.get("success") and not trans.get("success"):
#         return {"matched": False, "reason": "original_succeeds_transformed_fails"}

#     if trans.get("success") and not orig.get("success"):
#         return {"matched": False, "reason": "transformed_succeeds_original_fails"}

#     if orig.get("success") and trans.get("success"):
#         if orig.get("return_type") != trans.get("return_type"):
#             return {"matched": False, "reason": "return_type_mismatch"}

#         if orig.get("return_value_repr") != trans.get("return_value_repr"):
#             return {"matched": False, "reason": "return_value_mismatch"}

#         if orig.get("stdout") != trans.get("stdout"):
#             return {"matched": False, "reason": "stdout_mismatch"}

#         return {"matched": True, "reason": "match"}

#     if not orig.get("success") and not trans.get("success"):
#         if orig_exc == trans_exc:
#             return {"matched": True, "reason": "same_exception"}
#         return {"matched": False, "reason": "exception_type_mismatch"}

#     return {"matched": False, "reason": "unknown_mismatch"}


"""Behavioral fingerprinting utilities for Python and Java runtime checks."""

from __future__ import annotations

import json
import math
import multiprocessing as mp
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Optional


def _safe_repr(value: Any, max_length: int = 500) -> str:
    try:
        rendered = repr(value)
    except Exception:
        rendered = f"<unrepresentable {type(value).__name__}>"
    if len(rendered) > max_length:
        return rendered[:max_length] + "...<truncated>"
    return rendered


def mine_value_invariants(value: Any) -> Dict[str, Any]:
    invariants: Dict[str, Any] = {
        "type": type(value).__name__,
        "is_none": value is None,
    }

    try:
        invariants["truthy"] = bool(value)
    except Exception:
        invariants["truthy"] = None

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric_value = float(value)
        invariants["numeric"] = {
            "finite": math.isfinite(numeric_value),
            "min": numeric_value,
            "max": numeric_value,
            "sign": "negative" if numeric_value < 0 else "positive" if numeric_value > 0 else "zero",
        }

    if isinstance(value, str):
        invariants["string"] = {
            "length": len(value),
            "empty": value == "",
            "line_count": value.count("\n") + (1 if value else 0),
        }

    if isinstance(value, (list, tuple, set, frozenset, dict, str, bytes, bytearray)):
        try:
            invariants["size"] = {
                "length": len(value),
                "empty": len(value) == 0,
            }
        except Exception:
            pass

    if isinstance(value, (list, tuple, set, frozenset)) and value:
        item_types = sorted({type(item).__name__ for item in value})
        invariants["collection"] = {
            "item_types": item_types,
            "homogeneous": len(item_types) == 1,
        }

    if isinstance(value, dict):
        invariants["mapping"] = {
            "key_types": sorted({type(key).__name__ for key in value.keys()}),
            "value_types": sorted({type(item).__name__ for item in value.values()}),
            "keys_repr": sorted(_safe_repr(key, max_length=80) for key in value.keys()),
        }

    return invariants


def mine_exception_invariants(exc: BaseException | str, message: str | None = None) -> Dict[str, Any]:
    if isinstance(exc, BaseException):
        exc_type = type(exc).__name__
        exc_message = str(exc)
    else:
        exc_type = str(exc)
        exc_message = message or ""

    return {
        "exception": {
            "type": exc_type,
            "message_category": exc_message[:200],
            "has_message": bool(exc_message),
        }
    }


def stdout_invariants(stdout: str) -> Dict[str, Any]:
    return {
        "stdout": {
            "length": len(stdout),
            "line_count": stdout.count("\n") + (1 if stdout else 0),
            "empty": stdout == "",
        }
    }


def _runtime_temp_root() -> Path:
    root = Path(tempfile.gettempdir()) / "sctva_runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _make_runtime_temp_dir(prefix: str) -> Path:
    temp_path = _runtime_temp_root() / f"{prefix}_{uuid.uuid4().hex}"
    temp_path.mkdir(parents=True, exist_ok=False)
    return temp_path


def _execute_python_test_case(source_code: str, test: dict) -> Dict[str, Any]:
    out = StringIO()
    sys_stdout = sys.stdout

    try:
        sys.stdout = out
        namespace: Dict[str, Any] = {}

        exec(source_code, namespace)

        start = time.perf_counter()

        if "expression" in test:
            result = eval(str(test["expression"]), namespace)
        else:
            fn_name = str(test.get("call"))
            args = test.get("args", []) or []
            kwargs = test.get("kwargs", {}) or {}

            fn = namespace.get(fn_name)

            if not callable(fn):
                raise ValueError(f"Callable '{fn_name}' not found")

            result = fn(*args, **kwargs)

        duration = int((time.perf_counter() - start) * 1000)
        sys.stdout = sys_stdout

        fingerprint = {
            "success": True,
            "return_value_repr": _safe_repr(result),
            "return_type": type(result).__name__,
            "exception_type": None,
            "exception_message_category": None,
            "stdout": out.getvalue(),
            "execution_time_ms": duration,
            "timeout": False,
            "runtime_error_details": None,
            "observed_invariants": {
                "return": mine_value_invariants(result),
                **stdout_invariants(out.getvalue()),
            },
        }

    except Exception as exc:
        sys.stdout = sys_stdout
        stdout = out.getvalue() if "out" in locals() else ""

        fingerprint = {
            "success": False,
            "return_value_repr": None,
            "return_type": None,
            "exception_type": type(exc).__name__,
            "exception_message_category": str(exc)[:200],
            "stdout": stdout,
            "execution_time_ms": 0,
            "timeout": False,
            "runtime_error_details": traceback.format_exc(),
            "observed_invariants": {
                **mine_exception_invariants(exc),
                **stdout_invariants(stdout),
            },
        }

    return fingerprint


def _runner_callable(pipe, source_code: str, test: dict):
    fingerprint = _execute_python_test_case(source_code, test)
    try:
        pipe.send(fingerprint)
    except Exception:
        pass


class BehaviorFingerprintRunner:
    """Runs Python callables safely in a subprocess and returns a fingerprint."""

    def __init__(self, default_timeout_seconds: int = 2) -> None:
        self.default_timeout_seconds = default_timeout_seconds

    def run_python_test(
        self,
        source_code: str,
        test: dict,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        timeout = self.default_timeout_seconds if timeout is None else timeout

        try:
            parent_conn, child_conn = mp.Pipe()
            proc = mp.Process(target=_runner_callable, args=(child_conn, source_code, test))
            proc.start()
        except (OSError, PermissionError):
            return self._run_python_test_subprocess(source_code, test, timeout)

        start = time.perf_counter()

        finished = parent_conn.poll(timeout)

        if not finished:
            try:
                proc.terminate()
            except Exception:
                pass

            proc.join(0.1)

            return {
                "success": False,
                "return_value_repr": None,
                "return_type": None,
                "exception_type": "TimeoutError",
                "exception_message_category": "timeout",
                "stdout": "",
                "execution_time_ms": int((time.perf_counter() - start) * 1000),
                "timeout": True,
                "runtime_error_details": "Process terminated due to timeout.",
                "observed_invariants": {
                    **mine_exception_invariants("TimeoutError", "timeout"),
                    **stdout_invariants(""),
                },
            }

        try:
            fingerprint = parent_conn.recv()
        except EOFError:
            fingerprint = {
                "success": False,
                "return_value_repr": None,
                "return_type": None,
                "exception_type": "RuntimeError",
                "exception_message_category": "no_output",
                "stdout": "",
                "execution_time_ms": int((time.perf_counter() - start) * 1000),
                "timeout": False,
                "runtime_error_details": "Child process closed without sending result.",
                "observed_invariants": {
                    **mine_exception_invariants("RuntimeError", "no_output"),
                    **stdout_invariants(""),
                },
            }

        proc.join(0.1)
        return fingerprint

    def _run_python_test_subprocess(
        self,
        source_code: str,
        test: dict,
        timeout: float,
    ) -> Dict[str, Any]:
        start = time.perf_counter()
        temp_path = _make_runtime_temp_dir("python_fp")

        try:
            source_path = temp_path / "source.py.txt"
            test_path = temp_path / "test.json"
            result_path = temp_path / "fingerprint.json"

            source_path.write_text(source_code, encoding="utf-8")
            test_path.write_text(json.dumps(test), encoding="utf-8")

            package_root = Path(__file__).resolve().parents[2]
            runner = (
                "import json, sys\n"
                "from pathlib import Path\n"
                f"sys.path.insert(0, {str(package_root)!r})\n"
                "from sctva.validators.behavior_fingerprint import _execute_python_test_case\n"
                "source = Path(sys.argv[1]).read_text(encoding='utf-8')\n"
                "test = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
                "fingerprint = _execute_python_test_case(source, test)\n"
                "Path(sys.argv[3]).write_text(json.dumps(fingerprint), encoding='utf-8')\n"
            )

            try:
                completed = subprocess.run(
                    [sys.executable, "-c", runner, str(source_path), str(test_path), str(result_path)],
                    cwd=package_root,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return {
                    "success": False,
                    "return_value_repr": None,
                    "return_type": None,
                    "exception_type": "TimeoutError",
                    "exception_message_category": "timeout",
                    "stdout": "",
                    "execution_time_ms": int((time.perf_counter() - start) * 1000),
                    "timeout": True,
                    "runtime_error_details": "Process terminated due to timeout.",
                    "observed_invariants": {
                        **mine_exception_invariants("TimeoutError", "timeout"),
                        **stdout_invariants(""),
                    },
                }

            if completed.returncode != 0:
                return {
                    "success": False,
                    "return_value_repr": None,
                    "return_type": None,
                    "exception_type": "RuntimeError",
                    "exception_message_category": "subprocess_failed",
                    "stdout": completed.stdout,
                    "execution_time_ms": int((time.perf_counter() - start) * 1000),
                    "timeout": False,
                    "runtime_error_details": completed.stderr,
                    "observed_invariants": {
                        **mine_exception_invariants("RuntimeError", "subprocess_failed"),
                        **stdout_invariants(completed.stdout),
                    },
                }

            if not result_path.exists():
                return {
                    "success": False,
                    "return_value_repr": None,
                    "return_type": None,
                    "exception_type": "RuntimeError",
                    "exception_message_category": "no_output",
                    "stdout": completed.stdout,
                    "execution_time_ms": int((time.perf_counter() - start) * 1000),
                    "timeout": False,
                    "runtime_error_details": "Subprocess closed without writing fingerprint result.",
                    "observed_invariants": {
                        **mine_exception_invariants("RuntimeError", "no_output"),
                        **stdout_invariants(completed.stdout),
                    },
                }

            fingerprint = json.loads(result_path.read_text(encoding="utf-8"))
            fingerprint["execution_time_ms"] = int((time.perf_counter() - start) * 1000)
            return fingerprint
        finally:
            shutil.rmtree(temp_path, ignore_errors=True)


def compare_fingerprints(orig: Dict[str, Any], trans: Dict[str, Any]) -> Dict[str, Any]:
    """Compare two fingerprints.

    Important:
    Compilation/setup failures are not behavior equivalence.
    If both Java versions fail to compile, that must not be treated as a pass.
    """

    critical_setup_errors = {
        "CompilationError",
        "RuntimeUnavailable",
        "HarnessError",
        "RuntimeError",
    }

    if orig.get("timeout") or trans.get("timeout"):
        if orig.get("timeout") and trans.get("timeout"):
            return {"matched": False, "reason": "both_timed_out"}
        if orig.get("timeout"):
            return {"matched": False, "reason": "original_timed_out"}
        return {"matched": False, "reason": "transformed_timed_out"}

    orig_exception = orig.get("exception_type")
    trans_exception = trans.get("exception_type")

    if orig_exception in critical_setup_errors or trans_exception in critical_setup_errors:
        return {
            "matched": False,
            "reason": "fingerprint_execution_failed",
        }

    if orig.get("success") and not trans.get("success"):
        return {"matched": False, "reason": "original_succeeds_transformed_fails"}

    if trans.get("success") and not orig.get("success"):
        return {"matched": False, "reason": "transformed_succeeds_original_fails"}

    if orig.get("success") and trans.get("success"):
        if orig.get("return_type") != trans.get("return_type"):
            return {"matched": False, "reason": "return_type_mismatch"}

        if orig.get("return_value_repr") != trans.get("return_value_repr"):
            return {"matched": False, "reason": "return_value_mismatch"}

        if orig.get("stdout") != trans.get("stdout"):
            return {"matched": False, "reason": "stdout_mismatch"}

        return {"matched": True, "reason": "match"}

    if not orig.get("success") and not trans.get("success"):
        if orig_exception == trans_exception:
            return {"matched": True, "reason": "same_exception"}
        return {"matched": False, "reason": "exception_type_mismatch"}

    return {"matched": False, "reason": "unknown_mismatch"}
