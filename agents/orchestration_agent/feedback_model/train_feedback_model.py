"""
Feedback Manager — ML Model Training
======================================
R26-SE-008 | IT22277886 | Bandara S M Y M

This module trains and evaluates a machine learning model on developer
feedback collected during DIWO Agent workflow sessions.

PURPOSE:
  The Feedback Manager uses this model to PREDICT:
    - Whether a developer will ACCEPT or REJECT a refactoring plan step
    - The likely developer RATING for a given refactoring recommendation
  
  These predictions are fed back into the orchestration layer to:
    - Reorder or highlight plan steps most likely to be accepted
    - Warn before presenting steps likely to be rejected
    - Personalize refactoring recommendations over time

FEATURES USED (from feedback_entries table):
  - stage            : which workflow stage the feedback came from
  - action           : developer action taken
  - smell_type       : code smell type
  - refactoring_type : refactoring suggested
  - severity         : smell severity (critical/high/medium/low)
  - rating           : developer rating (1-5)
  - accepted         : binary outcome label (0/1)

USAGE:
  python feedback_model/train_feedback_model.py \
      --db ../backend/diwo_audit.db \
      --out ./feedback_model_output

  Or run directly (uses synthetic data if DB has < 20 records):
  python feedback_model/train_feedback_model.py
"""

import os
import sys
import json
import sqlite3
import argparse
import random
import pickle
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.ensemble import (
    RandomForestClassifier, GradientBoostingClassifier,
    RandomForestRegressor, GradientBoostingRegressor,
)
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    classification_report, confusion_matrix,
    mean_absolute_error, r2_score, roc_auc_score,
)
from sklearn.dummy import DummyClassifier
warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

SMELL_TYPES = [
    "Long Method", "God Class", "Feature Envy", "Duplicate Code",
    "Long Parameter List", "Data Clumps", "Primitive Obsession",
    "Shotgun Surgery", "Divergent Change", "Dead Code",
    "Large Class", "Switch Statements", "Lazy Class",
]

REFACTORING_TYPES = [
    "Extract Method", "Extract Class", "Move Method",
    "Introduce Parameter Object", "Replace Data Value with Object",
    "Rename Method", "Inline Class", "Collapse Hierarchy",
    "Replace Conditional with Polymorphism", "Remove Dead Code",
]

SEVERITIES = ["critical", "high", "medium", "low"]

STAGES = ["smell_review", "smell_selection", "plan_approval",
          "transformation", "comparison"]

ACTIONS = ["smell_excluded", "smell_selected", "plan_approved",
           "plan_rejected", "plan_modified", "transformation_accepted",
           "rollback_triggered"]

# Acceptance rates by smell severity (for realistic synthetic data)
SEVERITY_ACCEPT_RATES = {"critical": 0.85, "high": 0.75, "medium": 0.60, "low": 0.45}

# Acceptance rates by refactoring type
REFACTORING_ACCEPT_RATES = {
    "Extract Method": 0.82, "Remove Dead Code": 0.90, "Rename Method": 0.88,
    "Inline Class": 0.70, "Introduce Parameter Object": 0.75,
    "Extract Class": 0.65, "Move Method": 0.60,
    "Replace Data Value with Object": 0.55,
    "Replace Conditional with Polymorphism": 0.50,
    "Collapse Hierarchy": 0.45,
}


# ─────────────────────────────────────────────────────────────────────────────
# Data Loading
# ─────────────────────────────────────────────────────────────────────────────

def load_from_db(db_path: str) -> pd.DataFrame:
    """Load feedback_entries from the DIWO SQLite database."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM feedback_entries", conn)
    conn.close()
    print(f"[Data] Loaded {len(df)} records from database.")
    return df


def generate_synthetic_data(n: int = 800) -> pd.DataFrame:
    """
    Generate realistic synthetic training data when real feedback is sparse.
    Simulates developer feedback patterns across multiple workflow sessions.
    """
    random.seed(42)
    np.random.seed(42)

    records = []
    for i in range(n):
        smell = random.choice(SMELL_TYPES)
        refactoring = random.choice(REFACTORING_TYPES)
        severity = random.choices(SEVERITIES, weights=[0.1, 0.3, 0.4, 0.2])[0]
        stage = random.choice(STAGES)
        action = random.choice(ACTIONS)

        # Acceptance probability is a weighted combination of factors
        p_sev = SEVERITY_ACCEPT_RATES.get(severity, 0.6)
        p_ref = REFACTORING_ACCEPT_RATES.get(refactoring, 0.6)
        p_accept = 0.5 * p_sev + 0.5 * p_ref + random.gauss(0, 0.08)
        p_accept = max(0.05, min(0.95, p_accept))
        accepted = int(random.random() < p_accept)

        # Rating correlates with acceptance but has noise
        base_rating = 3.5 * p_accept + 1.0
        rating = int(max(1, min(5, round(base_rating + random.gauss(0, 0.6)))))

        records.append({
            "id": i,
            "workflow_id": f"wf_synth_{i // 20:03d}",
            "stage": stage,
            "action": action,
            "smell_type": smell,
            "refactoring_type": refactoring,
            "severity": severity,
            "reason": "",
            "rating": rating if random.random() > 0.25 else None,
            "accepted": accepted,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    df = pd.DataFrame(records)
    print(f"[Data] Generated {len(df)} synthetic training records.")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Feature Engineering
# ─────────────────────────────────────────────────────────────────────────────

CATEGORICAL_COLS = ["stage", "action", "smell_type", "refactoring_type", "severity"]

def encode_features(df: pd.DataFrame, encoders: Optional[dict] = None, fit: bool = True):
    """
    Encode categorical columns and build feature matrix.
    Returns: (X, encoders_dict)
    """
    df = df.copy()

    # Fill missing categoricals
    for col in CATEGORICAL_COLS:
        if col not in df.columns:
            df[col] = "unknown"
        df[col] = df[col].fillna("unknown").astype(str)

    # Fill missing rating with median
    if "rating" in df.columns:
        df["rating"] = df["rating"].fillna(df["rating"].median())
    else:
        df["rating"] = 3.0

    if encoders is None:
        encoders = {}

    encoded = {}
    for col in CATEGORICAL_COLS:
        if fit:
            le = LabelEncoder()
            encoded[col] = le.fit_transform(df[col])
            encoders[col] = le
        else:
            le = encoders[col]
            known = set(le.classes_)
            df[col] = df[col].apply(lambda x: x if x in known else le.classes_[0])
            encoded[col] = le.transform(df[col])

    # Severity ordinal
    sev_map = {"critical": 3, "high": 2, "medium": 1, "low": 0, "unknown": -1}
    encoded["severity_ord"] = df["severity"].map(lambda x: sev_map.get(x, -1))

    # Rating as numeric feature (for predicting acceptance)
    encoded["rating_num"] = df["rating"].astype(float)

    feature_cols = CATEGORICAL_COLS + ["severity_ord", "rating_num"]
    X = pd.DataFrame({
        col: encoded.get(col, encoded.get(col.replace("_ord", "").replace("_num", "")))
        for col in feature_cols
    })

    return X, encoders


# ─────────────────────────────────────────────────────────────────────────────
# Model Training
# ─────────────────────────────────────────────────────────────────────────────

def train_acceptance_classifier(X_train, y_train, X_test, y_test):
    """
    Binary classification: will the developer accept? (0/1)
    Compares multiple models and returns the best.
    """
    print("\n[Acceptance Classifier]")
    candidates = {
        "RandomForest":       RandomForestClassifier(n_estimators=100, random_state=42, class_weight="balanced"),
        "GradientBoosting":   GradientBoostingClassifier(n_estimators=100, random_state=42),
        "LogisticRegression": LogisticRegression(max_iter=1000, random_state=42, class_weight="balanced"),
        "Baseline(Majority)": DummyClassifier(strategy="most_frequent"),
    }

    best_model, best_auc = None, -1
    results = {}

    for name, clf in candidates.items():
        clf.fit(X_train, y_train)
        y_pred = clf.predict(X_test)
        y_prob = clf.predict_proba(X_test)[:, 1] if hasattr(clf, "predict_proba") else y_pred

        try:
            auc = roc_auc_score(y_test, y_prob)
        except Exception:
            auc = 0.5

        cv_scores = cross_val_score(clf, X_train, y_train, cv=5, scoring="roc_auc")
        results[name] = {
            "roc_auc_test":  round(auc, 4),
            "cv_roc_auc":    round(cv_scores.mean(), 4),
            "cv_std":        round(cv_scores.std(), 4),
            "report":        classification_report(y_test, y_pred, output_dict=True),
        }

        print(f"  {name:25s} → AUC={auc:.4f}  CV={cv_scores.mean():.4f}±{cv_scores.std():.4f}")

        if auc > best_auc and name != "Baseline(Majority)":
            best_auc = auc
            best_model = clf

    print(f"\n  ✓ Best classifier: {type(best_model).__name__} (AUC={best_auc:.4f})")
    return best_model, results


def train_rating_regressor(X_train, y_train, X_test, y_test):
    """
    Regression: predict developer rating (1–5).
    """
    print("\n[Rating Regressor]")
    candidates = {
        "RandomForestRegressor":     RandomForestRegressor(n_estimators=100, random_state=42),
        "GradientBoostingRegressor": GradientBoostingRegressor(n_estimators=100, random_state=42),
        "Ridge":                     Ridge(alpha=1.0),
    }

    best_model, best_r2 = None, -np.inf
    results = {}

    for name, reg in candidates.items():
        reg.fit(X_train, y_train)
        y_pred = reg.predict(X_test)
        mae = mean_absolute_error(y_test, y_pred)
        r2  = r2_score(y_test, y_pred)
        results[name] = {"mae": round(mae, 4), "r2": round(r2, 4)}
        print(f"  {name:30s} → MAE={mae:.4f}  R²={r2:.4f}")

        if r2 > best_r2:
            best_r2 = r2
            best_model = reg

    print(f"\n  ✓ Best regressor: {type(best_model).__name__} (R²={best_r2:.4f})")
    return best_model, results


# ─────────────────────────────────────────────────────────────────────────────
# Feature Importance
# ─────────────────────────────────────────────────────────────────────────────

def print_feature_importance(model, feature_names):
    if not hasattr(model, "feature_importances_"):
        return
    importances = model.feature_importances_
    pairs = sorted(zip(feature_names, importances), key=lambda x: -x[1])
    print("\n  Feature importances:")
    for feat, imp in pairs:
        bar = "█" * int(imp * 40)
        print(f"    {feat:20s} {bar} {imp:.4f}")


# ─────────────────────────────────────────────────────────────────────────────
# Inference Helper (used by Flask backend)
# ─────────────────────────────────────────────────────────────────────────────

class FeedbackPredictor:
    """
    Loaded by the DIWO backend at runtime to score refactoring recommendations.

    Usage:
        predictor = FeedbackPredictor.load("feedback_model_output/")
        result = predictor.predict(
            stage="plan_approval",
            action="plan_approved",
            smell_type="Long Method",
            refactoring_type="Extract Method",
            severity="high",
            rating=None,
        )
        # → {"accept_probability": 0.87, "predicted_rating": 4.1, "recommendation": "high_confidence"}
    """

    def __init__(self, classifier, regressor, encoders):
        self.classifier = classifier
        self.regressor  = regressor
        self.encoders   = encoders

    def predict(self, stage, action, smell_type, refactoring_type, severity, rating=None):
        row = pd.DataFrame([{
            "stage": stage or "plan_approval",
            "action": action or "plan_approved",
            "smell_type": smell_type or "Unknown",
            "refactoring_type": refactoring_type or "Unknown",
            "severity": severity or "medium",
            "rating": rating if rating is not None else 3,
        }])

        X, _ = encode_features(row, encoders=self.encoders, fit=False)

        p_accept = float(self.classifier.predict_proba(X)[0][1])
        pred_rating = float(np.clip(self.regressor.predict(X)[0], 1, 5))

        if p_accept >= 0.75:
            recommendation = "high_confidence"
        elif p_accept >= 0.50:
            recommendation = "moderate_confidence"
        else:
            recommendation = "low_confidence_review_needed"

        return {
            "accept_probability": round(p_accept, 4),
            "predicted_rating":   round(pred_rating, 2),
            "recommendation":     recommendation,
        }

    def save(self, out_dir: str):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        with open(os.path.join(out_dir, "classifier.pkl"), "wb") as f:
            pickle.dump(self.classifier, f)
        with open(os.path.join(out_dir, "regressor.pkl"), "wb") as f:
            pickle.dump(self.regressor, f)
        with open(os.path.join(out_dir, "encoders.pkl"), "wb") as f:
            pickle.dump(self.encoders, f)
        print(f"\n[Save] Model artifacts written to {out_dir}/")

    @classmethod
    def load(cls, model_dir: str):
        with open(os.path.join(model_dir, "classifier.pkl"), "rb") as f:
            clf = pickle.load(f)
        with open(os.path.join(model_dir, "regressor.pkl"), "rb") as f:
            reg = pickle.load(f)
        with open(os.path.join(model_dir, "encoders.pkl"), "rb") as f:
            enc = pickle.load(f)
        return cls(clf, reg, enc)


# ─────────────────────────────────────────────────────────────────────────────
# Main Training Pipeline
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="DIWO Feedback Manager — ML Training")
    parser.add_argument("--db",  default=None, help="Path to diwo_audit.db")
    parser.add_argument("--out", default="feedback_model_output", help="Output directory")
    parser.add_argument("--synthetic-n", type=int, default=800, help="Synthetic record count")
    parser.add_argument("--min-real",    type=int, default=20,  help="Min real records before using synthetic")
    args = parser.parse_args()

    print("=" * 60)
    print("  DIWO Agent — Feedback Manager Model Training")
    print("  R26-SE-008 | IT22277886 | Bandara S M Y M")
    print("=" * 60)

    # ── Load data ──────────────────────────────────────────────────────────
    df = None
    if args.db and os.path.isfile(args.db):
        try:
            df = load_from_db(args.db)
        except Exception as e:
            print(f"[Warning] Could not load DB: {e}")

    if df is None or len(df) < args.min_real:
        print(f"[Data] Insufficient real data (< {args.min_real}). Using synthetic dataset.")
        df = generate_synthetic_data(args.synthetic_n)
    else:
        print(f"[Data] Using {len(df)} real feedback records.")

    # ── Preprocess ────────────────────────────────────────────────────────
    df = df.dropna(subset=["accepted"])
    df["accepted"] = df["accepted"].astype(int)
    df["rating"]   = pd.to_numeric(df["rating"], errors="coerce").fillna(3.0)

    print(f"\n[Data] Class distribution: accepted={df['accepted'].sum()} / rejected={len(df)-df['accepted'].sum()}")
    print(f"[Data] Rating distribution: {df['rating'].value_counts().sort_index().to_dict()}")

    X, encoders = encode_features(df, fit=True)
    y_accept = df["accepted"]
    y_rating = df["rating"].astype(float)

    feature_names = list(X.columns)

    X_tr, X_te, ya_tr, ya_te, yr_tr, yr_te = train_test_split(
        X, y_accept, y_rating, test_size=0.2, random_state=42, stratify=y_accept
    )

    print(f"\n[Split] Train={len(X_tr)}  Test={len(X_te)}")

    # ── Train models ──────────────────────────────────────────────────────
    clf, clf_results = train_acceptance_classifier(X_tr, ya_tr, X_te, ya_te)
    print_feature_importance(clf, feature_names)

    reg, reg_results = train_rating_regressor(X_tr, yr_tr, X_te, yr_te)
    print_feature_importance(reg, feature_names)

    # ── Save ──────────────────────────────────────────────────────────────
    predictor = FeedbackPredictor(clf, reg, encoders)
    predictor.save(args.out)

    # Save training report
    report = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "records_used": len(df),
        "data_source": "real_db" if (args.db and os.path.isfile(args.db or "")) else "synthetic",
        "features": feature_names,
        "acceptance_classifier": {
            "best_model": type(clf).__name__,
            "results": clf_results,
        },
        "rating_regressor": {
            "best_model": type(reg).__name__,
            "results": reg_results,
        },
    }
    report_path = os.path.join(args.out, "training_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"[Report] Saved to {report_path}")

    # ── Quick inference demo ───────────────────────────────────────────────
    print("\n[Demo] Running inference on sample inputs:")
    samples = [
        ("plan_approval", "plan_approved", "Long Method",    "Extract Method",  "high",     None),
        ("plan_approval", "plan_rejected", "God Class",      "Extract Class",   "critical", 2),
        ("transformation","transformation_accepted","Duplicate Code","Extract Method","medium", 4),
        ("smell_selection","smell_excluded","Lazy Class","Inline Class","low",  None),
    ]
    for s in samples:
        result = predictor.predict(*s)
        print(f"  [{s[2]:20s} / {s[3]:35s}] → p(accept)={result['accept_probability']:.3f}  rating={result['predicted_rating']:.1f}  [{result['recommendation']}]")

    print("\n✓ Training complete.")


if __name__ == "__main__":
    main()
