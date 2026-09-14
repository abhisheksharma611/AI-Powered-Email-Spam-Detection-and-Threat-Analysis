# -*- coding: utf-8 -*-
"""Report content, part 2: Chapter 3 and Chapter 4 sections 4.1-4.6."""

PART2 = [
    # ==================================================================
    ("chapter", "3", "System Overview and Requirements"),
    # ==================================================================
    ("body",
     "The system is a monolithic but modular Flask application "
     "organised into an application layer (app.py, approximately 1,419 "
     "lines), a machine-learning package (models/), a service and "
     "utility package (utils/), a persistence layer (SQLAlchemy models "
     "plus Alembic migrations), and a presentation layer (templates/ "
     "and static/). The technology stack is summarised below."),

    ("sec", "3.1", "Technology Stack"),
    ("table",
     ["Layer", "Technology", "Role"],
     [["Web framework", "Flask 2.3.3, Werkzeug 2.3.7",
       "HTTP routes, sessions, error pages"],
      ["ORM / migrations", "Flask-SQLAlchemy 3.0.5, Flask-Migrate 4.0.5",
       "Schema management, versioned Alembic migrations"],
      ["Auth / mail", "Authlib 1.2.1, google-api-python-client 2.100.0",
       "Google OAuth 2.0 and Gmail API v1"],
      ["Deep learning", "PyTorch 2.0-2.2, Transformers 4.30-4.36",
       "RoBERTa-base fine-tuned classifier"],
      ["Classical ML", "scikit-learn 1.2-1.6, XGBoost 1.7.6, joblib",
       "TF-IDF ensemble training and serving"],
      ["Text processing", "NLTK 3.8.1, BeautifulSoup4, bleach, TextBlob",
       "Cleaning, MIME parsing, sanitisation"],
      ["Data", "pandas 2.0.3, numpy 1.24.3, SQLite emails.db",
       "Dataset handling and persistence"],
      ["Frontend", "Bootstrap 5.3, Font Awesome, Chart.js",
       "Dashboard, analytics, threat console"],
      ["Deployment", "Gunicorn 21.2.0, Procfile, Python 3.11.9",
       "Production serving (deploy.sh for Heroku)"]],
     "Table 3.1: Tech Stack"),

    ("sec", "3.2", "Module Architecture"),
    ("body",
     "app.py hosts every HTTP route, the per-IP rate limiter, the "
     "background analysis engine, sender-reputation updates, and "
     "timezone localisation helpers (IST by default, overridable per "
     "user via a cookie). The models package contains the SQLAlchemy "
     "definitions (email_model.py), the RoBERTa wrapper "
     "(roberta_model.py) with a module-level singleton, the ensemble "
     "training script (ensemble_train.py), the transformer training "
     "script (roberta_train.py), the shared evaluation harness "
     "(evaluate.py), and the fusion predictor (predictor.py). "
     "models/utils/preprocessing.py is the single source of truth for "
     "text cleaning and engineered features. The utils package "
     "provides auth.py (Authlib OAuth registration), gmail_client.py "
     "(a 505-line Gmail API wrapper), helpers.py (724 lines covering "
     "sanitisation, urgency scoring, keyword learning, and risk "
     "breakdown assembly), and ai_explanation.py. The migrations "
     "package records five schema revisions."),

    ("sec", "3.3", "Data Flow Overview"),
    ("body",
     "End to end, data moves as follows: the user authenticates via "
     "Google OAuth 2.0 with the openid, email, profile, and "
     "gmail.readonly scopes, and the resulting refresh token is "
     "stored in the oauth_store table; starting an analysis spawns a "
     "background thread that fetches the fifty most recent messages "
     "in batches of twenty with retry-on-429 backoff; each message is "
     "parsed from MIME into headers, body, and snippet; the cleaned "
     "text is scored by RoBERTa and the ensemble; the fused "
     "probabilities determine the category and confidence; urgency "
     "regexes, sender reputation, and learned-keyword boosts are "
     "added to produce the final risk score and breakdown; the row is "
     "persisted, sender statistics are updated, and the interface "
     "renders results, analytics, and the threat console from the "
     "database."),

    ("sec", "3.4", "Functional Requirements"),
    ("table",
     ["ID", "Functional requirement", "Implementation"],
     [["FR-1", "Authenticate users with Google OAuth 2.0",
       "auth.py + /auth/google + /callback/google"],
      ["FR-2", "Fetch the user's recent Gmail messages",
       "gmail_client.py get_recent_emails (batches of 20)"],
      ["FR-3", "Classify each message into six categories",
       "predictor.py fused RoBERTa + ensemble"],
      ["FR-4", "Compute a 0-100 risk score with level and urgency",
       "predictor.py _calc_risk + helpers.calculate_urgency"],
      ["FR-5", "Persist results and adaptive state",
       "email_model.py tables via SQLAlchemy"],
      ["FR-6", "Provide dashboard, results, analytics, threat console",
       "Flask routes + Jinja2 templates + Chart.js"],
      ["FR-7", "Allow manual text analysis without mail access",
       "/api/analyze_text endpoint"],
      ["FR-8", "Support bulk analysis of pasted message sets",
       "/bulk_analyze endpoint"]],
     "Table 3.2: Functional Requirements"),

    ("sec", "3.5", "Non-Functional Requirements"),
    ("body",
     "The non-functional requirements realised in the codebase are: "
     "performance - a fifty-message batch completing in under about one "
     "minute on CPU, achieved through batched transformer inference and "
     "model singletons; security - OAuth-only authentication, security "
     "headers on every response, whitelist-based HTML sanitisation with "
     "bleach, and per-endpoint rate limiting; reliability - retry-on-429 backoff "
     "for Gmail, per-message error skipping, and dual-model fallback; "
     "maintainability - a single shared preprocessing module, versioned "
     "Alembic migrations, and a Procfile/Gunicorn deployment recipe; and "
     "usability - a responsive Bootstrap 5.3 interface with "
     "colour-coded risk levels and timezone-correct localisation."),

    ("sec", "3.6", "Hardware and Software Requirements"),
    ("table",
     ["Component", "Requirement", "Rationale"],
     [["Python", "3.11.9 (pinned in runtime.txt)", "Reproducible runtime"],
      ["Flask", "2.3.3", "Lightweight web framework"],
      ["PyTorch", "2.6.0+cpu", "RoBERTa inference; CPU-only deployment"],
      ["Transformers", "4.36.2", "RoBERTa tokenizer and model loading"],
      ["SQLAlchemy", "2.0.36", "ORM for email_model.py tables"],
      ["Alembic", "1.14.1", "Schema version control"],
      ["Gunicorn", "23.0.0", "Production WSGI server"],
      ["Authlib", "1.3.2", "Google OAuth 2.0 integration"],
      ["bcrypt", "4.2.1", "Password hashing (admin panel)"],
      [" bleach", "6.2.0", "HTML sanitisation whitelist"],
      ["Chart.js", "4.4.7 (CDN)", "Analytics visualisation"],
      ["Heroku-22", "Heroku stack", "Deployment target via Procfile"]],
     "Table 3.3: HW/SW Requirements"),

    ("sec", "3.7", "Deployment Architecture"),
    ("body",
     "The application runs as a single Gunicorn process on Heroku-22 "
     "(gunicorn app:app --timeout 300 on 0.0.0.0:$PORT). Procfile, "
     "runtime.txt (Python 3.11.9) and deploy.sh install dependencies, "
     "run the train-and-evaluate pipeline once, and start the server. "
     "Model artefacts (RoBERTa 476 MB, Ensemble 192 MB) are loaded "
     "once and cached in memory."),

    ("sec", "3.8", "Preprocessing Engine"),
    ("body",
     "The shared preprocess_text in models/utils/preprocessing.py "
     "lower-cases text, masks URLs/emails/phones as URL/EMAIL/PHONE, "
     "collapses repeated punctuation/whitespace and strips non-alpha "
     "characters. A pre-fitted TF-IDF vectoriser (tfidf_vectorizer.pkl) "
     "vectorises the cleaned text; six engineered features are "
     "appended for the ensemble, while RoBERTa receives only the "
     "cleaned text via its own tokenizer."),

    # ==================================================================
    ("chapter", "4", "System Design and Implementation"),
    # ==================================================================
    ("sec", "4.1", "System Architecture"),
    ("body",
     "The architecture separates presentation, application, service, "
     "ML engine, and data layers, with two external services (Google "
     "OAuth and the Gmail API) at the boundary. Each layer "
     "communicates only with its neighbours: routes call utilities, "
     "utilities call the predictor, and the predictor reads the model "
     "artefacts, while routes and utilities read the database through "
     "the ORM. This layering keeps the transformer, the ensemble, and "
     "the storage engine independently replaceable."),
    ("diag", "diagrams/new_architecture.png",
     "Figure 4.1: System Architecture"),

    ("sec", "4.2", "Modules and Explanation"),
    ("body",
     "The application entry point app.py defines the Flask factory, "
     "registers Flask-Login with a session-backed user loader, "
     "installs security headers on every response, and wires the "
     "timezone helpers. The analysis engine is built from three "
     "cooperating functions: run_analysis orchestrates fetch, parse, "
     "and classification; _process_single_email applies urgency "
     "scoring, model prediction, risk assembly, keyword learning, and "
     "persistence for one message; and _run_analysis_bg wraps the run "
     "in a cancellable background task whose progress and errors are "
     "published to a lock-protected task registry polled by the "
     "frontend. update_sender_reputation maintains the per-sender "
     "aggregates after every classified message, and "
     "_ensure_refresh_token transparently refreshes expired Google "
     "credentials using the stored oauth_store row."),
    ("body",
     "The models package serves two artefact families: "
     "best_roberta_model.pth (476 MB of fine-tuned transformer "
     "weights) and the ensemble trio ensemble_model.joblib (192 MB), "
     "vectorizer.joblib (1.3 MB TF-IDF vocabulary), and "
     "encoder.joblib (label mapping). predictor.py lazily loads both "
     "families at first use and reports which sources contributed to "
     "each prediction through a model_source field, enabling the "
     "graceful-degradation behaviour described in Section 4.6."),

    ("sec", "4.3", "Web Routes and API Endpoints"),
    ("body",
     "The table below lists the principal routes implemented in "
     "app.py together with their rate limits. Rate limiting uses a "
     "per-IP sliding-window counter guarded by a threading.Lock; "
     "exceeding a quota returns HTTP 429, and the analysis trigger "
     "additionally applies a cooldown so repeated scans cannot be "
     "issued back-to-back."),
    ("table",
     ["Endpoint", "Method", "Purpose", "Limit"],
     [["/ and /login", "GET", "Landing and login pages", "-"],
      ["/auth/google, /callback/google", "GET",
       "OAuth 2.0 redirect and callback", "-"],
      ["/dashboard, /analytics, /results", "GET",
       "Authenticated summary, charts, results", "-"],
      ["/admin/threat-console", "GET",
       "Threat timeline and top offenders", "-"],
      ["/api/start_analysis", "POST",
       "Spawn background inbox analysis", "5/min + cooldown"],
      ["/analyze_emails, /bulk_analyze", "GET/POST",
       "Synchronous and bulk analysis", "10/min, 5/min"],
      ["/api/analyze_text", "POST",
       "Classify pasted text (max 5,000 chars)", "10/min"],
      ["/email/<id>", "GET", "Single-email detail view", "20/min"],
      ["/api/last_scan, /api/status, /health", "GET",
       "Task polling and health checks", "30/min"]],
     "Table 4.1: Route Map"),

    ("sec", "4.4", "RoBERTa Transformer Classifier"),
    ("body",
     "RoBERTa-base fine-tuned for six-way classification on the "
     "80/10/10 stratified split (24,022/3,003/3,003, seed 42) with "
     "AdamW (2e-5, decay 0.01), effective batch 16, linear warmup, "
     "six epochs with early stopping and deterministic seeding. "
     "Sequences truncated to 256 tokens. At serving, weights load "
     "once as a singleton, torch pinned to four CPU threads with "
     "batched inference (4 CPU /16 GPU) and AMP when available."),

    ("sec", "4.5", "Ensemble Classification"),
    ("body",
     "Cleaned text is TF-IDF vectorised (20k features, 1-2 grams, "
     "sublinear TF, English stopwords) plus six engineered features "
     "(length, special-char and capital ratios, URL/phone flags, "
     "urgency score). Five estimators trained in parallel are combined "
     "by soft VotingClassifier averaging calibrated probabilities."),
    ("table",
     ["Member", "Key hyper-parameters"],
     [["Multinomial Naive Bayes", "alpha = 0.1"],
      ["Logistic Regression",
       "multinomial, lbfgs, balanced class weights, max_iter 1000"],
      ["Random Forest", "100 trees, balanced class weights"],
      ["Gradient Boosting", "100 stages, default learning rate"],
      ["MLPClassifier", "hidden layers 128-64, max_iter 600"]],
     "Table 4.2: Ensemble Members"),
    ("body",
     "The engineered block appended to every TF-IDF row is computed on "
     "the raw, unprocessed text by extract_engineered_features and "
     "captures stylistic signals that vocabulary statistics cannot "
     "express."),
    ("table",
     ["#", "Engineered feature", "Definition"],
     [["1", "Normalised length", "len(text) / 500, clipped to 1.0"],
      ["2", "Special-character ratio",
       "count of !@#$%^&*() etc. divided by length"],
      ["3", "Capital-letter ratio", "uppercase count divided by length"],
      ["4", "URL present flag", "1 if an http(s) or www pattern exists"],
      ["5", "Phone-number present flag",
       "1 if a 3-3-4 digit pattern exists"],
      ["6", "Urgency vocabulary score",
       "matches among 13 trigger words, divided by 5"]],
     "Table 4.3: Engineered Features"),

    ("sec", "4.6", "Fusion, Risk and Urgency Scoring"),
    ("body",
     "The urgency scorer reads the unprocessed text with a fixed set of "
     "regular expression patterns defined in calculate_urgency and "
     "assigns a normalised weighted sum across all detected triggers. "
     "Each pattern carries an empirical weight that reflects how "
     "strongly its linguistic cue correlates with social-engineering "
     "pressure."),
    ("table",
     ["#", "Pattern category", "Regex cue", "Weight"],
     [["1", "Time pressure",
       "\\burgent|immediately|deadline|act now\\b", "40"],
      ["2", "Suspense",
       "\\battention|important|please note\\b", "30"],
      ["3", "Curiosity",
       "\\bsecret|exclusive|restricted\\b", "25"],
      ["4", "Suspicion",
       "\\bwarning|verify|unusual activity\\b", "20"],
      ["5", "Pressure",
       "\\bdon't delay|final notice|last chance\\b", "10"]],
     "Table 4.4: Urgency Patterns"),
    ("body",
     "Fusion is performed after both engines have returned their "
     "independent probability vectors. RoBERTa's output vector and the "
     "ensemble probability average are multiplied by the fixed weights "
     "0.90 and 0.10 respectively; the winning index of the fused vector "
     "is the category label, and its value is reported as the model "
     "confidence. Risk scores are not additive across categories; "
     "instead, each category maps to its own risk formula — "
     "Category 0 (safe mail) reads from SAFE_RISK only, Category 4 "
     "(promotion) reads from CATEGORIES[4] risk entry, and so on — "
     "ensuring that high-confidence spam always triggers a high risk "
     "regardless of the underlying transformer score."),

    ("sec", "4.7", "Base Risks and Historical Baselines"),
    ("body",
     "predictor.py fuses the two probability vectors with fixed "
     "weights of 0.90 for RoBERTa and 0.10 for the ensemble; if "
     "either model fails to load, the other is used alone and the "
     "model_source field records the fallback (roberta+ensemble, "
     "roberta, ensemble, or uniform). The risk score starts from a "
     "per-category base severity, multiplies by the winning "
     "confidence, halves when confidence falls below 0.25, and is "
     "clamped to 0-100; levels map to Low below 41, Medium from 41 "
     "to 60, and High at 61 and above. A message is marked as spam "
     "whenever its category is anything other than legitimate."),
    ("table",
     ["Category", "Base risk", "Display name"],
     [["legitimate", "5", "Not Spam"],
      ["newsletter", "10", "Newsletter"],
      ["promotion", "30", "Promotion"],
      ["spam", "50", "Spam"],
      ["phishing", "80", "Phishing"],
      ["malware", "85", "Malware"]],
     "Table 4.5: Base Risks"),
    ("body",
     "Urgency detection in helpers.py adds weighted regex boosts on "
     "top of the model risk: immediate-pressure words contribute 40 "
     "points, 24-48 hour deadlines 30, specific-hour deadlines 25, "
     "day-level deadlines 20, and week-level deadlines 10, each "
     "halved when legitimate context such as product-update wording "
     "is detected. Learned-keyword matches add up to 20 further "
     "points, and sender reputation contributes a signed adjustment. "
     "build_risk_breakdown stores every component in a JSON column so "
     "the interface can show exactly why a score was assigned, and "
     "generate_explanation_summary converts the numbers into plain "
     "language."),
    ("body",
     "Historical baselines are calibrated from the 39,000-message "
     "training distribution: majority-class priors anchor base risks, "
     "while per-sender aggregates in sender_reputation shift thresholds "
     "for repeat offenders. This keeps phishing from unknown senders "
     "at High even at moderate confidence, while familiar legitimate "
     "senders are down-weighted without retraining."),
]
