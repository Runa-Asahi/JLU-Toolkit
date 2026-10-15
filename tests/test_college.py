"""学院新闻（csw 软件学院 / ccst 计算机学院）：解析 + 缓存 + UI 接口，全部用离线 fixture。"""
from pathlib import Path

from jlu_toolkit import college, oa_subscribe, ui

FX = Path(__file__).parent / "fixtures" / "college"


def test_channels_named():
    ch = college.channels()
    assert ch["csw-xytz"] == "软件学院·学院通知"
    assert ch["ccst-xyxw"] == "计算机学院·学院新闻"


def test_parse_csw():
    items = college.parse_list((FX / "csw_xytz.html").read_text(encoding="utf-8"), "csw-xytz")
    assert len(items) == 15
    i = items[0]
    assert i["url"].startswith("https://csw.jlu.edu.cn/info/") and i["time"].count("-") == 2
    assert i["title"] and i["id"].startswith("csw-xytz:")
    assert all(x["url"].startswith("https://csw.jlu.edu.cn/") for x in items)


def test_parse_ccst():
    items = college.parse_list((FX / "ccst_xytz.html").read_text(encoding="utf-8"), "ccst-xytz")
    assert len(items) == 10 and items[0]["time"] == "2026-09-16"
    assert items[0]["url"] == "https://ccst.jlu.edu.cn/info/1091/21181.htm"


def test_offsite_links_dropped():
    html = ('<ul><li><span>2026-01-02</span><a href="https://evil.example/info/1/2.htm" title="x">x</a></li>'
            '<li><span>2026-01-03</span><a href="../info/9/8.htm" title="好">好</a></li></ul>')
    items = college.parse_list(html, "csw-xytz")
    assert [i["title"] for i in items] == ["好"]


def test_unreachable_is_need_campus(tmp_path):
    def boom(url):
        raise TimeoutError("t")
    r = college.fetch("ccst-xytz", getter=boom)
    assert r["ok"] is False and r["status"] == "need_campus" and "校园网" in r["hint"]
    college.save(tmp_path, r)
    s = ui.oa_notices_api(ui.Store(tmp_path), {"category": "ccst-xytz"})
    assert s["status"] == "need_campus" and s["count"] == 0


def test_ui_api(tmp_path):
    html = (FX / "csw_xytz.html").read_text(encoding="utf-8")
    college.save(tmp_path, college.fetch("csw-xytz", getter=lambda u: html))
    st = ui.Store(tmp_path)
    chs = {c["id"]: c["name"] for c in ui.oa_channels_api(st)["channels"]}
    assert "179577" in chs and chs["ccst-xytz"] == "计算机学院·学院通知"
    r = ui.oa_notices_api(st, {"category": "csw-xytz", "keyword": "蓝桥杯"})
    assert r["count"] == 1 and r["source"] == "college"
    d = ui.oa_notice_api(st, r["items"][0]["id"])
    assert d["item"]["url"].startswith("https://csw.jlu.edu.cn/") and d["item"]["source"] == "college"


def test_subscription_on_college(tmp_path, monkeypatch):
    html = (FX / "csw_xytz.html").read_text(encoding="utf-8")
    monkeypatch.setattr(college, "_get", lambda u, timeout=10: html)
    rule = oa_subscribe.add_rule("蓝桥杯", channel="csw-xytz", root=tmp_path)
    first = oa_subscribe.sync(None, "csw-xytz", tmp_path)
    assert first["first_run"] and first["fetched"] == 15
    monkeypatch.setattr(college, "_get", lambda u, timeout=10: html.replace("8077.htm", "9999.htm"))
    res = oa_subscribe.sync(None, "csw-xytz", tmp_path)
    hits = oa_subscribe.record_matches(res["new"], tmp_path)
    assert len(hits) == 1 and hits[0]["rule_id"] == rule["id"]
