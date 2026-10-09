"""Laya decision layer (local, zero-token).

Laya is a 421M non-autoregressive decision model: it scores every option of
every question in ONE forward pass and returns calibrated probabilities. It
does not generate text. It only classifies and scores.

Runtime: Ollaya (not Ollama). Ollaya serves the checkpoint on Windows over
CUDA and listens on 11435; Ollama keeps 11434 and is used separately by
utils/ai_explanation.py for narrative explanations. The two never collide.

EXPERIMENT SCOPE. This is wired to the dashboard Test Text modal only. The
inbox scan, Start Analysis and /api/analyze_text still use RoBERTa + ensemble
and are untouched. Revert point is commit 4d5cc8d.

The API contract below was verified live against the running server, not
assumed. Three details cost real round-trips to discover and will cost them
again to anyone who edits this file:

  - `questions` must be a JSON OBJECT. A list is rejected with HTTP 422
    ("Input should be a valid dictionary").
  - `criteria` must be a list of PLAIN STRINGS. A list of
    {"name":..., "description":...} objects is rejected with HTTP 422
    ("Input should be a valid string").
  - A `noul` answer carries its value under the key "noul", NOT "score"
    and NOT "probability". Reading the wrong key yields 0.0 for every
    question and the caller silently decides everything is legitimate.

Batching is NOT supported: putting ten emails in one `state` returns HTTP
422. One email per request.

Measured on this machine (RTX 3050, laya:en):
    cold load (server -> VRAM)   ~7.4 s   once per keep_alive window
    warm, single question          ~50 ms
    output tokens                   0

Accuracy on 60 held-out rows of models/test_set.csv was 50.0%, against
96.7% for the RoBERTa + ensemble stack on the same rows. Laya is a zero-shot
decision model and this corpus is not its training data, so the gap is
expected. This is why the modal labels the engine and never falls back
silently: if Laya is unavailable the user is told, rather than being handed
a RoBERTa answer under a Laya label.
"""

import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Local Ollaya / Laya (experiment: dashboard Test Text modal only) ────────
LAYA_BASE_URL = os.environ.get('LAYA_BASE_URL', 'http://localhost:11435').rstrip('/')
LAYA_MODEL = os.environ.get('LAYA_MODEL', 'laya:en')
LAYA_TIMEOUT = int(os.environ.get('LAYA_TIMEOUT', '60'))
# Keep the 421M checkpoint resident between clicks. Matches how
# utils/ai_explanation.py pins gemma2:2b.
LAYA_KEEP_ALIVE = os.environ.get('LAYA_KEEP_ALIVE', '15m')
# Laya shares a 512-token budget between state and questions, so a long paste
# is truncated by the model anyway. Clipping here keeps the request honest
# about what was actually read, and keeps the prompt side small.
LAYA_MAX_CHARS = int(os.environ.get('LAYA_MAX_CHARS', '1400'))
LAYA_MAX_RETENTION = int(os.environ.get('LAYA_MAX_RETENTION', '60'))

# The six categories, in the project's canonical order. Keys are the model's
# own wording; values are the slugs everything downstream already speaks
# (models/predictor.py CATEGORIES, app.py CATEGORIES_ALL, the templates).
#
# These strings are the only thing standing between Laya and the UI, and they
# are worth tuning: three phrasings were measured on 12 rows and this one
# scored best (58% vs 50% for bare category names). Each option names its
# category first so the model has an anchor, then states what distinguishes
# it. Overlapping options are what collapse the taxonomy, so the
# "no threat involved" qualifier on Promotion is load-bearing.
_LAYA_CRITERIA = (
    'Spam: unsolicited bulk junk that only wants attention',
    'Malware: message carrying a harmful file, attachment or payload',
    'Promotion: a marketing ad or sale from a company, no threat involved',
    'Newsletter: a recurring digest or mailing-list issue',
    'Phishing: a fake login, verification or payment page asking for secrets',
    'Legitimate: genuine one-to-one or work mail from a real sender',
)

# Model wording -> project slug. Keys must match _LAYA_CRITERIA exactly.
_CRITERIA_TO_CATEGORY = {
    'Spam: unsolicited bulk junk that only wants attention': 'spam',
    'Malware: message carrying a harmful file, attachment or payload': 'malware',
    'Promotion: a marketing ad or sale from a company, no threat involved': 'promotion',
    'Newsletter: a recurring digest or mailing-list issue': 'newsletter',
    'Phishing: a fake login, verification or payment page asking for secrets': 'phishing',
    'Legitimate: genuine one-to-one or work mail from a real sender': 'legitimate',
}

# The project's six slugs, used to fill any category Laya omits from its
# probability vector so the response shape never varies.
_ALL_CATEGORIES = ('spam', 'legitimate', 'promotion', 'malware', 'newsletter', 'phishing')


def _clip(text, limit=LAYA_MAX_CHARS):
    """Hard-truncate to the character budget; Laya truncates tokens anyway."""
    if not text:
        return ''
    text = str(text)
    if len(text) <= limit:
        return text
    # Cut on a word boundary so the last token is not a fragment.
    return text.rsplit(' ', 1)[0][:limit] if ' ' in text else text[:limit]


def available():
    """True when the Ollaya server answers. Cheap: GET on the root."""
    try:
        r = requests.get(LAYA_BASE_URL + '/', timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def analyze_email(subject=None, body=None, sender=None):
    """Classify one email with Laya.

    Returns None on ANY failure so the caller can report an honest refusal
    instead of inventing a verdict. Never raises.

    On success returns:
        {
            'category': 'phishing',
            'category_probabilities': {'spam': 0.39, ...},   # all 6 keys, sums to 1
            'category_confidence': 0.39,
            'latency_ms': 48.1,
        }
    """
    state_parts = []
    if subject:
        state_parts.append(str(subject))
    if body:
        state_parts.append(str(body))
    if sender:
        state_parts.append('From: ' + str(sender))
    state = {'body': _clip(' '.join(state_parts))}
    if not state['body']:
        return None

    payload = {
        'model': LAYA_MODEL,
        'state': state,
        # Object, not list. See the module docstring.
        'questions': {
            'category': {
                'type': 'choice',
                'instructions': 'Pick the single best category for this email.',
                # Plain strings. See the module docstring.
                'criteria': list(_LAYA_CRITERIA),
            }
        },
        'keep_alive': LAYA_KEEP_ALIVE,
    }

    start = time.perf_counter()
    try:
        response = requests.post(
            LAYA_BASE_URL + '/v1/systemone',
            json=payload,
            timeout=LAYA_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except requests.Timeout:
        logger.warning('Laya timed out after %ss', LAYA_TIMEOUT)
        return None
    except requests.RequestException as e:
        logger.error('Laya request failed: %s', e)
        return None
    except ValueError as e:
        logger.error('Laya returned a non-JSON payload: %s', e)
        return None

    latency_ms = round((time.perf_counter() - start) * 1000, 1)

    try:
        answer = data['answers']['category']
        raw_probs = answer['probabilities']
        category = _CRITERIA_TO_CATEGORY.get(answer.get('choice'))
    except (KeyError, TypeError) as e:
        logger.error('Laya returned an unexpected payload: %s (body=%s)', e, data)
        return None

    if category is None:
        logger.error('Laya chose an unknown option %r', answer.get('choice'))
        return None

    # Normalise onto the project's six slugs. Laya returns a probability for
    # every criterion, so this is normally a straight rename; the fallback
    # covers a partial vector so the caller never has to guard for a short
    # dict.
    probs = {}
    for criterion, value in (raw_probs or {}).items():
        slug = _CRITERIA_TO_CATEGORY.get(criterion)
        if slug is not None:
            try:
                probs[slug] = float(value)
            except (TypeError, ValueError):
                continue

    if not probs:
        logger.error('Laya returned no usable probabilities: %s', raw_probs)
        return None

    total = sum(probs.values())
    if total > 0:
        probs = {k: v / total for k, v in probs.items()}
    for missing in _ALL_CATEGORIES:
        probs.setdefault(missing, 0.0)

    # Trust the model's own top-1 probability rather than re-reading it out of
    # the vector: it is what Laya calibrated, and the vector may have been
    # renormalised above.
    try:
        confidence = float(answer.get('confidence', probs[category]))
    except (TypeError, ValueError):
        confidence = probs[category]

    return {
        'category': category,
        'category_probabilities': probs,
        'category_confidence': confidence,
        'latency_ms': latency_ms,
    }


def warm_up():
    """Pay the ~7s cold load once, up front, instead of on the first click.

    NOT called at app startup: that would add a blocking ~7s to boot and the
    Flask dev reloader would repeat it. The first click pays it instead,
    inside the modal's existing 25s fetch timeout.

    Safe to call manually from a shell while testing. Returns False if the
    server is not up.
    """
    if not available():
        logger.info('Laya warm-up skipped: server not reachable at %s', LAYA_BASE_URL)
        return False
    try:
        analyze_email(body='Laya warm up')
        logger.info('Laya warm-up complete')
        return True
    except Exception as e:
        logger.error('Laya warm-up failed: %s', e)
        return False


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    print(f'Laya server {LAYA_BASE_URL} reachable: {available()}')
    r = analyze_email(
        subject='Urgent: verify your account now',
        body=('Dear customer, your account will be suspended today. '
              'Click http://secure-update.example/verify and confirm your '
              'password within 24 hours or funds will be frozen.'),
        sender='billing@secure-update.example',
    )
    print(r)