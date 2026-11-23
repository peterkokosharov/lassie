# Changes

## 0.1.0 - 2026-11-23

First version: the browser front-end of the rex agent family (Layka = terminal, Sisi = Qt desktop, Lassie = browser).

- Server in the Python standard library, bound to 127.0.0.1 only (port 4100 by default): start token in the URL,
  exchanged for an HttpOnly SameSite=Strict cookie; token header on every POST; exact Host and Origin checks.
- One rex process per browser page (`python -P -m runtime --model M --jsonl [--local-tools]`, PYTHONPATH = rex_dir,
  RUNTIME_LITELLM__BASE_URL = endpoint, cwd = workspace); events stream to the page over SSE, input goes by POST.
- Page in plain HTML/CSS/JavaScript: endpoint/model/workspace fields, model list from `<endpoint>/v1/models`
  (fetched by the server), streaming chat, own Markdown renderer (headings, lists, quotes, code blocks with
  highlighting, tables, links, images), Send/Stop/Reset, Alt-S/Alt-T/Alt-R.
- "+" tools pane with tool_start/tool_end lines, rex stderr and coloured diffs.
- Permission dialog: Allow once / Allow always / Deny, Deny by default (focus, Esc), queued, every question
  answered exactly once (the server refuses a second answer).
- "ctx P%" indicator, orange from 80%.
- Images from the workspace through a confined route (Layka's rules: inside the workspace only, no UNC, device or
  drive-relative paths, symbolic links resolved).
- Mermaid blocks drawn server-side by merman-cli (MERMAN_HOME, then the absolute PATH entries; never the current
  folder) and shown as SVG images.
- lassie.ini with LASSIE_* environment overrides; six UI languages (English original; bg/de/fr/ru/zh wait for a
  translator).
