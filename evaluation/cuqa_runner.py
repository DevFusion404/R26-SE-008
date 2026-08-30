"""
CUQA Execution Wrapper for Evaluation
-------------------------------------
Invokes CUQA report generation routines in read-only evaluation mode.
Guarantees zero modification to CUQA production rules or detection thresholds.
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# Add CUQA agent src path to sys.path dynamically
CUQA_SRC = Path(__file__).resolve().parent.parent / "agents" / "cuqa_agent" / "src"
if str(CUQA_SRC) not in sys.path:
    sys.path.insert(0, str(CUQA_SRC))

try:
    from report_generator import generate_file_report, generate_repo_report
except ImportError:
    # Alternative relative import if executed from within agents/cuqa_agent
    try:
        from agents.cuqa_agent.src.report_generator import generate_file_report, generate_repo_report
    except ImportError:
        generate_file_report = None
        generate_repo_report = None


def run_cuqa_on_repository(repo_path: str | Path) -> Dict[str, Any]:
    """
    Executes CUQA on a repository folder and returns the complete quality report JSON.

    Args:
        repo_path (str | Path): Path to the target repository.

    Returns:
        Dict[str, Any]: Complete CUQA quality report output.
    """
    path = Path(repo_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Target evaluation repository does not exist: '{path}'")

    if generate_file_report is None or generate_repo_report is None:
        raise ImportError("Failed to import CUQA report_generator functions.")

    file_reports: List[Dict[str, Any]] = []
    sources: List[tuple[str, str]] = []

    # Collect source files (.py, .java, .c, .h)
    for root, _, files in os.walk(path):
        for f in files:
            ext = os.path.splitext(f)[-1].lower()
            if ext in (".py", ".java", ".c", ".h"):
                full_p = Path(root) / f
                rel_p = str(full_p.relative_to(path)).replace("\\", "/")
                try:
                    with open(full_p, "r", encoding="utf-8", errors="replace") as file_obj:
                        src_text = file_obj.read()
                    
                    rep = generate_file_report(src_text, rel_p)
                    file_reports.append(rep)
                    if ext == ".py":
                        sources.append((rel_p, src_text))
                except Exception as err:
                    file_reports.append({
                        "file": rel_p,
                        "language": "unknown",
                        "error": str(err),
                        "code_smells": [],
                    })

    # Aggregate into repository report with cross-file python index
    repo_report = generate_repo_report(file_reports, sources=sources if sources else None)
    return repo_report
