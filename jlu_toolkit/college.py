"""学院新闻/通知（只读、免登录、只取列表第 1 页）。

软件学院 csw.jlu.edu.cn、计算机科学与技术学院 ccst.jlu.edu.cn 都是博达站群：
列表页每条是 <li> 里一个 YYYY-MM-DD 日期 + 指向 info/<栏目>/<id>.htm 的链接。
连不上（部分站点只在校园网可达）时返回「需校园网」状态而不是抛错。
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json
import re
import urllib.parse
from pathlib import Path
from typing import Optional

CACHE = "college_news.json"
TIMEOUT = 10
UA = "Mozilla/5.0 (SuperJLU read-only)"

SOURCES = {
    "csw": {"name": "软件学院", "host": "csw.jlu.edu.cn", "cols": {
        "xytz": ("学院通知", "index/xytz_.htm"),
        "jbtz": ("教办通知", "index/jbtz.htm"),
        "xbtz": ("学办通知", "index/xbtz.htm"),
        "xsdt": ("学术动态", "index/xsdt.htm"),
    }},
    "ccst": {"name": "计算机学院", "host": "ccst.jlu.edu.cn", "cols": {
        "xytz": ("学院通知", "xwzx/xytz.htm"),
        "xyxw": ("学院新闻", "xwzx/xyxw.htm"),
        "jxtz": ("教学通知", "rcpy/bksjy/jxtz.htm"),
        "yjs": ("研究生通知", "rcpy/yjsjy/gltz.htm"),
    }},
}


def channels() -> dict:
    """{"csw-xytz": "软件学院·学院通知", ...}"""
    return {f"{s}-{c}": f"{v['name']}·{n}" for s, v in SOURCES.items() for c, (n, _) in v["cols"].items()}


def is_channel(ch: str) -> bool:
    return str(ch or "") in channels()


def _split(ch: str):
    s, c = ch.split("-", 1)
    return SOURCES[s], c


def list_url(ch: str) -> str:
    src, c = _split(ch)
    return f"https://{src['host']}/{src['cols'][c][1]}"


def safe_url(u, host: str, base: str = "") -> str:
    """补全为绝对地址，且只保留 https?://<host>/ 下的链接。"""
    u = str(u or "").strip()
    if not u:
        return ""
    full = urllib.parse.urljoin(base or f"https://{host}/", u)
    p = urllib.parse.urlparse(full)
    return full if p.scheme in ("http", "https") and p.hostname == host and not p.username else ""


_LI = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S | re.I)
_DATE = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})")
_A = re.compile(r"<a\b([^>]*?)href=\"([^\"]*info/\d+/\d+\.htm)\"([^>]*)>(.*?)</a>", re.S | re.I)
_TITLE = re.compile(r"title=\"([^\"]*)\"", re.I)


def parse_list(html: str, ch: str) -> list:
    src, _ = _split(ch)
    base = list_url(ch)
    out, seen = [], set()
    for blk in _LI.findall(html or ""):
        a = _A.search(blk)
        if not a:
            continue
        url = safe_url(a.group(2), src["host"], base)
        if not url or url in seen:
            continue
        t = _TITLE.search(a.group(1) + a.group(3))
        title = _html.unescape(t.group(1) if t else re.sub(r"<[^>]+>", "", a.group(4))).strip()
        if not title:
            continue
        d = _DATE.search(re.sub(r"<a\b.*?</a>", "", blk, flags=re.S))
        date = f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}" if d else ""
        m = re.search(r"info/(\d+)/(\d+)\.htm", url)
        seen.add(url)
        out.append({"id": f"{ch}:{m.group(1)}-{m.group(2)}", "title": title, "time": date,
                    "dept": channels()[ch], "top": False, "url": url, "channel": ch})
    return out


def _get(url: str, timeout: int = TIMEOUT) -> str:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — 固定 https 主机
        raw = r.read(2_000_000)
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def fetch(ch: str, getter=None) -> dict:
    """只取第 1 页。连不上返回 {"ok": False, "status": "need_campus"}，不抛异常。"""
    now = dt.datetime.now().isoformat(timespec="seconds")
    try:
        html = (getter or _get)(list_url(ch))
    except Exception as e:  # noqa: BLE001 — 超时/DNS/拒绝连接：多半是需要校园网
        return {"ok": False, "channel": ch, "status": "need_campus", "fetched_at": now, "items": [],
                "hint": f"连不上 {_split(ch)[0]['host']}，可能需要校园网或 vpn.jlu.edu.cn（{type(e).__name__}）"}
    items = parse_list(html, ch)
    st = "ok" if items else "empty"
    return {"ok": True, "channel": ch, "status": st, "fetched_at": now, "items": items,
            "hint": "" if items else "页面结构可能变了，没解析到条目"}


def load(root: Path) -> dict:
    try:
        d = json.loads((root / CACHE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(root: Path, res: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    d = load(root)
    ch = res["channel"]
    d.setdefault("status", {})[ch] = {"status": res["status"], "hint": res.get("hint", ""), "at": res["fetched_at"]}
    if res["items"]:
        prev = {i["id"]: i for i in (d.get("channels_items") or {}).get(ch, [])}
        for i in res["items"]:
            prev[i["id"]] = i
        d.setdefault("channels_items", {})[ch] = sorted(prev.values(), key=lambda i: (i["time"], i["id"]), reverse=True)[:300]
        d["updated"] = res["fetched_at"]
    f = root / CACHE
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)
    return f


def find(root: Path, nid: str) -> Optional[dict]:
    for lst in (load(root).get("channels_items") or {}).values():
        for i in lst or []:
            if isinstance(i, dict) and i.get("id") == nid:
                return i
    return None
