"""
evaluate_detectors.py
---------------------
Scores the whole CUQA Java smell detector, in each detection mode, on the test
repositories of the two labelled datasets.

    rules   every smell comes from the rule-based detectors
    hybrid  the shipped model decides the smells it learned and that are
            enabled; the rules decide the others (CUQA_SMELL_DETECTOR=hybrid)
    ml      only the model's smells
    hybrid_experimental
            hybrid with the switched-off model smells turned on as well
            (CUQA_ML_INCLUDE_EXPERIMENTAL=1); shown so the cost of turning
            them on is measured, not guessed

The test repositories are the ones train_model.py and train_dacos.py held out,
so neither the model nor its thresholds have seen them. The rules are the CUQA
rules as they run in the service:
    Long Method, Large Class  report_generator's Java rules, taken from the
                              per-sample predictions train_model.py saved
                              (they need the MLCQ source files)
    Feature Envy, Data Class, java_rules.py, scored on the feature tables
    Complex Method
    Long Parameter List       report_generator's "parameters > 5" rule
    Multifaceted Abstraction  no rule

Labelled smells only: MLCQ labels four smells and DACOS three. The other CUQA
smells (Magic Number, Dead Code, Message Chains, ...) have no labelled data
here and are not part of these numbers.

Writes results/combined_detector.md, .json and two figures:
combined_detector.png (micro F1 per mode) and combined_per_smell.png.

Usage:
    python training/evaluate_detectors.py
"""

import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

TRAINING_DIR = Path(__file__).resolve().parent
SRC_DIR = TRAINING_DIR.parent / "src"
for _path in (SRC_DIR, TRAINING_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import java_rules  # noqa: E402
from ml_detector.model import load_model  # noqa: E402
from train_dacos import labelled_rows  # noqa: E402
from train_model import (  # noqa: E402
    BOOTSTRAP_ITERATIONS,
    RANDOM_SEED,
    load_table,
    split_repositories,
    threshold_metrics,
)

DATASET_DIR = TRAINING_DIR / "dataset"
RESULTS_DIR = TRAINING_DIR / "results"
MLCQ_PREDICTIONS = RESULTS_DIR / "test_predictions.csv"
MODES = ("rules", "hybrid", "ml", "hybrid_experimental")


@dataclass(frozen=True)
class LabelledSmell:
    dataset: str
    key: str            # label column suffix
    smell_type: str     # CUQA smell type
    display_name: str
    entity: str
    rule: Callable[[pd.DataFrame], np.ndarray] | None   # rule prediction for every row, None = no rule


def _feature_rule(predicate) -> Callable[[pd.DataFrame], np.ndarray]:
    def apply(table: pd.DataFrame) -> np.ndarray:
        return np.array([bool(predicate(row)) for row in table.to_dict("records")])
    return apply


def _saved_rule(smell_key: str) -> Callable[[pd.DataFrame], np.ndarray]:
    """CUQA rule predictions train_model.py stored for the MLCQ test samples."""
    def apply(table: pd.DataFrame) -> np.ndarray:
        saved = pd.read_csv(MLCQ_PREDICTIONS)
        saved = saved[saved["smell"] == smell_key].set_index("sample_id")["cuqa_rule_prediction"]
        values = table["sample_id"].map(saved)
        if values.isna().any():
            raise SystemExit(f"{MLCQ_PREDICTIONS} has no CUQA rule prediction for some {smell_key} samples")
        return values.to_numpy(dtype=int).astype(bool)
    return apply


def _data_class_rule(table: pd.DataFrame) -> np.ndarray:
    # the runtime rule only looks at plain classes (not interfaces, enums, annotations)
    return _feature_rule(java_rules.is_data_class)(table) & (table["type_kind"] == "class").to_numpy()


SMELLS = (
    LabelledSmell("MLCQ", "long_method", "LongMethod", "Long Method", "method", _saved_rule("long_method")),
    LabelledSmell("MLCQ", "feature_envy", "FeatureEnvy", "Feature Envy", "method",
                  _feature_rule(java_rules.is_feature_envy)),
    LabelledSmell("MLCQ", "blob", "LargeClass", "Blob (Large Class)", "class", _saved_rule("blob")),
    LabelledSmell("MLCQ", "data_class", "DataClass", "Data Class", "class", _data_class_rule),
    LabelledSmell("DACOS", "complex_method", "ComplexMethod", "Complex Method", "method",
                  _feature_rule(java_rules.is_complex_method)),
    LabelledSmell("DACOS", "long_parameter_list", "TooManyParameters", "Long Parameter List", "method",
                  lambda t: t["n_params"].to_numpy() > 5),
    LabelledSmell("DACOS", "multifaceted_abstraction", "MultifacetedAbstraction", "Multifaceted Abstraction",
                  "class", None),
)


def test_rows() -> dict[str, pd.DataFrame]:
    """Test-repository rows of every labelled smell, as the training scripts split them."""
    mlcq = {"class": load_table("mlcq_classes.csv"), "method": load_table("mlcq_methods.csv")}
    dacos = {"class": pd.read_csv(DATASET_DIR / "dacos_classes.csv.gz", low_memory=False),
             "method": pd.read_csv(DATASET_DIR / "dacos_methods.csv.gz", low_memory=False)}
    _, mlcq_test = split_repositories(*mlcq.values())
    _, dacos_test = split_repositories(*dacos.values())

    rows = {}
    for spec in SMELLS:
        if spec.dataset == "MLCQ":
            table = mlcq[spec.entity]
            table = table[table[f"label_{spec.key}"].notna()]
            table = table[table["repository"].isin(mlcq_test)].reset_index(drop=True)
            table["label_source"] = "expert"
        else:
            table = labelled_rows(dacos[spec.entity], spec.key)
            table = table[table["repository"].isin(dacos_test)].reset_index(drop=True)
            table["label_source"] = table[f"label_source_{spec.key}"]
        rows[spec.key] = table
    return rows


def predictions(spec: LabelledSmell, table: pd.DataFrame, model) -> dict[str, np.ndarray]:
    """What each detection mode reports for every row."""
    n = len(table)
    rule = spec.rule(table) if spec.rule else np.zeros(n, dtype=bool)
    classifier = model.classifiers.get(spec.smell_type)
    trained = classifier is not None and classifier.entity == spec.entity
    learned = trained and classifier.enabled
    if trained:
        feature_rows = table[classifier.feature_names].to_dict("records")
        model_pred = np.array([classifier.predict_proba(f) >= classifier.threshold for f in feature_rows])
    else:
        model_pred = np.zeros(n, dtype=bool)
    return {
        "rules": rule,
        "hybrid": model_pred if learned else rule,
        "ml": model_pred if learned else np.zeros(n, dtype=bool),
        "hybrid_experimental": model_pred if trained else rule,
        "_learned": np.array(learned),
        "_trained": np.array(trained),
        "_has_rule": np.array(spec.rule is not None),
    }


def _summed(parts: list[dict]) -> dict:
    """Micro-average: one confusion matrix over every (sample, smell) decision."""
    tp = sum(p["tp"] for p in parts)
    fp = sum(p["fp"] for p in parts)
    fn = sum(p["fn"] for p in parts)
    tn = sum(p["tn"] for p in parts)
    y = np.array([1] * (tp + fn) + [0] * (fp + tn))
    pred = np.array([1] * tp + [0] * fn + [1] * fp + [0] * tn)
    return threshold_metrics(y, pred)


def micro_ci(groups_by_smell: list[tuple], mode: str, iterations: int, seed: int) -> list[float]:
    """95% interval of the micro F1, resampling test repositories (MLCQ only)."""
    rng = np.random.RandomState(seed)
    repositories = sorted({g for groups, _, _ in groups_by_smell for g in groups})
    index = {r: i for i, r in enumerate(repositories)}
    # per repository and smell: tp, fp, fn counts
    counts = np.zeros((len(repositories), 3))
    for groups, y, preds in groups_by_smell:
        pred = preds[mode]
        for g, yi, pi in zip(groups, y, pred):
            row = index[g]
            counts[row, 0] += yi and pi
            counts[row, 1] += (not yi) and pi
            counts[row, 2] += yi and not pi
    values = []
    for _ in range(iterations):
        picked = rng.randint(0, len(repositories), len(repositories))
        tp, fp, fn = counts[picked].sum(axis=0)
        if tp + fn == 0:
            continue
        values.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return [float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))]


def _fmt(v) -> str:
    return "n/a" if v is None else f"{v:.3f}"


# One colour per mode, in a fixed order, checked for colour-blind separation.
MODE_STYLE = {
    "rules": ("Rules only", "#2a78d6"),
    "ml": ("ML model only", "#eb6834"),
    "hybrid": ("Hybrid (shipped)", "#1baf7a"),
    "hybrid_experimental": ("Hybrid + switched-off model smells", "#eda100"),
}
LABEL_INK = "#52514e"
SCOPES = (("MLCQ_all", "MLCQ\n4 smells, expert labels"),
          ("DACOS_human", "DACOS\n3 smells, human-annotated"),
          ("DACOS_all", "DACOS\n3 smells, all labels"))


def _bar_label(ax, x, top, text, rotate=False):
    ax.text(x, top + 0.015, text, ha="center", va="bottom", fontsize=8, color=LABEL_INK,
            rotation=90 if rotate else 0)


def plot_whole_detector(overall: dict, per_smell: dict, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(11, 5))
    modes = ("rules", "ml", "hybrid", "hybrid_experimental")
    x = np.arange(len(SCOPES))
    width = 0.2
    for k, mode in enumerate(modes):
        label, color = MODE_STYLE[mode]
        offset = (k - (len(modes) - 1) / 2) * width
        values = [overall[scope][mode]["micro"]["f1"] for scope, _ in SCOPES]
        lower, upper = [], []
        for scope, _ in SCOPES:
            ci = overall[scope][mode].get("micro_f1_ci95")
            f1 = overall[scope][mode]["micro"]["f1"]
            lower.append(f1 - ci[0] if ci else np.nan)   # nan: no error bar drawn
            upper.append(ci[1] - f1 if ci else np.nan)
        ax.bar(x + offset, values, width, label=label, color=color, edgecolor="white", linewidth=1.5,
               yerr=[lower, upper], error_kw={"ecolor": "black", "elinewidth": 1, "capsize": 3})
        for i, (scope, _) in enumerate(SCOPES):
            dataset = scope.split("_")[0]
            model_off = not any(r["model_decides_in_hybrid"] for r in per_smell.values() if r["dataset"] == dataset)
            if mode == "ml" and model_off:
                _bar_label(ax, x[i] + offset, 0.0, "model off", rotate=True)
            else:
                top = values[i] + (0.0 if np.isnan(upper[i]) else upper[i])
                _bar_label(ax, x[i] + offset, top, f"{values[i]:.2f}")
    ax.set_xticks(x, [name for _, name in SCOPES])
    ax.set_ylabel("Micro F1 over all labelled smells")
    ax.set_ylim(0, 1.0)
    ax.grid(True, axis="y", alpha=0.3)
    ax.set_title("The whole CUQA Java smell detector on the test repositories, by detection mode")
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    fig.text(0.01, 0.01, "Error bars: 95% interval from resampling the MLCQ test repositories. "
             "DACOS has two test repositories, too few for an interval.", fontsize=8, color=LABEL_INK)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_per_smell(per_smell: dict, path: Path) -> None:
    panels = (("MLCQ", "all", "MLCQ (expert labels)"),
              ("DACOS", "human", "DACOS, human-annotated samples"),
              ("DACOS", "all", "DACOS, all labelled samples"))
    modes = ("rules", "hybrid", "hybrid_experimental")
    fig, axes = plt.subplots(1, len(panels), figsize=(17, 5.2), sharey=True,
                             gridspec_kw={"width_ratios": [4, 3, 3]})
    width = 0.26
    for ax, (dataset, subset, title) in zip(axes, panels):
        specs = [s for s in SMELLS if s.dataset == dataset]
        x = np.arange(len(specs))
        for k, mode in enumerate(modes):
            label, color = MODE_STYLE[mode]
            offset = (k - (len(modes) - 1) / 2) * width
            values = [per_smell[s.key][mode][subset]["f1"] for s in specs]
            ax.bar(x + offset, values, width, label=label, color=color, edgecolor="white", linewidth=1.5)
            for i, (s, v) in enumerate(zip(specs, values)):
                r = per_smell[s.key]
                if mode == "rules" and not r["has_rule"]:
                    _bar_label(ax, x[i] + offset, 0.0, "no rule", rotate=True)
                elif mode == "hybrid" and not r["has_rule"] and not r["model_decides_in_hybrid"]:
                    _bar_label(ax, x[i] + offset, 0.0, "nothing", rotate=True)
                else:
                    _bar_label(ax, x[i] + offset, v, f"{v:.2f}")
        ax.set_xticks(x, [s.display_name.replace(" (Large Class)", "\n(Large Class)")
                          .replace("Multifaceted Abstraction", "Multifaceted\nAbstraction")
                          .replace("Long Parameter List", "Long Parameter\nList") for s in specs])
        ax.set_title(title)
        ax.set_ylim(0, 1.0)
        ax.grid(True, axis="y", alpha=0.3)
    axes[0].set_ylabel("F1 on the test repositories")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False)
    fig.suptitle("F1 per smell: who decides the smell in each mode")
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.savefig(path, dpi=160)
    plt.close(fig)


def main() -> int:
    model = load_model()
    rows = test_rows()

    per_smell: dict[str, dict] = {}
    pooled: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    mlcq_groups: list[tuple] = []
    for spec in SMELLS:
        table = rows[spec.key]
        y = table[f"label_{spec.key}"].to_numpy(dtype=int).astype(bool)
        preds = predictions(spec, table, model)
        subsets = {"all": np.ones(len(table), dtype=bool)}
        if spec.dataset == "DACOS":
            subsets["human"] = (table["label_source"] == "human").to_numpy()
        result = {
            "dataset": spec.dataset, "smell_type": spec.smell_type,
            "model_decides_in_hybrid": bool(preds["_learned"]),
            "model_trained": bool(preds["_trained"]),
            "has_rule": bool(preds["_has_rule"]),
            "samples": {name: int(mask.sum()) for name, mask in subsets.items()},
            "positive": {name: int(y[mask].sum()) for name, mask in subsets.items()},
        }
        for mode in MODES:
            result[mode] = {name: threshold_metrics(y[mask], preds[mode][mask]) for name, mask in subsets.items()}
            for name in subsets:
                pooled[f"{spec.dataset}_{name}"][mode].append(result[mode][name])
        per_smell[spec.key] = result
        if spec.dataset == "MLCQ":
            mlcq_groups.append((table["repository"].to_numpy(), y, preds))

    overall = {}
    for scope, by_mode in pooled.items():
        overall[scope] = {}
        for mode, parts in by_mode.items():
            overall[scope][mode] = {
                "micro": _summed(parts),
                "macro_f1": float(np.mean([p["f1"] for p in parts])),
                "macro_mcc": float(np.mean([p["mcc"] for p in parts])),
            }
    for mode in MODES:
        overall["MLCQ_all"][mode]["micro_f1_ci95"] = micro_ci(mlcq_groups, mode, BOOTSTRAP_ITERATIONS, RANDOM_SEED)

    summary = {
        "generated_on": date.today().isoformat(),
        "model_version": model.version,
        "modes": {"rules": "rules only", "hybrid": "model for its enabled smells, rules for the rest",
                  "ml": "model only",
                  "hybrid_experimental": "hybrid with the switched-off model smells turned on"},
        "smells": per_smell,
        "overall": overall,
    }
    with open(RESULTS_DIR / "combined_detector.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    plot_whole_detector(overall, per_smell, RESULTS_DIR / "combined_detector.png")
    plot_per_smell(per_smell, RESULTS_DIR / "combined_per_smell.png")

    lines = ["# CUQA Java smell detector as a whole: evaluation on the test repositories", "",
             "Generated by `training/evaluate_detectors.py`. Do not edit by hand.", "",
             f"Model version {model.version}. Modes: **rules** = rule-based detectors only; **hybrid** = the model "
             "decides the smells it learned and that are enabled, the rules decide the rest; **ml** = the model only; "
             "**hybrid_experimental** = hybrid with the model smells that are switched off turned on as well "
             "(`CUQA_ML_INCLUDE_EXPERIMENTAL=1`), to show what turning them on would change.",
             "", "MLCQ labels are expert reviews. DACOS *human* labels are annotations of borderline cases; DACOS "
             "*all* adds the cases the DACOS authors labelled with metric thresholds. Only labelled smells are "
             "scored: Magic Number, Dead Code, Message Chains and the other rule-only smells have no labelled "
             "data here.", ""]

    lines += ["## Whole detector", "",
              "Micro = one confusion matrix over every (sample, smell) decision. Macro = mean of the per-smell values.", "",
              "| Test set | Mode | Precision | Recall | Micro F1 | Micro MCC | Accuracy | Macro F1 | Macro MCC |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    scope_names = {"MLCQ_all": "MLCQ, 4 smells", "DACOS_human": "DACOS human, 3 smells", "DACOS_all": "DACOS all, 3 smells"}
    for scope in ("MLCQ_all", "DACOS_human", "DACOS_all"):
        for mode in MODES:
            o = overall[scope][mode]
            m = o["micro"]
            ci = o.get("micro_f1_ci95")
            f1 = f"{m['f1']:.3f}" + (f" [{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "")
            lines.append(f"| {scope_names[scope]} | {mode} | {_fmt(m['precision'])} | {_fmt(m['recall'])} | {f1} | "
                         f"{_fmt(m['mcc'])} | {_fmt(m['accuracy'])} | {_fmt(o['macro_f1'])} | {_fmt(o['macro_mcc'])} |")
    lines += ["", "The MLCQ micro F1 interval resamples the test repositories (95%, "
              f"{BOOTSTRAP_ITERATIONS} resamples). DACOS has two test repositories, too few for an interval.", ""]

    lines += ["## Per smell", "",
              "| Dataset | Smell | Samples (positive) | Hybrid uses | Rules F1 | Hybrid P | Hybrid R | Hybrid F1 | "
              "Switched-off model F1 |",
              "|---|---|---|---|---:|---:|---:|---:|---:|"]
    for spec in SMELLS:
        r = per_smell[spec.key]
        for subset in r["samples"]:
            label = spec.display_name + ("" if subset == "all" and spec.dataset == "MLCQ" else f" ({subset})")
            uses = "model" if r["model_decides_in_hybrid"] else ("rule" if r["has_rule"] else "nothing")
            rules_f1 = _fmt(r["rules"][subset]["f1"]) if r["has_rule"] else "no rule"
            off_f1 = (_fmt(r["hybrid_experimental"][subset]["f1"])
                      if r["model_trained"] and not r["model_decides_in_hybrid"] else "")
            h = r["hybrid"][subset]
            lines.append(f"| {spec.dataset} | {label} | {r['samples'][subset]} ({r['positive'][subset]}) | {uses} | "
                         f"{rules_f1} | {_fmt(h['precision'])} | {_fmt(h['recall'])} | {_fmt(h['f1'])} | {off_f1} |")
    lines.append("")
    (RESULTS_DIR / "combined_detector.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
