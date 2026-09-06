import pandas as pd
import joblib
import os
import time
import warnings
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

warnings.filterwarnings('ignore')

# ─── Configuration ───────────────────────────────────────────────
DATASET_PATH = 'models/final_training_dataset.csv'
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

    # Replicate roberta_train.py's exact 80/10/10 split (seed 42) so BOTH models train
    # on the identical 80% and hold the same 10% val out of training. Fixes the prior
    # bug where the train mask used test_set.csv's positional index (lost because the
    # file is saved without an index), which made the ensemble train on an arbitrary
    # positional 90% slice instead of RoBERTa's stratified split.
    _sp_train, _sp_temp = train_test_split(
        df, test_size=0.2, random_state=RANDOM_STATE, stratify=df['label']
    )
    _sp_val, _sp_test = train_test_split(
        _sp_temp, test_size=0.5, random_state=RANDOM_STATE, stratify=_sp_temp['label']
    )
    # Train only on the 80% RoBERTa trained on; val is held out from both models.
    train_df = _sp_train

    print(f"  Train: {len(train_df):,} (80%, identical to RoBERTa)")
    print(f"  Test:  {len(test_df):,} (loaded from shared test_set.csv)")

    # Preprocess text
    print("\n[2/8] Preprocessing text...")
    train_df['text_processed'] = train_df['text'].apply(preprocess_text)
    test_df['text_processed'] = test_df['text'].apply(preprocess_text)
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
            random_state=RANDOM_STATE, max_iter=1000, multi_class='multinomial',
            solver='lbfgs', class_weight='balanced'
        ),
        'RandomForest': RandomForestClassifier(
            n_estimators=100, random_state=RANDOM_STATE, n_jobs=1, class_weight='balanced'
        ),
        'GradientBoosting': GradientBoostingClassifier(
            n_estimators=100, random_state=RANDOM_STATE
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

    def _fit_one(item):
        name, clf = item
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
