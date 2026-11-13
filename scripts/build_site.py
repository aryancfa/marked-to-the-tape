"""
build_site.py — the live page, built from what is already in reports/ (C9).

    .venv/Scripts/python scripts/build_site.py          # writes site/
    then open site/index.html in a browser

A static site: plain HTML and the charts, no server and no script on the
page. It reads ONLY files that are already committed: reports/ (derived
statistics), README.md, docs/findings.md and docs/investigations.md. It
never opens data/. So the page can show nothing the repository does not
already show, and hard rule 1 holds by construction: computed results only,
no price, no quote. A test (tests/test_site.py) checks that it reads nothing
else and that every page carries the notice.

Every page asks search engines not to index it. That keeps it out of search
results; it does not make it private. Anyone with the address can read it.

Uses the standard library only, so the deploy job needs no install.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
NAMES = ["RY", "TD", "BNS", "BMO", "MFC", "CNQ", "SU", "ENB", "CNR", "BCE", "ABX", "SHOP",
         "XIU", "ZEB", "XEG"]
BOOKS = [("diversified", "Diversified book"), ("bank_heavy", "Bank-heavy book")]
PAGES = [("index.html", "Overview"), ("front.html", "Model against market"),
         ("book.html", "The book"), ("review.html", "Review queue"),
         ("hedge.html", "After the hedge"), ("checks.html", "Checks"),
         ("findings.html", "Findings"), ("investigations.html", "Investigations"),
         ("limits.html", "Limits")]
NOTICE = ("Computed results only: differences in vol points, counts, ratios, and dollars on "
          "made-up books. No exchange price or quote appears anywhere on this site.")

CSS = """
:root{--bg:#fbfbf9;--fg:#1c1c1a;--mute:#666660;--line:#dcdcd4;--card:#ffffff;--acc:#1f5fa8}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#e9e9e4;--mute:#a0a098;
--line:#34342f;--card:#1e1e1b;--acc:#7fb2ee}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
header{border-bottom:1px solid var(--line);background:var(--card)}
.wrap{max-width:1060px;margin:0 auto;padding:0 16px}
header .wrap{padding-top:14px;padding-bottom:10px}
header h1{font-size:19px;margin:0 0 8px}
nav{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:14px}
nav a{color:var(--mute);text-decoration:none;padding:2px 0;border-bottom:2px solid transparent}
nav a.on{color:var(--fg);border-color:var(--acc)}
main{padding:22px 0 40px}
h2{font-size:22px;margin:26px 0 8px}h3{font-size:17px;margin:22px 0 6px}
p,li{max-width:76ch}a{color:var(--acc)}
img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:4px;background:#fff}
pre{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:12px;
overflow-x:auto;font:12.5px/1.45 ui-monospace,Consolas,monospace}
code{font:0.9em ui-monospace,Consolas,monospace}
table{border-collapse:collapse;margin:10px 0;font-size:14px;display:block;overflow-x:auto}
th,td{border-bottom:1px solid var(--line);padding:5px 10px;text-align:left;vertical-align:top}
th{font-weight:600}
.lede{font-size:18px;max-width:70ch}
.note{color:var(--mute);font-size:13.5px}
.pick{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 18px}
.pick a{border:1px solid var(--line);border-radius:4px;padding:2px 9px;text-decoration:none;
font-size:14px;background:var(--card)}
details{margin:10px 0}summary{cursor:pointer;color:var(--acc)}
footer{border-top:1px solid var(--line);padding:14px 0 30px;color:var(--mute);font-size:13px}
"""

# ---------------------------------------------------------------- markdown

def inline(text: str) -> str:
    """Bold, code, links and images inside one line of markdown."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)",
                 lambda m: f'<img src="{asset(m.group(2))}" alt="{m.group(1)}">', out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                 lambda m: f'<a href="{link(m.group(2))}">{m.group(1)}</a>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*([^*\s][^*]*)\*(?![\w*])", r"<em>\1</em>", out)
    return out


def asset(path: str) -> str:
    """A chart referenced as reports/x.png is served from img/x.png."""
    return "img/" + path.split("reports/", 1)[1] if "reports/" in path else path


def link(target: str) -> str:
    known = {"docs/findings.md": "findings.html", "docs/investigations.md": "investigations.html"}
    if target in known:
        return known[target]
    if target.startswith("http"):
        return target
    return "#"                      # a file in the repository, not on the site


def markdown(text: str) -> str:
    """Enough markdown for this project's own documents: headings, paragraphs,
    nested bullet and numbered lists, tables. Anything else passes through as
    a paragraph."""
    lines, out, i = text.splitlines(), [], 0
    stack: list[tuple[int, str]] = []            # open lists: (indent, tag)

    def close(to: int = -1) -> None:
        while stack and stack[-1][0] > to:
            out.append(f"</li></{stack.pop()[1]}>")

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            close()
            n = min(len(m.group(1)) + 1, 4)
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) and set(
                lines[i + 1].replace("|", "").strip()) <= set("-: "):
            close()
            cells = lambda l: [c.strip() for c in l.strip().strip("|").split("|")]
            out.append("<table><tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells(line))
                       + "</tr>")
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells(lines[i]))
                           + "</tr>")
                i += 1
            out.append("</table>")
            continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
        if m:
            indent, tag = len(m.group(1)), "ol" if m.group(2)[0].isdigit() else "ul"
            body = [m.group(3)]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(
                    r"^\s*([-*]|\d+\.)\s+", lines[i]) and lines[i].startswith(" "):
                body.append(lines[i].strip())
                i += 1
            if stack and stack[-1][0] == indent:
                out.append("</li>")
            elif stack and stack[-1][0] > indent:
                close(indent)
                if stack and stack[-1][0] == indent:
                    out.append("</li>")
            if not stack or stack[-1][0] < indent:
                stack.append((indent, tag))
                out.append(f"<{tag}>")
            out.append("<li>" + inline(" ".join(body)))
            continue
        close()
        para = [line.strip()]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,4}\s|\s*([-*]|\d+\.)\s|\|)", lines[i]):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>" + inline(" ".join(para)) + "</p>")
    close()
    return "\n".join(out)


def section(text: str, heading: str) -> str:
    """The body under one '## heading', up to the next heading of that level."""
    m = re.search(rf"^## {re.escape(heading)}\s*$", text, flags=re.M)
    if not m:
        raise SystemExit(f"no section '## {heading}' to build the page from")
    rest = text[m.end():]
    nxt = re.search(r"^## ", rest, flags=re.M)
    return rest[:nxt.start()] if nxt else rest


# ---------------------------------------------------------------- pieces

def report(name: str) -> str:
    """A text report as it stands, or a line saying it has not been run."""
    p = REPORTS / name
    if not p.exists():
        return f'<p class="note">{html.escape(name)} has not been produced yet.</p>'
    return "<pre>" + html.escape(p.read_text(encoding="utf-8")) + "</pre>"


def chart(name: str, alt: str) -> str:
    if not (REPORTS / name).exists():
        return f'<p class="note">{html.escape(name)} has not been produced yet.</p>'
    return f'<p><img src="img/{name}" alt="{html.escape(alt)}" loading="lazy"></p>'


def folded(title: str, body: str) -> str:
    return f"<details><summary>{html.escape(title)}</summary>{body}</details>"


def page(file: str, body: str, stamp: str) -> str:
    title = dict(PAGES)[file]
    nav = "".join(f'<a href="{f}"{" class=on" if f == file else ""}>{t}</a>' for f, t in PAGES)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{title} · Marked to the Tape</title><style>{CSS}</style></head>
<body><header><div class="wrap"><h1>Marked to the Tape</h1><nav>{nav}</nav></div></header>
<main><div class="wrap">{body}</div></main>
<footer><div class="wrap">{NOTICE}<br>{stamp}</div></footer></body></html>
"""


# ---------------------------------------------------------------- pages

def build(out: Path) -> list[Path]:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    findings = (ROOT / "docs/findings.md").read_text(encoding="utf-8")
    cases = (ROOT / "docs/investigations.md").read_text(encoding="utf-8")
    stamp = "Not yet reproduced from a clean commit."
    if (REPORTS / "manifest.json").exists():
        m = json.loads((REPORTS / "manifest.json").read_text(encoding="utf-8"))
        stamp = (f"Every number here was produced by one run of commit {str(m.get('commit'))[:7]}"
                 f", started {m.get('started', '?')}, {len(m.get('steps', []))} steps, from "
                 f"{len(m.get('inputs', {}))} input files identified by hash.")

    pages: dict[str, str] = {}
    pages["index.html"] = (
        '<p class="lede">A risk system for a small book of Canadian listed options. It prices '
        "each position with its own engine, tests each exchange mark against two references "
        "before its P&amp;L is believed, and shows what risk is left after a hedge that could "
        "actually have been put on.</p>"
        "<h2>Model against market</h2>"
        + chart("ry_front_chart.png", "Royal Bank: implied vol against three references")
        + '<p class="note">Royal Bank options, six months. Each cell is a difference in vol '
          'points; bold means more than two standard errors from zero. '
          '<a href="front.html">Fifteen names</a>.</p>'
        "<h2>What is left after the hedge</h2>"
        + chart("diversified_hedge.png", "The diversified book after each hedge")
        + '<p class="note">A made-up book marked at the exchange\'s prices each day. '
          '<a href="hedge.html">The three stages</a>.</p>'
        "<h2>What it has found so far</h2>" + markdown(section(readme, "What it has found so far")))

    pick = '<div class="pick">' + "".join(f'<a href="#{n}">{n}</a>' for n in NAMES) + "</div>"
    body = ("<h2>Model against market, by name</h2>"
            "<p>Each implied vol is compared with three references: the call against the put "
            "at the same strike, the strike against a smile fitted through its neighbours, and "
            "the exchange's own published vol. Differences are pooled over days, by days to "
            "expiry and by strike relative to the share price.</p>" + pick
            + "<h3>All fifteen names</h3>" + report("all_names_summary.txt"))
    for n in NAMES:
        body += (f'<h3 id="{n}">{n}</h3>' + chart(f"{n.lower()}_front_chart.png", f"{n} front chart")
                 + folded("Call minus put by expiry, pooled over days",
                          chart(f"{n.lower()}_cp_gap_pooled.png", f"{n} call minus put"))
                 + (folded("Funding and implied dividends",
                           report(f"{n.lower()}_funding_split.txt"))
                    if (REPORTS / f"{n.lower()}_funding_split.txt").exists() else ""))
    pages["front.html"] = body

    body = ("<h2>One book, marked and explained</h2>"
            "<p>Two made-up books, valued at the exchange's own marks on every day on file. "
            "Each option's daily change is split by repricing one input at a time (a day "
            "passing, dividend news, the share moving, vol moving), so the parts add up to the "
            "change in the marks with nothing left over.</p>")
    for stem, title in BOOKS:
        body += (f"<h3>{title}</h3>" + chart(f"{stem}_book.png", f"{title}: daily P&L explained")
                 + folded("The numbers", report(f"{stem}_book.txt")))
    pages["book.html"] = body

    body = ("<h2>Review queue</h2>"
            "<p>The question a desk asks each morning: which positions need attention, how "
            "much do they matter, and what should be checked first? Each item keeps what was "
            "observed apart from the likely cause. A call and a put that disagree are an "
            "observation; a wrong dividend date is one explanation, and a bad quote is "
            "another.</p>")
    for stem, title in BOOKS:
        body += f"<h3>{title}</h3>" + report(f"{stem}_review.txt")
        shots = sorted((REPORTS / "review").glob(f"{stem}_*.png")) if (REPORTS / "review").exists() else []
        if shots:
            body += folded("Drill-down charts for the largest items", "".join(
                f'<p><img src="img/review/{p.name}" alt="drill-down {p.stem}" loading="lazy"></p>'
                for p in shots))
    body += ("<h3>How often is the named cause right?</h3>"
             "<p>Measured on invented data with faults planted, where the truth is known. "
             "Thresholds were chosen on one set and scored on another.</p>"
             + report("review_benchmark.txt")
             + "<h3>Do the checks catch a bad quote, against plain rules?</h3>"
             + report("mark_benchmark.txt"))
    pages["review.html"] = body

    body = ("<h2>What is left after the hedge</h2>"
            "<p>Three stages, each a hedge set from what was known the evening before: "
            "unhedged; delta sold through the index and a sector fund, by ratios fitted on the "
            "250 days before; and on top of that an XIU straddle sized to the book's "
            "beta-weighted vega, charged half the quoted spread.</p>")
    for stem, title in BOOKS:
        body += (f"<h3>{title}</h3>" + chart(f"{stem}_hedge.png", f"{title} after each hedge")
                 + report(f"{stem}_hedge.txt"))
    body += ("<h3>The delta hedge on twelve years of shares</h3>"
             + chart("hedge_shares.png", "Index and sector hedge, share by share")
             + folded("The numbers", report("hedge_shares_numbers.txt")))
    pages["hedge.html"] = body

    body = ("<h2>Checks on the method</h2>"
            "<h3>Does any one choice drive the result?</h3>" + report("ry_robustness.txt")
            + "<h3>The engine against a second model</h3>"
              "<p>OptionMetrics on the New York listings of seven names, June to August 2025.</p>"
            + chart("ry_us_engine_check.png", "Engine check, Royal Bank New York listing")
            + folded("Royal Bank, the numbers", report("ry_us_engine_numbers.txt"))
            + "<h3>Funding from the index future</h3>" + report("index_funding_numbers.txt")
            + "<h3>Next year's dividends, three ways</h3>" + report("dividend_three_way.txt")
            + "<h3>Royal Bank, every number behind the front chart</h3>"
            + folded("ry_numbers.txt", report("ry_numbers.txt")))
    pages["checks.html"] = body

    pages["findings.html"] = markdown(findings)
    pages["investigations.html"] = markdown(cases)
    pages["limits.html"] = ("<h2>What is still not trusted</h2>"
                            + markdown(section(findings, "What is still not trusted"))
                            + "<h2>What does not exist yet</h2>"
                            + markdown(section(findings, "What does not exist yet")))

    if out.exists():
        shutil.rmtree(out)
    (out / "img").mkdir(parents=True)
    for p in REPORTS.rglob("*.png"):
        dest = out / "img" / p.relative_to(REPORTS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    written = []
    for file, body in pages.items():
        (out / file).write_text(page(file, body, stamp), encoding="utf-8")
        written.append(out / file)
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "site"
    files = build(target)
    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    print(f"{len(files)} pages and {len(list((target / 'img').rglob('*.png')))} charts in "
          f"{target}, {size / 1e6:.1f} MB")
