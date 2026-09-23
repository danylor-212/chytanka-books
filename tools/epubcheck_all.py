#!/usr/bin/env python3
"""Run epubcheck on every EPUB, never stopping early, then print a summary.

    python tools/epubcheck_all.py [--jar epubcheck.jar] [--allow-warnings] public/books/*.epub

Uses `java -jar <jar>` when --jar is given (or $EPUBCHECK_JAR), otherwise an
`epubcheck` executable on PATH. Exit code 1 if any book has fatals/errors
(or warnings, unless --allow-warnings).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("epubs", nargs="+")
    ap.add_argument("--jar", default=os.environ.get("EPUBCHECK_JAR"))
    ap.add_argument("--allow-warnings", action="store_true")
    args = ap.parse_args()

    if args.jar:
        cmd = [os.environ.get("JAVA", "java"), "-jar", args.jar]
    elif shutil.which("epubcheck"):
        cmd = ["epubcheck"]
    else:
        print("epubcheck not found (pass --jar or put epubcheck on PATH)", file=sys.stderr)
        return 2

    rows, bad = [], 0
    for epub in args.epubs:
        if not os.path.isfile(epub):  # e.g. an unexpanded shell glob — never report it as "ok"
            print(f"  FATAL: no such file: {epub}")
            rows.append((os.path.basename(epub), 1, 0, 0, "FAIL"))
            bad += 1
            continue
        proc = subprocess.run(cmd + [epub], capture_output=True, text=True)
        out = proc.stdout + proc.stderr
        m = re.search(r"Messages:\s*(\d+) fatals?\s*/\s*(\d+) errors?\s*/\s*(\d+) warnings?", out)
        fatals, errors, warnings = (int(x) for x in m.groups()) if m else (1, 0, 0)
        failed = fatals or errors or (warnings and not args.allow_warnings) or (proc.returncode and not m)
        bad += bool(failed)
        rows.append((os.path.basename(epub), fatals, errors, warnings, "FAIL" if failed else "ok"))
        for line in out.splitlines():
            if line.startswith(("FATAL", "ERROR", "WARNING")):
                print(f"  {line}")

    w = max(len(r[0]) for r in rows)
    print(f"\n{'book'.ljust(w)}  fatal  error  warn  result")
    for name, f, e, wn, res in rows:
        print(f"{name.ljust(w)}  {f:5d}  {e:5d}  {wn:4d}  {res}")
    print(f"\n{len(rows) - bad}/{len(rows)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
