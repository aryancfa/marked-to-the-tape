"""test_scripts_compile.py: every script at least parses.

On 12 November 2026 a one-line edit left scripts/review_queue.py with a
string broken across two lines. No test imported that script, the suite
passed, and the fault surfaced an hour into a full reproduction, which then
threw every report away. Compiling each script takes milliseconds.
"""

import py_compile
from pathlib import Path

import pytest

SCRIPTS = sorted((Path(__file__).resolve().parents[1] / "scripts").glob("*.py"))


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_script_compiles(path, tmp_path):
    py_compile.compile(str(path), cfile=str(tmp_path / "out.pyc"), doraise=True)
