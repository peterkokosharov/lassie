# lassie - the web front-end for rex
# Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
# AI models: claude-sonnet-5-5
# SPDX-License-Identifier: MIT
"""Safety gates (Host, Origin, token, cookie) and the JSONL relay with a fake rex, over real HTTP."""
import http.client
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from lassie import server as server_mod
from lassie.config import Config
from lassie.server import LassieServer, parse_models, sse_frame

HERE = Path(__file__).resolve().parent
FAKE_REX = HERE / "fake_rex"
FAKE_MERMAN = HERE / "fake_merman" / "fake_merman.py"
TOKEN = "test-token-123"


@pytest.fixture
def srv(tmp_path):
    cfg = Config(port=0, rex_dir=str(FAKE_REX), python=sys.executable, workspace=str(tmp_path),
                 endpoint="http://127.0.0.1:9", model="m1")
    s = LassieServer(cfg, token=TOKEN, merman=str(FAKE_MERMAN))
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    yield s
    s.close_all()
    s.shutdown()
    s.server_close()


def request(s, method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", s.port, timeout=10)
    h = {"Host": host or "127.0.0.1:%d" % s.port}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = raw
    return r.status, r.headers, payload


def api(s, route, body):
    return request(s, "POST", "/api/" + route, body, {"X-Lassie-Token": TOKEN})


# --- gates -----------------------------------------------------------------------------------------------------
def test_host_header_checked(srv):
    assert request(srv, "GET", "/static/app.js", host="evil.example:%d" % srv.port)[0] == 403
    assert request(srv, "GET", "/static/app.js", host="127.0.0.1")[0] == 403
    assert request(srv, "GET", "/static/app.js")[0] == 200
    assert request(srv, "GET", "/static/app.js", host="localhost:%d" % srv.port)[0] == 200


def test_page_needs_token_then_cookie(srv):
    assert request(srv, "GET", "/")[0] == 403
    assert request(srv, "GET", "/?t=wrong")[0] == 403
    status, headers, page = request(srv, "GET", "/?t=" + TOKEN)          # the page itself, no redirect
    assert status == 200
    assert TOKEN.encode() in page and b"__LASSIE_TOKEN__" not in page
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    pair = cookie.split(";")[0]
    assert TOKEN not in cookie and srv.cookie_secret in cookie            # the cookie is not the master token
    status, _, body = request(srv, "GET", "/", headers={"Cookie": pair})  # a cookie alone does not show the page
    assert status == 403 and b"start link again" in body and TOKEN.encode() not in body
    assert request(srv, "GET", "/", headers={"Cookie": "lassie_%d=nope" % srv.port})[0] == 403


def test_api_needs_header_token(srv):
    assert request(srv, "GET", "/api/state")[0] == 403
    assert request(srv, "POST", "/api/start", {})[0] == 403
    cookie = {"Cookie": "lassie_%d=%s" % (srv.port, srv.cookie_secret)}
    assert request(srv, "POST", "/api/start", {}, cookie)[0] == 403       # a cookie alone never drives rex
    assert request(srv, "GET", "/api/state", headers=cookie)[0] == 403
    assert request(srv, "GET", "/api/events?sid=x", headers={"Cookie": "lassie_%d=%s" % (srv.port, TOKEN)})[0] == 403
    status, _, state = request(srv, "GET", "/api/state", headers={"X-Lassie-Token": TOKEN})
    assert status == 200 and state["model"] == "m1" and state["merman"] is True


def test_foreign_origin_refused(srv):
    h = {"X-Lassie-Token": TOKEN, "Origin": "http://127.0.0.1:1234"}
    assert request(srv, "GET", "/api/state", headers=h)[0] == 403
    h["Origin"] = "http://127.0.0.1:%d" % srv.port
    assert request(srv, "GET", "/api/state", headers=h)[0] == 200


def test_binds_loopback_only(srv):
    assert srv.server_address[0] == "127.0.0.1"


# --- relay -----------------------------------------------------------------------------------------------------
class Stream:
    """Reads SSE events in a thread."""

    def __init__(self, s, sid, auth):
        self.events = []
        self.cond = threading.Condition()
        self.conn = http.client.HTTPConnection("127.0.0.1", s.port, timeout=30)
        h = {"Host": "127.0.0.1:%d" % s.port}
        h.update(auth)
        self.conn.request("GET", "/api/events?sid=" + sid, headers=h)
        self.resp = self.conn.getresponse()
        assert self.resp.status == 200
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for raw in self.resp:
                line = raw.decode().rstrip("\n")
                if line.startswith("data: "):
                    with self.cond:
                        self.events.append(json.loads(line[6:]))
                        self.cond.notify_all()
        except (OSError, ValueError):
            pass

    def wait(self, pred, timeout=15):
        end = time.monotonic() + timeout
        with self.cond:
            while True:
                hit = [e for e in self.events if pred(e)]
                if hit:
                    return hit[-1]
                left = end - time.monotonic()
                assert left > 0, "timeout; events: %r" % self.events[-10:]
                self.cond.wait(left)

    def close(self):
        self.conn.close()


def start(srv, tmp_path):
    status, _, data = api(srv, "start", {"endpoint": "http://127.0.0.1:9/", "model": "m1", "workspace": str(tmp_path)})
    assert status == 200, data
    return data["sid"]


def test_relay_chat_permission_model_reset(srv, tmp_path):
    sid = start(srv, tmp_path)
    st = Stream(srv, sid, {"Cookie": "lassie_%d=%s" % (srv.port, srv.cookie_secret)})
    try:
        assert st.wait(lambda e: e["type"] == "ready")["model"] == "m1"
        st.wait(lambda e: e["type"] == "log" and "fake rex starting" in e["text"])

        assert api(srv, "chat", {"sid": sid, "text": "env"})[0] == 200
        env = json.loads(st.wait(lambda e: e["type"] == "final")["text"])
        assert env["argv"][-4:] == ["--model", "m1", "--jsonl", "--local-tools"]
        assert env["endpoint"] == "http://127.0.0.1:9"
        assert Path(env["cwd"]).resolve() == tmp_path.resolve()

        assert api(srv, "chat", {"sid": sid, "text": "hello"})[0] == 200
        assert st.wait(lambda e: e["type"] == "final" and e["text"] == "echo: hello")
        assert st.wait(lambda e: e["type"] == "context")["percent"] == 90

        assert api(srv, "chat", {"sid": sid, "text": "perm"})[0] == 200
        q = st.wait(lambda e: e["type"] == "permission")
        assert api(srv, "chat", {"sid": sid, "text": "again"})[0] == 409            # busy while a turn runs
        assert api(srv, "permission", {"sid": sid, "id": "perm-9", "decision": "allow_once"})[0] == 409
        assert api(srv, "permission", {"sid": sid, "id": q["id"], "decision": "bogus"})[0] == 200
        assert api(srv, "permission", {"sid": sid, "id": q["id"], "decision": "allow_once"})[0] == 409  # once only
        assert st.wait(lambda e: e["type"] == "final" and e["text"] in ("deny", "allow_once"))["text"] == "deny"
        end = st.wait(lambda e: e["type"] == "tool_end")
        assert end["ok"] is False and end["diff"].startswith("@@")

        assert api(srv, "model", {"sid": sid, "model": "m2"})[0] == 200
        assert st.wait(lambda e: e["type"] == "model_ok")["model"] == "m2"
        assert api(srv, "reset", {"sid": sid})[0] == 200
        st.wait(lambda e: e["type"] == "reset_ok")

        assert api(srv, "chat", {"sid": sid, "text": "slow"})[0] == 200
        st.wait(lambda e: e["type"] == "token" and e["text"] == ".")
        assert api(srv, "stop", {"sid": sid})[0] == 200
        st.wait(lambda e: e["type"] == "error" and e["message"] == "interrupted")

        assert api(srv, "close", {"sid": sid})[0] == 200
        assert srv.session(sid) is None
    finally:
        st.close()


def test_start_errors_are_reported(srv, tmp_path):
    status, _, data = api(srv, "start", {"endpoint": "ftp://x", "model": "m", "workspace": str(tmp_path)})
    assert status == 400 and "http" in data["error"]
    status, _, data = api(srv, "start", {"endpoint": "http://x", "model": "m", "workspace": str(tmp_path / "nope")})
    assert status == 400 and "workspace" in data["error"]
    assert api(srv, "chat", {"sid": "unknown", "text": "x"})[0] == 404


def test_image_route_confined(srv, tmp_path):
    (tmp_path / "a.png").write_bytes(b"\x89PNGdata")
    (tmp_path.parent / "secret.png").write_bytes(b"\x89PNGsecret")
    sid = start(srv, tmp_path)
    cookie = {"Cookie": "lassie_%d=%s" % (srv.port, srv.cookie_secret)}
    status, headers, body = request(srv, "GET", "/api/image?sid=%s&path=a.png" % sid, headers=cookie)
    assert status == 200 and body == b"\x89PNGdata" and headers["Content-Type"] == "image/png"
    assert request(srv, "GET", "/api/image?sid=%s&path=../secret.png" % sid, headers=cookie)[0] == 404
    assert request(srv, "GET", "/api/image?sid=%s&path=a.png" % sid)[0] == 403          # no cookie, no token


def test_render_route(srv):
    status, _, data = api(srv, "render", {"source": "graph TD; A-->B"})
    assert status == 200 and data["svg"].startswith("<svg")
    status, _, data = api(srv, "render", {"source": "fail"})
    assert status == 200 and "Parse error" in data["error"]


def test_reaper_closes_sessions_without_a_page(srv, tmp_path):
    sid = start(srv, tmp_path)
    seen = srv.session(sid).last_seen
    assert srv.reap(idle=3600, now=seen + 10) == []
    assert srv.reap(idle=3600, now=seen + 3601) == [sid]      # injected clock: no dependence on timer resolution
    assert srv.session(sid) is None


def test_session_limit(srv, tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "MAX_SESSIONS", 1)
    start(srv, tmp_path)
    status, _, _ = api(srv, "start", {"endpoint": "http://x", "model": "m", "workspace": str(tmp_path)})
    assert status == 503


def test_parse_models_and_sse_frame():
    assert parse_models({"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}, {"x": 1}, "c"]}) == ["a", "b", "c"]
    assert parse_models("junk") == []
    assert sse_frame(3, {"type": "token", "text": "a\nb"}) == b'id: 3\ndata: {"type": "token", "text": "a\\nb"}\n\n'


# --- start / stop / close are serialised -------------------------------------------------------------------------
def _spy_rex(monkeypatch):
    made = []
    real = server_mod.RexProcess

    def make(*a, **k):
        r = real(*a, **k)
        made.append(r)
        return r
    monkeypatch.setattr(server_mod, "RexProcess", make)
    return made


def _wait_gone(procs, timeout=15):
    end = time.monotonic() + timeout
    while any(p.running for p in procs) and time.monotonic() < end:
        time.sleep(0.05)


def test_concurrent_starts_leave_one_rex(srv, tmp_path, monkeypatch):
    made = _spy_rex(monkeypatch)
    ses = srv.new_session()
    ts = [threading.Thread(target=ses.start, args=(srv.cfg, "http://127.0.0.1:9", "m1", str(tmp_path)))
          for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert len(made) == 4
    _wait_gone([r for r in made if r is not ses.rex])
    assert [r for r in made if r.running] == [ses.rex]
    srv.drop(ses.sid)
    _wait_gone(made)
    assert not any(r.running for r in made)


def test_start_racing_close_leaves_no_rex(srv, tmp_path, monkeypatch):
    made = _spy_rex(monkeypatch)
    ses = srv.new_session()
    errors = []

    def go():
        try:
            ses.start(srv.cfg, "http://127.0.0.1:9", "m1", str(tmp_path))
        except server_mod.RexError as exc:
            errors.append(str(exc))
    ts = [threading.Thread(target=go) for _ in range(3)]
    for t in ts:
        t.start()
    ses.close()
    for t in ts:
        t.join(30)
    _wait_gone(made)
    assert not any(r.running for r in made)
    with pytest.raises(server_mod.RexError):
        ses.start(srv.cfg, "http://127.0.0.1:9", "m1", str(tmp_path))


# --- event buffer, odd ids, timeout ------------------------------------------------------------------------------
def test_event_buffer_capped_by_bytes_and_since_is_correct(monkeypatch):
    monkeypatch.setattr(server_mod, "MAX_EVENT_BYTES", 10_000)
    ses = server_mod.Session("s")
    for i in range(50):
        ses.push({"type": "token", "text": "x" * 1000, "n": i})
    assert ses.event_bytes <= 10_000 + 1100 and len(ses.events) < 50
    assert ses.since(48, 0) == [(49, ses.events[-2][1]), (50, ses.events[-1][1])]
    assert [s for s, _ in ses.since(0, 0)][-1] == 50 and ses.since(50, 0) == []
    ses.push({"type": "token", "text": "y" * 50_000})          # one huge event is still kept (the newest)
    assert len(ses.events) == 1


def test_non_string_ids_are_harmless(srv, tmp_path):
    sid = start(srv, tmp_path)
    for bad in ([1], {"a": 1}, 5, None, True):
        assert api(srv, "permission", {"sid": sid, "id": bad, "decision": "deny"})[0] == 409
        assert api(srv, "permission", {"sid": bad, "id": "x", "decision": "deny"})[0] == 404
        assert api(srv, "close", {"sid": bad})[0] == 200
        assert api(srv, "chat", {"sid": bad, "text": "x"})[0] == 404
    assert srv.session(sid) is not None


def test_handler_has_a_read_timeout():
    assert server_mod.Handler.timeout == 60


# --- gate details ------------------------------------------------------------------------------------------------
def raw_request(s, text):
    import socket
    c = socket.create_connection(("127.0.0.1", s.port), timeout=10)
    c.sendall(text.encode())
    data = b""
    try:
        while b"\r\n\r\n" not in data:
            chunk = c.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        c.close()
    return int(data.split(b" ")[1])


def test_events_refused_without_auth(srv, tmp_path):
    sid = start(srv, tmp_path)
    assert request(srv, "GET", "/api/events?sid=" + sid)[0] == 403
    assert request(srv, "GET", "/api/events?sid=" + sid, headers={"Cookie": "lassie_%d=nope" % srv.port})[0] == 403


def test_origin_null_refused(srv):
    assert request(srv, "GET", "/api/state", headers={"X-Lassie-Token": TOKEN, "Origin": "null"})[0] == 403
    assert request(srv, "POST", "/api/start", {}, {"X-Lassie-Token": TOKEN, "Origin": "null"})[0] == 403


def test_transfer_encoding_and_oversize_body_refused(srv):
    host = "Host: 127.0.0.1:%d\r\n" % srv.port
    assert raw_request(srv, "POST /api/start HTTP/1.1\r\n" + host + "X-Lassie-Token: %s\r\n"
                       "Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n" % TOKEN) == 411
    assert raw_request(srv, "POST /api/start HTTP/1.1\r\n" + host + "X-Lassie-Token: %s\r\n"
                       "Content-Length: %d\r\n\r\n" % (TOKEN, server_mod.MAX_BODY + 1)) == 413
    assert raw_request(srv, "POST /api/start HTTP/1.1\r\n" + host + "X-Lassie-Token: %s\r\n"
                       "Content-Length: -5\r\n\r\n" % TOKEN) == 413


def test_close_all_stops_sessions_in_parallel(srv, monkeypatch):
    monkeypatch.setattr(server_mod.Session, "close", lambda self: time.sleep(0.6))
    for _ in range(5):
        srv.new_session()
    t0 = time.monotonic()
    srv.close_all()
    assert time.monotonic() - t0 < 2.0
    assert not srv.sessions


def test_models_fetch_does_not_follow_redirects(srv):
    import http.server
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path.startswith("/v1/models"):
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        def log_message(self, *a):
            pass
    gw = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=gw.serve_forever, daemon=True).start()
    try:
        status, _, data = request(srv, "GET", "/api/models?endpoint=http://127.0.0.1:%d" % gw.server_address[1],
                                  headers={"X-Lassie-Token": TOKEN})
    finally:
        gw.shutdown()
        gw.server_close()
    assert status == 200 and data["models"] == [] and "error" in data
    assert hits == ["/v1/models"]
