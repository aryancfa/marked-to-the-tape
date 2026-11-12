"""
wrds_catalogue.py — what does this WRDS account actually hold? (build sheet: WRDS)

Run it yourself, in your own terminal, because the first connection asks for
your WRDS username and password and offers to save them in your pgpass file:

    .venv/Scripts/python scripts/wrds_catalogue.py

It downloads NO prices. It only reads the catalogue: which OptionMetrics
libraries the account can see, their tables and columns, the security ids for
the names in this project, and the first and last dates available. The result
is printed and saved to data/raw/wrds/catalogue.txt (gitignored).

Nothing about table or column names is assumed here beyond the library
prefix. The script lists what exists and reports it, so the next step is
written against the real layout and not a remembered one.

Credentials never go in this file or anywhere in git (hard rule 3).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/wrds/catalogue.txt"

# New York listings of the interlisted names, plus the Canada ETF
TICKERS = ["RY", "TD", "BNS", "BMO", "CM", "CNQ", "SU", "EWC"]
# Library name fragments worth showing: OptionMetrics (US, Canada, Europe...),
# and the two usual sources of daily share prices
WANTED = ("optionm", "ivydb", "crsp", "comp")


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

    db = wrds.Connection()
    libs = sorted(db.list_libraries())
    mine = [l for l in libs if any(w in l.lower() for w in WANTED)]
    say(f"libraries visible: {len(libs)}; of interest here: {mine}")

    for lib in [l for l in mine if "optionm" in l.lower() or "ivydb" in l.lower()]:
        try:
            tables = sorted(db.list_tables(library=lib))
        except Exception as exc:                        # listed but not subscribed
            say(f"\n== {lib}: cannot list tables ({type(exc).__name__})")
            continue
        yearly = sorted({t.rstrip("0123456789") for t in tables if t[-4:].isdigit()})
        years = sorted({t[-4:] for t in tables if t[-4:].isdigit()})
        single = [t for t in tables if not t[-4:].isdigit()]
        say(f"\n== {lib}: {len(tables)} tables")
        say(f"   one per year: {yearly}  years {years[0] if years else '-'} to "
            f"{years[-1] if years else '-'}")
        say(f"   others: {single}")

        # columns of the tables this project will need, whatever they are called here
        for t in single:
            if any(k in t.lower() for k in ("sec", "name", "distr", "zero", "index", "info")):
                try:
                    cols = list(db.describe_table(library=lib, table=t)["name"])
                    say(f"   {t}: {cols}")
                except Exception as exc:
                    say(f"   {t}: columns not readable ({type(exc).__name__})")

        # security ids: look in any table that has both a ticker and a secid column
        for t in single:
            try:
                cols = [c.lower() for c in db.describe_table(library=lib, table=t)["name"]]
            except Exception:
                continue
            if "ticker" in cols and "secid" in cols:
                wanted = ", ".join(f"'{x}'" for x in TICKERS)
                extra = [c for c in ("issuer", "cusip", "exchange_d", "effect_date",
                                     "class", "index_flag") if c in cols]
                q = (f"select distinct secid, ticker{''.join(', ' + c for c in extra)} "
                     f"from {lib}.{t} where ticker in ({wanted}) order by ticker, secid")
                try:
                    df = db.raw_sql(q)
                    say(f"\n   ids from {lib}.{t}:")
                    say("   " + df.to_string(index=False).replace("\n", "\n   "))
                except Exception as exc:
                    say(f"   {lib}.{t}: id query failed ({type(exc).__name__}: {exc})")

        # latest date: the newest yearly table of each family that has a date column
        for fam in yearly:
            newest = max(t for t in tables if t.startswith(fam) and t[-4:].isdigit())
            try:
                cols = [c.lower() for c in db.describe_table(library=lib, table=newest)["name"]]
                if "date" in cols:
                    d = db.raw_sql(f"select min(date) as first, max(date) as last "
                                   f"from {lib}.{newest}")
                    say(f"   {newest}: {d['first'].iloc[0]} to {d['last'].iloc[0]}")
            except Exception as exc:
                say(f"   {newest}: dates not readable ({type(exc).__name__})")

    db.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(f"\nsaved: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
