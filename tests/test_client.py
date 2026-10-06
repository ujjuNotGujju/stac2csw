"""Client behaviour against a local mock of the Bhoonidhi API (auth, 401 refresh,
429 back-off, POST+GET pagination, token-request budget)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from stac2csw.client import AuthError, BhoonidhiClient

PAGES = [[{"id": f"i{n}", "collection": "C"} for n in range(k * 3, k * 3 + 3)] for k in range(3)]  # 9 items


class State:
    expire_after = None      # expire access token after N authenticated calls
    calls = 0
    token_calls = 0
    refresh_calls = 0
    throttle_once = True
    current = "tok1"


def make_handler(state):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): ...

        def _send(self, code, obj, headers=None):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b)

        def _body(self):
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")

        def _authed(self):
            return self.headers.get("Authorization") == f"Bearer {state.current}"

        def do_POST(self):
            body = self._body()
            if self.path == "/auth/token":
                if body.get("grant_type") == "password":
                    if body.get("password") != "pw":
                        return self._send(401, {"ErrorCode": 401})
                    state.token_calls += 1
                else:
                    state.refresh_calls += 1
                state.current = f"tok{state.token_calls + state.refresh_calls + 1}"
                return self._send(200, {"access_token": state.current, "refresh_token": "ref", "token_type": "Bearer", "expires_in": 1200})
            if not self._authed():
                return self._send(401, {})
            state.calls += 1
            if state.expire_after and state.calls == state.expire_after:
                state.current = "rotated-server-side"
                return self._send(401, {})
            if state.throttle_once:
                state.throttle_once = False
                return self._send(429, {}, {"Retry-After": "0"})
            if self.path == "/data/search":
                page = int(body.get("page", 0)) if "page" in body else 0
                return self._send(200, self._page(page, body))
            self._send(404, {})

        def do_GET(self):
            if not self._authed():
                return self._send(401, {})
            if self.path == "/data/collections":
                return self._send(200, {"collections": [{"id": "C"}]})
            if self.path.startswith("/data/search?page="):
                return self._send(200, self._page(int(self.path.split("=")[1]), {}))
            self._send(404, {})

        def _page(self, k, body):
            feats = PAGES[k]
            links = []
            if k == 0:   # POST next with merge
                links = [{"rel": "next", "method": "POST", "href": f"http://127.0.0.1:{self.server.server_port}/data/search", "body": {"page": 1}, "merge": True}]
            elif k == 1:  # GET next
                links = [{"rel": "next", "method": "GET", "href": f"http://127.0.0.1:{self.server.server_port}/data/search?page=2"}]
            return {"type": "FeatureCollection", "features": feats, "links": links}
    return H


@pytest.fixture
def server():
    state = State()
    srv = HTTPServer(("127.0.0.1", 0), make_handler(state))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield state, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def client(url, pw="pw"):
    return BhoonidhiClient(base_url=url, user="u", password=pw, min_interval=0, max_retries=3)


def test_pagination_follows_post_and_get_next_links(server):
    state, url = server
    items = list(client(url).search(["C"], limit=3))
    assert [i["id"] for i in items] == [f"i{n}" for n in range(9)]


def test_max_items_stops_early(server):
    _, url = server
    assert len(list(client(url).search(["C"], limit=3, max_items=4))) == 4


def test_429_is_retried(server):
    state, url = server
    assert len(list(client(url).search(["C"], limit=3))) == 9
    assert state.throttle_once is False


def test_expired_token_uses_refresh_not_password_login(server):
    state, url = server
    state.throttle_once = False
    state.expire_after = 2
    c = client(url)
    assert len(list(c.search(["C"], limit=3))) == 9
    assert state.refresh_calls == 1 and state.token_calls == 1   # login once, then refresh


def test_token_requests_stay_within_budget(server):
    state, url = server
    c = client(url)
    c.collections()
    list(c.search(["C"], limit=3))
    assert c.token_requests == 1   # the API allows only 20/hour


def test_bad_password_raises(server):
    _, url = server
    with pytest.raises(AuthError):
        client(url, pw="wrong").collections()
