"""
Standalone ensemble evaluation on the held-out test set.

models/evaluate.py imports torch at module level, and Windows Application
Control blocks a torch DLL on this machine, so that script cannot start. This
reproduces only the ensemble path, with no torch: the same preprocessing the
training script used, the same vectoriser, and the same saved artefacts.

Usage (from anywhere):
    set P1_ROOT=<path to the project root>
    python eval_ensemble_only.py

P1_ROOT must be the folder that contains models/ and utils/.
"""
import os
import sys

import joblib
import numpy as np
import pandas as pd
from scipy.sparse import hstack
from sklearn.metrics import accuracy_score, f1_score, classification_report, confusion_matrix

ROOT = os.environ.get("P1_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from models.utils.preprocessing import preprocess_text, extract_engineered_features
from utils.gmail_client import prepare_for_model

TEST_SET = os.path.join(ROOT, "models", "test_set.csv")
VECTORIZER = os.path.join(ROOT, "models", "vectorizer.joblib")
ENSEMBLE = os.path.join(ROOT, "models", "ensemble_model.joblib")
ENCODER = os.path.join(ROOT, "models", "encoder.joblib")

df = pd.read_csv(TEST_SET)
print(f"test rows: {len(df):,}")

df["text_for_tfidf"] = df.apply(
    lambda r: prepare_for_model(r["text"], sender=r.get("sender_email")), axis=1
)
df["text_processed"] = df["text_for_tfidf"].apply(preprocess_text)
before = len(df)
df = df[df["text_processed"].str.len() > 0]
print(f"after cleaning: {len(df):,} (dropped {before - len(df):,})")

vectorizer = joblib.load(VECTORIZER)
ensemble = joblib.load(ENSEMBLE)
label_encoder = joblib.load(ENCODER)

X_tfidf = vectorizer.transform(df["text_processed"])
X_eng = extract_engineered_features(df["text"])
X = hstack([X_tfidf, X_eng])

y_true = label_encoder.transform(df["label"])
y_pred = ensemble.predict(X)
classes = [str(c) for c in label_encoder.classes_]

acc = accuracy_score(y_true, y_pred)
macro_f1 = f1_score(y_true, y_pred, average="macro")

print()
print("=" * 64)
print(f"ENSEMBLE, held-out test set:  accuracy {acc * 100:.2f}%   macro F1 {macro_f1 * 100:.2f}%   ({len(y_true):,} rows)")
print("=" * 64)
print()
print(classification_report(y_true, y_pred, target_names=classes, zero_division=0, digits=4))
print("Confusion matrix (rows = actual, cols = predicted):")
cm = confusion_matrix(y_true, y_pred)
print(" " * 13 + "  ".join(f"{c[:6]:>6}" for c in classes))
for name, row in zip(classes, cm):
    print(f"{name[:12]:<13}" + "  ".join(f"{v:>6}" for v in row))
