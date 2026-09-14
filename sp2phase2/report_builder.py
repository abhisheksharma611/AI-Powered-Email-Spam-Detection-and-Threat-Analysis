# -*- coding: utf-8 -*-
"""Builds new_report.pdf from scratch.

Design (fonts, margins, orange-brown double-rule header/footer, chapter
layout) is identical to build_report.py / the sinchana reference PDF.
All CONTENT below is derived from an actual analysis of the project
files: app.py, config.py, models/*, utils/*, migrations/*, templates/*,
requirements.txt, deploy.sh, Procfile, runtime.txt, emails.db and
IEEE_Papers_Analysis.md. No facts are invented.
"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Image,
                                PageBreak, Table, TableStyle)
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ----------------------------------------------------------------------
# fonts (same as reference)
# ----------------------------------------------------------------------
def _load_times():
    reg = bold = None
    for p in [r"C:\Windows\Fonts\times.ttf",
              r"C:\Windows\Fonts\Times New Roman\Times New Roman.ttf"]:
        if os.path.exists(p):
            try:
                pdfmetrics.registerFont(TTFont("Times", p)); reg = "Times"
            except Exception:
                pass
            break
    if reg is None:
        reg = "Times-Roman"
    for p in [r"C:\Windows\Fonts\timesbd.ttf"]:
        if os.path.exists(p):
            try:
                pdfmetrics.registerFont(TTFont("TimesBd", p)); bold = "TimesBd"
            except Exception:
                pass
    if bold is None:
        bold = "Times-Bold"
    return reg, bold

REG, BOLD = _load_times()
MARGIN = 70
HDR_RGB = (0.773, 0.353, 0.067)  # Orange, Accent 2, Darker 50% #C55A11

styles = {
    "chapter": ParagraphStyle("chapter", fontName=BOLD, fontSize=16,
                              leading=20, spaceBefore=0, spaceAfter=14),
    "chapter_title": ParagraphStyle("chapter_title", fontName=BOLD, fontSize=16,
                                    leading=20, spaceBefore=0, spaceAfter=10,
                                    alignment=TA_CENTER),
    "sec": ParagraphStyle("sec", fontName=BOLD, fontSize=12, leading=20,
                          spaceBefore=10, spaceAfter=6),
    "body": ParagraphStyle("body", fontName=REG, fontSize=12, leading=20.5,
                           spaceAfter=8, alignment=TA_JUSTIFY),
    "cap": ParagraphStyle("cap", fontName=REG, fontSize=10, leading=13,
                           spaceBefore=3, spaceAfter=4, alignment=TA_CENTER),
    "tcell": ParagraphStyle("tcell", fontName=REG, fontSize=9, leading=11),
    "thead": ParagraphStyle("thead", fontName=BOLD, fontSize=9, leading=11,
                            textColor=colors.white),
    # Special styles for Conclusion / Future Scope / References
    "special_heading": ParagraphStyle("special_heading", fontName=BOLD,
                                      fontSize=16, leading=20,
                                      spaceBefore=0, spaceAfter=15,
                                      alignment=TA_CENTER),
    "special_body": ParagraphStyle("special_body", fontName=REG, fontSize=12,
                                   leading=20.5, spaceAfter=10,
                                   alignment=TA_JUSTIFY),
    "special_subhead": ParagraphStyle("special_subhead", fontName=BOLD,
                                      fontSize=12, leading=20,
                                      spaceBefore=12, spaceAfter=6),
    "ref_item": ParagraphStyle("ref_item", fontName=REG, fontSize=12,
                               leading=20.5, spaceAfter=6,
                               alignment=TA_LEFT,
                               leftIndent=0, firstLineIndent=0),
}

HEADER = "AI-POWERED EMAIL SPAM DETECTION AND THREAT ANALYSIS 2026-2027"
FOOTER = "Dept. Of CSE, CIT, Mandya"

# Pages that get the decorative border instead of standard header/footer.
# Populated during story building by scanning for special headings.
_special_border_pages = set()
# Set of story-item indices that are special headings (CONCLUSION, etc.)
_special_heading_indices = set()


class _BorderCanvas:
    """Wraps the ReportLab canvas.  Intercepts showPage() to apply
    the decorative border on pages whose number is in _special_border_pages,
    and the standard header/footer on all other pages."""

    def __init__(self, canvas):
        self._c = canvas

    def __getattr__(self, name):
        return getattr(self._c, name)

    def showPage(self):
        # doc.page was already incremented by the time showPage is called
        # during build, so doc.page - 1 is the page that was just rendered.
        page_that_was_rendered = self._c._doc.page - 1
        if page_that_was_rendered in _special_border_pages:
            _draw_border_page(self._c, self._c._doc)
        else:
            _decorate(self._c, self._c._doc)
        self._c.showPage()


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def _crop_whitespace(img_path):
    """Crop white border from screenshot to remove wasted space"""
    from PIL import Image, ImageChops
    try:
        im = Image.open(img_path).convert("RGB")
        bg = Image.new("RGB", im.size, (255,255,255))
        diff = ImageChops.difference(im, bg)
        bbox = diff.getbbox()
        if bbox:
            # Add 8px padding
            pad = 8
            bbox = (max(0,bbox[0]-pad), max(0,bbox[1]-pad), min(im.size[0], bbox[2]+pad), min(im.size[1], bbox[3]+pad))
            im_cropped = im.crop(bbox)
            # Save to temp path and return new path
            import tempfile, os
            tmp = os.path.join(tempfile.gettempdir(), "cropped_" + pathlib.Path(img_path).name)
            im_cropped.save(tmp)
            return tmp, im_cropped.size
        return img_path, im.size
    except Exception:
        from PIL import Image as PILI
        w,h = PILI.open(img_path).size
        return img_path, (w,h)


def _rect(c, x, top_from_top, width, height):
    c.rect(x, A4[1] - top_from_top, width, height, fill=1, stroke=0)


def _decorate(c, doc):
    """Standard header/footer for regular pages. Page number offset by _FRONT_PAGES."""
    c.saveState()
    w, h = A4
    c.setFillColorRGB(*HDR_RGB)
    _rect(c, MARGIN, 60.9, w - 2 * MARGIN, 0.8)
    _rect(c, MARGIN, 64.6, w - 2 * MARGIN, 3.0)
    c.setFillColorRGB(0, 0, 0)
    c.setFont(BOLD, 11)
    c.drawString(MARGIN, h - 55, HEADER.replace(" 2026-2027", ""))
    c.drawRightString(w - MARGIN, h - 55, "2026-2027")
    c.setFillColorRGB(*HDR_RGB)
    _rect(c, MARGIN, 765.7, w - 2 * MARGIN, 0.7)
    _rect(c, MARGIN, 769.5, w - 2 * MARGIN, 3.0)
    c.setFillColorRGB(0, 0, 0)
    c.setFont(BOLD, 11)
    c.drawString(MARGIN, 60, FOOTER)
    # show page - front offset so Chapter 1 page 1 is 1 not 6
    c.drawRightString(w - MARGIN, 60, str(doc.page - _FRONT_PAGES if doc.page > _FRONT_PAGES else doc.page))
    c.restoreState()


def _draw_corner_ornament(c, cx, cy, arm_len=25.92, thickness=1.08):
    """Draw an L-shaped corner ornament at (cx, cy) in page coords."""
    c.saveState()
    c.setFillColorRGB(0, 0, 0)
    # horizontal arm
    c.rect(cx, cy, arm_len, thickness, fill=1, stroke=0)
    # vertical arm
    c.rect(cx, cy, thickness, arm_len, fill=1, stroke=0)
    c.restoreState()


_FRONT_PAGES = 5  # Phase-2: Ack I, Abstract II, Contents III-IV (2 pages), LOF V
_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}
_ROMAN_EXT = {33: "XXXIII", 34: "XXXIV", 35: "XXXV"}

def _to_roman(n):
    vals = [(100,"C"),(90,"XC"),(50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]
    res=""
    for v,s in vals:
        while n>=v:
            res+=s
            n-=v
    return res or str(n)

def _draw_border_page(c, doc):
    """Double-line decorative border for front-matter + Conclusion/Future/References.
    Front-matter footer shows Roman I-V centered, chapter pages show Arabic page-FRONT_PAGES."""
    c.saveState()
    w, h = A4
    c.setStrokeColorRGB(*HDR_RGB)
    c.setLineWidth(3.0)
    c.rect(32, 32, w - 64, h - 64, fill=0, stroke=1)
    c.setStrokeColorRGB(*HDR_RGB)
    c.setLineWidth(1.0)
    c.rect(34.9, 34.9, w - 69.8, h - 69.8, fill=0, stroke=1)
    c.setFillColorRGB(0, 0, 0)
    c.setFont(BOLD, 11)
    if doc.page <= _FRONT_PAGES:
        c.drawCentredString(w / 2, 60, _ROMAN.get(doc.page, str(doc.page)))
    elif doc.page in _special_border_pages:
        # back special pages continue roman numerals after front matter
        _last = sorted([p for p in _special_border_pages if p > _FRONT_PAGES])
        if doc.page in _last:
            c.drawCentredString(w / 2, 60, _to_roman(_FRONT_PAGES + _last.index(doc.page)))
        else:
            arabic = doc.page - _FRONT_PAGES
            roman = _ROMAN.get(arabic) or _to_roman(arabic)
            c.drawCentredString(w / 2, 60, roman)
    else:
        # chapter pages: subtract front offset so Chapter 1 starts at 1
        c.drawCentredString(w / 2, 60, str(doc.page - _FRONT_PAGES))
    c.restoreState()


def _make_table(headers, rows):
    is_test_cases = headers[0] == "ID" and headers[1] == "Test Objective"
    if is_test_cases:
        from reportlab.lib.styles import ParagraphStyle as _PS
        small_thead = _PS("sm_thead", parent=styles["thead"], fontSize=7.5, leading=9.5)
        small_tcell = _PS("sm_tcell", parent=styles["tcell"], fontSize=7.5, leading=9.5)
        data = [[Paragraph(_esc(h), small_thead) for h in headers]]
        for r in rows:
            data.append([Paragraph(_esc(str(cell)), small_tcell) for cell in r])
    else:
        data = [[Paragraph(_esc(h), styles["thead"]) for h in headers]]
        for r in rows:
            data.append([Paragraph(_esc(str(cell)), styles["tcell"]) for cell in r])
    n = len(headers)
    tw = A4[0] - 2 * MARGIN
    # Identify table type by header text for targeted width optimization
    if headers == ["ID", "Functional requirement", "Implementation"]:
        # Table 3.2: Functional Requirements — IDs are tiny, impl refs are short
        widths = [tw * 0.15, tw * 0.45, tw * 0.40]
    elif headers == ["Component", "Requirement", "Rationale"]:
        # Table 3.3: HW/SW Requirements — rationales are the longest column
        widths = [tw * 0.25, tw * 0.35, tw * 0.40]
    elif n == 2:
        widths = [tw * 0.38, tw * 0.62]
    elif n == 3:
        if headers == ["#", "Preprocessing step", "Purpose"]:
            widths = [tw * 0.08, tw * 0.42, tw * 0.50]
        else:
            widths = [tw * 0.25, tw * 0.25, tw * 0.50]
    elif n == 4:
        widths = [tw * 0.22, tw * 0.38, tw * 0.25, tw * 0.15]
    elif n == 6:
        if headers[0] == "ID" and headers[1] == "Test Objective":
            widths = [tw * 0.06, tw * 0.16, tw * 0.16, tw * 0.30, tw * 0.22, tw * 0.10]
        else:
            widths = [tw / float(n)] * n
    else:
        widths = [tw / float(n)] * n
    rh = [28] + [22]*len(rows) if headers == ["Member", "Key hyper-parameters"] else [22]*len(data) if headers == ["#", "Preprocessing step", "Purpose"] else [20]+[46]*len(rows) if headers[0] == "ID" and headers[1] == "Test Objective" else None
    t = Table(data, colWidths=widths, repeatRows=1, rowHeights=rh)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(*HDR_RGB)),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.55, 0.35, 0.18)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.Color(0.99, 0.96, 0.92)]),
        ("LEFTPADDING", (0, 0), (-1, -1), 3 if is_test_cases else 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3 if is_test_cases else 5),
        ("TOPPADDING", (0, 0), (-1, -1), 2 if is_test_cases else 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2 if is_test_cases else 3),
    ]))
    return t


def _front_matter_ack():
    return [
        "Dreams never turn into reality unless a lot of efforts and hard work is put into it. It takes a lot "
        "of efforts to make your way to the goal and having someone to guide you and help you is "
        "always a blessing. So, we acknowledge all those whose guidance and encouragement served "
        "as a beacon light and crowned our efforts with success.",
        "We are thankful to <b>Dr. Srikantappa A S, Principal</b>, CIT, Mandya, for all the support he has "
        "rendered for providing us an opportunity to carry out our Major Project.",
        "We would like to thank <b>Prof. Arpitha S K, Assistant Professor and Head of Department</b>, Department of "
        "Computer Science and Engineering, for her support for the successful completion of our major project.",
        "We would like to sincerely thank our project co-ordinator, <b>Prof. Sahana G C, Assistant Professor</b>, Department of Computer Science and Engineering, CIT, Mandya, for her constant incitement and advice throughout the project.",
        "We would like to sincerely thank our internal guide <b>Prof. Chaithra N T, Assistant Professor</b>, Department of Computer Science and Engineering for providing relevant information, valuable guidance and encouragement to complete the Major Project.",
    ]

def _front_matter_abstract():
    return (
        "Electronic mail remains the dominant channel for official and personal communication, yet it is also the primary vector for phishing, malware, promotional flooding and bulk spam. Conventional binary filters separate mail into spam versus ham and give users little insight into why a message was flagged. This Phase-2 report presents the design and partial implementation of <b>AI-Powered Email Spam Detection and Threat Analysis</b>, a Flask-based system that connects to a user's Gmail mailbox through Google OAuth 2.0 (gmail.readonly scope) and classifies every message into six categories -- Spam, Not Spam, Promotion, Newsletter, Phishing and Malware -- using a classical TF-IDF ensemble of five models (Multinomial Naive Bayes, Logistic Regression, Random Forest, Gradient Boosting and MLP), with a RoBERTa-base transformer branch under experimental integration. "
        "Each prediction carries a 0-100 risk score with a Low/Medium/High level and an urgency estimate from curated patterns. Sender reputation tracking and learned-keyword memory stored in SQLite via SQLAlchemy begin personalising the filter. The pipeline -- OAuth consent, batched Gmail retrieval, MIME parsing and sanitisation, ensemble inference, risk assembly and persistence -- runs on a background thread, and a fifty-message scan completes in about a minute on CPU. A Bootstrap interface provides a dashboard, filterable results, analytics charts and a basic threat console. Full evaluation on a held-out split, security hardening, and production deployment complete in Phase 3."
    )

def build_pdf(path, CONTENT, include_front=True, toc_entries=None, lof_entries=None):
    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=64, rightMargin=64,
                            topMargin=65, bottomMargin=75,
                            title="AI-Powered Email Spam Detection and "
                                  "Threat Analysis 2026-2027")
    story = []
    # ---- Front matter with decorative border (same as last 3 special pages) ----
    if include_front:
        # Acknowledgment - page I (footer will show I)
        story.append(Paragraph("ACKNOWLEDGMENT", styles["special_heading"]))
        story.append(Spacer(1, 6))
        for para in _front_matter_ack():
            # para contains <b> tags for names - preserve them
            safe = para.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("&lt;b&gt;",f'<font name="{BOLD}">').replace("&lt;/b&gt;","</font>")
            story.append(Paragraph(safe, styles["special_body"]))
        story.append(Spacer(1, 20))
        sig_style = ParagraphStyle("sig", parent=styles["special_body"], alignment=TA_RIGHT, fontName=BOLD, leftIndent=0, spaceBefore=18, leading=14)
        story.append(Paragraph("Abhishek Kumar T [4CA23CS002]<br/>Javeriya Khan [4CA23CS048]<br/>Keerthana R [4CA23CS059]", sig_style))
        story.append(PageBreak())
        # Abstract - page II (footer will show II)
        story.append(Paragraph("ABSTRACT", styles["special_heading"]))
        story.append(Spacer(1, 6))
        _abs = _front_matter_abstract()
        _abs_safe = _abs.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("&lt;b&gt;",f'<font name="{BOLD}">').replace("&lt;/b&gt;","</font>")
        story.append(Paragraph(_abs_safe, styles["special_body"]))
        story.append(PageBreak())
        # Contents - page III (footer will show III, split may show IV) - style matched to reference image-1 exactly
        story.append(Paragraph("CONTENTS", styles["special_heading"]))
        story.append(Spacer(1, 6))
        # Styles for contents lines: chapter bold, section normal, page numbers right-aligned
        toc_chap_style = ParagraphStyle("toc_chap", parent=styles["body"], fontName=BOLD, fontSize=13.5, leading=19, spaceBefore=6, spaceAfter=3, alignment=TA_LEFT, leftIndent=0)
        toc_sec_style = ParagraphStyle("toc_sec", parent=styles["body"], fontName=REG, fontSize=12, leading=17, spaceBefore=3, spaceAfter=2, alignment=TA_LEFT, leftIndent=24)
        toc_page_chap = ParagraphStyle("toc_page_chap", parent=styles["body"], fontName=BOLD, fontSize=13.5, leading=19, spaceBefore=6, spaceAfter=3, alignment=TA_RIGHT)
        toc_page_sec = ParagraphStyle("toc_page_sec", parent=styles["body"], fontName=REG, fontSize=12, leading=17, spaceBefore=3, spaceAfter=2, alignment=TA_RIGHT)
        toc_header_left = ParagraphStyle("toc_hl", parent=styles["body"], fontName=BOLD, fontSize=13, leading=17, alignment=TA_LEFT, leftIndent=0)
        toc_header_right = ParagraphStyle("toc_hr", parent=styles["body"], fontName=BOLD, fontSize=13, leading=17, alignment=TA_RIGHT)
        tw = A4[0] - 2 * MARGIN
        # Build table rows with appropriate style per row
        toc_data = []
        # Header row
        toc_data.append([Paragraph(" ", toc_header_left), Paragraph(" ", toc_header_left)])  # placeholder will be overwritten below
        # We will use a custom table where second column is Right-aligned; use Table with colWidths [tw-60, 60]
        # Define widths: first column takes most, second fixed 60 for page numbers
        toc_col_widths = [tw - 62, 62]
        def _toc_row(left_text, right_text, is_chapter=False, is_header=False):
            if is_header:
                return [Paragraph(left_text, toc_header_left), Paragraph(right_text, toc_header_right)]
            elif is_chapter:
                return [Paragraph(_esc(left_text), toc_chap_style), Paragraph(right_text, toc_page_chap)]
            else:
                return [Paragraph(_esc(left_text), toc_sec_style), Paragraph(right_text, toc_page_sec)]
        # Header for page no column (like reference: blank left, Page no right bold)
        # First data row after heading is actually Acknowledgment I etc - keep as normal sec style but without bold
        # Build full list
        raw_toc = [
            ("Acknowledgment", "I", True),
            ("Abstract", "II", True),
            ("Table Of Contents", "Page no", True),
            ("Chapter 1 : Introduction", "1-3", True),
            ("    1.1 Problem Statement", "1-2", False),
            ("    1.2 Objectives", "1-2", False),
            ("    1.3 Project Scope", "2-3", False),
            ("    1.4 Limitations", "2", False),
            ("    1.5 Proposed Solution Overview", "2-3", False),
            ("    1.6 Report Organisation", "3", False),
            ("Chapter 2 : Literature Survey", "4-5", True),
            ("    2.1 Introduction", "4", False),
            ("    2.2 Related Work", "4", False),
            ("    2.3 Research Gap and Positioning", "5", False),
            ("Chapter 3 : System Overview and Requirements", "6-9", True),
            ("    3.1 Technology Stack", "6", False),
            ("    3.2 Module Architecture", "6-7", False),
            ("    3.3 Data Flow Overview", "7", False),
            ("    3.4 Functional Requirements", "7", False),
            ("    3.5 Non-Functional Requirements", "7", False),
            ("    3.6 Hardware and Software Requirements", "8", False),
            ("    3.7 Deployment Architecture", "8", False),
            ("    3.8 Preprocessing Engine", "8-9", False),
            ("Chapter 4 : System Design", "10-14", True),
            ("    4.1 System Architecture", "10", False),
            ("    4.2 Modules and Explanation", "10-11", False),
            ("    4.3 Web Routes and API Endpoints", "11", False),
            ("    4.4 Database Design", "12", False),
            ("    4.5 User Interface (UI)", "13", False),
            ("    4.6 Working Procedure", "13", False),
            ("    4.7 Authentication and Authorisation", "14", False),
            ("    4.8 Background Task Engine", "14", False),
            ("Chapter 5 : Implementation", "15-24", True),
            ("    5.1 RoBERTa Transformer Classifier", "15", False),
            ("    5.2 Ensemble Classification", "15", False),
            ("    5.3 Fusion, Risk and Urgency Scoring", "16", False),
            ("    5.4 Base Risks and Historical Baselines", "16-17", False),
            ("    5.5 Adaptive Intelligence", "17-18", False),
            ("        5.5.1 Adaptive Intelligence Configuration", "18", False),
            ("    5.6 Data Collection and Description", "18", False),
            ("        5.6.1 Dataset Audit Script", "19", False),
            ("        5.6.2 Engineered Features and Preprocessing Steps", "19", False),
            ("        5.6.3 Dataset Size Justification", "20", False),
            ("    5.7 Data Processing", "20", False),
            ("        5.7.1 Overall Processing", "20", False),
            ("        5.7.2 Textual Processing", "20", False),
            ("        5.7.3 Feature Formation", "20-21", False),
            ("    5.8 Proposed Model", "20-21", False),
            ("        5.8.1 Model Singleton Management", "22", False),
            ("        5.8.2 Ensemble Class Probabilities", "23", False),
            ("        5.8.3 Risk Score Composition", "23", False),
            ("        5.8.4 Fallback Strategy", "23-24", False),
            ("Chapter 6 : Testing and Security Analysis", "25-29", True),
            ("    6.1 Introduction to Testing", "25", False),
            ("    6.2 Types of Testing", "25-27", False),
            ("        6.2.1 Unit Testing", "25-26", False),
            ("        6.2.2 Integration Testing", "26", False),
            ("        6.2.3 System Testing", "26-27", False),
            ("    6.3 Security Audit (OWASP)", "28", False),
            ("    6.4 Edge Cases and Failure Handling", "28", False),
            ("    6.5 Performance Considerations", "29", False),
            ("Chapter 7 : Results", "30-34", True),
            ("    7.1 Evaluation Methodology and Model Results", "30", False),
            ("    7.2 Screenshots", "30-34", False),
            ("CONCLUSION", "VI", True),
            ("FUTURE SCOPE", "VII", True),
            ("REFERENCES", "VIII", True),
        ]
        if toc_entries is not None:
            raw_toc = list(toc_entries)
        toc_data = []
        # handle header row for Page no separately: we want first row after heading to be empty header trick
        # Instead build table where third entry Table Of Contents / Page no is header
        for left, right, is_chap in raw_toc:
            if left == "Table Of Contents":
                toc_data.append(_toc_row(left, right, is_header=True))
            elif is_chap:
                toc_data.append(_toc_row(left, right, is_chapter=True))
            else:
                toc_data.append(_toc_row(left, right, is_chapter=False))
        toc_tbl = Table(toc_data, colWidths=toc_col_widths)
        toc_tbl.setStyle(TableStyle([
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("LEFTPADDING", (0,0), (-1,-1), 0),
            ("RIGHTPADDING", (0,0), (-1,-1), 0),
            ("TOPPADDING", (0,0), (-1,-1), 2),
            ("BOTTOMPADDING", (0,0), (-1,-1), 3),
            ("LINEBELOW", (0,0), (-1,0), 0, colors.white),  # no line
        ]))
        story.append(toc_tbl)
        story.append(PageBreak())
        # List of Figures - page V (footer will show V) - style matched to reference image-2 exactly
        story.append(Paragraph("LIST OF FIGURES", styles["special_heading"]))
        story.append(Spacer(1, 6))
        lof_headers = ["FIGURE NO", "DESCRIPTION", "Page no"]
        lof_rows = [
            ["4.1", "System Architecture", "10"],
            ["4.2", "ER Diagram", "12"],
            ["5.1", "Dataset Distribution", "19"],
            ["5.2", "Dual-Model Pipeline", "21"],
            ["5.3", "Data Flow", "21"],
            ["5.4", "Workflow", "22"],
            ["7.1", "Landing Page", "31"],
            ["7.2", "Login Page", "31"],
            ["7.3", "Dashboard", "32"],
            ["7.4", "Results Summary", "32"],
            ["7.5", "Results Table", "33"],
            ["7.6", "Email Detail View", "33"],
            ["7.7", "Analytics", "34"],
            ["7.8", "Threat Console", "34"],
        ]
        if lof_entries is not None:
            lof_rows = list(lof_entries)
        # LOF table: black thin grid, no orange, centered text, bold header
        tw2 = A4[0] - 2 * MARGIN
        lof_col_widths = [tw2*0.22, tw2*0.52, tw2*0.26]
        lof_header_style = ParagraphStyle("lof_head", parent=styles["tcell"], fontName=BOLD, fontSize=12.5, leading=17, alignment=TA_CENTER)
        lof_cell_style = ParagraphStyle("lof_cell", parent=styles["tcell"], fontName=BOLD, fontSize=12.5, leading=17, alignment=TA_CENTER)
        lof_desc_style = ParagraphStyle("lof_desc", parent=styles["tcell"], fontName=BOLD, fontSize=12.5, leading=17, alignment=TA_LEFT, leftIndent=8)
        lof_data = [[Paragraph(_esc(h), lof_header_style) for h in lof_headers]]
        for r in lof_rows:
            lof_data.append([Paragraph(r[0], lof_cell_style), Paragraph(_esc(r[1]), lof_desc_style), Paragraph(r[2], lof_cell_style)])
        lof_tbl = Table(lof_data, colWidths=lof_col_widths, repeatRows=1)
        lof_tbl.setStyle(TableStyle([
            ("GRID", (0,0), (-1,-1), 0.6, colors.black),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("ALIGN", (0,0), (-1,-1), "CENTER"),
            ("LEFTPADDING", (0,0), (-1,-1), 4),
            ("RIGHTPADDING", (0,0), (-1,-1), 4),
            ("TOPPADDING", (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.white]),
        ]))
        story.append(lof_tbl)
        story.append(PageBreak())
    first_chapter = True
    for item in CONTENT:
        if item[0]=="__skip__":
            continue
        kind = item[0]
        if kind == "chapter":
            if not first_chapter:
                story.append(PageBreak())
            first_chapter = False
            story.append(Paragraph("CHAPTER %s" % item[1], styles["chapter"]))
            story.append(Paragraph(item[2], styles["chapter_title"]))
        elif kind == "sec":
            if item[1] == "1.6":
                story.append(PageBreak())
            if item[1] == "3.6":
                story.append(PageBreak())
            if item[1] == "5.6.3":
                story.append(PageBreak())
            if item[1] == "6.3":
                story.append(PageBreak())
            story.append(Paragraph("%s &nbsp;%s" % (item[1], item[2]),
                                   styles["sec"]))
        elif kind == "select":
            story.append(Paragraph(item[2], styles["sec"]))
        elif kind == "pagebreak":
            story.append(PageBreak())
        elif kind == "body":
            story.append(Paragraph(_esc(item[1]), styles["body"]))
        elif kind == "diag":
            img_path, cap = item[1], item[2]
            from PIL import Image as PILImage
            iw, ih = PILImage.open(img_path).size
            maxw = A4[0] - 2 * MARGIN
            # Sizing: 4.3 pipeline bigger, 4.4 + others shrink to keep same page
            if "Figure 5.1" in cap:
                maxw = maxw * 0.72
            elif "Figure 4.3" in cap:
                maxw = maxw * 0.98
            elif "Figure 4.4" in cap:
                maxw = maxw * 0.98
            elif "Figure 4.6" in cap:
                maxw = maxw * 0.98
            ratio = maxw / float(iw)
            # --- Screenshot centering for Results chapter (Figure 7.x) : maths equal top/bottom ---
            is_result_screenshot = cap.startswith("Figure 7.")
            if is_result_screenshot:
                AVAIL = A4[1] - 55 - 66
                first_of_pair = cap.startswith("Figure 7.1:") or cap.startswith("Figure 7.3:") or cap.startswith("Figure 7.5:") or cap.startswith("Figure 7.7:")
                if first_of_pair:
                    try:
                        idx = CONTENT.index(item)
                        nxt = CONTENT[idx+1] if idx+1 < len(CONTENT) else None
                    except ValueError:
                        nxt = None
                    if nxt and nxt[0]=="diag" and "Figure 7." in nxt[2]:
                        nxt_path, nxt_cap = nxt[1], nxt[2]
                        from PIL import Image as PILImage2
                        niw, nih = PILImage2.open(nxt_path).size
                        nratio = maxw / float(niw)
                        h1 = ih * ratio * 1.06
                        h2 = nih * nratio * 1.06
                        cap_h = 20
                        top_spacer = 0
                        bottom_spacer = 15
                        gap_pair = 30
                        story.append(Spacer(1, top_spacer))
                        im1 = Image(img_path, width=maxw, height=h1, hAlign='CENTER')
                        tbl1 = Table([[im1]], colWidths=[maxw])
                        tbl1.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),20),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
                        story.append(tbl1)
                        story.append(Paragraph(_esc(cap), styles["cap"]))
                        story.append(Spacer(1, 50))
                        im2 = Image(nxt_path, width=maxw, height=h2, hAlign='CENTER')
                        tbl2 = Table([[im2]], colWidths=[maxw])
                        tbl2.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
                        story.append(tbl2)
                        story.append(Paragraph(_esc(nxt_cap), styles["cap"]))
                        story.append(Spacer(1, bottom_spacer))
                        CONTENT[idx+1] = ("__skip__",)
                        continue
                        h = ih * ratio
                        cap_h = 20
                        gap_single = max((AVAIL - h - cap_h - 30) / 2, 18)
                        _single = []
                        _single.append(Spacer(1, gap_single))
                        im = Image(img_path, width=maxw, height=h, hAlign='CENTER')
                        tbl = Table([[im]], colWidths=[maxw])
                        tbl.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
                        _single.append(tbl)
                        _single.append(Paragraph(_esc(cap), styles["cap"]))
                        _single.append(Spacer(1, gap_single))
                        story.append(KeepTogether(_single))
                        story.append(PageBreak())
                        continue
            # Special: co-locate Figure 5.2 + 5.3 cropped, 5.2 slightly above, keep in page 21
            if cap.startswith("Figure 5.2:"):
                try:
                    idx = CONTENT.index(item)
                    nxt = CONTENT[idx+1] if idx+1 < len(CONTENT) else None
                except ValueError:
                    nxt = None
                if nxt and nxt[0]=="diag" and "Figure 5.3:" in nxt[2]:
                    # Pair 5.2 + 5.3 on same page 21, 5.2 slightly above (top 12), cropped to save space
                    # Use smaller width to fit with preceding body text on page 21
                    nxt_path2, nxt_cap2 = nxt[1], nxt[2]
                    # Crop to remove wasted white space and use 0.85 width to fit on page 21 with body
                    maxw_small = (A4[0] - 2 * MARGIN) * 0.85
                    cpath1, (ciw1, cih1) = _crop_whitespace(img_path)
                    cpath2, (ciw2, cih2) = _crop_whitespace(nxt_path2)
                    nratio2 = maxw_small / float(ciw2)
                    ratio_c = maxw_small / float(ciw1)
                    h1a = cih1 * ratio_c * 1.15
                    h2a = cih2 * nratio2 * 1.15
                    top_a = 12
                    gap_a = 50
                    bottom_a = 18
                    from reportlab.platypus import KeepTogether as KT2
                    pair2 = []
                    pair2.append(Spacer(1, top_a))
                    im1a = Image(cpath1, width=maxw_small, height=h1a, hAlign='CENTER')
                    tbl1a = Table([[im1a]], colWidths=[maxw_small])
                    tbl1a.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
                    pair2.append(tbl1a)
                    pair2.append(Paragraph(_esc(cap), styles["cap"]))
                    pair2.append(Spacer(1, gap_a))
                    im2a = Image(cpath2, width=maxw_small, height=h2a, hAlign='CENTER')
                    tbl2a = Table([[im2a]], colWidths=[maxw_small])
                    tbl2a.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
                    pair2.append(tbl2a)
                    pair2.append(Paragraph(_esc(nxt_cap2), styles["cap"]))
                    pair2.append(Spacer(1, bottom_a))
                    story.append(KeepTogether(pair2))
                    CONTENT[idx+1] = ("__skip__",)
                    story.append(PageBreak())
                    continue
            # Normal handling for non-result figures
            is_53 = cap.startswith("Figure 5.") and not cap.startswith("Figure 5.1")
            is_top = cap.startswith("Figure 5.3:") or cap.startswith("Figure 5.5:") or cap.startswith("Figure 5.7:")
            is_46 = "Figure 4.6" in cap
            if is_46:
                gap = 6
            elif cap.startswith("Figure 5.4:"):
                gap = 4
            elif is_top:
                gap = 80
            elif is_53:
                gap = 56
            else:
                gap = 20
            story.append(Spacer(1, gap))
            im = Image(img_path, width=maxw, height=ih * ratio, hAlign='CENTER')
            tbl = Table([[im]], colWidths=[maxw])
            tbl.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'CENTER'),
                                     ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                                     ('LEFTPADDING', (0,0), (-1,-1), 0),
                                     ('RIGHTPADDING', (0,0), (-1,-1), 0),
                                     ('BOTTOMPADDING', (0,0), (-1,-1), 0)]))
            story.append(tbl)
            if "Figure 4.6" in cap:
                cap_style = ParagraphStyle("cap46", parent=styles["cap"], spaceBefore=1, spaceAfter=4)
                story.append(Paragraph(_esc(cap), cap_style))
            else:
                story.append(Paragraph(_esc(cap), styles["cap"]))
        elif kind == "table":
            headers, rows = item[1], item[2]
            # Table 4.7 no forced break - flow naturally to avoid empty page 22
            if False and headers == ["Revision", "Migration name", "Schema change"]:
                story.append(PageBreak())
            # Table 4.8 - shrink to keep caption on same page
            if headers == ["View", "Route", "HTTP method", "Template", "Purpose"]:
                # Custom small table for 4.8
                from reportlab.lib.styles import ParagraphStyle as _PS
                small_tcell = ParagraphStyle("small_tcell", parent=styles["tcell"], fontSize=7, leading=9)
                small_thead = ParagraphStyle("small_thead", parent=styles["thead"], fontSize=7, leading=9)
                data = [[Paragraph(_esc(h), small_thead) for h in headers]]
                for r in rows:
                    data.append([Paragraph(_esc(str(c)), small_tcell) for c in r])
                tw = A4[0] - 2 * MARGIN
                widths = [tw*0.15, tw*0.18, tw*0.14, tw*0.22, tw*0.31]
                from reportlab.platypus import KeepTogether
                t = Table(data, colWidths=widths, repeatRows=1)
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.Color(*HDR_RGB)),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.55, 0.35, 0.18)),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.Color(0.99, 0.96, 0.92)]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]))
                story.append(Spacer(1, 6))
                # Keep table + caption together on same page
                cap_para = Paragraph(_esc(item[3]), styles["cap"]) if len(item) > 3 and item[3] else Spacer(1, 6)
                story.append(KeepTogether([t, cap_para]))
            else:
                is_tc = headers[0] == "ID" and headers[1] == "Test Objective"
                is_migrations = headers == ["Revision", "Migration name", "Schema change"]
                if is_tc:
                    spc = 8
                elif is_migrations:
                    spc = 18
                else:
                    spc = 2 if headers == ["#", "Preprocessing step", "Purpose"] else 6
                story.append(Spacer(1, spc))
                t = _make_table(headers, rows)
                if len(item) > 3 and item[3]:
                    cap_para = Paragraph(_esc(item[3]), styles["cap"])
                else:
                    cap_para = Spacer(1, 6)
                if headers == ["#", "Preprocessing step", "Purpose"] or headers == ["#", "Engineered feature", "Definition"]:
                    story.append(KeepTogether([t, cap_para]))
                else:
                    story.append(t)
                    story.append(cap_para)
        elif kind == "conclusion":
            story.append(PageBreak())
            story.append(Spacer(1, 18))
            story.append(Paragraph("CONCLUSION", styles["special_heading"]))
            for para in item[1:]:
                story.append(Paragraph(_esc(para), styles["special_body"]))
        elif kind == "future_scope":
            story.append(PageBreak())
            story.append(Spacer(1, 18))
            story.append(Paragraph("FUTURE SCOPE", styles["special_heading"]))
            story.append(Paragraph(_esc(item[1]), styles["special_body"]))
            for subhead, body in item[2]:
                story.append(Paragraph(_esc(subhead), styles["special_subhead"]))
                story.append(Paragraph(_esc(body), styles["special_body"]))
            if len(item) > 3 and item[3]:
                story.append(Paragraph(_esc(item[3]), styles["special_body"]))
        elif kind == "references":
            story.append(PageBreak())
            story.append(Spacer(1, 18))
            story.append(Paragraph("REFERENCES", styles["special_heading"]))
            for ref_list in item[1]:
                for ref_text in ref_list:
                    story.append(Paragraph(_esc(ref_text), styles["ref_item"]))

    # Two-pass: first build to count total pages, then mark last 3 as decorative border.
    import io, copy
    _special_border_pages.clear()
    # Dummy pass to count pages
    _tmp_buf = io.BytesIO()
    _tmp_doc = SimpleDocTemplate(_tmp_buf, pagesize=A4,
                                 leftMargin=64, rightMargin=64,
                                 topMargin=65, bottomMargin=75)
    def _dummy_cb(c, d):
        pass
    # Need deep copy of story because build consumes it
    import copy as _copy
    _tmp_story = _copy.deepcopy(story)
    try:
        _tmp_doc.build(_tmp_story, onFirstPage=_dummy_cb, onLaterPages=_dummy_cb)
        total_pages = _tmp_doc.page
    except Exception:
        total_pages = None
    if total_pages and total_pages >= 1:
        # Phase-2: only the trailing REFERENCES page gets the border (single special page)
        _special_border_pages.update({total_pages})
        if include_front:
            # Front matter occupies first 5 pages (Contents spans 2 pages)
            _special_border_pages.update({1, 2, 3, 4, 5})
    else:
        _special_border_pages.update({999})
        if include_front:
            _special_border_pages.update({1, 2, 3, 4, 5})

    def _page_callback(c, page_doc):
        if page_doc.page in _special_border_pages:
            _draw_border_page(c, page_doc)
        else:
            _decorate(c, page_doc)

    doc.build(story, onFirstPage=_page_callback, onLaterPages=_page_callback)
    print("PDF written:", path)
