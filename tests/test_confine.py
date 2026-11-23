# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# SPDX-License-Identifier: MIT
import os

import pytest

from lassie import confine


@pytest.mark.parametrize("ref", ["", "\\\\server\\share\\a.png", "//server/share/a.png", "\\\\?\\C:\\a.png",
                                 "\\\\.\\pipe\\x.png", "a\x00.png", "a\n.png", "C:a.png", "a.png:stream",
                                 "x/C:/a.png"])
def test_refused_on_windows(ref):
    assert not confine.ref_allowed(ref, win=True)


@pytest.mark.parametrize("ref", ["a.png", "diagrams/x.svg", "C:\\w\\a.png", "c:/w/a.png", "..\\a.png"])
def test_allowed_text_on_windows(ref):
    assert confine.ref_allowed(ref, win=True)


def test_posix_allows_colon_but_not_double_slash():
    assert confine.ref_allowed("a:b.png", win=False)
    assert not confine.ref_allowed("//host/a.png", win=False)


def test_lexical_inside():
    assert confine.path_inside("/w/a/b.png", "/w", win=False)
    assert not confine.path_inside("/w", "/w", win=False)
    assert not confine.path_inside("/w2/a.png", "/w", win=False)
    assert not confine.path_inside("/w/../etc/a.png", "/w", win=False)
    assert confine.path_inside("C:\\W\\Sub\\a.png", "c:/w", win=True)
    assert not confine.path_inside("D:\\w\\a.png", "C:\\w", win=True)


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    (w / "diagrams").mkdir(parents=True)
    (w / "diagrams" / "x.svg").write_text("<svg/>")
    (w / "pic.png").write_bytes(b"\x89PNG")
    (w / "notes.txt").write_text("x")
    (tmp_path / "outside.png").write_bytes(b"\x89PNG")
    return w


def test_resolve_inside(ws):
    assert confine.resolve_image(str(ws), "diagrams/x.svg") == (ws / "diagrams" / "x.svg").resolve()
    assert confine.resolve_image(str(ws), "./pic.png") is not None
    assert confine.resolve_image(str(ws), str(ws / "pic.png")) is not None       # absolute inside is fine


def test_resolve_refuses(ws, tmp_path):
    assert confine.resolve_image(str(ws), "../outside.png") is None
    assert confine.resolve_image(str(ws), str(tmp_path / "outside.png")) is None
    assert confine.resolve_image(str(ws), "notes.txt") is None                    # not an image type
    assert confine.resolve_image(str(ws), "missing.png") is None
    assert confine.resolve_image(str(ws), "diagrams") is None
    assert confine.resolve_image("", "pic.png") is None


def test_symlink_out_of_workspace_refused(ws, tmp_path):
    link = ws / "link.png"
    try:
        os.symlink(tmp_path / "outside.png", link)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links not available")
    assert confine.resolve_image(str(ws), "link.png") is None
