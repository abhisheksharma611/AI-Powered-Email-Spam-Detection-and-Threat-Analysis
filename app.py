import os
import re
import logging
import warnings
import secrets
import threading
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from babel.dates import format_datetime as babel_format_datetime, format_date as babel_format_date, format_time as babel_format_time
from functools import wraps
from markupsafe import escape
from urllib.parse import unquote

from flask import Flask, render_template, session, redirect, url_for, request, jsonify, flash, send_from_directory, make_response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_migrate import Migrate


from config import config
from utils.auth import init_oauth
from utils.gmail_client import GmailClient, prepare_for_model, GmailFetchError
from utils.ai_explanation import generate_ai_explanation
from models.predictor import Predictor
from models.email_model import db, Email, SenderReputation, OAuthStore, ScanSession
from utils.helpers import (
    calculate_urgency, learn_keywords_from_email, apply_adaptive_keyword_boost,
    build_risk_breakdown, generate_explanation_summary, get_matched_learned_keywords,
    sanitize_email_html
)

warnings.filterwarnings('ignore', category=FutureWarning, module='transformers.utils.generic')
warnings.filterwarnings('ignore', category=FutureWarning, module='torch.utils._pytree')

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_INPUT_LENGTH = 5000
# ── Test Text box: how much text is needed before the model is asked to judge ──
#
# Measured on this corpus: the shortest training row is 34 words and NOTHING in
# the 9280 rows is under 25 words, so below that the model has never seen the
# shape of the input. Measured behaviour in that gap:
#     'hi'          -> newsletter 40%
#     'ok'          -> MALWARE 77%   (risk 65, on a two-letter word)
#     '$%^&*()'     -> MALWARE 93%   (risk 78, on symbols)
#     '!!!!!!!!!!'  -> spam 43%      (a red "General spam content" triangle)
#
# A wrong confident verdict is worse than an honest refusal, so /api/analyze_text
# counts alphabetic characters and refuses to classify when there is not enough
# to read. Letters only: spaces, digits, punctuation and emoji do not count.
#
# Verified against the live inbox: 0 of 50 real emails fall at or below 10
# letters, so this cannot affect the Start Analysis path even in principle. It
# is deliberately NOT applied to /api/start_analysis or /bulk_analyze.
MIN_LETTERS_TO_JUDGE = 10
# Below this, the verdict is still returned but the user is told how little text
# the model actually had to work with. 40 letters sits above the corpus minimum
# of 34 words while staying far below a real body, so it only ever fires on the
# genuinely short pastes.
SHORT_INPUT_LETTERS = 40
# ── Inbox thin-input guards (Start Analysis path) ─────────────────────────────
#
# 34 words is the shortest row in all 9280 training rows. Below it the model has
# no training coverage at all, so its class and its confidence are both
# extrapolations. Measured on the live inbox, the shortest real mail was 32
# words (a Tata 1mg promo) and the next was 49, so this fires only on genuinely
# thin input and not on normal mail.
#
# Used for two things, both of which are about not letting a weak read escalate:
#   1. no thin message may contribute a hit to sender reputation
#   2. no thin message may be escalated past Medium risk
THIN_INPUT_WORDS = 34
# Ceiling for a thin read. 60 is the top of the dashboard's Medium band, so a
# message the model barely read can still be flagged for a look but can never
# raise an alarm. Measured: "Hello How are you?" scored risk 72 / High on four
# words.
THIN_INPUT_MAX_RISK = 60
MAX_BATCH_SIZE = 100
# Results paging. Always 50 per page.
#
# There was an adaptive rule here -- 50 up to 200 emails, 100 beyond -- which
# was wrong twice over. It made the page size depend on the FILTERED count, so
# picking a category silently changed how many rows you saw per page, and it
# contradicted the 50-per-page promise. A fixed 50 also means a filtered class
# of 4 renders as one page of 4, which is what the count in the Category
# Distribution strip is promising.
RESULTS_PER_PAGE = 50
DEFAULT_RATE_LIMIT_REQUESTS = 10
DEFAULT_RATE_LIMIT_WINDOW = 60
IST_OFFSET = timezone(timedelta(hours=5, minutes=30))
CATEGORIES_ALL = ['legitimate', 'promotion', 'phishing', 'malware', 'newsletter', 'spam']
# The All / Threats / Safe group. `threat` deliberately excludes promotion and
# newsletter: those are commercial, not malicious, and is_spam treats them as
# threats (a ~76% false rate). Promotion and Newsletter have their own filter
# entries in the category dropdown. Kept as a tuple so the query below can use
# it directly in an IN clause.
THREAT_CATEGORIES = ('phishing', 'malware', 'spam')
VIEW_FILTERS = ('all', 'threat', 'safe')
CHART_LABELS = ['Not Spam', 'Spam', 'Promotion', 'Phishing', 'Malware', 'Newsletter']
CHART_LABELS_ALT = ['Spam', 'Not Spam', 'Promotion', 'Phishing', 'Malware', 'Newsletter']

# ── App Factory ────────────────────────────────────────────────────────────────
app = Flask(__name__)
_flask_env = os.environ.get('FLASK_ENV', 'development')
app.config.from_object(config[_flask_env])

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'

oauth = init_oauth(app)
db.init_app(app)
migrate = Migrate(app, db)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

predictor = Predictor()
if predictor.is_trained:
    logger.info("Predictor initialized successfully!")
else:
    logger.warning("Predictor initialized but no models found. Train models first.")

with app.app_context():
    db.create_all()
    logger.info("Database tables created successfully!")


# ── Utility Helpers ───────────────────────────────────────────────────────────

def get_user_timezone():
    return session.get('timezone', 'Asia/Kolkata')


def get_user_locale():
    return session.get('locale', 'en')


def utc_to_ist(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST_OFFSET)


def ist_now():
    return datetime.now(IST_OFFSET)


def seven_days_ago_local():
    """The 7-day cutoff in the SAME frame as Email.date.

    Email.date is stored as naive local wall-clock time (IST, +5:30), because
    that is what Gmail sends. Comparing it against datetime.utcnow() skews the
    window by 5.5 hours, which silently drops emails from the newest 5.5 hours
    of every "last 7 days" window and lets slightly older ones in. Measured on
    the live data: the utcnow() cutoff returned 24 rows and the local cutoff
    22, and an email dated 00:00 on the boundary day was excluded outright.

    Returns a NAIVE datetime, because Email.date is naive. Do not use
    datetime.utcnow() for anything compared against it.
    """
    return datetime.now(IST_OFFSET).replace(tzinfo=None) - timedelta(days=7)


def format_ist(dt, fmt='%Y-%m-%d %I:%M %p IST'):
    dt_ist = utc_to_ist(dt)
    return dt_ist.strftime(fmt) if dt_ist else ''


def format_in_tz(dt, tz_name, fmt='%Y-%m-%d %I:%M %p %Z'):
    if dt is None:
        return ''
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz_name)).strftime(fmt)


def format_user_tz(dt, fmt='%Y-%m-%d'):
    return format_in_tz(dt, get_user_timezone(), fmt)


def format_user_date(dt):
    if dt is None:
        return ''
    tz_name = get_user_timezone()
    locale_str = get_user_locale()
    try:
        dt_tz = dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz_name))
        return babel_format_datetime(dt_tz, format='medium', locale=locale_str)
    except Exception:
        return format_in_tz(dt, tz_name, '%d-%m-%Y %I:%M %p')


def now_in_tz(tz_name):
    return datetime.now(ZoneInfo(tz_name))


def now_user_tz():
    return now_in_tz(get_user_timezone())


def get_user_email():
    return session.get('user_info', {}).get('email')


def parse_email_date(date_str):
    if not date_str:
        return None
    try:
        parsed = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception:
        return None


def empty_category_counts():
    return {c: 0 for c in CATEGORIES_ALL}


def chart_data_from_counts(counts, labels=None):
    lbl = labels or CHART_LABELS
    # Display labels use 'Not Spam' but the stored counts are keyed 'legitimate'
    label_to_key = {'not spam': 'legitimate'}
    return [int(counts.get(label_to_key.get(l.lower(), l.lower()), 0)) for l in lbl]


def relative_time(dt):
    if dt is None:
        return "N/A"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 60:
        return "Just now"
    if secs < 3600:
        return f"{int(secs / 60)}m ago"
    if secs < 86400:
        return f"{int(secs / 3600)}h ago"
    return f"{int(secs / 86400)}d ago"


def time_greeting():
    h = now_user_tz().hour
    if 5 <= h < 12:
        return "Good Morning", "🌅"
    if 12 <= h < 17:
        return "Good Afternoon", "☀️"
    if 17 <= h < 21:
        return "Good Evening", "🌆"
    return "Good Night", "🌙"


def risk_level_for_score(score):
    if score >= 61:
        return 'High'
    if score >= 41:
        return 'Medium'
    return 'Low'


def risk_badge_color(level):
    return {'Low': 'success', 'Medium': 'warning', 'High': 'danger'}.get(level, 'secondary')


# ── Template Filters ──────────────────────────────────────────────────────────

@app.context_processor
def inject_request():
    return dict(request=request)


@app.template_filter('utc_to_ist')
def utc_to_ist_filter(dt):
    if dt is None:
        return ''
    return format_user_date(dt)


# ── Timezone / Locale Sync ──────────────────────────────────────────────────

@app.before_request
def sync_timezone_from_cookie():
    tz_cookie = request.cookies.get('tz')
    if tz_cookie:
        tz_val = unquote(tz_cookie)
        try:
            ZoneInfo(tz_val)
            if session.get('timezone') != tz_val:
                session['timezone'] = tz_val
        except Exception:
            pass
    locale_cookie = request.cookies.get('locale')
    if locale_cookie:
        loc_val = unquote(locale_cookie).replace('-', '_')
        if session.get('locale') != loc_val:
            session['locale'] = loc_val


# ── Security Headers ─────────────────────────────────────────────────────────

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' "
        "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; style-src 'self' "
        "'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
        "img-src 'self' data: https:; font-src 'self' https://cdnjs.cloudflare.com; "
        "connect-src 'self' https://cdnjs.cloudflare.com"
    )
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


# ── Rate Limiting ─────────────────────────────────────────────────────────────

rate_limit_storage = {}
rate_limit_lock = threading.Lock()


def check_rate_limit(ip, endpoint, max_req=DEFAULT_RATE_LIMIT_REQUESTS, window=DEFAULT_RATE_LIMIT_WINDOW, cooldown=False):
    key = f"{ip}:{endpoint}"
    now = datetime.utcnow()
    with rate_limit_lock:
        if key not in rate_limit_storage:
            rate_limit_storage[key] = []
        cutoff = now - timedelta(seconds=window)
        rate_limit_storage[key] = [t for t in rate_limit_storage[key] if t > cutoff]
        if len(rate_limit_storage[key]) >= max_req:
            reset = (now + timedelta(seconds=window)) if cooldown else (min(rate_limit_storage[key]) + timedelta(seconds=window))
            return False, 0, reset
        rate_limit_storage[key].append(now)
        return True, max_req - len(rate_limit_storage[key]), None


def rate_limit(max_req=10, window=60, cooldown=False):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            ip = request.remote_addr or 'unknown'
            ep = request.endpoint or 'unknown'
            allowed, remaining, reset_time = check_rate_limit(ip, ep, max_req, window, cooldown)
            if not allowed:
                secs = max(int((reset_time - datetime.utcnow()).total_seconds()), 1) if reset_time else window
                resp = jsonify({'error': 'Rate limit exceeded', 'retry_after': secs})
                resp.status_code = 429
                resp.headers['Retry-After'] = str(secs)
                return resp
            resp = f(*args, **kwargs)
            if hasattr(resp, 'headers'):
                resp.headers['X-RateLimit-Remaining'] = str(remaining)
            return resp
        return wrapped
    return decorator


# ── Trusted senders ──────────────────────────────────────────────────────────

# Senders that deliver real infrastructure mail: cloud storage shares, account
# security notices, transactional mail, developer platforms. They are still
# classified by the model, but they are exempt from the reputation penalty,
# because a single bad prediction about Google Drive was enough to blacklist the
# entire sender permanently.
TRUSTED_SENDER_DOMAINS = frozenset({
    'google.com', 'googlemail.com', 'gmail.com', 'googleusercontent.com',
    'microsoft.com', 'microsoftonline.com', 'outlook.com', 'office365.com',
    'live.com', 'hotmail.com', 'sharepoint.com', 'microsoftonline.net',
    'apple.com', 'icloud.com', 'me.com', 'itunes.com',
    'amazon.com', 'amazonaws.com', 'amazon.in',
    'adobe.com', 'atlassian.net', 'slack.com', 'zoom.us', 'dropbox.com',
    'github.com', 'gitlab.com', 'okta.com', 'auth0.com', 'twilio.com',
    'salesforce.com', 'hubspot.com', 'zendesk.com', 'servicenow.com',
    'notion.so', 'asana.com', 'trello.com', 'intercom.io', 'mailchimp.com',
    'sendgrid.net', 'postmarkapp.com', 'stripe.com',
})


def is_trusted_sender(sender_email):
    """True if the sender's domain is known infrastructure mail.

    This suppresses only the *reputation* penalty. It does not override the model
    and it does not suppress the keyword boost, so a genuine phishing message that
    spoofs one of these domains is still scored on its content.
    """
    if not sender_email or '@' not in sender_email:
        return False
    domain = sender_email.rsplit('@', 1)[-1].strip().lower()
    return any(domain == d or domain.endswith('.' + d)
               for d in TRUSTED_SENDER_DOMAINS)


# ── Sender Reputation ─────────────────────────────────────────────────────────

def update_sender_reputation(sender_email, category, urgency_score, db_session=None,
                             model_word_count=None):
    """Fold one message into a sender's reputation.

    `model_word_count` is the length of the string the classifier actually read.
    When it is below THIN_INPUT_WORDS the message is still counted in
    total_emails -- it really did arrive -- but it never contributes a
    phishing/malware/spam hit, because the model had less text than any row in
    its training set and the class it returned is not evidence.

    Measured case this exists for: two personal messages reading "Hi" and
    "Hello How are you?" were classified phishing at 36% and 91% confidence, and
    drove that sender to 2 hits out of 2 messages, reputation_score 100.0. Two
    four-word messages are not a hostile-sender signal.
    """
    session = db_session or db.session
    thin = (model_word_count is not None and model_word_count < THIN_INPUT_WORDS)
    rep = session.query(SenderReputation).filter_by(sender_email=sender_email).first()
    if not rep:
        rep = SenderReputation(
            sender_email=sender_email, reputation_score=0.0, risk_level='Low',
            total_emails=0, phishing_count=0, malware_count=0,
            spam_count=0, high_urgency_count=0,
        )
        session.add(rep)

    if rep.last_seen and rep.total_emails > 0:
        months = (datetime.utcnow() - rep.last_seen).days / 30.0
        # BUG E FIX (part 2): only decay after a real gap. A monthly sender used to be
        # multiplied by 0.95 every message, and int(1 * 0.95) == 0 destroyed every count
        # within ~3 months while total_emails kept growing, so the denominator inflated
        # and the ratio decayed to 0. round() instead of int() also stops 0.4 -> 0.
        if months >= 3:
            decay = max(0.5, 1.0 - 0.05 * months)
            rep.phishing_count = max(0, round(rep.phishing_count * decay))
            rep.malware_count = max(0, round(rep.malware_count * decay))
            rep.spam_count = max(0, round(rep.spam_count * decay))
            rep.high_urgency_count = max(0, round(rep.high_urgency_count * decay))

    rep.total_emails += 1
    if thin:
        # Counted above as delivered, not counted below as hostile.
        pass
    elif category == 'phishing':
        rep.phishing_count += 1
    elif category == 'malware':
        rep.malware_count += 1
    elif category == 'spam':
        rep.spam_count += 1
    if urgency_score >= 70 and not thin:
        rep.high_urgency_count += 1
    rep.last_seen = datetime.utcnow()

    raw = rep.phishing_count * 4 + rep.malware_count * 4 + rep.spam_count * 2 + rep.high_urgency_count
    max_possible = rep.total_emails * 4
    rep.reputation_score = min((raw / max_possible) * 100, 100.0) if max_possible > 0 else 0

    # BUG E FIX (part 1): the old gate was `total_emails >= 3`, so ONE misclassified
    # email in three gave raw=4, max=12, score=33 -> 'Medium' -> a permanent +10 risk
    # on every future email from that sender. That is how real senders such as
    # care@emaila.1mg.com and info@connect.netmeds.com got flagged.
    # Require BOTH a minimum sample and a minimum absolute hit count, so a single
    # misfire can never cross a threshold and a small sample can never look bad.
    MIN_SAMPLES = 10
    MIN_ABS_HITS = 2
    abs_hits = rep.phishing_count + rep.malware_count + rep.spam_count

    if rep.total_emails >= MIN_SAMPLES and abs_hits >= MIN_ABS_HITS:
        if rep.reputation_score < 20:
            rep.risk_level = 'Low'
        elif rep.reputation_score < 50:
            rep.risk_level = 'Medium'
        else:
            rep.risk_level = 'High'
    else:
        rep.risk_level = 'Low'

    return rep.reputation_score, rep.risk_level


def prune_deleted_emails(gmail_client, user_email, session=None):
    """Drop stored rows for mail the user has since deleted.

    THE OBVIOUS VERSION IS WRONG, and the reason matters. The obvious
    implementation deletes every stored row whose id is not in the current
    fetch. Both scan paths call get_recent_emails(max_results=50), so "absent
    from the fetch" overwhelmingly means "older than the newest 50", not
    "deleted" -- that version would silently destroy the user's entire
    history on the first scan that ran.

    So instead of inferring deletion from absence, ask Gmail directly which of
    the ids we actually hold are in Trash, and delete only those. An id that
    Gmail confirms is in Trash is proof; an id we did not fetch is not.

    Returns the number of rows removed. Never raises: a failure here must not
    take down a scan that otherwise succeeded.
    """
    sess = session or db.session
    try:
        trashed = gmail_client.get_trashed_ids()
        # None means the question could not be answered (auth error, network).
        # Pruning on that would be acting on ignorance -- skip entirely.
        if trashed is None:
            logger.warning("Skipping prune: could not list trashed ids from Gmail")
            return 0

        stored_ids = {row[0] for row in sess.query(Email.id)
                      .filter_by(user_email=user_email).all()}
        if not stored_ids:
            return 0

        # Intersect with what we store, so a large Trash cannot widen the delete.
        doomed = stored_ids & trashed
        if not doomed:
            return 0

        removed = 0
        for row in sess.query(Email).filter(
                Email.user_email == user_email,
                Email.id.in_(doomed)).all():
            sess.delete(row)
            removed += 1
        sess.commit()
        logger.info("Pruned %d deleted email(s) no longer in the mailbox for %s",
                    removed, user_email)
        return removed
    except Exception as e:
        sess.rollback()
        logger.warning("Prune of deleted emails failed, keeping stored rows: %s", e)
        return 0


# ── Analysis Service (shared by sync + async) ─────────────────────────────────

def _prepare_email_data(emails):
    """Build the model input and the per-email metadata.

    The string handed to the classifier goes through prepare_for_model(), which is
    the same function models/roberta_train.py and models/evaluate.py apply to
    every training row. One definition, so there is no train/serve gap to reason
    about.

    200 words, not the previous 130. The corpus maximum is 137 words and the
    training cap is 150, so 130 was sitting BELOW the training distribution and
    was truncating real body text that the model had been trained to read in
    full. A real Gmail body is 300-600 words; uncapped, it truncates down to its
    unsubscribe footer and classifies worse than the snippet did. 200 is past
    where any body text stops being the message.

    metadata['body'] keeps the FULL uncapped text so the stored record and the UI
    still see everything.
    """
    texts, metadata = [], []
    for i, em in enumerate(emails):
        full_body = em.get('body', '') or em.get('snippet', '')
        subject = em.get('subject', '') or ''
        model_text = prepare_for_model(f"{subject} {full_body}".strip())
        texts.append(model_text)
        metadata.append({
            'index': i,
            'id': em.get('id', f'email_{i}'),
            'subject': subject or 'No Subject',
            'sender': em.get('sender', 'Unknown'),
            'date': em.get('date', ''),
            'date_obj': parse_email_date(em.get('date', '')),
            'snippet': em.get('snippet', ''),
            'body': full_body,
            'label_ids': em.get('label_ids') or [],
            # The exact string the classifier saw, kept so the thin-input
            # guards below measure the model's input rather than re-deriving a
            # different one from the full body. `body` is uncapped; this is not.
            'model_text': model_text,
        })
    return texts, metadata


def _process_single_email(metadata, prediction, user_email, db_session=None,
                          scan_id=None):
    sess = db_session or db.session
    cat = prediction['category']
    risk_score = prediction['risk_score']
    is_spam = prediction['is_spam']
    risk_level = prediction['risk_level']
    confidence = prediction['confidence']
    category_info = predictor.get_category_info(cat)

    # How much text the classifier actually read. Used by both thin-input
    # guards below. Falls back to the full body if a caller did not supply
    # model_text, so the guards can never silently switch off.
    model_text = metadata.get('model_text')
    if model_text is None:
        model_text = prepare_for_model(
            f"{metadata.get('subject', '')} {metadata.get('body', metadata.get('snippet', ''))}".strip())
    model_word_count = len(model_text.split())
    thin_input = model_word_count < THIN_INPUT_WORDS

    urgency = calculate_urgency(metadata['subject'], metadata.get('body', metadata['snippet']), metadata['sender'])

    try:
        record = Email.get_or_create(sess, metadata['id'], user_email)
        # Cache invalidation: if a re-scan changed the classification, the
        # cached AI explanation no longer matches — drop it so the next
        # open regenerates it from the fresh prediction.
        if record.ai_explanation and record.category and record.category != cat:
            record.ai_explanation = None
            record.ai_explanation_generated_at = None
        record.subject = str(metadata['subject'])[:500]
        record.sender = str(metadata['sender'])[:255]
        # The body was never written. Every stored row had an empty `body` column
        # even when the fetch produced one, which made it impossible to audit why
        # a message had been classified a certain way. Full text, not the capped
        # model input, so the UI and any review can still read the whole message.
        record.body = str(metadata.get('body') or '')[:20000]
        record.snippet = str(metadata['snippet'])[:1000]
        record.date = metadata.get('date_obj')
        # Persist the labels so a stored row records which mailbox it came from.
        _labels = metadata.get('label_ids') or []
        record.label_ids = ','.join(str(x) for x in _labels[:20])[:255] if _labels else None
        # Stamped every scan, so a re-scan of the same message moves it into the
        # new scope rather than leaving it visible in both.
        record.scan_id = scan_id
        record.category = cat
        record.is_spam = is_spam
        record.risk_score = risk_score
        record.risk_level = risk_level
        record.confidence = confidence
        record.urgency_score = urgency['urgency_score']
        record.urgency_level = urgency['urgency_level']
        record.updated_at = datetime.utcnow()
        record.risk_breakdown = {}
        record.explanation_summary = ""

        record.risk_score = apply_adaptive_keyword_boost(
            metadata['subject'], metadata.get('body', metadata['snippet']), record.risk_score, sess
        )

        sender_risk = 'Low'
        if metadata['sender']:
            _, sender_risk = update_sender_reputation(
                metadata['sender'], cat, urgency['urgency_score'], sess,
                model_word_count=model_word_count)
            # TRUSTED_SENDER: a confirmed legitimate infrastructure sender never gets
            # a reputation penalty. drive-shares-dm-noreply@google.com reached
            # reputation 100.0 / High, which added +20 to every subsequent Drive
            # share even though the model itself was only ~74% confident.
            if is_trusted_sender(metadata['sender']):
                sender_risk = 'Low'
        record.risk_level = risk_level_for_score(record.risk_score)

        # BOOST STACK CAP. The two boosts below used to be applied independently and
        # then summed, so a model that was only 63% certain could be pushed to 100:
        #     "Share request for DS.pdf" -> base 63, keyword +20, sender +20, urgency +20
        # The boosts are now combined into a single penalty with its own ceiling, and
        # the total is clamped so the result stays meaningful.
        matched_kw = get_matched_learned_keywords(metadata['subject'], metadata.get('body', metadata['snippet']), sess)
        flags = []
        base_score = risk_score
        ml_score = int(confidence)
        urg_boost = min(urgency['urgency_score'], 30)
        kw_boost = min(record.risk_score - risk_score, 20)
        snd_boost = 0

        if confidence > 80:
            flags.append('ml_high_confidence')
        if urgency['urgency_level'] == 'High':
            flags.append('urgency_detected')
        if sender_risk in ['Medium', 'High']:
            flags.append('historical_sender_risk')
            snd_boost = 10 if sender_risk == 'Medium' else 20
        if matched_kw:
            flags.append('learned_keyword_match')

        # Combined ceiling: at most 20 points of hand-written heuristics, whatever
        # the individual sources claim. Previously 30-60 points were possible.
        COMBINED_BOOST_CAP = 20
        combined = min(kw_boost + snd_boost + urg_boost, COMBINED_BOOST_CAP)
        record.risk_score = min(base_score + combined, 100)

        # THIN INPUT CLAMP. Applied last, after the boosts, so nothing can push
        # a weak read past Medium.
        #
        # The model's own base_risk for phishing is 80 and for malware 85, so a
        # confident read scales straight into the High band. On four words of
        # text that number is an extrapolation, not a measurement. Measured:
        # "Hello How are you?" -> phishing 0.91 -> risk 72 / High, and the
        # sender was then recorded as a confirmed phisher.
        #
        # This does NOT refuse to classify and does NOT hide the email. The
        # category is still stored for the audit trail, the risk is still shown,
        # it just cannot raise an alarm on text the model has never seen the
        # length of.
        thin_input_capped = False
        if thin_input and record.risk_score > THIN_INPUT_MAX_RISK:
            thin_input_capped = True
            record.risk_score = THIN_INPUT_MAX_RISK
            # Zeroing `combined` is what keeps the breakdown honest: the block
            # below derives all three components from it, so they come out at
            # zero rather than claiming boosts that no longer applied.
            combined = 0
        # Report the capped total in the breakdown, not the individual raw values,
        # so the breakdown explains the number that was actually stored.
        breakdown_kw = combined if kw_boost else 0
        breakdown_snd = 0
        breakdown_urg = 0
        if not breakdown_kw:
            breakdown_snd = combined if snd_boost else 0
            if not breakdown_snd:
                breakdown_urg = combined
        record.risk_level = risk_level_for_score(record.risk_score)

        if thin_input_capped:
            # Flag before the breakdown is built so the flag appears in it. The
            # human-readable summary is written after generate_explanation_summary
            # below, which would otherwise overwrite anything set here.
            flags.append('thin_input_risk_capped')

        record.risk_breakdown = build_risk_breakdown(
            base_score, ml_score, breakdown_urg, breakdown_kw, breakdown_snd,
            matched_kw, flags
        )
        record.explanation_summary = generate_explanation_summary(
            record.risk_breakdown, cat, urgency['urgency_level'],
            metadata['subject'], metadata.get('body', metadata['snippet']), confidence
        )
        if thin_input_capped:
            # Say why the number is lower than the model's own read. Without this
            # the stored risk simply disagrees with the category and nothing on
            # screen explains the gap.
            record.explanation_summary = (
                'This message is only %d words, which is shorter than anything '
                'the model was trained on, so its risk was capped at Medium '
                'instead of being escalated to High. The category shown is the '
                'model\'s read, not a confirmed judgement.'
                % model_word_count)

        if cat in ['phishing', 'malware'] and not thin_input:
            # Gated for the same reason as the reputation counter. A four-word
            # message classified phishing would otherwise teach the system that
            # its words are phishing vocabulary, and those words then boost every
            # future message that contains them. "Hello How are you?" is enough
            # to poison a common-word list.
            cnt = learn_keywords_from_email(metadata['subject'], metadata.get('body', metadata['snippet']), cat, sess, confidence)
            if cnt > 0:
                logger.info(f"Learned {cnt} keywords from {cat} email")
        elif cat in ['phishing', 'malware']:
            logger.info(
                f"Skipped keyword learning: {model_word_count} words is below "
                f"THIN_INPUT_WORDS={THIN_INPUT_WORDS}")

        return {
            'id': metadata['id'], 'subject': metadata['subject'], 'sender': metadata['sender'],
            'date': metadata['date'], 'snippet': metadata['snippet'], 'category': cat,
            'category_info': category_info, 'is_spam': is_spam,
            'risk_score': record.risk_score, 'risk_level': record.risk_level,
            'confidence': confidence, 'urgency_score': urgency['urgency_score'],
            'urgency_level': urgency['urgency_level'], 'risk_breakdown': record.risk_breakdown,
            'explanation_summary': record.ai_explanation or record.explanation_summary,
            'spam_probability': confidence if is_spam else max(0, 100 - confidence),
        }
    except Exception as e:
        logger.error(
            f"DB error processing email #{metadata.get('index', '?') + 1} "
            f"subject={metadata.get('subject')!r} sender={metadata.get('sender')!r}: {e}"
        )
        sess.rollback()
        return None


def _ensure_refresh_token(token, user_email):
    if not token.get('refresh_token'):
        store = OAuthStore.query.get(user_email)
        if store and store.refresh_token:
            token['refresh_token'] = store.refresh_token
    return token


# ── Scan scope (date period + how many) ───────────────────────────────────────
# One resolver for both scan paths, so the Start Analysis button and the
# /analyze_emails route can never drift apart on what "the last 7 days, 25
# emails" means.
SCAN_PERIODS = ('7d', '30d', 'month', 'custom')
# No user-facing email count any more. The modal used to offer 10/25/50 and the
# server rejected anything outside that set, which meant "Last 7 days" meant
# "at most 50 of the last 7 days" -- a cap the period label never mentioned.
# Every period now takes everything in its window. This is the runaway guard for
# "all of 2019", not a limit the user chooses.
SCAN_UNCAPPED_MAX = 500
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def build_scan_query(period='30d', from_date=None, to_date=None):
    """Turn a period choice into a Gmail search string.

    '-in:trash' is always first and is not optional: the Trash fix must not be
    bypassable by choosing a period, or deleted mail would come back the moment
    a user picked 'Custom'.

    Presets use Gmail's own relative operators rather than dates computed in
    Python. That removes any clock-skew question and makes 'this month' genuinely
    calendar-aware instead of a synonym for 'last 30 days'.

    Custom uses before:<to>+1 day, because Gmail's before: is EXCLUSIVE. Passing
    the user's chosen end date unmodified silently drops messages sent on the
    final day they selected, which is the day people most want to see.
    """
    q = '-in:trash'

    if period == '7d':
        q += ' newer_than:7d'
    elif period == '30d':
        q += ' newer_than:30d'
    elif period == 'month':
        q += ' after:%d/%d/01' % (datetime.utcnow().year, datetime.utcnow().month)
    elif period == 'custom':
        if from_date:
            q += ' after:%s' % from_date
        if to_date:
            try:
                end = datetime.strptime(to_date, '%Y-%m-%d') + timedelta(days=1)
                q += ' before:%s' % end.strftime('%Y/%m/%d')
            except ValueError:
                q += ' before:%s' % to_date
    return q


def validate_scan_scope(data):
    """Return (period, from_date, to_date, max_count, error_message).

    Rejects rather than silently defaulting. A scan that quietly analysed the
    wrong window is worse than one that says so.

    Order matters and was wrong the first time: the count check used to run
    BEFORE the custom branch, so a client sending max_count: null for a custom
    range was resolved to SCAN_UNCAPPED_MAX and then rejected because 500 is not
    one of 10/25/50 -- every custom range 400'd.
    """
    period = (data or {}).get('period', '30d')
    from_date = (data or {}).get('from_date') or None
    to_date = (data or {}).get('to_date') or None

    if period not in SCAN_PERIODS:
        return None, None, None, None, 'Unknown period.'

    if period == 'custom':
        for label, val in (('From', from_date), ('To', to_date)):
            if not val:
                return None, None, None, None, '%s date is required for a custom range.' % label
            if not _DATE_RE.match(val):
                return None, None, None, None, '%s date must look like 2026-09-01.' % label
        if from_date > to_date:
            return None, None, None, None, 'The From date is after the To date.'

    # Every period is uncapped now: the modal no longer offers a count, so the
    # scan always takes every email in the chosen window. SCAN_UNCAPPED_MAX is
    # the runaway guard for "all of 2019", not a user-facing limit.
    return period, from_date, to_date, SCAN_UNCAPPED_MAX, None


def describe_empty_scan(period, from_date=None, to_date=None):
    """Explain an empty result in terms of what the user actually asked for.

    "Inbox appears empty" was wrong twice over: the scan is usually filtered,
    and it does not read the whole inbox. Someone who picks a two-day range and
    sees that message goes looking for missing mail rather than a narrow window.
    """
    if period == 'custom' and from_date and to_date:
        return ('No emails between %s and %s. Deleted mail is never included, so '
                'a narrow range can come back empty. Try a wider range.'
                % (from_date, to_date))
    labels = {'7d': 'the last 7 days', '30d': 'the last 30 days',
              'month': 'this month'}
    window = labels.get(period, 'that period')
    return ('No emails in %s. Deleted mail is never included, so a short '
            'period can come back empty. Try a wider period.' % window)


def run_analysis(user_email, oauth_token, progress_callback=None,
                 period='30d', from_date=None, to_date=None, max_count=50,
                 scan_id=None):
    oauth_token = _ensure_refresh_token(oauth_token, user_email)
    gmail_client = GmailClient(oauth_token)
    # No blanket except here on purpose: GmailFetchError must reach the caller,
    # because this path returned "No emails returned from inbox" for an expired
    # token as well as for a real empty result.
    emails = gmail_client.get_recent_emails(
        max_results=max_count, query=build_scan_query(period, from_date, to_date))

    if not emails:
        return None, describe_empty_scan(period, from_date, to_date)

    # After the empty check on purpose: a Gmail failure returns [] and is
    # indistinguishable from an empty mailbox, so pruning on that path would
    # treat a network blip as a mass deletion.
    prune_deleted_emails(gmail_client, user_email)

    if progress_callback:
        progress_callback(15, f'Fetched {len(emails)} emails. Starting AI analysis...')

    texts, metadata = _prepare_email_data(emails)
    results = []
    cat_counts = empty_category_counts()
    total = len(texts)
    passed = 0
    failed = 0
    logger.info(f"[ANALYSIS] Analyzing {total} emails for {user_email}")
    # 10 -> 32. This does NOT reduce GPU work: predict_batch re-slices whatever
    # it is handed into 16-item chunks, so the number of forward passes is the
    # same either way. What it does reduce is the number of Python round-trips
    # into predict_batch, and with it the number of times the TF-IDF vectoriser
    # re-transforms a slice. Small, real, free.
    BATCH = 32

    for batch_start in range(0, len(texts), BATCH):
        batch_texts = texts[batch_start:batch_start + BATCH]
        batch_meta = metadata[batch_start:batch_start + BATCH]

        try:
            batch_preds = predictor.predict_batch(batch_texts)
        except Exception:
            batch_preds = [predictor.predict_single(t) for t in batch_texts]

        for j, meta in enumerate(batch_meta):
            pred = batch_preds[j]
            i = meta['index']
            if progress_callback:
                pct = 15 + int((i / len(texts)) * 75)
                progress_callback(pct, f'Analyzing email {i+1} of {len(texts)}: {meta["subject"][:40]}...')

            result = _process_single_email(meta, pred, user_email, scan_id=scan_id)
            n = i + 1
            if result:
                results.append(result)
                cat_counts[result['category']] += 1
                passed += 1
                logger.info(f"[ANALYSIS] {n} out of {total} — PASS — {result['category']}")
            else:
                failed += 1
                logger.error(f"[ANALYSIS] {n} out of {total} — FAIL — {pred.get('category', 'unknown')}")
                urg = calculate_urgency(meta['subject'], meta.get('body', meta['snippet']), meta['sender'])
                results.append({
                    'id': meta['id'], 'subject': meta['subject'], 'sender': meta['sender'],
                    'date': meta['date'], 'snippet': meta['snippet'],
                    'category': 'legitimate', 'category_info': predictor.get_category_info('legitimate'),
                    'is_spam': False, 'risk_score': 5, 'risk_level': 'Low',
                    'confidence': 50, 'urgency_score': urg['urgency_score'],
                    'urgency_level': urg['urgency_level'], 'spam_probability': 10,
                })
                cat_counts['legitimate'] += 1

    try:
        db.session.commit()
    except Exception as e:
        logger.error(f"Commit failed: {e}")
        db.session.rollback()

    if progress_callback:
        # The message must not claim success it did not have. `results` contains
        # a fallback dict for every email whose database write failed, so
        # len(results) equals the input count even when nothing was stored --
        # which is how a scan that silently saved nothing still reported
        # "Processed 50 emails".
        if failed:
            progress_callback(
                100,
                'Analysis finished with problems: %d of %d could not be saved '
                '(%d stored). See the server log for the database error.'
                % (failed, total, passed))
        else:
            progress_callback(100, f'Analysis complete! Processed {len(results)} emails.')

    logger.info(f"[ANALYSIS] Complete — {passed} passed, {failed} failed, {total} total")

    return results, None


# ── Async Task Tracking ───────────────────────────────────────────────────────

analysis_tasks = {}
analysis_tasks_lock = threading.Lock()


def _update_task(task_id, progress, status, complete=False, error=None):
    with analysis_tasks_lock:
        if task_id in analysis_tasks:
            analysis_tasks[task_id].update({'progress': progress, 'status': status, 'complete': complete})
            if error:
                analysis_tasks[task_id]['error'] = error


def _run_analysis_bg(task_id, oauth_token, user_email, flask_app,
                     period='30d', from_date=None, to_date=None, max_count=50,
                     scan_id=None):
    class _Cancelled(Exception):
        pass

    def check_cancelled():
        with analysis_tasks_lock:
            return analysis_tasks.get(task_id, {}).get('cancelled', False)

    def progress(pct, msg):
        if check_cancelled():
            raise _Cancelled
        _update_task(task_id, pct, msg)

    try:
        _update_task(task_id, 5, 'Connecting to Gmail...')
        try:
            with flask_app.app_context():
                oauth_token = _ensure_refresh_token(oauth_token, user_email)
            gmail_client = GmailClient(oauth_token)
        except Exception as e:
            _update_task(task_id, 0, 'Failed to connect to Gmail', complete=True, error=str(e))
            return

        _update_task(task_id, 10, 'Fetching emails from inbox...')
        try:
            emails = gmail_client.get_recent_emails(
                max_results=max_count, query=build_scan_query(period, from_date, to_date))
        except GmailFetchError as e:
            err = str(e).lower()
            if 'network' in err or 'connection' in err or 'timeout' in err:
                msg = 'Network error - could not reach Gmail. Check your connection.'
            elif 'auth' in err or 'credential' in err or 'token' in err or 'permission' in err:
                msg = 'Gmail rejected the request - your login has expired. Please log out and log in again.'
            else:
                msg = f'Could not fetch emails from Gmail: {e}'
            _update_task(task_id, 0, msg, complete=True, error=msg)
            return

        if not emails:
            # A query that matched nothing is NOT an empty inbox, and saying so
            # sends the user hunting for a problem they do not have. Name the
            # window that came back empty.
            msg = describe_empty_scan(period, from_date, to_date)
            _update_task(task_id, 0, 'No emails found', complete=True, error=msg)
            return

        # Same placement as the sync path: after the empty check, so a failed
        # fetch can never be read as "the user deleted everything".
        with flask_app.app_context():
            prune_deleted_emails(gmail_client, user_email)

        _update_task(task_id, 15, f'Fetched {len(emails)} emails. Starting AI analysis...')

        texts, metadata_list = _prepare_email_data(emails)
        results = []
        cat_counts = empty_category_counts()
        total = len(texts)
        passed = 0
        failed = 0
        logger.info(f"[ANALYSIS] Analyzing {total} emails for {user_email}")
        BATCH = 32   # matches the sync path; see the note there

        with flask_app.app_context():
            for bs in range(0, len(texts), BATCH):
                if check_cancelled():
                    _update_task(task_id, 0, 'Cancelled by user', complete=True)
                    return

                bt = texts[bs:bs + BATCH]
                bm = metadata_list[bs:bs + BATCH]
                try:
                    bp = predictor.predict_batch(bt)
                except Exception:
                    bp = [predictor.predict_single(t) for t in bt]

                for j, meta in enumerate(bm):
                    i = meta['index']
                    if check_cancelled():
                        _update_task(task_id, 0, 'Cancelled by user', complete=True)
                        return
                    pct = 15 + int((i / len(texts)) * 75)
                    _update_task(task_id, pct, f'Analyzing email {i+1}/{len(texts)}: {meta["subject"][:40]}...')

                    result = _process_single_email(meta, bp[j], user_email, db.session, scan_id=scan_id)
                    n = i + 1
                    if result:
                        results.append(result)
                        cat_counts[result['category']] += 1
                        passed += 1
                        logger.info(f"[ANALYSIS] {n} out of {total} — PASS — {result['category']}")
                    else:
                        failed += 1
                        logger.error(f"[ANALYSIS] {n} out of {total} — FAIL — {bp[j].get('category', 'unknown')}")
                        cat_counts['legitimate'] += 1

            if check_cancelled():
                _update_task(task_id, 0, 'Cancelled by user', complete=True)
                return

            try:
                db.session.commit()
            except Exception as e:
                logger.error(f"BG commit failed: {e}")
                db.session.rollback()

        if failed:
            _update_task(task_id, 100,
                         'Analysis finished with problems: %d of %d could not be '
                         'saved (%d stored). Check the server log.'
                         % (failed, total, passed),
                         complete=True)
        else:
            _update_task(task_id, 100, f'Analysis complete! Processed {len(emails)} emails.', complete=True)
        logger.info(f"[ANALYSIS] Complete — {passed} passed, {failed} failed, {total} total")

    except _Cancelled:
        _update_task(task_id, 0, 'Cancelled by user', complete=True)
    except Exception as e:
        logger.error(f"BG task {task_id} failed: {e}")
        _update_task(task_id, 0, 'Analysis failed', complete=True, error=str(e))


# ── User Model ─────────────────────────────────────────────────────────────────

class User(UserMixin):
    def __init__(self, info):
        self.id = info['email']
        self.email = info['email']
        self.name = info['name']
        self.picture = info.get('picture', '')


@login_manager.user_loader
def load_user(user_id):
    info = session.get('user_info')
    if info and info['email'] == user_id:
        return User(info)
    return None


# ── Auth Routes ───────────────────────────────────────────────────────────────

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static', 'img'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')


@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')


@app.route('/login')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('login.html')


@app.route('/auth/<provider>')
def oauth_login(provider):
    if provider != 'google':
        flash('Invalid provider', 'error')
        return redirect(url_for('login'))
    try:
        nonce = secrets.token_urlsafe(32)
        session['oauth_nonce'] = nonce
        client = oauth.create_client('google')
        scheme = 'https' if request.is_secure or request.headers.get('X-Forwarded-Proto') == 'https' else 'http'
        host = request.headers.get('X-Forwarded-Host') or request.host
        return client.authorize_redirect(f"{scheme}://{host}/callback/{provider}", nonce=nonce, access_type='offline')
    except Exception as e:
        logger.error(f"OAuth error: {e}")
        flash('Unable to connect to Google. Please try again.', 'error')
        return redirect(url_for('login'))


@app.route('/callback/<provider>')
def oauth_callback(provider):
    if provider != 'google':
        flash('Invalid provider', 'error')
        return redirect(url_for('login'))
    try:
        client = oauth.create_client('google')
        token = client.authorize_access_token()
        cfg_app = config[_flask_env]
        token['client_id'] = cfg_app.GOOGLE_CLIENT_ID
        token['client_secret'] = cfg_app.GOOGLE_CLIENT_SECRET
        token['token_uri'] = 'https://oauth2.googleapis.com/token'
        resp = client.get('https://www.googleapis.com/oauth2/v2/userinfo', token=token)
        resp.raise_for_status()
        user_info = resp.json()
        if user_info and user_info.get('email'):
            email_addr = user_info['email']
            rt = token.get('refresh_token')
            if rt:
                store = OAuthStore.query.get(email_addr)
                if store:
                    store.refresh_token = rt
                else:
                    db.session.add(OAuthStore(user_email=email_addr, refresh_token=rt))
                db.session.commit()
            session['user_info'] = user_info
            session['oauth_token'] = token
            login_user(User(user_info), remember=True)
            flash(f'Welcome, {user_info.get("name", "User")}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Authentication failed.', 'error')
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        flash('Authentication failed. Please try again.', 'error')
    return redirect(url_for('login'))


@app.route('/logout')
@login_required
def logout():
    name = current_user.name if current_user else 'User'
    greeting, emoji = time_greeting()
    hour = now_user_tz().hour
    if 5 <= hour < 12:
        msgs = [f"Goodbye {name}! Have a productive morning!", f"See you later {name}! Enjoy your morning!"]
    elif 12 <= hour < 17:
        msgs = [f"Goodbye {name}! Have a wonderful afternoon!", f"See you {name}! Enjoy the rest of your day!"]
    elif 17 <= hour < 21:
        msgs = [f"Goodbye {name}! Have a relaxing evening!", f"See you {name}! Enjoy your evening!"]
    else:
        msgs = [f"Goodnight {name}! Sweet dreams!", f"Goodbye {name}! Have a restful night!"]

    import random
    logout_user()
    session.clear()
    flash(random.choice(msgs), 'info')
    return redirect(url_for('index'))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    greeting, emoji = time_greeting()
    now = now_user_tz()
    locale_str = get_user_locale()
    try:
        current_time = babel_format_time(now, format='short', locale=locale_str)
        current_date = babel_format_date(now, format='full', locale=locale_str)
    except Exception:
        current_time = now.strftime('%I:%M %p')
        current_date = now.strftime('%A, %B %d, %Y')
    return render_template('dashboard.html', user=current_user, greeting=greeting,
                           greeting_emoji=emoji,
                           current_time=current_time, current_date=current_date)


# ── Analytics ─────────────────────────────────────────────────────────────────

@app.route('/analytics')
@login_required
def analytics():
    user_email = get_user_email()
    if not user_email:
        flash('Please log in to view analytics.', 'error')
        return redirect(url_for('login'))

    try:
        # Scoped to the scan the user just ran.
        #
        # This was `filter_by(user_email=...)` with no scan filter and a
        # `.limit(50)`, so Analytics described an arbitrary 50 rows drawn from
        # every scan ever run. On a 500-email scan it showed a slice of 50 and
        # the totals disagreed with the Results page on the same data.
        scan_id, scan_scope = resolve_scan(user_email)
        if not scan_id:
            flash('No analysis results found. Please run the analysis first.', 'info')
            return redirect(url_for('dashboard'))

        recent = (Email.query.filter_by(user_email=user_email, scan_id=scan_id)
                  .order_by(Email.updated_at.desc()).all())
        if not recent:
            flash('No analysis results found for this scan. Please run the analysis first.', 'info')
            return redirect(url_for('dashboard'))

        cat_counts = empty_category_counts()
        for e in recent:
            if e.category in cat_counts:
                cat_counts[e.category] += 1

        urgency_scores = [e.urgency_score for e in recent if e.urgency_score]
        avg_urgency = round(sum(urgency_scores) / len(urgency_scores), 1) if urgency_scores else 0
        high_urgency = sum(1 for e in recent if e.urgency_score and e.urgency_score >= 70)

        # The Top 5 table carried a "Last 7 Days" badge but was built from the
        # whole scan with no date filter, so it showed Aug and Sep rows under a
        # 7-day label. The user asked for the badge to go, but deleting the label
        # while leaving the data unscoped would just hide the bug, so the query
        # is scoped to match what the card claims to show. If the 7-day window
        # has nothing, it says so rather than silently falling back to the whole
        # scan, which is what made the label wrong in the first place.
        seven_ago_top = seven_days_ago_local()
        recent_7d = [e for e in recent if e.date and e.date >= seven_ago_top]
        top5_source = recent_7d if recent_7d else []
        top5 = sorted([e for e in top5_source if e.urgency_score],
                      key=lambda e: e.urgency_score, reverse=True)[:5]
        top5_data = [{
            'id': e.id, 'subject': e.subject, 'sender': e.sender,
            'date': format_user_date(e.date), 'category': e.category,
            'urgency_score': e.urgency_score or 0, 'urgency_level': e.urgency_level,
            'date_utc': (e.date.isoformat() + 'Z') if e.date else '',
        } for e in top5]

        risk_low = sum(1 for e in recent if e.risk_score is not None and 0 <= e.risk_score <= 40)
        risk_med = sum(1 for e in recent if e.risk_score is not None and 41 <= e.risk_score <= 60)
        risk_high = sum(1 for e in recent if e.risk_score is not None and 61 <= e.risk_score <= 100)

        seven_ago = seven_days_ago_local()
        trend_emails = Email.query.filter(Email.user_email == user_email, Email.date >= seven_ago, Email.risk_score.isnot(None)).all()

        today_tz_str = format_user_tz(datetime.utcnow(), '%Y-%m-%d')
        today_tz_dt = datetime.strptime(today_tz_str, '%Y-%m-%d')
        dates7 = [(today_tz_dt - timedelta(days=6-i)).strftime('%Y-%m-%d') for i in range(7)]
        daily_risk = {d: {'total': 0, 'count': 0} for d in dates7}
        for e in trend_emails:
            if e.date:
                dk = format_user_tz(e.date, '%Y-%m-%d')
                if dk in daily_risk:
                    daily_risk[dk]['total'] += e.risk_score
                    daily_risk[dk]['count'] += 1
        risk_trend_values = [round(daily_risk[d]['total'] / daily_risk[d]['count'], 1) if daily_risk[d]['count'] > 0 else 0 for d in dates7]

        raw_risk = cat_counts['phishing'] * 3 + cat_counts['malware'] * 3 + cat_counts['spam'] * 2 + high_urgency
        max_possible = len(recent) * 3
        risk_pct = round((raw_risk / max_possible * 100), 1) if max_possible > 0 else 0
        rlevel = risk_level_for_score(risk_pct)
        rcolor = risk_badge_color(rlevel)

        latest_update = max(e.updated_at for e in recent if e.updated_at)

        data = {
            'total_count': len(recent), 'avg_urgency': avg_urgency, 'high_urgency_count': high_urgency,
            'category_counts': dict(sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)),
            'top_5_high_urgency': top5_data,
        # Dynamic so the card header can state the real window instead of
        # asserting "Last 7 Days" unconditionally.
        'top_urgency_window_label': ('Last 7 Days' if recent_7d
                                     else 'No emails in the last 7 days'),
            'chart_labels': CHART_LABELS_ALT, 'chart_data': chart_data_from_counts(cat_counts, CHART_LABELS_ALT),
            'risk_percentage': int(risk_pct), 'risk_level': rlevel, 'risk_badge_color': rcolor,
            'risk_distribution': {'Low': risk_low, 'Medium': risk_med, 'High': risk_high},
            'risk_trend_dates': dates7, 'risk_trend_values': risk_trend_values,
            'last_scan_time': format_user_date(latest_update),
            'last_scan_time_utc': latest_update.isoformat() + 'Z',
            # Shown beside the Analytics heading so the numbers are attributable
            # to a known window instead of floating free.
            'scope_label': describe_scope(session.get('scan_scope')),
        }
        resp = make_response(render_template('analytics.html', analytics=data))
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return resp
    except Exception as e:
        flash(f'Error loading analytics: {e}', 'error')
        return redirect(url_for('dashboard'))


# ── Threat Console ─────────────────────────────────────────────────────────────

@app.route('/admin/threat-console')
@login_required
def threat_console():
    user_email = get_user_email()
    if not user_email:
        flash('Session expired. Please login again.', 'error')
        return redirect(url_for('login'))

    try:
        seven_ago = seven_days_ago_local()

        today_tz_str = format_user_tz(datetime.utcnow(), '%Y-%m-%d')
        today_tz_dt = datetime.strptime(today_tz_str, '%Y-%m-%d')
        dates7 = [(today_tz_dt - timedelta(days=6-i)).strftime('%Y-%m-%d') for i in range(7)]

        emails_7d = Email.query.filter(Email.user_email == user_email, Email.date >= seven_ago, Email.risk_score.isnot(None)).order_by(Email.date.desc()).all()
        total_emails = len(emails_7d)

        cat_counts = empty_category_counts()
        total_phish = total_mal = 0
        for e in emails_7d:
            if e.category in cat_counts:
                cat_counts[e.category] += 1
                if e.category == 'phishing':
                    total_phish += 1
                elif e.category == 'malware':
                    total_mal += 1

        high_risk = sum(1 for e in emails_7d if e.risk_score and e.risk_score >= 61)
        high_risk_pct = round((high_risk / total_emails * 100), 1) if total_emails > 0 else 0

        activity = sorted(emails_7d, key=lambda e: e.date if e.date else datetime.min, reverse=True)[:30]
        timeline = [{'id': e.id, 'subject': (e.subject or '')[:40] + ('...' if e.subject and len(e.subject) > 40 else ''), 'sender': (e.sender or '')[:30] + ('...' if e.sender and len(e.sender) > 30 else ''), 'category': e.category, 'risk_score': e.risk_score or 0, 'risk_level': e.risk_level, 'urgency_level': e.urgency_level, 'received_at': format_user_date(e.date), 'received_at_relative': relative_time(e.date)} for e in activity]

        risk_dist = {d: {'0-40': 0, '41-60': 0, '61-100': 0} for d in dates7}
        for e in emails_7d:
            dk = format_user_tz(e.date, '%Y-%m-%d') if e.date else None
            if dk in risk_dist and e.risk_score is not None:
                bucket = '0-40' if e.risk_score <= 40 else ('41-60' if e.risk_score <= 60 else '61-100')
                risk_dist[dk][bucket] += 1
        risk_dist_dates = sorted(risk_dist.keys())
        risk_dist_data = {k: [risk_dist[d][k] for d in risk_dist_dates] for k in ['0-40', '41-60', '61-100']}

        urg_trend = {d: {'High': 0, 'Medium': 0, 'Low': 0} for d in dates7}
        for e in emails_7d:
            dk = format_user_tz(e.date, '%Y-%m-%d') if e.date else None
            if dk in urg_trend and e.urgency_level in urg_trend[dk]:
                urg_trend[dk][e.urgency_level] += 1
        urg_dates = sorted(urg_trend.keys())
        urg_data = {k: [urg_trend[d][k] for d in urg_dates] for k in ['High', 'Medium', 'Low']}

        cat_trend = {d: {c: 0 for c in CATEGORIES_ALL} for d in dates7}
        for e in emails_7d:
            dk = format_user_tz(e.date, '%Y-%m-%d') if e.date else None
            if dk in cat_trend and e.category in cat_trend[dk]:
                cat_trend[dk][e.category] += 1
        cat_dates = sorted(cat_trend.keys())
        cat_trend_data = {c: [cat_trend[d][c] for d in cat_dates] for c in CATEGORIES_ALL}

        threat_data = {
            'total_emails': total_emails, 'total_phishing': total_phish, 'total_malware': total_mal,
            'high_risk_percentage': high_risk_pct,
            'chart_labels': CHART_LABELS, 'chart_data': chart_data_from_counts(cat_counts),
            'activity_timeline': timeline,
            'category_trend_dates': cat_dates, 'category_trend_data': cat_trend_data,
            'urgency_trend_dates': urg_dates, 'urgency_trend_data': urg_data,
            'risk_dist_dates': risk_dist_dates, 'risk_dist_data': risk_dist_data,
        }
        resp = make_response(render_template('threat_console.html', threat_data=threat_data))
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return resp
    except Exception as e:
        flash(f'Error loading threat console: {e}', 'error')
        return redirect(url_for('dashboard'))


# ── Results Page ───────────────────────────────────────────────────────────────

def _email_to_dict(e):
    return {
        'id': e.id, 'subject': e.subject, 'sender': e.sender,
        'date': format_user_tz(e.date, '%d-%m-%Y %I:%M %p'),
        'sortable_date': format_user_tz(e.date, '%Y-%m-%d %H:%M:%S'),
        'date_utc': (e.date.isoformat() + 'Z') if e.date else '',
        'snippet': e.snippet, 'category': e.category,
        'category_info': predictor.get_category_info(e.category) if e.category else None,
        'is_spam': e.is_spam, 'risk_score': e.risk_score or 0, 'risk_level': e.risk_level,
        'confidence': e.confidence or 0.0, 'urgency_score': e.urgency_score or 0,
        'urgency_level': e.urgency_level,
        'risk_breakdown': e.risk_breakdown or {},
        'explanation_summary': e.ai_explanation or e.explanation_summary,
        'spam_probability': e.confidence if e.is_spam else max(0, 100 - (e.confidence or 0)),
    }


def paginate(query, page):
    """Page a query at a fixed 50 rows and clamp out-of-range pages.

    total_pages, page, has_prev and has_next were computed in results_page and
    then never passed to the template, so `{% if total_pages and total_pages > 1 %}`
    was always false and the results page rendered no pagination controls at all.
    A 500-email scan showed its first 50 and nothing else.

    The page size is a constant, not derived from the total. That matters when a
    category filter is active: picking Spam must show all 4 spams on one page,
    not switch the page size because 4 happens to be under some threshold.

    One helper for both read pages so they cannot drift apart again.
    """
    total = query.count()
    size = RESULTS_PER_PAGE
    if total == 0:
        return [], dict(total=0, total_pages=0, page=1, page_size=size,
                        has_prev=False, has_next=False)
    total_pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, total_pages))       # ?page=99 on 2 pages -> page 2
    rows = query.offset((page - 1) * size).limit(size).all()
    return rows, dict(total=total, total_pages=total_pages, page=page,
                      page_size=size, has_prev=page > 1, has_next=page < total_pages)


def resolve_scan(user_email):
    """The scan id to render, and its scope dict.

    Order: the session copy, then the durable scan_sessions row, then the newest
    scan that actually has rows.

    This exists because current_scan_id lived only in the Flask session. Closing
    the browser or opening a new tab discarded it, and /results and /last-scan
    both redirected to the dashboard with "No scan has been run in this session"
    while the emails were still sitting in the database. The session is kept as
    the fast path so a live request never touches this table.

    Returns (scan_id, scope) or (None, None). Rehydrates the session on the way
    out so later code in the same request can keep reading the old key.

    Every candidate is VALIDATED against the emails table. An earlier version
    returned the session's scan_id unchecked, and because re-scans prune older
    rows a session could hold an id that no longer had any emails -- the views
    then took their `if not rows` branch and bounced back to the dashboard even
    though the database was full.
    """
    def _has_rows(sid):
        if not sid:
            return False
        try:
            return Email.query.filter_by(
                user_email=user_email, scan_id=sid).first() is not None
        except Exception:
            db.session.rollback()
            return False

    scan_id = session.get('current_scan_id')
    if scan_id and _has_rows(scan_id):
        return scan_id, session.get('scan_scope')
    # Stale or missing: fall through and re-resolve.

    row = ScanSession.recall(user_email)
    if row is not None:
        session['current_scan_id'] = row.scan_id
        if row.scope_json:
            try:
                session['scan_scope'] = row.to_dict()['scope']
            except Exception:
                pass
        return row.scan_id, session.get('scan_scope')

    # Last resort: the newest scan that actually has rows. This is what makes the
    # pages work at all after a restart, instead of bouncing to the dashboard.
    try:
        newest = (Email.query
                  .filter(Email.user_email == user_email, Email.scan_id.isnot(None))
                  .order_by(Email.updated_at.desc()).first())
    except Exception:
        db.session.rollback()
        newest = None
    if newest is not None and newest.scan_id:
        session['current_scan_id'] = newest.scan_id
        return newest.scan_id, session.get('scan_scope')
    return None, session.get('scan_scope')


def describe_scope(scope):
    """Human label for what the last scan covered, e.g. 'Last 30 days'."""
    if not scope:
        return None
    period = scope.get('period')
    f, t = scope.get('from_date'), scope.get('to_date')
    if period == 'custom' and f and t:
        return 'Custom %s to %s Emails' % (f, t)
    return {'7d': 'Last 7 days Emails', '30d': 'Last 30 days Emails',
            'month': 'This month Emails'}.get(period)


def _build_results_summary(results):
    # The summary must describe the WHOLE scan, never one page of it. It used to
    # be built from the 50 rows currently on screen, which is why a 500-email
    # scan reported "50 Total Emails" and why the numbers would have changed as
    # you paged. Callers now pass every row in the scan.
    if not results:
        return None
    cats = empty_category_counts()
    for r in results:
        if r['category'] in cats:
            cats[r['category']] += 1
    # Threats are counted the way the Threats BUTTON filters them
    # (category in THREAT_CATEGORIES), NOT by the is_spam flag.
    #
    # is_spam is true for every category except legitimate, so counting it put
    # all 273 Promotions and 22 Newsletters inside the Threats total. The card
    # read 315 while clicking Threats listed 20, which made the card look wrong
    # rather than making the two disagree.
    #
    # This drives both the "Threats" card and the "Threat Rate" card, because
    # spam_percentage below derives from the same count. Scoped to the results
    # and last-scan pages: those are the only callers of this function, and
    # results.html is the only reader of these two keys.
    spam_count = sum(1 for r in results
                     if r['category'] in THREAT_CATEGORIES)
    high_risk = sum(1 for r in results if r['risk_score'] >= 70)
    high_urg = sum(1 for r in results if r.get('urgency_level') == 'High')
    med_urg = sum(1 for r in results if r.get('urgency_level') == 'Medium')
    return {
        'total_emails': len(results), 'spam_emails': spam_count,
        'legitimate_emails': cats['legitimate'],
        'spam_percentage': round((spam_count / len(results)) * 100, 2),
        'risk_percentage': round((high_risk / len(results) * 100), 1),
        'chart_labels': CHART_LABELS, 'chart_data': chart_data_from_counts(cats),
        'categories': cats, 'analysis_date': format_user_tz(datetime.utcnow(), '%d-%m-%Y %I:%M %p'),
        'analysis_date_utc': datetime.utcnow().isoformat() + 'Z',
        'processing_time': 0, 'critical_threats': 0, 'high_risk_threats': high_risk,
        'total_threats': high_risk, 'high_urgency_emails': high_urg, 'medium_urgency_emails': med_urg,
    }


@app.route('/results')
@login_required
def results_page():
    user_email = get_user_email()
    if not user_email:
        flash('Please log in to view results.', 'error')
        return redirect(url_for('login'))

    try:
        cat_filter = request.args.get('category', 'all')
        sort_by = request.args.get('sort', 'date')
        page = max(request.args.get('page', 1, type=int), 1)
        # 'all' shows everything ever analysed; anything else shows one scan.
        scope = request.args.get('scope', 'current')
        # The All / Threats / Safe group, now applied server-side. An
        # unrecognised value falls back to 'all' rather than silently
        # returning an empty page.
        view_filter = request.args.get('view', 'all')
        if view_filter not in VIEW_FILTERS:
            view_filter = 'all'

        # TWO QUERIES ON PURPOSE.
        #
        # base_query is scan-scoped but UNFILTERED and backs every statistic:
        # the tiles, the Category Distribution card and the page count. query
        # adds the category filter and backs only the email list.
        #
        # They used to be the same query, so the moment you picked a category
        # the summary was rebuilt from that category alone and every other
        # category displayed 0. The breakdown has to stay stable while you
        # filter, otherwise you cannot tell where you are.
        base_query = Email.query.filter_by(user_email=user_email)
        if scope != 'all':
            scan_id, _scope = resolve_scan(user_email)
            if scan_id:
                base_query = base_query.filter(Email.scan_id == scan_id)
            else:
                # No scan has run AND none is remembered. Showing every
                # historical row would be the old confusing behaviour, so say so.
                flash('No scan has been run in this session. Run an analysis first.',
                      'info')
                return redirect(url_for('dashboard'))

        query = base_query
        if cat_filter != 'all' and cat_filter in CATEGORIES_ALL:
            query = query.filter(Email.category == cat_filter)

        # The All / Threats / Safe group. This used to be a client-side
        # display:none loop over the rows already in the DOM, which is blind to
        # every page except the current one -- the same defect the category
        # dropdown had. It is a real filter now, so it has to be applied here
        # where the whole scan is visible.
        #
        # `threat` is category-driven rather than is_spam: is_spam is
        # `category != 'legitimate'`, which counts every newsletter and
        # promotion as a threat. That definition is still parked, but routing
        # through it here would make the button disagree with the Category
        # Distribution counts directly above it.
        if view_filter == 'threat':
            query = query.filter(Email.category.in_(THREAT_CATEGORIES))
        elif view_filter == 'safe':
            query = query.filter(Email.category == 'legitimate')

        if sort_by == 'urgency':
            query = query.order_by(Email.urgency_score.desc())
        else:
            query = query.order_by(Email.date.desc())

        # Statistics come from base_query (the whole scan, category filter ignored).
        # The list below comes from query (filtered, one page).
        all_rows = base_query.all()
        scan_total = len(all_rows)
        rows, meta = paginate(query, page)

        # ONLY an empty SCAN is fatal. An empty FILTER is a valid answer.
        #
        # The guard used to be `if not rows`, where rows is the filtered page
        # slice -- so picking a category with zero emails (Phishing, Malware or
        # Spam on most inboxes) was indistinguishable from "the scan is empty".
        # The view then flashed "No emails in this scan. Run a wider analysis",
        # which was also factually wrong -- the scan had 24 emails -- and
        # redirected to the dashboard, throwing the user out of a page they had
        # not left. The zero-match case now renders in place instead.
        if scan_total == 0:
            flash('This scan returned no emails. Try a wider date range.', 'info')
            return redirect(url_for('dashboard'))

        results = [_email_to_dict(e) for e in rows]
        summary = _build_results_summary([_email_to_dict(e) for e in all_rows])
        # With a category filter the list is a subset, so the pager's denominator
        # must be the filtered total rather than the scan total.
        meta['total'] = query.count()
        return render_template('results.html', results=results, summary=summary,
                               current_category=cat_filter, current_sort=sort_by,
                               current_scope=scope, current_view=view_filter,
                               scan_total=scan_total,
                               scope_label=describe_scope(session.get('scan_scope')),
                               **meta)
    except Exception as e:
        logger.error(f"Error in results_page: {e}")
        flash(f'Error loading results: {e}', 'error')
        return redirect(url_for('dashboard'))


def _cat_meta():
    """Icon, label and colour per category, for the client-side label swap."""
    return {
        'all': ['fa-tag', 'All Categories', ''],
        'spam': ['fa-exclamation-triangle', 'Spam', 'var(--color-spam)'],
        'phishing': ['fa-skull-crossbones', 'Phishing', 'var(--color-phishing)'],
        'malware': ['fa-virus', 'Malware', 'var(--color-malware)'],
        'promotion': ['fa-bullhorn', 'Promotion', 'var(--color-promotion)'],
        'newsletter': ['fa-newspaper', 'Newsletter', 'var(--color-newsletter)'],
        'legitimate': ['fa-check-circle', 'Not Spam', 'var(--color-legitimate)'],
    }


@app.route('/results-fragment')
@login_required
def results_fragment():
    """The parts of the results view that change when you filter or paginate.

    Filter and page links used to be ordinary navigations, so every click threw
    the page away and re-rendered it. The client fetches this instead and swaps
    in the row list, the pager and the count line, leaving the header, the
    summary tiles and the category breakdown untouched -- those describe the
    whole scan and do not depend on the filter.

    JSON rather than HTML so the client needs no parser. The links stay real
    hrefs underneath, so if this request fails the browser navigates normally
    and the page still works.
    """
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'not_authenticated'}), 401

    cat_filter = request.args.get('category', 'all')
    sort_by = request.args.get('sort', 'date')
    view_filter = request.args.get('view', 'all')
    if view_filter not in VIEW_FILTERS:
        view_filter = 'all'
    page = max(request.args.get('page', 1, type=int), 1)

    scan_id, _scope = resolve_scan(user_email)
    if not scan_id:
        return jsonify({'error': 'no_scan'}), 409

    try:
        query = Email.query.filter_by(user_email=user_email, scan_id=scan_id)
        if cat_filter != 'all' and cat_filter in CATEGORIES_ALL:
            query = query.filter(Email.category == cat_filter)
        if view_filter == 'threat':
            query = query.filter(Email.category.in_(THREAT_CATEGORIES))
        elif view_filter == 'safe':
            query = query.filter(Email.category == 'legitimate')
        if sort_by == 'urgency':
            query = query.order_by(Email.urgency_score.desc())
        else:
            query = query.order_by(Email.date.desc())

        rows, meta = paginate(query, page)

        # A filter that matches NOTHING is a valid answer, not an error, so this
        # returns 200 with an empty body. It used to return 404, and the client's
        # catch treats any non-200 as a reason to fall back to
        # window.location.href -- which is precisely the full page reload the
        # filter was supposed to avoid.
        #
        # The genuinely-fatal cases keep their status codes so the client can
        # tell them apart: no_scan (409) means the scan is gone, and
        # not_authenticated (401) means the login expired.
        rows_html = ''
        cards_html = ''
        if rows:
            # Markup comes from the SAME Jinja macros the full page uses
            # (email_row / email_card in _email_row_macro.html), rendered through
            # the small _email_rows_fragment.html template.
            #
            # BOTH parts are needed. Swapping only the desktop table left the
            # mobile card grid showing the UNFILTERED list, so on a phone the
            # cards and the pager disagreed -- with Phishing selected the table
            # read "No phishing emails in this scan" while 24 cards sat below it.
            # Found by looking at the page on a narrow viewport.
            #
            # Two earlier approaches were tried and both are wrong:
            #   * template.module.email_row -- .module renders the whole template,
            #     and results.html extends base.html which needs `current_user`
            #     from Flask-Login's context processor. That raised
            #     UndefinedError and this endpoint returned 500.
            #   * rebuilding the row in JavaScript -- it drifted at once, printing
            #     the raw category slug and an unrounded confidence.
            both = [_email_to_dict(r) for r in rows]
            rows_html = render_template('_email_rows_fragment.html',
                                        part='rows', emails=both)
            cards_html = render_template('_email_rows_fragment.html',
                                         part='cards', emails=both)
        return jsonify({
            'rows_html': rows_html,
            'cards_html': cards_html,
            'meta': meta,
            'cat_meta': _cat_meta(),
            'view': view_filter,
            'sort': sort_by,
        })
    except Exception as e:
        logger.error(f"Error in results_fragment: {e}")
        return jsonify({'error': 'server_error'}), 500


@app.route('/last-scan')
@login_required
def last_scan_page():
    user_email = get_user_email()
    if not user_email:
        flash('Please log in to view results.', 'error')
        return redirect(url_for('login'))

    try:
        scan_id, _scope = resolve_scan(user_email)
        if not scan_id:
            flash('No scan has been run in this session. Run an analysis first.', 'info')
            return redirect(url_for('dashboard'))

        page = max(request.args.get('page', 1, type=int), 1)
        # Same three params as /results, so the filter controls and pager work
        # identically on both pages -- they render the same template.
        cat_filter = request.args.get('category', 'all')
        sort_by = request.args.get('sort', 'date')
        view_filter = request.args.get('view', 'all')
        if view_filter not in VIEW_FILTERS:
            view_filter = 'all'

        query = Email.query.filter_by(user_email=user_email, scan_id=scan_id)
        if cat_filter != 'all' and cat_filter in CATEGORIES_ALL:
            query = query.filter(Email.category == cat_filter)
        if view_filter == 'threat':
            query = query.filter(Email.category.in_(THREAT_CATEGORIES))
        elif view_filter == 'safe':
            query = query.filter(Email.category == 'legitimate')

        if sort_by == 'urgency':
            query = query.order_by(Email.urgency_score.desc())
        else:
            query = query.order_by(Email.date.desc())

        all_rows = Email.query.filter_by(user_email=user_email,
                                         scan_id=scan_id).all()
        scan_total = len(all_rows)
        rows, meta = paginate(query, page)

        # Same rule as results_page: only an empty SCAN is fatal. A filter that
        # matches nothing renders in place with an empty list, because the user
        # is still on the results page and should not be thrown to the dashboard.
        if scan_total == 0:
            flash('This scan returned no emails. Try a wider date range.', 'info')
            return redirect(url_for('dashboard'))

        # Summary covers the whole scan, the list below is one page of it.
        results = [_email_to_dict(e) for e in rows]
        summary = _build_results_summary([_email_to_dict(e) for e in all_rows])
        if summary:
            # all_rows[0], not rows[0]: rows is the FILTERED page slice and is
            # empty when a category filter matches nothing, so rows[0] raised
            # IndexError -- caught by the handler below, which redirected to the
            # dashboard with a misleading "Error loading results" flash.
            scan_ts = all_rows[0].updated_at or datetime.utcnow()
            summary['analysis_date'] = format_user_tz(scan_ts, '%d-%m-%Y %I:%M %p')
            summary['analysis_date_utc'] = scan_ts.isoformat() + 'Z'

        meta['total'] = query.count()
        return render_template('results.html', results=results, summary=summary,
                               current_category=cat_filter, current_sort=sort_by,
                               page_title='Last Scan Results', is_last_scan=True,
                               current_scope='current', current_view=view_filter,
                               scan_total=scan_total,
                               scope_label=describe_scope(session.get('scan_scope')),
                               **meta)
    except Exception as e:
        logger.error(f"Error in last_scan_page: {e}")
        flash(f'Error loading results: {e}', 'error')
        return redirect(url_for('dashboard'))


# ── Email Analysis (sync) ─────────────────────────────────────────────────────

@app.route('/analyze_emails')
@login_required
@rate_limit(max_req=10, window=60)
def analyze_emails():
    oauth_token = session.get('oauth_token')
    if not oauth_token:
        flash('OAuth token not found. Please login again.', 'error')
        return redirect(url_for('login'))

    user_email = get_user_email()
    if not user_email:
        flash('Session expired. Please login again.', 'error')
        return redirect(url_for('login'))

    try:
        start = datetime.now()
        try:
            results, err = run_analysis(user_email, oauth_token)
        except GmailFetchError as e:
            # run_analysis deliberately lets this propagate rather than
            # reporting an empty inbox, so the route has to translate it. Without
            # this it landed in the generic handler below as a red 500.
            err_text = str(e).lower()
            if 'credential' in err_text or 'auth' in err_text or 'token' in err_text:
                flash('Gmail rejected the request - your login has expired. '
                      'Please log out and log in again.', 'error')
            elif 'network' in err_text or 'timeout' in err_text:
                flash('Could not reach Gmail. Check your connection and try again.', 'error')
            else:
                flash('Could not fetch emails from Gmail: %s' % e, 'error')
            return redirect(url_for('dashboard'))
        if err:
            flash(f'Analysis issue: {err}', 'warning')
            return redirect(url_for('dashboard'))

        total_time = (datetime.now() - start).seconds
        high_risk = sum(1 for r in results if r['risk_level'] == 'High')

        if high_risk > 0:
            flash(f'Analysis complete! Found {high_risk} high-risk emails.', 'warning')
        else:
            flash(f'Analysis complete! All {len(results)} emails analyzed successfully.', 'success')

        return redirect(url_for('results_page'))
    except Exception as e:
        logger.error(f"Error in analyze_emails: {e}")
        flash(f'Error analyzing emails: {e}', 'error')
        return redirect(url_for('dashboard'))


# ── API Routes ────────────────────────────────────────────────────────────────

@app.route('/api/analyze_text', methods=['POST'])
@login_required
@rate_limit(max_req=10, window=60)
def analyze_text():
    try:
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 415
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON data'}), 400
        text = (data.get('text', '') or '').strip()
        if not text:
            return jsonify({'error': 'No text provided'}), 400
        if len(text) > MAX_INPUT_LENGTH:
            return jsonify({'error': f'Text too long. Maximum {MAX_INPUT_LENGTH} characters.'}), 400

        # How much readable text is actually here. isalpha() counts accented
        # letters as letters and rejects emoji, which is the behaviour we want.
        letter_count = sum(1 for ch in text if ch.isalpha())
        word_count = len(text.split())

        # Refuse rather than invent a category. See MIN_LETTERS_TO_JUDGE.
        #
        # This returns NO category, NO risk_score and NO probabilities on
        # purpose. The client must branch on judgeable before rendering; if it
        # fell through to the normal renderer it would hit
        # `data.category || 'legitimate'` and paint a green "Not Spam" card,
        # which is a worse lie than the one being replaced.
        if letter_count <= MIN_LETTERS_TO_JUDGE:
            return jsonify({
                'judgeable': False,
                'letter_count': letter_count,
                'word_count': word_count,
                'message': 'Not enough text to analyse',
                'detail': (
                    'That was %d letter%s and %d word%s. Paste a few sentences '
                    'of the email and we will check it.'
                    % (letter_count, '' if letter_count == 1 else 's',
                       word_count, '' if word_count == 1 else 's')
                ),
            })

        start = datetime.now()
        try:
            pred = predictor.predict_single(text)
        except Exception:
            return jsonify({'error': 'Model temporarily unavailable'}), 503

        # State B: judge it, but say out loud how little there was. There is no
        # reliable model-side signal for this -- vocabulary coverage and the
        # top-1/top-2 margin were both measured and neither separates a right
        # answer from a wrong one -- so the count of readable text is the only
        # honest basis available.
        note = None
        if letter_count <= SHORT_INPUT_LETTERS:
            note = ('Only %d word%s and %d letters. That is a very short piece '
                    'of text, so treat this result with some care.'
                    % (word_count, '' if word_count == 1 else 's', letter_count))

        result = {
            'judgeable': True,
            'letter_count': letter_count,
            'word_count': word_count,
            'note': note,
            'category': pred['category'], 'category_info': predictor.get_category_info(pred['category']),
            'is_spam': pred['is_spam'], 'risk_score': pred['risk_score'], 'risk_level': pred['risk_level'],
            'confidence': pred['confidence'],
            'probabilities': {k: v * 100 for k, v in pred['probabilities'].items()},
            'spam_probability': pred['confidence'] if pred['is_spam'] else max(0, 100 - pred['confidence']),
            'analysis_time': round((datetime.now() - start).total_seconds(), 2),
        }
        return jsonify(result)
    except Exception:
        return jsonify({'error': 'Analysis failed'}), 500


@app.route('/bulk_analyze', methods=['GET', 'POST'])
@login_required
@rate_limit(max_req=5, window=60)
def bulk_analyze():
    if request.method == 'GET':
        return render_template('bulk_analyze.html')
    try:
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 415
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON data'}), 400
        texts = data.get('texts', [])
        if not texts or not isinstance(texts, list):
            return jsonify({'error': 'No texts provided'}), 400
        if len(texts) > MAX_BATCH_SIZE:
            return jsonify({'error': f'Batch too large. Maximum {MAX_BATCH_SIZE} texts.'}), 400

        start = datetime.now()
        results, threat_ct = [], 0
        for i, text in enumerate(texts):
            if not text.strip():
                continue
            try:
                pred = predictor.predict_single(text)
                if pred['risk_level'] in ['Critical', 'High']:
                    threat_ct += 1
                results.append({
                    'index': i, 'text': text[:100] + '...' if len(text) > 100 else text,
                    'category': pred['category'], 'category_info': predictor.get_category_info(pred['category']),
                    'is_spam': pred['is_spam'], 'risk_score': pred['risk_score'],
                    'risk_level': pred['risk_level'], 'confidence': pred['confidence'],
                })
            except Exception:
                continue

        spam_ct = sum(1 for r in results if r['is_spam'])
        return jsonify({
            'results': results,
            'summary': {
                'total_analyzed': len(results), 'spam_detected': spam_ct,
                'legitimate': len(results) - spam_ct,
                'spam_percentage': round((spam_ct / len(results)) * 100, 2) if results else 0,
                'analysis_time': round((datetime.now() - start).total_seconds(), 2),
                'threat_count': threat_ct, 'critical_count': 0,
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/status')
@login_required
def api_status():
    return jsonify({
        'api_version': '2.0',
        'model_status': 'active' if predictor.is_trained else 'inactive',
        'supported_categories': predictor.categories,
        'risk_levels': ['Low', 'Medium', 'High'],
        'endpoints': {
            'analyze_text': '/api/analyze_text', 'bulk_analyze': '/bulk_analyze',
            'start_analysis': '/api/start_analysis', 'status': '/api/status',
        }
    })


@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy', 'timestamp': datetime.now(timezone.utc).isoformat(),
        'model_loaded': predictor.is_trained, 'risk_assessment': 'active'
    })


@app.route('/set-timezone')
def set_timezone():
    tz = request.args.get('tz', '')
    if tz:
        try:
            ZoneInfo(tz)
            session['timezone'] = tz
        except Exception:
            pass
    locale = request.args.get('locale', '')
    if locale:
        locale = locale.replace('-', '_')
        session['locale'] = locale
    return '', 204


@app.route('/api/last_scan')
@login_required
@rate_limit(max_req=30, window=60)
def api_last_scan():
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'User not found'}), 401
    try:
        recent = Email.query.filter_by(user_email=user_email).order_by(Email.created_at.desc()).limit(50).all()
        if not recent:
            return jsonify({'has_results': False, 'message': 'No scan results found.'})

        scan_time = recent[0].created_at or datetime.utcnow()
        threats = sum(1 for e in recent if e.is_spam)
        high_risk = sum(1 for e in recent if e.risk_level in ['High', 'Critical'])

        email_list = [{
            'subject': (e.subject or 'No Subject')[:60] + ('...' if e.subject and len(e.subject) > 60 else ''),
            'sender': (e.sender or 'Unknown')[:40] + ('...' if e.sender and len(e.sender) > 40 else ''),
            'category': e.category or 'legitimate', 'risk_level': e.risk_level or 'Low',
            'risk_score': e.risk_score or 0, 'urgency_level': e.urgency_level or 'Low',
        } for e in recent]

        return jsonify({
            'has_results': True,
            'scan_time': format_user_date(scan_time),
            'total_emails': len(recent), 'threats_found': threats, 'high_risk_count': high_risk,
            'emails': email_list,
        })
    except Exception:
        return jsonify({'error': 'Failed to load scan results'}), 500


# ── Email View ─────────────────────────────────────────────────────────────────

@app.route('/email/<email_id>')
@login_required
@rate_limit(max_req=20, window=60)
def view_email(email_id):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def err_resp(msg, code):
        if is_ajax:
            return jsonify({'error': msg}), code
        flash(msg, 'error')
        return redirect(url_for('results_page'))

    try:
        if not email_id or not isinstance(email_id, str):
            return err_resp('Invalid email ID', 400)
        email_id = str(escape(email_id)).strip()
        if not email_id:
            return err_resp('Invalid email ID', 400)

        user_email = get_user_email()
        if not user_email:
            return err_resp('Please log in', 401)

        record = Email.query.filter_by(id=email_id, user_email=user_email).first()
        if not record:
            return err_resp('Email not found', 404)

        oauth_token = session.get('oauth_token')
        if not oauth_token:
            return err_resp('OAuth token not found. Please login again.', 401)

        try:
            oauth_token = _ensure_refresh_token(oauth_token, user_email)
            gmail_client = GmailClient(oauth_token)
        except Exception:
            return err_resp('Unable to connect to Gmail', 500)

        try:
            full = gmail_client.get_full_email(email_id)
        except Exception:
            return err_resp('Unable to load email content', 500)

        if not full:
            return err_resp('Email content not found', 404)

        analysis = {
            'category': record.category,
            'category_info': predictor.get_category_info(record.category) if record.category else None,
            'is_spam': record.is_spam, 'risk_score': record.risk_score or 0,
            'risk_level': record.risk_level, 'confidence': record.confidence or 0.0,
            'urgency_score': record.urgency_score or 0, 'urgency_level': record.urgency_level,
            'risk_breakdown': record.risk_breakdown or {}, 'explanation_summary': record.explanation_summary,
        }

        email_data = {
            'id': full['id'], 'subject': full['subject'], 'sender': full['sender'],
            'sender_display': full['sender_display'],
            'date': format_user_date(record.date) if record.date else full['date'],
            'date_utc': record.date.isoformat() + 'Z' if record.date else '',
            'to': full['to'], 'cc': full['cc'],
            'body_html': sanitize_email_html(full['body_html']),
            'body_text': full['body_text'], 'body_type': full['body_type'], 'snippet': full['snippet'],
        }

        # AI explanation is generated ONCE per email and cached in the DB;
        # every subsequent open serves the stored explanation (stable, instant).
        if record.ai_explanation:
            analysis['explanation_summary'] = record.ai_explanation
        else:
            ai_exp = generate_ai_explanation(
                email_data.get('subject', ''), email_data.get('body_text', email_data.get('snippet', '')),
                analysis.get('category', 'unknown'), analysis.get('confidence', 0.0)
            )
            if ai_exp:
                record.ai_explanation = ai_exp
                record.ai_explanation_generated_at = datetime.utcnow()
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    logger.warning('Failed to cache AI explanation for email %s', email_id)
                analysis['explanation_summary'] = ai_exp
            # else: AI failed/timed out — fall back to the rule-based summary
            # already stored in analysis; the cache stays empty so the next
            # open retries generation (self-healing).

        if is_ajax:
            return jsonify({'success': True, 'email': email_data, 'analysis': analysis})
        return render_template('email_view.html', email=email_data, analysis=analysis, user=current_user)
    except Exception as e:
        logger.error(f"Error in view_email: {e}")
        return err_resp('An error occurred while loading the email', 500)


# ── Async Analysis API ────────────────────────────────────────────────────────

@app.route('/api/start_analysis', methods=['POST'])
@login_required
@rate_limit(max_req=5, window=60, cooldown=True)
def start_analysis():
    oauth_token = session.get('oauth_token')
    user_email = get_user_email()
    if not oauth_token or not user_email:
        return jsonify({'error': 'Authentication required. Please login again.'}), 401

    # Scan scope. The body is optional so an older cached page that sends nothing
    # still gets exactly the previous behaviour rather than a 400.
    scope = request.get_json(silent=True) or {}
    period, from_date, to_date, max_count, err = validate_scan_scope(scope)
    if err:
        return jsonify({'error': err}), 400

    task_id = secrets.token_urlsafe(8)
    # Identifies this run. Stored rows carry it so /results and /last-scan can
    # show the scan the user just chose rather than every email ever analysed.
    scan_id = secrets.token_hex(8)
    session['current_scan_id'] = scan_id
    # The human-readable half of the scope, so Results, Last Scan and Analytics
    # can all say what was scanned without re-deriving it from a query string.
    scope = {'period': period, 'from_date': from_date,
             'to_date': to_date, 'max_count': max_count}
    session['scan_scope'] = scope
    # Also write it outside the session. The session copy is lost on browser
    # restart, which used to send /results and /last-scan straight back to the
    # dashboard even though the emails were in the database.
    ScanSession.remember(get_user_email(), scan_id, scope)
    with analysis_tasks_lock:
        analysis_tasks[task_id] = {
            'progress': 0, 'status': 'Initializing...', 'user_email': user_email,
            'complete': False, 'cancelled': False, 'error': None,
            'timestamp': datetime.now().timestamp(),
            'scan_id': scan_id,
            'scope': {'period': period, 'from_date': from_date,
                      'to_date': to_date, 'max_count': max_count},
        }

    t = threading.Thread(target=_run_analysis_bg,
                         args=(task_id, oauth_token, user_email, app,
                               period, from_date, to_date, max_count, scan_id),
                         daemon=True)
    t.start()
    logger.info(f"Started async analysis task {task_id} for user {user_email} "
                f"(period={period}, count={max_count}, scan={scan_id})")
    return jsonify({'task_id': task_id, 'status': 'started', 'scan_id': scan_id,
                    'scope': {'period': period, 'max_count': max_count},
                    'message': f'Poll /api/analysis_status/{task_id}'})


@app.route('/api/cancel_analysis/<task_id>', methods=['POST'])
@login_required
def cancel_analysis(task_id):
    user_email = get_user_email()
    if not user_email:
        return jsonify({'error': 'Not authenticated'}), 401

    with analysis_tasks_lock:
        task = analysis_tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    if task.get('user_email') != user_email:
        return jsonify({'error': 'Unauthorized'}), 403
    if task.get('complete'):
        return jsonify({'status': 'already_complete'})

    with analysis_tasks_lock:
        if task_id in analysis_tasks:
            analysis_tasks[task_id]['cancelled'] = True
            analysis_tasks[task_id]['status'] = 'Cancelling...'
    return jsonify({'status': 'cancelled', 'message': 'Cancellation requested.'})


@app.route('/api/analysis_status/<task_id>')
@login_required
@rate_limit(max_req=120, window=60)
def analysis_status(task_id):
    with analysis_tasks_lock:
        task = analysis_tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    user_email = get_user_email()
    if task.get('user_email') != user_email:
        return jsonify({'error': 'Unauthorized'}), 403

    return jsonify({
        'task_id': task_id, 'progress': task.get('progress', 0),
        'status': task.get('status', 'Unknown'), 'complete': task.get('complete', False),
        'error': task.get('error'),
    })


# ── Error Handlers ─────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(e):
    return render_template('500.html'), 500

@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html'), 403


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    logger.info("Starting SpamProtection Flask App...")
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
