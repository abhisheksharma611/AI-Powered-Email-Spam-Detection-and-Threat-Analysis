# -*- coding: utf-8 -*-
"""Phase-2 report builder (SP2-honest subset).

Reuses sp2phase2/report_builder.py design untouched; only CONTENT is filtered
and SP5-specific claims are patched to SP2 truth. Mains outside sp2phase2/
are never touched. Output: ../sp2reportforphase2.pdf
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from content_part1 import PART1
from content_part2 import PART2
from content_part3 import PART3
from content_part4 import PART4
from content_part5 import PART5

DIAG_DIR = os.path.join(os.path.dirname(HERE), "diagrams")
OUT_PDF = os.path.join(os.path.dirname(HERE), "sp2reportforphase2.pdf")

CONTENT_ORIG = PART1 + PART2 + PART3 + PART4

# ---- Split logic (same as build_new_report.py) ----
ch_idx = [i for i, it in enumerate(CONTENT_ORIG) if it[0] == "chapter"]
CH1_CH2 = CONTENT_ORIG[ch_idx[0]:ch_idx[2]]
CH3 = CONTENT_ORIG[ch_idx[2]:ch_idx[3]]
ch4_start, ch4_end = ch_idx[3], ch_idx[4]
ch4_content = CONTENT_ORIG[ch4_start:ch4_end]
impl_start = next(i for i, it in enumerate(ch4_content) if it[0] == "sec" and it[1] == "4.4")
design_b_start = next(i for i, it in enumerate(ch4_content) if it[0] == "sec" and it[1] == "4.12")
DESIGN_A = ch4_content[1:impl_start]
IMPL = ch4_content[impl_start:design_b_start]
DESIGN_B = ch4_content[design_b_start:]
TESTING = CONTENT_ORIG[ch_idx[5]:]

DESIGN = [("chapter", "4", "System Design")]
DESIGN.extend(it for it in DESIGN_A if it[0] != "chapter")
remap_design = {"4.12": "4.4", "4.13": "4.5", "4.14": "4.6", "4.15": "4.7", "4.16": "4.8"}
for it in DESIGN_B:
    if it[0] == "sec":
        DESIGN.append(("sec", remap_design.get(it[1], it[1]), it[2]))
    elif it[0] == "diag" and "Figure 4.6" in it[2]:
        DESIGN.append((it[0], it[1], it[2].replace("Figure 4.6", "Figure 4.2")))
    elif it[0] == "table":
        cap = it[3] if len(it) > 3 else ""
        cap = cap.replace("Table 4.8:", "Table 4.2:").replace("Table 4.9:", "Table 4.3:")
        DESIGN.append((it[0], it[1], it[2], cap) if cap else it)
    elif it[0] == "body":
        txt = it[1].replace("Figures 5.1\u20137.8", "Figures 7.1\u20137.8").replace("Figures 5.1-5.8", "Figures 7.1-7.8")
        DESIGN.append(("body", txt))
    else:
        DESIGN.append(it)

IMPL_CH = [("chapter", "5", "Implementation")]
remap_impl = {
    "4.4": "5.1", "4.5": "5.2", "4.6": "5.3", "4.7": "5.4",
    "4.8": "5.5", "4.8.1": "5.5.1", "4.9": "5.6", "4.9.1": "5.6.1",
    "4.9.2": "5.6.2", "4.9.3": "5.6.3", "4.10": "5.7", "4.10.1": "5.7.1",
    "4.10.2": "5.7.2", "4.10.3": "5.7.3", "4.11": "5.8", "4.11.1": "5.8.1",
    "4.11.2": "5.8.2", "4.11.3": "5.8.3", "4.11.4": "5.8.4",
}
table_map_impl = {
    "Table 4.2:": "Table 5.1:", "Table 4.3:": "Table 5.2:", "Table 4.4:": "Table 5.3:",
    "Table 4.5:": "Table 5.4:", "Table 4.6:": "Table 5.5:", "Table 4.7:": "Table 5.6:",
}
for it in IMPL:
    kind = it[0]
    if kind == "sec":
        new = remap_impl.get(it[1])
        IMPL_CH.append(("sec", new, it[2]) if new else it)
    elif kind == "diag":
        cap = it[2]
        cap = cap.replace("Figure 4.2:", "Figure 5.1:").replace("Figure 4.3:", "Figure 5.2:")
        cap = cap.replace("Figure 4.4:", "Figure 5.3:").replace("Figure 4.5:", "Figure 5.4:")
        IMPL_CH.append((it[0], it[1], cap))
    elif kind == "table":
        cap = it[3] if len(it) > 3 else ""
        for o, n in table_map_impl.items():
            cap = cap.replace(o, n)
        IMPL_CH.append((it[0], it[1], it[2], cap) if cap else it)
    elif kind == "body":
        txt = it[1]
        txt = txt.replace("Figure 4.2", "Figure 5.1").replace("Figure 4.3", "Figure 5.2")
        txt = txt.replace("Figure 4.4", "Figure 5.3").replace("Figure 4.5", "Figure 5.4")
        txt = txt.replace("Table 4.2", "Table 5.1").replace("Table 4.3", "Table 5.2")
        txt = txt.replace("Table 4.4", "Table 5.3").replace("Table 4.5", "Table 5.4")
        txt = txt.replace("Table 4.6", "Table 5.5").replace("Table 4.7", "Table 5.6")
        IMPL_CH.append(("body", txt))
    else:
        IMPL_CH.append(it)

TESTING_CH = list(TESTING)
if TESTING_CH and TESTING_CH[0][0] == "chapter":
    TESTING_CH[0] = ("chapter", "6", "Testing and Security Analysis")

# ---- Phase-2 keep sets (final numbers) ----
DESIGN_KEEP = {"4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7", "4.8"}
IMPL_KEEP = {"5.1", "5.2", "5.3", "5.4", "5.5", "5.5.1", "5.6", "5.6.1", "5.6.2", "5.6.3",
             "5.7", "5.7.1", "5.7.2", "5.7.3"}
TEST_KEEP = {"6.1", "6.2", "6.2.1", "6.2.2", "6.2.3"}
DROP_BODY_ANCHORS = [
    "illustrated in the Results chapter screenshots",  # 4.5 second body
    "The Start Analysis button is disabled via main.js",  # 4.6 cancel body
    "Data contracts are also unit-tested.",  # 6.2.1 second body
    "Non-functional system tests cover failover:",  # 6.2.3 second body
]
DROP_TABLE_CAPS = ["Table 6.2:", "Table 6.3:"]  # OWASP, footprint (keep 5.5 adaptive cfg for Phase-2)
KEEP_DIAG_CAPS = ("Figure 4.1:", "Figure 4.2:", "Figure 5.1:")


def _filter(block, keep):
    out, cur = [], None
    for it in block:
        if it[0] == "chapter":
            out.append(it)
            cur = None
        elif it[0] == "sec":
            cur = it[1]
            if cur in keep:
                out.append(it)
        elif it[0] in ("body", "diag", "table"):
            if cur is not None and cur not in keep:
                continue
            if it[0] == "body" and any(a in it[1] for a in DROP_BODY_ANCHORS):
                continue
            if it[0] == "table":
                cap = it[3] if len(it) > 3 else ""
                if any(cap.startswith(d) for d in DROP_TABLE_CAPS):
                    continue
            if it[0] == "diag" and not it[2].startswith(KEEP_DIAG_CAPS):
                continue
            out.append(it)
        else:
            out.append(it)
    return out


DESIGN = _filter(DESIGN, DESIGN_KEEP)
IMPL_CH = _filter(IMPL_CH, IMPL_KEEP)
TESTING_CH = _filter(TESTING_CH, TEST_KEEP)

# ---- Body replacements (whole-body, anchored) ----
REPLACEMENTS = [
    ("classifies every message into one of six categories - Spam, Not Spam, Promotion, Malware, Newsletter, and Phishing - using a hybrid",
     "Electronic mail remains the most widely used channel of digital communication, and it is also the primary delivery vehicle for unsolicited bulk mail, phishing campaigns, and malware. Conventional mailbox filters separate mail into fixed folders and offer the user little insight into why a message was treated as dangerous. This project, developed for the academic year 2026-2027, presents an AI-Powered Email Spam Detection and Threat Analysis system that connects to a user's Gmail account through Google OAuth 2.0, retrieves recent messages through the Gmail API v1, and classifies every message into one of six categories - Spam, Not Spam, Promotion, Malware, Newsletter, and Phishing - using a classical machine-learning ensemble built on TF-IDF and engineered features, with a RoBERTa transformer branch under experimental integration. Every prediction is accompanied by a 0-100 risk score, a risk level, and an urgency estimate."),
    ("Adaptive intelligence mechanisms - sender reputation tracking and learned-keyword memory stored in the database - allow the filter to personalise",
     "The system is delivered as a Flask web application backed by SQLAlchemy and SQLite, with a responsive Bootstrap interface that provides a dashboard, sortable results, analytics charts, and a basic threat console. Sender reputation tracking and learned-keyword memory stored in the database begin personalising the filter to an individual inbox. The pipeline from OAuth consent to scored prediction runs in a background thread, so a fifty-message inbox scan completes in roughly one minute on CPU."),
    ("The specific objectives realised by this project are:",
     "The specific objectives for Phase 2 are: (i) to integrate Gmail securely through OAuth 2.0 using the read-only scope; (ii) to train a classical ensemble on TF-IDF and engineered features for six-way email classification on a 39,000-message corpus; (iii) to attach a risk score combining model confidence, category severity, urgency detection, and sender history; and (iv) to present results through a dashboard, results table, analytics charts, and a basic threat console. Transformer-based classification with RoBERTa is integrated as an experimental branch and completes in Phase 3."),
    ("The scope covers the full pipeline: OAuth authentication, Gmail retrieval in rate-limit-safe batches, MIME parsing and HTML sanitisation, dual-model inference, multi-factor risk assembly, persistence in four database tables, and presentation across eleven Jinja2 templates.",
     "The scope covers the working pipeline: OAuth authentication, Gmail retrieval in batches, MIME parsing and HTML sanitisation, ensemble inference, risk assembly, persistence in database tables, and presentation across nine Jinja2 templates."),
    ("The proposed solution pairs two independent classification engines with a behavioural enrichment layer.",
     "The solution pairs a classical classification engine with a behavioural enrichment layer. Raw message text is scored by a soft-voting ensemble of five machine learning models built on TF-IDF and engineered surface features, which is highly reliable on lexically distinctive content such as promotions and newsletters, while a RoBERTa transformer branch is being integrated to capture contextual deception cues such as impersonation phrasing. The decision is enriched by factors computed from database history and curated pattern matchers: an urgency score from time-pressure patterns, a sender reputation score from per-sender historical aggregates, and a learned-keyword boost from terms previously observed in risky mail. The result delivered to the user is a categorised threat assessment with its risk components, accessible through an authenticated web dashboard, an analytics view, and a basic threat console."),
    ("The remainder of this report is organised as follows. Chapter 2 surveys",
     "The remainder of this report is organised as follows. Chapter 2 surveys the IEEE literature that grounds and differentiates the project. Chapter 3 states the system requirements and the technology stack. Chapter 4 details the system design, covering architecture, routing, the database schema, and the user interface. Chapter 5 presents the Phase-2 implementation: the classification engines, risk scoring, data engineering, and processing pipeline. Chapter 6 defines the test plan executed in Phase 3 alongside full evaluation, security hardening, and deployment."),
    ("The surveyed literature establishes strong binary detectors but leaves four gaps",
     "The surveyed literature establishes strong binary detectors but leaves four gaps that this project addresses directly: fine-grained six-class labelling that separates phishing and malware from ordinary spam; continuous risk scoring rather than a binary verdict; behavioural signals such as urgency phrasing, sender history, and learned keywords layered on top of content classification; and end-to-end integration with a real mailbox through OAuth rather than evaluation on static datasets alone. Full comparative evaluation completes in Phase 3."),
    ("app.py hosts every HTTP route, the per-IP rate limiter, the background analysis engine, sender-reputation updates, and timezone localisation helpers",
     "app.py (2,452 lines) hosts every HTTP route, the per-IP rate limiter, the background analysis engine, sender-reputation updates, and dashboard rendering. The models package holds the SQLAlchemy definitions (email_model.py), the experimental RoBERTa wrapper (roberta_classifier.py), the classical ensemble trainer and scorer (spam_detector.py), the dataset builder (build_improved_dataset.py), and the training scripts (train_model.py, evaluate_and_inference.py). The utils package provides auth.py (OAuth registration), gmail_client.py (a 215-line Gmail API wrapper), and helpers.py (680 lines covering sanitisation, urgency scoring, keyword learning, and risk assembly). The migrations package records four schema revisions."),
    ("The application entry point app.py defines the Flask factory, registers Flask-Login",
     "The application entry point app.py (2,452 lines) defines the Flask routes, session-backed login, the analysis engine, and dashboard rendering. The analysis engine fetches recent Gmail messages, parses MIME parts, scores each message with the classical ensemble, assembles urgency, risk, and reputation signals, learns keywords, and persists results. update_sender_reputation maintains per-sender aggregates after every classified message."),
    ("The models package serves two artefact families: best_roberta_model.pth (476 MB",
     "The models directory holds two artefact families: the classical trio (spam_model.pkl, spam_model_lr.pkl, tfidf_vectorizer.pkl) used by the working ensemble, and best_roberta_model.pth, the transformer weights for the experimental RoBERTa branch integrated in Phase 3."),
    ("The primary classifier is RoBERTa-base fine-tuned for six-way classification. Training used",
     "The experimental classifier is RoBERTa-base fine-tuned for six-way classification. The branch reuses stratified splits of the 39,000-message corpus with AdamW optimisation and sub-word tokenisation to a maximum length of 256. Integration with the serving path completes in Phase 3; Phase-2 classification results rest on the classical ensemble described in Section 5.2."),
    ("Fusion is performed after both engines have returned their independent probability vectors.",
     "The Phase-2 decision is produced by the classical ensemble described in Section 5.2; the experimental RoBERTa branch is fused in Phase 3. Risk scores are category-specific: each category maps to its own risk formula, ensuring that high-confidence phishing or malware always triggers a high risk regardless of secondary signals."),
    ("predictor.py fuses the two probability vectors with fixed weights of 0.90 for RoBERTa",
     "The risk score starts from a per-category base severity, scales with the winning confidence, and is clamped to 0-100; levels map to Low below 41, Medium from 41 to 60, and High at 61 and above. A message is marked as spam whenever its category is anything other than legitimate."),
    ("The training corpus combines the SpamAssassin public corpus,",
     "The training corpus in models/combined_all_datasets.csv holds 39,000 labelled messages across six classes, built by build_improved_dataset.py from public spam, organisational mail, phishing collections, and newsletter/marketing archives, and consolidated into models/final_training_dataset.csv. The class distribution is shown in Figure 5.1; the imbalance toward the majority classes mirrors real inboxes and is compensated with balanced class weights during training."),
    ("The 30,028-message corpus is deliberately sized",
     "The 39,000-message corpus balances training fidelity against available compute: the two largest classes contribute about 15,159 and 14,652 messages and the smallest about 6,607. Majority-class dominance mirrors real inbox distributions and is compensated by balanced class weights in ensemble training. Final precision and recall trade-offs are reported in Phase 3."),
    ("The interface consists of eleven Jinja2 templates inheriting from a common base:",
     "The interface consists of nine Jinja2 templates inheriting from a common base: landing, login, dashboard, results, analytics, threat console, and styled error pages. Bootstrap and Font Awesome supply the layout and iconography with a colour system (green for safe, yellow for caution, red for danger), and Chart.js renders the analytics distributions with table filtering on results."),
    ("The background analysis runs in a daemon thread spawned by threading.Thread.",
     "The background analysis runs in a daemon thread that iterates over Gmail message batches obtained from gmail_client.get_recent_emails, classifies each message through the pipeline (text extraction, cleaning, ensemble inference, risk scoring, adaptive enrichment), and commits results to the database. After each batch the thread updates a progress record polled by the frontend. No external message broker is required because the workload is single-user."),
    ("Unit tests target the pure, deterministic helpers in models/utils/preprocessing.py and utils/helpers.py.",
     "Unit testing targets the deterministic helpers in train_model.py and utils/helpers.py: text cleaning (lower-casing, URL/EMAIL/PHONE masking, punctuation collapse), engineered-feature extraction on synthetic strings, urgency scoring per pattern category, and risk composition across the six base severities. Tests are table-driven pytest parametrisations that run without model artefacts."),
    ("Integration tests wire two or more real modules with test doubles only at the external boundary.",
     "Integration testing wires real modules with test doubles only at the external boundary: the Gmail wrapper runs against canned MIME payloads, the ensemble scores through the committed vectoriser, and persistence tests verify result rows, sender aggregates, and keyword updates round-trip through SQLite."),
    ("System tests drive the running Flask application through its HTTP surface using the Flask test client",
     "System testing drives the running Flask application through its HTTP surface: the OAuth callback creates a session, /api/start_analysis progresses via /api/status, and a fifty-message run completes with every result row carrying a risk level. Full system-test evidence, rate-limit verification, and failover tests complete in Phase 3."),
]

# Small substring patches (surgical, keep surrounding text)
SUB_PATCHES = [
    ("approximately 1,419 lines", "approximately 2,452 lines"),
    ("the fusion predictor (predictor.py)", "the classification scripts (spam_detector.py, roberta_classifier.py)"),
    ("gmail_client.py (a 505-line Gmail API wrapper)", "gmail_client.py (a 215-line Gmail API wrapper)"),
    ("helpers.py (724 lines covering", "helpers.py (680 lines covering"),
    (", and ai_explanation.py.", "."),
    ("records five schema revisions", "records four schema revisions"),
    ("utilities call the predictor, and the predictor reads the model artefacts",
     "utilities call the classifier, and the classifier reads the model artefacts"),
    ("five Alembic revisions (learned_keywords table, sender_reputation table, risk_breakdown column, an updated_at trigger fix, and not-null constraints on sender counters)",
     "four Alembic revisions (learned_keywords table, sender_reputation table, risk_breakdown column, and an updated_at trigger fix)"),
    ("FR-3', 'Classify each message into six categories', 'predictor.py fused RoBERTa + ensemble",
     "FR-3', 'Classify each message into six categories', 'spam_detector.py ensemble (+ roberta_classifier.py experimental"),
    ("build_risk_breakdown stores every component in a JSON column so the interface can show exactly why a score was assigned, and generate_explanation_summary converts the numbers into plain language.",
     "Per-factor details are stored alongside each result so the interface can show why a score was assigned."),
    ("The Content-Security-Policy retains unsafe-inline", "PHASE2-DROP-MARKER"),
    ("30,028", "39,000"),
    ("39,000", "39,000"),
    ("95 tracked senders", "tracked senders"),
    ("232 keywords had already been learned", "keywords have been learned"),
    ("232 learned", "learned"),
    ("95 tracked", "tracked"),
]

T41_ROWS = [
    ["/ and /login", "GET", "Landing and login pages", "-"],
    ["/auth/<provider>, /callback/<provider>", "GET", "OAuth 2.0 redirect and callback", "-"],
    ["/dashboard, /analytics, /results", "GET", "Summary, charts, results table", "-"],
    ["/admin/threat-console", "GET", "Ranked dangerous messages and senders", "-"],
    ["/api/start_analysis", "POST", "Spawn background inbox analysis", "5/min"],
    ["/analyze_emails, /bulk_analyze", "GET/POST", "Single and bulk analysis", "10/min, 5/min"],
    ["/api/analyze_text", "POST", "Classify pasted text", "10/min"],
    ["/stats, /settings", "GET", "Corpus stats and app settings", "-"],
    ["/export_results/<format>", "GET", "Download results file", "-"],
    ["/api/last_scan, /api/status, /health", "GET", "Task polling and health checks", "30/min"],
]

T49_ROWS = [
    ["Dashboard", "/", "GET", "dashboard.html", "Overview statistics and charts"],
    ["Login", "/login", "GET", "login.html", "Google sign-in"],
    ["Results", "/results", "GET", "results.html", "Filterable message table"],
    ["Analytics", "/analytics", "GET", "analytics.html", "Risk/category distribution charts"],
    ["Threat console", "/admin/threat-console", "GET", "threat_console.html", "Ranked dangerous messages"],
    ["Text API", "/api/analyze_text", "POST", "JSON", "Returns predicted category + risk"],
    ["Bulk analysis", "/bulk_analyze", "GET/POST", "-", "Batch classify pasted messages"],
    ["Statistics", "/stats", "GET", "-", "Corpus statistics"],
    ["Settings", "/settings", "GET", "-", "Application settings"],
]

T61_ROWS = [
    ["TC-P1", "OAuth login and session", "Google test account", "Complete OAuth callback and open /dashboard", "Session created, dashboard renders", "Planned"],
    ["TC-P2", "Manual text classification", "Sample spam and ham texts", "POST /api/analyze_text with each sample", "Correct category with 0-100 risk", "Planned"],
    ["TC-P3", "Inbox analysis run", "Authenticated session, test mailbox", "POST /api/start_analysis, poll /api/status", "Progress reaches 100%, rows in /results", "Planned"],
]


def _apply(block):
    out = []
    for it in block:
        if it[0] == "body":
            txt = it[1]
            for old, new in SUB_PATCHES:
                if old in txt:
                    txt = txt.replace(old, new)
            replaced = False
            for anchor, new in REPLACEMENTS:
                if anchor in txt:
                    txt = new
                    replaced = True
                    break
            out.append(("body", txt))
        elif it[0] == "table":
            hdr, rows = it[1], it[2]
            cap = it[3] if len(it) > 3 else ""
            if hdr == ["Endpoint", "Method", "Purpose", "Limit"]:
                rows = T41_ROWS
            elif hdr == ["View", "Route", "HTTP method", "Template", "Purpose"]:
                rows, cap = T49_ROWS, "Table 4.3: Views and Routes (SP2)"
            elif hdr == ["Revision", "Migration name", "Schema change"]:
                rows = [r for r in rows if r[0] in ("001", "002", "003", "004")]
                cap = "Table 4.2: Migrations"
            elif hdr == ["ID", "Functional requirement", "Implementation"]:
                rows = [[c.replace("predictor.py fused RoBERTa + ensemble",
                                    "spam_detector.py ensemble (+ roberta_classifier.py experimental)").replace(
                    "/bulk_analyze endpoint", "/bulk_analyze endpoint (basic)").replace(
                    "predictor.py _calc_risk + helpers.calculate_urgency",
                    "spam_detector.py risk scoring + helpers.calculate_urgency") for c in r] for r in rows]
            elif hdr == ["Layer", "Technology", "Role"]:
                rows = [[c.replace("Production serving (deploy.sh for Heroku)",
                                    "Local development (deployment planned Phase 3)") for c in r] for r in rows]
            elif hdr[0] == "ID" and hdr[1] == "Test Objective":
                rows, cap = T61_ROWS, "Table 6.1: Phase-2 Test Plan"
            out.append((it[0], hdr, rows, cap) if cap else (it[0], hdr, rows))
        elif it[0] == "diag":
            base = os.path.basename(it[1])
            out.append((it[0], os.path.join(DIAG_DIR, base), it[2]))
        else:
            out.append(it)
    return out


DESIGN = _apply(DESIGN)
IMPL_CH = _apply(IMPL_CH)
TESTING_CH = _apply(TESTING_CH)
CH1_CH2 = _apply(CH1_CH2)
CH3 = _apply(CH3)

REFS = [it for it in PART5 if it[0] == "references"]
CONTENT = CH1_CH2 + CH3 + DESIGN + IMPL_CH + TESTING_CH + REFS


def check_no_dangling(content):
    bad = ["predictor.py", "ai_explanation", "Figure 5.2", "Figure 5.3", "Figure 5.4",
           "Figure 7.", "Table 7.", "Chapter 7", "uniform probabilities",
           "generate_explanation_summary", "stop_flags", "/email/<id>",
           "set-timezone", "30,028", "232 learned", "95 tracked",
           "approximately 98%", "PHASE2-DROP-MARKER"]
    hits = []
    for it in content:
        for part in it[1:]:
            if isinstance(part, str) and any(b in part for b in bad):
                hits.append((it[0], str(part)[:90]))
    return hits


if __name__ == "__main__":
    import json
    from report_builder import build_pdf
    dangling = check_no_dangling(CONTENT)
    if dangling:
        print("DANGLING REFS FOUND:")
        for h in dangling:
            print(" ", h)
        sys.exit(1)
    toc = json.load(open(os.path.join(HERE, "toc_phase2.json"))) if os.path.exists(
        os.path.join(HERE, "toc_phase2.json")) else None
    lof = json.load(open(os.path.join(HERE, "lof_phase2.json"))) if os.path.exists(
        os.path.join(HERE, "lof_phase2.json")) else None
    kwargs = {}
    if toc:
        kwargs["toc_entries"] = [tuple(t) for t in toc]
    if lof:
        kwargs["lof_entries"] = lof
    build_pdf(OUT_PDF, CONTENT, **kwargs)
    secs = [it[1] for it in CONTENT if it[0] == "sec"]
    print("Sections kept (%d): %s" % (len(secs), secs))
