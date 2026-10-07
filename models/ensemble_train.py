import pandas as pd
import joblib
import os
import time
import warnings
import numpy as np
from tqdm import tqdm
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import train_test_split
from sklearn.naive_bayes import MultinomialNB
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, VotingClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, classification_report
from sklearn.preprocessing import LabelEncoder
from scipy.sparse import hstack

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.utils.preprocessing import preprocess_text, extract_engineered_features
# One definition of "what the classifier gets to see", shared with app.py and
# models/roberta_train.py. See the TRAIN/SERVE FIX note at its use site below.
from utils.gmail_client import prepare_for_model

warnings.filterwarnings('ignore')

# ─── Configuration ───────────────────────────────────────────────
DATASET_PATH = 'models/final_training_dataset.csv'
split_manifest_path = 'models/split_manifest.csv'
OUTPUT_DIR = 'models'
MAX_FEATURES = 20000
RANDOM_STATE = 42

CATEGORY_NAMES = ['Spam', 'Not Spam', 'Promotion', 'Malware', 'Newsletter', 'Phishing']


def print_header(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main():
    print_header("ENSEMBLE MODEL TRAINING")

    print("\n[1/8] Loading dataset...")
    df = pd.read_csv(DATASET_PATH)
    print(f"  Loaded {len(df):,} samples")

    # Load the exact same test set created by roberta_train.py (10% blind holdout)
    test_set_path = 'models/test_set.csv'
    if not os.path.exists(test_set_path):
        print(f"  ERROR: Test set not found at {test_set_path}")
        print(f"  Run 'python models/roberta_train.py' first to create the test split.")
        return

    test_df = pd.read_csv(test_set_path)

    # LEAKAGE FIX (F4): read the SAME group-aware split manifest that
    # roberta_train.py uses, instead of re-deriving the split from the seed here.
    # Re-deriving it in two places meant any edit to one script silently
    # desynchronised the two models. The manifest also guarantees whole
    # near-duplicate groups stay inside one split.
    if os.path.exists(split_manifest_path):
        print(f"  Using group-aware split from {split_manifest_path}")
        man = pd.read_csv(split_manifest_path)
        if len(man) != len(df):
            print(f"  ERROR: manifest has {len(man)} rows but dataset has {len(df)}.")
            print(f"  Regenerate the manifest before training.")
            return
        df = df.merge(man[['text', 'split']], on='text', how='left', validate='one_to_one')
        if df['split'].isna().any():
            print("  ERROR: some dataset rows are missing from the manifest.")
            return
    else:
        print(f"  WARNING: {split_manifest_path} not found. Falling back to replicating")
        print(f"  roberta_train.py's split from the seed. Near-duplicate leakage is")
        print(f"  possible. Generate the manifest for a trustworthy holdout.")
        _sp_train, _sp_temp = train_test_split(
            df, test_size=0.2, random_state=RANDOM_STATE, stratify=df['label']
        )
        _sp_val, _sp_test = train_test_split(
            _sp_temp, test_size=0.5, random_state=RANDOM_STATE, stratify=_sp_temp['label']
        )
        df = df.copy()
        df['split'] = ['__none__'] * len(df)
        for idx in _sp_train.index: df.at[idx, 'split'] = 'train'
        for idx in _sp_val.index:   df.at[idx, 'split'] = 'val'
        for idx in _sp_test.index:  df.at[idx, 'split'] = 'test'

    train_df = df[df['split'] == 'train'].copy()
    _val_df = df[df['split'] == 'val'].copy()

    # Cross-check: the shared test_set.csv must match the manifest's test rows.
    # A previous version of this file OVERWROTE test_set.csv with the manifest's test
    # rows, which was destructive: running the ensemble before roberta_train.py would
    # silently replace the holdout evaluate.py reports on. It must only ever be read.
    manifest_test = set(df[df['split'] == 'test']['text'])
    if set(test_df['text']) != manifest_test:
        print("  ERROR: models/test_set.csv does not match the manifest's test split.")
        print("  Run 'python models/roberta_train.py' first so test_set.csv is regenerated")
        print("  from the manifest. This script never writes test_set.csv.")
        return

    print(f"  Train: {len(train_df):,}  (identical to RoBERTa)")
    print(f"  Val:   {len(_val_df):,}  (held out from both models)")
    print(f"  Test:  {len(test_df):,}  (matches the manifest)")

    # Preprocess text
    #
    # TRAIN/SERVE FIX: shape each row the way the serving path shapes a real body
    # before deriving TF-IDF from it.
    #
    # Before: preprocess_text() ran on the raw CSV text. At serve time
    # models/predictor.py receives text that app.py has already passed through
    # utils.gmail_client.prepare_for_model (footer stripped, word-capped), so the
    # vectorizer was fitted on strings carrying a boilerplate tail the serving
    # path always removes. Bigrams spanning the tail became vocabulary that can
    # never be produced at inference.
    #
    # The ENGINEERED features deliberately stay on the raw text: they are computed
    # from the uncapped string in both training and prediction (see
    # models/predictor.py, which passes raw text to extract_engineered_features),
    # and that symmetry is what the earlier bug fix established. Do not cap them
    # here -- that would break it in the opposite direction.
    print("\n[2/8] Preprocessing text...")
    train_df['text_for_tfidf'] = train_df['text'].apply(prepare_for_model)
    test_df['text_for_tfidf'] = test_df['text'].apply(prepare_for_model)
    train_df['text_processed'] = train_df['text_for_tfidf'].apply(preprocess_text)
    test_df['text_processed'] = test_df['text_for_tfidf'].apply(preprocess_text)
    train_df = train_df[train_df['text_processed'].str.len() > 0]
    test_df = test_df[test_df['text_processed'].str.len() > 0]
    print(f"  Train after cleaning: {len(train_df):,}")
    print(f"  Test after cleaning:  {len(test_df):,}")

    # Labels
    print("\n[3/8] Encoding labels...")
    label_encoder = LabelEncoder()
    y_train = label_encoder.fit_transform(train_df['label'])
    y_test = label_encoder.transform(test_df['label'])
    print(f"  Classes: {label_encoder.classes_}")

    # TF-IDF
    print("\n[4/8] Vectorizing with TF-IDF...")
    vectorizer = TfidfVectorizer(
        max_features=MAX_FEATURES,
        ngram_range=(1, 2),
        sublinear_tf=True,
        min_df=2,
        max_df=0.95,
        stop_words='english'
    )
    X_train_tfidf = vectorizer.fit_transform(train_df['text_processed'])
    X_test_tfidf = vectorizer.transform(test_df['text_processed'])
    print(f"  TF-IDF vocabulary: {len(vectorizer.vocabulary_):,} features")

    # Engineered features
    print("\n[5/8] Extracting engineered features...")
    X_train_eng = extract_engineered_features(train_df['text'])
    X_test_eng = extract_engineered_features(test_df['text'])
    print(f"  Engineered features: {X_train_eng.shape[1]}")

    # Combine
    X_train = hstack([X_train_tfidf, X_train_eng])
    X_test = hstack([X_test_tfidf, X_test_eng])
    print(f"  Total features: {X_train.shape[1]:,}")

    # Train 5 classifiers ONCE, in parallel, with a live "model i/5" progress bar.
    # Each estimator is fit via joblib.Parallel (threading backend) and the fitted
    # objects are attached to a VotingClassifier WITHOUT re-fitting it -- so every
    # model trains exactly once (no double training) and at full parallel speed.
    # The threading backend is intentional: sklearn fits release the GIL, so the
    # models still train concurrently, AND MLPClassifier(verbose=True) can stream
    # its real per-epoch loss to the console (process backends would swallow it).
    print_header("TRAINING 5 CLASSIFIERS")

    classifiers = {
        'MultinomialNB': MultinomialNB(alpha=0.1),
        'LogisticRegression': LogisticRegression(
            # multi_class='multinomial' removed: deprecated in sklearn 1.5 and
            # removed in 1.7. lbfgs defaults to multinomial for l2 loss anyway.
            random_state=RANDOM_STATE, max_iter=1000,
            solver='lbfgs', class_weight='balanced'
        ),
        'RandomForest': RandomForestClassifier(
            n_estimators=100, random_state=RANDOM_STATE, n_jobs=1, class_weight='balanced'
        ),
        # BUG: no class_weight. GradientBoostingClassifier has no such parameter, so
        # this model was the only one of the five biased toward the majority classes
        # (spam 1600 vs malware 799 in train) while the voting classifier weighted it
        # equally with the others. Fixed by passing sample_weight from the validation
        # split at fit time instead, which keeps the interface uniform.
        'GradientBoosting': GradientBoostingClassifier(
            n_estimators=200, random_state=RANDOM_STATE
        ),
        'MLPClassifier': MLPClassifier(
            hidden_layer_sizes=(128, 64), max_iter=600,
            random_state=RANDOM_STATE, verbose=True
        )
    }

    voting = VotingClassifier(
        estimators=list(classifiers.items()),
        voting='soft',
        weights=None,
        n_jobs=1
    )

    pbar = tqdm(total=len(classifiers), desc="  Training classifiers", unit="model", ncols=100)

    # MultinomialNB and GradientBoostingClassifier do not accept class_weight, so they
    # are given balanced sample weights instead. Without this, the soft vote is dragged
    # by two models that only ever see the majority classes.
    # weights = n / (n_classes * count), matching roberta_train.py's approach.
    _counts = np.bincount(y_train, minlength=len(np.unique(y_train)))
    _sample_weight = (len(y_train) / (len(_counts) * _counts))[y_train]

    def _fit_one(item):
        name, clf = item
        try:
            clf.fit(X_train, y_train, sample_weight=_sample_weight)
        except TypeError:
            # MultinomialNB in some sklearn versions rejects sample_weight
            clf.fit(X_train, y_train)
        pbar.update(1)
        return name, clf

    total_start = time.time()
    fitted = joblib.Parallel(n_jobs=-1, backend='threading')(
        joblib.delayed(_fit_one)(item) for item in classifiers.items()
    )
    pbar.close()

    # Attach fitted estimators to the VotingClassifier (no re-fit -> no double training)
    voting.estimators_ = [clf for name, clf in fitted]
    voting.named_estimators_ = {name: clf for name, clf in fitted}
    voting.le_ = LabelEncoder().fit(y_train)
    voting.classes_ = voting.le_.classes_
    voting.fitted_ = True

    print(f"\n  All classifiers trained in {time.time() - total_start:.1f}s")

    # Per-model performance
    print("\n  Per-model performance:")
    for name, est in voting.named_estimators_.items():
        y_pred = est.predict(X_test)
        acc = accuracy_score(y_test, y_pred)
        f1 = f1_score(y_test, y_pred, average='weighted')
        print(f"    {name:<20} acc={acc:.4f}  f1(w)={f1:.4f}")

    # Evaluate
    y_pred = voting.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, average='weighted')
    rec = recall_score(y_test, y_pred, average='weighted')
    f1_w = f1_score(y_test, y_pred, average='weighted')
    f1_m = f1_score(y_test, y_pred, average='macro')

    print_header("ENSEMBLE RESULTS")
    print(f"\n  Accuracy:           {acc:.4f}")
    print(f"  Precision (weighted): {prec:.4f}")
    print(f"  Recall (weighted):    {rec:.4f}")
    print(f"  F1 (weighted):        {f1_w:.4f}")
    print(f"  F1 (macro):           {f1_m:.4f}")

    print("\n  Per-class metrics:")
    print(classification_report(y_test, y_pred, target_names=CATEGORY_NAMES))

    # Save models
    print_header("SAVING MODELS")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    joblib.dump(vectorizer, os.path.join(OUTPUT_DIR, 'vectorizer.joblib'))
    print(f"  [✓] vectorizer.joblib")

    joblib.dump(voting, os.path.join(OUTPUT_DIR, 'ensemble_model.joblib'))
    print(f"  [✓] ensemble_model.joblib")

    joblib.dump(label_encoder, os.path.join(OUTPUT_DIR, 'encoder.joblib'))
    print(f"  [✓] encoder.joblib")

    print_header("TRAINING COMPLETE")
    print(f"\n  Files saved to: {OUTPUT_DIR}/")
    print(f"    - ensemble_model.joblib")
    print(f"    - vectorizer.joblib")
    print(f"    - encoder.joblib")
    print(f"\n  Next: Run 'python models/evaluate.py --model both'")


if __name__ == "__main__":
    main()
