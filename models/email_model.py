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
    
    # Spam classification fields
    category = db.Column(db.String(50), default='legitimate', index=True)
    is_spam = db.Column(db.Boolean, default=False)
    risk_score = db.Column(db.Integer, default=0)
    risk_level = db.Column(db.String(20))
    confidence = db.Column(db.Float, default=0.0)
    
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
