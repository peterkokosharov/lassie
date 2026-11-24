# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
# SPDX-License-Identifier: MIT
"""HTTP server: page, static files, API and the event stream (SSE). Binds 127.0.0.1 only.

Safety gates (every request):
- Host must be exactly 127.0.0.1:<port> or localhost:<port> (no DNS rebinding);
- an Origin header, when sent, must be this server's own;
- the page is served only with the start token: `/?t=<token>` answers with the page itself (the token is written
  into a meta tag for the script) and sets an HttpOnly SameSite=Strict cookie holding a separate random secret,
  not the token; the script then replaces the address with `/`; a plain `/` is never served;
- POST routes need the token in the X-Lassie-Token header (a page on another local port cannot set it without a
  CORS preflight, which is never answered);
- GET routes that a browser loads by itself (event stream, images) accept the cookie secret or the token header.
  The cookie unlocks nothing else.

One rex process per browser session: the page asks for a session id (POST /api/start) and gets its own rex.
A session whose page has been gone for SESSION_IDLE seconds is closed.
"""
import hmac
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from lassie import __version__, confine
from lassie.render import MAX_SOURCE, RenderError, Renderer, find_merman
from lassie.rexproc import RexError, RexProcess, build_command, check_endpoint

log = logging.getLogger("lassie")

STATIC_DIR = Path(__file__).resolve().parent / "static"
LANG_DIR = Path(__file__).resolve().parent / "lang"
STATIC = {
    "app.js": "text/javascript; charset=utf-8",
    "markdown.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
    "lassie.svg": "image/svg+xml",
    "lassie-32.png": "image/png",
    "lassie-128.png": "image/png",
    "favicon.ico": "image/x-icon",
}
MAX_BODY = 1024 * 1024
MAX_TEXT = 200_000
MAX_EVENTS = 20_000          # events kept per session for a reconnecting page
MAX_EVENT_BYTES = 32 * 1024 * 1024   # ... and at most this many bytes of them (a final answer can be 4 MB)
HEARTBEAT = 15.0             # seconds between SSE keep-alive comments
SESSION_IDLE = 120.0         # a session without a connected page for this long is closed
MAX_SESSIONS = 8
MODELS_TIMEOUT = 8
MODELS_MAX = 2 * 1024 * 1024
DECISIONS = ("allow_once", "allow_always", "deny")

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def _eq(a, b):
    return isinstance(a, str) and isinstance(b, str) and hmac.compare_digest(a.encode(), b.encode())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A gateway that answers with a redirect is an error: the API key must not be sent anywhere else."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class Session:
    """One page = one rex process plus the list of events the page has not necessarily seen yet."""

    def __init__(self, sid):
        self.sid = sid
        self.rex = None
        self.workspace = ""
        self.model = ""
        self.endpoint = ""
        self.busy = False
        self.pending = set()          # permission ids rex asked and nobody answered yet
        self.events = deque()         # (seq, event, size in bytes)
        self.event_bytes = 0
        self.seq = 0
        self.cond = threading.Condition()
        self.clients = 0
        self.last_seen = time.monotonic()
        self.closed = False
        self.gen = 0                  # rex generation: events of a replaced rex are dropped
        self.life = threading.Lock()  # start / stop / close run one at a time

    # --- events -----------------------------------------------------------------------------------------
    def push(self, event):
        with self.cond:
            t = event.get("type")
            if t == "permission" and isinstance(event.get("id"), str):
                self.pending.add(event["id"])
            elif t in ("final", "error", "bye", "exit"):
                self.busy = False
            if t in ("bye", "exit"):
                self.pending.clear()
            self.seq += 1
            size = len(json.dumps(event, ensure_ascii=False)) + 32
            self.events.append((self.seq, event, size))
            self.event_bytes += size
            while len(self.events) > 1 and (len(self.events) > MAX_EVENTS or self.event_bytes > MAX_EVENT_BYTES):
                self.event_bytes -= self.events.popleft()[2]
            self.cond.notify_all()

    def since(self, seq, timeout):
        with self.cond:
            if self.seq <= seq and not self.closed:
                self.cond.wait(timeout)
            out = []
            for s, e, _ in reversed(self.events):     # newest first: stop at the first one already seen
                if s <= seq:
                    break
                out.append((s, e))
            out.reverse()
            return out

    def note(self, kind, **fields):
        """A Lassie event (not from rex)."""
        self.push(dict({"type": "lassie", "kind": kind}, **fields))

    # --- rex ----------------------------------------------------------------------------------------------
    def start(self, cfg, endpoint, model, workspace):
        argv, env, cwd = build_command(cfg, endpoint, model, workspace)
        with self.life:
            if self.closed:
                raise RexError("session closed")
            self.gen += 1             # first: the old rex's last events (exit) are dropped
            gen = self.gen
            self._stop()
            with self.cond:
                self.busy = False
                self.pending.clear()
            self.endpoint, self.model, self.workspace = check_endpoint(endpoint), model.strip(), cwd
            log.info("session %s: rex start in %s", self.sid[:6], cwd)
            self.note("starting", workspace=cwd, model=self.model, endpoint=self.endpoint)
            self.rex = RexProcess(argv, env, cwd, lambda event: self._from_rex(event, gen))

    def _from_rex(self, event, gen):
        if gen == self.gen:
            self.push(event)

    def send(self, obj):
        rex = self.rex
        return bool(rex and rex.send(obj))

    def chat(self, text):
        with self.cond:
            if self.busy:
                return "busy"
            if not (self.rex and self.rex.running):
                return "not running"
            self.busy = True
        if not self.send({"type": "chat", "text": text}):
            with self.cond:
                self.busy = False
            return "not running"
        return None

    def reply(self, pid, decision):
        """Answer one permission question exactly once. Returns an error text or None."""
        if decision not in DECISIONS:
            decision = "deny"
        with self.cond:
            if not isinstance(pid, str) or pid not in self.pending:
                return "no such question"
            self.pending.discard(pid)
        if not self.send({"type": "permission_reply", "id": pid, "decision": decision}):
            return "not running"
        self.push({"type": "lassie", "kind": "answered", "id": pid, "decision": decision})
        return None

    def stop(self):
        with self.life:
            self._stop()

    def _stop(self):
        rex, self.rex = self.rex, None
        if rex is not None:
            for pid in list(self.pending):     # rex denies them anyway when stdin closes; say it explicitly
                rex.send({"type": "permission_reply", "id": pid, "decision": "deny"})
            rex.stop()

    def close(self):
        with self.life:
            self.closed = True        # no start can follow
            self._stop()
        with self.cond:
            self.cond.notify_all()


class LassieServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = (os.name != "nt")

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, cfg, token=None, merman="auto"):
        super().__init__(("127.0.0.1", cfg.port), Handler)
        self.cfg = cfg
        self.token = token or secrets.token_urlsafe(32)
        self.port = self.server_address[1]
        self.hosts = {"127.0.0.1:%d" % self.port, "localhost:%d" % self.port}
        self.origins = {"http://" + h for h in self.hosts}
        self.cookie = "lassie_%d" % self.port
        self.cookie_secret = secrets.token_urlsafe(32)   # what the cookie holds: opens events and images only
        self.merman = find_merman() if merman == "auto" else merman
        self.renderer = Renderer(self.merman)
        self.sessions = {}
        self.lock = threading.Lock()
        self.stopping = False

    @property
    def url(self):
        return "http://127.0.0.1:%d/?t=%s" % (self.port, self.token)

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):
            log.info("client went away")
        else:
            log.error("request failed: %s", type(exc).__name__)

    def session(self, sid):
        with self.lock:
            return self.sessions.get(sid) if isinstance(sid, str) else None

    def new_session(self):
        with self.lock:
            if len(self.sessions) >= MAX_SESSIONS:
                return None
            ses = Session(secrets.token_urlsafe(16))
            self.sessions[ses.sid] = ses
            return ses

    def drop(self, sid):
        if not isinstance(sid, str):
            return
        with self.lock:
            ses = self.sessions.pop(sid, None)
        if ses:
            ses.close()

    def reap(self, idle=None, now=None):
        """Close sessions whose page has been gone for `idle` seconds. Returns the closed ids.
        `now` is the clock reading (time.monotonic by default); tests pass it in."""
        idle = SESSION_IDLE if idle is None else idle
        now = time.monotonic() if now is None else now
        with self.lock:
            gone = [s.sid for s in self.sessions.values() if s.clients == 0 and now - s.last_seen > idle]
        for sid in gone:
            log.info("session %s: no page for %.0f s, closing", sid[:6], idle)
            self.drop(sid)
        return gone

    def watch(self, every=5.0):
        def run():
            while not self.stopping:
                time.sleep(every)
                self.reap()
        threading.Thread(target=run, daemon=True).start()

    def close_all(self):
        self.stopping = True
        with self.lock:
            sids = list(self.sessions)
        ts = [threading.Thread(target=self.drop, args=(sid,)) for sid in sids]   # each stop may wait a few seconds
        for t in ts:
            t.start()
        for t in ts:
            t.join()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "lassie"
    timeout = 60                  # a client that stops sending (slow loris) is dropped; the event stream only writes

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), (fmt % args).split("?")[0])

    # --- helpers --------------------------------------------------------------------------------------------
    def _headers(self, status, ctype, length=None, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)

    def _reply(self, status, body=b"", ctype="text/plain; charset=utf-8", extra=None, close=False):
        if isinstance(body, (dict, list)):
            body, ctype = json.dumps(body).encode(), "application/json"
        elif isinstance(body, str):
            body = body.encode()
        self._headers(status, ctype, len(body), extra)
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _body(self):
        """The JSON object of a POST body, or None (a reply was sent)."""
        self._body_read = True
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self._reply(413, "too large", close=True)
            return None
        try:
            data = json.loads(self.rfile.read(n) or b"null")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._reply(400, {"error": "bad json"})
            return None
        return data

    def _cookie_token(self):
        try:
            c = SimpleCookie(self.headers.get("Cookie") or "")
        except CookieError:
            return None
        name = self.server.cookie
        return c[name].value if name in c else None

    def _header_ok(self):
        return _eq(self.headers.get("X-Lassie-Token", ""), self.server.token)

    def _cookie_ok(self):
        return _eq(self._cookie_token(), self.server.cookie_secret)

    # --- routing -----------------------------------------------------------------------------------------------
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method):
        self._body_read = method != "POST"
        try:
            self._route_inner(method)
        finally:
            if not self._body_read:
                self.close_connection = True

    def _route_inner(self, method):
        srv = self.server
        if self.headers.get("Transfer-Encoding") is not None:
            return self._reply(411, "length required", close=True)
        if self.headers.get("Host") not in srv.hosts:
            return self._reply(403, "forbidden", close=True)
        origin = self.headers.get("Origin")
        if origin is not None and origin not in srv.origins:
            return self._reply(403, "forbidden", close=True)
        url = urlsplit(self.path)
        path = url.path
        self.query = parse_qs(url.query)
        if method == "GET" and path == "/":
            return self._index()
        if method == "GET" and path.startswith("/static/"):
            name = path[len("/static/"):]
            if name in STATIC:
                return self._file(STATIC_DIR / name, STATIC[name])
        if method == "GET" and path.startswith("/lang/"):
            code = path[len("/lang/"):].removesuffix(".json")
            if code in ("en", "bg", "de", "fr", "ru", "zh"):
                return self._file(LANG_DIR / (code + ".json"), "application/json")
        if method == "GET" and path == "/favicon.ico":
            return self._file(STATIC_DIR / "favicon.ico", STATIC["favicon.ico"])
        if path.startswith("/api/"):
            return self._api(method, path[len("/api/"):])
        self._reply(404, "not found")

    def _q(self, name):
        v = self.query.get(name)
        return v[0] if v else None

    def _file(self, path, ctype):
        try:
            self._reply(200, path.read_bytes(), ctype)
        except OSError:
            self._reply(404, "not found")

    def _index(self):
        srv = self.server
        given = self._q("t")
        if given is not None:
            if not _eq(given, srv.token):
                return self._reply(403, "forbidden: open the link Lassie printed in its console")
            cookie = "%s=%s; HttpOnly; SameSite=Strict; Path=/" % (srv.cookie, srv.cookie_secret)
            return self._page({"Set-Cookie": cookie})
        if self._cookie_ok():
            return self._reply(403, "forbidden: open the start link again (the one Lassie printed in its console)")
        return self._reply(403, "forbidden: open the link Lassie printed in its console")

    def _page(self, extra):
        srv = self.server
        try:
            page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        except OSError:
            return self._reply(500, "page missing")
        page = page.replace("__LASSIE_TOKEN__", srv.token)
        return self._reply(200, page, "text/html; charset=utf-8", extra=extra)

    # --- API -----------------------------------------------------------------------------------------------------
    def _api(self, method, route):
        if method == "GET" and route in ("events", "image"):
            if not (self._header_ok() or self._cookie_ok()):
                return self._reply(403, "forbidden", close=True)
            return self._events() if route == "events" else self._image()
        if not self._header_ok():
            return self._reply(403, "forbidden", close=True)
        if method == "GET" and route == "state":
            return self._state()
        if method == "GET" and route == "models":
            return self._models()
        if method == "POST":
            fn = {
                "start": self._start, "chat": self._chat, "stop": self._stop, "reset": self._reset,
                "model": self._model, "permission": self._permission, "close": self._close,
                "render": self._render,
            }.get(route)
            if fn:
                return fn()
        return self._reply(404, "not found")

    def _state(self):
        cfg = self.server.cfg
        return self._reply(200, {
            "version": __version__, "endpoint": cfg.endpoint, "model": cfg.model, "workspace": cfg.workspace,
            "language": cfg.language, "local_tools": cfg.local_tools, "merman": bool(self.server.merman),
            "config": cfg.source,
        })

    def _models(self):
        """Model names from <endpoint>/v1/models, fetched here so the page needs no access to the gateway."""
        try:
            endpoint = check_endpoint(self._q("endpoint") or self.server.cfg.endpoint)
        except RexError as exc:
            return self._reply(200, {"models": [], "error": str(exc)})
        req = urllib.request.Request(endpoint + "/v1/models", headers={"Accept": "application/json"})
        key = os.environ.get("RUNTIME_LITELLM__API_KEY")
        if key:
            req.add_header("Authorization", "Bearer " + key)
        try:
            with _OPENER.open(req, timeout=MODELS_TIMEOUT) as r:
                data = json.loads(r.read(MODELS_MAX + 1)[:MODELS_MAX])
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return self._reply(200, {"models": [], "error": "models not available: %s" % type(exc).__name__})
        return self._reply(200, {"models": parse_models(data)})

    def _ses(self, data):
        ses = self.server.session(data.get("sid"))
        if ses is None:
            self._reply(404, {"error": "no such session"})
        return ses

    def _start(self):
        """Start (or restart) rex for a page: {sid?, endpoint, model, workspace} -> {sid}."""
        srv = self.server
        data = self._body()
        if data is None:
            return
        ses = srv.session(data.get("sid")) if data.get("sid") else None
        if ses is None:
            ses = srv.new_session()
            if ses is None:
                return self._reply(503, {"error": "too many sessions"})
        try:
            ses.start(srv.cfg, str(data.get("endpoint") or ""), str(data.get("model") or ""),
                      str(data.get("workspace") or ""))
        except RexError as exc:
            ses.note("failed", message=str(exc))
            return self._reply(400, {"sid": ses.sid, "error": str(exc)})
        return self._reply(200, {"sid": ses.sid, "workspace": ses.workspace})

    def _chat(self):
        data = self._body()
        if data is None:
            return
        ses = self._ses(data)
        if ses is None:
            return
        text = data.get("text")
        if not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT:
            return self._reply(400, {"error": "bad text"})
        err = ses.chat(text)
        if err:
            return self._reply(409, {"error": err})
        return self._reply(200, {"ok": True})

    def _simple(self, message):
        data = self._body()
        if data is None:
            return
        ses = self._ses(data)
        if ses is None:
            return
        ok = ses.send(message)
        return self._reply(200 if ok else 409, {"ok": ok})

    def _stop(self):
        return self._simple({"type": "stop"})

    def _reset(self):
        return self._simple({"type": "reset"})

    def _model(self):
        data = self._body()
        if data is None:
            return
        ses = self._ses(data)
        if ses is None:
            return
        model = data.get("model")
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            return self._reply(400, {"error": "bad model"})
        ses.model = model.strip()
        ok = ses.send({"type": "model", "model": ses.model})
        return self._reply(200 if ok else 409, {"ok": ok})

    def _permission(self):
        data = self._body()
        if data is None:
            return
        ses = self._ses(data)
        if ses is None:
            return
        err = ses.reply(data.get("id"), data.get("decision"))
        if err:
            return self._reply(409, {"error": err})
        return self._reply(200, {"ok": True})

    def _close(self):
        data = self._body()
        if data is None:
            return
        self.server.drop(data.get("sid"))
        return self._reply(200, {"ok": True})

    def _render(self):
        data = self._body()
        if data is None:
            return
        src = data.get("source")
        if not isinstance(src, str) or len(src) > MAX_SOURCE:
            return self._reply(400, {"error": "bad source"})
        try:
            return self._reply(200, {"svg": self.server.renderer.render(src)})
        except RenderError as exc:
            return self._reply(200, {"error": str(exc)})

    def _image(self):
        ses = self.server.session(self._q("sid"))
        if ses is None or not ses.workspace:
            return self._reply(404, "not found")
        path = confine.resolve_image(ses.workspace, self._q("path"))
        if path is None:
            return self._reply(404, "not found")
        try:
            body = path.read_bytes()
        except OSError:
            return self._reply(404, "not found")
        ctype = confine.IMAGE_TYPES[path.suffix.lower()]
        # an SVG opened directly must not run scripts
        return self._reply(200, body, ctype, extra={"Content-Security-Policy": "sandbox"})

    # --- event stream ---------------------------------------------------------------------------------------------
    def _events(self):
        srv = self.server
        ses = srv.session(self._q("sid"))
        if ses is None:
            return self._reply(404, "no such session")
        try:
            since = int(self.headers.get("Last-Event-ID") or self._q("since") or 0)
        except ValueError:
            since = 0
        self._headers(200, "text/event-stream; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        with ses.cond:
            ses.clients += 1
        try:
            while not ses.closed and not srv.stopping:
                batch = ses.since(since, HEARTBEAT)
                if not batch:
                    self.wfile.write(b": ping\n\n")
                for seq, event in batch:
                    self.wfile.write(sse_frame(seq, event))
                    since = seq
                self.wfile.flush()
                ses.last_seen = time.monotonic()
        except OSError:
            pass
        finally:
            with ses.cond:
                ses.clients -= 1
            ses.last_seen = time.monotonic()


def sse_frame(seq, event):
    """One SSE message. JSON never contains a raw newline, so one data: line is enough."""
    return b"id: %d\ndata: %s\n\n" % (seq, json.dumps(event, ensure_ascii=False).encode("utf-8"))


def parse_models(data):
    """Model ids from an OpenAI-style /v1/models answer ({"data": [{"id": ...}]}), sorted, unique."""
    items = data.get("data") if isinstance(data, dict) else data
    out = set()
    if isinstance(items, list):
        for it in items:
            mid = it.get("id") if isinstance(it, dict) else it
            if isinstance(mid, str) and mid.strip() and len(mid) <= 200:
                out.add(mid.strip())
    return sorted(out)
