import os
import requests
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def generate_ai_explanation(subject: str, body_snippet: str, category: str, confidence: float):
    """
    Generate a natural-language explanation for an email classification
    using NVIDIA NIM (OpenAI-compatible API).

    Args:
        subject: Email subject line
        body_snippet: Plain-text email body (or snippet)
        category: Predicted category (phishing, malware, spam, etc.)
        confidence: Classification confidence (0-100)

    Returns:
        Generated explanation string, or None on failure.
    """
    base_url = os.environ.get('NVIDIA_NIM_BASE_URL', '').rstrip('/')
    api_key = os.environ.get('NVIDIA_NIM_API_KEY', '')
    model = os.environ.get('NVIDIA_NIM_MODEL', '')

    if not all([base_url, api_key, model]):
        logger.error('NVIDIA NIM configuration incomplete: missing BASE_URL, API_KEY, or MODEL')
        return None

    body_truncated = (body_snippet or '')[:500]

    system_prompt = (
        "You are a security email analyst. You write brief, natural "
        "explanations of email classifications. Output ONLY the explanation "
        "itself — no meta-commentary, instructions, or framing.\n\n"
        "Example:\n"
        "The email adopts Google's official 'Security alert' subject line and "
        "includes an image of the Google logo, signaling an authentic brand "
        "source. It specifically mentions an app gaining access to account "
        "data and provides a clickable link that leads to a genuine "
        "Google account URL."
    )

    user_prompt = (
        f"Classified as {category} with {confidence:.0f}% confidence.\n"
        f"Subject: {subject}\n"
        f"Body: {body_truncated}\n\n"
        f"Explanation:"
    )

    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 200
            },
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        choice = data['choices'][0]['message']['content']
        return choice.strip() if choice else None
    except requests.Timeout:
        logger.warning('AI explanation timed out after 15s')
        return None
    except Exception as e:
        logger.error(f'AI explanation failed: {e}')
        return None
