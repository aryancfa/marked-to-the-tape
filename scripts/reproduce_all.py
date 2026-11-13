"""
reproduce_all.py — every reported number, one command, with a record of what went in.

    .venv/Scripts/python scripts/reproduce_all.py                 # everything (hours, uncached)
    .venv/Scripts/python scripts/reproduce_all.py --list          # show the steps
    .venv/Scripts/python scripts/reproduce_all.py --only hedge,index_funding,demo

scripts/reproduce.py covers one name's day runs and front chart. This covers
the rest as well: every name, the summary across names, the funding split,
the dividend comparison, the index funding, the share hedge report, the
robustness table, the engine check, the made-up-data example, the two
book reports and the mark-check benchmark.

Three things it does that running the scripts by hand does not
--------------------------------------------------------------
  1. ALL OR NOTHING. reports/ is copied aside before the first step. If any
     step fails, reports/ is put back exactly as it was, so the published
     folder is never half new and half old.
  2. A MANIFEST. On success it writes reports/manifest.json: the git commit
     (and whether the tree had uncommitted changes), the Python and package
     versions, each step's command and time, a content hash of every input
     file (raw data under data/raw, schedules under reference/, the books),
     and each name's cache fingerprint. Two runs with the same manifest
     inputs must give the same numbers; if they do not, something outside
     the manifest is leaking in.
  3. A LOG PER STEP, kept with the run under data/derived/runs/<time>/.

The manifest holds file names and hashes only, never contents, so it is safe
to commit beside the reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
S = "scripts/"

NAMES = ["RY", "TD", "BNS", "BMO", "SHOP", "SU", "BCE", "ABX", "CNR", "XIU", "CNQ", "MFC",
         "ENB", "ZEB", "XEG"]
NEW_YORK = ["RY", "TD", "BNS", "BMO", "CM", "CNQ", "SU"]


def steps() -> list[tuple[str, list[str]]]:
    """(key, command) in the order they must run. The pass with no funding
    spread comes first for EVERY name, because a name whose own spread is
    badly measured takes the median of the others' (surface.choose_spread)."""
    out = [(f"base_{n}", [S + "run_days.py", n, "--base-only"]) for n in NAMES]
    out.append(("days_RY", [S + "reproduce.py", "RY"]))
    for n in NAMES[1:]:
        out.append((f"days_{n}", [S + "run_days.py", n]))
        out.append((f"chart_{n}", [S + "front_chart.py", n]))
    out.append(("summary", [S + "summary_names.py"]))
    out += [(f"split_{n}", [S + "funding_split.py", n]) for n in NAMES if n not in ("SHOP", "ZEB")]
    out.append(("dividends", [S + "dividend_three_way.py"]))
    out.append(("index_funding", [S + "index_funding.py"]))
    out.append(("hedge", [S + "hedge_shares.py"]))
    out.append(("robustness", [S + "robustness.py", "RY"]))
    out += [(f"engine_{n}", [S + "engine_check.py", n]) for n in NEW_YORK]
    out.append(("demo", [S + "demo_synthetic.py"]))
    out += [(f"book_{b}", [S + "book_report.py", f"books/{b}.csv"])
            for b in ("bank_heavy", "diversified")]
    out.append(("benchmark", [S + "mark_benchmark.py"]))
    out += [(f"review_{b}", [S + "review_queue.py", f"books/{b}.csv"])
            for b in ("bank_heavy", "diversified")]
    out.append(("review_benchmark", [S + "review_benchmark.py"]))
    out += [(f"hedge_{b}", [S + "book_hedge.py", f"books/{b}.csv"])
            for b in ("bank_heavy", "diversified")]
    return out


def sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def inputs() -> dict[str, dict]:
    """Every input file with its size and content hash."""
    out = {}
    for folder in ("data/raw", "reference", "books"):
        for f in sorted((ROOT / folder).rglob("*")):
            if f.is_file():
                out[f.relative_to(ROOT).as_posix()] = {"bytes": f.stat().st_size, "sha1": sha1(f)}
    return out


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return ""


def versions() -> dict[str, str]:
    out = {"python": platform.python_version()}
    for pkg in ("numpy", "pandas", "matplotlib"):
        try:
            out[pkg] = __import__(pkg).__version__
        except Exception:
            out[pkg] = "missing"
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", default="", help="comma-separated step keys, or prefixes "
                                               "(base, days, chart, split, engine)")
    ap.add_argument("--list", action="store_true", help="print the steps and stop")
    a = ap.parse_args()
    todo = steps()
    if a.only:
        want = [w.strip() for w in a.only.split(",") if w.strip()]
        todo = [(k, c) for k, c in todo if any(k == w or k.startswith(w + "_") for w in want)]
    if a.list or not todo:
        for k, c in todo or steps():
            print(f"{k:16s} {' '.join(c)}")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = ROOT / "data/derived/runs" / stamp
    run_dir.mkdir(parents=True)
    before = run_dir / "reports_before"
    shutil.copytree(ROOT / "reports", before)
    manifest = {"started": datetime.now().isoformat(timespec="seconds"),
                "commit": git("rev-parse", "HEAD"),
                "uncommitted_changes": bool(git("status", "--porcelain")),
                "versions": versions(), "only": a.only or "everything",
                "inputs": inputs(), "steps": []}
    print(f"run {stamp}: {len(todo)} steps, {len(manifest['inputs'])} input files hashed; "
          f"logs in {run_dir.relative_to(ROOT)}")

    failed = None
    for key, cmd in todo:
        t0 = time.time()
        with open(run_dir / f"{key}.log", "w", encoding="utf-8") as log:
            code = subprocess.run([PY, *cmd], cwd=ROOT, stdout=log,
                                  stderr=subprocess.STDOUT).returncode
        secs = round(time.time() - t0, 1)
        manifest["steps"].append({"step": key, "command": " ".join(cmd), "seconds": secs,
                                  "exit_code": code})
        print(f"  {key:16s} {'ok' if code == 0 else 'FAILED'}  {secs:8.1f} s")
        if code != 0:
            failed = key
            break

    manifest["finished"] = datetime.now().isoformat(timespec="seconds")
    manifest["status"] = "ok" if failed is None else f"failed at {failed}"
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from run_days import fingerprint, mx_files
        manifest["cache_fingerprints"] = {n: fingerprint(n) for n in NAMES if mx_files(n)}
    except Exception as exc:
        manifest["cache_fingerprints"] = f"not available ({exc})"
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")

    if failed is not None:
        # put the published folder back exactly as it was
        shutil.rmtree(ROOT / "reports")
        shutil.copytree(before, ROOT / "reports")
        raise SystemExit(f"step {failed} failed; reports/ restored to how it was. "
                         f"See {(run_dir / (failed + '.log')).relative_to(ROOT)}")
    (ROOT / "reports" / "manifest.json").write_text(json.dumps(manifest, indent=1),
                                                    encoding="utf-8")
    shutil.rmtree(before)
    print(f"all {len(todo)} steps ok; manifest: reports/manifest.json")


if __name__ == "__main__":
    main()
