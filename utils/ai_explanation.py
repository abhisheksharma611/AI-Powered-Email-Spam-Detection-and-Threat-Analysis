import os
import re
import requests
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Local Ollama (active — gemma2:2b only) ─────────────────────────────
OLLAMA_BASE_URL = os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434').rstrip('/')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'gemma2:2b')
OLLAMA_TIMEOUT = int(os.environ.get('OLLAMA_TIMEOUT', '90'))
OLLAMA_NUM_CTX = int(os.environ.get('OLLAMA_NUM_CTX', '4096'))
# Target output: about 70 words (verdict + URL check + advice).
# num_predict is a hard ceiling; the prompt aims near 70, _cap_words pins it.
OLLAMA_NUM_PREDICT = int(os.environ.get('OLLAMA_NUM_PREDICT', '100'))
# Model stays in VRAM only 5 min after last explanation (no background run).
OLLAMA_KEEP_ALIVE = os.environ.get('OLLAMA_KEEP_ALIVE', '5m')

_SYSTEM_PROMPT = (
    "You are a spam-filter explainer. Write about 70 words in 3 short "
    "sentences, plain words a non-technical user understands. Sentence 1: "
    "the verdict and confidence. Sentence 2: judge the listed link domains "
    "— you MUST write each domain name and say if it matches the claimed "
    "sender or looks fake (example: fake-bank-login.tk belongs to no real "
    "bank). Sentence 3: what the user should do. Safe mail means no "
    "action needed. Never repeat a fact, never paste full URLs, no generic "
    "warnings. Output only the explanation."
)

_URL_RE = re.compile(r'https?://\S+|www\.\S+|\S+\.(com|net|org|io|tk|ru|xyz|top|info|biz)\S*', re.IGNORECASE)


def _extract_domains(text: str) -> list:
    """Clean domain names from raw URLs (no tracking junk passed to model)."""
    from urllib.parse import urlparse
    domains = []
    for m in _URL_RE.finditer(text or ''):
        raw = m.group(0)
        url = raw if raw.startswith('http') else 'http://' + raw
        try:
            host = urlparse(url).hostname or ''
        except Exception:
            host = ''
        host = host.lower().lstrip('www.')
        if host and host not in domains:
            domains.append(host)
    return domains[:5]


def _strip_urls(text: str) -> str:
    """Delete URLs (incl. bare tracking domains) so model can't quote them."""
    text = _URL_RE.sub('', text)
    return re.sub(r'\s+', ' ', text).strip()


def _inline_domains(text: str) -> str:
    """Replace each raw URL with '(link to domain)' — model sees clean names."""
    from urllib.parse import urlparse

    def _rep(m):
        raw = m.group(0)
        url = raw if raw.startswith('http') else 'http://' + raw
        try:
            host = (urlparse(url).hostname or '').lower().lstrip('www.')
        except Exception:
            host = ''
        return f'(link to {host})' if host else '(link)'

    return re.sub(r'\s+', ' ', _URL_RE.sub(_rep, text or '')).strip()


_FULL_URL_RE = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)


def _clean_output(text: str) -> str:
    """Remove full URLs/tracking junk but KEEP bare domain names for user."""
    text = text.replace('[link]', '').strip()
    text = _FULL_URL_RE.sub('', text)
    return re.sub(r'\s+', ' ', text).strip()


def _cap_words(text: str, limit: int = 70) -> str:
    """Cut to last full sentence within `limit` words — never mid-sentence."""
    text = text.replace('[link]', '').strip()
    text = re.sub(r'\s+', ' ', text)
    words = text.split()
    if len(words) <= limit:
        return text
    cut = ' '.join(words[:limit])
    m = list(re.finditer(r'[.!?](?=\s|$)', cut))
    if m:
        return cut[:m[-1].end()].strip()
    return cut.rstrip('.') + '.'


def generate_ai_explanation(subject: str, body_snippet: str, category: str, confidence: float):
    """
    Generate explanation via local Ollama (gemma2:2b only).

    Same signature as before, so app.py needs no change.
    Returns None on failure — caller falls back to rule-based summary
    and retries on next open (self-healing).
    """
    body_truncated = _inline_domains((body_snippet or ''))[:1500]
    subject_clean = _inline_domains(subject or '')
    domains = _extract_domains(f"{subject or ''} {body_snippet or ''}")
    links_line = (
        f"Link domains in email: {', '.join(domains)}."
        if domains else "No links in email."
    )
    user_prompt = (
        f"Classified as {category} with {confidence:.0f}% confidence. "
        f"Call the category exactly '{category}'.\n"
        f"Subject: {subject_clean}\n"
        f"Body: {body_truncated}\n"
        f"{links_line}\n\n"
        f"Explanation (about 70 words):"
    )
    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "system": _SYSTEM_PROMPT,
                "prompt": user_prompt,
                "stream": False,
                "keep_alive": OLLAMA_KEEP_ALIVE,
                "options": {"temperature": 0.3, "num_ctx": OLLAMA_NUM_CTX, "num_predict": OLLAMA_NUM_PREDICT},
            },
            timeout=OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
        choice = (response.json().get('response') or '').strip()
        if choice:
            think_open = chr(60) + "think" + chr(62)
            think_close = chr(60) + "/" + "think" + chr(62)
            choice = re.sub(
                re.escape(think_open) + ".*?" + re.escape(think_close),
                "", choice, flags=re.DOTALL
            ).strip()
            choice = _cap_words(_clean_output(choice))
        return choice or None
    except requests.Timeout:
        logger.warning('Ollama explanation timed out after %ss', OLLAMA_TIMEOUT)
        return None
    except Exception as e:
        logger.error(f'Ollama explanation failed: {e}')
        return None


# ── NVIDIA NIM (commented for now — uncomment to re-enable) ────────────
# Requires: NVIDIA_NIM_BASE_URL, NVIDIA_NIM_API_KEY, NVIDIA_NIM_MODEL
# NOTE: current key returns 403 "Authorization failed" on every invoke
# (lists models fine) — trial inference entitlement exhausted. Get a fresh
# key at build.nvidia.com before re-enabling.
# def generate_ai_explanation_nim(subject: str, body_snippet: str, category: str, confidence: float):
#     """Same contract as above, via NVIDIA NIM OpenAI-compatible API."""
#     base_url = os.environ.get('NVIDIA_NIM_BASE_URL', '').rstrip('/')
#     api_key = os.environ.get('NVIDIA_NIM_API_KEY', '')
#     model = os.environ.get('NVIDIA_NIM_MODEL', '')
#     if not all([base_url, api_key, model]):
#         logger.error('NVIDIA NIM config incomplete')
#         return None
#     body_truncated = (body_snippet or '')[:500]
#     user_prompt = (
#         f"Classified as {category} with {confidence:.0f}% confidence.\n"
#         f"Subject: {subject}\n"
#         f"Body: {body_truncated}\n\n"
#         f"Explanation:"
#     )
#     try:
#         resp = requests.post(
#             f"{base_url}/chat/completions",
#             headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
#             json={
#                 "model": model,
#                 "messages": [
#                     {"role": "system", "content": _SYSTEM_PROMPT},
#                     {"role": "user", "content": user_prompt},
#                 ],
#                 "temperature": 0.3,
#                 "max_tokens": 200,
#                 **({"chat_template_kwargs": {"enable_thinking": False}}
#                    if "nemotron" in model.lower() else {}),
#             },
#             timeout=15,
#         )
#         resp.raise_for_status()
#         return (resp.json()['choices'][0]['message']['content'] or '').strip() or None
#     except Exception as e:
#         logger.error(f'NIM explanation failed: {e}')
#         return None
