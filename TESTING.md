# Testing Lassie

Everything below can be run by one person alone. Production code is the Python standard library only; the tests
need `pytest`, installed in a throw-away virtual environment.

## Prerequisites

| OS | Needed |
|----|--------|
| Windows 10/11 | Python 3.12 or newer (python.org installer; tick "Add python.exe to PATH"), Git |
| Linux / Mint / Ubuntu | `sudo apt install python3 python3-venv python3-pip git` (Python 3.12 or newer) |
| macOS | Python 3.12 or newer (`brew install python`) and Git |

No compiler, Node, Docker, network, rex checkout or gateway is needed: the tests use a fake rex
(`tests/fake_rex/`) and a fake merman (`tests/fake_merman/`).

## One-time setup (virtual environment, git-ignored)

Linux / macOS:

```
python3 -m venv .venv
.venv/bin/python -m pip install pytest
```

Windows (Git Bash or cmd):

```
python -m venv .venv
.venv\Scripts\python -m pip install pytest
```

## Run everything

```
.venv/bin/python tools/run_tests.py          (Windows: .venv\Scripts\python tools\run_tests.py)
```

This runs every suite possible on the machine and writes `test-results.txt` (date UTC, git commit, OS, and per suite
the command, passed/failed/skipped and PASS/FAIL, then an overall line). Exit code 0 means all green.

## Run a suite alone

```
.venv/bin/python -m pytest -q tests                 (use pytest; `unittest discover` does not work)
.venv/bin/python -m pytest -q tests/test_server.py  (one file)
.venv/bin/python -m pytest -q -k permission         (by name)
python -I tools/check_ai_header.py                  (header check, no pytest needed)
```

## What the suites cover

| Suite | Covers |
|-------|--------|
| `tests/test_config.py` | configuration order (defaults, ini, environment, `--port`), invalid values |
| `tests/test_confine.py` | workspace path confinement (symlinks, UNC, device and drive-relative paths) |
| `tests/test_server.py` | token / cookie / Host / Origin gates, SSE stream, "answer a permission once" rule |
| `tests/test_rexproc.py` | JSON-lines relay with the fake rex, started through the real command line |
| `tests/test_render.py` | merman lookup and diagram rendering (fake merman), no search in the current folder |
| `tests/test_static.py` | standard-library-only rule, language bundles, static files |
| `tools/check_ai_header.py` | every file with an author header has an `AI models:` / `LLM:` line |

About 85 tests; a few are skipped on some systems (for example symlink tests where symlinks are not allowed).

## What red means

- A failing test prints its name, file and the assertion; start at that file in `tests/` and at the module it
  imports from `lassie/` (the names match: `test_confine.py` tests `lassie/confine.py`).
- `test_static.py` red: a non-standard import was added to production code, or a language file lost a key.
- Header check red: the listed file has an `Author:` header but no `AI models:` line; add your model id.
- Everything red on one machine only: check the Python version (`python --version`, must be 3.12 or newer) and that
  you run the venv's Python, not the system one.
- Timeouts in `test_rexproc.py` / `test_server.py`: another program blocks local ports or an antivirus slows
  process start; run the file again alone.

## Manual tests

The page itself (JavaScript) has no automated tests. The manual plan is in [README.md](README.md): the feature
lists under "The page", "Keyboard" and "Safety", and the screenshot checklist under "Screenshots". Start Lassie
against a real rex and gateway and work through each bullet. No separate manual test-plan files exist yet.

## Continuous integration

`.github/workflows/ci.yml` runs `tools/run_tests.py` on Ubuntu, Windows and macOS with Python 3.12 and 3.14 on every
push and pull request, and uploads `test-results.txt` as an artifact.

## Releases

Tests stay in the repository and must not go into release archives. Lassie has no packaging script at the moment;
any future script must exclude `tests/`, `tools/`, `test-results.txt` and `.github/`.
