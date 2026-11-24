# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
# SPDX-License-Identifier: MIT
"""Rules the owner can check by reading: standard library only, no CDN, every language bundle complete."""
import ast
import json
import re
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "lassie"
LANGS = ("en", "bg", "de", "fr", "ru", "zh")


def test_production_code_is_stdlib_only():
    allowed = set(sys.stdlib_module_names) | {"lassie", "__future__"}
    for py in PKG.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module]
            else:
                continue
            for n in names:
                assert n.split(".")[0] in allowed, "%s imports %s" % (py.name, n)


def test_no_remote_resources_in_the_page():
    for f in ("index.html", "app.js", "markdown.js", "style.css"):
        text = (PKG / "static" / f).read_text(encoding="utf-8")
        assert not re.search(r"""(src|href)\s*=\s*["']https?:""", text), f
        assert "@import" not in text and "cdn" not in text.lower(), f


def test_page_has_no_inline_script():
    html = (PKG / "static" / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"<script[^>]*>", html) == ['<script type="module" src="/static/app.js">']
    assert "onclick" not in html and "style=" not in html


@pytest.mark.parametrize("code", LANGS)
def test_language_bundles(code):
    en = json.loads((PKG / "lang" / "en.json").read_text(encoding="utf-8"))
    bundle = json.loads((PKG / "lang" / (code + ".json")).read_text(encoding="utf-8"))
    assert code == "en" or set(bundle) == set(en) - {"_comment"}
    for key, value in bundle.items():
        assert isinstance(value, str)
        assert set(re.findall(r"\{\w+\}", value)) == set(re.findall(r"\{\w+\}", en[key])), (code, key)


def test_every_string_key_used_in_the_page_exists():
    en = json.loads((PKG / "lang" / "en.json").read_text(encoding="utf-8"))
    html = (PKG / "static" / "index.html").read_text(encoding="utf-8")
    js = (PKG / "static" / "app.js").read_text(encoding="utf-8")
    keys = set(re.findall(r'data-s[pt]?="(\w+)"', html)) | set(re.findall(r'\bt\("(\w+)"(?!\s*\+)', js))
    keys |= {"status_" + s for s in ("off", "starting", "busy", "idle")}
    missing = keys - set(en)
    assert not missing, missing
