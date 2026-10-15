"""
check_rdp_dataset.py
--------------------
Checks whether agents/rdp_agent/datasets/code_smells_refactoring_dataset_120k.csv
can be used to train a code smell detector.

A dataset is only useful for this if its columns carry information about the
smell label. The script tests that directly: it looks for any statistical link
between each column and the label, and it trains two standard classifiers to
see whether they can beat random guessing.

Usage:
    python training/check_rdp_dataset.py
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency, f_oneway
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score

REPO_ROOT = Path(__file__).resolve().parents[3]
DATASET = REPO_ROOT / "agents" / "rdp_agent" / "datasets" / "code_smells_refactoring_dataset_120k.csv"

NUMERIC = [
    "lines_of_code", "cyclomatic_complexity", "num_methods", "num_classes",
    "pre_refactor_complexity", "post_refactor_complexity", "technical_debt_minutes",
    "maintainability_index", "bug_prone_score", "developer_experience_years",
]
CATEGORICAL = ["language", "framework", "smell_severity", "refactoring_suggested", "refactoring_applied"]
LABEL = "code_smell_type"


def main() -> int:
    warnings.filterwarnings("ignore")
    df = pd.read_csv(DATASET)
    n_classes = df[LABEL].nunique()
    print(f"Rows: {len(df)}, smell classes: {n_classes}, chance accuracy: {1 / n_classes:.4f}")

    extensions = df["file_name"].str.extract(r"(\.\w+)$")[0]
    print("\nFile extension per language (a real dataset would not have .py for every language):")
    print(pd.crosstab(df["language"], extensions).to_string())

    print("\nIs any numeric column related to the smell label? (one-way ANOVA)")
    for column in NUMERIC:
        groups = [g[column].to_numpy() for _, g in df.groupby(LABEL)]
        print(f"  {column:28s} p = {f_oneway(*groups).pvalue:.3f}")

    print("\nIs any categorical column related to the smell label? (chi-squared)")
    for column in CATEGORICAL:
        table = pd.crosstab(df[column].fillna("missing"), df[LABEL])
        print(f"  {column:28s} p = {chi2_contingency(table)[1]:.3f}")

    correlations = df[NUMERIC].corr().to_numpy()
    largest = np.abs(correlations - np.eye(len(NUMERIC))).max()
    print(f"\nLargest correlation between two numeric columns: {largest:.4f}")

    print("\nCan a classifier predict the smell? (5-fold cross-validation on 40,000 rows)")
    sample = df.sample(40000, random_state=0)
    X = pd.get_dummies(sample[NUMERIC + CATEGORICAL].fillna("missing"))
    y = sample[LABEL]
    folds = StratifiedKFold(5, shuffle=True, random_state=0)
    for name, model in (
        ("Random Forest", RandomForestClassifier(200, random_state=0)),
        ("Gradient Boosting", HistGradientBoostingClassifier(random_state=0)),
    ):
        scores = cross_val_score(model, X, y, cv=folds, scoring="accuracy")
        print(f"  {name:18s} accuracy = {scores.mean():.4f} +/- {scores.std():.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
