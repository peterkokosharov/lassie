// lassie - the web front-end for rex
// Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
// SPDX-License-Identifier: MIT
// Markdown -> DOM nodes (taken from kircho, images added). Every piece of text goes in through textContent;
// nothing is ever parsed as markup. Headings, lists, quotes, code blocks, tables, links, images.

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

// ---- inline ---------------------------------------------------------------------------------------------
const INLINE = new RegExp([
  "`([^`\\n]+)`",                              // 1 code
  "\\*\\*(?=\\S)(.{1,2000}?)(?<=\\S)\\*\\*",          // 2 bold
  "__(?=\\S)(.{1,2000}?)(?<=\\S)__",                  // 3 bold
  "\\*(?=[^\\s*])(.{1,2000}?)(?<=[^\\s*])\\*",        // 4 italic
  "\\b_(?=[^\\s_])(.{1,2000}?)(?<=[^\\s_])_\\b",      // 5 italic
  "\\[([^\\]\\n]{1,500})\\]\\(([^)\\s]{1,2000})(?:\\s+\"[^\"]*\")?\\)",  // 6 text, 7 url
  "!\\[([^\\]\\n]{0,500})\\]\\(([^)\\s]{1,2000})(?:\\s+\"[^\"]*\")?\\)", // 8 alt, 9 image path
].join("|"));

// The page decides how a local image is shown (a confined server route); without a hook the text stays as it is.
let imageHook = null;
export function setImageHook(fn) { imageHook = fn; }

function safeUrl(u) {
  try {
    const x = new URL(u);
    return x.protocol === "http:" || x.protocol === "https:" ? x.href : null;
  } catch (e) { return null; }
}

const MAX_LINE = 20000;  // longer lines are shown as plain text: no pattern ever runs over them

function inline(text, parent, depth = 0) {
  if (text.length > MAX_LINE) { parent.appendChild(document.createTextNode(text)); return; }
  let rest = text;
  while (rest) {
    const m = depth < 4 ? INLINE.exec(rest) : null;
    if (!m) { parent.appendChild(document.createTextNode(rest)); return; }
    if (m.index) parent.appendChild(document.createTextNode(rest.slice(0, m.index)));
    rest = rest.slice(m.index + m[0].length);
    if (m[1] !== undefined) parent.appendChild(el("code", null, m[1]));
    else if (m[2] !== undefined || m[3] !== undefined) inline(m[2] !== undefined ? m[2] : m[3], parent.appendChild(el("strong")), depth + 1);
    else if (m[4] !== undefined || m[5] !== undefined) inline(m[4] !== undefined ? m[4] : m[5], parent.appendChild(el("em")), depth + 1);
    else if (m[9] !== undefined) {
      const node = imageHook ? imageHook(m[9], m[8]) : null;
      parent.appendChild(node || document.createTextNode(m[0]));
    } else {
      const href = safeUrl(m[7]);
      if (href) {
        const a = el("a");
        a.href = href; a.rel = "noopener noreferrer"; a.target = "_blank";
        inline(m[6], a, depth + 1);
        parent.appendChild(a);
      } else parent.appendChild(document.createTextNode(m[0]));
    }
  }
}

function inlineLines(lines, parent) {
  lines.forEach((l, i) => { if (i) parent.appendChild(el("br")); inline(l, parent); });
}

// ---- code highlighting (tokens wrapped in spans made through the DOM) ------------------------------------
const kw = (s) => new RegExp("\\b(?:" + s.split(" ").join("|") + ")\\b");
const JS_KW = "var let const function return if else for while do switch case break continue new this class extends import export from default async await try catch finally throw typeof instanceof in of null undefined true false interface type enum implements public private protected static void";
const LANGS = {
  python: { c: /#.*/, s: /"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?/, k: kw("def class return if elif else for while in not and or is import from as with try except finally raise pass break continue lambda yield None True False async await global nonlocal del assert") },
  js: { c: /\/\/.*|\/\*[\s\S]*?(?:\*\/|$)/, s: /`(?:\\.|[^`\\])*`?|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?/, k: kw(JS_KW) },
  java: { c: /\/\/.*|\/\*[\s\S]*?(?:\*\/|$)/, s: /"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?/, k: kw("class interface enum extends implements public private protected static final void int long double float boolean char byte short new return if else for while do switch case break continue try catch finally throw throws import package this super null true false abstract synchronized instanceof var") },
  bash: { c: /#.*/, s: /"(?:\\.|[^"\\])*"?|'[^']*'?/, k: kw("if then else elif fi for while do done case esac function in return exit export local echo cd set unset source") },
  json: { c: /(?!)/, s: /"(?:\\.|[^"\\\n])*"?/, k: kw("true false null") },
  sql: { c: /--.*|\/\*[\s\S]*?(?:\*\/|$)/, s: /'(?:''|[^'])*'?/, k: /\b(?:select|from|where|and|or|not|insert|into|values|update|set|delete|create|table|drop|alter|join|left|right|inner|outer|on|group|by|order|having|limit|as|distinct|union|null|is|in|like|between|case|when|then|else|end|primary|key|index)\b/i },
  html: { c: /<!--[\s\S]*?(?:-->|$)/, s: /"[^"\n]*"?|'[^'\n]*'?/, k: /<\/?[A-Za-z][\w:-]*|\/?>/ },
};
const ALIAS = { py: "python", python3: "python", javascript: "js", jsx: "js", ts: "js", typescript: "js", tsx: "js",
  sh: "bash", shell: "bash", zsh: "bash", xml: "html", svg: "html", jsonc: "json" };
const compiled = new Map();

function lexer(name) {
  if (!compiled.has(name)) {
    const L = LANGS[name];
    const flags = L.k.flags.includes("i") ? "gi" : "g";
    compiled.set(name, new RegExp("(" + L.c.source + ")|(" + L.s.source + ")|(" + L.k.source + ")|(\\b\\d+(?:\\.\\d+)?\\b)", flags));
  }
  const r = compiled.get(name); r.lastIndex = 0; return r;
}

export function highlight(code, lang, parent) {
  const name = ALIAS[lang] || lang;
  if (!LANGS[name] || code.length > 20000) { parent.appendChild(document.createTextNode(code)); return; }
  const re = lexer(name);
  let last = 0, m;
  while ((m = re.exec(code))) {
    if (!m[0]) { re.lastIndex++; continue; }
    if (m.index > last) parent.appendChild(document.createTextNode(code.slice(last, m.index)));
    parent.appendChild(el("span", "tk-" + (m[1] ? "c" : m[2] ? "s" : m[3] ? "k" : "n"), m[0]));
    last = m.index + m[0].length;
  }
  if (last < code.length) parent.appendChild(document.createTextNode(code.slice(last)));
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) { /* fall back */ }
  try {
    const ta = el("textarea"); ta.value = text; document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy"); ta.remove(); return ok;
  } catch (e) { return false; }
}

export function codeBlock(code, lang, labels) {
  const copy = (labels && labels.copy) || "copy", copied = (labels && labels.copied) || "copied";
  const box = el("div", "code");
  const bar = el("div", "codebar");
  bar.appendChild(el("span", "lang", lang || ""));
  const btn = el("button", "copy", copy);
  btn.type = "button";
  btn.addEventListener("click", async () => {
    if (await copyText(code)) {
      btn.textContent = copied;
      setTimeout(() => { btn.textContent = copy; }, 1200);
    }
  });
  bar.appendChild(btn);
  const pre = el("pre"), c = el("code");
  highlight(code, (lang || "").toLowerCase(), c);
  pre.appendChild(c);
  box.append(bar, pre);
  return box;
}

// ---- blocks ---------------------------------------------------------------------------------------------
const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([^\s`]*)[^`]*$/;

// "#{1,6} text" parsed by hand (linear): returns [level, text] or null. Closing #s are stripped only after a space.
function parseHeading(line) {
  if (line.length > MAX_LINE) return null;
  let i = 0;
  while (i < 3 && line[i] === " ") i++;
  let n = 0;
  while (line[i + n] === "#") n++;
  if (n < 1 || n > 6) return null;
  const c = line[i + n];
  if (c !== " " && c !== "\t") return null;
  let a = i + n, b = line.length;
  while (a < b && /\s/.test(line[a])) a++;
  while (b > a && /\s/.test(line[b - 1])) b--;
  let e = b;
  while (e > a && line[e - 1] === "#") e--;
  if (e < b && (e === a || /\s/.test(line[e - 1]))) {
    b = e;
    while (b > a && /\s/.test(line[b - 1])) b--;
  }
  return [n, line.slice(a, b)];
}
const HR = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE = /^ {0,3}>\s?(.*)$/;
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$/;

function splitRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}

function isTableStart(lines, i) {
  const line = lines[i];
  return line.includes("|") && i + 1 < lines.length && lines[i + 1].length <= MAX_LINE && line.length <= MAX_LINE
    && TABLE_SEP.test(lines[i + 1]) && lines[i + 1].includes("-")
    && splitRow(line).length === splitRow(lines[i + 1]).length;
}

function startsBlock(line) {
  if (line.length > MAX_LINE) return true;
  return FENCE.test(line) || parseHeading(line) || HR.test(line) || QUOTE.test(line) || ITEM.test(line);
}

function buildList(lines) {
  const top = ITEM.exec(lines[0])[1].length;
  const items = [];  // {ordered, num, text:[], subs:[{ordered, num, text:[]}]}
  for (const line of lines) {
    const m = ITEM.exec(line);
    const tail = items[items.length - 1];
    if (m && (m[1].length <= top + 1 || !tail)) {
      items.push({ ordered: /\d/.test(m[2]), num: parseInt(m[2], 10), text: [m[3]], subs: [] });
    } else if (m) {
      tail.subs.push({ ordered: /\d/.test(m[2]), num: parseInt(m[2], 10), text: [m[3]] });
    } else if (tail) {
      const target = tail.subs.length ? tail.subs[tail.subs.length - 1] : tail;
      target.text.push(line.trim());
    }
  }
  const make = (group) => {
    const list = el(group[0].ordered ? "ol" : "ul");
    if (group[0].ordered && group[0].num !== 1) list.start = group[0].num;
    for (const it of group) {
      const li = el("li");
      inlineLines(it.text, li);
      if (it.subs && it.subs.length) li.appendChild(make(it.subs));
      list.appendChild(li);
    }
    return list;
  };
  return make(items);
}

// opts.special(lang, code, closed) may return a node for a fenced block (mermaid, svg), or null.
// opts.labels = {copy, copied}.
export function renderMarkdown(text, opts = {}, depth = 0) {
  const frag = document.createDocumentFragment();
  const lines = String(text).replace(/\r\n?/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (line.length > MAX_LINE) { frag.appendChild(el("p", null, line)); i++; continue; }
    let m = FENCE.exec(line);
    if (m) {
      const ch = m[1][0], len = m[1].length, lang = m[2].toLowerCase(), body = [];
      let closed = false;
      i++;
      while (i < lines.length) {
        const c = /^ {0,3}(`{3,}|~{3,})\s*$/.exec(lines[i]);
        if (c && c[1][0] === ch && c[1].length >= len) { closed = true; i++; break; }
        body.push(lines[i++]);
      }
      const code = body.join("\n");
      const special = opts.special ? opts.special(lang, code, closed) : null;
      frag.appendChild(special || codeBlock(code, lang, opts.labels));
      continue;
    }
    if ((m = parseHeading(line))) {
      inline(m[1], frag.appendChild(el("h" + m[0])));
      i++; continue;
    }
    if (HR.test(line)) { frag.appendChild(el("hr")); i++; continue; }
    if (QUOTE.test(line) && depth < 4) {
      const q = [];
      while (i < lines.length && QUOTE.test(lines[i])) q.push(QUOTE.exec(lines[i++])[1]);
      const bq = el("blockquote");
      bq.appendChild(renderMarkdown(q.join("\n"), opts, depth + 1));
      frag.appendChild(bq);
      continue;
    }
    if (isTableStart(lines, i)) {
      const aligns = splitRow(lines[i + 1]).map((c) => (c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : ""));
      const table = el("table"), thead = el("thead"), tbody = el("tbody"), hr = el("tr");
      splitRow(line).forEach((c, k) => { const th = el("th", aligns[k] ? "al-" + aligns[k] : ""); inline(c, th); hr.appendChild(th); });
      thead.appendChild(hr);
      i += 2;
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        const tr = el("tr"), cells = splitRow(lines[i++]);
        for (let k = 0; k < aligns.length; k++) { const td = el("td", aligns[k] ? "al-" + aligns[k] : ""); inline(cells[k] || "", td); tr.appendChild(td); }
        tbody.appendChild(tr);
      }
      table.append(thead, tbody);
      const wrap = el("div", "tablewrap"); wrap.appendChild(table);
      frag.appendChild(wrap);
      continue;
    }
    if (ITEM.test(line)) {
      const group = [];
      while (i < lines.length) {
        const l = lines[i];
        if (!l.trim()) {  // a blank line continues the list only when another item follows
          let j = i; while (j < lines.length && !lines[j].trim()) j++;
          if (j < lines.length && ITEM.test(lines[j])) { i = j; continue; }
          break;
        }
        if (ITEM.test(l) || (/^\s{2,}\S/.test(l) && !FENCE.test(l))) group.push(l);
        else break;
        i++;
      }
      frag.appendChild(buildList(group));
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && (para.length === 0 || !(startsBlock(lines[i]) || isTableStart(lines, i)))) para.push(lines[i++].trim());
    inlineLines(para, frag.appendChild(el("p")));
  }
  return frag;
}
