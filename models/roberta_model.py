import torch
from transformers import RobertaTokenizer, RobertaForSequenceClassification
import os

CATEGORY_NAMES = {
    0: 'spam', 1: 'legitimate', 2: 'promotion',
    3: 'malware', 4: 'newsletter', 5: 'phishing'
}

DISPLAY_NAMES = {
    0: 'Spam', 1: 'Not Spam', 2: 'Promotion',
    3: 'Malware', 4: 'Newsletter', 5: 'Phishing'
}

torch.set_num_threads(4)


class RobertaModel:
    def __init__(self, model_path='models/best_roberta_model.pth', max_len=256):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.max_len = max_len
        self.model = None
        self.tokenizer = None
        self.is_loaded = False
        self._load(model_path)

    def _load(self, model_path):
        try:
            local_cache = os.path.join('models', 'tokenizer_cache')
            if os.path.exists(local_cache):
                self.tokenizer = RobertaTokenizer.from_pretrained(local_cache)
            else:
                self.tokenizer = RobertaTokenizer.from_pretrained('roberta-base', local_files_only=True)
                try:
                    self.tokenizer.save_pretrained(local_cache)
                except Exception:
                    pass

            self.model = RobertaForSequenceClassification.from_pretrained(
                'roberta-base', num_labels=6, local_files_only=True
            )
            self.model.load_state_dict(torch.load(model_path, map_location=self.device))
            self.model = self.model.to(self.device)
            self.model.eval()
            self.is_loaded = True
        except Exception as e:
            print(f"Error loading RoBERTa model: {e}")
            self.is_loaded = False

    def predict(self, text):
        if not self.is_loaded:
            return {'category': 'unknown', 'label': -1, 'confidence': 0.0, 'error': 'Model not loaded'}

        try:
            encoding = self.tokenizer(
                text, add_special_tokens=True, max_length=self.max_len,
                padding='max_length', truncation=True, return_tensors='pt'
            )
            input_ids = encoding['input_ids'].to(self.device)
            attention_mask = encoding['attention_mask'].to(self.device)

            with torch.no_grad():
                if torch.cuda.is_available():
                    with torch.cuda.amp.autocast():
                        outputs = self.model(input_ids=input_ids, attention_mask=attention_mask)
                else:
                    outputs = self.model(input_ids=input_ids, attention_mask=attention_mask)

            probs = torch.softmax(outputs.logits, dim=1)
            pred = torch.argmax(probs, dim=1).item()
            confidence = float(probs[0][pred])

            return {
                'category': CATEGORY_NAMES[pred],
                'label': pred,
                'confidence': confidence,
                'category_name': DISPLAY_NAMES[pred]
            }
        except Exception as e:
            return {'category': 'unknown', 'label': -1, 'confidence': 0.0, 'error': str(e)}

    def predict_proba(self, text):
        if not self.is_loaded:
            return {cat: 1.0/6 for cat in CATEGORY_NAMES.values()}

        try:
            encoding = self.tokenizer(
                text, add_special_tokens=True, max_length=self.max_len,
                padding='max_length', truncation=True, return_tensors='pt'
            )
            input_ids = encoding['input_ids'].to(self.device)
            attention_mask = encoding['attention_mask'].to(self.device)

            with torch.no_grad():
                if torch.cuda.is_available():
                    with torch.cuda.amp.autocast():
                        outputs = self.model(input_ids=input_ids, attention_mask=attention_mask)
                else:
                    outputs = self.model(input_ids=input_ids, attention_mask=attention_mask)

            probs = torch.softmax(outputs.logits, dim=1)
            return {CATEGORY_NAMES[i]: float(probs[0][i]) for i in range(6)}
        except Exception:
            return {cat: 1.0/6 for cat in CATEGORY_NAMES.values()}

    def predict_batch(self, texts, batch_size=None):
        results = []
        if batch_size is None:
            batch_size = 16 if self.device.type == 'cuda' else 4

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i+batch_size]
            encoding = self.tokenizer(
                batch, add_special_tokens=True, max_length=self.max_len,
                padding='max_length', truncation=True, return_tensors='pt'
            )
            input_ids = encoding['input_ids'].to(self.device)
            attention_mask = encoding['attention_mask'].to(self.device)

            with torch.no_grad():
                if self.device.type == 'cuda':
                    with torch.cuda.amp.autocast():
                        outputs = self.model(input_ids=input_ids, attention_mask=attention_mask)
                else:
                    outputs = self.model(input_ids=input_ids, attention_mask=attention_mask)

            probs = torch.softmax(outputs.logits, dim=1)
            preds = torch.argmax(probs, dim=1)

            for j in range(len(batch)):
                pred = preds[j].item()
                results.append({
                    'category': CATEGORY_NAMES[pred],
                    'label': pred,
                    'confidence': float(probs[j][pred]),
                    'category_name': DISPLAY_NAMES[pred]
                })

        return results

    def predict_proba_batch(self, texts, batch_size=None):
        if not self.is_loaded:
            uniform = {cat: 1.0/6 for cat in CATEGORY_NAMES.values()}
            return [uniform for _ in texts]

        results = []
        if batch_size is None:
            batch_size = 16 if self.device.type == 'cuda' else 4

        try:
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i+batch_size]
                encoding = self.tokenizer(
                    batch, add_special_tokens=True, max_length=self.max_len,
                    padding='max_length', truncation=True, return_tensors='pt'
                )
                input_ids = encoding['input_ids'].to(self.device)
                attention_mask = encoding['attention_mask'].to(self.device)

                with torch.no_grad():
                    if self.device.type == 'cuda':
                        with torch.cuda.amp.autocast():
                            outputs = self.model(input_ids=input_ids, attention_mask=attention_mask)
                    else:
                        outputs = self.model(input_ids=input_ids, attention_mask=attention_mask)

                probs = torch.softmax(outputs.logits, dim=1)

                for j in range(len(batch)):
                    results.append({CATEGORY_NAMES[k]: float(probs[j][k]) for k in range(6)})
        except Exception as e:
            print(f"predict_proba_batch failed: {e}")
            uniform = {cat: 1.0/6 for cat in CATEGORY_NAMES.values()}
            return [uniform for _ in texts]

        return results


_singleton = None


def get_roberta_model():
    global _singleton
    if _singleton is None:
        _singleton = RobertaModel()
    return _singleton
