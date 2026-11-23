// lassie - the web front-end for rex
// Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
// SPDX-License-Identifier: MIT
// The page: fields, chat with streaming, the "+" tools pane, permission questions, ctx indicator, shortcuts.
// Input goes to the server by POST; everything rex says comes back over one event stream (SSE).

import { renderMarkdown, codeBlock, setImageHook } from "./markdown.js";

const TOKEN = document.querySelector('meta[name="lassie-token"]').content;
const $ = (id) => document.getElementById(id);
const LANGS = ["en", "bg", "de", "fr", "ru", "zh"];

let sid = null;          // this page's session (its own rex process)
let es = null;           // EventSource
let running = false;     // rex is up (after "ready")
let busy = false;        // a chat request is running
let starting = false;    // a start was asked, "ready" not seen yet
let startedWith = null;  // {endpoint, workspace} rex was started with
let waitingText = null;  // a message typed before rex was ready
let current = null;      // the assistant bubble being streamed: {node, text}
const perms = [];        // queued permission events; perms[0] is on screen
let strings = {}, english = {};

// ---- strings ---------------------------------------------------------------------------------------------
function t(key, vars) {
  let s = strings[key] || english[key] || key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split("{" + k + "}").join(String(v));
  return s;
}

async function loadLang(code) {
  const get = async (c) => { try { const r = await fetch("/lang/" + c + ".json"); return r.ok ? await r.json() : {}; } catch (e) { return {}; } };
  if (!Object.keys(english).length) english = await get("en");
  strings = code === "en" ? english : await get(code);
  document.documentElement.lang = code;
  for (const n of document.querySelectorAll("[data-s]")) n.textContent = t(n.dataset.s);
  for (const n of document.querySelectorAll("[data-sp]")) n.placeholder = t(n.dataset.sp);
  for (const n of document.querySelectorAll("[data-st]")) n.title = t(n.dataset.st);
  $("lang").value = code;
  setStatus();
}

function pickLang(configured) {
  const saved = localStorage.getItem("lassie.lang");
  if (LANGS.includes(saved)) return saved;
  if (LANGS.includes(configured)) return configured;
  for (const l of navigator.languages || [navigator.language || "en"]) {
    const c = String(l).slice(0, 2).toLowerCase();
    if (LANGS.includes(c)) return c;
  }
  return "en";
}

// ---- server calls ----------------------------------------------------------------------------------------
async function post(route, body) {
  try {
    const r = await fetch("/api/" + route, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Lassie-Token": TOKEN },
      body: JSON.stringify(body || {}),
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* not JSON */ }
    return { ok: r.ok, status: r.status, data };
  } catch (e) { return { ok: false, status: 0, data: { error: String(e) } }; }
}

async function get(route) {
  const r = await fetch("/api/" + route, { headers: { "X-Lassie-Token": TOKEN } });
  return r.json();
}

// ---- chat log --------------------------------------------------------------------------------------------
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function nearBottom() { const l = $("log"); return l.scrollHeight - l.scrollTop - l.clientHeight < 80; }
function add(node) { const stick = nearBottom(); $("log").appendChild(node); if (stick) node.scrollIntoView({ block: "end" }); return node; }
function sys(text, cls) { return add(el("div", "sys" + (cls ? " " + cls : ""), text)); }

const special = (lang, code, closed) => {
  if (lang !== "mermaid" || !closed) return null;
  const box = el("div", "diagram");
  box.appendChild(el("div", "sys", t("diagramRendering")));
  post("render", { source: code }).then(({ data }) => {
    box.replaceChildren();
    if (data && data.svg) {
      const img = el("img");
      img.alt = "mermaid";
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(data.svg);
      box.appendChild(img);
    } else {
      box.appendChild(el("div", "sys", t("diagramFailed", { error: (data && data.error) || "" })));
      box.appendChild(codeBlock(code, "mermaid", labels()));
    }
  });
  return box;
};

function labels() { return { copy: t("copy"), copied: t("copied") }; }

setImageHook((path, alt) => {
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(path) || /^data:/i.test(path) || !sid) return null;  // local files only
  const img = el("img", "local");
  img.alt = alt || path;
  img.title = path;
  img.src = "/api/image?sid=" + encodeURIComponent(sid) + "&path=" + encodeURIComponent(path);
  img.addEventListener("error", () => img.replaceWith(el("span", "sys", t("imageMissing", { path }))), { once: true });
  return img;
});

function renderAnswer(node, text) {
  node.replaceChildren(renderMarkdown(text, { special, labels: labels() }));
}

// ---- diff colouring (permission details, tool results) ---------------------------------------------------
function looksLikeDiff(text) { return /^(@@|\+\+\+ |--- )/m.test(text); }

function fillDiff(pre, text) {
  pre.replaceChildren();
  if (!looksLikeDiff(text)) { pre.textContent = text; return; }
  for (const line of text.split("\n")) {
    const cls = line.startsWith("@@") ? "hunk" : line.startsWith("+++") || line.startsWith("---") ? "file"
      : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : null;
    pre.appendChild(el("span", cls ? "d-" + cls : null, line + "\n"));
  }
}

// ---- the "+" tools pane ----------------------------------------------------------------------------------
function paneLine(text, cls) {
  const log = $("panelog");
  const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.appendChild(el("span", cls || null, text + "\n"));
  while (log.childNodes.length > 3000) log.removeChild(log.firstChild);
  if (stick) log.scrollTop = log.scrollHeight;
  if ($("pane").hidden) $("plus").classList.add("news");
}

function toolLine(ev) {
  const a = ev.args || {};
  if (typeof a.command === "string") return "$ " + a.command;
  let s = "";
  try { s = JSON.stringify(a); } catch (e) { s = ""; }
  if (s.length > 400) s = s.slice(0, 400) + "...";
  return "$ " + ev.name + (s && s !== "{}" ? " " + s : "");
}

function togglePane(open) {
  const pane = $("pane");
  pane.hidden = open === undefined ? !pane.hidden : !open;
  $("plus").textContent = pane.hidden ? "+" : "−";
  $("plus").setAttribute("aria-pressed", String(!pane.hidden));
  if (!pane.hidden) $("plus").classList.remove("news");
}

// ---- permission modal ------------------------------------------------------------------------------------
function showPerm() {
  const dlg = $("perm");
  const ev = perms[0];
  if (!ev) { if (dlg.open) dlg.close(); return; }
  $("perm-summary").textContent = ev.summary || ev.tool || "";
  $("perm-tool").textContent = ev.tool || "";
  $("perm-kind").textContent = ev.kind ? "(" + ev.kind + ")" : "";
  $("perm-rule").textContent = ev.rule ? t("permRule", { rule: ev.rule }) : "";
  $("perm-reason").textContent = ev.reason || "";
  fillDiff($("perm-detail"), String(ev.detail || ""));
  $("perm-more").textContent = perms.length > 1 ? t("permMore", { n: perms.length - 1 }) : "";
  for (const b of dlg.querySelectorAll("button")) b.disabled = false;
  if (!dlg.open) dlg.showModal();
  $("perm-deny").focus();          // the default answer is Deny
}

async function answerPerm(decision) {
  const ev = perms[0];
  if (!ev) return;
  for (const b of $("perm").querySelectorAll("button")) b.disabled = true;   // one answer per question
  perms.shift();
  await post("permission", { sid, id: ev.id, decision });
  showPerm();
}

function dropPerms() { perms.length = 0; showPerm(); }

// ---- status ----------------------------------------------------------------------------------------------
function setStatus() {
  const state = !running ? (starting ? "starting" : "off") : (busy || perms.length ? "busy" : "idle");
  $("dot").className = "dot " + (state === "starting" ? "busy" : state);
  $("status").textContent = sid ? t("status_" + state) : t("status_off");
  $("send").disabled = busy;
  $("stop").disabled = !busy;
  $("reset").disabled = !running || busy;
  const changed = startedWith && (startedWith.endpoint !== $("endpoint").value.trim() || startedWith.workspace !== $("workspace").value.trim());
  $("start").textContent = startedWith ? t("restart") : t("start");
  $("start").classList.toggle("hot", !!changed);
}

function setCtx(ev) {
  const p = Number(ev.percent);
  if (!Number.isFinite(p)) return;
  const c = $("ctx");
  c.hidden = false;
  c.textContent = "ctx " + Math.round(p) + "%";
  c.classList.toggle("warn", p >= 80);
  c.title = (ev.used || "?") + " / " + (ev.limit || "?") + (ev.source ? " (" + ev.source + ")" : "");
}

// ---- events from rex (and from Lassie) -------------------------------------------------------------------
function onEvent(ev) {
  switch (ev.type) {
    case "ready":
      running = true; starting = false;
      sys(t("ready", { model: ev.model || "" }));
      if (Array.isArray(ev.instructions) && ev.instructions.length) sys(t("instructions", { list: ev.instructions.join(", ") }));
      if (waitingText !== null) { const w = waitingText; waitingText = null; sendText(w); }
      break;
    case "token":
      if (!current) { current = { node: add(el("div", "block assistant streaming")), text: "" }; }
      current.text += ev.text || "";
      current.node.textContent = current.text;
      if (nearBottom()) current.node.scrollIntoView({ block: "end" });
      break;
    case "final": {
      const node = current ? current.node : add(el("div", "block assistant"));
      node.classList.remove("streaming");
      node.classList.add("md");
      renderAnswer(node, String(ev.text || (current && current.text) || ""));
      current = null;
      busy = false;
      break;
    }
    case "error":
      if (current) { current.node.classList.remove("streaming"); current = null; }
      sys(t("error", { message: ev.message || "" }), "err");
      busy = false;
      break;
    case "tool_start":
      paneLine(toolLine(ev), "cmd");
      sys("→ " + (ev.name || "tool"), "tool");
      break;
    case "tool_end":
      paneLine(ev.ok ? "  " + t("toolOk") : "  " + t("toolFailed", { error: ev.error || "" }), ev.ok ? "ok" : "bad");
      if (typeof ev.diff === "string" && ev.diff) {
        const pre = el("pre");
        fillDiff(pre, ev.diff);
        for (const n of [...pre.childNodes]) $("panelog").appendChild(n);
      }
      break;
    case "permission":
      perms.push(ev);
      showPerm();
      break;
    case "context": setCtx(ev); break;
    case "reset_ok": sys(t("resetOk")); $("ctx").hidden = true; break;
    case "model_ok": sys(t("modelOk", { model: ev.model || "" })); break;
    case "log": paneLine(ev.text || "", "logline"); break;
    case "bye":
    case "exit":
      if (running || ev.type === "exit") sys(ev.type === "exit" ? t("exit", { code: ev.code }) : t("bye"));
      running = false; starting = false; busy = false; current = null; dropPerms();
      if (ev.type === "exit" && ev.code) togglePane(true);   // why it ended is in the stderr lines
      break;
    case "lassie":
      if (ev.kind === "starting") { running = false; sys(t("starting", { workspace: ev.workspace, model: ev.model })); }
      else if (ev.kind === "failed") starting = false, sys(t("failed", { message: ev.message || "" }), "err");
      else if (ev.kind === "answered") {   // answered here or in another tab: take it off the queue
        const i = perms.findIndex((p) => p.id === ev.id);
        if (i >= 0) { perms.splice(i, 1); showPerm(); }
      }
      break;
    default: break;
  }
  setStatus();
}

function openStream() {
  if (es) return;
  es = new EventSource("/api/events?sid=" + encodeURIComponent(sid));
  es.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch (e) { console.error("lassie: bad event", e); } };
  es.onerror = () => { if (es && es.readyState === EventSource.CLOSED) { es = null; running = false; setStatus(); } };
}

// ---- actions ---------------------------------------------------------------------------------------------
async function start() {
  const body = { sid, endpoint: $("endpoint").value.trim(), model: $("model").value.trim(), workspace: $("workspace").value.trim() };
  running = false; starting = true; setStatus();
  const r = await post("start", body);
  if (r.data && r.data.sid) { sid = r.data.sid; openStream(); }
  if (!r.ok) {   // with a session the reason also arrives as an event: show it once
    starting = false; waitingText = null;
    if (!(r.data && r.data.sid)) sys(t("failed", { message: (r.data && r.data.error) || r.status }), "err");
    setStatus(); return false;
  }
  startedWith = { endpoint: body.endpoint, workspace: body.workspace };
  if (r.data.workspace) { $("workspace").value = r.data.workspace; startedWith.workspace = r.data.workspace; }
  setStatus();
  return true;
}

async function sendText(text) {
  add(el("div", "block user", text));
  busy = true; setStatus();
  const r = await post("chat", { sid, text });
  if (!r.ok) {
    busy = false;
    sys(r.data && r.data.error === "busy" ? t("busy") : t("failed", { message: (r.data && r.data.error) || r.status }), "err");
    setStatus();
  }
}

async function send() {
  const text = $("input").value;
  if (!text.trim() || busy) return;
  $("input").value = "";
  if (!running) {
    waitingText = text;
    if (!sid || !es || !startedWith) { if (!(await start())) $("input").value = text; }
    return;
  }
  sendText(text);
}

async function stop() { if (sid) await post("stop", { sid }); }
async function reset() { if (sid && running && !busy) await post("reset", { sid }); }

async function loadModels() {
  const ep = $("endpoint").value.trim();
  const data = await get("models?endpoint=" + encodeURIComponent(ep));
  const list = $("models");
  list.replaceChildren();
  for (const m of data.models || []) { const o = el("option"); o.value = m; list.appendChild(o); }
  sys(data.error ? t("failed", { message: data.error }) : (data.models || []).length ? t("modelsFound", { n: data.models.length }) : t("modelsNone"),
    data.error ? "err" : "");
}

// ---- wiring ----------------------------------------------------------------------------------------------
function keys(e) {
  if (!e.altKey || e.ctrlKey || e.metaKey) return;
  const k = e.key.toLowerCase();
  const act = { s: send, t: stop, r: reset, e: () => $("endpoint").focus(), m: () => $("model").focus(), w: () => $("workspace").focus() }[k];
  if (act && !$("perm").open) { e.preventDefault(); act(); }
}

async function init() {
  let state = {};
  try { state = await get("state"); } catch (e) { /* server gone */ }
  $("endpoint").value = state.endpoint || "";
  $("model").value = state.model || "";
  $("workspace").value = state.workspace || "";
  await loadLang(pickLang(state.language));
  $("send").addEventListener("click", send);
  $("stop").addEventListener("click", stop);
  $("reset").addEventListener("click", reset);
  $("start").addEventListener("click", start);
  $("getmodels").addEventListener("click", loadModels);
  $("plus").addEventListener("click", () => togglePane());
  $("paneclear").addEventListener("click", () => $("panelog").replaceChildren());
  $("perm-once").addEventListener("click", () => answerPerm("allow_once"));
  $("perm-always").addEventListener("click", () => answerPerm("allow_always"));
  $("perm-deny").addEventListener("click", () => answerPerm("deny"));
  $("perm").addEventListener("cancel", (e) => { e.preventDefault(); answerPerm("deny"); });   // Esc = Deny
  $("lang").addEventListener("change", () => { localStorage.setItem("lassie.lang", $("lang").value); loadLang($("lang").value); });
  $("model").addEventListener("change", () => {
    const m = $("model").value.trim();
    if (sid && running && m) post("model", { sid, model: m });
  });
  for (const id of ["endpoint", "workspace"]) $(id).addEventListener("input", setStatus);
  $("input").addEventListener("keydown", (e) => { if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); send(); } });
  document.addEventListener("keydown", keys);
  window.addEventListener("pagehide", () => {
    if (sid) fetch("/api/close", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json", "X-Lassie-Token": TOKEN }, body: JSON.stringify({ sid }) });
  });
  setStatus();
  $("input").focus();
}

init();
