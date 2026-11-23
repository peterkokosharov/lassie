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
    env = {"PATH": os.pathsep.join([".", "relative", str(tmp_path)]), "PYTHONPATH": "old"}
    argv, out, cwd = build_command(cfg, "http://gw:5000/", " m1 ", str(tmp_path), env)
    assert argv == [sys.executable, "-P", "-m", "runtime", "--model", "m1", "--jsonl"]
    assert out["RUNTIME_LITELLM__BASE_URL"] == "http://gw:5000"
    assert out["RUNTIME_LITELLM__API_KEY"] == "dummy"
    assert out["PYTHONPATH"] == str(rex) + os.pathsep + "old"
    assert out["PATH"] == str(tmp_path)                    # relative PATH entries are dropped
    assert cwd == str(tmp_path.resolve())
    assert env["PYTHONPATH"] == "old"                      # the caller's mapping is not changed


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
