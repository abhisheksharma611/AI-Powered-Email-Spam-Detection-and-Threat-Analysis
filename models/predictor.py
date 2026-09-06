import joblib
import numpy as np
import torch
import os
import logging
from scipy.sparse import hstack

from models.utils.preprocessing import preprocess_text, extract_engineered_features
from models.roberta_model import get_roberta_model

logger = logging.getLogger(__name__)

CATEGORIES = ['spam', 'legitimate', 'promotion', 'malware', 'newsletter', 'phishing']

BASE_RISKS = {
    'legitimate': 5,
    'newsletter': 10,
    'promotion': 30,
    'spam': 50,
    'phishing': 80,
    'malware': 85
}


class Predictor:
    def __init__(self):
        self.roberta = None
        self.ensemble_model = None
        self.vectorizer = None
        self.encoder = None
        self._is_trained = False
        self.categories = CATEGORIES
        self._load_models()

    def _load_models(self):
        # Load RoBERTa
        roberta_path = 'models/best_roberta_model.pth'
        if os.path.exists(roberta_path):
            try:
                self.roberta = get_roberta_model()
                logger.info("RoBERTa model loaded successfully")
            except Exception as e:
                logger.warning(f"Failed to load RoBERTa: {e}")
                self.roberta = None

        # Load Ensemble
        ensemble_path = 'models/ensemble_model.joblib'
        vectorizer_path = 'models/vectorizer.joblib'
        encoder_path = 'models/encoder.joblib'

        if all(os.path.exists(p) for p in [ensemble_path, vectorizer_path, encoder_path]):
            try:
                self.ensemble_model = joblib.load(ensemble_path)
                self.vectorizer = joblib.load(vectorizer_path)
                self.encoder = joblib.load(encoder_path)
                logger.info("Ensemble model loaded successfully")
            except Exception as e:
                logger.warning(f"Failed to load ensemble: {e}")
                self.ensemble_model = None
                self.vectorizer = None
                self.encoder = None

        if self.roberta is not None or self.ensemble_model is not None:
            self._is_trained = True
            available = []
            if self.roberta is not None:
                available.append('RoBERTa')
            if self.ensemble_model is not None:
                available.append('Ensemble')
            logger.info(f"Predictor ready with: {' + '.join(available)}")
        else:
            logger.warning("No trained models found. No predictions possible.")

    @property
    def is_trained(self):
        return self._is_trained

    def _get_roberta_probs(self, text):
        if self.roberta is None or not self.roberta.is_loaded:
            return None
        try:
            return self.roberta.predict_proba(text)
        except Exception as e:
            logger.warning(f"RoBERTa proba failed: {e}")
            return None

    def _get_ensemble_probs(self, text):
        if self.ensemble_model is None:
            return None
        try:
            processed = preprocess_text(text)
            if not processed:
                return None
            tfidf_vec = self.vectorizer.transform([processed])
            eng_features = extract_engineered_features([text])
            combined = hstack([tfidf_vec, eng_features])

            raw_probs = self.ensemble_model.predict_proba(combined)
            int_to_cat = {0: 'spam', 1: 'legitimate', 2: 'promotion',
                          3: 'malware', 4: 'newsletter', 5: 'phishing'}
            probs = {}
            for i, class_label in enumerate(self.encoder.classes_):
                cat_name = int_to_cat.get(int(class_label), str(class_label))
                probs[cat_name] = float(raw_probs[0][i])
            return probs
        except Exception as e:
            logger.warning(f"Ensemble proba failed: {e}")
            return None

    def _get_roberta_probs_batch(self, texts):
        if self.roberta is None or not self.roberta.is_loaded:
            return None
        try:
            return self.roberta.predict_proba_batch(texts)
        except Exception as e:
            logger.warning(f"RoBERTa batch proba failed: {e}")
            return None

    def _get_ensemble_probs_batch(self, texts):
        if self.ensemble_model is None:
            return None
        try:
            processed_texts = [preprocess_text(t) for t in texts]
            valid_indices = [i for i, t in enumerate(processed_texts) if t]
            if not valid_indices:
                uniform = {cat: 1.0/len(CATEGORIES) for cat in CATEGORIES}
                return [uniform for _ in texts]

            valid_texts = [processed_texts[i] for i in valid_indices]
            tfidf_vecs = self.vectorizer.transform(valid_texts)
            eng_features = extract_engineered_features(valid_texts)
            combined = hstack([tfidf_vecs, eng_features])

            raw_probs = self.ensemble_model.predict_proba(combined)
            int_to_cat = {0: 'spam', 1: 'legitimate', 2: 'promotion',
                          3: 'malware', 4: 'newsletter', 5: 'phishing'}

            results = [None] * len(texts)
            for idx, result_idx in enumerate(valid_indices):
                probs = {}
                for i, class_label in enumerate(self.encoder.classes_):
                    cat_name = int_to_cat.get(int(class_label), str(class_label))
                    probs[cat_name] = float(raw_probs[idx][i])
                results[result_idx] = probs

            uniform = {cat: 1.0/len(CATEGORIES) for cat in CATEGORIES}
            for i in range(len(texts)):
                if results[i] is None:
                    results[i] = uniform

            return results
        except Exception as e:
            logger.warning(f"Ensemble batch proba failed: {e}")
            return None

    def predict_batch(self, texts):
        roberta_probs_list = self._get_roberta_probs_batch(texts)
        ensemble_probs_list = self._get_ensemble_probs_batch(texts)

        results = []
        for i in range(len(texts)):
            rp = roberta_probs_list[i] if roberta_probs_list else None
            ep = ensemble_probs_list[i] if ensemble_probs_list else None

            if rp is not None and ep is not None:
                final_probs = {}
                for cat in CATEGORIES:
                    final_probs[cat] = 0.90 * rp.get(cat, 0) + 0.10 * ep.get(cat, 0)
                source = 'roberta+ensemble'
            elif rp is not None:
                final_probs = rp
                source = 'roberta'
            elif ep is not None:
                final_probs = ep
                source = 'ensemble'
            else:
                final_probs = {cat: 1.0 / len(CATEGORIES) for cat in CATEGORIES}
                source = 'uniform'

            category = max(final_probs, key=final_probs.get)
            confidence = final_probs[category]

            risk_score = self._calc_risk(final_probs, category)
            risk_level = self.get_risk_level(risk_score, category)
            is_spam = category != 'legitimate'

            results.append({
                'category': category,
                'probabilities': final_probs,
                'risk_score': risk_score,
                'risk_level': risk_level,
                'is_spam': is_spam,
                'confidence': confidence * 100,
                'model_source': source
            })

        return results

    def predict_single(self, text):
        roberta_probs = self._get_roberta_probs(text)
        ensemble_probs = self._get_ensemble_probs(text)

        if roberta_probs is not None and ensemble_probs is not None:
            final_probs = {}
            for cat in CATEGORIES:
                final_probs[cat] = 0.90 * roberta_probs.get(cat, 0) + 0.10 * ensemble_probs.get(cat, 0)
            source = 'roberta+ensemble'
        elif roberta_probs is not None:
            final_probs = roberta_probs
            source = 'roberta'
        elif ensemble_probs is not None:
            final_probs = ensemble_probs
            source = 'ensemble'
        else:
            final_probs = {cat: 1.0 / len(CATEGORIES) for cat in CATEGORIES}
            source = 'uniform'

        category = max(final_probs, key=final_probs.get)
        confidence = final_probs[category]

        risk_score = self._calc_risk(final_probs, category)
        risk_level = self.get_risk_level(risk_score, category)
        is_spam = category != 'legitimate'

        return {
            'category': category,
            'probabilities': final_probs,
            'risk_score': risk_score,
            'risk_level': risk_level,
            'is_spam': is_spam,
            'confidence': confidence * 100,
            'model_source': source
        }

    def _calc_risk(self, probs, category):
        confidence = max(probs.values()) if probs else 0.5
        base_risk = BASE_RISKS.get(category, 40)
        risk_score = base_risk * confidence
        if confidence < 0.25:
            risk_score *= 0.5
        return min(max(int(risk_score), 0), 100)

    def get_risk_level(self, risk_score, category=None):
        if risk_score >= 61:
            return 'High'
        elif risk_score >= 41:
            return 'Medium'
        return 'Low'

    def get_category_info(self, category):
        info = {
            'legitimate': {'color': 'success', 'icon': 'check-circle', 'emoji': '✅',
                           'description': 'Safe and legitimate email content',
                           'display_name': 'Not Spam', 'risk_level': 'Very Low'},
            'promotion': {'color': 'info', 'icon': 'bullhorn', 'emoji': '📢',
                          'description': 'Marketing or promotional content',
                          'display_name': 'Promotion', 'risk_level': 'Low'},
            'phishing': {'color': 'dark', 'icon': 'skull-crossbones', 'emoji': '🎣',
                         'description': 'Phishing attempt - credential theft',
                         'display_name': 'Phishing', 'risk_level': 'High'},
            'malware': {'color': 'warning', 'icon': 'virus', 'emoji': '🦠',
                        'description': 'Malware or malicious software',
                        'display_name': 'Malware', 'risk_level': 'High'},
            'newsletter': {'color': 'secondary', 'icon': 'newspaper', 'emoji': '📰',
                           'description': 'Newsletter or subscription content',
                           'display_name': 'Newsletter', 'risk_level': 'Low'},
            'spam': {'color': 'danger', 'icon': 'exclamation-triangle', 'emoji': '🚨',
                     'description': 'General spam content',
                     'display_name': 'Spam', 'risk_level': 'High'}
        }
        return info.get(category, info['legitimate'])

    def get_risk_color_info(self, risk_level):
        legacy_map = {'Very Low': 'Low', 'Critical': 'High'}
        normalized = legacy_map.get(risk_level, risk_level)
        colors = {
            'Low': {'color': 'success', 'icon': 'check-circle', 'emoji': '🟢'},
            'Medium': {'color': 'warning', 'icon': 'exclamation-circle', 'emoji': '⚠️'},
            'High': {'color': 'danger', 'icon': 'exclamation-triangle', 'emoji': '🔴'}
        }
        return colors.get(normalized, colors['Low'])
