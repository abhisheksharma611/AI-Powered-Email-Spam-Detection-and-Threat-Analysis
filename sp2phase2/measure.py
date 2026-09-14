# -*- coding: utf-8 -*-
"""Measure pass-1 PDF: section->footer page, figure->page, front length, total."""
import re
import pdfplumber

PDF = r"C:\Users\dell\Desktop\Spam Protection 5\sp2reportforphase2.pdf"
SEC_RE = re.compile(r"^(\d+\.\d+(?:\.\d+)?)\s+([A-Z])")
FIG_RE = re.compile(r"^Figure (\d+\.\d+)\s*:")
TAB_RE = re.compile(r"^Table (\d+\.\d+)")

sec_pages, fig_pages, tab_pages = {}, {}, {}
n = 0
with pdfplumber.open(PDF) as pdf:
    n = len(pdf.pages)
    for i, pg in enumerate(pdf.pages, start=1):
        text = pg.extract_text() or ""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        footer = None
        for l in lines:
            if "Dept" in l:
                footer = l.split()[-1]
        if footer is not None and footer.isdigit():
            for l in lines:
                m = SEC_RE.match(l)
                if m and m.group(1) not in sec_pages:
                    sec_pages[m.group(1)] = footer
                m = FIG_RE.match(l)
                if m and m.group(1) not in fig_pages:
                    fig_pages[m.group(1)] = footer
                m = TAB_RE.match(l)
                if m and m.group(1) not in tab_pages:
                    tab_pages[m.group(1)] = footer
        if i <= 7 or i >= n - 1:
            last = lines[-1] if lines else ""
            print("p%d footer=%s tail=%s" % (i, footer, last[:60]))

print("total:", n)
print("SEC:", sec_pages)
print("FIG:", fig_pages)
print("TAB:", tab_pages)
