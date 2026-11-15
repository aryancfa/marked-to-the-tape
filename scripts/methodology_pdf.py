"""
methodology_pdf.py — docs/methodology.md -> reports/methodology.pdf (build sheet C9).

The methodology is written once, in Markdown, so it reads on GitHub and on the
site; this script typesets the same text as a PDF to hand to someone. It
understands only what that document uses: headings, paragraphs, bullet and
numbered lists, tables, **bold**, *italic* and `code`.

Needs reportlab (pinned in requirements.txt). The PDF is committed, so the
site build on GitHub needs nothing beyond the standard library.

    .venv/Scripts/python scripts/methodology_pdf.py [source.md] [target.pdf]
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (KeepTogether, ListFlowable, ListItem, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "methodology.md"
TARGET = ROOT / "reports" / "methodology.pdf"

INK = colors.HexColor("#1f2328")
MUTED = colors.HexColor("#57606a")
RULE = colors.HexColor("#d0d7de")
HEAD = colors.HexColor("#f6f8fa")


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=base["Normal"], fontName="Helvetica", fontSize=9.5,
                          leading=13, textColor=INK, alignment=TA_LEFT, spaceAfter=5)
    return {
        "title": ParagraphStyle("title", parent=body, fontName="Helvetica-Bold", fontSize=17,
                                leading=21, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=body, fontName="Helvetica-Bold", fontSize=12,
                             leading=15, spaceBefore=9, spaceAfter=4),
        "body": body,
        "cell": ParagraphStyle("cell", parent=body, fontSize=8.5, leading=11, spaceAfter=0),
        "cellhead": ParagraphStyle("cellhead", parent=body, fontName="Helvetica-Bold",
                                   fontSize=8.5, leading=11, spaceAfter=0),
    }


def inline(text: str) -> str:
    """Markdown inline marks -> reportlab's mini-HTML. Escape first."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"`([^`]+)`", r'<font name="Courier" size="8.5">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*([^*]+)\*(?![\w*])", r"<i>\1</i>", text)
    return text


def blocks(md: str) -> list[tuple[str, object]]:
    """Split the document into (kind, content) blocks."""
    out: list[tuple[str, object]] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.startswith("# "):
            out.append(("title", line[2:].strip())); i += 1
        elif line.startswith("## "):
            out.append(("h2", line[3:].strip())); i += 1
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            out.append(("table", rows))
        elif re.match(r"(- |\d+\. )", line):
            ordered = bool(re.match(r"\d+\. ", line))
            items: list[str] = []
            while i < len(lines) and lines[i].strip():
                if re.match(r"(- |\d+\. )", lines[i]):
                    items.append(re.sub(r"^(- |\d+\. )", "", lines[i]).strip())
                else:                       # continuation of the item above
                    items[-1] += " " + lines[i].strip()
                i += 1
            out.append(("ol" if ordered else "ul", items))
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not re.match(r"(#|\||- |\d+\. )", lines[i]):
                para.append(lines[i].strip()); i += 1
            out.append(("p", " ".join(para)))
    return out


def flow(md: str, width: float) -> list:
    st = styles()
    story: list = []
    pending_heading = None
    for kind, content in blocks(md):
        if kind == "title":
            story.append(Paragraph(inline(content), st["title"]))
            continue
        if kind == "h2":
            pending_heading = Paragraph(inline(content), st["h2"])
            continue
        if kind == "p":
            item = Paragraph(inline(content), st["body"])
        elif kind in ("ul", "ol"):
            item = ListFlowable(
                [ListItem(Paragraph(inline(t), st["body"]), leftIndent=12) for t in content],
                bulletType="1" if kind == "ol" else "bullet", start="1" if kind == "ol" else None,
                bulletFormat="%s." if kind == "ol" else None,
                leftIndent=12, bulletFontSize=8 if kind == "ul" else 9.5)
        else:
            rows = content
            ncol = len(rows[0])
            data = [[Paragraph(inline(c), st["cellhead" if r == 0 else "cell"]) for c in row]
                    for r, row in enumerate(rows)]
            longest = [max(len(row[c]) for row in rows) for c in range(ncol)]
            share = [min(max(n, 8), 70) for n in longest]
            widths = [width * s / sum(share) for s in share]
            item = Table(data, colWidths=widths, repeatRows=1)
            item.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), HEAD),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
        if pending_heading is not None:     # never leave a heading alone at a page foot
            story.append(KeepTogether([pending_heading, item]))
            pending_heading = None
        else:
            story.append(item)
        if kind == "table":
            story.append(Spacer(1, 6))
    return story


def footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(doc.leftMargin, 0.5 * inch, "Marked to the Tape: methodology")
    canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 0.5 * inch, f"{doc.page}")
    canvas.restoreState()


def build(source: Path = SOURCE, target: Path = TARGET) -> int:
    # The source's hash goes in the PDF's keywords, so a test can tell when the
    # committed PDF was typeset from an older text (tests/test_site.py).
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    doc = SimpleDocTemplate(str(target), pagesize=letter, leftMargin=0.85 * inch,
                            rightMargin=0.85 * inch, topMargin=0.75 * inch,
                            bottomMargin=0.8 * inch, title="Marked to the Tape: methodology",
                            author="Marked to the Tape", subject="Methods and settings",
                            keywords=f"source sha256 {digest}",
                            invariant=1)    # no timestamp: the same text gives the same bytes
    doc.build(flow(source.read_text(encoding="utf-8"), doc.width),
              onFirstPage=footer, onLaterPages=footer)
    return doc.page


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else TARGET
    pages = build(src, dst)
    print(f"{pages} pages -> {dst}")
