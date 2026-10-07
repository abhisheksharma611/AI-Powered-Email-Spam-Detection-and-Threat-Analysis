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
    """Extract 10 numerical features from raw text.
    Must be called on ORIGINAL (un-preprocessed) text for character-level stats.
    Returns np.ndarray of shape (n_samples, 10).
    """
    urgency_words = ['urgent', 'now', 'immediately', 'today', 'limited', 'act now',
                     'hurry', 'quick', 'last chance', 'only', 'exclusive', 'warning', 'alert']

    # Feature 7: attachment / payload-delivery markers (malware mechanism)
    attach_markers = ['.exe', '.zip', '.docm', '.rar', '.msi', '.cmd', '.bat',
                      'attachment', 'attached', 'enable macros', 'enable content',
                      'run as administrator', 'disable your antivirus', 'antivirus',
                      'installer', 'unsigned']
    # Feature 8: credential / payment harvesting (phishing mechanism)
    cred_markers = ['password', 'one time code', 'otp', 'card number', 'security code',
                    'billing address', 'verify your identity', 'cvv', 'sort code',
                    'account number', 'date of birth', 'national insurance']
    # Feature 9: deadline pressure
    deadline_markers = ['within 24 hours', 'within 12 hours', 'within 6 hours',
                        'within 48 hours', 'expires', 'expire today', 'last chance',
                        'final notice', 'before friday', 'act now', 'immediately']
    # Feature 10: money / prize lure
    prize_markers = ['prize', 'winner', 'lottery', 'lakh', 'crore', 'million',
                     'inheritance', 'risk free', 'guaranteed', 'congratulations',
                     'you have been selected']

    features = []
    for text in text_series:
        if not text or not isinstance(text, str):
            features.append([0.0] * 10)
            continue

        text_len = len(text)
        lower = text.lower()

        # Feature 1: Normalized text length
        # Divisor 1000.0, not 500.0: the corpus is 214-733 chars, so /500.0 saturated
        # 37.5% of all rows and 98.6% of malware rows, turning this into a malware flag.
        length = min(text_len / 1000.0, 1.0)

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
        urgency_count = sum(1 for word in urgency_words if word in lower)
        urgency_score = min(urgency_count / 5.0, 1.0)

        # Feature 7: attachment / payload-delivery score
        attach_score = min(sum(1 for m in attach_markers if m in lower) / 3.0, 1.0)

        # Feature 8: credential / payment harvesting score
        cred_score = min(sum(1 for m in cred_markers if m in lower) / 3.0, 1.0)

        # Feature 9: deadline pressure score
        deadline_score = min(sum(1 for m in deadline_markers if m in lower) / 3.0, 1.0)

        # Feature 10: money / prize lure score
        prize_score = min(sum(1 for m in prize_markers if m in lower) / 2.0, 1.0)

        features.append([length, special_ratio, capital_ratio, has_url, has_phone,
                         urgency_score, attach_score, cred_score, deadline_score, prize_score])

    return np.array(features, dtype=np.float32)
