"""单点跳转：mock CAS，ticket 只出现在 302 Location，不进 JSON。"""
import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from jlu_toolkit import jwxt, sessions, sso, ui

CK = [{"name": "CASTGC", "value": "TGT-secret", "domain": "cas.jlu.edu.cn", "path": "/"}]


@pytest.fixture
def srv(tmp_path):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ui.make_handler(ui.Store(tmp_path)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def _req(port, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    return r.status, r.getheader("Location"), r.read()


def _mock(monkeypatch, cookies, resp, seen=None):
    monkeypatch.setattr(sessions, "migrate_cas_from_jwxt", lambda jar=None: 0)
    monkeypatch.setattr(sessions, "load_cookies", lambda s: cookies if s == "cas" else None)
    monkeypatch.setattr(sessions, "save_cas_from_jar", lambda jar: 1)
    exp = []
    monkeypatch.setattr(sessions, "mark_expired", lambda s, r="": exp.append(s))

    def fake(self, url, data=None, headers=None, method=None, allow_redirects=True):
        assert allow_redirects is False
        if seen is not None:
            seen.append(url)
        if isinstance(resp, Exception):
            raise resp
        return resp
    monkeypatch.setattr(jwxt.JwxtClient, "_request", fake)
    return exp


def test_jump_ok(monkeypatch):
    seen = []
    _mock(monkeypatch, CK, (302, "", "https://iedu.jlu.edu.cn/?ticket=ST-1-abc", b""), seen)
    url, ok = sso.jump_url("jwxt")
    assert ok and url.endswith("ticket=ST-1-abc")
    assert seen[0].startswith("https://cas.jlu.edu.cn/tpass/login?service=https%3A%2F%2Fiedu")


def test_ilearn_uses_jwcidentity(monkeypatch):
    seen = []
    _mock(monkeypatch, CK, (302, "", "https://jwcidentity.jlu.edu.cn/iplat-pass-jlu/thirdLogin/jlu/login?ticket=ST-9", b""), seen)
    url, ok = sso.jump_url("ilearn")
    assert ok and "jwcidentity" in seen[0]


@pytest.mark.parametrize("resp", [
    (200, '<form id="loginForm">', "https://cas.jlu.edu.cn/tpass/login?service=x", b""),
    (302, "", "https://evil.example/?ticket=ST-1", b""),
    (302, "", "https://iedu.jlu.edu.cn/", b""),
    jwxt.NetworkError("x"),
])
def test_jump_fallback(monkeypatch, resp):
    _mock(monkeypatch, CK, resp)
    url, ok = sso.jump_url("jwxt")
    assert not ok and url == jwxt.IEDU_PORTAL + "?sso=failed"


def test_no_cas_cookies(monkeypatch):
    _mock(monkeypatch, None, AssertionError("no request"))
    assert sso.jump_url("ilearn") == ("https://ilearntec.jlu.edu.cn/coursecenter/main/index?sso=failed", False)


def test_http_endpoint(monkeypatch, srv):
    _mock(monkeypatch, CK, (302, "", "https://iedu.jlu.edu.cn/?ticket=ST-1-abc", b""))
    h = {"Host": "127.0.0.1"}
    st, _, body = _req(srv, "/api/links", h)
    j = json.loads(body)
    assert "ST-" not in body.decode() and "TGT" not in body.decode()
    links = {x["id"]: x for x in j["links"]}
    assert links["jwxt"]["sso_url"].startswith("/api/sso/jump?id=jwxt&n=")
    assert "sso_url" in links["ilearn"] and "sso_url" not in links["pay"]
    st, loc, _ = _req(srv, links["jwxt"]["sso_url"], h)
    assert st == 302 and loc.endswith("ticket=ST-1-abc")
    assert _req(srv, "/api/sso/jump?id=jwxt&n=bad", h)[0] == 403
    assert _req(srv, links["jwxt"]["sso_url"], {**h, "Sec-Fetch-Site": "cross-site"})[0] == 403
    n = links["jwxt"]["sso_url"].split("n=")[1]
    assert _req(srv, "/api/sso/jump?id=pay&n=" + n, h)[0] == 404
