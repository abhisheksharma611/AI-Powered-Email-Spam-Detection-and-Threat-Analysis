import pandas as pd
import numpy as np
import torch
import torch.nn as nn
import os
import time
import warnings
from tqdm import tqdm
from torch.utils.data import Dataset, DataLoader
from transformers import RobertaTokenizer, RobertaForSequenceClassification, get_linear_schedule_with_warmup
from torch.optim import AdamW
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, confusion_matrix, classification_report

warnings.filterwarnings('ignore')

# ─── Configuration ───────────────────────────────────────────────
DATASET_PATH = 'models/final_training_dataset.csv'
MODEL_SAVE_PATH = 'models/best_roberta_model.pth'
MODEL_NAME = 'roberta-base'
MAX_LEN = 256
BATCH_SIZE = 8
GRADIENT_ACCUMULATION_STEPS = 2
EFFECTIVE_BATCH_SIZE = BATCH_SIZE * GRADIENT_ACCUMULATION_STEPS
LEARNING_RATE = 2e-5
EPOCHS = 6
WARMUP_RATIO = 0.1
WEIGHT_DECAY = 0.01
EARLY_STOPPING_PATIENCE = 2
RANDOM_STATE = 42

CATEGORY_NAMES = {0: 'Spam', 1: 'Not Spam', 2: 'Promotion', 3: 'Malware', 4: 'Newsletter', 5: 'Phishing'}

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Device: {device}")
if torch.cuda.is_available():
    print(f"  GPU: {torch.cuda.get_device_name(0)}")
    print(f"  VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")

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

    # 80/10/10 stratified split
    train_df, temp_df = train_test_split(
        df, test_size=0.2, random_state=RANDOM_STATE, stratify=df['label']
    )
    val_df, test_df = train_test_split(
        temp_df, test_size=0.5, random_state=RANDOM_STATE, stratify=temp_df['label']
    )

    print(f"\n  Train: {len(train_df):,}")
    print(f"  Val:   {len(val_df):,}")
    print(f"  Test:  {len(test_df):,}")

    # Save test set for evaluate.py
    test_df.to_csv('models/test_set.csv', index=False)
    print(f"  Test set saved to models/test_set.csv")

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
    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=4, prefetch_factor=2)
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE * 2, shuffle=False, num_workers=4, prefetch_factor=2)
    print(f"  Train batches: {len(train_loader)}")
    print(f"  Val batches:   {len(val_loader)}")
    return train_loader, val_loader


def compute_class_weights(train_df):
    class_counts = train_df['label'].value_counts().sort_index()
    total = len(train_df)
    n_classes = len(class_counts)
    weights = torch.tensor(
        [total / (n_classes * count) for count in class_counts],
        dtype=torch.float
    ).to(device)
    print(f"\n  Class weights: {weights.cpu().numpy()}")
    return weights


def train_epoch(model, loader, optimizer, scheduler, scaler, class_weights, epoch, total_epochs):
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
                loss = nn.CrossEntropyLoss(weight=class_weights)(outputs.logits, labels)
                loss = loss / GRADIENT_ACCUMULATION_STEPS
            scaler.scale(loss).backward()
        else:
            outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
            loss = nn.CrossEntropyLoss(weight=class_weights)(outputs.logits, labels)
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


def validate_with_pbar(model, pbar, class_weights, scaler):
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
            else:
                outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)

            loss = nn.CrossEntropyLoss(weight=class_weights)(outputs.logits, labels)
            total_loss += loss.item()

            preds = torch.argmax(outputs.logits, dim=1)
            all_preds.extend(preds.cpu().numpy())
            all_labels.extend(labels.cpu().numpy())

    avg_loss = total_loss / len(pbar)
    accuracy = accuracy_score(all_labels, all_preds)
    return avg_loss, accuracy, all_preds, all_labels


def validate(model, loader, class_weights, scaler):
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
            else:
                outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)

            loss = nn.CrossEntropyLoss(weight=class_weights)(outputs.logits, labels)
            total_loss += loss.item()

            preds = torch.argmax(outputs.logits, dim=1)
            all_preds.extend(preds.cpu().numpy())
            all_labels.extend(labels.cpu().numpy())

    avg_loss = total_loss / len(loader)
    accuracy = accuracy_score(all_labels, all_preds)
    return avg_loss, accuracy, all_preds, all_labels


# ─── Main ─────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("ROBERTA MODEL TRAINING")
    print("=" * 60)

    print(f"\n  Model: {MODEL_NAME}")
    print(f"  Batch size: {BATCH_SIZE}")
    print(f"  Gradient accumulation: {GRADIENT_ACCUMULATION_STEPS}")
    print(f"  Effective batch size: {EFFECTIVE_BATCH_SIZE}")
    print(f"  Max epochs: {EPOCHS}")
    print(f"  Learning rate: {LEARNING_RATE}")
    print(f"  Max length: {MAX_LEN}")
    print(f"  Early stopping patience: {EARLY_STOPPING_PATIENCE}")
    print(f"  Mixed precision: {'FP16' if torch.cuda.is_available() else 'No'}")

    # Load and split data
    train_df, val_df, test_df = load_and_split()

    # Tokenizer
    print("\n[3/7] Loading tokenizer...")
    tokenizer = RobertaTokenizer.from_pretrained(MODEL_NAME)
    print("  Done")

    # Dataloaders
    train_loader, val_loader = create_dataloaders(train_df, val_df, tokenizer)

    # Model
    print("\n[4/7] Loading model...")
    model = RobertaForSequenceClassification.from_pretrained(MODEL_NAME, num_labels=6)
    model = model.to(device)
    print("  Done")

    # Class weights
    print("\n[5/7] Computing class weights...")
    class_weights = compute_class_weights(train_df)
    if torch.cuda.is_available():
        class_weights = class_weights.half()

    # Optimizer and scheduler
    optimizer = AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
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

        train_loss = train_epoch(model, train_loader, optimizer, scheduler, scaler, class_weights, epoch, EPOCHS)

        # Validation progress
        val_pbar = tqdm(val_loader, desc=f"  Epoch {epoch+1}/{EPOCHS} [Val]", unit="batch", leave=False)
        val_loss, val_acc, all_preds, all_labels = validate_with_pbar(model, val_pbar, class_weights, scaler)
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
            print(f"    [✓] Saved best model (macro_f1: {best_metric:.4f})")
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
    _, val_acc, all_preds, all_labels = validate(model, val_loader, class_weights, scaler)

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
