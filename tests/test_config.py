# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# SPDX-License-Identifier: MIT
import pytest

from lassie import config


def write_ini(tmp_path, body):
    p = tmp_path / "lassie.ini"
    p.write_text(body, encoding="utf-8")
    return p


def test_defaults_without_ini(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)      # no lassie.ini next to the package
    cfg = config.load(environ={})
    assert cfg.port == 4100
    assert cfg.endpoint == "http://127.0.0.1:5000"
    assert cfg.local_tools is True
    assert cfg.source == ""


def test_ini_then_env_then_command_line(tmp_path):
    ini = write_ini(tmp_path, "[lassie]\nendpoint = http://ini:1\nmodel = ini-model ; comment\nport = 4200\n"
                              "language = bg_BG.UTF-8\nlocal_tools = no\nunknown = x\n")
    cfg = config.load(str(ini), environ={})
    assert (cfg.endpoint, cfg.model, cfg.port, cfg.language, cfg.local_tools) == \
        ("http://ini:1", "ini-model", 4200, "bg", False)
    env = {"LASSIE_MODEL": "env-model", "LASSIE_PORT": "4300", "LASSIE_LANG": "de-AT", "LASSIE_ENDPOINT": ""}
    cfg = config.load(str(ini), environ=env)
    assert (cfg.endpoint, cfg.model, cfg.port, cfg.language) == ("http://ini:1", "env-model", 4300, "de")
    cfg = config.load(str(ini), environ=env, overrides={"port": "0"})
    assert cfg.port == 0


def test_lassie_config_env_names_the_file(tmp_path):
    ini = write_ini(tmp_path, "[lassie]\nworkspace = /w\n")
    assert config.load(environ={"LASSIE_CONFIG": str(ini)}).workspace == "/w"


def test_ini_next_to_package(monkeypatch, tmp_path):
    write_ini(tmp_path, "[lassie]\nrex_dir = /rex\n")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    assert config.load(environ={}).rex_dir == "/rex"


@pytest.mark.parametrize("body", ["[lassie]\nport = abc\n", "[lassie]\nport = 70000\n",
                                  "[lassie]\nlocal_tools = maybe\n", "not an ini"])
def test_bad_values(tmp_path, body):
    with pytest.raises(config.ConfigError):
        config.load(str(write_ini(tmp_path, body)), environ={})


def test_named_file_must_exist_and_be_absolute(tmp_path):
    with pytest.raises(config.ConfigError):
        config.load(str(tmp_path / "missing.ini"), environ={})
    with pytest.raises(config.ConfigError):
        config.load("relative.ini", environ={})


@pytest.mark.parametrize("value,want", [("bg", "bg"), ("ZH_cn", "zh"), ("fr-FR", "fr"), ("ru_RU.UTF-8", "ru"),
                                        ("xx", ""), ("", ""), ("C", "")])
def test_normalize_lang(value, want):
    assert config.normalize_lang(value) == want
