"""
build_dataset.py
----------------
Joins the MLCQ expert labels with features extracted from the real code.

For every reviewed sample the script opens the cached source file, runs the
CUQA feature extractor (the same one used at inference time) and matches the
sample to the extracted class or method by its line range. The result is two
tables, one for class-level smells and one for method-level smells.

Label rule: a sample has a smell when more than half of its reviewers gave a
severity other than "none". This is the same consensus rule used by
evaluation/ground_truth_loader.py.

Usage:
    python training/build_dataset.py
"""

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

TRAINING_DIR = Path(__file__).resolve().parent
REPO_ROOT = TRAINING_DIR.parents[2]
SRC_DIR = TRAINING_DIR.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(TRAINING_DIR) not in sys.path:
    sys.path.insert(0, str(TRAINING_DIR))

from fetch_mlcq_sources import source_key  # noqa: E402
from ml_detector.java_features import (  # noqa: E402
    CLASS_FEATURES,
    METHOD_FEATURES,
    JavaFeatureError,
    extract_entities,
)

DEFAULT_CSV = REPO_ROOT / "evaluation" / "data" / "MLCQCodeSmellSamples.csv"
DEFAULT_CACHE = TRAINING_DIR / "data" / "mlcq_sources"
DEFAULT_OUT = TRAINING_DIR / "dataset"

# MLCQ smell name -> column suffix
SMELL_COLUMNS = {
    "blob": "blob",
    "data class": "data_class",
    "long method": "long_method",
    "feature envy": "feature_envy",
}
CLASS_SMELLS = ("blob", "data_class")
METHOD_SMELLS = ("long_method", "feature_envy")
SEVERITY_SCORE = {"none": 0, "minor": 1, "major": 2, "critical": 3}

# A sample is matched to an extracted entity when both ends of the line range
# are within this many lines. MLCQ ranges start at the first annotation.
LINE_TOLERANCE = 2

META_COLUMNS = [
    "sample_id", "repository", "commit_hash", "path", "code_name", "entity_name",
    "type_kind", "start_line", "end_line", "fingerprint",
]


def entity_name_from_code_name(code_name: str) -> str:
    """'pkg.Class#method int|String' -> 'method', 'pkg.Outer.Inner' -> 'Inner'."""
    head = (code_name or "").split(" ")[0]
    if "#" in head:
        return head.split("#", 1)[1]
    return head.rsplit(".", 1)[-1]


def load_samples(csv_path: Path) -> dict[str, dict]:
    """Group the MLCQ reviews into one record per code sample."""
    samples: dict[str, dict] = {}
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            sample_id = (row.get("sample_id") or "").strip()
            smell = SMELL_COLUMNS.get((row.get("smell") or "").strip().lower())
            severity = (row.get("severity") or "").strip().lower()
            if not sample_id or smell is None or severity not in SEVERITY_SCORE:
                continue
            sample = samples.setdefault(sample_id, {
                "sample_id": sample_id,
                "repository": row["repository"].strip(),
                "commit_hash": row["commit_hash"].strip(),
                "path": row["path"].strip(),
                "code_name": row["code_name"].strip(),
                "kind": "class" if row["type"].strip().lower() == "class" else "method",
                "start_line": int(row["start_line"]),
                "end_line": int(row["end_line"]),
                "reviews": defaultdict(list),
            })
            sample["reviews"][smell].append(SEVERITY_SCORE[severity])
    return samples


def label_columns(reviews: dict[str, list[int]], smells: tuple[str, ...]) -> dict:
    """Consensus label and review statistics for each smell of one sample."""
    out: dict = {}
    for smell in smells:
        scores = reviews.get(smell)
        if not scores:
            out[f"label_{smell}"] = ""
            out[f"n_reviews_{smell}"] = 0
            out[f"n_positive_{smell}"] = 0
            out[f"mean_severity_{smell}"] = ""
            continue
        positives = sum(1 for s in scores if s > 0)
        out[f"label_{smell}"] = 1 if positives > len(scores) / 2 else 0
        out[f"n_reviews_{smell}"] = len(scores)
        out[f"n_positive_{smell}"] = positives
        out[f"mean_severity_{smell}"] = round(sum(scores) / len(scores), 3)
    return out


def process_file(job: tuple[str, list[dict]]) -> dict:
    """Extract features for one source file and match its samples."""
    file_path, samples = job
    result = {"rows": [], "status": Counter()}
    try:
        source = Path(file_path).read_text(encoding="utf-8", errors="replace")
        entities = extract_entities(source, with_fingerprint=True)
    except JavaFeatureError:
        result["status"]["parse_failed"] += len(samples)
        return result

    by_kind = {"class": [], "method": []}
    for entity in entities:
        by_kind[entity.kind].append(entity)

    for sample in samples:
        best = None
        best_distance = None
        for entity in by_kind[sample["kind"]]:
            start_gap = abs(entity.start_line - sample["start_line"])
            end_gap = abs(entity.end_line - sample["end_line"])
            if start_gap > LINE_TOLERANCE or end_gap > LINE_TOLERANCE:
                continue
            distance = start_gap + end_gap
            if best_distance is None or distance < best_distance:
                best, best_distance = entity, distance
        if best is None:
            result["status"]["unmatched"] += 1
            continue

        result["status"]["matched"] += 1
        expected_name = entity_name_from_code_name(sample["code_name"])
        if expected_name and expected_name != best.name:
            result["status"]["matched_name_differs"] += 1
        smells = CLASS_SMELLS if sample["kind"] == "class" else METHOD_SMELLS
        row = {
            "kind": sample["kind"],
            "sample_id": sample["sample_id"],
            "repository": sample["repository"],
            "commit_hash": sample["commit_hash"],
            "path": sample["path"],
            "code_name": sample["code_name"],
            "entity_name": best.name,
            "type_kind": best.type_kind,
            "start_line": best.start_line,
            "end_line": best.end_line,
            "fingerprint": best.fingerprint,
        }
        row.update(label_columns(sample["reviews"], smells))
        row.update(best.features)
        result["rows"].append(row)
    return result


def write_table(path: Path, rows: list[dict], smells: tuple[str, ...], features: tuple[str, ...]) -> None:
    label_cols = []
    for smell in smells:
        label_cols += [f"label_{smell}", f"n_reviews_{smell}", f"n_positive_{smell}", f"mean_severity_{smell}"]
    columns = META_COLUMNS + label_cols + list(features)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in sorted(rows, key=lambda r: int(r["sample_id"])):
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the MLCQ feature tables.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    samples = load_samples(args.csv)
    print(f"MLCQ samples: {len(samples)}")

    jobs: dict[str, list[dict]] = defaultdict(list)
    status: Counter = Counter()
    for sample in samples.values():
        key = source_key(sample["repository"], sample["commit_hash"], sample["path"])
        file_path = args.cache / f"{key}.java"
        if not file_path.exists():
            status["source_unavailable"] += 1
            continue
        # defaultdict cannot cross a process boundary, send plain dicts
        jobs[str(file_path)].append(dict(sample, reviews=dict(sample["reviews"])))
    print(f"Source files to parse: {len(jobs)}")

    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for done, result in enumerate(pool.map(process_file, jobs.items(), chunksize=8), start=1):
            rows.extend(result["rows"])
            status.update(result["status"])
            if done % 500 == 0:
                print(f"  parsed {done}/{len(jobs)} files", flush=True)

    args.out.mkdir(parents=True, exist_ok=True)
    class_rows = [r for r in rows if r["kind"] == "class"]
    method_rows = [r for r in rows if r["kind"] == "method"]
    write_table(args.out / "mlcq_classes.csv", class_rows, CLASS_SMELLS, CLASS_FEATURES)
    write_table(args.out / "mlcq_methods.csv", method_rows, METHOD_SMELLS, METHOD_FEATURES)

    label_summary = {}
    for smell, table in (("blob", class_rows), ("data_class", class_rows),
                         ("long_method", method_rows), ("feature_envy", method_rows)):
        labeled = [r for r in table if r[f"label_{smell}"] != ""]
        positives = sum(1 for r in labeled if r[f"label_{smell}"] == 1)
        label_summary[smell] = {
            "samples": len(labeled),
            "positive": positives,
            "negative": len(labeled) - positives,
            "positive_rate": round(positives / len(labeled), 4) if labeled else 0.0,
        }

    report = {
        "mlcq_samples_total": len(samples),
        "source_files_parsed": len(jobs),
        "sample_status": dict(status),
        "class_rows": len(class_rows),
        "method_rows": len(method_rows),
        "repositories": len({r["repository"] for r in rows}),
        "line_tolerance": LINE_TOLERANCE,
        "labels": label_summary,
    }
    with open(args.out / "dataset_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
