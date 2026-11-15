"""
build_site.py — the live page, built from what is already in reports/ (C9).

    .venv/Scripts/python scripts/build_site.py          # writes site/
    then open site/index.html in a browser

A static site: plain HTML and the charts, no server and no script on the
page. It reads ONLY files that are already committed: reports/ (derived
statistics), README.md, docs/findings.md, docs/investigations.md and
docs/methodology.md (with its typeset copy, reports/methodology.pdf). It
never opens data/. So the page can show nothing the repository does not
already show, and hard rule 1 holds by construction: computed results only,
no price, no quote. A test (tests/test_site.py) checks that it reads nothing
else and that every page carries the notice.

Every page asks search engines not to index it. That keeps it out of search
results; it does not make it private. Anyone with the address can read it.

Uses the standard library only, so the deploy job needs no install.
"""

from __future__ import annotations

import csv
import html
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
NAMES = ["RY", "TD", "BNS", "BMO", "MFC", "CNQ", "SU", "ENB", "CNR", "BCE", "ABX", "SHOP",
         "XIU", "ZEB", "XEG"]
BOOKS = [("diversified", "Diversified book"), ("bank_heavy", "Bank-heavy book")]
PAGES = [("index.html", "Overview"), ("front.html", "Model against market"),
         ("book.html", "The book"), ("review.html", "Review queue"),
         ("hedge.html", "After the hedge"), ("fund.html", "Stress and tail risk"),
         ("vol.html", "Implied against realized"), ("checks.html", "Checks"),
         ("findings.html", "Findings"), ("investigations.html", "Investigations"),
         ("days.html", "Pick a day"), ("method.html", "Method"),
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
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;
margin:10px 0 18px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px 12px}
.tile .k{font-size:13px;color:var(--mute)}
.tile .v{font-size:24px;font-weight:600;margin:2px 0;font-variant-numeric:tabular-nums}
.tile .n{font-size:12.5px;color:var(--mute);line-height:1.4}
details{margin:10px 0}summary{cursor:pointer;color:var(--acc)}
.cal{font-size:14px;margin:4px 0}.cal b{display:inline-block;min-width:5.5em;font-weight:600}
.cal a{display:inline-block;min-width:1.9em;text-align:right;text-decoration:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
td.big{font-weight:700}
.steps{font-size:14px;margin:6px 0 14px}
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
                 lambda m: (m.group(1) if link(m.group(2)) is None else
                            f'<a href="{link(m.group(2))}">{m.group(1)}</a>'), out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![\w*])\*([^*\s][^*]*)\*(?![\w*])", r"<em>\1</em>", out)
    return out


def asset(path: str) -> str:
    """A chart referenced as reports/x.png is served from img/x.png."""
    return "img/" + path.split("reports/", 1)[1] if "reports/" in path else path


def link(target: str) -> str | None:
    known = {"findings.md": "findings.html", "investigations.md": "investigations.html",
             "methodology.md": "method.html"}
    if target.split("/")[-1] in known:
        return known[target.split("/")[-1]]
    if target.startswith("http"):
        return target
    return None                     # a file in the repository, not on the site: plain text


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


def page(file: str, body: str, stamp: str, title: str | None = None, up: str = "") -> str:
    """One page. `file` is the nav entry it sits under; `up` is the path back
    to the site's root from a page in a subfolder (day pages: '../../')."""
    title = title or dict(PAGES)[file]
    nav = "".join(f'<a href="{up}{f}"{" class=on" if f == file else ""}>{t}</a>'
                  for f, t in PAGES)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{html.escape(title)} · Marked to the Tape</title>
<link rel="stylesheet" href="{up}style.css"></head>
<body><header><div class="wrap"><h1>Marked to the Tape</h1><nav>{nav}</nav></div></header>
<main><div class="wrap">{body}</div></main>
<footer><div class="wrap">{NOTICE}<br>{stamp}</div></footer></body></html>
"""


# ---------------------------------------------------------------- desk summary

def figure(file: str, pattern: str, group: int = 1) -> str:
    """One figure read out of a committed report. Stops the build if the
    report or the line is missing: a summary must never show a number the
    reports do not hold."""
    p = REPORTS / file
    if not p.exists():
        raise SystemExit(f"desk summary: reports/{file} has not been produced")
    m = re.search(pattern, p.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit(f"desk summary: no match for {pattern!r} in reports/{file}")
    return m.group(group)


def number(text: str) -> float:
    return float(text.replace(",", ""))


def desk_figures(stem: str) -> dict[str, str]:
    """The handful of figures a desk would ask for first, for one book."""
    rv, hg, tl = f"{stem}_review.txt", f"{stem}_hedge.txt", f"{stem}_tail.txt"
    return dict(
        failed=figure(rv, r"failed a check: (\d+) \("),
        failed_pct=figure(rv, r"failed a check: \d+ \(([\d.]+)% of checkable"),
        gross=figure(rv, r"over the period: ([\d,]+) by repricing"),
        positions=figure(rv, r"(\d+) distinct positions failed"),
        once=figure(rv, r"at its largest: ([\d,]+)\."),
        one_day=figure(rv, r"Open on any one day: median [\d,]+, largest ([\d,]+);"),
        not_mark=figure(rv, r"NOT the likely fault: \d+ of \d+ \((\d+)%\)"),
        removed=figure(hg, r"\n\s+delta hedged\s+[\d,]+\s+(\d+)\s"),
        vol_before=figure(hg, r"daily st\. dev\. of ([\d,]+); with the hedge's P&L added"),
        vol_after=figure(hg, r"with the hedge's P&L added, ([\d,]+)\."),
        vega_cost=figure(hg, r"half the quoted spread -([\d,]+) "),
        var_days=figure(tl, r"(\d+) days tested"),
        var_breaks=figure(tl, r"went past it: (\d+),"),
        var_expected=figure(tl, r"99% VaR expects ([\d.]+)"),
        light=figure(tl, r"Basel traffic light: (\w+)"),
    )


def desk_summary() -> str:
    """Tiles and three decisions, written from the figures and nothing else."""
    def tile(label: str, value: str, note: str) -> str:
        return (f'<div class="tile"><div class="k">{html.escape(label)}</div>'
                f'<div class="v">{html.escape(value)}</div>'
                f'<div class="n">{html.escape(note)}</div></div>')

    out, got = "", {}
    for stem, title in BOOKS:
        f = got[stem] = desk_figures(stem)
        helped = number(f["vol_after"]) < number(f["vol_before"])
        out += f"<h3>{title}</h3>" + '<div class="tiles">' + "".join([
            tile("Marks that failed a check", f["failed"],
                 f"{f['failed_pct']}% of checkable position-days, on {f['positions']} positions"),
            tile("Most in dispute on one day", f"${f['one_day']}",
                 f"${f['gross']} if every failed day is added up; ${f['once']} counting each "
                 "position once"),
            tile("Failed, but the mark was not the likely fault", f"{f['not_mark']}%",
                 "an input or the partner's quote is the first thing to check"),
            tile("Delta hedge", f"{f['removed']}%",
                 "of daily variance removed, ratios from earlier days only"),
            tile("Index vega hedge", "Helped" if helped else "No benefit",
                 f"vol P&L st. dev. ${f['vol_before']} before, ${f['vol_after']} after; cost "
                 f"${f['vega_cost']}"),
            tile("Day-ahead 99% VaR", f"{f['var_breaks']} breaks in {f['var_days']} days",
                 f"{f['var_expected']} expected; Basel light {f['light'].lower()}"),
        ]) + "</div>"

    every = list(got.values())
    decide = []
    if all(number(f["not_mark"]) >= 30 for f in every):
        decide.append("Before challenging a flagged mark, check the projected dividend and the "
                      "other side's quote: on these books a third or more of failed checks "
                      "point there, not at the mark.")
    if all(number(f["vol_after"]) >= number(f["vol_before"]) for f in every):
        decide.append("Do not put on the index vega hedge tested here. It cost money and did "
                      "not reduce the vol part of the P&amp;L on either book.")
    late = [t for (s, t), f in zip(BOOKS, every) if f["light"].upper() != "GREEN"]
    if late:
        decide.append(f"Treat the {' and '.join(t.lower() for t in late)}'s VaR as too small "
                      "until it has taken in the recent moves: it was broken more often than a "
                      "correct one would be.")
    decide.append("What is left after the delta hedge is single-name risk. An index and a "
                  "sector fund cannot remove it; it has to be limited by name.")
    return ('<h2>Desk summary</h2><p class="note">Two made-up books marked at the exchange\'s '
            "prices over six months. Historical results, not live risk. Every figure is read "
            "from the reports on the other pages.</p>" + out
            + "<h3>What a desk would do with this</h3><ul>"
            + "".join(f"<li>{d}</li>" for d in decide) + "</ul>")


# ---------------------------------------------------------------- any day, any expiry

DAYS = REPORTS / "days"          # written by scripts/day_tables.py
DAY_PANELS = [("pair", "Call minus put", 2),          # a pair's quote bar is its two quotes'
              ("smile", "Strike minus smile", 1),
              ("exch_call", "Ours minus exchange: calls", 1),
              ("exch_put", "Ours minus exchange: puts", 1)]
CARRY = {"calibrated": "calibrated: the share price was implied from this expiry, so its "
                       "call-minus-put gap is zero by construction and left out",
         "declared only": "declared dividends only",
         "projected dividend": "rests on a projected dividend"}
BUCKET = re.compile(r"^pair_(\d\.\d\d-\d\.\d\d)$")


def table(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def num(v: str) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None                       # nan -> None


def vp(v: str, bar: float | None = None, bars: int = 1) -> str:
    """A difference in vol points as a table cell; bold when it is larger than
    the expiry's typical quote bar (two for a call-put pair)."""
    x = num(v)
    if x is None:
        return '<td class="num">–</td>'
    big = bar is not None and abs(x) > bars * bar
    return f'<td class="num{" big" if big else ""}">{x:+.2f}</td>'


def day_label(d: str) -> str:
    return date.fromisoformat(d).strftime("%a %d %b %Y").replace(" 0", " ")


def day_file(name: str, d: str) -> str:
    return f"day/{name}/{d}.html"


def day_index() -> dict[str, tuple[list[dict], list[dict]]]:
    """Every name that has day tables: (its days, its expiry rows)."""
    out = {}
    for n in NAMES:
        days, exps = DAYS / f"{n.lower()}_days.csv", DAYS / f"{n.lower()}_expiries.csv"
        if days.exists() and exps.exists():
            out[n] = (table(days), table(exps))
    return out


def days_page(index: dict) -> str:
    """The picker: each name's trading days, a row per month."""
    if not index:
        return '<p class="note">reports/days has not been produced yet.</p>'
    body = ("<h2>Any day, any expiry</h2>"
            "<p>Pick a name and a day. The day's page shows every expiry against the "
            "three references, in vol points, with the day's rejections and the funding "
            "spread it was priced with. From there, the previous and next days and the same day "
            "on the other names are one click away.</p>"
            '<p class="note">These are the days the front chart is drawn from: those priced with a '
            "funding spread. The first weeks of each name's files come before any spread had been "
            "measured from earlier days, so they are not here.</p>"
            '<div class="pick">' + "".join(f'<a href="#{n}">{n}</a>' for n in index) + "</div>")
    for n, (days, _) in index.items():
        months: dict[str, list[str]] = {}
        for r in days:
            months.setdefault(r["date"][:7], []).append(r["date"])
        body += f'<h3 id="{n}">{n}: {len(days)} days</h3>'
        for m, ds in months.items():
            body += (f'<div class="cal"><b>{date.fromisoformat(m + "-01").strftime("%b %Y")}</b>'
                     + "".join(f'<a href="{day_file(n, d)}">{int(d[8:])}</a>' for d in ds)
                     + "</div>")
    return body


def one_day_page(name: str, i: int, days: list[dict], exps: list[dict],
                 on_day: dict[str, list[str]]) -> str:
    """One name on one day: the day in numbers, then every expiry."""
    r, d = days[i], days[i]["date"]
    up = "../../"
    steps = [f'<a href="{up}days.html#{name}">all {name} days</a>']
    if i > 0:
        steps.insert(0, f'<a href="{days[i - 1]["date"]}.html">&larr; {day_label(days[i - 1]["date"])}</a>')
    if i + 1 < len(days):
        steps.append(f'<a href="{days[i + 1]["date"]}.html">{day_label(days[i + 1]["date"])} &rarr;</a>')
    others = [n for n in on_day.get(d, []) if n != name]
    rows = [e for e in exps if e["date"] == d]

    spread = num(r["spread_bp"])
    body = (f"<h2>{name}, {day_label(d)}</h2>"
            f'<p class="steps">{" &middot; ".join(steps)}</p>'
            + (f'<p class="steps">Same day on another name:</p><div class="pick">' + "".join(
                f'<a href="../{n}/{d}.html">{n}</a>' for n in others) + "</div>" if others else "")
            + '<p class="steps">Expiries:</p><div class="pick">' + "".join(
                f'<a href="#e{e["expiry"]}">{date.fromisoformat(e["expiry"]).strftime("%d %b %y").lstrip("0")}</a>'
                for e in rows) + "</div>"
            + "<ul>"
            f'<li>{r["rows"]} option rows; {r["inverted"]} inverted, {r["rejected"]} rejected'
            + (f' ({html.escape(r["rejections"])})' if r["rejections"] else "") + ".</li>"
            f'<li>Evidence: {r["traded"]} traded, {r["quoted"]} quoted only, '
            f'{r["settlement_only"]} settlement only, {r["no_price"]} with no price.</li>'
            + (f'<li>Rates: {html.escape(r["rates"])} plus a funding spread of {spread:.1f} basis '
               f'points a year, FITTED FROM OPTIONS ({html.escape(r["spread_source"])}).</li>'
               if spread is not None else
               f'<li>Rates: {html.escape(r["rates"])}, no funding spread applied.</li>')
            + "</ul>"
            '<p class="note">Differences are in vol points, the mean over the strikes in each '
            "expiry. Bold: larger than the expiry's median quote bar (two bars for a call-put "
            "pair), so too large for the quote's own width to explain on its own. Ours minus the "
            "exchange compares two models, not two prices.</p>")

    head = ("<tr><th>Expiry</th><th class=num>Days</th><th>Carry</th>"
            "<th class=num>Rows</th><th class=num>Rejected</th><th class=num>Quote bar</th>"
            + "".join(f"<th class=num>{t}</th>" for _, t, _ in DAY_PANELS) + "</tr>")
    body += "<table>" + head
    for e in rows:
        bar = num(e["quote_bar"])
        body += (f'<tr><td><a href="#e{e["expiry"]}">{e["expiry"]}</a></td>'
                 f'<td class=num>{e["days"]}</td><td>{html.escape(e["carry"] or "–")}</td>'
                 f'<td class=num>{e["rows"]}</td><td class=num>{e["rejected"]}</td>'
                 + (f"<td class=num>{bar:.2f}</td>" if bar is not None else "<td class=num>–</td>")
                 + "".join(vp(e[f"{p}_mean"], bar, k) for p, _, k in DAY_PANELS) + "</tr>")
    body += "</table>"

    buckets = [m.group(1) for k in (exps[0] if exps else {}) if (m := BUCKET.match(k))]
    for e in rows:
        bar = num(e["quote_bar"])
        fit = {"slice": "its own curve", "surface": "the day's surface (too few strikes for a "
               "curve of its own)"}.get(e["smile_fit"], "no curve")
        body += (f'<h3 id="e{e["expiry"]}">Expiry {e["expiry"]}, {e["days"]} days</h3>'
                 f'<p class="note">{html.escape(CARRY.get(e["carry"], e["carry"]))}. Smile: '
                 f"{fit}"
                 + ("; POOR FIT: the curve misses a typical quote by more than a quote bar, so "
                    "its residuals describe the curve, not the quotes" if e["smile_poor_fit"] == "True"
                    else "")
                 + f'. {e["rows"]} rows, {e["rejected"]} rejected'
                 + (f' ({html.escape(e["rejections"])})' if e["rejections"] else "")
                 + f'; {e["traded"]} traded, {e["quoted"]} quoted only, '
                 f'{e["settlement_only"]} settlement only.</p>'
                 "<table><tr><th>Reference</th><th class=num>Count</th>"
                 + "".join(f"<th class=num>{b}</th>" for b in buckets)
                 + "<th class=num>All</th></tr>")
        for p, t, k in DAY_PANELS:
            body += (f"<tr><td>{t}</td><td class=num>{e[f'{p}_n']}</td>"
                     + "".join(vp(e[f"{p}_{b}"], bar, k) for b in buckets)
                     + vp(e[f"{p}_mean"], bar, k) + "</tr>")
        body += ("</table><p class=note>Count: call-put pairs on the first line, options on the "
                 "others. Columns are strike over share price; strikes outside "
                 f"{buckets[0][:4]} to {buckets[-1][-4:]} count in All only.</p>" if buckets else "</table>")
    return body


# ---------------------------------------------------------------- pages

def build(out: Path) -> list[Path]:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    findings = (ROOT / "docs/findings.md").read_text(encoding="utf-8")
    cases = (ROOT / "docs/investigations.md").read_text(encoding="utf-8")
    method = (ROOT / "docs/methodology.md").read_text(encoding="utf-8")
    pdf = REPORTS / "methodology.pdf"
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
        + desk_summary()
        + "<h2>Model against market</h2>"
        + chart("ry_front_chart.png", "Royal Bank: implied vol against three references")
        + '<p class="note">Royal Bank options, six months. Each cell is a difference in vol '
          'points; bold means more than two standard errors from zero. '
          '<a href="front.html">Fifteen names</a>; <a href="days.html">any day or expiry</a>.</p>'
        "<h2>What is left after the hedge</h2>"
        + chart("diversified_hedge.png", "The diversified book after each hedge")
        + '<p class="note">A made-up book marked at the exchange\'s prices each day. '
          '<a href="hedge.html">The three stages</a>.</p>'
        "<h2>What it has found so far</h2>" + markdown(section(readme, "What it has found so far"))
        + '<p class="note">How every number is made: <a href="method.html">the method</a>'
        + (', also as a <a href="methodology.pdf">PDF</a>' if pdf.exists() else "")
        + ".</p>")

    pick = '<div class="pick">' + "".join(f'<a href="#{n}">{n}</a>' for n in NAMES) + "</div>"
    body = ("<h2>Model against market, by name</h2>"
            "<p>Each implied vol is compared with three references: the call against the put "
            "at the same strike, the strike against a smile fitted through its neighbours, and "
            "the exchange's own published vol. Differences are pooled over days, by days to "
            "expiry and by strike relative to the share price. To see one day and its "
            'expiries instead of the pool, <a href="days.html">pick a day</a>.</p>' + pick
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
    body += ("<h2>Where the vega sits, and the delta in index terms</h2>"
             "<p>Vega by days to expiry and strike over share price, on the front chart's own "
             "cells, by name: a one-month vol point on one name is not a six-month vol point "
             "on another. Vega is counted in a cell only where the exchange quotes a smile "
             "around it. Each name's option delta is also mapped to the TSX 60 by its beta, "
             "with its correlation beside it, because a beta is worth something only where the "
             "index explains the name.</p>")
    for stem, title in BOOKS:
        body += (f"<h3>{title}</h3>" + chart(f"{stem}_grid.png", f"{title}: vega by cell")
                 + folded("The numbers", report(f"{stem}_grid.txt")))
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
    body += ("<h3>Where the interlisted names trade</h3>"
             "<p>Thirteen names list in Toronto and New York, one share for one. Toronto's "
             "share of the two listings' volume, the days only one market is open, and how "
             "long each book takes to exit at a fifth of its volume. Toronto here is the "
             "TSX alone and New York every US venue, so Toronto's share is a lower "
             "bound.</p>"
             + chart("venues.png", "Toronto against New York, by year")
             + folded("The numbers", report("venues.txt")))
    pages["hedge.html"] = body

    body = ("<h2>Stress and tail risk</h2>"
            "<p>Everything on this page that prices a move is a <strong>model price on a "
            "rule-based vol</strong>, not a mark. The first part of each tail report is the "
            "exception: it uses the book's actual marked P&amp;L.</p>"
            "<h3>The vol rule is a control</h3>"
            "<p>A share falling 20% with its implied vol unchanged has never happened. The "
            "same book and the same index move are repriced under four rules for what vol "
            "does; only the last lets it rise in a fall. The rule and its size are options "
            "of <code>scripts/stress.py</code>.</p>")
    for stem, title in BOOKS:
        body += f"<h3>{title}: stress</h3>" + report(f"{stem}_stress.txt")
    body += ("<h3>Real episodes, replayed on today's book</h3>"
             "<p>January 2015, February to March 2020, April 2020 and the 2022 to 2023 rate "
             "cycle: every name moves as it really moved, together, with time passing as it "
             "did. Vols come from the New York listings, currency taken out, where one "
             "exists, read at each option's own strike on the smile; each option-day is "
             "labelled by where its vol came from.</p>")
    for stem, title in BOOKS:
        body += (f"<h3>{title}: episodes</h3>"
                 + chart(f"{stem}_episodes.png", f"{title}: historical episodes")
                 + folded("The numbers", report(f"{stem}_episodes.txt")))
    body += ("<h3>The heavy-oil discount</h3>"
             "<p>Western Canadian Select trades below WTI by an amount that moves for its own "
             "reasons. Each name's monthly sensitivity to that discount is fitted beside the "
             "index and WTI, and a month with only the discount widening is priced on the book. "
             "No name's sensitivity is distinguishable from zero.</p>")
    for stem, title in BOOKS:
        body += f"<h3>{title}: oil discount</h3>" + report(f"{stem}_oil.txt")
    body += ("<h3>The worst days, who causes them, and whether the VaR held</h3>"
             "<p>Historical VaR and expected shortfall on the marks; a day-ahead VaR tested "
             "against what happened, with Kupiec's test and the Basel traffic light; and each "
             "position's share of the tail, from the book repriced under every day's moves "
             "since 2015.</p>")
    for stem, title in BOOKS:
        body += (f"<h3>{title}: tail risk</h3>"
                 + chart(f"{stem}_tail.png", f"{title}: VaR backtest and component ES")
                 + report(f"{stem}_tail.txt"))
    body += ("<h3>The morning's P&amp;L explain, and the limits</h3>"
             "<p>Each day's change in the marks through the previous evening's Greeks "
             "(delta, gamma, vega, theta, rho) and the residual they leave; then limits on "
             "mapped delta, vega by maturity and one-day expected shortfall, all made from "
             "one stated loss tolerance, with usage on every day.</p>")
    for stem, title in BOOKS:
        body += (f"<h3>{title}: P&amp;L explain and limits</h3>"
                 + chart(f"{stem}_limits.png", f"{title}: limit usage")
                 + folded("The numbers", report(f"{stem}_limits.txt")))
    pages["fund.html"] = body

    pages["vol.html"] = (
        "<h2>Implied vol against the vol that followed</h2>"
        "<p>Implied vol is an option's price quoted as a volatility. Realized vol is how much "
        "the share then moved. The comparison looks forward: implied on the first day of a "
        "window against what was realized from that day to expiry. Six months gives five "
        "windows that do not overlap, so this describes what happened and tests nothing.</p>"
        + chart("realized_implied.png", "Implied minus realized by expiry, and a hedged straddle")
        + report("realized_implied.txt"))

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
    index = day_index()
    pages["days.html"] = days_page(index)
    pages["method.html"] = (
        ('<p class="note">The same text as a <a href="methodology.pdf">PDF</a>.</p>'
         if pdf.exists() else "")
        + markdown(method.split("\n", 1)[1] if method.startswith("# ") else method))
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
    if pdf.exists():
        shutil.copyfile(pdf, out / "methodology.pdf")
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / "style.css").write_text(CSS, encoding="utf-8")
    written = []
    for file, body in pages.items():
        (out / file).write_text(page(file, body, stamp), encoding="utf-8")
        written.append(out / file)
    on_day: dict[str, list[str]] = {}
    for n, (days, _) in index.items():
        for r in days:
            on_day.setdefault(r["date"], []).append(n)
    for n, (days, exps) in index.items():
        (out / "day" / n).mkdir(parents=True, exist_ok=True)
        for i, r in enumerate(days):
            f = out / day_file(n, r["date"])
            f.write_text(page("days.html", one_day_page(n, i, days, exps, on_day), stamp,
                              title=f"{n} {r['date']}", up="../../"), encoding="utf-8")
            written.append(f)
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "site"
    files = build(target)
    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    print(f"{len(files)} pages and {len(list((target / 'img').rglob('*.png')))} charts in "
          f"{target}, {size / 1e6:.1f} MB")
