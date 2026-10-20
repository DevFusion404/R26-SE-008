"""
build_dacos_dataset.py
----------------------
Joins the DACOS code smell labels with features extracted from the DACOS code.

DACOS (Nandani, Saad and Sharma, MSR 2023, https://doi.org/10.5281/zenodo.7570428)
labels three smells in ten open-source Java projects:

    Complex Method            (method)
    Long Parameter List       (method)
    Multifaceted Abstraction  (class)

It holds two kinds of labels, and this script keeps them apart:

  human      DACOS Main. Two people annotated each snippet whose metric value
             lies in the range where the smell is a judgement call (for example
             a cyclomatic complexity between about 5 and 10). Label rule: the
             smell is present when more than half of the annotators marked it,
             the same rule build_dataset.py uses for MLCQ.
  threshold  DACOSX. Snippets outside that range, labelled by the DACOS authors
             as definitely smelly or definitely benign with the metric
             thresholds they derived from their first annotation round.

DACOS stores each class and each method as a separate snippet. Method features
are taken from the enclosing class snippet, so that class context (fields,
other methods) is measured the same way as on an uploaded repository. The
method is found in the class by its token fingerprint.

Usage:
    download DACOSMain.sql, DACOSExtended.sql and files.zip into training/data/dacos/
    python training/build_dacos_dataset.py
"""

import argparse
import csv
import gzip
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

TRAINING_DIR = Path(__file__).resolve().parent
SRC_DIR = TRAINING_DIR.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ml_detector.java_features import (  # noqa: E402
    CLASS_FEATURES,
    METHOD_FEATURES,
    JavaFeatureError,
    extract_entities,
)

DEFAULT_DATA = TRAINING_DIR / "data" / "dacos"
DEFAULT_OUT = TRAINING_DIR / "dataset"

# smell column suffix -> (entity, code in sample.smells, annotation column,
#                         DACOSX "present" id, DACOSX "absent" id)
SMELLS = {
    "complex_method":           ("method", "1", "iscm", "3", "6"),
    "long_parameter_list":      ("method", "2", "islp", "2", "5"),
    "multifaceted_abstraction": ("class",  "3", "isma", "1", "4"),
}
METHOD_SMELLS = ("complex_method", "long_parameter_list")
CLASS_SMELLS = ("multifaceted_abstraction",)

META_COLUMNS = [
    "sample_id", "repository", "path", "entity_name", "type_kind",
    "start_line", "end_line", "fingerprint", "context",
]

# The snippet files hold one method each. Wrapping a method in a class makes it
# parseable; the wrapper adds no tokens inside the method's own span.
_WRAPPER_HEAD = "class DacosSnippetWrapper {\n"
_WRAPPER_TAIL = "\n}\n"


# ---------------------------------------------------------------------------
# MySQL dump reader
# ---------------------------------------------------------------------------

_SQL_ESCAPES = {"0": "\0", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a", "b": "\b"}
_BARE_TOKEN = re.compile(r"[^,)]+")


def _parse_values(text: str, pos: int):
    """Yield the row tuples of one INSERT ... VALUES statement starting at *pos*."""
    n = len(text)
    while pos < n:
        c = text[pos]
        if c == ";":
            return
        if c != "(":
            pos += 1
            continue
        row, pos = [], pos + 1
        while True:
            if text.startswith("_binary '", pos):
                pos += 8  # bit(1) columns: _binary '<one raw byte>'
            if text[pos] == "'":
                pos += 1
                buf = []
                while True:
                    ch = text[pos]
                    if ch == "\\":
                        buf.append(_SQL_ESCAPES.get(text[pos + 1], text[pos + 1]))
                        pos += 2
                    elif ch == "'" and text[pos + 1] == "'":
                        buf.append("'")
                        pos += 2
                    elif ch == "'":
                        pos += 1
                        break
                    else:
                        buf.append(ch)
                        pos += 1
                row.append("".join(buf))
            else:
                m = _BARE_TOKEN.match(text, pos)
                token = m.group(0)
                row.append(None if token == "NULL" else token)
                pos = m.end()
            if text[pos] == ",":
                pos += 1
                continue
            pos += 1  # closing ")"
            break
        yield tuple(row)


def read_dump(path: Path) -> dict[str, list[dict]]:
    """Every table of a mysqldump file as a list of row dicts."""
    text = path.read_text(encoding="utf-8", errors="replace")
    columns = {
        m.group(1): re.findall(r"^\s*`(\w+)`", m.group(2), re.M)
        for m in re.finditer(r"CREATE TABLE `(\w+)` \((.*?)\n\) ENGINE", text, re.S)
    }
    tables: dict[str, list[dict]] = {name: [] for name in columns}
    for m in re.finditer(r"INSERT INTO `(\w+)` VALUES ", text):
        names = columns[m.group(1)]
        tables[m.group(1)].extend(dict(zip(names, row)) for row in _parse_values(text, m.end()))
    return tables


def _bit(value) -> int:
    """MySQL bit(1) columns are dumped as the bytes 0x00 / 0x01."""
    return 1 if value in ("\x01", "1") else 0


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

def collect_labels(main: dict, extended: dict) -> tuple[dict, Counter]:
    """Label of every (sample, smell) pair, with its source.

    Returns {sample_id: {"sample": row, "labels": {smell: {...}}}} and status counts.
    """
    status: Counter = Counter()
    samples = {row["id"]: row for row in main["sample"]}
    out: dict[str, dict] = {}

    annotations = defaultdict(list)
    for row in main["annotation"]:
        annotations[row["sample_id"]].append(row)

    for sample_id, rows in annotations.items():
        sample = samples.get(sample_id)
        if sample is None:
            status["annotation_without_sample"] += 1
            continue
        asked = [code for code in (sample["smells"] or "").split(",") if code]
        if not asked:
            status["annotated_without_smell_code"] += 1
            continue
        for smell, (entity, code, column, _, _) in SMELLS.items():
            if code not in asked:
                continue
            if (entity == "class") != bool(_bit(sample["is_class"])):
                status[f"{smell}_human_wrong_entity"] += 1
                continue
            positives = sum(_bit(r[column]) for r in rows)
            out.setdefault(sample_id, {"sample": sample, "labels": {}})["labels"][smell] = {
                "label": 1 if positives > len(rows) / 2 else 0,
                "source": "human",
                "n_reviews": len(rows),
                "n_positive": positives,
            }
            status[f"{smell}_human"] += 1

    for row in extended["entry"]:
        sample = samples.get(row["sampleId"])
        if sample is None:
            status["threshold_without_sample"] += 1
            continue
        for smell, (entity, _, _, present_id, absent_id) in SMELLS.items():
            if row["smellPresented"] not in (present_id, absent_id):
                continue
            # DACOSX also lists method smells against class snippets and the
            # other way round; those pairs cannot be measured and are skipped.
            if (entity == "class") != bool(_bit(sample["is_class"])):
                status[f"{smell}_threshold_wrong_entity"] += 1
                break
            record = out.setdefault(row["sampleId"], {"sample": sample, "labels": {}})
            if smell in record["labels"]:
                status[f"{smell}_threshold_also_human"] += 1
                break
            record["labels"][smell] = {
                "label": 1 if row["smellPresented"] == present_id else 0,
                "source": "threshold",
                "n_reviews": 0,
                "n_positive": 0,
            }
            status[f"{smell}_threshold"] += 1
            break
    return out, status


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

def _zip_name(path_to_file: str) -> str:
    return path_to_file.lstrip("/")


def class_file_of(sample: dict) -> str:
    """Zip member of the class snippet that holds a sample."""
    parts = _zip_name(sample["path_to_file"]).split("/")
    if _bit(sample["is_class"]):
        return "/".join(parts)
    # codesplit_java_method/<p>/<p>/<package>/<Type>/<method>.code
    # -> codesplit_java_class/<p>/<p>/<package>/<Type>.code
    return "/".join(["codesplit_java_class"] + parts[1:-2] + [parts[-2] + ".code"])


def _read(archive: zipfile.ZipFile, name: str) -> str | None:
    try:
        return archive.read(name).decode("utf-8", errors="replace")
    except KeyError:
        return None


# One open archive per worker process: reading the directory of 117,000 entries
# for every class snippet would take longer than the parsing itself.
_ARCHIVES: dict[str, zipfile.ZipFile] = {}


def _archive(zip_path: str) -> zipfile.ZipFile:
    if zip_path not in _ARCHIVES:
        _ARCHIVES[zip_path] = zipfile.ZipFile(zip_path)
    return _ARCHIVES[zip_path]


def process_class_file(job: tuple[str, str, list[dict]]) -> dict:
    """Extract features for every sample that lives in one class snippet."""
    zip_path, class_file, records = job
    result = {"rows": [], "status": Counter()}
    archive = _archive(zip_path)
    class_source = _read(archive, class_file)
    class_entities = None
    if class_source is not None:
        try:
            class_entities = extract_entities(class_source, with_fingerprint=True)
        except JavaFeatureError:
            result["status"]["class_parse_failed"] += len(records)

    for record in records:
        sample = record["sample"]
        is_class = bool(_bit(sample["is_class"]))
        entity, context = None, ""
        if is_class:
            if class_entities is None:
                result["status"]["class_unavailable"] += 1
                continue
            top = [e for e in class_entities if e.kind == "class" and e.parent is None]
            entity, context = (top[0] if top else None), "class_file"
        else:
            snippet = _read(archive, _zip_name(sample["path_to_file"]))
            if snippet is None:
                result["status"]["method_snippet_missing"] += 1
                continue
            try:
                wrapped = extract_entities(_WRAPPER_HEAD + snippet + _WRAPPER_TAIL, with_fingerprint=True)
            except JavaFeatureError:
                result["status"]["method_parse_failed"] += 1
                continue
            own = [e for e in wrapped if e.kind == "method" and e.parent == "DacosSnippetWrapper"]
            if not own:
                result["status"]["method_not_found_in_snippet"] += 1
                continue
            target = own[0]
            if class_entities is not None:
                matches = [e for e in class_entities
                           if e.kind == "method" and e.fingerprint == target.fingerprint]
                if matches:
                    entity, context = matches[0], "class_file"
            if entity is None:
                # No enclosing class snippet: the class context features
                # would describe the wrapper, so the sample is left out.
                result["status"]["method_not_in_class_file"] += 1
                continue
        if entity is None:
            result["status"]["class_entity_missing"] += 1
            continue

        row = {
            "kind": "class" if is_class else "method",
            "sample_id": sample["id"],
            "repository": sample["project_name"],
            "path": sample["path_to_file"],
            "entity_name": entity.name,
            "type_kind": entity.type_kind,
            "start_line": entity.start_line,
            "end_line": entity.end_line,
            "fingerprint": entity.fingerprint,
            "context": context,
        }
        smells = CLASS_SMELLS if is_class else METHOD_SMELLS
        for smell in smells:
            label = record["labels"].get(smell)
            row[f"label_{smell}"] = "" if label is None else label["label"]
            row[f"label_source_{smell}"] = "" if label is None else label["source"]
            row[f"n_reviews_{smell}"] = 0 if label is None else label["n_reviews"]
            row[f"n_positive_{smell}"] = 0 if label is None else label["n_positive"]
        row.update(entity.features)
        result["rows"].append(row)
        result["status"]["matched"] += 1
    return result


def write_table(path: Path, rows: list[dict], smells: tuple[str, ...], features: tuple[str, ...]) -> None:
    label_cols = []
    for smell in smells:
        label_cols += [f"label_{smell}", f"label_source_{smell}", f"n_reviews_{smell}", f"n_positive_{smell}"]
    columns = META_COLUMNS + label_cols + list(features)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in sorted(rows, key=lambda r: int(r["sample_id"])):
            writer.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the DACOS feature tables.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    print("Reading the DACOS database dumps ...")
    main_db = read_dump(args.data / "DACOSMain.sql")
    extended_db = read_dump(args.data / "DACOSExtended.sql")
    labelled, status = collect_labels(main_db, extended_db)
    print(f"Samples with at least one label: {len(labelled)}")

    zip_path = str(args.data / "files.zip")
    jobs: dict[str, list[dict]] = defaultdict(list)
    for record in labelled.values():
        jobs[class_file_of(record["sample"])].append(record)
    print(f"Class snippets to parse: {len(jobs)}")

    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        work = ((zip_path, name, records) for name, records in jobs.items())
        for done, result in enumerate(pool.map(process_class_file, work, chunksize=16), start=1):
            rows.extend(result["rows"])
            status.update(result["status"])
            if done % 2000 == 0:
                print(f"  parsed {done}/{len(jobs)} class snippets", flush=True)

    args.out.mkdir(parents=True, exist_ok=True)
    class_rows = [r for r in rows if r["kind"] == "class"]
    method_rows = [r for r in rows if r["kind"] == "method"]
    write_table(args.out / "dacos_classes.csv.gz", class_rows, CLASS_SMELLS, CLASS_FEATURES)
    write_table(args.out / "dacos_methods.csv.gz", method_rows, METHOD_SMELLS, METHOD_FEATURES)

    label_summary = {}
    for smell, table in (("complex_method", method_rows), ("long_parameter_list", method_rows),
                         ("multifaceted_abstraction", class_rows)):
        summary = {}
        for source in ("human", "threshold"):
            labelled_rows = [r for r in table if r[f"label_source_{smell}"] == source]
            positives = sum(1 for r in labelled_rows if r[f"label_{smell}"] == 1)
            summary[source] = {
                "samples": len(labelled_rows),
                "positive": positives,
                "negative": len(labelled_rows) - positives,
                "positive_rate": round(positives / len(labelled_rows), 4) if labelled_rows else 0.0,
            }
        human = [r for r in table if r[f"label_source_{smell}"] == "human"]
        summary["human"]["annotator_split_votes"] = sum(
            1 for r in human if 0 < r[f"n_positive_{smell}"] < r[f"n_reviews_{smell}"]
        )
        label_summary[smell] = summary

    report = {
        "dataset": "DACOS (Nandani, Saad and Sharma, MSR 2023), doi:10.5281/zenodo.7570428",
        "labelled_samples": len(labelled),
        "class_snippets_parsed": len(jobs),
        "sample_status": dict(sorted(status.items())),
        "class_rows": len(class_rows),
        "method_rows": len(method_rows),
        "repositories": sorted({r["repository"] for r in rows}),
        "labels": label_summary,
    }
    with open(args.out / "dacos_dataset_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
