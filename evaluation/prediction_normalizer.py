"""
CUQA Prediction Normalizer
--------------------------
Standardizes CUQA JSON reports into unified prediction objects with
normalized file paths, entity types, entity names, and line ranges.
"""

import os
import re
from typing import Any, Dict, List

# Mapping smell types to entity granularities
SMELL_ENTITY_TYPE_MAP = {
    "LongMethod": "function",
    "LongFunction": "function",
    "TooManyParameters": "function",
    "SwitchStatements": "function",
    "MessageChains": "function",
    "UnreachableCode": "function",
    "UnusedVariable": "function",
    "DeepNesting": "function",
    "FeatureEnvy": "method",
    "RefusedBequest": "method",
    "LargeClass": "class",
    "LazyClass": "class",
    "PrimitiveObsession": "class",
    "InappropriateIntimacy": "class",
    "SpeculativeGenerality": "class",
    "DataClass": "class",
    "TemporaryField": "class",
    "GlobalVariable": "declaration",
    "UnsafeFunctionUsage": "function_call",
    "MagicNumber": "occurrence",
    "BareExcept": "occurrence",
    "DeadCode": "function",
    "DuplicateCode": "function",
    "LargeHeaderFile": "file",
    "Comments": "file",
}


def normalize_path(path_str: str) -> str:
    """Normalizes Windows/Linux file paths to relative forward-slash paths."""
    if not path_str:
        return ""
    p = path_str.replace("\\", "/").strip()
    p = re.sub(r"^([A-Za-z]:)?/+", "", p)  # Strip drive letters or leading slashes
    p = re.sub(r"/+", "/", p)              # Collapse multiple slashes
    p = p.lstrip("/")
    return p


def normalize_cuqa_predictions(cuqa_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parses a CUQA quality report dict and returns a list of normalized prediction records.

    Args:
        cuqa_report (Dict[str, Any]): CUQA output JSON dict.

    Returns:
        List[Dict[str, Any]]: Normalized predictions.
    """
    normalized: List[Dict[str, Any]] = []

    files = cuqa_report.get("files", [])
    for file_rep in files:
        raw_file = file_rep.get("file", "")
        norm_file = normalize_path(raw_file)
        lang = (file_rep.get("language") or "").strip().lower()

        for smell in file_rep.get("code_smells", []):
            smell_type = (smell.get("type") or "").strip()
            entity_name = (smell.get("entity") or "").strip()
            entity_type = SMELL_ENTITY_TYPE_MAP.get(smell_type, "function")

            line = smell.get("line")
            start_line = smell.get("start_line", line)
            end_line = smell.get("end_line", start_line)

            pred_record = {
                "file_path": norm_file,
                "language": lang,
                "smell_type": smell_type,
                "entity_type": entity_type,
                "entity_name": entity_name,
                "line": line,
                "start_line": start_line,
                "end_line": end_line,
                "severity": smell.get("severity", "medium"),
                "message": smell.get("message", ""),
                "raw_smell": smell,
            }
            normalized.append(pred_record)

    return normalized
