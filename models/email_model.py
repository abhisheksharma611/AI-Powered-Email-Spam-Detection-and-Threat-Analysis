"""
Email Model for SQLAlchemy database storage.
Stores email data along with spam classification and urgency prediction results.
"""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Email(db.Model):
    """Email model storing classification and urgency data."""
    
    __tablename__ = 'emails'
    
    # Primary identification
    id = db.Column(db.String(100), primary_key=True)
    user_email = db.Column(db.String(255), nullable=False, index=True)
    
    # Email content fields
    subject = db.Column(db.String(500))
    sender = db.Column(db.String(255))
    body = db.Column(db.Text)
    snippet = db.Column(db.Text)
    date = db.Column(db.DateTime)
    
    # Urgency prediction fields (NEW)
    urgency_score = db.Column(db.Integer, default=0, index=True)
    urgency_level = db.Column(db.String(20), default='Low')  # Low, Medium, High
    
    # Explainability & Risk Breakdown fields
    risk_breakdown = db.Column(db.JSON, nullable=True)
    explanation_summary = db.Column(db.Text, nullable=True)
    
    # Cached AI explanation (generated once on first open, reused afterwards)
    ai_explanation = db.Column(db.Text, nullable=True)
    ai_explanation_generated_at = db.Column(db.DateTime, nullable=True)
    
    # Spam classification fields
    category = db.Column(db.String(50), default='legitimate', index=True)
    is_spam = db.Column(db.Boolean, default=False)
    risk_score = db.Column(db.Integer, default=0)
    risk_level = db.Column(db.String(20))
    confidence = db.Column(db.Float, default=0.0)

    # Gmail labels as they were when this row was last scanned, comma-joined.
    #
    # Nullable and additive, so existing rows are fine and no data migration is
    # required. This exists because the labels used to be fetched, held in a
    # dict inside get_recent_emails and then dropped on the floor -- which is
    # exactly how deleted mail kept coming back with nothing to show for it.
    # Keeping them makes "why is this row here" answerable without Gmail.
    label_ids = db.Column(db.String(255), nullable=True)

    # Which scan produced this row.
    #
    # Without this the results page could not tell the mail from the scan the
    # user just ran from mail left over from earlier scans: /results simply did
    # `Email.query.filter_by(user_email=...)` and showed everything, so choosing
    # "custom, 10 emails" still displayed 50 rows. The user asked for a scope and
    # had no way to see it.
    #
    # Nullable and additive: rows written before this column existed belong to no
    # scan and still show under "All scans".
    scan_id = db.Column(db.String(32), nullable=True, index=True)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<Email {self.id} - {self.category} - Urgency: {self.urgency_level}>'
    
    def to_dict(self):
        """Convert email to dictionary for JSON serialization."""
        return {
            'id': self.id,
            'user_email': self.user_email,
            'subject': self.subject,
            'sender': self.sender,
            'body': self.body,
            'snippet': self.snippet,
            'date': self.date.isoformat() if self.date else None,
            'urgency_score': self.urgency_score,
            'urgency_level': self.urgency_level,
            'category': self.category,
            'is_spam': self.is_spam,
            'risk_score': self.risk_score,
            'risk_level': self.risk_level,
            'confidence': self.confidence,
            'risk_breakdown': self.risk_breakdown,
            'explanation_summary': self.explanation_summary,
            'ai_explanation': self.ai_explanation,
            'ai_explanation_generated_at': self.ai_explanation_generated_at.isoformat() if self.ai_explanation_generated_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
    
    @staticmethod
    def get_or_create(session, email_id, user_email):
        """Get existing email or create new one."""
        email = session.query(Email).filter_by(id=email_id, user_email=user_email).first()
        if not email:
            email = Email(id=email_id, user_email=user_email)
            session.add(email)
        return email


class SenderReputation(db.Model):
    """Sender reputation tracking for adaptive intelligence.
    
    Tracks email sender behavior patterns to calculate reputation scores
    and risk levels based on their historical email characteristics.
    """
    
    __tablename__ = 'sender_reputation'
    
    # Primary identification
    id = db.Column(db.Integer, primary_key=True)
    
    # Sender identifier (unique - one record per sender)
    sender_email = db.Column(db.String(255), unique=True, index=True, nullable=False)
    
    # Email counts
    total_emails = db.Column(db.Integer, default=0)
    phishing_count = db.Column(db.Integer, default=0)
    malware_count = db.Column(db.Integer, default=0)
    spam_count = db.Column(db.Integer, default=0)
    high_urgency_count = db.Column(db.Integer, default=0)
    
    # Calculated metrics
    reputation_score = db.Column(db.Float, default=0.0)
    risk_level = db.Column(db.String(20), default='Low')
    
    # Timestamps
    last_seen = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<SenderReputation {self.sender_email} - Score: {self.reputation_score}>'
    
    def to_dict(self):
        """Convert sender reputation to dictionary for JSON serialization."""
        return {
            'id': self.id,
            'sender_email': self.sender_email,
            'total_emails': self.total_emails,
            'phishing_count': self.phishing_count,
            'malware_count': self.malware_count,
            'spam_count': self.spam_count,
            'high_urgency_count': self.high_urgency_count,
            'reputation_score': self.reputation_score,
            'risk_level': self.risk_level,
            'last_seen': self.last_seen.isoformat() if self.last_seen else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class OAuthStore(db.Model):
    __tablename__ = 'oauth_store'
    user_email = db.Column(db.String(255), primary_key=True)
    refresh_token = db.Column(db.String(512))


class ScanSession(db.Model):
    """The most recent scan per user, kept OUTSIDE the Flask session.

    current_scan_id used to live only in session['current_scan_id'], so closing
    the browser or opening a new tab threw it away while /results and
    /last-scan both refused to render without it -- "No scan has been run in
    this session" -- even with the emails sitting in the database. The session
    copy stays as the fast path; this table is the durable fallback.
    """
    __tablename__ = 'scan_sessions'

    user_email = db.Column(db.String(255), primary_key=True)
    scan_id = db.Column(db.String(32), nullable=False, index=True)
    scope_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    def to_dict(self):
        import json
        try:
            scope = json.loads(self.scope_json) if self.scope_json else None
        except (ValueError, TypeError):
            scope = None
        return {'user_email': self.user_email, 'scan_id': self.scan_id,
                'scope': scope,
                'updated_at': self.updated_at.isoformat() if self.updated_at else None}

    @staticmethod
    def remember(user_email, scan_id, scope=None):
        """Upsert the pointer. Returns the row, or None if the write failed --
        a caller must not treat that as fatal, because the session copy may
        still cover the current request."""
        import json
        try:
            row = ScanSession.query.get(user_email)
            if row is None:
                row = ScanSession(user_email=user_email)
                db.session.add(row)
            row.scan_id = scan_id
            row.scope_json = json.dumps(scope) if scope else None
            db.session.commit()
            return row
        except Exception:
            db.session.rollback()
            return None

    @staticmethod
    def recall(user_email):
        """The remembered scan, or None. Also drops a pointer whose scan has no
        rows, so a cleared or re-scanned database cannot resurrect a dead id."""
        if not user_email:
            return None
        try:
            row = ScanSession.query.get(user_email)
        except Exception:
            db.session.rollback()
            return None
        if row is None or not row.scan_id:
            return None
        try:
            exists = Email.query.filter_by(user_email=user_email,
                                           scan_id=row.scan_id).first()
        except Exception:
            db.session.rollback()
            return None
        return row if exists else None


class LearnedKeyword(db.Model):
    """Learned keyword memory for adaptive intelligence.
    
    Tracks keywords extracted from phishing and malware emails to build
    a memory of dangerous terms. These learned keywords are then used to
    boost risk scores for new incoming emails containing these terms.
    
    Fields:
    - keyword: The extracted keyword (unique, indexed)
    - category: Source category (phishing/malware)
    - frequency: Number of times keyword has been learned
    - weight: Risk boost value (default 5, increases with frequency)
    """
    
    __tablename__ = 'learned_keywords'
    
    # Primary identification
    id = db.Column(db.Integer, primary_key=True)
    
    # Keyword fields
    keyword = db.Column(db.String(100), unique=True, index=True, nullable=False)
    category = db.Column(db.String(50), index=True)
    frequency = db.Column(db.Integer, default=1)
    weight = db.Column(db.Integer, default=5)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<LearnedKeyword {self.keyword} - Category: {self.category} - Weight: {self.weight}>'
    
    def to_dict(self):
        """Convert learned keyword to dictionary for JSON serialization."""
        return {
            'id': self.id,
            'keyword': self.keyword,
            'category': self.category,
            'frequency': self.frequency,
            'weight': self.weight,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
