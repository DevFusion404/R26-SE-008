"""
train_model.py
--------------
Trains and evaluates the CUQA code smell model on the MLCQ feature tables.

Protocol:
  1. Repositories are split 80/20. No repository contributes samples to both
     parts, so the test score measures detection on projects never seen before.
  2. On the training repositories, three model families are compared with
     repeated repository-grouped cross-validation.
  3. The decision threshold of the chosen model is tuned on the out-of-fold
     training predictions, never on the test set.
  4. The final model is evaluated once on the test repositories and compared
     with the existing CUQA rules and with the best single-metric threshold.
  5. The model is exported to JSON and its pure-Python predictions are checked
     against scikit-learn.

Usage:
    python training/build_dataset.py
    python training/train_model.py
"""

import argparse
import csv
import json
import math
import sys
import warnings
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

TRAINING_DIR = Path(__file__).resolve().parent
REPO_ROOT = TRAINING_DIR.parents[2]
SRC_DIR = TRAINING_DIR.parent / "src"
for _path in (SRC_DIR, TRAINING_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fetch_mlcq_sources import source_key  # noqa: E402
from ml_detector.java_features import CLASS_FEATURES, METHOD_FEATURES  # noqa: E402
from ml_detector.model import MODEL_FORMAT, SUPPORTED_FORMAT_VERSION, SmellModel  # noqa: E402

DATASET_DIR = TRAINING_DIR / "dataset"
RESULTS_DIR = TRAINING_DIR / "results"
SOURCE_CACHE = TRAINING_DIR / "data" / "mlcq_sources"
RULE_CACHE = TRAINING_DIR / "data" / "cuqa_rule_output.json"
MLCQ_CSV = REPO_ROOT / "evaluation" / "data" / "MLCQCodeSmellSamples.csv"
MODEL_OUT = SRC_DIR / "ml_detector" / "artifacts" / "smell_model.json"

RANDOM_SEED = 42
TEST_FRACTION = 0.20
CV_FOLDS = 5
CV_REPEATS = 3
BOOTSTRAP_ITERATIONS = 1000

# A smell is switched on in the shipped model only if it reaches this
# cross-validated F1 on the training repositories.
MIN_CV_F1_TO_ENABLE = 0.50

GB_PARAMS = dict(n_estimators=150, max_depth=2, learning_rate=0.05, subsample=0.8)


@dataclass(frozen=True)
class SmellSpec:
    key: str            # column suffix in the dataset tables
    smell_type: str     # CUQA smell type emitted in reports
    display_name: str
    mlcq_name: str
    entity: str         # "class" | "method"
    severity: str
    rule_type: str | None   # smell type of the existing CUQA Java rule, if one exists


SMELLS = (
    SmellSpec("long_method", "LongMethod", "Long Method", "long method", "method", "high", "LongMethod"),
    SmellSpec("feature_envy", "FeatureEnvy", "Feature Envy", "feature envy", "method", "medium", None),
    SmellSpec("blob", "LargeClass", "Blob (Large Class)", "blob", "class", "high", "LargeClass"),
    SmellSpec("data_class", "DataClass", "Data Class", "data class", "class", "low", None),
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def make_gradient_boosting(seed: int = RANDOM_SEED) -> GradientBoostingClassifier:
    return GradientBoostingClassifier(random_state=seed, **GB_PARAMS)


def candidate_models(seed: int) -> dict:
    return {
        "Logistic Regression": make_pipeline(
            FunctionTransformer(np.log1p),
            StandardScaler(),
            LogisticRegression(C=0.3, class_weight="balanced", max_iter=2000),
        ),
        "Random Forest": RandomForestClassifier(
            n_estimators=300, min_samples_leaf=3, class_weight="balanced_subsample",
            random_state=seed,
        ),
        "Gradient Boosting": make_gradient_boosting(seed),
    }


def out_of_fold(make_model, X, y, groups, seed: int) -> np.ndarray:
    """Predictions for every training row from a model that did not see its repository."""
    oof = np.zeros(len(y))
    cv = StratifiedGroupKFold(n_splits=CV_FOLDS, shuffle=True, random_state=seed)
    for train_idx, valid_idx in cv.split(X, y, groups):
        model = make_model(seed)
        model.fit(X[train_idx], y[train_idx])
        oof[valid_idx] = model.predict_proba(X[valid_idx])[:, 1]
    return oof


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def best_f1_threshold(y, proba) -> tuple[float, float]:
    precision, recall, thresholds = precision_recall_curve(y, proba)
    f1 = 2 * precision * recall / np.maximum(precision + recall, 1e-12)
    best = int(np.argmax(f1[:-1]))
    return float(thresholds[best]), float(f1[best])


def confusion_counts(y, pred) -> tuple[int, int, int, int]:
    y = np.asarray(y).astype(bool)
    pred = np.asarray(pred).astype(bool)
    tp = int(np.sum(y & pred))
    fp = int(np.sum(~y & pred))
    fn = int(np.sum(y & ~pred))
    tn = int(np.sum(~y & ~pred))
    return tp, fp, fn, tn


def threshold_metrics(y, pred) -> dict:
    tp, fp, fn, tn = confusion_counts(y, pred)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    denom = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    mcc = (tp * tn - fp * fn) / denom if denom else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": f1,
        "specificity": specificity,
        "accuracy": (tp + tn) / max(tp + fp + fn + tn, 1),
        "balanced_accuracy": (recall + specificity) / 2,
        "mcc": mcc,
    }


def ranking_metrics(y, proba) -> dict:
    return {
        "roc_auc": float(roc_auc_score(y, proba)),
        "pr_auc": float(average_precision_score(y, proba)),
        "brier": float(brier_score_loss(y, proba)),
    }


def cluster_bootstrap(y, pred, proba, groups, iterations: int, seed: int) -> dict:
    """95% intervals from resampling whole repositories with replacement.

    Samples from one repository are not independent, so repositories are the
    resampling unit. Resampling single rows would give intervals that are too narrow.
    """
    rng = np.random.RandomState(seed)
    rows_by_group = defaultdict(list)
    for i, g in enumerate(groups):
        rows_by_group[g].append(i)
    group_rows = [np.array(rows) for rows in rows_by_group.values()]

    collected = defaultdict(list)
    for _ in range(iterations):
        picked = rng.randint(0, len(group_rows), len(group_rows))
        idx = np.concatenate([group_rows[i] for i in picked])
        yb = y[idx]
        if yb.sum() == 0 or yb.sum() == len(yb):
            continue
        for name, value in threshold_metrics(yb, pred[idx]).items():
            if name in ("precision", "recall", "f1", "mcc"):
                collected[name].append(value)
        if proba is not None:
            collected["pr_auc"].append(average_precision_score(yb, proba[idx]))
            collected["roc_auc"].append(roc_auc_score(yb, proba[idx]))
    return {
        name: [float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))]
        for name, values in collected.items()
    }


def paired_f1_difference(y, pred_a, pred_b, groups, iterations: int, seed: int) -> dict:
    """F1 of detector A minus F1 of detector B, with a 95% interval.

    Both detectors are scored on the same repository resamples, so the interval
    describes the difference itself and not two separate uncertainties.
    """
    rng = np.random.RandomState(seed)
    rows_by_group = defaultdict(list)
    for i, g in enumerate(groups):
        rows_by_group[g].append(i)
    group_rows = [np.array(rows) for rows in rows_by_group.values()]

    differences = []
    for _ in range(iterations):
        picked = rng.randint(0, len(group_rows), len(group_rows))
        idx = np.concatenate([group_rows[i] for i in picked])
        if y[idx].sum() == 0:
            continue
        differences.append(threshold_metrics(y[idx], pred_a[idx])["f1"]
                           - threshold_metrics(y[idx], pred_b[idx])["f1"])
    return {
        "difference": threshold_metrics(y, pred_a)["f1"] - threshold_metrics(y, pred_b)["f1"],
        "ci95": [float(np.percentile(differences, 2.5)), float(np.percentile(differences, 97.5))],
    }


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def best_single_metric_rule(X_train, y_train, feature_names) -> dict:
    """Strongest rule of the form 'metric >= t' or 'metric <= t' on the training data."""
    best = {"f1": -1.0}
    for j, name in enumerate(feature_names):
        column = X_train[:, j]
        for value in np.unique(column):
            for direction in (">=", "<="):
                pred = column >= value if direction == ">=" else column <= value
                tp = np.sum(pred & (y_train == 1))
                if tp == 0:
                    continue
                f1 = 2 * tp / (pred.sum() + y_train.sum())
                if f1 > best["f1"]:
                    best = {"feature": name, "index": j, "direction": direction,
                            "threshold": float(value), "f1": float(f1)}
    return best


def apply_single_metric_rule(rule: dict, X) -> np.ndarray:
    column = X[:, rule["index"]]
    return column >= rule["threshold"] if rule["direction"] == ">=" else column <= rule["threshold"]


def _rule_smells_for_file(file_path: str) -> list[dict]:
    """Run the existing CUQA Java rules on one cached source file.

    Only LongMethod and LargeClass are needed here.
    """
    import report_generator
    try:
        source = Path(file_path).read_text(encoding="utf-8", errors="replace")
        smells = report_generator._analyze_java_smells(source)
    except Exception:
        smells = []
    return [
        {"type": s.get("type"), "entity": s.get("entity"), "line": s.get("line")}
        for s in smells if s.get("type") in ("LongMethod", "LargeClass")
    ]


def cuqa_rule_predictions(tables: dict[str, pd.DataFrame]) -> dict[str, list[dict]] | None:
    """CUQA rule output for every test source file, or None if the source cache is absent.

    The output is kept in a JSON cache next to the downloaded sources and
    reused on later runs.
    """
    keys: set[str] = set()
    for table in tables.values():
        for row in table.itertuples():
            key = source_key(row.repository, row.commit_hash, row.path)
            if (SOURCE_CACHE / f"{key}.java").exists():
                keys.add(key)
    if not keys:
        return None

    cached: dict[str, list[dict]] = {}
    if RULE_CACHE.exists():
        cached = json.loads(RULE_CACHE.read_text(encoding="utf-8"))
    missing = sorted(keys - set(cached))
    for done, key in enumerate(missing, start=1):
        cached[key] = _rule_smells_for_file(str(SOURCE_CACHE / f"{key}.java"))
        if done % 100 == 0 or done == len(missing):
            RULE_CACHE.write_text(json.dumps(cached), encoding="utf-8")
            print(f"  rules run on {done}/{len(missing)} files", flush=True)
    return {key: cached[key] for key in keys}


def rule_labels(table: pd.DataFrame, rule_output: dict[str, list[dict]], rule_type: str) -> np.ndarray:
    """1 where the CUQA rule flags the sampled entity with *rule_type*."""
    flags = np.zeros(len(table), dtype=int)
    for i, row in enumerate(table.itertuples()):
        key = source_key(row.repository, row.commit_hash, row.path)
        for smell in rule_output.get(key, []):
            line = smell.get("line")
            if (smell["type"] == rule_type and smell["entity"] == row.entity_name
                    and line is not None and row.start_line <= line <= row.end_line):
                flags[i] = 1
                break
    return flags


def reviewer_agreement() -> dict:
    """How often two MLCQ reviewers of the same sample agree, per smell.

    Only samples with at least two reviews can be used, and those were chosen
    for re-review, so they contain more smells than the dataset as a whole.
    """
    reviews = pd.read_csv(MLCQ_CSV, sep=";")
    reviews["positive"] = (reviews["severity"] != "none").astype(int)
    result = {}
    for smell, group in reviews.groupby("smell"):
        both_pos = both_neg = differ = 0
        for labels in group.groupby("sample_id")["positive"].apply(list):
            n, k = len(labels), sum(labels)
            if n < 2:
                continue
            both_pos += k * (k - 1) // 2
            both_neg += (n - k) * (n - k - 1) // 2
            differ += k * (n - k)
        pairs = both_pos + both_neg + differ
        if not pairs:
            continue
        observed = (both_pos + both_neg) / pairs
        p_pos = (2 * both_pos + differ) / (2 * pairs)
        expected = p_pos ** 2 + (1 - p_pos) ** 2
        result[smell] = {
            "review_pairs": int(pairs),
            "observed_agreement": observed,
            "positive_agreement_f1": 2 * both_pos / (2 * both_pos + differ) if both_pos + differ else 0.0,
            "cohen_kappa": (observed - expected) / (1 - expected) if expected < 1 else 0.0,
        }
    return result


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_classifier(clf: GradientBoostingClassifier, X_reference) -> dict:
    """Serialise a fitted binary gradient boosting model to plain lists."""
    init_score = float(np.ravel(clf._raw_predict_init(X_reference[:1]))[0])
    trees = []
    for estimator in clf.estimators_[:, 0]:
        tree = estimator.tree_
        trees.append({
            "feature": [int(f) if f >= 0 else -1 for f in tree.feature],
            "threshold": [float(t) if f >= 0 else 0.0 for f, t in zip(tree.feature, tree.threshold)],
            "left": [int(v) for v in tree.children_left],
            "right": [int(v) for v in tree.children_right],
            "value": [float(v) for v in tree.value[:, 0, 0]],
        })
    return {"init_score": init_score, "learning_rate": float(clf.learning_rate), "trees": trees}


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def plot_curves(results: dict, path_pr: Path, path_roc: Path) -> None:
    fig, axes = plt.subplots(1, 4, figsize=(18, 4.8))
    handles: dict[str, object] = {}
    for ax, spec in zip(axes, SMELLS):
        r = results[spec.key]
        precision, recall, _ = precision_recall_curve(r["y_test"], r["proba_test"])
        handles["ML model"], = ax.plot(recall, precision)
        handles["Chance level"] = ax.axhline(r["y_test"].mean(), linestyle="--", color="gray")
        handles["Chosen threshold"] = ax.scatter(
            [r["test"]["recall"]], [r["test"]["precision"]], color="red", zorder=3)
        if r.get("cuqa_rule"):
            handles["Existing CUQA rule"] = ax.scatter(
                [r["cuqa_rule"]["recall"]], [r["cuqa_rule"]["precision"]], marker="s",
                color="black", zorder=3)
        ax.set_title(f"{spec.display_name} (PR-AUC = {r['test']['pr_auc']:.2f})")
        ax.set_xlabel("Recall")
        ax.set_ylabel("Precision")
        ax.set_xlim(0, 1.02)
        ax.set_ylim(0, 1.02)
        ax.grid(True, alpha=0.3)
    fig.suptitle("Precision-recall curves on the held-out test repositories")
    # one legend under the panels, so it never covers a curve
    fig.legend(handles.values(), handles.keys(), loc="lower center", ncol=len(handles), frameon=False)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(path_pr, dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 5))
    for spec in SMELLS:
        r = results[spec.key]
        fpr, tpr, _ = roc_curve(r["y_test"], r["proba_test"])
        ax.plot(fpr, tpr, label=f"{spec.display_name} (AUC = {r['test']['roc_auc']:.2f})")
    ax.plot([0, 1], [0, 1], linestyle="--", color="gray")
    ax.set_xlabel("False positive rate")
    ax.set_ylabel("True positive rate")
    ax.set_title("ROC curves on the held-out test repositories")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(path_roc, dpi=160)
    plt.close(fig)


def plot_confusion(results: dict, path: Path) -> None:
    fig, axes = plt.subplots(1, 4, figsize=(16, 4.2))
    for ax, spec in zip(axes, SMELLS):
        t = results[spec.key]["test"]
        matrix = np.array([[t["tn"], t["fp"]], [t["fn"], t["tp"]]])
        ax.imshow(matrix, cmap="Blues")
        for (i, j), value in np.ndenumerate(matrix):
            ax.text(j, i, str(value), ha="center", va="center", fontsize=13,
                    color="white" if value > matrix.max() / 2 else "black")
        ax.set_xticks([0, 1], ["No smell", "Smell"])
        ax.set_yticks([0, 1], ["No smell", "Smell"])
        ax.set_xlabel("Predicted")
        ax.set_ylabel("Expert label")
        ax.set_title(spec.display_name)
    fig.suptitle("Confusion matrices on the held-out test repositories")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_importance(results: dict, path: Path) -> None:
    fig, axes = plt.subplots(1, 4, figsize=(19, 4.8))
    for ax, spec in zip(axes, SMELLS):
        top = results[spec.key]["importance"][:10][::-1]
        ax.barh([name for name, _, _ in top], [mean for _, mean, _ in top],
                xerr=[std for _, _, std in top])
        ax.set_title(spec.display_name)
        ax.set_xlabel("Drop in PR-AUC when shuffled")
    fig.suptitle("Permutation feature importance on the test repositories (top 10)")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_baselines(results: dict, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.8))
    labels = [spec.display_name for spec in SMELLS]
    x = np.arange(len(SMELLS))
    width = 0.27
    model_f1 = [results[s.key]["test"]["f1"] for s in SMELLS]
    single_f1 = [results[s.key]["single_metric"]["test"]["f1"] for s in SMELLS]
    rule_f1 = [results[s.key]["cuqa_rule"]["f1"] if results[s.key].get("cuqa_rule") else 0.0 for s in SMELLS]
    ax.bar(x - width, rule_f1, width, label="Existing CUQA rule")
    ax.bar(x, single_f1, width, label="Best single-metric threshold")
    ax.bar(x + width, model_f1, width, label="ML model")
    for i, spec in enumerate(SMELLS):
        if not results[spec.key].get("cuqa_rule"):
            ax.text(x[i] - width, 0.02, "no rule", ha="center", va="bottom", rotation=90, fontsize=8)
    ax.set_xticks(x, labels)
    ax.set_ylabel("F1 on test repositories")
    ax.set_ylim(0, 1)
    ax.set_title("ML model compared with rule-based baselines")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Report tables
# ---------------------------------------------------------------------------

def _fmt(value: float) -> str:
    return f"{value:.3f}"


def _fmt_ci(interval) -> str:
    return f"[{interval[0]:.2f}, {interval[1]:.2f}]" if interval else "n/a"


def write_tables_markdown(summary: dict, path: Path) -> None:
    lines = ["# CUQA ML smell model: evaluation tables", "",
             "Generated by `training/train_model.py`. Do not edit by hand.", ""]

    split = summary["split"]
    lines += ["## Data", "",
              "| Smell | Train samples | Train positive | Test samples | Test positive |",
              "|---|---:|---:|---:|---:|"]
    for spec in SMELLS:
        d = summary["smells"][spec.key]["data"]
        lines.append(f"| {spec.display_name} | {d['train_samples']} | {d['train_positive']} | "
                     f"{d['test_samples']} | {d['test_positive']} |")
    lines += ["", f"Train repositories: {split['train_repositories']}. "
              f"Test repositories: {split['test_repositories']}.", ""]

    lines += ["## Model comparison (cross-validation on training repositories)", "",
              "| Smell | Model | PR-AUC | ROC-AUC | Best F1 |", "|---|---|---:|---:|---:|"]
    for spec in SMELLS:
        for name, m in summary["smells"][spec.key]["model_comparison"].items():
            lines.append(f"| {spec.display_name} | {name} | {m['pr_auc_mean']:.3f} ± {m['pr_auc_std']:.3f} | "
                         f"{_fmt(m['roc_auc_mean'])} | {_fmt(m['best_f1'])} |")
    lines.append("")

    lines += ["## Test results of the shipped model (unseen repositories)", "",
              "| Smell | Threshold | Precision | Recall | F1 | MCC | ROC-AUC | PR-AUC | TP | FP | FN | TN |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for spec in SMELLS:
        s = summary["smells"][spec.key]
        t = s["test"]
        lines.append(f"| {spec.display_name} | {s['threshold']:.3f} | {_fmt(t['precision'])} | {_fmt(t['recall'])} | "
                     f"{_fmt(t['f1'])} | {_fmt(t['mcc'])} | {_fmt(t['roc_auc'])} | {_fmt(t['pr_auc'])} | "
                     f"{t['tp']} | {t['fp']} | {t['fn']} | {t['tn']} |")
    lines.append("")

    lines += ["## 95% confidence intervals (repository bootstrap)", "",
              "| Smell | Precision | Recall | F1 | MCC | ROC-AUC | PR-AUC |", "|---|---|---|---|---|---|---|"]
    for spec in SMELLS:
        ci = summary["smells"][spec.key]["test_ci95"]
        lines.append(f"| {spec.display_name} | " + " | ".join(
            _fmt_ci(ci.get(k)) for k in ("precision", "recall", "f1", "mcc", "roc_auc", "pr_auc")) + " |")
    lines.append("")

    lines += ["## Comparison with rule-based baselines (test repositories)", "",
              "| Smell | Detector | Precision | Recall | F1 | MCC |", "|---|---|---:|---:|---:|---:|"]
    for spec in SMELLS:
        s = summary["smells"][spec.key]
        rule = s.get("cuqa_rule")
        if rule:
            lines.append(f"| {spec.display_name} | Existing CUQA rule | {_fmt(rule['precision'])} | "
                         f"{_fmt(rule['recall'])} | {_fmt(rule['f1'])} | {_fmt(rule['mcc'])} |")
        else:
            lines.append(f"| {spec.display_name} | Existing CUQA rule | no Java rule | | | |")
        single = s["single_metric"]
        st = single["test"]
        lines.append(f"| {spec.display_name} | `{single['feature']} {single['direction']} {single['threshold']:g}` | "
                     f"{_fmt(st['precision'])} | {_fmt(st['recall'])} | {_fmt(st['f1'])} | {_fmt(st['mcc'])} |")
        t = s["test"]
        lines.append(f"| {spec.display_name} | ML model | {_fmt(t['precision'])} | {_fmt(t['recall'])} | "
                     f"{_fmt(t['f1'])} | {_fmt(t['mcc'])} |")
    lines.append("")

    lines += ["## Is the ML model better than the baselines? (paired repository bootstrap)", "",
              "F1 of the ML model minus F1 of the baseline. An interval that includes 0 means the "
              "test set cannot tell the two detectors apart.", "",
              "| Smell | Baseline | F1 difference | 95% interval |", "|---|---|---:|---|"]
    for spec in SMELLS:
        diffs = summary["smells"][spec.key]["f1_differences"]
        for key, label in (("vs_cuqa_rule", "Existing CUQA rule"), ("vs_single_metric", "Best single-metric threshold")):
            if key in diffs:
                lines.append(f"| {spec.display_name} | {label} | {diffs[key]['difference']:+.3f} | "
                             f"[{diffs[key]['ci95'][0]:+.2f}, {diffs[key]['ci95'][1]:+.2f}] |")
    lines.append("")

    lines += ["## Cross-validation of the shipped configuration (training repositories)", "",
              "| Smell | Precision | Recall | F1 | MCC | Enabled in shipped model |", "|---|---:|---:|---:|---:|---|"]
    for spec in SMELLS:
        s = summary["smells"][spec.key]
        c = s["cross_validation"]
        lines.append(f"| {spec.display_name} | {_fmt(c['precision'])} | {_fmt(c['recall'])} | {_fmt(c['f1'])} | "
                     f"{_fmt(c['mcc'])} | {'yes' if s['enabled'] else 'no'} |")
    lines.append("")

    lines += ["## Agreement between MLCQ reviewers", "",
              "| Smell | Review pairs | Observed agreement | Positive agreement (F1) | Cohen's kappa |",
              "|---|---:|---:|---:|---:|"]
    for spec in SMELLS:
        a = summary["reviewer_agreement"].get(spec.mlcq_name)
        if a:
            lines.append(f"| {spec.display_name} | {a['review_pairs']} | {_fmt(a['observed_agreement'])} | "
                         f"{_fmt(a['positive_agreement_f1'])} | {_fmt(a['cohen_kappa'])} |")
    lines.append("")

    lines += ["## Most important features (permutation importance, test repositories)", ""]
    for spec in SMELLS:
        top = summary["smells"][spec.key]["importance"][:8]
        lines.append(f"- **{spec.display_name}**: " + ", ".join(f"`{n}` ({m:.3f})" for n, m, _ in top))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_table(name: str) -> pd.DataFrame:
    table = pd.read_csv(DATASET_DIR / name)
    # the same code copied into two repositories must not sit on both sides of the split
    return table.drop_duplicates("fingerprint", keep="first").reset_index(drop=True)


def split_repositories(*tables: pd.DataFrame) -> tuple[set, set]:
    repositories = sorted(set().union(*(set(t["repository"]) for t in tables)))
    rng = np.random.RandomState(RANDOM_SEED)
    rng.shuffle(repositories)
    n_test = int(round(len(repositories) * TEST_FRACTION))
    return set(repositories[n_test:]), set(repositories[:n_test])


def main() -> int:
    parser = argparse.ArgumentParser(description="Train and evaluate the CUQA smell model.")
    parser.add_argument("--no-export", action="store_true", help="Evaluate only, do not write the model file.")
    args = parser.parse_args()
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    tables = {"class": load_table("mlcq_classes.csv"), "method": load_table("mlcq_methods.csv")}
    features = {"class": list(CLASS_FEATURES), "method": list(METHOD_FEATURES)}
    train_repos, test_repos = split_repositories(*tables.values())
    print(f"Repositories: {len(train_repos)} train, {len(test_repos)} test")

    test_tables = {kind: t[t["repository"].isin(test_repos)] for kind, t in tables.items()}
    print("Running the existing CUQA rules on the test files ...")
    rule_output = cuqa_rule_predictions(test_tables)
    if rule_output is None:
        print("  source cache not found, the CUQA rule baseline is skipped")

    results: dict[str, dict] = {}
    exported: dict[str, dict] = {}
    prediction_rows: list[dict] = []

    for spec in SMELLS:
        table = tables[spec.entity]
        table = table[table[f"label_{spec.key}"].notna()].reset_index(drop=True)
        names = features[spec.entity]
        is_test = table["repository"].isin(test_repos).to_numpy()
        X = table[names].to_numpy(dtype=float)
        y = table[f"label_{spec.key}"].to_numpy(dtype=int)
        groups = table["repository"].to_numpy()
        X_train, y_train, g_train = X[~is_test], y[~is_test], groups[~is_test]
        X_test, y_test, g_test = X[is_test], y[is_test], groups[is_test]
        test_table = table[is_test].reset_index(drop=True)
        print(f"\n== {spec.display_name}: train {len(y_train)} ({y_train.sum()} positive), "
              f"test {len(y_test)} ({y_test.sum()} positive)")

        # 1. model comparison with repeated grouped cross-validation
        comparison = {}
        gb_oof = np.zeros(len(y_train))
        for name in candidate_models(0):
            pr_aucs, roc_aucs, mean_oof = [], [], np.zeros(len(y_train))
            for repeat in range(CV_REPEATS):
                oof = out_of_fold(lambda seed, n=name: candidate_models(seed)[n],
                                  X_train, y_train, g_train, seed=RANDOM_SEED + repeat)
                pr_aucs.append(average_precision_score(y_train, oof))
                roc_aucs.append(roc_auc_score(y_train, oof))
                mean_oof += oof / CV_REPEATS
            comparison[name] = {
                "pr_auc_mean": float(np.mean(pr_aucs)), "pr_auc_std": float(np.std(pr_aucs)),
                "roc_auc_mean": float(np.mean(roc_aucs)),
                "best_f1": best_f1_threshold(y_train, mean_oof)[1],
            }
            if name == "Gradient Boosting":
                gb_oof = mean_oof
            print(f"  CV {name:20s} PR-AUC {comparison[name]['pr_auc_mean']:.3f}  "
                  f"ROC-AUC {comparison[name]['roc_auc_mean']:.3f}  F1 {comparison[name]['best_f1']:.3f}")

        # 2. threshold from out-of-fold training predictions only
        threshold, _ = best_f1_threshold(y_train, gb_oof)
        cv_metrics = {**threshold_metrics(y_train, gb_oof >= threshold), **ranking_metrics(y_train, gb_oof)}
        enabled = cv_metrics["f1"] >= MIN_CV_F1_TO_ENABLE

        # 3. final model and single evaluation on the test repositories
        model = make_gradient_boosting().fit(X_train, y_train)
        proba_test = model.predict_proba(X_test)[:, 1]
        pred_test = proba_test >= threshold
        test_metrics = {**threshold_metrics(y_test, pred_test), **ranking_metrics(y_test, proba_test)}
        test_ci = cluster_bootstrap(y_test, pred_test, proba_test, g_test, BOOTSTRAP_ITERATIONS, RANDOM_SEED)
        print(f"  threshold {threshold:.3f} | test P {test_metrics['precision']:.3f} R {test_metrics['recall']:.3f} "
              f"F1 {test_metrics['f1']:.3f} MCC {test_metrics['mcc']:.3f} PR-AUC {test_metrics['pr_auc']:.3f}")

        # 4. baselines
        single = best_single_metric_rule(X_train, y_train, names)
        single_pred = apply_single_metric_rule(single, X_test)
        single_summary = {
            "feature": single["feature"], "direction": single["direction"],
            "threshold": single["threshold"], "train_f1": single["f1"],
            "test": threshold_metrics(y_test, single_pred),
            "test_ci95": cluster_bootstrap(y_test, single_pred, None, g_test, BOOTSTRAP_ITERATIONS, RANDOM_SEED),
        }
        rule_summary = None
        rule_pred = None
        if rule_output is not None and spec.rule_type:
            rule_pred = rule_labels(test_table, rule_output, spec.rule_type)
            rule_summary = threshold_metrics(y_test, rule_pred)
            rule_summary["ci95"] = cluster_bootstrap(y_test, rule_pred, None, g_test, BOOTSTRAP_ITERATIONS, RANDOM_SEED)
            print(f"  CUQA rule            P {rule_summary['precision']:.3f} R {rule_summary['recall']:.3f} "
                  f"F1 {rule_summary['f1']:.3f}")
        print(f"  single metric ({single['feature']} {single['direction']} {single['threshold']:g}) "
              f"F1 {single_summary['test']['f1']:.3f}")

        f1_differences = {
            "vs_single_metric": paired_f1_difference(
                y_test, pred_test, single_pred, g_test, BOOTSTRAP_ITERATIONS, RANDOM_SEED),
        }
        if rule_pred is not None:
            f1_differences["vs_cuqa_rule"] = paired_f1_difference(
                y_test, pred_test, rule_pred.astype(bool), g_test, BOOTSTRAP_ITERATIONS, RANDOM_SEED)

        # 5. feature importance
        perm = permutation_importance(model, X_test, y_test, scoring="average_precision",
                                      n_repeats=20, random_state=RANDOM_SEED)
        order = np.argsort(-perm.importances_mean)
        importance = [(names[i], float(perm.importances_mean[i]), float(perm.importances_std[i])) for i in order]
        evidence = [names[i] for i in np.argsort(-model.feature_importances_)[:5]]

        results[spec.key] = {
            "data": {
                "train_samples": int(len(y_train)), "train_positive": int(y_train.sum()),
                "test_samples": int(len(y_test)), "test_positive": int(y_test.sum()),
            },
            "model_comparison": comparison,
            "threshold": threshold,
            "enabled": bool(enabled),
            "cross_validation": cv_metrics,
            "test": test_metrics,
            "test_ci95": test_ci,
            "single_metric": single_summary,
            "cuqa_rule": rule_summary,
            "f1_differences": f1_differences,
            "importance": importance,
            "y_test": y_test,
            "proba_test": proba_test,
        }
        exported[spec.smell_type] = {
            "display_name": spec.display_name,
            "mlcq_name": spec.mlcq_name,
            "entity": spec.entity,
            "enabled": bool(enabled),
            "threshold": threshold,
            "severity": spec.severity,
            "features": names,
            "evidence_features": evidence,
            "metrics": {
                "test_precision": round(test_metrics["precision"], 4),
                "test_recall": round(test_metrics["recall"], 4),
                "test_f1": round(test_metrics["f1"], 4),
                "test_mcc": round(test_metrics["mcc"], 4),
                "test_roc_auc": round(test_metrics["roc_auc"], 4),
                "test_pr_auc": round(test_metrics["pr_auc"], 4),
                "cv_f1": round(cv_metrics["f1"], 4),
                "test_samples": int(len(y_test)),
                "test_positive": int(y_test.sum()),
            },
            **export_classifier(model, X_train),
        }
        for i, row in enumerate(test_table.itertuples()):
            prediction_rows.append({
                "smell": spec.key, "sample_id": row.sample_id, "repository": row.repository,
                "path": row.path, "entity_name": row.entity_name,
                "start_line": row.start_line, "end_line": row.end_line,
                "expert_label": int(y_test[i]), "probability": round(float(proba_test[i]), 5),
                "model_prediction": int(pred_test[i]),
                "cuqa_rule_prediction": "" if rule_pred is None else int(rule_pred[i]),
            })

        results[spec.key]["_model"] = model
        results[spec.key]["_X_test"] = X_test

    # ----- export and parity check -----------------------------------------
    payload = {
        "format": MODEL_FORMAT,
        "format_version": SUPPORTED_FORMAT_VERSION,
        "model_version": date.today().isoformat(),
        "algorithm": "gradient boosted decision trees (binary log-loss), one model per smell",
        "hyperparameters": GB_PARAMS,
        "language": "java",
        "training_data": "MLCQ code smell dataset (Madeyski and Lewowski, 2020), consensus of expert reviews",
        "trained_with": f"scikit-learn {sklearn.__version__}",
        "split": "80/20 by repository, random seed 42",
        "smells": exported,
    }
    exported_model = SmellModel(json.loads(json.dumps(payload)))
    max_difference = 0.0
    for spec in SMELLS:
        r = results[spec.key]
        classifier = exported_model.classifiers[spec.smell_type]
        names = features[spec.entity]
        for row, expected in zip(r["_X_test"], r["proba_test"]):
            got = classifier.predict_proba(dict(zip(names, row)))
            max_difference = max(max_difference, abs(got - expected))
    print(f"\nPure-Python model vs scikit-learn, largest probability difference: {max_difference:.2e}")
    if max_difference > 1e-9:
        print("ERROR: exported model does not reproduce scikit-learn predictions")
        return 1
    if not args.no_export:
        MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
        MODEL_OUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        print(f"Model written to {MODEL_OUT} ({MODEL_OUT.stat().st_size / 1024:.0f} KB)")

    # ----- figures ----------------------------------------------------------
    plot_curves(results, RESULTS_DIR / "pr_curves.png", RESULTS_DIR / "roc_curves.png")
    plot_confusion(results, RESULTS_DIR / "confusion_matrices.png")
    plot_importance(results, RESULTS_DIR / "feature_importance.png")
    plot_baselines(results, RESULTS_DIR / "model_vs_baselines.png")

    # ----- result files -----------------------------------------------------
    summary = {
        "generated_on": date.today().isoformat(),
        "protocol": {
            "random_seed": RANDOM_SEED, "test_fraction": TEST_FRACTION,
            "cv_folds": CV_FOLDS, "cv_repeats": CV_REPEATS,
            "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
            "min_cv_f1_to_enable": MIN_CV_F1_TO_ENABLE,
            "hyperparameters": GB_PARAMS,
            "scikit_learn": sklearn.__version__,
        },
        "split": {"train_repositories": len(train_repos), "test_repositories": len(test_repos)},
        "export_max_probability_difference": max_difference,
        "reviewer_agreement": reviewer_agreement(),
        "smells": {
            spec.key: {k: v for k, v in results[spec.key].items()
                       if k not in ("y_test", "proba_test", "_model", "_X_test")}
            for spec in SMELLS
        },
    }
    with open(RESULTS_DIR / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    write_tables_markdown(summary, RESULTS_DIR / "evaluation_tables.md")

    with open(RESULTS_DIR / "test_predictions.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(prediction_rows[0].keys()))
        writer.writeheader()
        writer.writerows(prediction_rows)

    with open(RESULTS_DIR / "feature_importance.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["smell", "feature", "importance_mean", "importance_std"])
        for spec in SMELLS:
            for name, mean, std in results[spec.key]["importance"]:
                writer.writerow([spec.key, name, round(mean, 5), round(std, 5)])

    print(f"Results written to {RESULTS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
