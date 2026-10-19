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

## Files

| Path | Content |
|---|---|
| `fetch_mlcq_sources.py` | Downloads the MLCQ source files |
| `build_dataset.py` | Builds the labeled feature tables |
| `train_model.py` | Model comparison, training, evaluation, export |
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
