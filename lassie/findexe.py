# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# SPDX-License-Identifier: MIT
r"""Find an executable on PATH, never in the current directory (taken from kircho).

shutil.which on Windows searches the current directory first, even with path= (Python 3.12+ adds it unless
NoDefaultCurrentDirectoryInExePath is set): a python.exe or merman-cli.exe planted in the folder Lassie starts from
would win. Rule: only absolute PATH entries are searched, one by one, each with a full path (shutil.which then looks
only there and still honours PATHEXT).

With PATH unset the fallback holds system folders only: os.defpath is not used on Windows (it is ".;C:\bin")."""
import ntpath
import os
import shutil

WINDOWS_ROOT = r"C:\Windows"
POSIX_DEFAULT = "/usr/bin:/bin"


def system_root(environ=None):
    r"""Windows folder from SystemRoot, or C:\Windows when SystemRoot is unset or not an absolute path."""
    environ = os.environ if environ is None else environ
    value = (environ.get("SystemRoot") or "").strip('"')
    drive, _ = ntpath.splitdrive(value)
    if value and drive and ntpath.isabs(value):
        return value
    return WINDOWS_ROOT


def default_path(environ=None):
    """The system default search path used when PATH is unset: system folders only."""
    if os.name == "nt":
        root = system_root(environ)
        return ntpath.pathsep.join([ntpath.join(root, "System32"), root, ntpath.join(root, "System32", "Wbem")])
    try:
        return os.confstr("CS_PATH") or POSIX_DEFAULT
    except (AttributeError, ValueError, OSError):
        return POSIX_DEFAULT


def safe_path(path=None, environ=None):
    """`path` (default: PATH, or default_path() when unset) reduced to its absolute entries."""
    environ = os.environ if environ is None else environ
    if path is None:
        path = environ.get("PATH")
    if path is None:
        path = default_path(environ)
    keep = []
    for d in path.split(os.pathsep):
        d = d.strip('"')
        if d and os.path.isabs(d):  # "", "." or a relative entry would be resolved against the current directory
            keep.append(d)
    return os.pathsep.join(keep)


def which(name, path=None):
    """Full path of `name` from the absolute entries of `path` (default: PATH), or None."""
    for d in safe_path(path).split(os.pathsep):
        if not d:
            continue
        found = shutil.which(os.path.join(d, name))
        if found:
            return found
    return None
