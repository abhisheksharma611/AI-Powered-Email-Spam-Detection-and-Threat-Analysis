import os
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
from utils.gmail_client import GmailClient
from utils.ai_explanation import generate_ai_explanation
from models.predictor import Predictor
from models.email_model import db, Email, SenderReputation, OAuthStore
from utils.helpers import (
    calculate_urgency, learn_keywords_from_email, apply_adaptive_keyword_boost,
    build_risk_breakdown, generate_explanation_summary, get_matched_learned_keywords,
    sanitize_email_html
)

warnings.filterwarnings('ignore', category=FutureWarning, module='transformers.utils.generic')
warnings.filterwarnings('ignore', category=FutureWarning, module='torch.utils._pytree')

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_INPUT_LENGTH = 5000
MAX_BATCH_SIZE = 100
RESULTS_PER_PAGE = 50
DEFAULT_RATE_LIMIT_REQUESTS = 10
DEFAULT_RATE_LIMIT_WINDOW = 60
IST_OFFSET = timezone(timedelta(hours=5, minutes=30))
CATEGORIES_ALL = ['legitimate', 'promotion', 'phishing', 'malware', 'newsletter', 'spam']
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


# ── Sender Reputation ─────────────────────────────────────────────────────────

def update_sender_reputation(sender_email, category, urgency_score, db_session=None):
    session = db_session or db.session
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
        decay = max(0.5, 1.0 - 0.05 * months)
        rep.phishing_count = max(0, int(rep.phishing_count * decay))
        rep.malware_count = max(0, int(rep.malware_count * decay))
        rep.spam_count = max(0, int(rep.spam_count * decay))
        rep.high_urgency_count = max(0, int(rep.high_urgency_count * decay))

    rep.total_emails += 1
    if category == 'phishing':
        rep.phishing_count += 1
    elif category == 'malware':
        rep.malware_count += 1
    elif category == 'spam':
        rep.spam_count += 1
    if urgency_score >= 70:
        rep.high_urgency_count += 1
    rep.last_seen = datetime.utcnow()

    raw = rep.phishing_count * 4 + rep.malware_count * 4 + rep.spam_count * 2 + rep.high_urgency_count
    max_possible = rep.total_emails * 4
    rep.reputation_score = min((raw / max_possible) * 100, 100.0) if max_possible > 0 else 0

    if rep.total_emails >= 3:
        if rep.reputation_score < 20:
            rep.risk_level = 'Low'
        elif rep.reputation_score < 50:
            rep.risk_level = 'Medium'
        else:
            rep.risk_level = 'High'
    else:
        rep.risk_level = 'Low'

    return rep.reputation_score, rep.risk_level


# ── Analysis Service (shared by sync + async) ─────────────────────────────────

def _prepare_email_data(emails):
    texts, metadata = [], []
    for i, em in enumerate(emails):
        body = em.get('body', '') or em.get('snippet', '')
        texts.append(f"{em.get('subject', '')} {body}")
        metadata.append({
            'index': i,
            'id': em.get('id', f'email_{i}'),
            'subject': em.get('subject', 'No Subject'),
            'sender': em.get('sender', 'Unknown'),
            'date': em.get('date', ''),
            'date_obj': parse_email_date(em.get('date', '')),
            'snippet': em.get('snippet', ''),
            'body': body,
        })
    return texts, metadata


def _process_single_email(metadata, prediction, user_email, db_session=None):
    sess = db_session or db.session
    cat = prediction['category']
    risk_score = prediction['risk_score']
    is_spam = prediction['is_spam']
    risk_level = prediction['risk_level']
    confidence = prediction['confidence']
    category_info = predictor.get_category_info(cat)

    urgency = calculate_urgency(metadata['subject'], metadata.get('body', metadata['snippet']), metadata['sender'])

    try:
        record = Email.get_or_create(sess, metadata['id'], user_email)
        record.subject = str(metadata['subject'])[:500]
        record.sender = str(metadata['sender'])[:255]
        record.snippet = str(metadata['snippet'])[:1000]
        record.date = metadata.get('date_obj')
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
            _, sender_risk = update_sender_reputation(metadata['sender'], cat, urgency['urgency_score'], sess)
            if sender_risk == 'Medium':
                record.risk_score = min(record.risk_score + 10, 100)
            elif sender_risk == 'High':
                record.risk_score = min(record.risk_score + 20, 100)
            record.risk_level = risk_level_for_score(record.risk_score)

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

        record.risk_breakdown = build_risk_breakdown(
            base_score, ml_score, urg_boost, kw_boost, snd_boost, matched_kw, flags
        )
        record.explanation_summary = generate_explanation_summary(
            record.risk_breakdown, cat, urgency['urgency_level'],
            metadata['subject'], metadata.get('body', metadata['snippet']), confidence
        )

        if cat in ['phishing', 'malware']:
            cnt = learn_keywords_from_email(metadata['subject'], metadata.get('body', metadata['snippet']), cat, sess, confidence)
            if cnt > 0:
                logger.info(f"Learned {cnt} keywords from {cat} email")

        return {
            'id': metadata['id'], 'subject': metadata['subject'], 'sender': metadata['sender'],
            'date': metadata['date'], 'snippet': metadata['snippet'], 'category': cat,
            'category_info': category_info, 'is_spam': is_spam,
            'risk_score': record.risk_score, 'risk_level': record.risk_level,
            'confidence': confidence, 'urgency_score': urgency['urgency_score'],
            'urgency_level': urgency['urgency_level'], 'risk_breakdown': record.risk_breakdown,
            'explanation_summary': record.explanation_summary,
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


def run_analysis(user_email, oauth_token, progress_callback=None):
    oauth_token = _ensure_refresh_token(oauth_token, user_email)
    gmail_client = GmailClient(oauth_token)
    emails = gmail_client.get_recent_emails(max_results=50)

    if not emails:
        return None, "No emails returned from inbox"

    if progress_callback:
        progress_callback(15, f'Fetched {len(emails)} emails. Starting AI analysis...')

    texts, metadata = _prepare_email_data(emails)
    results = []
    cat_counts = empty_category_counts()
    total = len(texts)
    passed = 0
    failed = 0
    logger.info(f"[ANALYSIS] Analyzing {total} emails for {user_email}")
    BATCH = 10

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

            result = _process_single_email(meta, pred, user_email)
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


def _run_analysis_bg(task_id, oauth_token, user_email, flask_app):
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
            emails = gmail_client.get_recent_emails(max_results=50)
        except Exception as e:
            err = str(e).lower()
            if 'network' in err or 'connection' in err:
                msg = 'Network error - check your internet connection'
            elif 'auth' in err or 'token' in err:
                msg = 'Authentication error - please login again'
            else:
                msg = f'Failed to fetch emails: {str(e)}'
            _update_task(task_id, 0, msg, complete=True, error=msg)
            return

        if not emails:
            _update_task(task_id, 0, 'No emails found', complete=True, error='Inbox appears empty')
            return

        _update_task(task_id, 15, f'Fetched {len(emails)} emails. Starting AI analysis...')

        texts, metadata_list = _prepare_email_data(emails)
        results = []
        cat_counts = empty_category_counts()
        total = len(texts)
        passed = 0
        failed = 0
        logger.info(f"[ANALYSIS] Analyzing {total} emails for {user_email}")
        BATCH = 10

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

                    result = _process_single_email(meta, bp[j], user_email, db.session)
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
        recent = Email.query.filter_by(user_email=user_email).order_by(Email.updated_at.desc()).limit(50).all()
        if not recent:
            flash('No analysis results found. Please run the analysis first.', 'info')
            return redirect(url_for('dashboard'))

        cat_counts = empty_category_counts()
        for e in recent:
            if e.category in cat_counts:
                cat_counts[e.category] += 1

        urgency_scores = [e.urgency_score for e in recent if e.urgency_score]
        avg_urgency = round(sum(urgency_scores) / len(urgency_scores), 1) if urgency_scores else 0
        high_urgency = sum(1 for e in recent if e.urgency_score and e.urgency_score >= 70)

        top5 = sorted([e for e in recent if e.urgency_score], key=lambda e: e.urgency_score, reverse=True)[:5]
        top5_data = [{
            'id': e.id, 'subject': e.subject, 'sender': e.sender,
            'date': format_user_date(e.date), 'category': e.category,
            'urgency_score': e.urgency_score or 0, 'urgency_level': e.urgency_level,
            'date_utc': (e.date.isoformat() + 'Z') if e.date else '',
        } for e in top5]

        risk_low = sum(1 for e in recent if e.risk_score is not None and 0 <= e.risk_score <= 40)
        risk_med = sum(1 for e in recent if e.risk_score is not None and 41 <= e.risk_score <= 60)
        risk_high = sum(1 for e in recent if e.risk_score is not None and 61 <= e.risk_score <= 100)

        seven_ago = datetime.utcnow() - timedelta(days=7)
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
            'chart_labels': CHART_LABELS_ALT, 'chart_data': chart_data_from_counts(cat_counts, CHART_LABELS_ALT),
            'risk_percentage': int(risk_pct), 'risk_level': rlevel, 'risk_badge_color': rcolor,
            'risk_distribution': {'Low': risk_low, 'Medium': risk_med, 'High': risk_high},
            'risk_trend_dates': dates7, 'risk_trend_values': risk_trend_values,
            'last_scan_time': format_user_date(latest_update),
            'last_scan_time_utc': latest_update.isoformat() + 'Z',
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
        seven_ago = datetime.utcnow() - timedelta(days=7)

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
        'risk_breakdown': e.risk_breakdown or {}, 'explanation_summary': e.explanation_summary,
        'spam_probability': e.confidence if e.is_spam else max(0, 100 - (e.confidence or 0)),
    }


def _build_results_summary(results):
    if not results:
        return None
    cats = empty_category_counts()
    for r in results:
        if r['category'] in cats:
            cats[r['category']] += 1
    spam_count = sum(1 for r in results if r['is_spam'])
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

        query = Email.query.filter_by(user_email=user_email)
        if cat_filter != 'all' and cat_filter in CATEGORIES_ALL:
            query = query.filter(Email.category == cat_filter)

        if sort_by == 'urgency':
            query = query.order_by(Email.urgency_score.desc())
        else:
            query = query.order_by(Email.date.desc())

        total = query.count()
        emails = query.offset((page - 1) * RESULTS_PER_PAGE).limit(RESULTS_PER_PAGE).all()
        total_pages = (total + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE

        if not emails:
            flash('No analysis results found. Please run the analysis first.', 'info')
            return redirect(url_for('dashboard'))

        results = [_email_to_dict(e) for e in emails]
        summary = _build_results_summary(results)
        return render_template('results.html', results=results, summary=summary,
                               current_category=cat_filter, current_sort=sort_by)
    except Exception as e:
        logger.error(f"Error in results_page: {e}")
        flash(f'Error loading results: {e}', 'error')
        return redirect(url_for('dashboard'))


@app.route('/last-scan')
@login_required
def last_scan_page():
    user_email = get_user_email()
    if not user_email:
        flash('Please log in to view results.', 'error')
        return redirect(url_for('login'))

    try:
        recent = Email.query.filter_by(user_email=user_email).order_by(Email.date.desc()).limit(50).all()
        if not recent:
            flash('No scan results found. Run an analysis first.', 'info')
            return redirect(url_for('dashboard'))

        results = [_email_to_dict(e) for e in recent]
        summary = _build_results_summary(results)
        if summary:
            scan_ts = recent[0].updated_at or datetime.utcnow()
            summary['analysis_date'] = format_user_tz(scan_ts, '%d-%m-%Y %I:%M %p')
            summary['analysis_date_utc'] = scan_ts.isoformat() + 'Z'

        return render_template('results.html', results=results, summary=summary,
                               current_category='all', current_sort='date',
                               page_title='Last Scan Results', is_last_scan=True)
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
        results, err = run_analysis(user_email, oauth_token)
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

        start = datetime.now()
        try:
            pred = predictor.predict_single(text)
        except Exception:
            return jsonify({'error': 'Model temporarily unavailable'}), 503

        result = {
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

        ai_exp = generate_ai_explanation(
            email_data.get('subject', ''), email_data.get('body_text', email_data.get('snippet', '')),
            analysis.get('category', 'unknown'), analysis.get('confidence', 0.0)
        )
        if ai_exp:
            analysis['explanation_summary'] = ai_exp

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

    task_id = secrets.token_urlsafe(8)
    with analysis_tasks_lock:
        analysis_tasks[task_id] = {
            'progress': 0, 'status': 'Initializing...', 'user_email': user_email,
            'complete': False, 'cancelled': False, 'error': None,
            'timestamp': datetime.now().timestamp(),
        }

    t = threading.Thread(target=_run_analysis_bg, args=(task_id, oauth_token, user_email, app), daemon=True)
    t.start()
    logger.info(f"Started async analysis task {task_id} for user {user_email}")
    return jsonify({'task_id': task_id, 'status': 'started', 'message': f'Poll /api/analysis_status/{task_id}'})


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
