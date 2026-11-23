# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# SPDX-License-Identifier: MIT
"""Workspace confinement for images shown in the page (the same rules as Layka).

An image reference from an answer is served only when
- the text itself passes ref_allowed() (checked before the file system is touched): not empty, no control characters,
  not starting with two separators (UNC, //host, \\\\?\\ and \\\\.\\ device paths), and on Windows no ':' except in a
  leading drive letter "X:\\" or "X:/" (drive-relative paths and NTFS streams are refused);
- the path (relative paths are read from the workspace) lies strictly inside the workspace, both lexically and after
  symbolic links are resolved;
- it is an existing file with an image extension.
"""
import os
from pathlib import Path

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}
MAX_IMAGE = 20 * 1024 * 1024


def _sep(c):
    return c in "/\\"


def ref_allowed(ref, win=None):
    """Pure verdict on an image reference (see the module text)."""
    win = (os.name == "nt") if win is None else win
    if not isinstance(ref, str) or not ref:
        return False
    if len(ref) >= 2 and _sep(ref[0]) and _sep(ref[1]):
        return False
    for i, ch in enumerate(ref):
        o = ord(ch)
        if o < 0x20 or o == 0x7F:
            return False
        if win and ch == ":":
            drive = i == 1 and len(ref) > 2 and _sep(ref[2]) and ref[0].isascii() and ref[0].isalpha()
            if not drive:
                return False
    return True


def lex_normalize(path, win=None):
    """Lexical form of an absolute path for comparisons: '.' and duplicate separators dropped, '..' removes its
    parent, '/' as separator; on Windows '\\' is a separator too and ASCII letters are lower-cased."""
    win = (os.name == "nt") if win is None else win
    s = path
    if win:
        s = "".join("/" if c == "\\" else (c.lower() if c.isascii() else c) for c in s)
    drive = ""
    if win and len(s) >= 2 and s[1] == ":":
        drive, s = s[:2], s[2:]
    root = s.startswith("/")
    parts = []
    for part in s.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            elif not root:
                parts.append(part)
            continue
        parts.append(part)
    return drive + ("/" if root else "") + "/".join(parts)


def path_inside(path, folder, win=None):
    """True if `path` is strictly inside `folder` (the folder itself or a sibling with the same prefix is not)."""
    a = lex_normalize(path, win)
    d = lex_normalize(folder, win)
    if not d:
        return False
    if not d.endswith("/"):
        d += "/"
    return len(a) > len(d) and a.startswith(d)


def _is_abs(ref, win):
    if win:
        return len(ref) > 2 and ref[1] == ":" and _sep(ref[2]) or _sep(ref[0])
    return ref.startswith("/")


def resolve_image(workspace, ref, win=None):
    """Path of the image file `ref` inside `workspace`, or None. `workspace` must be an absolute folder."""
    win = (os.name == "nt") if win is None else win
    if not ref_allowed(ref, win) or not workspace:
        return None
    if Path(ref).suffix.lower() not in IMAGE_TYPES:
        return None
    ws = str(workspace)
    if _is_abs(ref, win):
        if win and _sep(ref[0]):       # "\x" on Windows is relative to the current drive: refuse
            return None
        cand = ref
    else:
        cand = os.path.join(ws, ref)
    if not path_inside(cand, ws, win):
        return None
    try:
        real_ws = os.path.realpath(ws)
        real = os.path.realpath(cand)
    except (OSError, ValueError):
        return None
    if not path_inside(real, real_ws, win):
        return None
    try:
        if not os.path.isfile(real) or os.path.getsize(real) > MAX_IMAGE:
            return None
    except (OSError, ValueError):
        return None
    return Path(real)
