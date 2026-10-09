import joblib
import numpy as np
import torch
import os
import logging
from scipy.sparse import hstack
from dotenv import load_dotenv

from models.utils.preprocessing import preprocess_text, extract_engineered_features

# PREDICTOR_BACKEND is read from the environment at import time, so the .env
# file has to be loaded by THIS module. Relying on config.py to have done it
# first is a trap: app.py imports config at line 19 and this module at line 24,
# so it happens to work there, but every standalone entry point (evaluate.py,
# run_local.py, `python -c "from models.predictor import Predictor"`) imports
# this module WITHOUT config, never reads .env, and silently falls back to
# roberta. That is exactly how a machine can have PREDICTOR_BACKEND=laya in
# .env and still boot with RoBERTa + ensemble loaded.
load_dotenv()

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

# ── Backend switch (EXPERIMENT) ────────────────────────────────────────────
#
# 'roberta' (default) is the original behaviour: fine-tuned RoBERTa 0.90 +
# sklearn ensemble 0.10, both loaded from models/. Untouched.
#
# 'laya' serves every prediction from laya:en over Ollaya on port 11435 and
# does NOT load the 498 MB checkpoint or the 111 MB ensemble. The artefacts
# stay on disk either way; only the loading is skipped, so switching back is
# a one-line .env change with no retraining and no data loss.
#
# Set PREDICTOR_BACKEND=laya in .env. Defaults to 'roberta' when unset.
PREDICTOR_BACKEND = os.environ.get('PREDICTOR_BACKEND', 'roberta').strip().lower()


def risk_from_confidence(category, confidence):
    """The 0-100 risk score, from a category and a top-1 probability.

    Deliberately shared: the Laya branch of Predictor and POST /api/laya_text
    must produce the same number for the same verdict, or the same email would
    carry two different risk scores depending on which route classified it.
    """
    base_risk = BASE_RISKS.get(category, 40)
    risk_score = base_risk * confidence
    if confidence < 0.25:
        risk_score *= 0.5
    return min(max(int(risk_score), 0), 100)


class Predictor:
    # Class-level default, not just an __init__ assignment. Tests construct a
    # bare instance via Predictor.__new__(Predictor) to exercise the fusion
    # fallbacks without loading any weights; an instance attribute set only in
    # __init__ would be absent there and every predict_* call would raise
    # AttributeError. A class attribute is inherited by that instance too.
    backend = PREDICTOR_BACKEND if PREDICTOR_BACKEND in ('roberta', 'laya') else 'roberta'

    def __init__(self):
        self.roberta = None
        self.ensemble_model = None
        self.vectorizer = None
        self.encoder = None
        self._is_trained = False
        self.categories = CATEGORIES
        self.backend = self.backend  # instance value shadows the class default
        self._load_models()

    def _load_models(self):
        # ── Laya backend: no local artefacts are touched at all ──────────────
        # The 498 MB RoBERTa checkpoint and the 111 MB ensemble are deliberately
        # NOT opened. They stay on disk untouched, so PREDICTOR_BACKEND=roberta
        # restores the original stack with no retraining.
        if self.backend == 'laya':
            from utils.laya_client import available as laya_available
            reachable = laya_available()
            self._is_trained = reachable
            if reachable:
                logger.info("Predictor backend: LAYA (RoBERTa + ensemble NOT loaded)")
            else:
                logger.error(
                    "Predictor backend: LAYA but no Ollaya server at %s. "
                    "Predictions will fail until `ollaya serve` is running.",
                    os.environ.get('LAYA_BASE_URL', 'http://localhost:11435'),
                )
            return

        # Load RoBERTa
        from models.roberta_model import get_roberta_model

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

    def _get_laya_probs(self, text):
        """Six-category probability dict from Laya, or None if it is unavailable.

        Returns the same {category: prob} shape as _get_roberta_probs so the
        fusion and reporting code below is shared between backends.
        """
        try:
            from utils.laya_client import analyze_email as laya_analyze
        except Exception as e:
            logger.error(f"Laya client import failed: {e}")
            return None

        result = laya_analyze(body=text)
        if result is None:
            return None
        return result['category_probabilities']

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

    def _get_laya_probs_batch(self, texts):
        """Laya over a list, one HTTP request per message.

        Laya has no batch mode: putting several emails in one `state` returns
        HTTP 422. So this is a loop, which is the entire reason Laya is not
        suitable for the inbox scan at scale. Measured ~66ms per message, so a
        32-message slice is roughly 2.1s.
        """
        out = []
        failures = 0
        for t in texts:
            probs = self._get_laya_probs(t)
            if probs is None:
                failures += 1
                out.append(None)
            else:
                out.append(probs)

        if failures:
            # Match the ensemble branch's discipline: a failure here means the
            # verdict for that message is a guess, not a measurement.
            logger.error(
                "Laya failed on %d of %d messages. Those rows fall back to a "
                "uniform 1/6 distribution, which is a COIN FLIP across six "
                "categories, not a classification.",
                failures, len(texts),
            )
        return out

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
            # BUG B FIX: engineered features must be extracted from RAW text, not the
            # preprocessed text. preprocess_text() replaces URLs with the token 'URL',
            # strips digits, and removes punctuation, so features 4 (has_url), 5
            # (has_phone), 7 (attach), 8 (cred), 9 (deadline) and 10 (prize) were
            # dead in the batch path. ensemble_train.py:103 and evaluate.py:195 both
            # pass raw text; this line was the only mismatch.
            eng_features = extract_engineered_features([texts[i] for i in valid_indices])
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
            # This used to be logger.warning + return None, which is how a 20010
            # vs 20006 feature mismatch went unnoticed for an entire session: the
            # ensemble silently contributed nothing, the 0.90/0.10 blend in
            # predict_batch never ran, and every classification was pure RoBERTa
            # while the console showed one unremarkable warning line per batch.
            # A feature-shape failure is a broken artefact, not a soft condition,
            # so it is logged at ERROR with the shape that was actually passed.
            shape = None
            try:
                shape = combined.shape
            except Exception:
                pass
            logger.error(
                "Ensemble batch proba FAILED (%s: %s) shape=%s expected=%s. "
                "The ensemble contributed NOTHING to these predictions and the "
                "blend fell back to RoBERTa alone. Retrain with "
                "'python models/ensemble_train.py'.",
                type(e).__name__, e, shape,
                getattr(self.ensemble_model, 'n_features_in_', 'unknown'))
            return None

    def predict_batch(self, texts):
        # ── Laya backend ───────────────────────────────────────────────────
        # One request per message, no fusion. `source` records which engine
        # answered so a stored row can never be silently misattributed.
        if self.backend == 'laya':
            laya_list = self._get_laya_probs_batch(texts)
            results = []
            for i in range(len(texts)):
                probs = laya_list[i] if laya_list else None
                if probs is None:
                    probs = {cat: 1.0 / len(CATEGORIES) for cat in CATEGORIES}
                    source = 'uniform'
                else:
                    source = 'laya'
                category = max(probs, key=probs.get)
                confidence = probs[category]
                risk_score = risk_from_confidence(category, confidence)
                results.append({
                    'category': category,
                    'probabilities': probs,
                    'risk_score': risk_score,
                    'risk_level': self.get_risk_level(risk_score, category),
                    'is_spam': category != 'legitimate',
                    'confidence': confidence * 100,
                    'model_source': source,
                })
            return results

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
        if self.backend == 'laya':
            probs = self._get_laya_probs(text)
            if probs is None:
                probs = {cat: 1.0 / len(CATEGORIES) for cat in CATEGORIES}
                source = 'uniform'
            else:
                source = 'laya'
            category = max(probs, key=probs.get)
            confidence = probs[category]
            risk_score = risk_from_confidence(category, confidence)
            return {
                'category': category,
                'probabilities': probs,
                'risk_score': risk_score,
                'risk_level': self.get_risk_level(risk_score, category),
                'is_spam': category != 'legitimate',
                'confidence': confidence * 100,
                'model_source': source,
            }

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
