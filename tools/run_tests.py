# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
"""Run every test suite that can run on this machine and write test-results.txt at the repo root.

Usage: python -I tools/run_tests.py     (stdlib only; needs pytest installed in the Python that runs it)
Exit code 0 when every suite that ran passed, 1 otherwise.
"""
import datetime
import platform
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def counts(text: str) -> dict:
    found = {}
    for n, word in re.findall(r"(\d+) (passed|failed|skipped|error|errors)\b", text):
        key = "failed" if word.startswith("error") else word
        found[key] = found.get(key, 0) + int(n)
    return found


def run(name: str, cmd: list[str], shown: str, parse: bool) -> dict:
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = p.stdout + p.stderr
    c = counts(out) if parse else {"passed": 1 if p.returncode == 0 else 0, "failed": 0 if p.returncode == 0 else 1}
    if p.returncode != 0 and not c.get("failed"):
        c["failed"] = 1
    print(out[-2000:])
    return {"name": name, "cmd": shown, "ok": p.returncode == 0, "c": c}


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    suites = [
        run("python tests (pytest)", [PY, "-m", "pytest", "-q", "tests"], "python -m pytest -q tests", True),
        run("AI header check", [PY, "-I", "tools/check_ai_header.py"], "python -I tools/check_ai_header.py", False),
    ]
    ok = all(s["ok"] for s in suites)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    dirty = git("status", "--porcelain", "--", ".", ":!test-results.txt") not in ("", "unknown")
    lines = [f"date: {now}", f"commit: {git('rev-parse', 'HEAD')}" + (" (+ uncommitted changes)" if dirty else ""),
             f"os: {platform.platform()}", f"python: {platform.python_version()}", ""]
    for s in suites:
        c = s["c"]
        lines += [f"suite: {s['name']}", f"  command: {s['cmd']}",
                  f"  passed: {c.get('passed', 0)}  failed: {c.get('failed', 0)}  skipped: {c.get('skipped', 0)}",
                  f"  result: {'PASS' if s['ok'] else 'FAIL'}"]
    lines += ["suite: C/C++ tests", "  not run here: Lassie has no compiled code (Python, HTML, CSS and JavaScript only)",
              "suite: JavaScript page tests", "  not run here: the page has no automated tests; see TESTING.md for the manual plan"]
    lines += ["", f"overall: {'PASS' if ok else 'FAIL'}"]
    (ROOT / "test-results.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
