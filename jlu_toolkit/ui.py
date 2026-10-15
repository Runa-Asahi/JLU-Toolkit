"""`jlu-toolkit ui`：本地网页界面（标准库 http.server，仅监听 127.0.0.1）。

数据目录：~/.config/jlu-toolkit/data/
  schedule.csv / grades.csv / exams.csv —— 由「从教务刷新」或 CLI fetch 生成
  schemes.json  —— 命名绩点方案（保研/综测…）：{"方案名": ["学期|课程", ...]}
  settings.json —— {"first_monday": "YYYY-MM-DD"}
所有数据只在本机；网页不引用任何外部资源。
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from . import core, exams as ex, jwxt
from .web_login import LoginFlow
from .ui_page import PAGE

ALLOWED_HOSTS = ("127.0.0.1", "localhost")


def data_dir() -> Path:
    d = jwxt.config_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


import secrets as _secrets
SSO_NONCE = _secrets.token_urlsafe(18)  # 每次启动换新；只经同源 /api/links 下发


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()

    def p(self, name: str) -> Path:
        return self.root / name

    def _json(self, name: str, default):
        from . import secure_store
        if name in secure_store.PERSONAL_CACHES:
            return secure_store.load_json(self.p(name), default)
        try:
            return json.loads(self.p(name).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def _write_json(self, name: str, obj) -> None:
        from . import secure_store
        if name in secure_store.PERSONAL_CACHES:
            secure_store.save_json(self.p(name), obj)
            return
        self.p(name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")

    def courses(self) -> list[core.Course]:
        f = self.p("schedule.csv")
        return core.load_courses(str(f)) if f.exists() else []

    def grades(self) -> list[core.Grade]:
        f = self.p("grades.csv")
        return core.load_grades(str(f)) if f.exists() else []

    def exams(self) -> list[ex.Exam]:
        f = self.p("exams.csv")
        return ex.load_exams(str(f)) if f.exists() else []

    def schemes(self) -> dict:
        return self._json("schemes.json", {})

    def settings(self) -> dict:
        return self._json("settings.json", {})

    def calendar(self) -> dict:
        return self._json("calendar.json", {})

    def periods(self) -> dict:
        return self._json("periods.json", {})

    def first_monday(self) -> Optional[str]:
        """手动覆盖优先，否则用教务校历。"""
        return self.settings().get("first_monday") or self.calendar().get("first_monday") or None

    def save_scheme(self, name: str, keys: list[str]) -> None:
        name = (name or "").strip()[:40]
        if not name:
            raise ValueError("方案名不能为空")
        with self.lock:
            s = self.schemes()
            s[name] = sorted({str(k) for k in keys})
            self._write_json("schemes.json", s)

    def delete_scheme(self, name: str) -> None:
        with self.lock:
            s = self.schemes()
            s.pop(name, None)
            self._write_json("schemes.json", s)

    def save_settings(self, upd: dict) -> None:
        fm = upd.get("first_monday")
        if fm == "":
            with self.lock:
                s = self.settings()
                s.pop("first_monday", None)
                self._write_json("settings.json", s)
            return
        if fm:
            d = dt.date.fromisoformat(fm)
            if d.weekday() != 0:
                raise ValueError("第一周起始日必须是星期一")
        with self.lock:
            s = self.settings()
            s.update({k: v for k, v in upd.items() if k in ("first_monday",)})
            self._write_json("settings.json", s)

    def snapshot(self) -> dict:
        gs = self.grades()
        mtimes = [self.p(n).stat().st_mtime for n in ("schedule.csv", "grades.csv", "exams.csv")
                  if self.p(n).exists()]
        return {
            "courses": [c.__dict__ for c in self.courses()],
            "grades": [dict(g.__dict__, key=f"{g.term}|{g.name}",
                            point=core.score_to_point(g.score)) for g in gs],
            "exams": [e.to_dict() for e in ex.upcoming(self.exams(), dt.datetime(1970, 1, 1))],
            "schemes": self.schemes(),
            "settings": self.settings(),
            "calendar": self.calendar(),
            "periods": self.periods(),
            "first_monday": self.first_monday(),
            "first_monday_source": ("manual" if self.settings().get("first_monday")
                                    else "calendar" if self.calendar().get("first_monday") else None),
            "official_gpa": self._json("official_gpa.json", None),
            "completion": self._json("completion.json", None),
            "plan_courses": self._json("plan_courses.json", None),
            "gpa_all": core.gpa(gs),
            "updated": (dt.datetime.fromtimestamp(max(mtimes)).isoformat(timespec="minutes")
                        if mtimes else None),
            **cache_info(self),
        }


def cache_info(store: Store) -> dict:
    """{offline, cached_at}：教务会话不可用（过期/未登录）但本地有缓存时 offline=True。只读本地。"""
    from . import secure_store
    times = [v.get("updated_at") for v in secure_store.cache_meta(store.root).values() if isinstance(v, dict)]
    for n in ("schedule.csv", "grades.csv", "exams.csv"):
        if store.p(n).exists():
            times.append(dt.datetime.fromtimestamp(store.p(n).stat().st_mtime).isoformat(timespec="seconds"))
    cached_at = max((t for t in times if t), default=None)
    st = jwxt.session_status()
    return {"offline": bool(cached_at) and st["state"] != jwxt.SESSION_OK, "cached_at": cached_at}


def refresh_from_jwxt(store: Store) -> dict:
    """用已保存会话拉取全部数据；不做交互登录。"""
    from .sync import sync_all

    client = jwxt.JwxtClient()
    if not client.load_session():
        raise jwxt.LoginError("没有已保存的会话，请先在终端运行 jlu-toolkit fetch 登录一次")
    return sync_all(client, store.root)


def _client() -> "jwxt.JwxtClient":
    c = jwxt.JwxtClient()
    if not c.load_session():
        raise jwxt.LoginError("没有已保存的会话，请先在终端运行 jlu-toolkit fetch 登录一次")
    return c


# 吉大六个校区（教务代码表 01–06）；前端同样内置，接口取不到时也用这份
CAMPUSES = [{"code": "01", "name": "前卫校区"}, {"code": "02", "name": "南岭校区"},
            {"code": "03", "name": "新民校区"}, {"code": "04", "name": "朝阳校区"},
            {"code": "05", "name": "南湖校区"}, {"code": "06", "name": "和平校区"}]


def campuses_api(store: Store) -> dict:
    from . import rooms
    cache = store.p("campuses.json")
    if cache.exists():
        return {"campuses": json.loads(cache.read_text(encoding="utf-8"))}
    try:
        c = _client()
    except Exception:  # noqa: BLE001  无会话时直接给内置列表
        return {"campuses": CAMPUSES}
    lst = rooms.list_campuses(c)
    cache.write_text(json.dumps(lst, ensure_ascii=False), encoding="utf-8")
    c.save_session()
    return {"campuses": lst}


def build_room_query(store: Store, body: dict, term: str = ""):
    """UI / CLI / MCP 共用：body 字段 campus,date,periods,building,kind,seat_min,seat_max,
    exam_min,exam_max,mode(date|week),weeks,weekday,extra{}。"""
    from . import rooms
    mode = str(body.get("mode") or "date")
    start, end = rooms.parse_periods_arg(str(body.get("periods") or "1-2"))
    campus = str(body.get("campus") or "")
    if not campus:
        raise ValueError("请选择校区")
    i = lambda k: int(body.get(k) or 0)  # noqa: E731
    if mode == "week":
        weeks = rooms.expand_weeks(str(body.get("weeks") or ""))
        if not weeks:
            raise ValueError("按周次查询需要周次，如 6 或 6-8")
        weekday = i("weekday")
        if not 1 <= weekday <= 7:
            raise ValueError("按周次查询需要星期 1-7")
        date, week = "", int(weeks.split(",")[0])
    else:
        date = str(body.get("date") or dt.date.today().isoformat())
        dt.date.fromisoformat(date)
        week, weekday = rooms.week_and_weekday(date, store.first_monday())
        weeks = ""
    extra = body.get("extra") or {}
    if not isinstance(extra, dict):
        raise ValueError("extra 应为 {字段: 值}")
    return rooms.RoomQuery(campus, date, start, end, str(body.get("building") or ""),
                           term or store.calendar().get("term", ""), week, weekday,
                           kind=str(body.get("kind") or ""), seat_min=i("seat_min"), seat_max=i("seat_max"),
                           exam_min=i("exam_min"), exam_max=i("exam_max"), mode=mode, weeks=weeks,
                           extra={str(k): str(v) for k, v in extra.items()})


def rooms_api(store: Store, body: dict) -> dict:
    from . import rooms
    q = build_room_query(store, body)
    c = _client()
    found, prof = rooms.query_rooms_any(c, q, store.p("rooms_profile.json"), store.first_monday())
    c.save_session()
    return {"mode": q.mode, "date": q.date, "week": q.week, "weeks": q.weeks, "weekday": q.weekday,
            "periods": [q.start, q.end], "total": prof.get("total"), "rooms": [r.to_dict() for r in found]}


def _cached(store: Store, name: str, fn):
    f = store.p(name)
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    c = _client()
    v = fn(c)
    if v:
        f.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    c.save_session()
    return v


def buildings_api(store: Store, body: dict) -> dict:
    from . import rooms
    campus = str(body.get("campus") or "")
    safe = re.sub(r"\W", "_", campus)
    return {"buildings": _cached(store, f"buildings2_{safe}.json", lambda c: rooms.list_buildings(c, campus))}


def room_types_api(store: Store) -> dict:
    from . import rooms
    return {"types": _cached(store, "room_types.json", lambda c: rooms.list_room_types(c))}


def room_fields_api(store: Store) -> dict:
    """「更多条件」：元数据里额外的检索字段（名称+标题）。"""
    from . import rooms
    return {"fields": _cached(store, "room_fields2.json",
                              lambda c: [{k: s[k] for k in ("name", "caption", "xtype")}
                                         for s in rooms.extra_search_fields(rooms.full_meta(c))])}


# ───────────────────────── OA 校内通知（只读本地缓存） ─────────────────────────

def _oa_url(u) -> str:
    """只保留指向 oa.jlu.edu.cn 的链接（相对路径补全），其它一律丢弃。"""
    import urllib.parse
    from . import oa
    u = str(u or "").strip()
    if not u:
        return ""
    full = urllib.parse.urljoin(oa.BASE + "/", u)
    pr = urllib.parse.urlparse(full)
    return full if pr.scheme in ("http", "https") and pr.hostname == "oa.jlu.edu.cn" else ""


def _oa_item(i: dict, full: bool = False) -> dict:
    out = {"id": str(i.get("id") or ""), "title": str(i.get("title") or ""), "time": str(i.get("time") or ""),
           "dept": str(i.get("dept") or ""), "top": bool(i.get("top")), "url": _oa_url(i.get("url"))}
    if full:
        atts = i.get("attachments")
        out["text"] = str(i.get("text") or "")
        out["attachments"] = [{"name": str(a.get("name") or "附件"), "url": _oa_url(a.get("url"))}
                              for a in (atts if isinstance(atts, list) else []) if isinstance(a, dict) and _oa_url(a.get("url"))]
    return out


def _oa_data(store: Store) -> dict:
    d = store._json("oa_notices.json", {})
    return d if isinstance(d, dict) else {}


def _oa_known(store: Store) -> dict:
    """栏目名：内置 179577 校内通知 + data/oa_channels.json（--probe 发现）+ 缓存里记下的栏目。"""
    from . import oa
    f = store._json("oa_channels.json", {})
    f = {str(k): str(v) for k, v in f.items()} if isinstance(f, dict) else {}
    from . import college
    oa_ch = {**oa.KNOWN_CHANNELS, **f, **(_oa_data(store).get("channels") or {})}
    lead = {k: v for k, v in oa_ch.items() if not _OA_TAIL.search(v)}
    tail = {k: v for k, v in oa_ch.items() if _OA_TAIL.search(v)}
    # 顺序：OA 常规栏目 → 软件学院/计算机学院 → 医院、动科等院系栏目
    return {**lead, **college.channels(), **tail}


import re as _re
_OA_TAIL = _re.compile(r"医院|动科|动物")


def _resolve(cat: str, known: dict) -> str:
    from . import college, oa
    return cat if college.is_channel(cat) else oa.resolve_category(cat, known)


def oa_channels_api(store: Store) -> dict:
    ch = _oa_known(store)
    return {"ok": True, "channels": [{"id": k, "name": v} for k, v in ch.items()]}


def oa_notices_api(store: Store, q: dict) -> dict:
    """GET /api/oa/notices?category=&keyword=&limit= —— 只读 data/oa_notices.json，不访问 OA。"""
    from . import oa
    d = _oa_data(store)
    known = _oa_known(store)
    ch = _resolve(q.get("category", ""), known)
    kw = q.get("keyword", "").strip()
    try:
        limit = max(1, min(500, int(q.get("limit") or 500)))
    except ValueError:
        raise ValueError("limit 应为整数")
    from . import college
    extra = {}
    if college.is_channel(ch):
        d = college.load(store.root)
        src, _ = college._split(ch)
        items = [{**_oa_item(i), "url": college.safe_url(i.get("url"), src["host"])}
                 for i in ((d.get("channels_items") or {}).get(ch) or []) if isinstance(i, dict)]
        st = (d.get("status") or {}).get(ch) or {}
        extra = {"source": "college", "status": st.get("status", ""), "hint": st.get("hint", ""),
                 "source_url": college.list_url(ch)}
        d = {**d, "updated": st.get("at") or d.get("updated")}
    else:
        items = [_oa_item(i) for i in ((d.get("channels_items") or {}).get(ch) or []) if isinstance(i, dict)]
    if kw:
        items = [i for i in items if kw in i["title"] or kw in i["dept"]]
    items.sort(key=lambda i: (i["top"], i["time"], i["id"]), reverse=True)
    items = items[:limit]
    return {"ok": True, "channel": ch, "channel_name": known.get(ch) or oa.KNOWN_CHANNELS.get(ch, ""),
            "count": len(items), "fetched_at": d.get("updated"), "items": items, **extra}


def oa_notice_api(store: Store, nid: str) -> Optional[dict]:
    """GET /api/oa/notice?id= —— 单条（含本地已缓存的正文/附件，若有）。"""
    for lst in ((_oa_data(store).get("channels_items") or {}).values()):
        for i in lst or []:
            if isinstance(i, dict) and str(i.get("id")) == nid:
                return {"ok": True, "item": _oa_item(i, full=True)}
    from . import college
    i = college.find(store.root, nid)
    if i:
        src, _ = college._split(i.get("channel", ""))
        return {"ok": True, "item": {**_oa_item(i, full=True), "url": college.safe_url(i.get("url"), src["host"]),
                                     "source": "college"}}
    return None


def oa_refresh_api(store: Store, body: dict) -> dict:
    """POST /api/oa/refresh —— 只抓第 1 页（max_pages=1），写回本地缓存。"""
    from . import oa
    from . import college
    ch = _resolve(str(body.get("category") or ""), _oa_known(store))
    if college.is_channel(ch):
        college.save(store.root, college.fetch(ch))
        return oa_notices_api(store, {"category": ch})
    client = jwxt.JwxtClient()
    has = bool(client.load_session())
    res = oa.fetch_notices(client, ch, limit=30, max_pages=1)
    oa.save(store.root, res)
    if has:
        client.save_session()
    return oa_notices_api(store, {"category": ch})


LINK_FIELDS = ("id", "name", "icon", "url", "purpose", "login", "campus_only", "vpn_url")


def _link_ok(u) -> bool:
    import urllib.parse
    try:
        p = urllib.parse.urlsplit(str(u or ""))
    except ValueError:
        return False
    h = (p.hostname or "").lower()
    return p.scheme == "https" and (h == "jlu.edu.cn" or h.endswith(".jlu.edu.cn")) and not p.username


def _vpn_rewriter():
    """露娜 #18 的 campus.vpn_url（WebVPN 主机 AES-CFB 加密）；还没合进来时返回 None。"""
    try:
        from .campus import vpn_url  # type: ignore
    except Exception:  # noqa: BLE001 — 模块不存在或缺 cryptography 都当作暂不支持
        return None
    return vpn_url


def campus_links_api(override: Optional[Path] = None) -> dict:
    """GET /api/links —— 内置 data/campus_links.json + 可选用户覆盖（config_dir/campus_links.json）。

    覆盖规则：同 id 合并字段；"hidden": true 隐藏；新 id 追加。只返回 https://*.jlu.edu.cn 链接。
    """
    base = json.loads((Path(__file__).parent / "data" / "campus_links.json").read_text(encoding="utf-8"))
    items = {x["id"]: dict(x) for x in base.get("links", [])}
    order = list(items)
    override = override or jwxt.config_dir() / "campus_links.json"
    src, err = "builtin", None
    if override.exists():
        try:
            o = json.loads(override.read_text(encoding="utf-8"))
            for x in (o.get("links", []) if isinstance(o, dict) else o):
                if not isinstance(x, dict) or not x.get("id"):
                    continue
                i = str(x["id"])
                if i not in items:
                    order.append(i)
                items[i] = {**items.get(i, {}), **x}
            src = "override"
        except (json.JSONDecodeError, OSError, AttributeError, TypeError) as e:
            err = f"覆盖文件无效，已忽略：{e}"
    out, dropped = [], 0
    rewrite = _vpn_rewriter()
    for i in order:
        x = items[i]
        if x.get("hidden"):
            continue
        if not _link_ok(x.get("url")):
            dropped += 1
            continue
        y = {k: x[k] for k in LINK_FIELDS if k in x}
        if "vpn_url" in y and not _link_ok(y["vpn_url"]):
            y.pop("vpn_url")
        y["campus_only"] = bool(y.get("campus_only"))
        if y["campus_only"] and "vpn_url" not in y and rewrite is not None:
            try:
                v = rewrite(y["url"])
            except Exception:  # noqa: BLE001
                v = None
            if v and v != y["url"] and _link_ok(v):
                y["vpn_url"] = v
        out.append(y)
    r = {"ok": True, "links": out, "source": src, "dropped": dropped}
    if err:
        r["warning"] = err
    return r


def make_handler(store: Store, login: Optional[LoginFlow] = None):
    login = login or LoginFlow()

    class H(BaseHTTPRequestHandler):
        server_version = "SuperJLU-UI"

        def log_message(self, fmt, *args):  # 安静
            pass

        def _sso_jump(self):
            """GET /api/sso/jump?id=&n= —— 同源 nonce 校验后 302 到带 ST 的目标；失败退回普通地址 ?sso=failed。"""
            import hmac, urllib.parse
            from . import sso
            q = {k: v[0] for k, v in urllib.parse.parse_qs(self.path.partition("?")[2]).items()}
            sid = q.get("id", "")
            if not hmac.compare_digest(q.get("n", ""), SSO_NONCE):
                return self._send(403, b"bad nonce", "text/plain")
            site = (self.headers.get("Sec-Fetch-Site") or "").lower()
            if site and site not in ("same-origin", "none"):
                return self._send(403, b"cross-site", "text/plain")
            if not sso.capable(sid):
                return self._send(404, b"not sso-capable", "text/plain")
            loc, _ok = sso.jump_url(sid)
            if not sso._jlu_https(loc):
                return self._send(502, b"bad target", "text/plain")
            return self._send(302, b"", "text/plain", {"Location": loc, "Referrer-Policy": "no-referrer"})

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ALLOWED_HOSTS

        def _send(self, code: int, body: bytes, ctype: str, extra: Optional[dict] = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                raise ValueError("请求过大")
            return json.loads(self.rfile.read(n) or b"{}")

        def _oa_sub(self, method: str) -> bool:
            # OA 订阅接口（oa_subscribe.api）：GET/POST/DELETE /api/oa/subscriptions[/<id>]
            from urllib.parse import parse_qs, urlsplit
            from . import oa_subscribe
            u = urlsplit(self.path)
            if not u.path.startswith("/api/oa/subscriptions"):
                return False
            try:
                body = self._body() if method == "POST" else None
            except ValueError as e:
                self._json({"ok": False, "error": str(e)}, 400)
                return True
            code, obj = oa_subscribe.api(method, u.path, parse_qs(u.query), body, store.root)
            self._json(obj, code)
            return True

        def do_DELETE(self):
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            path = self.path.split("?")[0]
            if path.startswith("/api/login/accounts/"):
                import urllib.parse
                u = urllib.parse.unquote(path[len("/api/login/accounts/"):])
                ok = login.delete_account(u)
                return self._json({"ok": ok, "accounts": login.accounts()}, 200 if ok else 404)
            if not self._oa_sub("DELETE"):
                self._send(404, b"not found", "text/plain")

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            path = self.path.split("?")[0]
            if self._oa_sub("GET"):
                return
            if path in ("/", "/index.html"):
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"})
            if path in ("/favicon.svg", "/favicon.ico"):  # SuperJLU 官方标志
                from importlib import resources
                data = resources.files("jlu_toolkit").joinpath("data", path[1:]).read_bytes()
                ctype = "image/svg+xml" if path.endswith(".svg") else "image/x-icon"
                return self._send(200, data, ctype)
            if path == "/api/sessions":  # 各校园站点会话状态（只读本地）
                from . import sessions
                return self._json(sessions.all_status())
            if path == "/api/session":
                # 会话状态 + 离线缓存信息（offline/cached_at），供前端显示「请重新登录」/「离线数据」；只读本地
                return self._json({**jwxt.session_status(), **cache_info(store)})
            if path.startswith("/api/oa/"):
                import urllib.parse
                q = {k: v[0] for k, v in urllib.parse.parse_qs(self.path.partition("?")[2]).items()}
                try:
                    if path == "/api/oa/notices":
                        return self._json(oa_notices_api(store, q))
                    if path == "/api/oa/channels":
                        return self._json(oa_channels_api(store))
                    if path == "/api/oa/notice":
                        r = oa_notice_api(store, q.get("id", ""))
                        return self._json(r or {"ok": False, "error": "本地没有这条通知"}, 200 if r else 404)
                except ValueError as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
            if path == "/api/links":
                from . import sso
                j = campus_links_api()
                for x in j.get("links") or []:
                    if isinstance(x, dict) and sso.capable(x.get("id", "")):
                        x["sso_url"] = "/api/sso/jump?id=" + x["id"] + "&n=" + SSO_NONCE
                return self._json(j)
            if path == "/api/sso/jump":
                return self._sso_jump()
            if path == "/api/login/accounts":  # 只返回账号名，绝不返回密码
                return self._json({"ok": True, "accounts": login.accounts()})
            if path == "/api/login":
                return self._json({"ok": True, "saved_username": login.saved_username(),
                                   "accounts": login.accounts(),
                                   "session": jwxt.session_status()})
            if path == "/api/data":
                return self._json(store.snapshot())
            if path == "/api/ics":
                fm = store.first_monday()
                if not fm:
                    return self._json({"error": "缺少开学日期：请先刷新（自动读取校历）或在设置里手动填写"}, 400)
                body = core.to_ics(store.courses(), fm, store.periods() or None).encode()
                return self._send(200, body, "text/calendar; charset=utf-8",
                                  {"Content-Disposition": 'attachment; filename="jlu-schedule.ics"'})
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if not self._host_ok() or "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._send(403, b"forbidden", "text/plain")
            if self._oa_sub("POST"):
                return
            try:
                body = self._body()
                if self.path == "/api/schemes":
                    store.save_scheme(body.get("name", ""), body.get("include") or [])
                elif self.path == "/api/schemes/delete":
                    store.delete_scheme(body.get("name", ""))
                elif self.path == "/api/settings":
                    store.save_settings(body)
                elif self.path == "/api/rooms":
                    return self._json({"ok": True, **rooms_api(store, body)})
                elif self.path == "/api/rooms/buildings":
                    return self._json({"ok": True, **buildings_api(store, body)})
                elif self.path == "/api/rooms/types":
                    return self._json({"ok": True, **room_types_api(store)})
                elif self.path == "/api/rooms/fields":
                    return self._json({"ok": True, **room_fields_api(store)})
                elif self.path == "/api/rooms/campuses":
                    return self._json({"ok": True, **campuses_api(store)})
                elif self.path == "/api/login/start":
                    return self._json({"ok": True, **login.start(body.get("username", ""), body.get("password", ""),
                                                                 body.get("remember", True) is not False)})
                elif self.path == "/api/login/send_code":
                    return self._json({"ok": True, **login.send_code()})
                elif self.path == "/api/login/verify":
                    return self._json({"ok": True, **login.verify(wechat_code=body.get("wechat_code", ""))})
                elif self.path == "/api/login/cancel":
                    return self._json({"ok": True, **login.cancel()})
                elif self.path == "/api/oa/refresh":
                    try:
                        return self._json(oa_refresh_api(store, body))
                    except jwxt.NetworkError as e:
                        return self._json({"ok": False, "error": f"连不上 OA（校外请先连 vpn.jlu.edu.cn）：{e}",
                                           "offline": True}, 502)
                    except jwxt.SessionExpiredError:
                        raise
                    except jwxt.LoginError as e:
                        return self._json({"ok": False, "error": str(e), "login_needed": True}, 400)
                elif self.path == "/api/session/keepalive":  # App 前台约 15 分钟调用一次
                    from . import sessions
                    r = sessions.keepalive()
                    if r["session"].get("session_expired"):
                        raise jwxt.SessionExpiredError()
                    return self._json({**r, **cache_info(store)})
                elif self.path == "/api/refresh":
                    return self._json({"ok": True, "result": refresh_from_jwxt(store), **store.snapshot()})
                else:
                    return self._send(404, b"not found", "text/plain")
                return self._json({"ok": True, **store.snapshot()})
            except jwxt.SessionExpiredError as e:
                return self._json({"ok": False, "error": str(e), "session_expired": True,
                                   "site": getattr(e, "site", "jwxt")}, 401)
            except (ValueError, jwxt.JwxtError) as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

    return H


def seed_demo(root: Path) -> None:
    ex_dir = Path(__file__).resolve().parent / "data" / "demo"
    for n in ("schedule.csv", "grades.csv", "exams.csv"):
        src = ex_dir / n
        if src.exists():
            shutil.copy(src, root / n)
    for n in ("calendar.json", "periods.json", "official_gpa.json", "completion.json", "plan_courses.json"):
        src = ex_dir / n
        if src.exists():
            shutil.copy(src, root / n)
    store = Store(root)
    keys = [f"{g.term}|{g.name}" for g in store.grades()
            if not g.name.startswith(("体育", "军事", "创新"))]
    store.save_scheme("保研", keys)


def serve(port: int = 8765, root: Optional[Path] = None, open_browser: bool = False) -> None:
    store = Store(root or data_dir())
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"SuperJLU 界面已启动：{url}\n数据目录：{store.root}\n按 Ctrl+C 退出。", flush=True)
    if open_browser:
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
