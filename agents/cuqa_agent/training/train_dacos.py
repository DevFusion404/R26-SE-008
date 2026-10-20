"""
train_dacos.py
--------------
Trains the CUQA smell model for the three DACOS smells and adds them to the
shipped model file next to the four MLCQ smells.

Protocol (the same as train_model.py):
  1. The DACOS repositories are split 80/20 with the same seed. No repository
     contributes samples to both parts.
  2. Logistic regression, random forest and gradient boosting are compared with
     repeated repository-grouped cross-validation on the training repositories.
  3. The decision threshold is tuned on the out-of-fold training predictions.
  4. The gradient boosting model is evaluated once on the test repositories and
     compared with the CUQA rule and with the best single-metric threshold.
  5. The model is exported to JSON and its pure-Python predictions are checked
     against scikit-learn.

Three things differ from MLCQ, all because of how DACOS was built:
  - Results are reported twice: on the human-annotated samples and on all
    labelled samples. The other samples were labelled by the DACOS authors with
    metric thresholds, so they are easy by construction. The human-annotated
    samples are the cases where the smell is a judgement call, and they are the
    meaningful test.
  - A smell is enabled only when, on the human-annotated training samples, its
    cross-validated F1 reaches MIN_CV_F1_TO_ENABLE and is higher than the F1 of
    flagging every one of those samples. About half of them are positive, so
    flagging all of them already gives a fair F1; that is reported as a baseline.
  - DACOS has ten repositories, so the test part holds two. A repository
    bootstrap over two repositories has no meaning. The spread is shown with a
    leave-one-repository-out run over all ten repositories instead, and the
    paired comparison with the baselines is bootstrapped over those ten.

Usage:
    python training/build_dacos_dataset.py
    python training/train_dacos.py
"""

import argparse
import csv
import gzip
import json
import sys
import warnings
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import sklearn
from joblib import Parallel, delayed
from sklearn.inspection import permutation_importance
from sklearn.metrics import precision_recall_curve, roc_curve
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.utils import Bunch

TRAINING_DIR = Path(__file__).resolve().parent
SRC_DIR = TRAINING_DIR.parent / "src"
for _path in (SRC_DIR, TRAINING_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from java_rules import COMPLEX_METHOD_CC_LIMIT  # noqa: E402
from ml_detector.java_features import CLASS_FEATURES, METHOD_FEATURES  # noqa: E402
from ml_detector.model import SmellModel  # noqa: E402
from train_model import (  # noqa: E402
    BOOTSTRAP_ITERATIONS,
    CV_FOLDS,
    CV_REPEATS,
    GB_PARAMS,
    MIN_CV_F1_TO_ENABLE,
    MODEL_OUT,
    RANDOM_SEED,
    TEST_FRACTION,
    best_f1_threshold,
    candidate_models,
    export_classifier,
    make_gradient_boosting,
    out_of_fold,
    paired_f1_difference,
    ranking_metrics,
    split_repositories,
    threshold_metrics,
)

DATASET_DIR = TRAINING_DIR / "dataset"
RESULTS_DIR = TRAINING_DIR / "results" / "dacos"
DATASET_NAME = "DACOS"
DATASET_CITATION = "DACOS (Nandani, Saad and Sharma, 2023), doi:10.5281/zenodo.7570428"
LORO_INNER_FOLDS = 3


@dataclass(frozen=True)
class DacosSmell:
    key: str            # column suffix in the dataset tables
    smell_type: str     # CUQA smell type emitted in reports
    display_name: str
    entity: str         # "class" | "method"
    severity: str
    rule_name: str | None                                   # the CUQA rule for this smell, if one exists
    rule: Callable[[pd.DataFrame], np.ndarray] | None


SMELLS = (
    DacosSmell("complex_method", "ComplexMethod", "Complex Method", "method", "medium",
               f"cyclomatic complexity > {COMPLEX_METHOD_CC_LIMIT}",
               lambda t: t["cyclomatic_complexity"].to_numpy() > COMPLEX_METHOD_CC_LIMIT),
    # CUQA's Java rule for too many parameters is the same check on the same count
    DacosSmell("long_parameter_list", "TooManyParameters", "Long Parameter List", "method", "medium",
               "parameters > 5", lambda t: t["n_params"].to_numpy() > 5),
    DacosSmell("multifaceted_abstraction", "MultifacetedAbstraction", "Multifaceted Abstraction",
               "class", "medium", None, None),
)


def _parallel(model):
    """Use every core for the random forest; the result does not change."""
    if "n_jobs" in model.get_params():
        model.set_params(n_jobs=-1)
    return model


def _metrics(y, pred, proba=None) -> dict:
    out = threshold_metrics(y, pred)
    if proba is not None and 0 < np.sum(y) < len(y):
        out.update(ranking_metrics(y, proba))
    return out


def best_single_metric_rule(X_train, y_train, feature_names) -> dict:
    """Strongest rule of the form 'metric >= t' or 'metric <= t' on the training data.

    The same search as train_model.best_single_metric_rule, written with sorted
    cumulative counts because DACOS has about 40 times more rows than MLCQ.
    """
    best: dict[str, Any] = {"f1": -1.0}
    positives = int(y_train.sum())
    n = len(y_train)
    for j, name in enumerate(feature_names):
        column = X_train[:, j]
        order = np.argsort(column, kind="stable")
        values, labels = column[order], y_train[order]
        cum_pos = np.concatenate([[0], np.cumsum(labels)])
        unique, first = np.unique(values, return_index=True)
        last = np.append(first[1:], n)                  # end (exclusive) of each value's run
        tp_ge, pred_ge = positives - cum_pos[first], n - first
        tp_le, pred_le = cum_pos[last], last
        # iterate as the original does: each value, ">=" first, then "<="
        tp = np.column_stack([tp_ge, tp_le]).ravel()
        pred = np.column_stack([pred_ge, pred_le]).ravel()
        f1 = np.where(tp > 0, 2 * tp / (pred + positives), -1.0)
        k = int(np.argmax(f1))
        if f1[k] > best["f1"]:
            best = {"feature": name, "index": j, "direction": ">=" if k % 2 == 0 else "<=",
                    "threshold": float(unique[k // 2]), "f1": float(f1[k])}
    return best


def apply_single_metric_rule(rule: dict, X) -> np.ndarray:
    column = X[:, rule["index"]]
    return column >= rule["threshold"] if rule["direction"] == ">=" else column <= rule["threshold"]


def annotator_agreement(table: pd.DataFrame, key: str) -> dict:
    """How often the two DACOS annotators of a sample agree (as train_model.reviewer_agreement)."""
    human = table[table[f"label_source_{key}"] == "human"]
    both_pos = both_neg = differ = 0
    for n, k in zip(human[f"n_reviews_{key}"], human[f"n_positive_{key}"]):
        n, k = int(n), int(k)
        if n < 2:
            continue
        both_pos += k * (k - 1) // 2
        both_neg += (n - k) * (n - k - 1) // 2
        differ += k * (n - k)
    pairs = both_pos + both_neg + differ
    if not pairs:
        return {}
    observed = (both_pos + both_neg) / pairs
    p_pos = (2 * both_pos + differ) / (2 * pairs)
    expected = p_pos ** 2 + (1 - p_pos) ** 2
    return {
        "review_pairs": int(pairs),
        "observed_agreement": observed,
        "positive_agreement_f1": 2 * both_pos / (2 * both_pos + differ) if both_pos + differ else 0.0,
        "cohen_kappa": (observed - expected) / (1 - expected) if expected < 1 else 0.0,
    }


def _held_out_repository(X, y, groups, repo, names, seed: int) -> tuple:
    """One leave-one-repository-out run: model and single-metric predictions for *repo*."""
    test = groups == repo
    train = ~test
    oof = np.zeros(int(train.sum()))
    cv = StratifiedGroupKFold(n_splits=LORO_INNER_FOLDS, shuffle=True, random_state=seed)
    X_tr, y_tr, g_tr = X[train], y[train], groups[train]
    for fit_idx, valid_idx in cv.split(X_tr, y_tr, g_tr):
        inner = make_gradient_boosting(seed).fit(X_tr[fit_idx], y_tr[fit_idx])
        oof[valid_idx] = inner.predict_proba(X_tr[valid_idx])[:, 1]
    threshold, _ = best_f1_threshold(y_tr, oof)
    model = make_gradient_boosting(seed).fit(X_tr, y_tr)
    single = best_single_metric_rule(X_tr, y_tr, names)
    return (repo, model.predict_proba(X[test])[:, 1] >= threshold,
            apply_single_metric_rule(single, X[test]))


def leave_one_repository_out(X, y, groups, human, rule_pred, names, seed: int) -> dict:
    """Train on nine repositories, test on the tenth, for every repository.

    The threshold of each run is tuned on out-of-fold predictions of its own
    nine training repositories, so the held-out repository is never used.
    The runs are independent and use one core each.
    """
    pooled = {"model": np.zeros(len(y), dtype=bool), "single": np.zeros(len(y), dtype=bool)}
    per_repo = {}
    runs = Parallel(n_jobs=-1)(
        delayed(_held_out_repository)(X, y, groups, repo, names, seed) for repo in sorted(set(groups))
    )
    for repo, model_pred, single_pred in runs:
        test = groups == repo
        pooled["model"][test] = model_pred
        pooled["single"][test] = single_pred

        h = test & human
        per_repo[repo] = {
            "human_samples": int(h.sum()), "human_positive": int(y[h].sum()),
            "all_samples": int(test.sum()), "all_positive": int(y[test].sum()),
        }
        for name, pred in (("model", pooled["model"]), ("single_metric", pooled["single"]),
                           ("cuqa_rule", rule_pred)):
            if pred is None:
                continue
            per_repo[repo][f"{name}_f1_human"] = threshold_metrics(y[h], pred[h])["f1"] if y[h].sum() else None
            per_repo[repo][f"{name}_f1_all"] = threshold_metrics(y[test], pred[test])["f1"] if y[test].sum() else None

    summary: dict[str, Any] = {"per_repository": per_repo}
    for name, pred in (("model", pooled["model"]), ("single_metric", pooled["single"]), ("cuqa_rule", rule_pred)):
        if pred is None:
            continue
        values = [r[f"{name}_f1_human"] for r in per_repo.values() if r[f"{name}_f1_human"] is not None]
        summary[name] = {
            "pooled_human": threshold_metrics(y[human], pred[human]),
            "pooled_all": threshold_metrics(y, pred),
            "f1_human_mean": float(np.mean(values)) if values else None,
            "f1_human_std": float(np.std(values)) if values else None,
        }
    # paired comparison, resampling the ten repositories
    summary["f1_differences_human"] = {}
    for name, pred in (("vs_single_metric", pooled["single"]), ("vs_cuqa_rule", rule_pred)):
        if pred is not None:
            summary["f1_differences_human"][name] = paired_f1_difference(
                y[human], pooled["model"][human], pred[human], groups[human], BOOTSTRAP_ITERATIONS, seed)
    return summary


# ---------------------------------------------------------------------------
# Figures and tables
# ---------------------------------------------------------------------------

def plot_curves(results: dict, path: Path) -> None:
    fig, axes = plt.subplots(1, len(SMELLS), figsize=(15, 4.8))
    handles: dict[str, Any] = {}
    for ax, spec in zip(axes, SMELLS):
        r = results[spec.key]
        h = r["human_test"]
        y, proba = r["y_test"][h], r["proba_test"][h]
        precision, recall, _ = precision_recall_curve(y, proba)
        handles["ML model"], = ax.plot(recall, precision)
        handles["Chance level"] = ax.axhline(y.mean(), linestyle="--", color="gray")
        t = r["test_human"]
        handles["Chosen threshold"] = ax.scatter([t["recall"]], [t["precision"]], color="red", zorder=3)
        if r.get("cuqa_rule"):
            c = r["cuqa_rule"]["human"]
            handles["CUQA rule"] = ax.scatter([c["recall"]], [c["precision"]], marker="s", color="black", zorder=3)
        ax.set_title(f"{spec.display_name} (PR-AUC = {t.get('pr_auc', float('nan')):.2f})")
        ax.set_xlabel("Recall")
        ax.set_ylabel("Precision")
        ax.set_xlim(0, 1.02)
        ax.set_ylim(0, 1.02)
        ax.grid(True, alpha=0.3)
    fig.suptitle("Precision-recall on the human-annotated DACOS samples of the test repositories")
    fig.legend(list(handles.values()), list(handles.keys()), loc="lower center", ncol=len(handles), frameon=False)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(path, dpi=160)
    plt.close(fig)


# One colour per detector, in a fixed order, checked for colour-blind separation.
# The colour follows the detector in every figure; value labels carry the numbers.
DETECTOR_COLORS = {"cuqa_rule": "#2a78d6", "single_metric": "#eb6834", "model": "#1baf7a", "flag_all": "#eda100"}
SMELL_COLORS = ("#2a78d6", "#eb6834", "#1baf7a")
LABEL_INK = "#52514e"


def plot_baselines(results: dict, path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(15, 4.8), sharey=True)
    detectors = (("cuqa_rule", "CUQA rule"), ("single_metric", "Best single-metric threshold"),
                 ("flag_all", "Flag every human-annotated sample"), ("model", "ML model"))
    x = np.arange(len(SMELLS))
    width = 0.2
    for ax, subset, title in ((axes[0], "human", "Human-annotated test samples"),
                              (axes[1], "all", "All labelled test samples")):
        for k, (key, label) in enumerate(detectors):
            values = []
            for s in SMELLS:
                r = results[s.key]
                if key == "cuqa_rule":
                    values.append(r["cuqa_rule"][subset]["f1"] if r.get("cuqa_rule") else None)
                elif key == "single_metric":
                    values.append(r["single_metric"][subset]["f1"])
                elif key == "flag_all":
                    values.append(r["flag_all_human"]["test"]["f1"] if subset == "human" else None)
                else:
                    values.append(r[f"test_{subset}"]["f1"])
            offset = (k - (len(detectors) - 1) / 2) * width
            bars = ax.bar(x + offset, [v or 0.0 for v in values], width, label=label,
                          color=DETECTOR_COLORS[key], edgecolor="white", linewidth=1.5)
            for bar, v in zip(bars, values):
                text = "n/a" if v is None else f"{v:.2f}"
                if key == "cuqa_rule" and v is None:
                    text = "no rule"
                ax.text(bar.get_x() + bar.get_width() / 2, (v or 0.0) + 0.015, text, ha="center",
                        va="bottom", fontsize=8, color=LABEL_INK, rotation=90 if v is None else 0)
        ax.set_xticks(x, [s.display_name for s in SMELLS])
        ax.set_title(title)
        ax.set_ylim(0, 1.08)
        ax.grid(True, axis="y", alpha=0.3)
    axes[0].set_ylabel("F1 on test repositories")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False)
    fig.suptitle("DACOS: ML model compared with rule-based baselines")
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_roc(results: dict, path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    for ax, subset, title in ((axes[0], "human", "Human-annotated test samples"),
                              (axes[1], "all", "All labelled test samples")):
        for spec, color in zip(SMELLS, SMELL_COLORS):
            r = results[spec.key]
            mask = r["human_test"] if subset == "human" else np.ones(len(r["y_test"]), dtype=bool)
            fpr, tpr, _ = roc_curve(r["y_test"][mask], r["proba_test"][mask])
            ax.plot(fpr, tpr, color=color, linewidth=2,
                    label=f"{spec.display_name} (AUC = {r[f'test_{subset}']['roc_auc']:.2f})")
        ax.plot([0, 1], [0, 1], linestyle="--", color="gray", label="Chance level")
        ax.set_xlabel("False positive rate")
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="lower right")
    axes[0].set_ylabel("True positive rate")
    fig.suptitle("DACOS: ROC curves on the test repositories")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_confusion(results: dict, path: Path) -> None:
    fig, axes = plt.subplots(2, len(SMELLS), figsize=(13, 8))
    for row, subset in enumerate(("human", "all")):
        for ax, spec in zip(axes[row], SMELLS):
            t = results[spec.key][f"test_{subset}"]
            matrix = np.array([[t["tn"], t["fp"]], [t["fn"], t["tp"]]])
            ax.imshow(matrix, cmap="Blues")
            for (i, j), value in np.ndenumerate(matrix):
                ax.text(j, i, str(value), ha="center", va="center", fontsize=13,
                        color="white" if value > matrix.max() / 2 else "black")
            ax.set_xticks([0, 1], ["No smell", "Smell"])
            ax.set_yticks([0, 1], ["No smell", "Smell"])
            ax.set_xlabel("Predicted")
            ax.set_ylabel("Label")
            ax.set_title(f"{spec.display_name}\n{'human-annotated' if subset == 'human' else 'all labelled'} samples")
    fig.suptitle("DACOS: confusion matrices on the test repositories")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_importance(results: dict, path: Path) -> None:
    fig, axes = plt.subplots(1, len(SMELLS), figsize=(16, 4.8))
    for ax, spec in zip(axes, SMELLS):
        top = results[spec.key]["importance"][:10][::-1]
        ax.barh([name for name, _, _ in top], [mean for _, mean, _ in top],
                xerr=[std for _, _, std in top], color=SMELL_COLORS[0])
        ax.axvline(0, color="gray", linewidth=0.8)
        ax.set_title(spec.display_name)
        ax.set_xlabel("Drop in PR-AUC when shuffled")
    fig.suptitle("DACOS: permutation feature importance on the human-annotated test samples (top 10)")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_leave_one_out(results: dict, path: Path) -> None:
    """F1 on the human-annotated samples of each held-out repository, per detector."""
    fig, axes = plt.subplots(1, len(SMELLS), figsize=(15, 4.8), sharey=True)
    detectors = (("cuqa_rule", "CUQA rule"), ("single_metric", "Best single-metric threshold"),
                 ("model", "ML model"))
    rng = np.random.RandomState(RANDOM_SEED)
    for ax, spec in zip(axes, SMELLS):
        loro = results[spec.key]["leave_one_repository_out"]
        shown = [(key, label) for key, label in detectors if key in loro]
        for k, (key, label) in enumerate(shown):
            values = [r[f"{key}_f1_human"] for r in loro["per_repository"].values()
                      if r[f"{key}_f1_human"] is not None]
            ax.scatter(k + rng.uniform(-0.12, 0.12, len(values)), values, s=48, color=DETECTOR_COLORS[key],
                       edgecolor="white", linewidth=1, label=label, zorder=3)
            pooled = loro[key]["pooled_human"]["f1"]
            ax.hlines(pooled, k - 0.3, k + 0.3, color="black", linewidth=2, zorder=4)
            ax.text(k + 0.32, pooled, f"{pooled:.2f}", va="center", fontsize=8, color=LABEL_INK)
        ax.set_xticks(range(len(shown)), [label.replace("Best single-metric threshold", "Single threshold")
                                          for _, label in shown])
        ax.set_xlim(-0.6, len(shown) - 0.3)
        ax.set_title(spec.display_name)
        ax.set_ylim(0, 1.02)
        ax.grid(True, axis="y", alpha=0.3)
    axes[0].set_ylabel("F1 on the held-out repository")
    fig.suptitle("DACOS leave-one-repository-out: one dot per held-out repository, line = pooled F1 "
                 "(human-annotated samples)")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_all(results: dict) -> None:
    plot_curves(results, RESULTS_DIR / "pr_curves.png")
    plot_roc(results, RESULTS_DIR / "roc_curves.png")
    plot_confusion(results, RESULTS_DIR / "confusion_matrices.png")
    plot_importance(results, RESULTS_DIR / "feature_importance.png")
    plot_baselines(results, RESULTS_DIR / "model_vs_baselines.png")
    plot_leave_one_out(results, RESULTS_DIR / "leave_one_repository_out.png")


def results_from_files() -> dict:
    """Rebuild what the figures need from metrics.json and test_predictions.csv.gz."""
    summary = json.loads((RESULTS_DIR / "metrics.json").read_text(encoding="utf-8"))
    predictions = pd.read_csv(RESULTS_DIR / "test_predictions.csv.gz")
    results = {}
    for spec in SMELLS:
        rows = predictions[predictions["smell"] == spec.key]
        results[spec.key] = {
            **summary["smells"][spec.key],
            "y_test": rows["label"].to_numpy(dtype=int),
            "proba_test": rows["probability"].to_numpy(dtype=float),
            "human_test": (rows["label_source"] == "human").to_numpy(),
        }
    return results


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def write_tables_markdown(summary: dict, path: Path) -> None:
    lines = ["# CUQA ML smell model: DACOS evaluation tables", "",
             "Generated by `training/train_dacos.py`. Do not edit by hand.", "",
             "Human = samples two people annotated (the metric value is in the range where the smell "
             "is a judgement call). All = human samples plus the samples the DACOS authors labelled "
             "with metric thresholds, which are easy by construction.", ""]
    split = summary["split"]
    lines += ["## Data", "",
              "| Smell | Train human (positive) | Train all (positive) | Test human (positive) | Test all (positive) |",
              "|---|---:|---:|---:|---:|"]
    for spec in SMELLS:
        d = summary["smells"][spec.key]["data"]
        lines.append(f"| {spec.display_name} | {d['train_human']} ({d['train_human_positive']}) | "
                     f"{d['train_all']} ({d['train_all_positive']}) | {d['test_human']} ({d['test_human_positive']}) | "
                     f"{d['test_all']} ({d['test_all_positive']}) |")
    lines += ["", f"Train repositories ({len(split['train_repositories'])}): {', '.join(split['train_repositories'])}.",
              f"Test repositories ({len(split['test_repositories'])}): {', '.join(split['test_repositories'])}.", ""]

    lines += ["## Model comparison (cross-validation on training repositories)", "",
              "PR-AUC of a detector that cannot tell samples apart equals the share of positive samples, "
              "and its ROC-AUC is 0.5. The last column is the F1 of flagging every human-annotated sample.", "",
              "| Smell | Model | PR-AUC all | PR-AUC human | ROC-AUC human | Best F1 human | F1 flag all human |",
              "|---|---|---:|---:|---:|---:|---:|"]
    for spec in SMELLS:
        flag_all = summary["smells"][spec.key]["flag_all_human"]["cross_validation"]["f1"]
        for name, m in summary["smells"][spec.key]["model_comparison"].items():
            lines.append(f"| {spec.display_name} | {name} | {m['pr_auc_mean']:.3f} ± {m['pr_auc_std']:.3f} | "
                         f"{_fmt(m['pr_auc_human'])} | {_fmt(m['roc_auc_human'])} | {_fmt(m['best_f1_human'])} | "
                         f"{_fmt(flag_all)} |")
    lines.append("")

    lines += ["## Test results of the shipped model (unseen repositories)", "",
              "| Smell | Samples | Threshold | Precision | Recall | F1 | MCC | Accuracy | ROC-AUC | PR-AUC | TP | FP | FN | TN |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for spec in SMELLS:
        s = summary["smells"][spec.key]
        for label, t in (("human", s["test_human"]), ("all", s["test_all"])):
            lines.append(f"| {spec.display_name} | {label} | {s['threshold']:.3f} | {_fmt(t['precision'])} | "
                         f"{_fmt(t['recall'])} | {_fmt(t['f1'])} | {_fmt(t['mcc'])} | {_fmt(t['accuracy'])} | "
                         f"{_fmt(t.get('roc_auc'))} | {_fmt(t.get('pr_auc'))} | "
                         f"{t['tp']} | {t['fp']} | {t['fn']} | {t['tn']} |")
    lines.append("")

    lines += ["## Comparison with rule-based baselines (test repositories)", "",
              "| Smell | Detector | Precision human | Recall human | F1 human | F1 all |", "|---|---|---:|---:|---:|---:|"]
    for spec in SMELLS:
        s = summary["smells"][spec.key]
        rule = s.get("cuqa_rule")
        if rule:
            lines.append(f"| {spec.display_name} | CUQA rule (`{rule['rule']}`) | {_fmt(rule['human']['precision'])} | "
                         f"{_fmt(rule['human']['recall'])} | {_fmt(rule['human']['f1'])} | {_fmt(rule['all']['f1'])} |")
        else:
            lines.append(f"| {spec.display_name} | CUQA rule | no Java rule | | | |")
        single = s["single_metric"]
        lines.append(f"| {spec.display_name} | `{single['feature']} {single['direction']} {single['threshold']:g}` | "
                     f"{_fmt(single['human']['precision'])} | {_fmt(single['human']['recall'])} | "
                     f"{_fmt(single['human']['f1'])} | {_fmt(single['all']['f1'])} |")
        flag_all = s["flag_all_human"]["test"]
        lines.append(f"| {spec.display_name} | Flag every human-annotated sample | {_fmt(flag_all['precision'])} | "
                     f"{_fmt(flag_all['recall'])} | {_fmt(flag_all['f1'])} | n/a |")
        lines.append(f"| {spec.display_name} | ML model | {_fmt(s['test_human']['precision'])} | "
                     f"{_fmt(s['test_human']['recall'])} | {_fmt(s['test_human']['f1'])} | {_fmt(s['test_all']['f1'])} |")
    lines.append("")

    lines += ["## Leave-one-repository-out over all ten repositories (human-annotated samples)", "",
              "Each repository is held out once; the model and threshold come from the other nine. "
              "Pooled F1 joins the ten held-out predictions. Mean ± sd is over repositories with at "
              "least one positive sample.", "",
              "| Smell | Detector | Pooled precision | Pooled recall | Pooled F1 | F1 mean ± sd |", "|---|---|---:|---:|---:|---|"]
    for spec in SMELLS:
        loro = summary["smells"][spec.key]["leave_one_repository_out"]
        for name, label in (("cuqa_rule", "CUQA rule"), ("single_metric", "Best single-metric threshold"),
                            ("model", "ML model")):
            if name not in loro:
                continue
            p = loro[name]["pooled_human"]
            lines.append(f"| {spec.display_name} | {label} | {_fmt(p['precision'])} | {_fmt(p['recall'])} | "
                         f"{_fmt(p['f1'])} | {_fmt(loro[name]['f1_human_mean'])} ± {_fmt(loro[name]['f1_human_std'])} |")
    lines.append("")

    lines += ["## Is the ML model better than the baselines? (paired bootstrap over the ten repositories)", "",
              "F1 of the ML model minus F1 of the baseline on the pooled leave-one-repository-out "
              "predictions, human-annotated samples. An interval that includes 0 means the data "
              "cannot tell the two detectors apart.", "",
              "| Smell | Baseline | F1 difference | 95% interval |", "|---|---|---:|---|"]
    for spec in SMELLS:
        diffs = summary["smells"][spec.key]["leave_one_repository_out"]["f1_differences_human"]
        for key, label in (("vs_cuqa_rule", "CUQA rule"), ("vs_single_metric", "Best single-metric threshold")):
            if key in diffs:
                lines.append(f"| {spec.display_name} | {label} | {diffs[key]['difference']:+.3f} | "
                             f"[{diffs[key]['ci95'][0]:+.2f}, {diffs[key]['ci95'][1]:+.2f}] |")
    lines.append("")

    lines += ["## Cross-validation of the shipped configuration (training repositories)", "",
              f"Enabled when the F1 on human-annotated samples is at least {MIN_CV_F1_TO_ENABLE} and higher "
              "than the F1 of flagging every human-annotated sample.", "",
              "| Smell | Samples | Precision | Recall | F1 | MCC | F1 flag all | Enabled in shipped model |",
              "|---|---|---:|---:|---:|---:|---:|---|"]
    for spec in SMELLS:
        s = summary["smells"][spec.key]
        flag_all = s["flag_all_human"]["cross_validation"]["f1"]
        for label, c, base in (("human", s["cross_validation_human"], _fmt(flag_all)),
                               ("all", s["cross_validation_all"], "n/a")):
            lines.append(f"| {spec.display_name} | {label} | {_fmt(c['precision'])} | {_fmt(c['recall'])} | "
                         f"{_fmt(c['f1'])} | {_fmt(c['mcc'])} | {base} | {'yes' if s['enabled'] else 'no'} |")
    lines.append("")

    lines += ["## Agreement between DACOS annotators", "",
              "| Smell | Annotation pairs | Observed agreement | Positive agreement (F1) | Cohen's kappa |",
              "|---|---:|---:|---:|---:|"]
    for spec in SMELLS:
        a = summary["annotator_agreement"].get(spec.key)
        if a:
            lines.append(f"| {spec.display_name} | {a['review_pairs']} | {_fmt(a['observed_agreement'])} | "
                         f"{_fmt(a['positive_agreement_f1'])} | {_fmt(a['cohen_kappa'])} |")
    lines.append("")

    lines += ["## Most important features (permutation importance, human-annotated test samples)", ""]
    for spec in SMELLS:
        top = summary["smells"][spec.key]["importance"][:8]
        lines.append(f"- **{spec.display_name}**: " + ", ".join(f"`{n}` ({m:.3f})" for n, m, _ in top))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_table(name: str) -> pd.DataFrame:
    return pd.read_csv(DATASET_DIR / name, low_memory=False)


def labelled_rows(table: pd.DataFrame, key: str) -> pd.DataFrame:
    """Rows labelled for one smell; a copy of the same code is kept once, preferring a human label."""
    t = table[table[f"label_{key}"].notna()].copy()
    t["_human"] = (t[f"label_source_{key}"] == "human").astype(int)
    t = t.sort_values(["_human", "sample_id"], ascending=[False, True], kind="stable")
    t = t.drop_duplicates("fingerprint", keep="first").sort_values("sample_id", kind="stable")
    return t.reset_index(drop=True)


def merge_into_model_file(exported: dict, payload_extra: dict) -> dict:
    """Shipped model with the DACOS smells replaced and every other smell kept."""
    payload: dict[str, Any] = {}
    if MODEL_OUT.exists():
        payload = json.loads(MODEL_OUT.read_text(encoding="utf-8"))
    kept = {k: v for k, v in payload.get("smells", {}).items() if v.get("dataset", "MLCQ") != DATASET_NAME}
    for spec in kept.values():
        spec.setdefault("dataset", "MLCQ")  # files written before DACOS was added have no dataset field
    payload.update(payload_extra)
    payload["smells"] = {**kept, **exported}
    datasets = []
    if any(v.get("dataset", "MLCQ") == "MLCQ" for v in payload["smells"].values()):
        datasets.append("MLCQ code smell dataset (Madeyski and Lewowski, 2020), consensus of expert reviews, "
                        "for Long Method, Feature Envy, Blob and Data Class")
    datasets.append(f"{DATASET_CITATION}, for Complex Method, Long Parameter List and Multifaceted Abstraction")
    payload["training_data"] = "; ".join(datasets)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Train and evaluate the DACOS smells of the CUQA model.")
    parser.add_argument("--no-export", action="store_true", help="Evaluate only, do not write the model file.")
    parser.add_argument("--plots-only", action="store_true",
                        help="Redraw the figures from the saved results without training.")
    args = parser.parse_args()
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.plots_only:
        plot_all(results_from_files())
        print(f"Figures written to {RESULTS_DIR}")
        return 0

    tables = {"class": load_table("dacos_classes.csv.gz"), "method": load_table("dacos_methods.csv.gz")}
    features = {"class": list(CLASS_FEATURES), "method": list(METHOD_FEATURES)}
    train_repos, test_repos = split_repositories(*tables.values())
    print(f"Repositories: {len(train_repos)} train, {len(test_repos)} test ({', '.join(sorted(test_repos))})")

    results: dict[str, dict] = {}
    exported: dict[str, dict] = {}
    prediction_rows: list[dict] = []
    agreement: dict[str, dict] = {}

    for spec in SMELLS:
        table = labelled_rows(tables[spec.entity], spec.key)
        agreement[spec.key] = annotator_agreement(table, spec.key)
        names = features[spec.entity]
        is_test = table["repository"].isin(test_repos).to_numpy()
        X = table[names].to_numpy(dtype=float)
        y = table[f"label_{spec.key}"].to_numpy(dtype=int)
        groups = table["repository"].to_numpy()
        human = (table[f"label_source_{spec.key}"] == "human").to_numpy()
        rule_all = spec.rule(table) if spec.rule else None

        X_train, y_train, g_train, h_train = X[~is_test], y[~is_test], groups[~is_test], human[~is_test]
        X_test, y_test, h_test = X[is_test], y[is_test], human[is_test]
        test_table = table[is_test].reset_index(drop=True)
        print(f"\n== {spec.display_name}: train {len(y_train)} ({y_train.sum()} positive, {h_train.sum()} human), "
              f"test {len(y_test)} ({y_test.sum()} positive, {h_test.sum()} human)", flush=True)

        # 1. model comparison with repeated grouped cross-validation
        comparison = {}
        gb_oof = np.zeros(len(y_train))
        for name in candidate_models(0):
            pr_aucs, mean_oof = [], np.zeros(len(y_train))
            for repeat in range(CV_REPEATS):
                oof = out_of_fold(lambda seed, n=name: _parallel(candidate_models(seed)[n]),
                                  X_train, y_train, g_train, seed=RANDOM_SEED + repeat)
                pr_aucs.append(ranking_metrics(y_train, oof)["pr_auc"])
                mean_oof += oof / CV_REPEATS
            human_ranking = ranking_metrics(y_train[h_train], mean_oof[h_train])
            comparison[name] = {
                "pr_auc_mean": float(np.mean(pr_aucs)), "pr_auc_std": float(np.std(pr_aucs)),
                "pr_auc_human": human_ranking["pr_auc"],
                "roc_auc_human": human_ranking["roc_auc"],
                "best_f1_human": best_f1_threshold(y_train[h_train], mean_oof[h_train])[1],
            }
            if name == "Gradient Boosting":
                gb_oof = mean_oof
            print(f"  CV {name:20s} PR-AUC all {comparison[name]['pr_auc_mean']:.3f}  "
                  f"human {comparison[name]['pr_auc_human']:.3f}  best F1 human {comparison[name]['best_f1_human']:.3f}",
                  flush=True)

        # 2. threshold from out-of-fold training predictions only
        threshold, _ = best_f1_threshold(y_train, gb_oof)
        cv_all = _metrics(y_train, gb_oof >= threshold, gb_oof)
        cv_human = _metrics(y_train[h_train], gb_oof[h_train] >= threshold, gb_oof[h_train])
        flag_all_cv = threshold_metrics(y_train[h_train], np.ones(int(h_train.sum()), dtype=bool))
        enabled = cv_human["f1"] >= MIN_CV_F1_TO_ENABLE and cv_human["f1"] > flag_all_cv["f1"]

        # 3. final model and single evaluation on the test repositories
        model = make_gradient_boosting().fit(X_train, y_train)
        proba_test = model.predict_proba(X_test)[:, 1]
        pred_test = proba_test >= threshold
        test_all = _metrics(y_test, pred_test, proba_test)
        test_human = _metrics(y_test[h_test], pred_test[h_test], proba_test[h_test])
        flag_all_test = threshold_metrics(y_test[h_test], np.ones(int(h_test.sum()), dtype=bool))
        print(f"  threshold {threshold:.3f} | test human P {test_human['precision']:.3f} R {test_human['recall']:.3f} "
              f"F1 {test_human['f1']:.3f} (flag all {flag_all_test['f1']:.3f}) | test all F1 {test_all['f1']:.3f} | "
              f"CV F1 human {cv_human['f1']:.3f} (flag all {flag_all_cv['f1']:.3f}) "
              f"-> {'enabled' if enabled else 'disabled'}", flush=True)

        # 4. baselines
        single = best_single_metric_rule(X_train, y_train, names)
        single_pred = apply_single_metric_rule(single, X_test)
        single_summary = {
            "feature": single["feature"], "direction": single["direction"],
            "threshold": single["threshold"], "train_f1": single["f1"],
            "human": threshold_metrics(y_test[h_test], single_pred[h_test]),
            "all": threshold_metrics(y_test, single_pred),
        }
        rule_summary = None
        rule_pred = None
        if rule_all is not None:
            rule_pred = rule_all[is_test]
            rule_summary = {
                "rule": spec.rule_name,
                "human": threshold_metrics(y_test[h_test], rule_pred[h_test]),
                "all": threshold_metrics(y_test, rule_pred),
            }
            print(f"  CUQA rule ({spec.rule_name}) F1 human {rule_summary['human']['f1']:.3f} "
                  f"all {rule_summary['all']['f1']:.3f}")
        print(f"  single metric ({single['feature']} {single['direction']} {single['threshold']:g}) "
              f"F1 human {single_summary['human']['f1']:.3f} all {single_summary['all']['f1']:.3f}", flush=True)

        # 5. leave-one-repository-out over all repositories
        loro = leave_one_repository_out(X, y, groups, human, rule_all, names, RANDOM_SEED)
        print(f"  LORO pooled F1 human: model {loro['model']['pooled_human']['f1']:.3f}, "
              f"single metric {loro['single_metric']['pooled_human']['f1']:.3f}"
              + (f", CUQA rule {loro['cuqa_rule']['pooled_human']['f1']:.3f}" if "cuqa_rule" in loro else ""),
              flush=True)

        # 6. feature importance on the human-annotated test samples
        perm = permutation_importance(model, X_test[h_test], y_test[h_test], scoring="average_precision",
                                      n_repeats=20, random_state=RANDOM_SEED)
        assert isinstance(perm, Bunch)  # one scoring name gives one Bunch, not a dict of them
        order = np.argsort(-perm.importances_mean)
        importance = [(names[i], float(perm.importances_mean[i]), float(perm.importances_std[i])) for i in order]
        evidence = [names[i] for i in np.argsort(-model.feature_importances_)[:5]]

        results[spec.key] = {
            "data": {
                "train_all": int(len(y_train)), "train_all_positive": int(y_train.sum()),
                "train_human": int(h_train.sum()), "train_human_positive": int(y_train[h_train].sum()),
                "test_all": int(len(y_test)), "test_all_positive": int(y_test.sum()),
                "test_human": int(h_test.sum()), "test_human_positive": int(y_test[h_test].sum()),
            },
            "model_comparison": comparison,
            "threshold": threshold,
            "enabled": bool(enabled),
            "cross_validation_all": cv_all,
            "cross_validation_human": cv_human,
            "test_all": test_all,
            "test_human": test_human,
            "flag_all_human": {"cross_validation": flag_all_cv, "test": flag_all_test},
            "single_metric": single_summary,
            "cuqa_rule": rule_summary,
            "leave_one_repository_out": loro,
            "importance": importance,
            "y_test": y_test,
            "proba_test": proba_test,
            "human_test": h_test,
            "_X_test": X_test,
        }
        exported[spec.smell_type] = {
            "display_name": spec.display_name,
            "dataset": DATASET_NAME,
            "entity": spec.entity,
            "enabled": bool(enabled),
            "threshold": threshold,
            "severity": spec.severity,
            "features": names,
            "evidence_features": evidence,
            "metrics": {
                # test_* are measured on the human-annotated samples of the test
                # repositories; *_all_labels adds the threshold-labelled samples
                "test_precision": round(test_human["precision"], 4),
                "test_recall": round(test_human["recall"], 4),
                "test_f1": round(test_human["f1"], 4),
                "test_mcc": round(test_human["mcc"], 4),
                "test_roc_auc": round(test_human.get("roc_auc", 0.0), 4),
                "test_pr_auc": round(test_human.get("pr_auc", 0.0), 4),
                "test_f1_all_labels": round(test_all["f1"], 4),
                "cv_f1": round(cv_human["f1"], 4),
                "loro_f1": round(loro["model"]["pooled_human"]["f1"], 4),
                "test_samples": int(h_test.sum()),
                "test_positive": int(y_test[h_test].sum()),
            },
            **export_classifier(model, X_train),
        }
        for i, row in enumerate(test_table.itertuples()):
            prediction_rows.append({
                "smell": spec.key, "sample_id": row.sample_id, "repository": row.repository,
                "path": row.path, "entity_name": row.entity_name,
                "label_source": "human" if h_test[i] else "threshold",
                "label": int(y_test[i]), "probability": round(float(proba_test[i]), 5),
                "model_prediction": int(pred_test[i]),
                "cuqa_rule_prediction": "" if rule_pred is None else int(rule_pred[i]),
            })

    # ----- export and parity check -----------------------------------------
    payload = merge_into_model_file(exported, {
        "model_version": date.today().isoformat(),
        "trained_with": f"scikit-learn {sklearn.__version__}",
    })
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
        MODEL_OUT.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        print(f"Model written to {MODEL_OUT} ({MODEL_OUT.stat().st_size / 1024:.0f} KB), "
              f"smells: {', '.join(payload['smells'])}")

    plot_all(results)

    summary = {
        "generated_on": date.today().isoformat(),
        "dataset": DATASET_CITATION,
        "protocol": {
            "random_seed": RANDOM_SEED, "test_fraction": TEST_FRACTION,
            "cv_folds": CV_FOLDS, "cv_repeats": CV_REPEATS,
            "leave_one_repository_out_inner_folds": LORO_INNER_FOLDS,
            "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
            "min_cv_f1_to_enable": MIN_CV_F1_TO_ENABLE,
            "enable_rule": ("cross-validated F1 on the human-annotated training samples is at least "
                            "min_cv_f1_to_enable and higher than the F1 of flagging all of them"),
            "hyperparameters": GB_PARAMS,
            "scikit_learn": sklearn.__version__,
        },
        "split": {"train_repositories": sorted(train_repos), "test_repositories": sorted(test_repos)},
        "export_max_probability_difference": max_difference,
        "annotator_agreement": agreement,
        "smells": {
            spec.key: {k: v for k, v in results[spec.key].items()
                       if k not in ("y_test", "proba_test", "human_test", "_X_test")}
            for spec in SMELLS
        },
    }
    with open(RESULTS_DIR / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    write_tables_markdown(summary, RESULTS_DIR / "evaluation_tables.md")

    # about 36,000 rows, so the file is compressed like the dataset tables
    with gzip.open(RESULTS_DIR / "test_predictions.csv.gz", "wt", encoding="utf-8", newline="") as f:
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
