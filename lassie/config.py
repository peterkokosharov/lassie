# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# SPDX-License-Identifier: MIT
"""Configuration: built-in defaults, then lassie.ini, then LASSIE_* environment variables, then command line.

lassie.ini is looked up in this order: the --config argument, the LASSIE_CONFIG environment variable, lassie.ini
next to the lassie package folder (the project root). The current directory is never searched.
"""
import configparser
import os
from dataclasses import dataclass, fields
from pathlib import Path

LANGUAGES = ("en", "bg", "de", "fr", "ru", "zh")
SECTION = "lassie"
ROOT = Path(__file__).resolve().parent.parent

# key -> environment variable
ENV = {
    "endpoint": "LASSIE_ENDPOINT",
    "model": "LASSIE_MODEL",
    "workspace": "LASSIE_WORKSPACE",
    "python": "LASSIE_PYTHON",
    "rex_dir": "LASSIE_REX_DIR",
    "port": "LASSIE_PORT",
    "language": "LASSIE_LANG",
    "local_tools": "LASSIE_LOCAL_TOOLS",
}


class ConfigError(Exception):
    """Message is safe to show to the user."""


@dataclass
class Config:
    endpoint: str = "http://127.0.0.1:5000"
    model: str = ""
    workspace: str = ""
    python: str = ""          # empty: the Python that runs Lassie
    rex_dir: str = ""         # folder that holds the rex "runtime" package; put first on PYTHONPATH
    port: int = 4100
    language: str = ""        # empty: the browser's language
    local_tools: bool = True  # pass --local-tools to rex (tools run on the host, like Layka)
    source: str = ""          # the lassie.ini that was read, or ""


def normalize_lang(value):
    """'bg_BG.UTF-8' -> 'bg', 'de-AT' -> 'de'; '' for anything that is not one of LANGUAGES."""
    if not isinstance(value, str):
        return ""
    code = value.strip().replace("-", "_").split("_")[0].split(".")[0].lower()
    return code if code in LANGUAGES else ""


def _bool(value, key):
    v = str(value).strip().lower()
    if v in ("1", "yes", "true", "on"):
        return True
    if v in ("0", "no", "false", "off"):
        return False
    raise ConfigError("%s: expected yes or no, got %r" % (key, value))


def _port(value, key="port"):
    try:
        port = int(str(value).strip())
    except ValueError:
        raise ConfigError("%s: not a number: %r" % (key, value)) from None
    if not 0 <= port <= 65535:
        raise ConfigError("%s: out of range: %d" % (key, port))
    return port


def _set(cfg, key, value):
    if key == "port":
        cfg.port = _port(value)
    elif key == "local_tools":
        cfg.local_tools = _bool(value, key)
    elif key == "language":
        cfg.language = normalize_lang(value)
    else:
        setattr(cfg, key, str(value).strip())


def find_ini(arg=None, environ=None):
    """Path of the lassie.ini to read, or None. An explicitly named file must exist."""
    environ = os.environ if environ is None else environ
    for given in (arg, environ.get("LASSIE_CONFIG")):
        if given:
            p = Path(given).expanduser()
            if not p.is_absolute():
                raise ConfigError("config file must be an absolute path: %s" % given)
            if not p.is_file():
                raise ConfigError("config file not found: %s" % p)
            return p
    p = ROOT / "lassie.ini"
    return p if p.is_file() else None


def load(ini=None, environ=None, overrides=None):
    """Build the Config. `ini`: path or None (see find_ini); `overrides`: dict from the command line."""
    environ = os.environ if environ is None else environ
    cfg = Config()
    path = find_ini(ini, environ)
    if path is not None:
        parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
        try:
            with open(path, encoding="utf-8-sig") as f:
                parser.read_file(f)
        except (OSError, configparser.Error) as exc:
            raise ConfigError("cannot read %s: %s" % (path, exc)) from None
        if parser.has_section(SECTION):
            known = {f.name for f in fields(Config)} - {"source"}
            for key, value in parser.items(SECTION):
                if key in known:
                    _set(cfg, key, value)
        cfg.source = str(path)
    for key, var in ENV.items():
        value = environ.get(var)
        if value is not None and value != "":
            _set(cfg, key, value)
    for key, value in (overrides or {}).items():
        if value is not None:
            _set(cfg, key, value)
    return cfg
