"""
export_preprocessed.py
----------------------
Writes the preprocessed datasets of the two models as plain CSV files, one per
smell, exactly as the training scripts use them:

    dataset/preprocessed/mlcq/<smell>.csv    model 1, train_model.py
    dataset/preprocessed/dacos/<smell>.csv   model 2, train_dacos.py

Each file holds the labelled samples of one smell after every preprocessing
step (code matched to the label, features extracted, label consensus applied,
copies of the same code removed), with a `split` column that says whether the
sample was in the training or the test repositories. The rows and the split
are produced by the same functions the training scripts call, so they cannot
drift apart.

Usage:
    python training/build_dataset.py        (MLCQ tables, already committed)
    python training/build_dacos_dataset.py  (DACOS tables, already committed)
    python training/export_preprocessed.py
"""

import json
import sys
from pathlib import Path

import pandas as pd

TRAINING_DIR = Path(__file__).resolve().parent
SRC_DIR = TRAINING_DIR.parent / "src"
for _path in (SRC_DIR, TRAINING_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from ml_detector.java_features import CLASS_FEATURES, METHOD_FEATURES  # noqa: E402
from train_dacos import SMELLS as DACOS_SMELLS, labelled_rows  # noqa: E402
from train_model import SMELLS as MLCQ_SMELLS, load_table, split_repositories  # noqa: E402

DATASET_DIR = TRAINING_DIR / "dataset"
OUT_DIR = DATASET_DIR / "preprocessed"
FEATURES = {"class": list(CLASS_FEATURES), "method": list(METHOD_FEATURES)}


def _write(table: pd.DataFrame, meta: list[str], entity: str, path: Path) -> None:
    columns = meta + FEATURES[entity]
    table[columns].round(6).to_csv(path, index=False)


def _keep_names(table: pd.DataFrame, source: Path) -> pd.DataFrame:
    """pandas reads a class called `None` or `NA` as a missing value; restore the real names."""
    names = pd.read_csv(source, usecols=["sample_id", "entity_name"], keep_default_na=False, dtype=str)
    by_id = dict(zip(names["sample_id"].astype(int), names["entity_name"]))
    table["entity_name"] = table["sample_id"].astype(int).map(by_id)
    return table


def export_mlcq() -> list[dict]:
    tables = {"class": load_table("mlcq_classes.csv"), "method": load_table("mlcq_methods.csv")}
    train_repos, _ = split_repositories(*tables.values())
    (OUT_DIR / "mlcq").mkdir(parents=True, exist_ok=True)
    summary = []
    for spec in MLCQ_SMELLS:
        table = tables[spec.entity]
        table = table[table[f"label_{spec.key}"].notna()].reset_index(drop=True).copy()
        table = _keep_names(table, DATASET_DIR / f"mlcq_{'classes' if spec.entity == 'class' else 'methods'}.csv")
        table["split"] = table["repository"].map(lambda r: "train" if r in train_repos else "test")
        table["label"] = table[f"label_{spec.key}"].astype(int)
        table["n_reviews"] = table[f"n_reviews_{spec.key}"].astype(int)
        table["n_positive_reviews"] = table[f"n_positive_{spec.key}"].astype(int)
        table["mean_severity"] = table[f"mean_severity_{spec.key}"]
        meta = ["sample_id", "repository", "commit_hash", "path", "entity_name", "type_kind",
                "start_line", "end_line", "split", "label", "n_reviews", "n_positive_reviews", "mean_severity"]
        _write(table, meta, spec.entity, OUT_DIR / "mlcq" / f"{spec.key}.csv")
        summary += _counts("MLCQ", spec.key, spec.display_name, spec.entity, table)
    return summary


def export_dacos() -> list[dict]:
    tables = {"class": pd.read_csv(DATASET_DIR / "dacos_classes.csv.gz", low_memory=False),
              "method": pd.read_csv(DATASET_DIR / "dacos_methods.csv.gz", low_memory=False)}
    train_repos, _ = split_repositories(*tables.values())
    (OUT_DIR / "dacos").mkdir(parents=True, exist_ok=True)
    summary = []
    for spec in DACOS_SMELLS:
        table = labelled_rows(tables[spec.entity], spec.key).copy()
        table = _keep_names(table, DATASET_DIR / f"dacos_{'classes' if spec.entity == 'class' else 'methods'}.csv.gz")
        table["split"] = table["repository"].map(lambda r: "train" if r in train_repos else "test")
        table["label"] = table[f"label_{spec.key}"].astype(int)
        table["label_source"] = table[f"label_source_{spec.key}"]
        table["n_reviews"] = table[f"n_reviews_{spec.key}"].astype(int)
        table["n_positive_reviews"] = table[f"n_positive_{spec.key}"].astype(int)
        meta = ["sample_id", "repository", "path", "entity_name", "type_kind", "start_line", "end_line",
                "split", "label", "label_source", "n_reviews", "n_positive_reviews"]
        _write(table, meta, spec.entity, OUT_DIR / "dacos" / f"{spec.key}.csv")
        summary += _counts("DACOS", spec.key, spec.display_name, spec.entity, table)
    return summary


def _counts(dataset: str, key: str, name: str, entity: str, table: pd.DataFrame) -> list[dict]:
    rows = []
    for split in ("train", "test"):
        part = table[table["split"] == split]
        row = {
            "dataset": dataset, "smell": key, "display_name": name, "entity": entity, "split": split,
            "samples": len(part), "positive": int(part["label"].sum()),
            "repositories": part["repository"].nunique(),
        }
        if "label_source" in part:
            human = part[part["label_source"] == "human"]
            row["human_samples"] = len(human)
            row["human_positive"] = int(human["label"].sum())
        rows.append(row)
    return rows


def main() -> int:
    summary = export_mlcq() + export_dacos()
    frame = pd.DataFrame(summary)
    frame.to_csv(OUT_DIR / "summary.csv", index=False)
    print(frame.to_string(index=False))
    sizes = {str(p.relative_to(OUT_DIR)): round(p.stat().st_size / 1e6, 2) for p in sorted(OUT_DIR.rglob("*.csv"))}
    print(json.dumps(sizes, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
