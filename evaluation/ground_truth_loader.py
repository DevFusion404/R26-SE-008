"""
Java Ground Truth Loader & Validator (MLCQ & Standard Support)
--------------------------------------------------------------
Parses and validates MLCQCodeSmellSamples.csv and standard ground truth CSV files
specifically for Java code smell evaluation.
"""

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple
from collections import defaultdict

# Mapping MLCQ smell names to CUQA canonical smell types
MLCQ_SMELL_MAP = {
    "long method": "LongMethod",
    "blob": "LargeClass",
    "data class": "DataClass",
    "feature envy": "FeatureEnvy",
}


def parse_entity_name(code_name: str, entity_type: str) -> str:
    """Extracts simple class or method name from code_name string."""
    if not code_name:
        return ""
    
    # Handle method signature with # (e.g. org.pkg.Class#method)
    if "#" in code_name:
        parts = code_name.split("#")
        method_part = parts[1].split()[0]
        return method_part.split("(")[0]
    
    clean = code_name.split()[0]
    tokens = clean.split(".")
    if len(tokens) >= 1:
        return tokens[-1]
    return code_name


def load_mlcq_ground_truth(
    csv_path: str | Path,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Loads MLCQCodeSmellSamples.csv dataset."""
    path = Path(csv_path)
    quality_report: Dict[str, Any] = {
        "file_path": str(path),
        "total_rows_read": 0,
        "valid_records_count": 0,
        "errors": [],
        "warnings": [],
        "passed": False,
        "smell_distribution": {},
    }

    if not path.exists():
        quality_report["errors"].append(f"Ground truth file not found: '{path}'")
        return [], quality_report

    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample_line = f.readline()
        delimiter = ";" if ";" in sample_line else ","
        f.seek(0)

        reader = csv.DictReader(f, delimiter=delimiter)
        for row in reader:
            quality_report["total_rows_read"] += 1
            sample_id = (row.get("sample_id") or "").strip()
            smell_raw = (row.get("smell") or "").strip().lower()

            if not sample_id or not smell_raw:
                continue

            key = (sample_id, smell_raw)
            groups[key].append(row)

    records: List[Dict[str, Any]] = []

    for (sample_id, smell_raw), reviews in groups.items():
        canonical_smell = MLCQ_SMELL_MAP.get(smell_raw, smell_raw)
        first_row = reviews[0]

        reviewer_labels = [
            1 if (r.get("severity") or "").strip().lower() != "none" else 0
            for r in reviews
        ]
        consensus_label = 1 if sum(reviewer_labels) > (len(reviewer_labels) / 2) else 0

        r1_label = reviewer_labels[0] if len(reviewer_labels) >= 1 else None
        r2_label = reviewer_labels[1] if len(reviewer_labels) >= 2 else None

        raw_path = (first_row.get("path") or "").strip().replace("\\", "/")
        norm_path = raw_path.lstrip("/")

        raw_type = (first_row.get("type") or "").strip().lower()
        entity_type = "method" if raw_type in ("function", "method") else "class"
        entity_name = parse_entity_name(first_row.get("code_name", ""), entity_type)

        try:
            start_line = int(first_row["start_line"]) if (first_row.get("start_line") or "").strip() else None
            end_line = int(first_row["end_line"]) if (first_row.get("end_line") or "").strip() else start_line
        except ValueError:
            start_line = None
            end_line = None

        repo_url = (first_row.get("repository") or "").strip()
        repo_name = repo_url.split("/")[-1].replace(".git", "") if "/" in repo_url else repo_url

        record = {
            "sample_id": f"MLCQ_{sample_id}_{canonical_smell}",
            "original_sample_id": sample_id,
            "repository": repo_name,
            "language": "java",
            "file_path": norm_path,
            "entity_type": entity_type,
            "entity_name": entity_name,
            "code_name": first_row.get("code_name", ""),
            "start_line": start_line,
            "end_line": end_line,
            "smell_type": canonical_smell,
            "ground_truth": consensus_label,
            "consensus_label": consensus_label,
            "reviewer_1_label": r1_label,
            "reviewer_2_label": r2_label,
            "reviews_count": len(reviews),
            "commit_hash": first_row.get("commit_hash", ""),
            "link": first_row.get("link", ""),
        }
        records.append(record)

    quality_report["valid_records_count"] = len(records)
    quality_report["passed"] = len(records) > 0

    smell_dist = defaultdict(lambda: {"total": 0, "pos": 0, "neg": 0})
    for r in records:
        st = r["smell_type"]
        smell_dist[st]["total"] += 1
        if r["ground_truth"] == 1:
            smell_dist[st]["pos"] += 1
        else:
            smell_dist[st]["neg"] += 1

    quality_report["smell_distribution"] = dict(smell_dist)
    return records, quality_report


def load_standard_ground_truth(
    csv_path: str | Path,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Loads standard format ground truth CSV files."""
    path = Path(csv_path)
    quality_report: Dict[str, Any] = {
        "file_path": str(path),
        "total_rows_read": 0,
        "valid_records_count": 0,
        "errors": [],
        "warnings": [],
        "passed": False,
    }

    if not path.exists():
        quality_report["errors"].append(f"File not found: '{path}'")
        return [], quality_report

    records: List[Dict[str, Any]] = []

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            quality_report["total_rows_read"] += 1
            sample_id = (row.get("sample_id") or "").strip()
            gt_val_raw = str(row.get("ground_truth") or row.get("consensus_label") or "").strip()
            if not sample_id or gt_val_raw not in ("0", "1"):
                continue

            gt_val = int(gt_val_raw)
            rec = {
                "sample_id": sample_id,
                "repository": (row.get("repository") or "").strip(),
                "language": (row.get("language") or "java").strip().lower(),
                "file_path": (row.get("file_path") or "").strip().lstrip("/"),
                "entity_type": (row.get("entity_type") or "method").strip(),
                "entity_name": (row.get("entity_name") or "").strip(),
                "start_line": int(row["start_line"]) if (row.get("start_line") or "").strip() else None,
                "end_line": int(row["end_line"]) if (row.get("end_line") or "").strip() else None,
                "smell_type": (row.get("smell_type") or "").strip(),
                "ground_truth": gt_val,
                "consensus_label": gt_val,
                "reviewer_1_label": int(row["reviewer_1_label"]) if (row.get("reviewer_1_label") or "").strip() in ("0", "1") else None,
                "reviewer_2_label": int(row["reviewer_2_label"]) if (row.get("reviewer_2_label") or "").strip() in ("0", "1") else None,
            }
            records.append(rec)

    quality_report["valid_records_count"] = len(records)
    quality_report["passed"] = len(records) > 0
    return records, quality_report


def load_ground_truth(csv_path: str | Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Auto-detects format (MLCQ vs Standard CSV) and loads ground truth records."""
    path = Path(csv_path)
    if not path.exists():
        return [], {"file_path": str(path), "passed": False, "errors": [f"File not found: '{path}'"]}

    with open(path, "r", encoding="utf-8-sig") as f:
        first_line = f.readline()

    if "smell;" in first_line or "code_name" in first_line:
        return load_mlcq_ground_truth(csv_path)
    else:
        return load_standard_ground_truth(csv_path)
