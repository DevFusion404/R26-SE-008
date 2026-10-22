# CUQA ML smell model: training pipeline

This folder holds everything needed to rebuild the model that the CUQA agent
loads from `src/ml_detector/artifacts/smell_model.json`.

The CUQA service does not need anything in this folder at runtime. It only
reads the exported JSON model.

## Steps

```bash
pip install -r training/requirements-training.txt
```

Run the three scripts from `agents/cuqa_agent`:

```bash
python training/fetch_mlcq_sources.py
```

Downloads the Java files referenced by `evaluation/data/MLCQCodeSmellSamples.csv`
from GitHub into `training/data/mlcq_sources/` (about 150 MB, not committed).
MLCQ stores labels and links only, so the code has to be fetched.

```bash
python training/build_dataset.py
```

Extracts features with `src/ml_detector/java_features.py`, matches each MLCQ
sample to a class or method by line range and writes the feature tables to
`training/dataset/`.

```bash
python training/train_model.py
```

Trains, evaluates and exports the model. Writes metrics, tables and figures to
`training/results/` and the model to `src/ml_detector/artifacts/smell_model.json`.
Use `--no-export` to evaluate without replacing the shipped model.

Steps 1 and 2 can be skipped when only retraining, because the feature tables
in `training/dataset/` are committed. In that case the comparison with the
existing CUQA rules is left out, since it needs the source files.

### DACOS smells (Complex Method, Long Parameter List, Multifaceted Abstraction)

Download `DACOSMain.sql`, `DACOSExtended.sql` and `files.zip` from
<https://doi.org/10.5281/zenodo.7570428> (CC BY 4.0, about 140 MB) into
`training/data/dacos/` (not committed), then:

```bash
python training/build_dacos_dataset.py
python training/train_dacos.py
```

`build_dacos_dataset.py` reads the MySQL dumps directly (no MySQL needed),
extracts features from the DACOS code snippets and writes
`dataset/dacos_methods.csv.gz` and `dataset/dacos_classes.csv.gz`. Each label
keeps its source: `human` (two people annotated a borderline snippet) or
`threshold` (the DACOS authors labelled a clear case with a metric threshold).

`train_dacos.py` uses the same protocol as `train_model.py` and adds the three
smells to the shipped model file; the MLCQ smells in that file are kept, and a
rerun of `train_model.py` keeps the DACOS smells. Results go to
`results/dacos/`. Every number is given for the human-annotated samples and
for all samples, because the threshold-labelled samples are easy by
construction.

### The whole detector

```bash
python training/evaluate_detectors.py
```

Scores the CUQA Java detector in `rules`, `hybrid` and `ml` mode on the test
repositories of both datasets, per smell and as a whole (micro and macro
averages). Writes `results/combined_detector.md` and `.json`.

## Files

| Path | Content |
|---|---|
| `fetch_mlcq_sources.py` | Downloads the MLCQ source files |
| `build_dataset.py` | Builds the labeled feature tables |
| `train_model.py` | Model comparison, training, evaluation, export |
| `build_dacos_dataset.py` | Builds the DACOS feature tables from the downloaded dumps |
| `train_dacos.py` | DACOS model comparison, training, evaluation, export |
| `evaluate_detectors.py` | The whole Java detector per mode on both test sets |
| `export_preprocessed.py` | Writes `dataset/preprocessed/`: one CSV per smell for both models, with split and label (see its `DATA_DICTIONARY.md`) |
| `dataset/dacos_*.csv.gz` | DACOS samples with features and labelled source |
| `results/dacos/` | DACOS metrics, tables and figures (`python training/train_dacos.py --plots-only` redraws the figures without training) |
| `results/combined_detector.md`, `combined_*.png` | Whole-detector results and figures per mode |
| `dataset/mlcq_classes.csv` | Class samples: Blob and Data Class labels with features |
| `dataset/mlcq_methods.csv` | Method samples: Long Method and Feature Envy labels with features |
| `dataset/dataset_report.json` | How many samples were available, parsed and matched |
| `results/metrics.json` | All evaluation numbers |
| `results/evaluation_tables.md` | The same numbers as tables |
| `results/test_predictions.csv` | Per-sample predictions on the test repositories |
| `results/*.png` | Precision-recall curves, ROC curves, confusion matrices, feature importance, baseline comparison |
| `MODEL_EVALUATION.md` | Method, results and discussion |

## Reproducibility

The repository split, cross-validation folds, model and bootstrap all use fixed
seeds, so a rerun on the same feature tables gives the same numbers. A rerun of
`fetch_mlcq_sources.py` can change the dataset slightly if more repositories
have been removed from GitHub in the meantime.
