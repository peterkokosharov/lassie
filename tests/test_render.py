# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
# SPDX-License-Identifier: MIT
import os
from pathlib import Path

import pytest

from lassie import render

FAKE = Path(__file__).resolve().parent / "fake_merman" / "fake_merman.py"


def make_exe(folder):
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / render.exe_name()
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return exe


def test_merman_home_first(tmp_path):
    home = make_exe(tmp_path / "home")
    on_path = make_exe(tmp_path / "path")
    found = render.find_merman({"MERMAN_HOME": str(tmp_path / "home"), "PATH": str(on_path.parent)})
    assert Path(found) == home.resolve()


def test_merman_home_bin(tmp_path):
    exe = make_exe(tmp_path / "home" / "bin")
    assert Path(render.find_merman({"MERMAN_HOME": str(tmp_path / "home"), "PATH": ""})) == exe.resolve()


def test_path_and_cwd_never(tmp_path, monkeypatch):
    make_exe(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert render.find_merman({"PATH": ".", "MERMAN_HOME": "relative"}) is None
    exe = make_exe(tmp_path / "abs")
    assert Path(render.find_merman({"PATH": str(tmp_path / "abs")})) == exe.resolve()


def test_render_with_fake():
    r = render.Renderer(str(FAKE))
    svg = r.render("graph TD; A-->B")
    assert svg.startswith("<svg")
    assert r.render("graph TD; A-->B") is svg          # cached
    with pytest.raises(render.RenderError, match="Parse error"):
        r.render("fail")


def test_render_refuses():
    with pytest.raises(render.RenderError):
        render.Renderer(None).render("graph TD")
    r = render.Renderer(str(FAKE))
    with pytest.raises(render.RenderError):
        r.render("   ")
    with pytest.raises(render.RenderError):
        r.render("x" * (render.MAX_SOURCE + 1))


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_exe_name_windows():
    assert render.exe_name() == "merman-cli.exe"


def test_cache_is_bounded_by_bytes(monkeypatch):
    from lassie import render
    monkeypatch.setattr(render, "CACHE_MAX_BYTES", 1000)
    r = render.Renderer("x")
    monkeypatch.setattr(r, "_run", lambda src: "<svg>" + "a" * 400 + src + "</svg>")
    for i in range(10):
        r.render("graph %d" % i)
    assert r._cache_bytes <= 1000 and 1 <= len(r._cache) <= 2
    assert r._cache_bytes == sum(len(v) for v in r._cache.values())
