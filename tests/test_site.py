"""test_site.py: the live page is built from committed, derived files only."""

import hashlib
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_site as bs


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    bs.build(out)
    return out


def test_every_page_is_built_and_says_what_it_shows(site):
    for file, title in bs.PAGES:
        text = (site / file).read_text(encoding="utf-8")
        assert bs.NOTICE in text and title in text
        assert 'name="robots" content="noindex,nofollow"' in text
    assert "Disallow: /" in (site / "robots.txt").read_text()


def test_the_builder_never_opens_the_data_folder():
    """Hard rule 1 by construction: the only inputs are reports/, the README
    and two documents. Nothing under data/ or private/ is named in the code."""
    code = Path(bs.__file__).read_text(encoding="utf-8")
    code = code.split('"""', 2)[2]                      # the module note mentions data/
    assert not re.search(r"""["'/](data|private)/""", code)
    assert "data/raw" not in code and "derived" not in code.replace("derived statistics", "")


def test_every_chart_a_page_shows_was_copied(site):
    for file, _ in bs.PAGES:
        for src in re.findall(r'<img src="([^"]+)"', (site / file).read_text(encoding="utf-8")):
            assert (site / src).exists(), f"{file} shows {src}, which is not in the site"


def test_no_page_scrolls_sideways_because_of_a_table_or_report(site):
    """Wide tables and reports scroll inside their own box."""
    assert "overflow-x:auto" in bs.CSS and "max-width:100%" in bs.CSS


def test_markdown_handles_what_the_documents_use():
    out = bs.markdown("## A heading\n\nSome **bold** and `code` and a "
                      "[link](docs/findings.md).\n\n- one\n  continued\n  - nested\n- two\n\n"
                      "| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert "<h3>A heading</h3>" in out and "<strong>bold</strong>" in out
    assert '<a href="findings.html">link</a>' in out and "<code>code</code>" in out
    assert out.count("<ul>") == 2 and out.count("</ul>") == 2 and "one continued" in out
    assert "<th>a</th>" in out and "<td>2</td>" in out


def test_the_method_page_carries_the_whole_document_and_its_pdf(site):
    """Every section of docs/methodology.md is on the Method page, and the PDF
    it links to is in the site whenever the typeset copy exists."""
    text = (site / "method.html").read_text(encoding="utf-8")
    md = (bs.ROOT / "docs/methodology.md").read_text(encoding="utf-8")
    for heading in re.findall(r"^## (.+)$", md, flags=re.M):
        assert f"<h3>{bs.inline(heading)}</h3>" in text
    if (bs.REPORTS / "methodology.pdf").exists():
        assert 'href="methodology.pdf"' in text and (site / "methodology.pdf").exists()


def test_the_methodology_pdf_is_typeset_from_the_current_text():
    """The committed PDF must not lag the document: rebuild it with
    scripts/methodology_pdf.py after editing docs/methodology.md."""
    pdf = bs.REPORTS / "methodology.pdf"
    md = bs.ROOT / "docs/methodology.md"
    if not pdf.exists():
        pytest.skip("no typeset copy yet")
    digest = hashlib.sha256(md.read_bytes().replace(bytes([13, 10]), bytes([10]))).hexdigest()
    assert f"source sha256 {digest}".encode() in pdf.read_bytes(), (
        "reports/methodology.pdf was typeset from an older docs/methodology.md")


def test_every_day_is_reachable_and_every_link_on_a_day_page_resolves(site):
    """C9: any day or expiry in under a minute. The picker links every day of
    every name with tables; a day page's links (previous, next, same day on
    other names, back to the picker) all land on pages that exist, and every
    expiry link has its section."""
    index = bs.day_index()
    if not index:
        pytest.skip("reports/days not produced yet")
    picker = (site / "days.html").read_text(encoding="utf-8")
    for n, (days, exps) in index.items():
        for r in days:
            assert f'href="{bs.day_file(n, r["date"])}"' in picker
    for n, (days, exps) in index.items():
        for r in (days[0], days[len(days) // 2], days[-1]):
            f = site / bs.day_file(n, r["date"])
            text = f.read_text(encoding="utf-8")
            assert bs.NOTICE in text and 'href="../../style.css"' in text
            for href in re.findall(r'href="([^"#]+\.html)', text):
                assert (f.parent / href).resolve().exists(), f"{f.name} links to {href}"
            for anchor in re.findall(r'href="#(e[\d-]+)"', text):
                assert f'id="{anchor}"' in text
            want = sum(1 for e in exps if e["date"] == r["date"])
            assert len(re.findall(r'<h3 id="e[\d-]+"', text)) == want


def test_text_is_escaped_not_trusted():
    assert "<script>" not in bs.markdown("a <script>alert(1)</script> b")
    assert "&lt;" in bs.inline("x < y")


def test_a_missing_section_stops_the_build():
    with pytest.raises(SystemExit):
        bs.section("## One\ntext\n", "Two")


def test_the_desk_summary_shows_only_figures_the_reports_hold(site):
    text = (site / "index.html").read_text(encoding="utf-8")
    assert "Desk summary" in text and text.count('class="tile"') == 6 * len(bs.BOOKS)
    for stem, _ in bs.BOOKS:
        f = bs.desk_figures(stem)
        review = (bs.REPORTS / f"{stem}_review.txt").read_text(encoding="utf-8")
        assert f"failed a check: {f['failed']} (" in review
        assert f"${f['one_day']}" in text and f"{f['removed']}%" in text


def test_a_figure_the_reports_do_not_hold_stops_the_build():
    with pytest.raises(SystemExit):
        bs.figure("all_names_summary.txt", r"no such line (\d+)")
    with pytest.raises(SystemExit):
        bs.figure("no_such_report.txt", r"(\d+)")


def test_the_provenance_line_says_only_what_the_manifest_shows():
    m = {"commit": "7b445f5abc", "started": "2026-11-14T10:00:00", "only": "everything",
         "steps": [{}] * 92, "inputs": {"a": 1, "b": 2}}
    same = bs.provenance(m, "7b445f5abc")
    assert "commit 7b445f5" in same and "92 steps (every step)" in same
    assert "built from" not in same and "Every number" not in same
    assert "does not check the reports" in same
    later = bs.provenance(m, "49ac726def")
    assert "built from commit 49ac726" in later
    part = bs.provenance({**m, "only": "hedge"}, None)
    assert "(only hedge)" in part and "built from" not in part
    assert "uncommitted" in bs.provenance({**m, "uncommitted_changes": True}, None)
