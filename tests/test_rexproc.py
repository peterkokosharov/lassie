# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# SPDX-License-Identifier: MIT
import os
import sys

import pytest

from lassie.config import Config
from lassie.rexproc import RexError, build_command, check_endpoint, python_exe


def test_command_line_and_environment(tmp_path):
    rex = tmp_path / "rex"
    rex.mkdir()
    cfg = Config(python=sys.executable, rex_dir=str(rex), local_tools=False)
    env = {"PATH": os.pathsep.join([".", "relative", str(tmp_path)]), "PYTHONPATH": os.pathsep.join(["rel", str(tmp_path / "old"), ".", ""])}
    argv, out, cwd = build_command(cfg, "http://gw:5000/", " m1 ", str(tmp_path), env)
    assert argv == [sys.executable, "-P", "-m", "runtime", "--model", "m1", "--jsonl"]
    assert out["RUNTIME_LITELLM__BASE_URL"] == "http://gw:5000"
    assert out["RUNTIME_LITELLM__API_KEY"] == "dummy"
    assert out["PYTHONPATH"] == str(rex) + os.pathsep + str(tmp_path / "old")      # relative entries dropped
    assert out["PATH"] == str(tmp_path)                    # relative PATH entries are dropped
    assert cwd == str(tmp_path.resolve())
    assert env["PYTHONPATH"].startswith("rel")             # the caller's mapping is not changed


def test_local_tools_and_api_key_kept(tmp_path):
    cfg = Config(python=sys.executable)
    argv, out, _ = build_command(cfg, "https://gw", "m", str(tmp_path), {"RUNTIME_LITELLM__API_KEY": "k"})
    assert argv[-1] == "--local-tools"
    assert out["RUNTIME_LITELLM__API_KEY"] == "k"


@pytest.mark.parametrize("endpoint", ["", "ftp://x", "http://", "localhost:5000", None])
def test_bad_endpoint(endpoint):
    with pytest.raises(RexError):
        check_endpoint(endpoint)


def test_bad_inputs(tmp_path):
    cfg = Config(python=sys.executable)
    with pytest.raises(RexError):
        build_command(cfg, "http://x", "", str(tmp_path), {})
    with pytest.raises(RexError):
        build_command(cfg, "http://x", "m", "relative/dir", {})
    with pytest.raises(RexError):
        build_command(cfg, "http://x", "m", str(tmp_path / "missing"), {})
    with pytest.raises(RexError):
        build_command(Config(python=sys.executable, rex_dir="relative"), "http://x", "m", str(tmp_path), {})


def test_python_lookup_never_uses_cwd(tmp_path, monkeypatch):
    assert python_exe("") == sys.executable
    with pytest.raises(RexError):
        python_exe("sub/python")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ("python-lassie-test.exe" if os.name == "nt" else "python-lassie-test")).write_text("x")
    with pytest.raises(RexError):
        python_exe("python-lassie-test", {"PATH": "."})


def test_relative_pythonpath_dropped_without_rex_dir(tmp_path):
    cfg = Config(python=sys.executable)
    _, out, _ = build_command(cfg, "http://gw", "m", str(tmp_path), {"PYTHONPATH": "rel"})
    assert "PYTHONPATH" not in out


def _sleeper():
    import subprocess
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.DEVNULL)


@pytest.mark.skipif(os.name != "nt", reason="Windows job object")
def test_job_object_kills_the_process_when_closed():
    import ctypes
    import time
    from lassie import rexproc
    job = rexproc.new_job()
    assert job
    p = _sleeper()
    try:
        if not rexproc.assign_job(p, job):
            pytest.skip("this process is itself in a job that forbids nesting")
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(job))
        end = time.monotonic() + 10
        while p.poll() is None and time.monotonic() < end:
            time.sleep(0.05)
        assert p.poll() is not None
    finally:
        p.kill()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_kill_tree_kills_group_even_when_leader_is_gone():
    import subprocess
    import time
    from lassie import rexproc
    p = subprocess.Popen([sys.executable, "-c",
                          "import subprocess,sys;c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                          "print(c.pid,flush=True)"], stdout=subprocess.PIPE, start_new_session=True)
    child = int(p.stdout.readline())
    p.wait()
    rexproc._kill_tree(p)
    time.sleep(0.5)
    with pytest.raises(OSError):
        os.kill(child, 0)
