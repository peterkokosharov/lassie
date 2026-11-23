# Lassie

<img src="lassie/static/lassie-128.png" alt="Lassie" width="64" align="right">

Lassie is the browser front-end of the rex agent family: **Layka** is the terminal window, **Sisi** the Qt desktop
window, **Lassie** the browser window. It works like Kircho (a web window for Claude Code), but it drives rex.

The production code is the Python standard library plus plain HTML, CSS and JavaScript: no Node, no frameworks, no
build step, no CDN. Everything the page loads comes from Lassie itself.

## Run

Requirements: Python 3.12 or newer, a rex checkout (the folder that holds the `runtime` package) and an
OpenAI-compatible gateway (for example LiteLLM).

1. Copy `lassie.ini.example` to `lassie.ini` (in this folder) and set `endpoint`, `model`, `workspace` and `rex_dir`.
2. Start: `python -m lassie` (options: `--config FILE`, `--port N`, `--open`, `--verbose`).
3. Open the link Lassie prints (`http://127.0.0.1:4100/?t=...`), or start with `--open`.
4. Check the fields, press **Start** (or just type and press **Send**: rex starts first).

Stop Lassie with Ctrl+C in its console; it stops every rex it started.

## How it works

```
browser page  --POST (token header)-->  Lassie server  --JSON lines on stdin-->   rex (one per page)
browser page  <--SSE event stream----   Lassie server  <--JSON lines on stdout--  rex
```

For every page Lassie starts one rex process:
`<python> -P -m runtime --model <model> --jsonl [--local-tools]`, with `PYTHONPATH=<rex_dir>`,
`RUNTIME_LITELLM__BASE_URL=<endpoint>` (and `RUNTIME_LITELLM__API_KEY=dummy` unless one is set), and the workspace as
the current folder. The page sends `chat`, `stop`, `reset`, `model` and `permission_reply`; rex answers with `ready`,
`token`, `final`, `error`, `tool_start`, `tool_end` (a `diff` field is shown when present), `permission`, `context`
and `bye`. Lines rex writes to stderr appear in the tools pane.

**Restart** (the Start button after the first start) starts a new rex with the current endpoint and workspace; it
turns orange when those fields differ from the running rex. A new model in the Model field is sent to the running rex
without a restart. Closing or reloading the page stops its rex (at the latest two minutes after the page is gone).

## The page

- **Endpoint, Model, Workspace** fields; **Models** asks the server for `<endpoint>/v1/models` and fills the Model
  field's list.
- **Chat** with streaming: tokens appear as plain text, the finished answer is shown as Markdown by a small renderer
  of our own (headings, lists, quotes, code blocks with highlighting and a copy button, tables, links, images). Every
  other piece of text is shown as text, never as HTML.
- **Images**: `![alt](path)` in an answer shows an image from the workspace (png, jpg, gif, svg, webp). The same rules
  as Layka: relative paths are read from the workspace, the file must lie inside it (also after symbolic links are
  resolved); UNC paths, device paths, drive-relative paths and paths outside the workspace are refused.
- **Diagrams**: a fenced `mermaid` block is drawn on the server by `merman-cli` into SVG and shown as an image. Lookup:
  the folder named by `MERMAN_HOME` (the file itself or `bin/` inside it), then the absolute entries of `PATH`; the
  current folder is never searched. Without merman-cli the block stays as code with a note.
- **+** opens the tools pane: one line per tool call (`$ command` or `$ tool {args}`), then `ok` or `failed: ...`,
  rex's stderr lines in grey, diffs coloured. The `+` turns blue when something new arrived while it was closed.
- **Permission dialog**: when rex asks, a dialog shows the summary, tool, kind, rule, reason and the detail (diffs
  coloured). **Allow once**, **Allow always** or **Deny**; Deny has the focus and Esc means Deny. Several questions
  wait in a queue ("N more waiting"). Each question is answered exactly once: the server forwards the first answer
  and refuses any other.
- **ctx P%** in the header after each answer: the share of the model's context window in use; orange from 80%.
- **Send / Stop / Reset**. Send is off while an answer runs; Stop interrupts it; Reset clears the conversation.
- **Language** selector (EN, BG, DE, FR, RU, ZH); the choice is remembered by the browser.

### Keyboard

| Key | Action |
|-----|--------|
| Alt-S or Ctrl+Enter | Send |
| Alt-T | Stop the running request |
| Alt-R | Reset the conversation |
| Alt-E, Alt-M, Alt-W | go to Endpoint, Model, Workspace |
| Esc (in the permission dialog) | Deny |

## Configuration

`lassie.ini`, section `[lassie]` (see `lassie.ini.example`):

| Key | Environment | Default | Meaning |
|-----|-------------|---------|---------|
| endpoint | LASSIE_ENDPOINT | http://127.0.0.1:5000 | OpenAI-compatible gateway |
| model | LASSIE_MODEL | (empty) | model name |
| workspace | LASSIE_WORKSPACE | (empty) | rex's working folder (absolute) |
| python | LASSIE_PYTHON | the Python running Lassie | absolute path or bare name on PATH |
| rex_dir | LASSIE_REX_DIR | (empty) | folder with the rex `runtime` package |
| port | LASSIE_PORT | 4100 | port on 127.0.0.1 |
| language | LASSIE_LANG | the browser's | en, bg, de, fr, ru, zh |
| local_tools | LASSIE_LOCAL_TOOLS | yes | pass `--local-tools` to rex |

Order: built-in defaults, then `lassie.ini`, then the environment, then `--port`. The file is found by `--config`,
then `LASSIE_CONFIG`, then `lassie.ini` next to the `lassie/` package; never in the current folder.

`local_tools = yes` (like Layka) runs rex's tools on this computer **without** the Docker guard. Do not open an
untrusted repository as the workspace with it.

## Safety

- Lassie listens on 127.0.0.1 only.
- The start link carries a random token. Opening it sets an HttpOnly, SameSite=Strict cookie and redirects to `/`, so
  the token leaves the address bar. The page gets the token in a meta tag and sends it in the `X-Lassie-Token` header
  with every POST; another local page cannot set that header (the server never answers a CORS preflight). The event
  stream and images (loaded by the browser itself) accept the cookie.
- The Host header must be exactly `127.0.0.1:<port>` or `localhost:<port>` (no DNS rebinding); an Origin header, when
  present, must be Lassie's own.
- Content-Security-Policy: scripts, styles and connections from Lassie only; no inline script; images from Lassie or
  `data:` (diagrams).
- The Python for rex and merman-cli are never looked up in the current folder; child processes get a PATH without
  relative entries.

## Languages

The UI texts are JSON files in `lassie/lang/` (`en.json` is the original and the fallback). The bg, de, fr, ru and zh
files still hold the English texts and carry a TODO note for a translator: translate the values, keep the keys and the
`{name}` placeholders. A missing key falls back to English.

## Screenshots

To be added (`docs/screenshots/`):

1. Start page, light theme, fields filled, not started.
2. A chat with a streamed answer rendered as Markdown (heading, list, table, code block).
3. The permission dialog with a coloured diff and "1 more waiting".
4. The "+" tools pane open with tool lines and a diff.
5. A mermaid diagram and a workspace image in an answer.
6. The "ctx 85%" indicator in orange.
7. Dark theme (system setting).

## Tests

```
python -m venv .venv
.venv/bin/python -m pip install pytest        (Windows: .venv\Scripts\python -m pip install pytest)
.venv/bin/python -m pytest -q
```

The tests cover the configuration order, the path confinement, the token/cookie/Host/Origin gates, the JSON-lines
relay with a fake rex (`tests/fake_rex/`, started through the real command line), the permission "answer once" rule,
merman lookup and rendering with a fake merman, the standard-library-only rule, and the language bundles.
The page itself (JavaScript) has no automated tests in this repository.

## License

MIT, see `LICENSE`. The Lassie icon is the author's own work, also MIT.
