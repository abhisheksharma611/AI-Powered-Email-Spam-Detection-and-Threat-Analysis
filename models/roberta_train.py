import pandas as pd
import numpy as np
import torch
import torch.nn as nn
import os
import sys
import time
import warnings
from tqdm import tqdm
from torch.utils.data import Dataset, DataLoader
from transformers import RobertaTokenizer, RobertaForSequenceClassification, get_linear_schedule_with_warmup
from torch.optim import AdamW
from functools import partial
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, confusion_matrix, classification_report

# utils/ lives one level up, and this script is run as `python models/roberta_train.py`,
# which puts models/ on sys.path rather than the repo root. The other two scripts
# already do this insert; without it the import below raises ModuleNotFoundError.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.gmail_client import prepare_for_model

warnings.filterwarnings('ignore')

# Windows consoles default to a legacy code page (cp1252). Any non-ASCII character
# in a print() raises UnicodeEncodeError and kills the run mid-epoch. Force UTF-8
# with a replacement fallback so a stray character can never abort training.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# ─── Configuration ───────────────────────────────────────────────
DATASET_PATH = 'models/final_training_dataset.csv'
SPLIT_MANIFEST_PATH = 'models/split_manifest.csv'
MODEL_SAVE_PATH = 'models/best_roberta_model.pth'
MODEL_NAME = 'roberta-base'
# 320 tokens, not 256. The corpus maximum is 137 words = 160 tokens, so 256 was
# already far above what training needs -- but TRAIN_MAX_WORDS=150 admits rows up
# to ~211 tokens at p99 and 261 in the worst measured case (a row dense with
# proper nouns and an email address, 1.75 tokens/word). At 256 the tail of that
# distribution was being truncated at the tokeniser AFTER the word cap had passed
# it, which is the train/serve asymmetry again, one layer down. Dynamic padding
# means this ceiling costs nothing on the 99% of rows that are 72-84 tokens.
MAX_LEN = 320
# Word cap applied to every training row, mirroring what the serving path does.
#
# This is a guard, not a truncation: the corpus maximum is 137 words, so no row
# is currently cut. It exists so that a future row written at 600 words is
# shaped the same way at training time as a real body is at serve time, rather
# than teaching the model on a shape it will never receive. Serve-time cap is
# MODEL_INPUT_MAX_WORDS=200 in utils/gmail_client.py; the two differ on purpose
# because a real body is longer than any row we would write, and because the
# serving side additionally strips the footer before capping.
TRAIN_MAX_WORDS = 150
BATCH_SIZE = 8
GRADIENT_ACCUMULATION_STEPS = 2
EFFECTIVE_BATCH_SIZE = BATCH_SIZE * GRADIENT_ACCUMULATION_STEPS
LEARNING_RATE = 2e-5
# The classifier head is randomly initialised and must learn from scratch while the
# encoder only needs fine tuning, so the head gets a larger step. Without this the
# head is still underfitted when the encoder converges.
HEAD_LR_MULTIPLIER = 10.0
# 12 epochs at 410 optimizer steps/epoch = 4920 total steps. Early stopping runs on
# val macro-F1 at the end of each epoch with patience 3, so the best-scoring epoch
# is the one that gets saved and training halts once it stops improving.
#
# The model is not the weak link: the previous checkpoint scored 0.9932 macro-F1 on
# the manifest test split. It fits the synthetic distribution and generalises poorly
# to the real inbox, because 0 of the real subjects have a >=0.60 near-duplicate in
# the training rows. More epochs cannot fix that; the training data is what does.
EPOCHS = 12
WARMUP_RATIO = 0.1
WEIGHT_DECAY = 0.01
EARLY_STOPPING_PATIENCE = 3
# Small smoothing helps when hand-written labels contain genuine near-duplicates
# across two classes, which the dataset does contain.
LABEL_SMOOTHING = 0.05
RANDOM_STATE = 42

CATEGORY_NAMES = {0: 'Spam', 1: 'Not Spam', 2: 'Promotion', 3: 'Malware', 4: 'Newsletter', 5: 'Phishing'}

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Reproducible seeds (weight init + dataloader order)
import random
import transformers
SEED = RANDOM_STATE
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(SEED)
transformers.set_seed(SEED)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False


class EmailDataset(Dataset):
    """Tokenises lazily so a collate_fn can pad each batch to its own longest row.

    The previous version padded every row to MAX_LEN in __getitem__, which meant
    191 of 256 positions were attention-masked padding on a corpus whose mean
    row is only 65 tokens. Measured on the RTX 3050 that cost 4.12 steps/s versus
    7.67 steps/s with dynamic padding, and 3.11 GB of VRAM versus 2.53 GB.
    """
    def __init__(self, texts, labels, tokenizer, max_len):
        self.texts = texts
        self.labels = labels
        self.tokenizer = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx):
        # TRAIN/SERVE FIX: shape the row the way the serving path shapes a real
        # body, rather than reading the CSV verbatim.
        #
        # Before: the CSV text went to the tokeniser raw. The serving path
        # (utils/gmail_client.prepare_for_model) strips the unsubscribe/terms tail
        # and caps the word count, so the model was fitted on strings containing
        # boilerplate it would never be shown and without the length limit it is
        # always shown under. Nothing raised; it just quietly trained on a
        # different distribution than it was asked to classify.
        #
        # Measured effect of the strip on this corpus: 3 of 9280 rows change, so
        # this is a correctness fix rather than a scoring one. It matters for
        # every future row, and it matters for the words that follow a footer in
        # real mail.
        text = prepare_for_model(str(self.texts[idx]), TRAIN_MAX_WORDS)
        label = self.labels[idx]
        encoding = self.tokenizer(
            text, add_special_tokens=True, max_length=self.max_len,
            return_token_type_ids=False, padding=False,
            truncation=True, return_attention_mask=True, return_tensors='pt',
        )
        return {
            'input_ids': encoding['input_ids'].flatten(),
            'attention_mask': encoding['attention_mask'].flatten(),
            'labels': torch.tensor(label, dtype=torch.long)
        }


def pad_collate(batch, pad_id=1):
    """Pad a batch to its own longest row instead of the global MAX_LEN."""
    max_in_batch = max(item['input_ids'].size(0) for item in batch)
    input_ids, attention_mask, labels = [], [], []
    for item in batch:
        n = item['input_ids'].size(0)
        pad = max_in_batch - n
        input_ids.append(torch.cat([item['input_ids'],
                                    torch.full((pad,), pad_id, dtype=torch.long)]))
        attention_mask.append(torch.cat([item['attention_mask'],
                                         torch.zeros(pad, dtype=torch.long)]))
        labels.append(item['labels'])
    return {
        'input_ids': torch.stack(input_ids),
        'attention_mask': torch.stack(attention_mask),
        'labels': torch.stack(labels),
    }


def pad_collate_factory(pad_id):
    """Return a picklable collate_fn bound to pad_id.

    A lambda here crashes the DataLoader on Windows:
        AttributeError: Can't pickle local object
        '<locals>.<lambda>'
    DataLoader workers use the 'spawn' start method, which pickles the dataset
    and the collate_fn to send them to the child process. A closure is not
    picklable, so it must be replaced by a partial of a module-level function.
    """
    return partial(pad_collate, pad_id=pad_id)


def load_and_split():
    print("\n[1/7] Loading dataset...")
    df = pd.read_csv(DATASET_PATH)
    print(f"  Total samples: {len(df):,}")

    df = df.dropna(subset=['text', 'label'])
    df['label'] = df['label'].astype(int)

    print("\n  Class distribution:")
    for label, name in CATEGORY_NAMES.items():
        count = len(df[df['label'] == label])
        print(f"    {name}: {count:,}")

    # LEAKAGE FIX (F3): read the precomputed group-aware split instead of    # re-deriving it here. A plain stratified split can put two near-identical
    # emails on opposite sides of the boundary, which inflates validation and
    # test scores. models/split_manifest.csv assigns whole near-duplicate groups
    # to one split. ensemble_train.py reads the same file, so both models always
    # see identical data.
    if os.path.exists(SPLIT_MANIFEST_PATH):
        print(f"\n  Using group-aware split from {SPLIT_MANIFEST_PATH}")
        man = pd.read_csv(SPLIT_MANIFEST_PATH)
        if len(man) != len(df):
            raise SystemExit(
                f"  ERROR: manifest has {len(man)} rows but dataset has {len(df)}. "
                f"Regenerate the manifest before training."
            )
        df = df.merge(
            man[['text', 'split']], on='text', how='left', validate='one_to_one'
        )
        if df['split'].isna().any():
            raise SystemExit("  ERROR: some dataset rows are missing from the manifest.")
    else:
        print(f"\n  WARNING: {SPLIT_MANIFEST_PATH} not found, falling back to a plain")
        print(f"  stratified split. Near-duplicate leakage is possible. Generate the")
        print(f"  manifest to get a trustworthy holdout.")
        train_df, temp_df = train_test_split(
            df, test_size=0.2, random_state=RANDOM_STATE, stratify=df['label']
        )
        val_df, test_df = train_test_split(
            temp_df, test_size=0.5, random_state=RANDOM_STATE, stratify=temp_df['label']
        )
        return train_df, val_df, test_df

    train_df = df[df['split'] == 'train'].copy()
    val_df = df[df['split'] == 'val'].copy()
    test_df = df[df['split'] == 'test'].copy()

    straddling = man.groupby('group_id')['split'].nunique()
    bad_groups = int((straddling > 1).sum())
    if bad_groups:
        raise SystemExit(f"  ERROR: {bad_groups} near-duplicate groups span more than one split.")

    print(f"\n  Train: {len(train_df):,}")
    print(f"  Val:   {len(val_df):,}")
    print(f"  Test:  {len(test_df):,}")
    print(f"  Near-duplicate groups spanning >1 split: {bad_groups} (must be 0)")

    # Save test set for evaluate.py. This is now FIXED across retrains, which is the
    # point of the manifest: re-deriving the split on every run meant the blind
    # holdout moved each time and scores were not comparable between runs.
    test_df.drop(columns=['split']).to_csv('models/test_set.csv', index=False)
    print(f"  Test set saved to models/test_set.csv (stable across retrains)")

    return train_df, val_df, test_df


def create_dataloaders(train_df, val_df, tokenizer):
    print("\n[2/7] Creating dataloaders...")
    train_dataset = EmailDataset(
        texts=train_df['text'].values, labels=train_df['label'].values,
        tokenizer=tokenizer, max_len=MAX_LEN
    )
    val_dataset = EmailDataset(
        texts=val_df['text'].values, labels=val_df['label'].values,
        tokenizer=tokenizer, max_len=MAX_LEN
    )
    # drop_last=True: train_epoch() only steps the optimizer on
    # (batch_idx + 1) % GRADIENT_ACCUMULATION_STEPS == 0, so with 819 batches and
    # accumulation 2 the final batch would be computed and its gradients silently
    # discarded. drop_last drops it instead of wasting the forward pass.
    # Val keeps the remainder so every validation row is scored.
    # collate_fn pads each batch to its own longest row. Measured 1.86x faster and
    # 0.6 GB less VRAM on the RTX 3050 (2.53 GB vs 3.11 GB at batch 8).
    #
    # num_workers=0 on purpose. Three reasons:
    #   1. Windows DataLoader uses the 'spawn' start method, which re-imports this
    #      module in every child, so module-level code (the device banner) runs
    #      again per worker and floods the console mid-progress-bar.
    #   2. spawn has to pickle the dataset and the collate_fn. pad_collate_factory
    #      returns a functools.partial, which is picklable; a lambda is not, and
    #      raised "Can't pickle local object ... <lambda>" at epoch 0.
    #   3. Tokenising ~6.5k short strings is a few hundred ms of work in total.
    #      Worker startup costs more than it saves at this data size.
    # Set NUM_WORKERS > 0 only on Linux, where fork is the default.
    NUM_WORKERS = 0
    train_loader = DataLoader(
        train_dataset, batch_size=BATCH_SIZE, shuffle=True, drop_last=True,
        num_workers=NUM_WORKERS,
        collate_fn=pad_collate_factory(tokenizer.pad_token_id))
    val_loader = DataLoader(
        val_dataset, batch_size=BATCH_SIZE * 2, shuffle=False, drop_last=False,
        num_workers=NUM_WORKERS,
        collate_fn=pad_collate_factory(tokenizer.pad_token_id))
    print(f"  Train batches: {len(train_loader)}  (drop_last=True, dynamic padding)")
    print(f"  Val batches:   {len(val_loader)}  (drop_last=False, dynamic padding)")
    return train_loader, val_loader


def compute_class_weights(train_df):
    n_classes = len(CATEGORY_NAMES)
    class_counts = train_df['label'].value_counts().reindex(
        range(n_classes), fill_value=0
    )
    total = len(train_df)
    # reindex() is essential: value_counts() only returns classes that happen to be
    # present, so a missing class produced a shorter weight tensor and
    # CrossEntropyLoss raised "weight tensor should be defined either for all 6
    # classes or no classes". Indexing a missing label would divide by zero, so
    # fall back to 1.0 for a class with no examples in this split.
    weights = torch.tensor(
        [total / (n_classes * count) if count > 0 else 1.0
         for count in class_counts],
        dtype=torch.float
    ).to(device)
    print(f"\n  Class weights: {weights.cpu().numpy()}")
    return weights


def train_epoch(model, loader, optimizer, scheduler, scaler, criterion, epoch, total_epochs):
    model.train()
    total_loss = 0
    optimizer.zero_grad()
    total_batches = len(loader)

    pbar = tqdm(loader, desc=f"  Epoch {epoch+1}/{total_epochs} [Train]", unit="batch", leave=False)
    for batch_idx, batch in enumerate(pbar):
        input_ids = batch['input_ids'].to(device)
        attention_mask = batch['attention_mask'].to(device)
        labels = batch['labels'].to(device)

        if scaler:
            with torch.cuda.amp.autocast():
                outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
                loss = criterion(outputs.logits.float(), labels)
                loss = loss / GRADIENT_ACCUMULATION_STEPS
            scaler.scale(loss).backward()
        else:
            outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
            loss = criterion(outputs.logits, labels)
            loss = loss / GRADIENT_ACCUMULATION_STEPS
            loss.backward()

        if (batch_idx + 1) % GRADIENT_ACCUMULATION_STEPS == 0:
            if scaler:
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                scaler.step(optimizer)
                scaler.update()
            else:
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()
            scheduler.step()
            optimizer.zero_grad()

        total_loss += loss.item()
        pbar.set_postfix(loss=f"{loss.item() * GRADIENT_ACCUMULATION_STEPS:.4f}")

    return total_loss / len(loader)


def validate_with_pbar(model, pbar, criterion, scaler):
    model.eval()
    total_loss = 0
    all_preds = []
    all_labels = []

    with torch.no_grad():
        for batch in pbar:
            input_ids = batch['input_ids'].to(device)
            attention_mask = batch['attention_mask'].to(device)
            labels = batch['labels'].to(device)

            if scaler:
                with torch.cuda.amp.autocast():
                    outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
                    # Cast the fp16 logits back to fp32 before the weighted loss.
                    # autocast does not cover cross_entropy with a float32 weight
                    # tensor, and mixing the two dtypes raises
                    # "expected scalar type Half but found Float". This is also the
                    # numerically safer order: the loss is accumulated in fp32.
                    loss = criterion(outputs.logits.float(), labels)
            else:
                outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
                loss = criterion(outputs.logits, labels)
            total_loss += loss.item()

            preds = torch.argmax(outputs.logits, dim=1)
            all_preds.extend(preds.cpu().numpy())
            all_labels.extend(labels.cpu().numpy())

    avg_loss = total_loss / len(pbar)
    accuracy = accuracy_score(all_labels, all_preds)
    return avg_loss, accuracy, all_preds, all_labels


def validate(model, loader, criterion, scaler):
    model.eval()
    total_loss = 0
    all_preds = []
    all_labels = []

    with torch.no_grad():
        for batch in loader:
            input_ids = batch['input_ids'].to(device)
            attention_mask = batch['attention_mask'].to(device)
            labels = batch['labels'].to(device)

            if scaler:
                with torch.cuda.amp.autocast():
                    outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
                    loss = criterion(outputs.logits.float(), labels)
            else:
                outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
                loss = criterion(outputs.logits, labels)
            total_loss += loss.item()

            preds = torch.argmax(outputs.logits, dim=1)
            all_preds.extend(preds.cpu().numpy())
            all_labels.extend(labels.cpu().numpy())

    avg_loss = total_loss / len(loader)
    accuracy = accuracy_score(all_labels, all_preds)
    return avg_loss, accuracy, all_preds, all_labels


# ─── Main ─────────────────────────────────────────────────────────
# Load roberta-base from the local HuggingFace cache and never touch the network.
# Without this, from_pretrained() issues a HEAD request to huggingface.co on every
# run; when that times out (it did, on a flaky connection) the call blocks for
# 10s per file, then retries, and prints a ReadTimeoutError tuple before falling
# back to the cache. The weights are already cached from earlier runs, so the
# network call buys nothing. local_files_only=True makes it a pure disk read.
# If you ever need to re-download the model, delete this override temporarily or
# run:  huggingface-cli download roberta-base
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def main():
    print("=" * 60)
    print("ROBERTA MODEL TRAINING")
    print("=" * 60)
    print(f"Device: {device}")
    if torch.cuda.is_available():
        print(f"  GPU: {torch.cuda.get_device_name(0)}")
        print(f"  VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")

    print(f"\n  Model: {MODEL_NAME}")
    print(f"  Batch size: {BATCH_SIZE}")
    print(f"  Gradient accumulation: {GRADIENT_ACCUMULATION_STEPS}")
    print(f"  Effective batch size: {EFFECTIVE_BATCH_SIZE}")
    print(f"  Max epochs: {EPOCHS}")
    print(f"  Learning rate: {LEARNING_RATE} (head x{HEAD_LR_MULTIPLIER})")
    print(f"  Max length: {MAX_LEN} (dynamic padding to batch max)")
    print(f"  Early stopping patience: {EARLY_STOPPING_PATIENCE}")
    print(f"  Label smoothing: {LABEL_SMOOTHING}")
    print(f"  Mixed precision: {'FP16' if torch.cuda.is_available() else 'No'}")

    # Load and split data
    train_df, val_df, test_df = load_and_split()

    # Tokenizer
    print("\n[3/7] Loading tokenizer...")
    tokenizer = RobertaTokenizer.from_pretrained(MODEL_NAME, local_files_only=True)
    print("  Done")

    # Dataloaders
    train_loader, val_loader = create_dataloaders(train_df, val_df, tokenizer)

    # Model
    print("\n[4/7] Loading model...")
    model = RobertaForSequenceClassification.from_pretrained(
        MODEL_NAME, num_labels=6, local_files_only=True)
    model = model.to(device)
    print("  Done")

    # Class weights
    print("\n[5/7] Computing class weights...")
    class_weights = compute_class_weights(train_df)
    # NOTE: previously cast to .half() here. Kept in float32 deliberately: under
    # autocast the loss is computed in fp16 anyway, and pre-casting the weights to
    # fp16 can underflow for the small weight on the majority class.
    # Build the criterion once and reuse it, rather than constructing a new
    # nn.CrossEntropyLoss on every batch.
    criterion = nn.CrossEntropyLoss(weight=class_weights.to(device),
                                    label_smoothing=LABEL_SMOOTHING)

    # Optimizer: the randomly-initialised classifier head gets a larger LR than the
    # pretrained encoder so it is not left behind while the encoder converges.
    head_params, encoder_params = [], []
    for name, param in model.named_parameters():
        (head_params if name.startswith("classifier") else encoder_params).append(param)
    optimizer = AdamW(
        [
            {'params': encoder_params, 'lr': LEARNING_RATE},
            {'params': head_params, 'lr': LEARNING_RATE * HEAD_LR_MULTIPLIER},
        ],
        weight_decay=WEIGHT_DECAY
    )
    print(f"  Encoder LR: {LEARNING_RATE:.2e}  |  Head LR: {LEARNING_RATE*HEAD_LR_MULTIPLIER:.2e}")
    print(f"  Label smoothing: {LABEL_SMOOTHING}")

    total_steps = (len(train_loader) // GRADIENT_ACCUMULATION_STEPS) * EPOCHS
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(WARMUP_RATIO * total_steps),
        num_training_steps=total_steps
    )
    scaler = torch.cuda.amp.GradScaler() if torch.cuda.is_available() else None

    # Training
    print("\n[6/7] Training...")
    print("-" * 60)

    best_metric = -float('inf')  # early-stop on MACRO-F1, not val_loss (protects minority class)
    epochs_no_improve = 0
    total_start = time.time()

    print(f"  Overall Progress: 0/{EPOCHS} epochs (0%)")
    for epoch in range(EPOCHS):
        epoch_start = time.time()

        train_loss = train_epoch(model, train_loader, optimizer, scheduler, scaler, criterion, epoch, EPOCHS)

        # Validation progress
        val_pbar = tqdm(val_loader, desc=f"  Epoch {epoch+1}/{EPOCHS} [Val]", unit="batch", leave=False)
        val_loss, val_acc, all_preds, all_labels = validate_with_pbar(model, val_pbar, criterion, scaler)
        val_pbar.close()
        val_macro_f1 = f1_score(all_labels, all_preds, average='macro')

        epoch_time = time.time() - epoch_start
        elapsed = time.time() - total_start
        epochs_done = epoch + 1
        avg_epoch_time = elapsed / epochs_done
        remaining_epochs = EPOCHS - epochs_done
        eta = avg_epoch_time * remaining_epochs
        overall_pct = (epochs_done / EPOCHS) * 100

        lr_now = scheduler.get_last_lr()[0] if scheduler else LEARNING_RATE

        print(f"\n  [{overall_pct:.0f}%] Epoch {epoch+1}/{EPOCHS} | "
              f"Train Loss: {train_loss:.4f} | "
              f"Val Loss: {val_loss:.4f} | "
              f"Val Acc: {val_acc:.4f} | "
              f"Val Macro-F1: {val_macro_f1:.4f} | "
              f"LR: {lr_now:.2e} | "
              f"Time: {epoch_time:.1f}s | "
              f"ETA: {eta:.0f}s ({eta/60:.1f} min)")

        # Early stopping check (macro-F1: balances all 6 classes, including smallest = Malware)
        if val_macro_f1 > best_metric:
            best_metric = val_macro_f1
            torch.save(model.state_dict(), MODEL_SAVE_PATH)
            # ASCII only. A literal U+2713 tick crashed the run on a Windows console
            # whose code page is cp1252:
            #   UnicodeEncodeError: 'charmap' codec can't encode character '\u2713'
            # It failed at the first "best model saved" print, i.e. after epoch 1,
            # which is the worst possible place to lose a run.
            print(f"    [OK] Saved best model (macro_f1: {best_metric:.4f})")
            epochs_no_improve = 0
        else:
            epochs_no_improve += 1
            if epochs_no_improve >= EARLY_STOPPING_PATIENCE:
                print(f"    Early stopping triggered after {epoch+1}/{EPOCHS} epochs")
                break

    total_time = time.time() - total_start
    print(f"\n  Total training time: {total_time:.1f}s ({total_time/60:.1f} min)")

    # Load best model and final eval on validation set
    print("\n[7/7] Final validation evaluation...")
    model.load_state_dict(torch.load(MODEL_SAVE_PATH, map_location=device))
    _, val_acc, all_preds, all_labels = validate(model, val_loader, criterion, scaler)

    precision_w = precision_score(all_labels, all_preds, average='weighted')
    recall_w = recall_score(all_labels, all_preds, average='weighted')
    f1_w = f1_score(all_labels, all_preds, average='weighted')
    f1_m = f1_score(all_labels, all_preds, average='macro')

    print(f"\n  Validation Results:")
    print(f"    Accuracy:  {val_acc:.4f}")
    print(f"    Precision (weighted): {precision_w:.4f}")
    print(f"    Recall (weighted):    {recall_w:.4f}")
    print(f"    F1 (weighted):        {f1_w:.4f}")
    print(f"    F1 (macro):           {f1_m:.4f}")

    print("\n  Classification Report:")
    print(classification_report(all_labels, all_preds, target_names=list(CATEGORY_NAMES.values())))

    print("\n" + "=" * 60)
    print(f"Model saved to: {MODEL_SAVE_PATH}")
    print("Next: Run 'python models/ensemble_train.py'")
    print("=" * 60)


if __name__ == "__main__":
    main()
