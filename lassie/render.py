# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# SPDX-License-Identifier: MIT
"""Mermaid diagrams drawn server-side by merman-cli into SVG (adapted from kircho).

merman-cli is looked up in this order, never in the current directory:
1. the folder named by MERMAN_HOME (the file itself, or bin/ inside it);
2. the absolute entries of PATH (see findexe).
The renderer runs `merman-cli render IN.mmd -o OUT.svg -f svg -q` in a private temporary folder with a 20 second
limit; results are cached by the hash of the source.
"""
import hashlib
import logging
import os
import subprocess
import sys
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from lassie import findexe

log = logging.getLogger("lassie")

MAX_SOURCE = 50_000
TIMEOUT = 20
CACHE_MAX = 100
CACHE_MAX_BYTES = 64 * 1024 * 1024
MAX_SVG = 8 * 1024 * 1024
MAX_PARALLEL = 2


class RenderError(Exception):
    """Message is safe to show to the user."""


def exe_name():
    return "merman-cli.exe" if os.name == "nt" else "merman-cli"


def find_merman(environ=None):
    """Full path of merman-cli, or None (see the module text)."""
    environ = os.environ if environ is None else environ
    exe = exe_name()
    home = (environ.get("MERMAN_HOME") or "").strip('"')
    if home and os.path.isabs(home):
        for cand in (Path(home) / exe, Path(home) / "bin" / exe):
            if cand.is_file():
                return str(cand.resolve())
    found = findexe.which("merman-cli", path=environ.get("PATH"))
    return str(Path(found).resolve()) if found else None


class Renderer:
    def __init__(self, path):
        self.path = path
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._lock = threading.Lock()
        self._sem = threading.Semaphore(MAX_PARALLEL)

    def _argv(self, src, out):
        base = [self.path]
        if Path(self.path).suffix.lower() == ".py":  # test doubles written in Python
            base = [sys.executable, "-I", self.path]
        return base + ["render", str(src), "-o", str(out), "-f", "svg", "-q"]

    def render(self, source):
        """SVG text for a mermaid source. Raises RenderError."""
        if not self.path:
            raise RenderError("merman-cli not found (set MERMAN_HOME or PATH)")
        if not isinstance(source, str) or not source.strip():
            raise RenderError("empty diagram")
        if len(source) > MAX_SOURCE:
            raise RenderError("diagram too long")
        key = hashlib.sha256(source.encode("utf-8")).hexdigest()
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        with self._sem:
            svg = self._run(source)
        with self._lock:
            if key not in self._cache:
                self._cache[key] = svg
                self._cache_bytes += len(svg)
            while len(self._cache) > 1 and (len(self._cache) > CACHE_MAX or self._cache_bytes > CACHE_MAX_BYTES):
                self._cache_bytes -= len(self._cache.popitem(last=False)[1])
        return svg

    def _run(self, source):
        with tempfile.TemporaryDirectory(prefix="lassie-") as tmp:
            src, out = Path(tmp) / "in.mmd", Path(tmp) / "out.svg"
            src.write_text(source, encoding="utf-8")
            env = dict(os.environ)
            env["PATH"] = findexe.safe_path()
            try:
                p = subprocess.run(self._argv(src, out), cwd=tmp, capture_output=True, timeout=TIMEOUT,
                                   stdin=subprocess.DEVNULL, check=False, env=env)
            except subprocess.TimeoutExpired:
                raise RenderError("diagram renderer timed out") from None
            except OSError:
                raise RenderError("diagram renderer could not be started") from None
            if p.returncode != 0 or not out.is_file():
                msg = (p.stderr or p.stdout or b"").decode("utf-8", "replace").strip().splitlines()
                raise RenderError((msg[-1] if msg else "render failed")[:300])
            if out.stat().st_size > MAX_SVG:
                raise RenderError("diagram too large")
            svg = out.read_text(encoding="utf-8", errors="replace")
            if "<svg" not in svg:
                raise RenderError("renderer produced no SVG")
            return svg
