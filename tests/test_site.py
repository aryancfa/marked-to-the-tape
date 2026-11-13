"""test_site.py: the live page is built from committed, derived files only."""

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


def test_text_is_escaped_not_trusted():
    assert "<script>" not in bs.markdown("a <script>alert(1)</script> b")
    assert "&lt;" in bs.inline("x < y")


def test_a_missing_section_stops_the_build():
    with pytest.raises(SystemExit):
        bs.section("## One\ntext\n", "Two")
