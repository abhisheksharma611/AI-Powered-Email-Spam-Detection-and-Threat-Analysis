# -*- coding: utf-8 -*-
"""Report content, part 3: Chapter 4 sections 4.8-4.16."""

PART3 = [
    ("sec", "4.8", "Adaptive Intelligence"),
    ("body",
     "Two database-backed feedback loops personalise the filter. "
     "learn_keywords_from_email extracts salient terms from "
     "high-risk mail and records them in learned_keywords with a "
     "frequency and a weight that starts at 5; later messages "
     "containing those terms receive the capped boost described "
     "above, and 232 keywords had already been learned in the "
     "deployment database at the time of writing. "
     "update_sender_reputation maintains per-sender totals, "
     "phishing/malware/spam/high-urgency counts, a composite "
     "reputation score, and a risk level for each of the 95 tracked "
     "senders, letting repeat offenders accumulate risk while known "
     "good senders earn trust."),
    ("body",
     "The sender reputation algorithm operates as follows: every "
     "time a message is classified, its sender address is looked up "
     "in the sender_reputation table. If the sender is new, a row is "
     "inserted with total_emails = 1 and the appropriate category "
     "counter incremented (spam_count, phishing_count, or "
     "malware_count). If the sender already exists, the counters "
     "are updated and the reputation score is recomputed as a "
     "weighted sum: reputation = (spam_count * 2 + phishing_count * "
     "5 + malware_count * 10 + high_urgency_count * 1) / "
     "total_emails * 100, clamped to [0, 100]. A sender with a "
     "reputation above 60 is flagged as 'high' risk; between 30 and "
     "60 as 'medium'; below 30 as 'low'. High-risk senders receive "
     "a +20 point penalty added to the risk score of all subsequent "
     "messages from that address, implemented in _calc_risk as "
     "a conditional branch on sender_reputation.risk_level."),
    ("body",
     "The learned keyword system operates on a similar per-row "
     "basis: each keyword carries a frequency (how many messages "
     "contained it) and a weight (initialised at 5, incremented by "
     "1 for each additional high-risk message). The boost applied "
     "to a new message is min(weight, 20), ensuring no single "
     "keyword can dominate the risk score. Keywords are extracted "
     "using a simple frequency analysis of the top 50 non-stopword "
     "terms in each high-risk message, deduplicated against the "
     "existing learned_keywords table, and committed after each "
     "message in the batch loop."),

    ("sec", "4.8.1", "Adaptive Intelligence Configuration"),
    ("table",
     ["Parameter", "Value", "Source"],
     [["Initial keyword weight", "5", "helpers.py learn_keywords_from_email"],
      ["Max keyword boost", "20 points", "predictor.py _calc_risk"],
      ["Sender reputation penalty", "+20 points", "predictor.py _calc_risk"],
      ["High-risk sender threshold", "reputation > 60",
       "helpers.py update_sender_reputation"],
      ["Medium-risk sender threshold", "30 < reputation <= 60",
       "helpers.py update_sender_reputation"],
      ["Spam weight in reputation", "2x", "helpers.py update_sender_reputation"],
      ["Phishing weight in reputation", "5x", "helpers.py update_sender_reputation"],
      ["Malware weight in reputation", "10x", "helpers.py update_sender_reputation"]],
     "Table 4.6: Adaptive Config"),

    ("sec", "4.9", "Data Collection and Description"),
    ("body",
     "The training corpus combines the SpamAssassin public corpus, "
     "the Enron organisational mail archive, CEAS 2008 and Kaggle "
     "phishing collections, and curated newsletter and marketing "
     "archives. After deduplication and length filtering, "
     "final_training_dataset.csv holds 30,028 labelled messages with "
     "the class distribution shown in Figure 4.2; the imbalance "
     "toward Spam and Not Spam mirrors real inboxes, and balanced "
     "class weights in the ensemble compensate during training. The "
     "same 3,003-message test split is shared by both training "
     "pipelines so that RoBERTa and the ensemble are evaluated on "
     "identical data."),
    ("body",
     "Class-wise: 8,360 spam, 7,191 legitimate, 6,770 promotion, 4,539 malware, 5,852 newsletter, 6,288 phishing. Figure 5.1 shows the imbalance balanced by class weights."),
    ("diag", "diagrams/new_dataset_chart.png",
     "Figure 4.2: Dataset Distribution"),
    ("sec", "4.9.1", "Dataset Audit Script"),
    ("body",
     "A standalone dataset_audit.py script (run via python "
     "dataset_audit.py) inspects final_training_dataset.csv at "
     "training time and writes audit/dataset_audit_report.txt and "
     "audit/audit_summary.json. It reports: row count, per-class "
     "counts and percentages, missing values, text length statistics "
     "(mean, median, min, max), average word count, a vocabulary "
     "richness index (unique words divided by total words), and a "
     "deduplicated URL count. This audit is the canonical source for "
     "the 30,028-row figure cited throughout the report and ensures "
     "that training data statistics remain reproducible."),

    ("sec", "4.9.2", "Engineered Features and Preprocessing Steps"),
    ("table",
     ["#", "Preprocessing step", "Purpose"],
     [["1", "Lower-casing", "Normalises casing to reduce vocabulary size"],
      ["2", "URL masking (URL, http/https)", "Prevents URL patterns from dominating TF-IDF"],
      ["3", "Email masking (EMAIL)", "Removes personal addresses from features"],
      ["4", "Phone masking (PHONE)", "Prevents numeric phone patterns from leaking"],
      ["5", "Repeated punctuation collapse", "Normalises !!!!!! to a single !"],
      ["6", "Repeated whitespace collapse", "Removes artefact spacing"],
      ["7", "Non-alpha character removal", "Strips residual digits and special characters"],
      ["8", "TF-IDF vectorisation",
       "Converts cleaned text to sparse numeric representation"],
      ["9", "Engineered feature concatenation",
       "Appends length, ratios, flags, urgency score"]],
     "Table 4.7: Preprocessing Steps"),

    ("sec", "4.9.3", "Dataset Size Justification"),
    ("body",
     "The 30,028-message corpus is deliberately sized to balance "
     "training fidelity against available compute. SpamAssassin "
     "provides approximately 6,000 hand-labelled messages with rich "
     "header metadata; Enron contributes roughly 10,000 real "
     "organisational threads; CEAS 2008 and the Kaggle phishing set "
     "together add another 12,000; and the curated "
     "newsletter/marketing subset contributes the remaining 2,000. "
     "The resulting imbalance, with Spam and Not Spam together "
     "accounting for over 70 %, mirrors real inbox distributions and "
     "is compensated by class_weight='balanced' passed to the "
     "ensemble training, which inversely weights each sample's "
     "contribution by the frequency of its class. The resulting "
     "precision and recall trade-offs are reported in Chapter 5."),

    ("sec", "4.10", "Data Processing"),
    ("sec", "4.10.1", "Overall Processing"),
    ("body",
     "Raw Gmail payloads are base64url-decoded, MIME parts are "
     "walked recursively, HTML is stripped with BeautifulSoup for "
     "analysis, and displayed bodies are sanitised with bleach, "
     "which whitelists formatting tags, strips script, iframe, and "
     "event-handler attributes, and removes javascript: and "
     "vbscript: URLs."),
    ("sec", "4.10.2", "Textual Processing"),
    ("body",
     "The shared preprocess_text function lowercases text, masks "
     "URLs, e-mail addresses, and phone numbers with placeholder "
     "tokens, collapses repeated punctuation and whitespace, and "
     "removes residual non-alphabetic characters. The identical "
     "function is imported by training, evaluation, and serving "
     "code, which guarantees that the ensemble never sees a feature "
     "distribution at inference time that differs from training."),
    ("sec", "4.10.3", "Feature Formation"),
    ("body",
     "The ensemble consumes the TF-IDF sparse vector concatenated "
     "with the six engineered features, while RoBERTa consumes the "
     "cleaned text through its sub-word tokenizer; the two "
     "representations are complementary, lexical versus contextual, "
     "and meet only at the fusion step."),

    ("sec", "4.11", "Proposed Model"),
    ("body",
     "The proposed model is the dual-path architecture shown in "
     "Figure 4.3. Both paths score every message independently; "
     "their probabilities are fused 0.90/0.10; and the adaptive "
     "signals of urgency, reputation, and learned keywords adjust "
     "the risk score without altering the category decision. The "
     "end-to-end data flow and the operational workflow are shown "
     "in Figures 4.4 and 4.5."),
    ("diag", "diagrams/new_pipeline.png",
     "Figure 4.3: Dual-Model Pipeline"),
    ("diag", "diagrams/new_data_flow.png",
     "Figure 4.4: Data Flow"),
    ("diag", "diagrams/new_workflow.png",
     "Figure 4.5: Workflow"),
    ("sec", "4.11.1", "Model Singleton Management"),
    ("body",
     "Both RoBERTa and the ensemble are loaded once at module import "
     "time in predictor.py and cached in module-level variables "
     "(_roberta_model and _ensemble_model). This singleton pattern "
     "avoids repeated loading of the 476 MB transformer artifact and "
     "the 192 MB ensemble pickle on every request. If either artifact "
     "is missing from disk, the corresponding singleton remains None, "
     "and the predictor falls back to the other engine; if both are "
     "missing, the system returns uniform probabilities across all "
     "six categories, ensuring that the application never crashes "
     "due to a missing model file. The singleton approach is "
     "appropriate for the single-worker Heroku deployment; a "
     "multi-worker setup would require a shared model cache or "
     "preloading via Gunicorn's preload_app option."),

    ("sec", "4.11.2", "Ensemble Class Probabilities"),
    ("body",
     "Each of the five ensemble models (Multinomial Naive Bayes, "
     "Logistic Regression, Random Forest, Gradient Boosting, and "
     "Multi-Layer Perceptron) produces a probability vector of "
     "length six. The ensemble_train module averages these five "
     "vectors element-wise to produce the ensemble probability "
     "average. This soft-voting approach is preferred over hard "
     "voting because it preserves confidence information: if four "
     "models are uncertain but one is highly confident, the average "
     "still reflects the confident model's signal. The "
     "classification confidence is then computed as the maximum "
     "value of the averaged probability vector, and the predicted "
     "category is the argmax index."),

    ("sec", "4.11.3", "Risk Score Composition"),
    ("body",
     "The risk score (0-100) is composed of three additive layers. "
     "The base layer assigns a category-specific default from the "
     "BASE_RISKS dictionary in predictor.py: safe mail starts at 5, "
     "newsletters at 15, promotions at 25, spam at 50, phishing at "
     "75, and malware at 90. The model-confidence layer adjusts "
     "this base by multiplying by a scaling factor derived from the "
     "fusion confidence: high-confidence predictions receive a "
     "modest upward adjustment (up to +10 points for confidence "
     "above 0.95), while low-confidence predictions receive a "
     "downward adjustment (down to -10 points for confidence below "
     "0.60). The adaptive layer adds the urgency score (0-40 from "
     "the regex patterns), the sender reputation penalty (0-20 from "
     "sender_reputation.risk_level), and the learned keyword boost "
     "(0-20 from the keyword frequency and weight). The final score "
     "is clamped to [0, 100]."),

    ("sec", "4.11.4", "Fallback Strategy"),
    ("body",
     "The fallback strategy in predictor.py follows a three-tier "
     "degradation path. If both RoBERTa and the ensemble are "
     "available, the fused 0.90/0.10 prediction is used. If RoBERTa "
     "fails or is missing, the ensemble prediction is used alone "
     "(weight 1.0). If the ensemble fails or is missing, RoBERTa "
     "prediction is used alone. If both fail, uniform probabilities "
     "are returned with category 'Unknown' and confidence 0.0. In "
     "all cases, the risk score is still computed from whatever "
     "category is available, and the adaptive layer (urgency, "
     "reputation, keywords) always runs regardless of model "
     "availability. This ensures that the system degrades "
     "gracefully rather than crashing, and that the risk assessment "
     "remains partially functional even when both models are "
     "unavailable."),

    ("sec", "4.12", "Database Design"),
    ("body",
     "Persistence uses four SQLAlchemy tables defined in "
     "email_model.py and evolved through five Alembic revisions "
     "(learned_keywords table, sender_reputation table, "
     "risk_breakdown column, an updated_at trigger fix, and not-null "
     "constraints on sender counters). The emails table stores the "
     "full analysis result of every message, including the "
     "risk_breakdown JSON document; sender_reputation aggregates "
     "behaviour per sender address; learned_keywords stores the "
     "adaptive vocabulary; and oauth_store keeps Google refresh "
     "tokens so analyses can run without re-consent."),
    ("diag", "diagrams/new_er.png",
     "Figure 4.6: ER Diagram"),
    ("table",
     ["Revision", "Migration name", "Schema change"],
     [["001", "add_learned_keywords_table",
       "Creates learned_keywords (id, keyword, frequency, weight, "
       "first_seen, last_seen)."],
      ["002", "add_sender_reputation_table",
       "Creates sender_reputation with sender_email, "
       "total_emails, spam_count, phishing_count, reputation_score, "
       "risk_level, first_seen, last_seen."],
      ["003", "add_risk_breakdown_column",
       "Adds emails.risk_breakdown (JSON) to store per-score details."],
      ["004", "update_sender_reputation_timestamps",
       "Adds updated_at column to sender_reputation with onupdate "
       "trigger for last-seen tracking."],
      ["005", "add_sender_counter_constraints",
       "Sets NOT NULL constraints on spam_count, phishing_count, "
       "total_emails and initialises defaults."]],
     "Table 4.8: Migrations (5)"),

    ("sec", "4.13", "User Interface (UI)"),
    ("body",
     "The interface consists of eleven Jinja2 templates inheriting "
     "from a common base: landing, login, dashboard, results, last "
     "scan, analytics, email detail, threat console, manual text "
     "analysis, and styled 404/500 error pages. Bootstrap 5.3 and "
     "Font Awesome supply the layout and iconography, a 55 KB custom "
     "stylesheet applies the colour system (green for safe, yellow "
     "for caution, red for danger), and main.js drives Chart.js "
     "rendering, table filtering, and progress polling of the "
     "analysis task registry. Timestamps are localised to the "
     "user's timezone with an IST default."),
    ("table",
     ["View", "Route", "HTTP method", "Template", "Purpose"],
     [["Dashboard", "/", "GET", "dashboard.html", "Overview statistics and charts"],
      ["Last scan", "/last_scan", "GET", "last_scan.html", "Most recent analysis batch"],
      ["Results", "/results", "GET", "results.html", "Filterable message table"],
      ["Analytics", "/analytics", "GET", "analytics.html", "Risk/category distribution charts"],
      ["Threat console", "/threat_console", "GET", "threat_console.html",
       "Ranked dangerous messages and senders"],
      ["Manual analysis", "/manual_analysis", "GET", "manual_analysis.html",
       "Paste text for one-off classification"],
      ["Email detail", "/email/<id>", "GET", "email_detail.html", "Per-message explanation view"],
      ["Text API", "/api/analyze_text", "POST", "JSON", "Returns predicted category + risk"],
      ["Bulk API", "/bulk_analyze", "POST", "JSON", "Batch classify pasted messages"],
      ["Export", "/export/email/<id>", "GET", "download", "Download single-message report"]],
     "Table 4.9: Routes (12)"),

    ("body",
     "The interface workflow from authentication through threat analysis is illustrated "
      "in the Results chapter screenshots (Figures 7.1–7.8)."),

    ("sec", "4.14", "Working Procedure"),
    ("body",
     "In operation the user signs in with Google, clicks Start "
     "Analysis, and the server spawns a background thread that "
     "fetches the fifty most recent messages, classifies each one "
     "through the full pipeline, and updates a progress percentage "
     "that the frontend polls. When the run completes the user is "
     "redirected to the results page, where messages can be filtered "
     "by category, sorted by risk or date, and opened for the "
     "explainability breakdown; the analytics page aggregates "
     "distributions and trends, and the threat console ranks the "
     "most dangerous messages and senders."),
    ("body",
     "The Start Analysis button is disabled via main.js once a "
     "session is active, preventing overlapping background threads. "
     "A stop-analysis endpoint cancels the current run by setting a "
     "threading.Event flag, causing the processing loop to break "
     "after the current message completes. The task registry "
     "dictionaries in-memory (analysis_tasks and stop_flags) keep "
     "progress and state for the current user; no external message "
     "broker is required because the workload is single-user."),

    ("sec", "4.15", "Authentication and Authorisation"),
    ("body",
     "Authentication is implemented through Authlib's OAuth 2.0 "
     "client using Google's discovery document. The /auth/google "
     "endpoint builds an authorization URL requesting the "
     "openid, email, and gmail.readonly scopes; the "
     "/callback/google endpoint exchanges the authorization code for "
     "a token, fetches the user profile from Google's userinfo "
     "endpoint, and stores the refresh token in the oauth_store "
     "table keyed by user_email. Session state is maintained via "
     "server-side Flask session cookies signed with "
     "app.config['SECRET_KEY']. Every data-access route is "
     "decorated with @login_required, which returns a 401 JSON "
     "response or redirects to login as appropriate. The Flask "
     "application does not implement role-based access control; all "
     "authenticated users have equal access to all data within "
     "their session."),

    ("sec", "4.16", "Background Task Engine"),
    ("body",
     "The background analysis runs in a daemon thread spawned by "
     "threading.Thread. It iterates over batches of twenty Gmail "
     "messages obtained from gmail_client.get_recent_emails, "
     "classifies each batch through the full pipeline (text "
     "extraction, preprocessing, RoBERTa inference, ensemble "
     "inference, fusion, risk scoring, adaptive enrichment), and "
     "commits results to the database. After each batch the thread "
     "writes a progress dict to the analysis_tasks registry with "
     "keys total, processed, category_counts, risk_counts, and "
     "status. A cancellation flag stored in the stop_flags "
     "dictionary is checked after each batch; when set, the thread "
     "breaks the loop and marks the status as 'stopped'. "
     "Intermittent Gmail 429 responses trigger exponential backoff "
     "with a maximum of three retries per batch before the batch is "
     "skipped and the skip count is appended to the progress dict."),
]
