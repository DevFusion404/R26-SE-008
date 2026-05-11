"""
report_generator.py
-------------------
Generates the CUQA Quality Report JSON for a parsed codebase.
Produces code smell detection, complexity metrics, and a structured
report suitable for downstream consumption by the RDP Agent.
"""

import os
import ast as pyast
from typing import Any

try:
    import javalang
    JAVALANG_AVAILABLE = True
except ImportError:
    JAVALANG_AVAILABLE = False


# ---------------------------------------------------------------------------
# Code smell detectors (Python)
# ---------------------------------------------------------------------------

class _PythonSmellVisitor(pyast.NodeVisitor):
    """Walk a Python AST and collect code smells with full entity context."""

    def __init__(self):
        self.smells: list[dict] = []
        self._class_stack: list[str] = []
        self._func_stack: list[str] = []  # tracks enclosing function names

    def _add(self, smell_type: str, message: str, line: int | None,
             severity: str = "medium", entity: str | None = None):
        """Append a smell dict — entity is always resolved."""
        resolved = (
            entity
            or (self._func_stack[-1] if self._func_stack else None)
            or (self._class_stack[-1] if self._class_stack else None)
        )
        self.smells.append({
            "type": smell_type,
            "message": message,
            "line": line,
            "severity": severity,
            "entity": resolved,
        })

    # --- Long method / Too many parameters ---
    def visit_FunctionDef(self, node: pyast.FunctionDef):
        self._func_stack.append(node.name)

        body_lines = (node.end_lineno or node.lineno) - node.lineno
        if body_lines > 30:
            self._add(
                "LongMethod",
                f"Function '{node.name}' has {body_lines} lines (>30)",
                node.lineno, "high", entity=node.name,
            )

        # Exclude self/cls from parameter count
        real_args = [a for a in node.args.args if a.arg not in ("self", "cls")]
        if len(real_args) > 5:
            self._add(
                "TooManyParameters",
                f"Function '{node.name}' has {len(real_args)} parameters (>5)",
                node.lineno, "medium", entity=node.name,
            )

        self.generic_visit(node)
        self._func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    # --- Large class ---
    def visit_ClassDef(self, node: pyast.ClassDef):
        self._class_stack.append(node.name)
        method_count = sum(
            1 for n in pyast.walk(node)
            if isinstance(n, (pyast.FunctionDef, pyast.AsyncFunctionDef))
        )
        if method_count > 15:
            self._add(
                "LargeClass",
                f"Class '{node.name}' has {method_count} methods (>15)",
                node.lineno, "high", entity=node.name,
            )
        self.generic_visit(node)
        self._class_stack.pop()

    # --- Magic numbers (entity resolved via stack) ---
    def visit_Constant(self, node: pyast.Constant):
        if isinstance(node.value, (int, float)) and node.value not in (0, 1, -1, 2, True, False):
            self._add(
                "MagicNumber",
                f"Magic number {node.value}",
                getattr(node, "lineno", None), "low",
            )

    # --- Bare except (entity resolved via stack) ---
    def visit_ExceptHandler(self, node: pyast.ExceptHandler):
        if node.type is None:
            self._add(
                "BareExcept",
                "Bare 'except:' clause catches all exceptions",
                node.lineno, "medium",
            )
        self.generic_visit(node)


def _analyze_python_smells(source: str) -> list[dict]:
    try:
        tree = pyast.parse(source)
    except SyntaxError:
        return []
    visitor = _PythonSmellVisitor()
    visitor.visit(tree)
    return visitor.smells




def _python_metrics(source: str, filename: str) -> dict:
    lines = source.splitlines()
    blank = sum(1 for l in lines if not l.strip())
    comment = sum(1 for l in lines if l.strip().startswith("#"))
    loc = len(lines)
    try:
        tree = pyast.parse(source)
        functions = sum(1 for n in pyast.walk(tree) if isinstance(n, (pyast.FunctionDef, pyast.AsyncFunctionDef)))
        classes = sum(1 for n in pyast.walk(tree) if isinstance(n, pyast.ClassDef))
    except SyntaxError:
        functions = classes = 0
    return {
        "filename": filename,
        "lines_of_code": loc,
        "blank_lines": blank,
        "comment_lines": comment,
        "functions": functions,
        "classes": classes,
    }


# ---------------------------------------------------------------------------
# Code smell detectors (Java - structural heuristics)
# ---------------------------------------------------------------------------

def _analyze_java_smells(source: str) -> list[dict]:
    smells = []
    if not JAVALANG_AVAILABLE:
        return smells
    try:
        tree = javalang.parse.parse(source)
    except Exception:
        return smells

    for _, node in tree:
        if isinstance(node, javalang.tree.MethodDeclaration):
            params = getattr(node, "parameters", []) or []
            if len(params) > 5:
                line = getattr(node.position, "line", None) if node.position else None
                smells.append({
                    "type": "TooManyParameters",
                    "message": f"Method '{node.name}' has {len(params)} parameters (>5)",
                    "line": line,
                    "severity": "medium",
                    "entity": node.name,        # ← method name
                })

        if isinstance(node, (javalang.tree.ClassDeclaration, javalang.tree.InterfaceDeclaration)):
            methods = getattr(node, "methods", []) or []
            if len(methods) > 15:
                line = getattr(node.position, "line", None) if node.position else None
                smells.append({
                    "type": "LargeClass",
                    "message": f"Class '{node.name}' has {len(methods)} methods (>15)",
                    "line": line,
                    "severity": "high",
                    "entity": node.name,        # ← class name
                })

    return smells



def _java_metrics(source: str, filename: str) -> dict:
    lines = source.splitlines()
    blank = sum(1 for l in lines if not l.strip())
    comment = sum(1 for l in lines if l.strip().startswith("//") or l.strip().startswith("*"))
    loc = len(lines)
    classes = functions = 0
    if JAVALANG_AVAILABLE:
        try:
            tree = javalang.parse.parse(source)
            for _, node in tree:
                if isinstance(node, (javalang.tree.ClassDeclaration, javalang.tree.InterfaceDeclaration)):
                    classes += 1
                if isinstance(node, javalang.tree.MethodDeclaration):
                    functions += 1
        except Exception:
            pass
    return {
        "filename": filename,
        "lines_of_code": loc,
        "blank_lines": blank,
        "comment_lines": comment,
        "functions": functions,
        "classes": classes,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_file_report(source: str, filename: str) -> dict:
    """
    Generate a quality report for a single source file.

    Args:
        source:   Raw source code string.
        filename: Original filename (used for language detection).

    Returns:
        CUQA quality report dict for the file.
    """
    ext = os.path.splitext(filename)[-1].lower()

    if ext == ".py":
        smells = _analyze_python_smells(source)
        metrics = _python_metrics(source, filename)
        language = "python"
    elif ext == ".java":
        smells = _analyze_java_smells(source)
        metrics = _java_metrics(source, filename)
        language = "java"
    else:
        return {
            "file": filename,
            "language": "unknown",
            "error": f"Unsupported file type: '{ext}'",
        }

    severity_counts = {"high": 0, "medium": 0, "low": 0}
    for smell in smells:
        s = smell.get("severity", "medium")
        severity_counts[s] = severity_counts.get(s, 0) + 1

    return {
        "file": filename,
        "language": language,
        "metrics": metrics,
        "code_smells": smells,
        "smell_summary": severity_counts,
        "quality_score": _compute_score(smells, metrics),
    }


def generate_repo_report(file_reports: list[dict]) -> dict:
    """
    Aggregate per-file reports into a repository-level quality report.

    Args:
        file_reports: List of results from generate_file_report().

    Returns:
        Aggregated CUQA quality report.
    """
    total_loc = sum(r.get("metrics", {}).get("lines_of_code", 0) for r in file_reports)
    total_smells = sum(len(r.get("code_smells", [])) for r in file_reports)
    high_smells = sum(r.get("smell_summary", {}).get("high", 0) for r in file_reports)
    medium_smells = sum(r.get("smell_summary", {}).get("medium", 0) for r in file_reports)
    low_smells = sum(r.get("smell_summary", {}).get("low", 0) for r in file_reports)
    files_analyzed = len(file_reports)
    avg_score = (
        sum(r.get("quality_score", 100) for r in file_reports) / files_analyzed
        if files_analyzed else 100
    )

    return {
        "summary": {
            "files_analyzed": files_analyzed,
            "total_lines_of_code": total_loc,
            "total_code_smells": total_smells,
            "smell_severity": {
                "high": high_smells,
                "medium": medium_smells,
                "low": low_smells,
            },
            "average_quality_score": round(avg_score, 1),
        },
        "files": file_reports,
    }


def _compute_score(smells: list[dict], metrics: dict) -> float:
    """Compute a 0–100 quality score (higher = better)."""
    score = 100.0
    for smell in smells:
        severity = smell.get("severity", "medium")
        deduction = {"high": 8, "medium": 4, "low": 1}.get(severity, 2)
        score -= deduction
    return max(0.0, round(score, 1))
