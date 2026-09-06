import re
import numpy as np

def preprocess_text(text):
    """Preprocess text for ML analysis.
    Single source of truth — ALL scripts must import from here.
    """
    if not text or not isinstance(text, str):
        return ''

    try:
        text = text.lower()
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'http[s]?://\S+|www\.\S+', 'URL', text)
        text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', 'EMAIL', text)
        text = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', 'PHONE', text)
        text = re.sub(r'[!]{2,}', '!', text)
        text = re.sub(r'[?]{2,}', '?', text)
        text = re.sub(r'\b\d+\b', 'NUMBER', text)
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[^a-zA-Z\s]', '', text)
        return text.strip()
    except Exception:
        return str(text) if text else ''


def extract_engineered_features(text_series):
    """Extract 6 numerical features from raw text.
    Must be called on ORIGINAL (un-preprocessed) text for character-level stats.
    Returns np.ndarray of shape (n_samples, 6).
    """
    urgency_words = ['urgent', 'now', 'immediately', 'today', 'limited', 'act now',
                     'hurry', 'quick', 'last chance', 'only', 'exclusive', 'warning', 'alert']

    features = []
    for text in text_series:
        if not text or not isinstance(text, str):
            features.append([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            continue

        text_len = len(text)

        # Feature 1: Normalized text length
        length = min(text_len / 500.0, 1.0)

        # Feature 2: Special character ratio
        special_chars = sum(1 for c in text if c in '!@#$%^&*()_+-=[]{}|;:,.<>?')
        special_ratio = special_chars / text_len if text_len > 0 else 0.0

        # Feature 3: Capital letter ratio
        capital_chars = sum(1 for c in text if c.isupper())
        capital_ratio = capital_chars / text_len if text_len > 0 else 0.0

        # Feature 4: URL present flag
        has_url = 1.0 if re.search(r'http[s]?://\S+|www\.\S+', text) else 0.0

        # Feature 5: Phone number present flag
        has_phone = 1.0 if re.search(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', text) else 0.0

        # Feature 6: Urgency word score
        urgency_count = sum(1 for word in urgency_words if word in text.lower())
        urgency_score = min(urgency_count / 5.0, 1.0)

        features.append([length, special_ratio, capital_ratio, has_url, has_phone, urgency_score])

    return np.array(features, dtype=np.float32)
