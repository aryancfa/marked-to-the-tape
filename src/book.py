"""
book.py — what a book of positions looks like on disk (build sheet A3).

A book is a CSV with one position per line:

    kind,underlying,call_put,strike,expiry,quantity,multiplier,venue
    option,RY,C,250,2027-01-15,10,100,MX
    future,SXF,,,2026-12-17,-2,200,MX
    etf,XIU,,,,500,1,TSX
    equity,TD,,,,-300,1,TSX

kind        option, future, etf or equity
quantity    contracts for an option or future, units for a share or ETF.
            Positive is long, negative is short.
multiplier  what one unit of quantity is worth per point of price: 100
            shares for a listed option, the contract size for a future, 1 for
            a share. Risk is quantity times multiplier times a per-share
            number, so leaving it out would misstate every option a hundredfold.
venue       where it trades, which decides the trading calendar.

A book someone pastes in will have mistakes. Every one is collected and
reported together, by line, in words. Nothing is guessed or silently dropped:
a position the system misread is worse than one it refused.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

KINDS = ("option", "future", "etf", "equity")
COLUMNS = ("kind", "underlying", "call_put", "strike", "expiry", "quantity",
           "multiplier", "venue")

# the usual contract size and venue, used only where the file leaves them blank
_DEFAULT_MULTIPLIER = {"option": 100.0, "etf": 1.0, "equity": 1.0}
_DEFAULT_VENUE = {"option": "MX", "future": "MX", "etf": "TSX", "equity": "TSX"}


class BookError(ValueError):
    """The book file cannot be used. The message lists every problem."""


@dataclass(frozen=True)
class Position:
    kind: str
    underlying: str
    quantity: float                 # signed: positive long, negative short
    multiplier: float
    venue: str
    call_put: str | None = None     # options only
    strike: float | None = None     # options only
    expiry: date | None = None      # options and futures


def _number(text: str, what: str, problems: list[str]) -> float | None:
    try:
        return float(text)
    except ValueError:
        problems.append(f"{what} '{text}' is not a number")
        return None


def _row(row: dict[str, str]) -> tuple[Position | None, list[str]]:
    g = {k: (row.get(k) or "").strip() for k in COLUMNS}
    kind = g["kind"].lower()
    problems: list[str] = []
    if kind not in KINDS:
        return None, [f"kind '{g['kind']}' is not one of {', '.join(KINDS)}"]
    if not g["underlying"]:
        problems.append("underlying is missing")

    quantity = _number(g["quantity"], "quantity", problems) if g["quantity"] else None
    if not g["quantity"]:
        problems.append("quantity is missing")
    elif quantity == 0:
        problems.append("quantity is zero, so this is not a position")

    if g["multiplier"]:
        multiplier = _number(g["multiplier"], "multiplier", problems)
        if multiplier is not None and multiplier <= 0:
            problems.append("multiplier must be above zero")
    elif kind in _DEFAULT_MULTIPLIER:
        multiplier = _DEFAULT_MULTIPLIER[kind]
    else:
        multiplier = None
        problems.append("multiplier is missing; a future has no usual contract size")

    call_put, strike, expiry = None, None, None
    if kind == "option":
        call_put = g["call_put"].upper()
        if call_put not in ("C", "P"):
            problems.append(f"call_put '{g['call_put']}' must be C or P")
        if not g["strike"]:
            problems.append("strike is missing for an option")
        else:
            strike = _number(g["strike"], "strike", problems)
            if strike is not None and strike <= 0:
                problems.append("strike must be above zero")
    elif g["call_put"] or g["strike"]:
        problems.append("call_put and strike belong to only an option; leave them blank")

    if kind in ("option", "future"):
        if not g["expiry"]:
            problems.append(f"expiry is missing for {'an option' if kind == 'option' else 'a future'}")
        else:
            try:
                expiry = date.fromisoformat(g["expiry"])
            except ValueError:
                problems.append(f"expiry '{g['expiry']}' must be written YYYY-MM-DD")
    elif g["expiry"]:
        problems.append("expiry belongs to an option or a future; leave it blank")

    if problems:
        return None, problems
    return Position(kind, g["underlying"].upper(), quantity, multiplier,
                    (g["venue"] or _DEFAULT_VENUE[kind]).upper(), call_put, strike, expiry), []


def load_book(path: str | Path) -> list[Position]:
    """Read a book. Raises BookError listing every problem, by line."""
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        have = [c.strip() for c in (reader.fieldnames or [])]
        missing = [c for c in COLUMNS if c not in have]
        if missing:
            raise BookError(f"{path.name}: missing columns {', '.join(missing)}. "
                            f"Expected: {', '.join(COLUMNS)}")
        book, problems = [], []
        for line, row in enumerate(reader, start=2):        # line 1 is the header
            if not any((v or "").strip() for v in row.values()):
                continue                                    # a blank line
            pos, bad = _row({(k or "").strip(): v for k, v in row.items()})
            if pos is not None:
                book.append(pos)
            problems += [f"  line {line}: {b}" for b in bad]
    if problems:
        raise BookError(f"{path.name}: {len(problems)} problem(s)\n" + "\n".join(problems))
    if not book:
        raise BookError(f"{path.name}: no positions in the file")
    return book
