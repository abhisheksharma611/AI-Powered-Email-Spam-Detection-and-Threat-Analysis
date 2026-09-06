import pandas as pd
import numpy as np
import joblib
import os
import time
import argparse
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from transformers import RobertaTokenizer, RobertaForSequenceClassification
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score,
    f1_score, confusion_matrix, classification_report
)
from scipy.sparse import hstack

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.utils.preprocessing import preprocess_text, extract_engineered_features
from models.predictor import Predictor

import warnings
warnings.filterwarnings('ignore')

# ─── Config ──────────────────────────────────────────────────────
TEST_SET_PATH = 'models/test_set.csv'
ROBERTA_MODEL_PATH = 'models/best_roberta_model.pth'
ENSEMBLE_MODEL_PATH = 'models/ensemble_model.joblib'
VECTORIZER_PATH = 'models/vectorizer.joblib'
ENCODER_PATH = 'models/encoder.joblib'

MAX_LEN = 256
BATCH_SIZE = 16

CATEGORY_NAMES = {0: 'Spam', 1: 'Not Spam', 2: 'Promotion', 3: 'Malware', 4: 'Newsletter', 5: 'Phishing'}
CATEGORY_IDS = {'Spam': 0, 'Not Spam': 1, 'Promotion': 2, 'Malware': 3, 'Newsletter': 4, 'Phishing': 5}
CATEGORY_ORDER = ['Spam', 'Not Spam', 'Promotion', 'Malware', 'Newsletter', 'Phishing']

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


class EmailDataset(Dataset):
    def __init__(self, texts, labels, tokenizer, max_len):
        self.texts = texts
        self.labels = labels
        self.tokenizer = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx):
        text = str(self.texts[idx])
        label = self.labels[idx]
        encoding = self.tokenizer(
            text, add_special_tokens=True, max_length=self.max_len,
            return_token_type_ids=False, padding='max_length',
            truncation=True, return_attention_mask=True, return_tensors='pt',
        )
        return {
            'input_ids': encoding['input_ids'].flatten(),
            'attention_mask': encoding['attention_mask'].flatten(),
            'labels': torch.tensor(label, dtype=torch.long)
        }


def print_header(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def print_metrics(y_true, y_pred, model_name):
    acc = accuracy_score(y_true, y_pred)
    prec_macro = precision_score(y_true, y_pred, average='macro')
    prec_weighted = precision_score(y_true, y_pred, average='weighted')
    rec_macro = recall_score(y_true, y_pred, average='macro')
    rec_weighted = recall_score(y_true, y_pred, average='weighted')
    f1_macro = f1_score(y_true, y_pred, average='macro')
    f1_weighted = f1_score(y_true, y_pred, average='weighted')
    conf = confusion_matrix(y_true, y_pred)

    print(f"\n  {'=' * 50}")
    print(f"  {model_name}")
    print(f"  {'=' * 50}")
    print(f"  Accuracy:           {acc:.4f} ({acc*100:.2f}%)")
    print(f"  Precision (macro):  {prec_macro:.4f}")
    print(f"  Precision (wtd):    {prec_weighted:.4f}")
    print(f"  Recall (macro):     {rec_macro:.4f}")
    print(f"  Recall (wtd):       {rec_weighted:.4f}")
    print(f"  F1 Score (macro):   {f1_macro:.4f}")
    print(f"  F1 Score (wtd):     {f1_weighted:.4f}")

    print(f"\n  Confusion Matrix:")
    print("  " + "-" * 55)
    header = "          " + " ".join([f"{CATEGORY_NAMES[i][:5]:>5}" for i in range(6)])
    print(header)
    for i, row in enumerate(conf):
        print(f"  {CATEGORY_NAMES[i][:10]:>10} " + " ".join([f"{val:>5}" for val in row]))

    print(f"\n  Per-Class Report:")
    print(classification_report(y_true, y_pred, target_names=CATEGORY_ORDER))

    return {
        'accuracy': acc, 'precision_macro': prec_macro, 'precision_weighted': prec_weighted,
        'recall_macro': rec_macro, 'recall_weighted': rec_weighted,
        'f1_macro': f1_macro, 'f1_weighted': f1_weighted
    }


def evaluate_roberta(df):
    print_header("EVALUATING ROBERTA MODEL")

    # Load tokenizer
    print("\nLoading tokenizer...")
    tokenizer = RobertaTokenizer.from_pretrained('roberta-base')

    # Load model
    print(f"Loading model from {ROBERTA_MODEL_PATH}...")
    model = RobertaForSequenceClassification.from_pretrained('roberta-base', num_labels=6)
    model.load_state_dict(torch.load(ROBERTA_MODEL_PATH, map_location=device))
    model = model.to(device)
    model.eval()
    print(f"  Model loaded on {device}")

    # Convert category strings to labels
    y_true = df['label'].values

    # Create DataLoader
    dataset = EmailDataset(
        texts=df['text'].values, labels=y_true,
        tokenizer=tokenizer, max_len=MAX_LEN
    )
    loader = DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=False)

    # Evaluate
    print("\nRunning evaluation...")
    all_preds = []
    eval_start = time.time()

    with torch.no_grad():
        for batch in loader:
            input_ids = batch['input_ids'].to(device)
            attention_mask = batch['attention_mask'].to(device)
            labels = batch['labels'].to(device)

            if torch.cuda.is_available():
                with torch.cuda.amp.autocast():
                    outputs = model(input_ids=input_ids, attention_mask=attention_mask)
            else:
                outputs = model(input_ids=input_ids, attention_mask=attention_mask)

            preds = torch.argmax(outputs.logits, dim=1)
            all_preds.extend(preds.cpu().numpy())

    eval_time = time.time() - eval_start

    metrics = print_metrics(y_true, all_preds, "ROBERTA-BASE (6-Class)")

    # Inference benchmark
    print_header("ROBERTA INFERENCE BENCHMARK")

    print(f"\n  Samples evaluated: {len(df):,}")
    print(f"  Total time:        {eval_time:.4f}s")
    print(f"  Avg per sample:    {eval_time/len(df)*1000:.2f} ms")
    print(f"  Throughput:        {len(df)/eval_time:.1f} emails/sec")

    return metrics


def evaluate_ensemble(df):
    print_header("EVALUATING ENSEMBLE MODEL")

    # Load model files
    print("\nLoading ensemble model...")
    model = joblib.load(ENSEMBLE_MODEL_PATH)
    vectorizer = joblib.load(VECTORIZER_PATH)
    label_encoder = joblib.load(ENCODER_PATH)
    print(f"  Model: {model.__class__.__name__}")
    print(f"  TF-IDF features: {len(vectorizer.vocabulary_):,}")

    # Process text
    print("\nPreprocessing text...")
    df['text_processed'] = df['text'].apply(preprocess_text)
    df = df[df['text_processed'].str.len() > 0]

    y_true = label_encoder.transform(df['label'].values)

    # Vectorize
    print("Vectorizing...")
    X_tfidf = vectorizer.transform(df['text_processed'])

    # Engineered features
    print("Extracting engineered features...")
    X_eng = extract_engineered_features(df['text'])

    X_combined = hstack([X_tfidf, X_eng])
    print(f"  Total features: {X_combined.shape[1]:,}")

    # Evaluate
    print("\nRunning evaluation...")
    eval_start = time.time()
    y_pred = model.predict(X_combined)
    eval_time = time.time() - eval_start

    metrics = print_metrics(y_true, y_pred, "ENSEMBLE (5-Classifiers Voting)")

    # Inference benchmark
    print_header("ENSEMBLE INFERENCE BENCHMARK")

    # Benchmark predict_proba as well (used by predictor.py)
    print("Running predict_proba benchmark...")
    proba_start = time.time()
    _ = model.predict_proba(X_combined)
    proba_time = time.time() - proba_start

    print(f"\n  Samples evaluated:     {len(df):,}")
    print(f"  Predict time:          {eval_time:.4f}s")
    print(f"  Predict+Proba time:    {proba_time:.4f}s")
    print(f"  Avg per sample:        {eval_time/len(df)*1000:.2f} ms")
    print(f"  Avg per sample (proba): {proba_time/len(df)*1000:.2f} ms")
    print(f"  Throughput:            {len(df)/eval_time:.1f} emails/sec")

    return metrics


def evaluate_orchestrator(df):
    print_header("EVALUATING ORCHESTRATOR (90% RoBERTa + 10% Ensemble)")

    print("\nLoading Predictor (both models)...")
    predictor = Predictor()
    if not predictor.is_trained:
        print("  ERROR: Predictor could not load models.")
        return None

    # Map Predictor's lowercase category names to label indices
    cat_to_label = {
        'spam': 0, 'legitimate': 1, 'promotion': 2,
        'malware': 3, 'newsletter': 4, 'phishing': 5
    }

    y_true = df['label'].values
    texts = df['text'].values
    y_pred = []

    print(f"Running orchestrator on {len(df):,} samples...")
    eval_start = time.time()

    for i, text in enumerate(texts):
        result = predictor.predict_single(text)
        pred_cat = result['category']
        y_pred.append(cat_to_label.get(pred_cat, -1))

    eval_time = time.time() - eval_start

    metrics = print_metrics(y_true, y_pred, "ORCHESTRATOR (90% RoBERTa + 10% Ensemble)")

    print_header("ORCHESTRATOR INFERENCE BENCHMARK")
    print(f"\n  Samples evaluated:  {len(df):,}")
    print(f"  Total time:         {eval_time:.4f}s")
    print(f"  Avg per sample:     {eval_time/len(df)*1000:.2f} ms")
    print(f"  Throughput:         {len(df)/eval_time:.1f} emails/sec")

    return metrics


def main():
    parser = argparse.ArgumentParser(description='Evaluate trained models on blind test set')
    parser.add_argument('--model', choices=['roberta', 'ensemble', 'both'], default='both',
                        help='Which model to evaluate (default: both)')
    parser.add_argument('--orchestrator', action='store_true',
                        help='Evaluate the 65/35 weighted voting orchestrator')
    args = parser.parse_args()

    print("=" * 70)
    print("MODEL EVALUATION ON BLIND TEST SET")
    print("=" * 70)

    # Load test set
    print("\nLoading test set...")
    if not os.path.exists(TEST_SET_PATH):
        print(f"  ERROR: Test set not found at {TEST_SET_PATH}")
        print(f"  Run 'python models/roberta_train.py' first to create the test split.")
        return

    df = pd.read_csv(TEST_SET_PATH)
    df['label'] = df['label'].astype(int)
    print(f"  Test set: {len(df):,} samples")

    print("\n  Class distribution:")
    for label, name in CATEGORY_NAMES.items():
        count = len(df[df['label'] == label])
        print(f"    {name}: {count}")

    # Check if the ensemble model files exist
    ensemble_available = (
        os.path.exists(ENSEMBLE_MODEL_PATH) and
        os.path.exists(VECTORIZER_PATH) and
        os.path.exists(ENCODER_PATH)
    )

    roberta_available = os.path.exists(ROBERTA_MODEL_PATH)

    if not roberta_available and not ensemble_available:
        print(f"\n  ERROR: No trained models found!")
        print(f"  Run 'python models/roberta_train.py' and/or 'python models/ensemble_train.py' first.")
        return

    # Orchestrator mode
    if args.orchestrator:
        if not roberta_available or not ensemble_available:
            print(f"\n  ERROR: Orchestrator requires both models to be trained.")
            print(f"  Run 'python models/roberta_train.py' and 'python models/ensemble_train.py' first.")
            return
        evaluate_orchestrator(df)
        print("\n" + "=" * 70)
        print("EVALUATION COMPLETE")
        print("=" * 70)
        return

    results = {}
    if args.model in ('roberta', 'both') and roberta_available:
        results['roberta'] = evaluate_roberta(df)
    elif args.model in ('roberta', 'both') and not roberta_available:
        print(f"\n  [SKIP] RoBERTa model not found at {ROBERTA_MODEL_PATH}")

    if args.model in ('ensemble', 'both') and ensemble_available:
        results['ensemble'] = evaluate_ensemble(df)
    elif args.model in ('ensemble', 'both') and not ensemble_available:
        print(f"\n  [SKIP] Ensemble model files not found")

    # Summary comparison
    if 'roberta' in results and 'ensemble' in results:
        print_header("MODEL COMPARISON")
        print(f"\n  {'Metric':<20} {'RoBERTa':<12} {'Ensemble':<12}")
        print(f"  {'-'*44}")
        print(f"  {'Accuracy':<20} {results['roberta']['accuracy']:<12.4f} {results['ensemble']['accuracy']:<12.4f}")
        print(f"  {'F1 (macro)':<20} {results['roberta']['f1_macro']:<12.4f} {results['ensemble']['f1_macro']:<12.4f}")
        print(f"  {'F1 (weighted)':<20} {results['roberta']['f1_weighted']:<12.4f} {results['ensemble']['f1_weighted']:<12.4f}")

    print("\n" + "=" * 70)
    print("EVALUATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
