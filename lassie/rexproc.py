# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# SPDX-License-Identifier: MIT
"""One rex child process driven by JSON lines over stdin/stdout.

Start: `<python> -P -m runtime --model <m> --jsonl [--local-tools]` with PYTHONPATH=<rex_dir>,
RUNTIME_LITELLM__BASE_URL=<endpoint> and the workspace as the current directory.

Every JSON object rex writes on stdout is handed to `on_event` as it is. Other stdout lines and stderr lines become
{"type": "log", "stream": "stdout|stderr", "text": ...}; the end of the process becomes {"type": "exit", "code": N}.
"""
import json
import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from lassie import findexe

log = logging.getLogger("lassie")

MAX_LINE = 4 * 1024 * 1024     # one JSON line from rex (a long final answer fits)
STOP_GRACE = 3.0               # seconds rex gets to answer "bye" before the process tree is killed


class RexError(Exception):
    """Message is safe to show to the user."""


def check_endpoint(endpoint):
    """The endpoint must be an http(s) URL with a host. Returns it without a trailing slash."""
    if not isinstance(endpoint, str):
        raise RexError("endpoint missing")
    e = endpoint.strip().rstrip("/")
    try:
        u = urlsplit(e)
    except ValueError:
        raise RexError("endpoint is not a URL") from None
    if u.scheme not in ("http", "https") or not u.hostname:
        raise RexError("endpoint must be an http:// or https:// URL")
    return e


def python_exe(configured, environ=None):
    """The Python that runs rex: the configured one (absolute path, or a bare name looked up on the absolute PATH
    entries only), else the Python running Lassie."""
    if not configured:
        return sys.executable
    p = Path(configured).expanduser()
    if p.is_absolute():
        if not p.is_file():
            raise RexError("python not found: %s" % configured)
        return str(p)
    if any(c in configured for c in "/\\:"):
        raise RexError("python must be a bare name or an absolute path: %s" % configured)
    environ = os.environ if environ is None else environ
    found = findexe.which(configured, path=environ.get("PATH"))
    if not found:
        raise RexError("python not found on PATH: %s" % configured)
    return found


def build_command(cfg, endpoint, model, workspace, environ=None):
    """(argv, env, cwd) for one rex process. Pure apart from the python lookup and folder checks."""
    environ = dict(os.environ if environ is None else environ)
    endpoint = check_endpoint(endpoint)
    model = (model or "").strip()
    if not model or any(ord(c) < 0x20 for c in model) or len(model) > 200:
        raise RexError("model missing or invalid")
    if not workspace:
        raise RexError("workspace missing")
    ws = Path(workspace).expanduser()
    if not ws.is_absolute():
        raise RexError("workspace must be an absolute path")
    if not ws.is_dir():
        raise RexError("workspace is not a folder: %s" % ws)
    argv = [python_exe(cfg.python, environ), "-P", "-m", "runtime", "--model", model, "--jsonl"]
    if cfg.local_tools:
        argv.append("--local-tools")
    env = environ
    keep = [p for p in (env.get("PYTHONPATH") or "").split(os.pathsep) if p and os.path.isabs(p)]
    if keep:
        env["PYTHONPATH"] = os.pathsep.join(keep)      # relative entries would reach the current folder
    else:
        env.pop("PYTHONPATH", None)
    if cfg.rex_dir:
        rex = Path(cfg.rex_dir).expanduser()
        if not rex.is_absolute() or not rex.is_dir():
            raise RexError("rex_dir must be an existing absolute folder: %s" % cfg.rex_dir)
        env["PYTHONPATH"] = os.pathsep.join([str(rex)] + keep)
    env["RUNTIME_LITELLM__BASE_URL"] = endpoint
    env.setdefault("RUNTIME_LITELLM__API_KEY", "dummy")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PATH"] = findexe.safe_path(env.get("PATH"), env)
    return argv, env, str(ws.resolve())


_JOB = None
_JOB_LOCK = threading.Lock()


def new_job():
    """A Windows job object that kills every process in it when its handle is closed (also when Lassie dies).
    Returns the handle, or None."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    info.Basic.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        k32.CloseHandle(job)
        return None
    return job


def assign_job(proc, job=None):
    """Put a started process into the job (the shared one by default). True on success; failure is not fatal."""
    global _JOB
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        with _JOB_LOCK:
            if job is None:
                if _JOB is None:
                    _JOB = new_job()
                job = _JOB
        if not job:
            return False
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        return bool(k32.AssignProcessToJobObject(job, int(proc._handle)))
    except (OSError, AttributeError, TypeError, ValueError):
        return False


def _kill_tree(proc):
    """Kill the process and everything it started. On POSIX the group is killed even when the leader is already
    gone: its children may still run."""
    try:
        if os.name == "nt":
            if proc.poll() is not None:
                return
            taskkill = os.path.join(findexe.system_root(), "System32", "taskkill.exe")
            subprocess.run([taskkill, "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=10,
                           check=False)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


class RexProcess:
    """A running rex. `on_event(dict)` is called from reader threads."""

    def __init__(self, argv, env, cwd, on_event):
        self.on_event = on_event
        self._write_lock = threading.Lock()
        self._closed = False
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, **kw)
        except OSError as exc:
            raise RexError("rex could not be started: %s" % exc.strerror) from None
        assign_job(self.proc)         # Windows: the tree dies with Lassie
        self._out = threading.Thread(target=self._read_stdout, daemon=True)
        self._err = threading.Thread(target=self._read_stderr, daemon=True)
        self._out.start()
        self._err.start()

    @property
    def pid(self):
        return self.proc.pid

    @property
    def running(self):
        return self.proc.poll() is None

    def send(self, obj):
        """Write one JSON line to rex. False when rex is gone."""
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            if self._closed or self.proc.poll() is not None:
                return False
            try:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
                return True
            except (OSError, ValueError):
                return False

    def _read_stdout(self):
        try:
            for raw in iter(lambda: self.proc.stdout.readline(MAX_LINE + 1), b""):
                text = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not text.strip():
                    continue
                try:
                    obj = json.loads(text) if len(raw) <= MAX_LINE else None
                except ValueError:
                    obj = None
                if isinstance(obj, dict) and isinstance(obj.get("type"), str):
                    self.on_event(obj)
                else:
                    self.on_event({"type": "log", "stream": "stdout", "text": text[:2000]})
        except (OSError, ValueError):
            pass
        code = self.proc.wait()
        self._err.join(2)
        self.on_event({"type": "exit", "code": code})

    def _read_stderr(self):
        try:
            for raw in iter(lambda: self.proc.stderr.readline(65536), b""):
                text = raw.decode("utf-8", "replace").rstrip("\r\n")
                if text.strip():
                    self.on_event({"type": "log", "stream": "stderr", "text": text[:2000]})
        except (OSError, ValueError):
            pass

    def stop(self, grace=STOP_GRACE):
        """Say bye, wait a moment, then kill the process tree."""
        self.send({"type": "bye"})
        with self._write_lock:
            self._closed = True
            try:
                self.proc.stdin.close()
            except (OSError, ValueError):
                pass
        try:
            self.proc.wait(grace)
        except subprocess.TimeoutExpired:
            pass
        _kill_tree(self.proc)
