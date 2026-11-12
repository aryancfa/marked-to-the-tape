"""test_book.py: the position format (build sheet A3). Positions are made up."""

from datetime import date
from pathlib import Path

import pytest

from book import BookError, Position, load_book

HEADER = "kind,underlying,call_put,strike,expiry,quantity,multiplier,venue\n"
ROOT = Path(__file__).resolve().parents[1]


def write(tmp_path, body):
    p = tmp_path / "book.csv"
    p.write_text(HEADER + body, encoding="utf-8")
    return p


def test_reads_the_four_kinds_of_position(tmp_path):
    book = load_book(write(tmp_path,
                           "option,RY,C,250,2027-01-15,10,100,MX\n"
                           "future,SXF,,,2026-12-17,-2,200,MX\n"
                           "etf,XIU,,,,500,1,TSX\n"
                           "equity,TD,,,,-300,1,TSX\n"))
    assert book[0] == Position("option", "RY", 10.0, 100.0, "MX", "C", 250.0, date(2027, 1, 15))
    assert book[1].kind == "future" and book[1].expiry == date(2026, 12, 17)
    assert book[1].quantity == -2.0 and book[1].multiplier == 200.0
    assert book[2].kind == "etf" and book[2].strike is None and book[2].expiry is None
    assert book[3].quantity == -300.0


def test_blank_multiplier_and_venue_take_the_usual_values(tmp_path):
    """An option contract is 100 shares on the Montreal Exchange; a share or
    ETF unit is one, in Toronto. A future has no usual size, so it must say."""
    book = load_book(write(tmp_path, "option,RY,P,240,2027-01-15,5,,\n"
                                     "equity,RY,,,,100,,\n"))
    assert (book[0].multiplier, book[0].venue) == (100.0, "MX")
    assert (book[1].multiplier, book[1].venue) == (1.0, "TSX")
    with pytest.raises(BookError, match="multiplier"):
        load_book(write(tmp_path, "future,SXF,,,2026-12-17,1,,\n"))


def test_every_bad_row_is_reported_at_once_with_its_line(tmp_path):
    """Someone pasting in their own book should see every problem in one go,
    in words, with the line it is on."""
    p = write(tmp_path,
              "option,RY,C,250,2027-01-15,10,100,MX\n"       # line 2: fine
              "option,RY,X,250,2027-01-15,10,100,MX\n"       # line 3: call_put
              "option,RY,C,,2027-01-15,10,100,MX\n"          # line 4: strike
              "option,RY,C,250,15/01/2027,10,100,MX\n"       # line 5: date
              "swap,RY,,,,10,1,TSX\n"                        # line 6: kind
              "equity,,,,,10,1,TSX\n"                        # line 7: underlying
              "equity,TD,,,,0,1,TSX\n"                       # line 8: zero quantity
              "equity,TD,C,250,,10,1,TSX\n")                 # line 9: option fields on a share
    with pytest.raises(BookError) as err:
        load_book(p)
    msg = str(err.value)
    for line, word in ((3, "C or P"), (4, "strike"), (5, "YYYY-MM-DD"), (6, "kind"),
                       (7, "underlying"), (8, "quantity"), (9, "only an option")):
        assert f"line {line}:" in msg and word in msg
    assert "line 2:" not in msg
    assert msg.count("\n") == 7                    # a heading line and seven problems


def test_missing_columns_are_named(tmp_path):
    p = tmp_path / "book.csv"
    p.write_text("kind,underlying,quantity\nequity,RY,10\n", encoding="utf-8")
    with pytest.raises(BookError, match="missing columns.*strike"):
        load_book(p)


def test_an_empty_book_is_an_error(tmp_path):
    with pytest.raises(BookError, match="no positions"):
        load_book(write(tmp_path, ""))


@pytest.mark.parametrize("name", ["bank_heavy.csv", "diversified.csv"])
def test_the_sample_books_load(name):
    book = load_book(ROOT / "books" / name)
    kinds = {p.kind for p in book}
    assert "option" in kinds and len(book) >= 8
    assert all(p.expiry > date(2026, 11, 30) for p in book if p.expiry)


def test_bank_heavy_book_is_mostly_banks():
    book = load_book(ROOT / "books" / "bank_heavy.csv")
    banks = {"RY", "TD", "BNS", "BMO", "CM"}
    options = [p for p in book if p.kind == "option"]
    assert sum(p.underlying in banks for p in options) / len(options) > 0.7
