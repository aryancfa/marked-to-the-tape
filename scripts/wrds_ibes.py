"""
wrds_ibes.py — analysts' dividend forecasts from I/B/E/S on WRDS (build sheet A4).

Run it yourself, because it uses your WRDS login (from your pgpass file; the
username from the WRDS_USERNAME environment variable if it is set):

    .venv/Scripts/python scripts/wrds_ibes.py

Why. Dividends that have not been declared are projected here by a rule (a
raise the size of last year's). The options give a second number, the
dividend they imply (scripts/funding_split.py). Analysts' consensus is a
third, and it is independent of both: it uses neither my rule nor option
prices. Three numbers that agree are worth more than one.

What it does, in order, assuming nothing about the layout beyond the word
"ibes" in a library name:

  1. lists the I/B/E/S libraries this account can see, and their tables;
  2. finds the summary-statistics tables (the ones with a `measure`, a
     `meanest` and a `statpers` column) and says which measures they hold;
  3. finds this project's names in them, by exchange ticker and by company
     name, and PRINTS what it matched so a wrong match can be seen;
  4. pulls the consensus dividend per share (measure DPS) since January 2025
     for those names and saves it.

Saved under data/raw/wrds/ (gitignored; WRDS data cannot be redistributed):
  ibes_catalogue.txt   what was found, including anything that failed
  ibes_dps.csv         the consensus rows, if step 4 got that far

If I/B/E/S is not in the subscription, step 1 says so and the script stops.

Credentials never go in this file or anywhere in git (hard rule 3).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/wrds"

# exchange ticker -> a fragment of the company name, to find it and to check the match
NAMES = {
    "RY": "ROYAL BANK", "TD": "TORONTO-DOMINION", "BNS": "NOVA SCOTIA",
    "BMO": "BANK OF MONTREAL", "CM": "CANADIAN IMPERIAL", "MFC": "MANULIFE",
    "CNQ": "CANADIAN NATURAL", "SU": "SUNCOR", "ENB": "ENBRIDGE",
    "CNR": "CANADIAN NATIONAL", "BCE": "BCE", "ABX": "BARRICK", "SHOP": "SHOPIFY",
}
SINCE = "2025-01-01"
NEEDED = {"measure", "meanest", "statpers", "ticker"}


def main() -> None:
    try:
        import wrds
    except ImportError:
        raise SystemExit("the wrds package is missing: "
                         ".venv/Scripts/python -m pip install -r requirements.txt")
    lines: list[str] = []

    def say(text: str = "") -> None:
        print(text)
        lines.append(text)

    def save() -> None:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "ibes_catalogue.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nsaved: {(OUT / 'ibes_catalogue.txt').relative_to(ROOT)}")

    user = os.environ.get("WRDS_USERNAME")
    db = wrds.Connection(wrds_username=user) if user else wrds.Connection()
    libs = sorted(l for l in db.list_libraries() if "ibes" in l.lower())
    say(f"I/B/E/S libraries visible: {libs or 'NONE'}")
    if not libs:
        say("I/B/E/S is not in this subscription (or is listed under another name).")
        db.close()
        return save()

    # 1 and 2: tables, and which are summary statistics
    summary: list[tuple[str, str, list[str]]] = []
    for lib in libs:
        try:
            tables = sorted(db.list_tables(library=lib))
        except Exception as exc:
            say(f"\n== {lib}: cannot list tables ({type(exc).__name__}); not subscribed?")
            continue
        say(f"\n== {lib}: {len(tables)} tables: {tables}")
        for t in tables:
            try:
                cols = [c.lower() for c in db.describe_table(library=lib, table=t)["name"]]
            except Exception:
                continue
            if NEEDED <= set(cols):
                summary.append((lib, t, cols))
                say(f"   summary table {t}: {cols}")
    if not summary:
        say("\nno table with measure, meanest, statpers and ticker columns was found")
        db.close()
        return save()

    # which of them hold dividends per share
    with_dps = []
    for lib, t, cols in summary:
        try:
            m = db.raw_sql(f"select measure, count(*) as n, max(statpers) as last "
                           f"from {lib}.{t} where statpers >= '{SINCE}' group by measure "
                           f"order by measure")
            say(f"\n   {lib}.{t} measures since {SINCE}: "
                + ", ".join(f"{a} ({int(b)}, to {c})" for a, b, c in
                            zip(m["measure"], m["n"], m["last"])))
            if "DPS" in set(m["measure"]):
                with_dps.append((lib, t, cols))
        except Exception as exc:
            say(f"   {lib}.{t}: measure count failed ({type(exc).__name__}: {exc})")
    if not with_dps:
        say("\nno summary table holds the DPS measure since " + SINCE)
        db.close()
        return save()

    # The same data is offered several times over (two library names, adjusted
    # and unadjusted, with and without the 'n' prefix). Unadjusted figures are
    # the ones as published at the time, which is what a point-in-time
    # comparison needs, so take the plain unadjusted tables of one library.
    first = [x for x in with_dps if x[1].startswith("statsumu_xeps")]
    lib0 = "ibes" if any(x[0] == "ibes" for x in first) else (first[0][0] if first else None)
    with_dps = [x for x in first if x[0] == lib0] or with_dps[:1]
    say()
    say("pulling from: " + ", ".join(f"{a}.{b}" for a, b, _ in with_dps))

    # 3 and 4: find the names and pull
    import pandas as pd
    frames = []
    for lib, t, cols in with_dps:
        name_col = next((c for c in ("cname", "cnameu", "company") if c in cols), None)
        tests = []
        if "oftic" in cols:
            tests.append("oftic in (" + ", ".join(f"'{k}'" for k in NAMES) + ")")
        if name_col:
            # strpos, not LIKE: the driver reads a percent sign as a parameter marker
            tests += [f"strpos(upper({name_col}), '{frag}') > 0" for frag in NAMES.values()]
        if not tests:
            say(f"\n   {lib}.{t}: no exchange ticker or company name column to match on")
            continue
        keep = [c for c in ("ticker", "cusip", "oftic", name_col, "statpers", "measure",
                            "fiscalp", "fpi", "fpedats", "numest", "medest", "meanest",
                            "stdev", "highest", "lowest", "curcode", "curr_act", "usfirm")
                if c and c in cols]
        q = (f"select {', '.join(keep)} from {lib}.{t} where measure = 'DPS' "
             f"and statpers >= '{SINCE}' and ({' or '.join(tests)})")
        try:
            df = db.raw_sql(q)
        except Exception as exc:
            say(f"\n   {lib}.{t}: pull failed ({type(exc).__name__}: {exc})")
            continue
        df["source_table"] = f"{lib}.{t}"
        frames.append(df)
        ident = [c for c in ("ticker", "oftic", name_col, "curcode") if c and c in df.columns]
        say(f"\n   {lib}.{t}: {len(df)} DPS rows. Matched (CHECK these are the right companies):")
        say("   " + df[ident].drop_duplicates().sort_values(ident).to_string(index=False)
            .replace("\n", "\n   "))
    db.close()

    if frames:
        out = pd.concat(frames, ignore_index=True)
        out.to_csv(OUT / "ibes_dps.csv", index=False)
        say(f"\nsaved {len(out)} rows: data/raw/wrds/ibes_dps.csv (gitignored)")
    save()


if __name__ == "__main__":
    sys.exit(main())
