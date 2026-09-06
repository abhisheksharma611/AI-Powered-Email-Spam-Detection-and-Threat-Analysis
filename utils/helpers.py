import re
from datetime import datetime, timedelta
from typing import Dict, List, Any
import bleach


# ============================================================================
# EMAIL HTML SANITIZER
# ============================================================================

def sanitize_email_html(html: str) -> str:
    """Sanitize email HTML to prevent XSS while preserving formatting.
    
    Allows safe formatting tags (p, div, span, table, tr, td, img, a, etc.)
    but strips script, iframe, object, embed, form, and event handler attributes.
    """
    if not html:
        return html
    
    allowed_tags = [
        'p', 'div', 'span', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'strong', 'b', 'em', 'i', 'u', 's', 'sub', 'sup', 'small', 'big',
        'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th', 'caption', 'colgroup', 'col',
        'ul', 'ol', 'li', 'dl', 'dt', 'dd',
        'a', 'img', 'figure', 'figcaption',
        'blockquote', 'pre', 'code', 'q', 'cite',
        'section', 'article', 'aside', 'header', 'footer', 'nav', 'main',
        'style', 'font', 'center', 'ruby', 'rt', 'rp',
    ]
    
    allowed_attrs = {
        '*': ['style', 'class', 'id', 'dir', 'lang', 'title', 'role', 'aria-label'],
        'a': ['href', 'target', 'rel', 'name'],
        'img': ['src', 'alt', 'width', 'height', 'border', 'align', 'hspace', 'vspace'],
        'td': ['colspan', 'rowspan', 'valign', 'align', 'width', 'height'],
        'th': ['colspan', 'rowspan', 'valign', 'align', 'width', 'height'],
        'table': ['border', 'cellpadding', 'cellspacing', 'width', 'height', 'align', 'bgcolor'],
        'col': ['span', 'width'],
        'colgroup': ['span'],
        'font': ['color', 'size', 'face'],
        'hr': ['width', 'size', 'noshade'],
        'li': ['value', 'type'],
        'ol': ['type', 'start', 'reversed'],
    }
    
    allowed_protocols = ['http', 'https', 'mailto']
    
    # Strip tags not in allowed list, strip event handlers, sanitize URLs
    cleaned = bleach.clean(
        html,
        tags=allowed_tags,
        attributes=allowed_attrs,
        protocols=allowed_protocols,
        strip=True
    )
    
    # Remove javascript: URLs that may have slipped through
    cleaned = re.sub(r'javascript\s*:', '', cleaned, flags=re.IGNORECASE)
    # Remove vbscript: URLs
    cleaned = re.sub(r'vbscript\s*:', '', cleaned, flags=re.IGNORECASE)
    
    return cleaned


# ============================================================================
# COMPILED REGEX PATTERNS FOR URGENCY DETECTION (Module level for performance)
# ============================================================================

# Immediate Pressure patterns (+40 points)
# Matches: immediately, urgent, asap, right now, act now
IMMEDIATE_PRESSURE_PATTERN = re.compile(
    r'\b(immediately|urgent|asap|right now|act now)\b',
    re.IGNORECASE
)

# 24-48 Hour Deadline patterns (+30 points)
# Matches: within 24 hours, within 48 hours, expires today, by tomorrow, today only
SHORT_DEADLINE_PATTERN = re.compile(
    r'\b(within 24 hours|within 48 hours|expires today|by tomorrow|today only)\b',
    re.IGNORECASE
)

# Specific Hour Mention patterns (+25 points)
# Matches: in X hours, within X hours
HOUR_PATTERN = re.compile(
    r'\b(in \d+ hours?|within \d+ hours?)\b',
    re.IGNORECASE
)

# 1-3 Day Deadline patterns (+20 points)
# Matches: in X days, within X days, deadline
DAY_PATTERN = re.compile(
    r'\b(in \d+ days?|within \d+ days?|deadline)\b',
    re.IGNORECASE
)

# Week-Level Deadline patterns (+10 points)
# Matches: this week, within 7 days
WEEK_PATTERN = re.compile(
    r'\b(this week|within 7 days)\b',
    re.IGNORECASE
)

# Context-aware exclusion: words that should NOT trigger urgency when combined
# with legitimate context (product updates, newsletters, informational content)
URGENCY_EXCLUSION_PATTERNS = [
    re.compile(r'\b(new|latest|update|available|release|feature|announcement)\b.*\b(product|plan|release|update|version|feature|announcement)\b', re.IGNORECASE),
    re.compile(r'\b(product|plan|release|update|version|feature|announcement)\b.*\b(new|latest|update|available|release|feature|announcement)\b', re.IGNORECASE),
    re.compile(r'\b(weekly|monthly|daily|regular)\s+(update|digest|newsletter|summary|recap)\b', re.IGNORECASE),
]


def _has_urgency_exclusion_context(text: str) -> bool:
    """Check if text contains legitimate context that should exclude urgency scoring."""
    for pattern in URGENCY_EXCLUSION_PATTERNS:
        if pattern.search(text):
            return True
    return False



# ============================================================================
# URGENCY PREDICTION FUNCTIONS
# ============================================================================

# Important senders list - can be configured per user in production
IMPORTANT_SENDERS = [
    'manager', 'director', 'vp', 'vice president',
    'hr@', 'human resources',
    'it-security', 'security@',
    'finance@', 'accounting@',
    'ceo@', 'cfo@', 'cto@',
    'admin@', 'support@',
    'noreply@', 'no-reply@'
]

def calculate_urgency(subject: str, body: str, sender: str) -> Dict[str, Any]:
    """
    Calculate urgency score for an email.
    
    This is the main entry point that combines subject and body text,
    then uses the advanced regex-based urgency detection.
    
    Scoring Logic (from regex-based detection):
    - +40 points: Immediate pressure (immediately, urgent, asap, right now, act now)
    - +30 points: Short deadline (within 24/48 hours, expires today, by tomorrow)
    - +25 points: Hour mentions (in X hours, within X hours)
    - +20 points: Day deadline (in X days, within X days, deadline)
    - +10 points: Week deadline (this week, within 7 days)
    - +20 points: Sender in important sender list (legacy bonus)
    - Max score: 100
    
    Args:
        subject: Email subject line
        body: Email body content
        sender: Sender email address
    
    Returns:
        dict: {"urgency_score": int, "urgency_level": "High/Medium/Low"}
    """
    # Combine subject and body for analysis
    combined_text = f"{subject or ''} {body or ''}"
    sender_lower = sender.lower() if sender else ''
    
    # Use new regex-based urgency calculation
    # Start with base score from sender importance (legacy bonus)
    sender_bonus = _check_important_sender(sender_lower)
    
    # Calculate urgency using the new regex-based function
    urgency_score = _calculate_urgency_from_text(combined_text, base_score=sender_bonus)
    
    # Determine urgency level using the new function
    urgency_level = calculate_urgency_level(urgency_score)
    
    return {
        'urgency_score': urgency_score,
        'urgency_level': urgency_level
    }


def _check_important_sender(sender: str) -> int:
    """Check if sender is in important sender list. Returns 0-20."""
    if not sender:
        return 0
    
    for important in IMPORTANT_SENDERS:
        if important.lower() in sender:
            return 20
    return 0



# ============================================================================
# ADVANCED TIME-SENSITIVE URGENCY DETECTION
# ============================================================================

def _calculate_urgency_from_text(email_text: str, base_score: int = 0) -> int:
    """
    Calculate urgency score using regex-based time-sensitive pattern detection.
    
    This function analyzes email text for time-sensitive language and applies
    weighted boosts based on matched patterns.
    
    Pattern Categories and Weights:
    - Immediate Pressure (+40): immediately, urgent, asap, right now, act now
    - 24-48 Hour Deadline (+30): within 24/48 hours, expires today, by tomorrow, today only
    - Specific Hour Mentions (+25): in X hours, within X hours
    - 1-3 Day Deadline (+20): in X days, within X days, deadline
    - Week-Level Deadline (+10): this week, within 7 days
    
    Multiple categories can stack. Final score is capped at 100.
    
    Args:
        email_text: The email text to analyze (subject + body combined)
        base_score: Optional base score to add to (default: 0)
    
    Returns:
        Integer urgency score (0-100, non-negative)
    """
    # Handle edge cases
    if not email_text:
        return max(0, base_score)
    
    # Convert to lowercase for case-insensitive matching
    text_lower = email_text.lower()
    
    # Check if email has legitimate context that should reduce urgency scoring
    # (product updates, newsletters, informational content)
    has_exclusion_context = _has_urgency_exclusion_context(email_text)
    
    # Calculate total boost from pattern matches
    total_boost = 0
    
    # Check Immediate Pressure (+40) - always applies (truly urgent words)
    if IMMEDIATE_PRESSURE_PATTERN.search(text_lower):
        total_boost += 40
    
    # Check Short Deadline (+30) - reduced if exclusion context present
    if SHORT_DEADLINE_PATTERN.search(text_lower):
        total_boost += 15 if has_exclusion_context else 30
    
    # Check Hour Pattern (+25) - reduced if exclusion context present
    if HOUR_PATTERN.search(text_lower):
        total_boost += 12 if has_exclusion_context else 25
    
    # Check Day Pattern (+20) - reduced if exclusion context present
    if DAY_PATTERN.search(text_lower):
        total_boost += 10 if has_exclusion_context else 20
    
    # Check Week Pattern (+10) - reduced if exclusion context present
    if WEEK_PATTERN.search(text_lower):
        total_boost += 5 if has_exclusion_context else 10
    
    # Calculate final score
    final_score = base_score + total_boost
    
    # Cap at 100, ensure non-negative
    final_score = min(final_score, 100)
    final_score = max(0, int(final_score))
    
    return final_score


def calculate_urgency_level(score: int) -> str:
    """
    Determine urgency level based on score.
    
    Args:
        score: Urgency score (0-100)
    
    Returns:
        Urgency level: 'High', 'Medium', or 'Low'
    """
    if score >= 61:
        return 'High'
    elif score >= 41:
        return 'Medium'
    else:
        return 'Low'


# ============================================================================
# LEARNED KEYWORD MEMORY SYSTEM
# ============================================================================

# Common stopwords to filter out during keyword extraction
STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
    'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
    'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'need',
    'dare', 'ought', 'used', 'this', 'that', 'these', 'those', 'i',
    'you', 'he', 'she', 'it', 'we', 'they', 'what', 'which', 'who',
    'whom', 'whose', 'where', 'when', 'why', 'how', 'all', 'each',
    'every', 'both', 'few', 'more', 'most', 'other', 'some', 'such',
    'no', 'nor', 'not', 'only', 'own', 'same', 'so', 'than', 'too',
    'very', 'just', 'also', 'now', 'here', 'there', 'then', 'once',
    'if', 'about', 'into', 'through', 'during', 'before', 'after',
    'above', 'below', 'between', 'under', 'again', 'further', 'then',
    'email', 'mail', 'message', 'subject', 'body', 'send', 'sender',
    'please', 'thank', 'thanks', 'dear', 'hello', 'hi', 'regards',
    'best', 'sincerely', 'would', 'could', 'should', 'get', 'got',
    'like', 'just', 'know', 'want', 'think', 'see', 'make', 'way',
    'time', 'year', 'people', 'thing', 'way', 'day', 'way', 'back',
    'come', 'take', 'still', 'well', 'even', 'want', 'give', 'look',
    'your', 'our', 'their', 'my', 'his', 'her', 'its', 'been', 'being'
}

# Punctuation to remove
PUNCTUATION = '!@#$%^&*()_+-=[]{}|;:\'",.<>?/\\~`'


def extract_keywords_from_text(text: str, min_length: int = 4) -> list:
    """
    Extract meaningful keywords from email text.
    
    Processing steps:
    1. Lowercase the text
    2. Remove punctuation
    3. Remove words shorter than min_length characters
    4. Remove common stopwords
    5. Keep top meaningful words (most frequent)
    
    Args:
        text: The email text (subject + body)
        min_length: Minimum word length to keep (default: 4)
    
    Returns:
        List of extracted keywords (unique, cleaned)
    """
    if not text or not isinstance(text, str):
        return []
    
    try:
        # Lowercase
        text = text.lower()
        
        # Remove punctuation
        for char in PUNCTUATION:
            text = text.replace(char, ' ')
        
        # Split into words
        words = text.split()
        
        # Filter words
        filtered_words = []
        for word in words:
            # Remove remaining non-alphabetic characters
            word = ''.join(c for c in word if c.isalpha())
            
            # Skip short words and stopwords (use all() for Unicode-safe check)
            if len(word) >= min_length and word not in STOPWORDS and all(c.isalpha() for c in word):
                filtered_words.append(word)
        
        # Count word frequencies
        word_freq = {}
        for word in filtered_words:
            word_freq[word] = word_freq.get(word, 0) + 1
        
        # Sort by frequency and return top words
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        
        # Return top 10 most frequent meaningful words
        return [word for word, freq in sorted_words[:10]]
    
    except Exception as e:
        print(f"Error extracting keywords: {str(e)}")
        return []


def learn_keywords_from_email(subject: str, body: str, category: str, db_session, confidence: float = 1.0) -> int:
    """
    Learn keywords from phishing or malware emails.
    
    When an email is classified as phishing or malware, extract meaningful
    words from subject + body and store them in the LearnedKeyword table.
    
    COOLDOWN FIX: Only learns from high-confidence predictions (> 80%) to
    prevent feedback loops where misclassified emails poison the keyword memory.
    
    Args:
        subject: Email subject line
        body: Email body content
        category: Email category (phishing or malware)
        db_session: SQLAlchemy database session
        confidence: Classification confidence (0.0-1.0)
    
    Returns:
        Number of keywords learned
    """
    from models.email_model import LearnedKeyword
    
    # Only learn from phishing and malware emails
    if category not in ['phishing', 'malware']:
        return 0
    
    # COOLDOWN: Only learn from high-confidence predictions to prevent feedback loops
    if confidence < 0.80:
        return 0
    
    try:
        # Combine subject and body for extraction
        combined_text = f"{subject or ''} {body or ''}"
        
        # Extract keywords
        keywords = extract_keywords_from_text(combined_text)
        
        if not keywords:
            return 0
        
        learned_count = 0
        
        for keyword in keywords:
            # Get or create the keyword entry
            existing_keyword = db_session.query(LearnedKeyword).filter_by(
                keyword=keyword
            ).first()
            
            if existing_keyword:
                # Increment frequency
                existing_keyword.frequency += 1
                
                # Update weight based on frequency (cap at 20)
                # Base weight is 5, increases by 1 for each occurrence above 5
                existing_keyword.weight = min(5 + (existing_keyword.frequency - 1), 20)
                
                # Update category if different
                if existing_keyword.category != category:
                    existing_keyword.category = category
            else:
                # Create new learned keyword
                new_keyword = LearnedKeyword(
                    keyword=keyword,
                    category=category,
                    frequency=1,
                    weight=5
                )
                db_session.add(new_keyword)
            
            learned_count += 1
        
        # Commit the changes
        db_session.commit()
        
        return learned_count
    
    except Exception as e:
        print(f"Error learning keywords: {str(e)}")
        db_session.rollback()
        return 0


def apply_adaptive_keyword_boost(subject: str, body: str, risk_score: int, db_session) -> int:
    """
    Apply adaptive keyword boost to risk score based on learned keywords.
    
    When analyzing any new email, check if it contains stored learned keywords
    from previous phishing/malware emails and add their weights to the risk score.
    
    Args:
        subject: Email subject line
        body: Email body content
        risk_score: Current risk score
        db_session: SQLAlchemy database session
    
    Returns:
        Updated risk score with keyword boost (capped at 100)
    """
    from models.email_model import LearnedKeyword
    
    try:
        # Combine subject and body
        combined_text = f"{subject or ''} {body or ''}".lower()
        
        if not combined_text:
            return risk_score
        
        # Get all learned keywords from database
        learned_keywords = db_session.query(LearnedKeyword).all()
        
        if not learned_keywords:
            return risk_score
        
        # Calculate keyword boost
        keyword_boost = 0
        
        for learned_kw in learned_keywords:
            # Check if keyword appears in the email text
            if learned_kw.keyword in combined_text:
                keyword_boost += learned_kw.weight
        
        # Cap keyword-based boost at 20
        keyword_boost = min(keyword_boost, 20)
        
        # Apply boost to risk score
        new_risk_score = risk_score + keyword_boost
        
        # Cap final risk score at 100
        new_risk_score = min(new_risk_score, 100)
        
        return new_risk_score
    
    except Exception as e:
        print(f"Error applying keyword boost: {str(e)}")
        return risk_score


def build_risk_breakdown(base_score: int, ml_score: int, urgency_boost: int, 
                         keyword_boost: int, sender_boost: int, 
                         matched_keywords: List[str], flags: List[str]) -> Dict[str, Any]:
    """
    Build a structured risk breakdown object.
    
    This function creates a comprehensive breakdown of how the final risk score
    was calculated, including each component's contribution.
    
    Args:
        base_score: Base risk score from ML classifier
        ml_score: ML confidence-based score
        urgency_boost: Urgency detection boost
        keyword_boost: Learned keyword boost
        sender_boost: Sender reputation boost
        matched_keywords: List of matched learned keywords
        flags: List of detection flags
    
    Returns:
        Dictionary with risk breakdown structure
    """
    # Calculate final score ensuring it doesn't exceed 100
    final_score = min(base_score + ml_score + urgency_boost + keyword_boost + sender_boost, 100)
    
    breakdown = {
        "base_score": base_score,
        "ml_score": ml_score,
        "urgency_boost": urgency_boost,
        "keyword_boost": keyword_boost,
        "sender_boost": sender_boost,
        "final_score": final_score,
        "matched_keywords": matched_keywords,
        "flags": flags
    }
    
    return breakdown


def generate_explanation_summary(risk_breakdown: Dict[str, Any], category: str, 
                                  urgency_level: str, subject: str = '', 
                                  body: str = '', confidence: float = 0) -> str:
    """
    Generate a 30-40 word explanation of why the email was classified as it was.
    Focuses on reasoning and detected indicators rather than generic classification statements.
    
    Args:
        risk_breakdown: The risk breakdown dictionary
        category: Email category (phishing, malware, spam, legitimate, promotion, newsletter)
        urgency_level: Urgency level (High, Medium, Low)
        subject: Email subject line
        body: Email body content
        confidence: Classification confidence score (0-100)
    
    Returns:
        Human-readable explanation string (30-40 words)
    """
    flags = risk_breakdown.get('flags', [])
    matched_kw = risk_breakdown.get('matched_keywords', [])
    urgency_boost = risk_breakdown.get('urgency_boost', 0)
    keyword_boost = risk_breakdown.get('keyword_boost', 0)
    sender_boost = risk_breakdown.get('sender_boost', 0)
    
    # Build concise explanation based on category and detected indicators
    explanation_parts = []
    
    # Category-specific explanations (concise, ~20-25 words base)
    if category == 'phishing':
        explanation_parts.append("This email exhibits deceptive characteristics associated with fraudulent attempts to obtain sensitive information.")
        if 'urgency' in flags or urgency_boost > 20:
            explanation_parts.append("It contains urgent requests demanding immediate action, a classic phishing pressure tactic.")
        elif 'suspicious_link' in flags:
            explanation_parts.append("Suspicious links detected that may direct to fraudulent sites stealing credentials.")
        elif 'credential_request' in flags:
            explanation_parts.append("It requests sensitive information like passwords under false pretenses.")
        elif matched_kw:
            keywords_text = ', '.join(matched_kw[:2])
            explanation_parts.append(f"Language patterns include '{keywords_text}', frequently used in phishing deception.")
        else:
            explanation_parts.append("Multiple risk indicators suggest fraudulent information gathering intent.")
            
    elif category == 'malware':
        explanation_parts.append("This email contains suspicious content associated with malicious software distribution attempts.")
        if 'attachment' in flags:
            explanation_parts.append("File attachments may contain executable code designed to install malware when opened.")
        elif 'executable' in flags:
            explanation_parts.append("Executable content detected that could compromise security by installing viruses or ransomware.")
        elif matched_kw:
            keywords_text = ', '.join(matched_kw[:2])
            explanation_parts.append(f"Technical terms like '{keywords_text}' suggest harmful software distribution attempts.")
        else:
            explanation_parts.append("Multiple indicators suggest potential malicious software distribution intent.")
            
    elif category == 'spam':
        explanation_parts.append("This email matches patterns typical of unsolicited bulk messaging recipients find unwanted.")
        if 'bulk_marketing' in flags:
            explanation_parts.append("Content shows mass-distributed marketing characteristics sent without recipient consent.")
        elif 'promotional' in flags:
            explanation_parts.append("Promotional language indicates commercial spam intent throughout the message.")
        elif matched_kw:
            keywords_text = ', '.join(matched_kw[:2])
            explanation_parts.append(f"Keywords like '{keywords_text}' suggest bulk commercial intent.")
        else:
            explanation_parts.append("Multiple indicators point to unwanted bulk email characteristics.")
            
    elif category == 'promotion':
        explanation_parts.append("This email contains marketing material and commercial messaging advertising products or services.")
        if 'sales_offer' in flags:
            explanation_parts.append("Time-limited offers and discounts encourage immediate purchase decisions.")
        elif 'product_promotion' in flags:
            explanation_parts.append("Product promotion language focuses on commercial advertising rather than personal communication.")
        elif matched_kw:
            keywords_text = ', '.join(matched_kw[:2])
            explanation_parts.append(f"Marketing terms like '{keywords_text}' reinforce promotional nature.")
        else:
            explanation_parts.append("Commercial content indicates promotional marketing communication intent.")
            
    elif category == 'newsletter':
        explanation_parts.append("This email appears to be subscription-based content distribution or periodic information digest.")
        if 'subscription' in flags:
            explanation_parts.append("Structure suggests origin from mailing list recipient previously opted into.")
        elif 'digest' in flags:
            explanation_parts.append("Content organization shows compiled updates typical of newsletter formats.")
        elif matched_kw:
            keywords_text = ', '.join(matched_kw[:2])
            explanation_parts.append(f"Terms like '{keywords_text}' align with newsletter distribution patterns.")
        else:
            explanation_parts.append("Content structure indicates legitimate periodic information distribution.")
            
    else:  # legitimate
        explanation_parts.append("This email displays characteristics consistent with normal, trustworthy communication.")
        if 'known_sender' in flags or sender_boost > 10:
            explanation_parts.append("Sender is identified as known contact with established reputation.")
        elif 'normal_communication' in flags:
            explanation_parts.append("Content structure and tone align with standard professional communication patterns.")
        elif sender_boost < -10:
            explanation_parts.append("Historical sender analysis shows pattern of legitimate communications.")
        elif matched_kw:
            explanation_parts.append("Normal correspondence elements indicate professional communication etiquette.")
        else:
            explanation_parts.append("Multiple factors support trustworthy legitimate email classification.")
    
    # Add urgency context if significant (only high urgency)
    if urgency_boost > 20:
        explanation_parts.append("High-urgency language increases risk as time-pressure bypasses scrutiny.")
    
    # Add sender reputation if significantly negative
    if sender_boost > 15:
        explanation_parts.append("Sender has poor reputation history with suspicious content instances.")
    
    # Combine parts
    explanation = " ".join(explanation_parts)
    
    # Ensure 30-40 word range
    words = explanation.split()
    
    # If too short (less than 30 words), add brief enhancement
    if len(words) < 30:
        enhancements = {
            'phishing': "Classification based on pattern recognition and threat intelligence analysis.",
            'malware': "Classification based on threat detection algorithms analyzing code patterns.",
            'spam': "Classification based on filtering algorithms analyzing bulk distribution patterns.",
            'promotion': "Classification based on marketing content and commercial language detection.",
            'newsletter': "Classification based on content structure and subscription pattern analysis.",
            'legitimate': "Classification based on sender verification and authenticity analysis."
        }
        enhancement = enhancements.get(category, "")
        if enhancement:
            explanation = explanation + " " + enhancement
    
    # If still too short, add another sentence
    words = explanation.split()
    if len(words) < 30:
        explanation = explanation + " This determination considers multiple risk factors and behavioral indicators."
    
    # If too long (more than 40 words), truncate
    if len(words) > 40:
        truncated_words = words[:40]
        explanation = " ".join(truncated_words)
        if not explanation.endswith('.'):
            explanation = explanation.rsplit(' ', 1)[0] + '.'
    
    return explanation


def get_matched_learned_keywords(subject: str, body: str, db_session) -> List[str]:
    """
    Get list of matched learned keywords from an email.
    
    Args:
        subject: Email subject
        body: Email body
        db_session: SQLAlchemy database session
    
    Returns:
        List of matched keyword strings
    """
    from models.email_model import LearnedKeyword
    
    try:
        combined_text = f"{subject or ''} {body or ''}".lower()
        
        if not combined_text:
            return []
        
        # Get all learned keywords
        learned_keywords = db_session.query(LearnedKeyword).all()
        
        matched = []
        for learned_kw in learned_keywords:
            if learned_kw.keyword in combined_text:
                matched.append(learned_kw.keyword)
        
        return matched[:10]  # Limit to top 10 matches
    
    except Exception as e:
        print(f"Error getting matched keywords: {str(e)}")
        return []
