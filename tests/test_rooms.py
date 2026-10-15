import json

import pytest

from jlu_toolkit import jwxt, rooms


def test_parse_periods_arg():
    assert rooms.parse_periods_arg("3") == (3, 3)
    assert rooms.parse_periods_arg("3-4") == (3, 4)
    with pytest.raises(ValueError):
        rooms.parse_periods_arg("4-3")


def test_week_and_weekday():
    assert rooms.week_and_weekday("2026-10-15", "2026-09-07") == (6, 4)
    assert rooms.week_and_weekday("2026-10-15", None) == (0, 4)


META = {"models": [{"name": "cxkxjs", "url": "/jwapp/sys/kxjas/modules/kxjscx/cxkxjs.do",
                    "controls": [{"name": "XXXQDM", "url": "/jwapp/code/abc-xq.do"},
                                 {"name": "KXRQ"}, {"name": "KSJC"}, {"name": "JSJC"},
                                 {"name": "JXLDM", "url": "/jwapp/code/abc-jxl.do"}, {"name": "XNXQDM"}]}]}


def test_parse_meta():
    m = rooms.parse_meta(META)
    assert m["actions"] == ["/jwapp/sys/kxjas/modules/kxjscx/cxkxjs.do"]
    assert {"XXXQDM", "KXRQ", "KSJC", "JSJC"} <= set(m["fields"])
    assert m["dicts"]["XXXQDM"].endswith("abc-xq.do")


class FC:
    def __init__(self, ok_when):
        self.ok_when = ok_when
        self.calls = []

    def enter_emap_app(self, app):
        self.entered = app

    def _request(self, url, data=None, headers=None, method=None, allow_redirects=True):
        if "*default/index.do" in url:
            return 200, '<script src="../modules/kxjscx/kxjscx.js"></script>', url, b""
        if url.endswith("/jwapp/sys/kxjas/modules/kxjscx/kxjscx.js"):
            return 200, 'WIS_EMAP_SERV.getModel("modules/kxjscx.do","cxkxjs");$.post("./modules/kxjscx/cxkxjs.do")', url, b""
        return 404, "", url, b""

    def post_raw(self, path, fields, ref):
        self.calls.append((path, dict(fields)))
        if path.endswith("/kxjscx.do") and not fields:
            return 200, json.dumps(META), path
        if path.endswith(".do") and path.count("/") == 5 and not fields:
            return 404, "", path
        if path.endswith("abc-xq.do"):
            return 200, json.dumps({"datas": {"code": {"rows": [{"id": "01", "name": "前卫南区"}]}}}), path
        if self.ok_when(path, fields):
            return 200, json.dumps({"code": "0", "datas": {"cxkxjs": {"rows": [
                {"JASMC": "逸夫楼A101", "JXLDM_DISPLAY": "逸夫楼", "SKZWS": 120.0}]}}}), path
        return 200, json.dumps({"code": "1", "msg": "请选择校区"}), path


def test_query_uses_meta_scheme_and_saves_profile(tmp_path):
    fc = FC(lambda p, f: f.get("XXXQDM") == "01" and f.get("KXRQ") == "2026-10-15" and f.get("KSJC") == "1")
    q = rooms.RoomQuery("01", "2026-10-15", 1, 2, term="2026-2027-1", week=6, weekday=4)
    prof_path = tmp_path / "p.json"
    found, prof = rooms.query_rooms(fc, q, prof_path)
    assert found[0].name == "逸夫楼A101" and found[0].seats == "120"
    assert prof["scheme"] == "meta" and prof_path.exists()
    # 第二次直接用 profile，第一枪就中
    fc.calls.clear()
    rooms.query_rooms(fc, q, prof_path, meta=rooms.parse_meta(META))
    assert len(fc.calls) == 1


def test_query_failure_reports_codes(tmp_path):
    fc = FC(lambda p, f: False)
    with pytest.raises(jwxt.JwxtError, match="请选择校区"):
        rooms.query_rooms(fc, rooms.RoomQuery("01", "2026-10-15", 1, 2), None, max_tries=5)


def test_list_campuses_from_dict():
    assert rooms.list_campuses(FC(lambda p, f: False)) == [{"code": "01", "name": "前卫南区"}]


def test_probe_has_no_data_values():
    fc = FC(lambda p, f: f.get("XXXQDM") == "01" and "KXRQ" in f)
    rep = rooms.probe(fc, "2026-09-07", "2026-2027-1", None, "2026-10-15", (1, 2), log=lambda s: None)
    dumped = json.dumps(rep, ensure_ascii=False)
    assert rep["hit"] and rep["hit"]["rows"] == 1
    for v in ("逸夫楼A101", "前卫南区", "2026-10-15"):
        assert v not in dumped


def test_discover_finds_view_script_and_module():
    d = rooms.discover(FC(lambda p, f: False))
    assert "/jwapp/sys/kxjas/modules/kxjscx/kxjscx.js" in [f["path"] for f in d["fetched"]]
    assert "modules/kxjscx.do" in d["do_paths"] and "kxjscx" in d["modules_seen"]


def test_app_is_kxjas():
    assert rooms.APP.endswith("/jwapp/sys/kxjas/*default/index.do")
    assert rooms._abs("./modules/kxjscx/cxkxjs.do") == "/jwapp/sys/kxjas/modules/kxjscx/cxkxjs.do"
    assert rooms._abs("kxjscx/cxkxjs.do") == "/jwapp/sys/kxjas/modules/kxjscx/cxkxjs.do"
    assert rooms._abs("modules/kxjscx.do?*json=1") == "/jwapp/sys/kxjas/modules/kxjscx.do"


def test_expand_weeks_and_search_fields():
    assert rooms.expand_weeks("6-8,10") == "6,7,8,10"
    meta = {"models": [{"controls": [
        {"name": "XXXQDM", "caption": "学校校区", "search": True, "url": "code/xq.do"},
        {"name": "JASLXDM", "caption": "教室类型", "search": True, "url": "code/jaslx.do"},
        {"name": "SKZWS", "caption": "上课座位数", "search": True, "xtype": "number-range"},
        {"name": "SFYKT", "caption": "是否有空调", "search": True},
        {"name": "BZ", "caption": "备注", "search": False}]}]}
    sf = rooms.parse_search_fields(meta)
    assert [s["name"] for s in sf] == ["XXXQDM", "JASLXDM", "SKZWS", "SFYKT"]
    assert [s["name"] for s in rooms.extra_search_fields({"search": sf})] == ["SFYKT"]


def test_filters_week_mode_and_pagination():
    q = rooms.RoomQuery("01", "", 1, 2, term="t", weekday=3, mode="week", weeks="6-7",
                        seat_min=100, kind="K1", extra={"SFYKT": "1"})
    fn = rooms._with_filters(rooms.SCHEMES["querySetting"], ["SKZWS", "JASLXDM"])
    import json as _j
    qs = _j.loads(fn(q)["querySetting"])
    names = {(c["name"], c["builder"]) for c in qs}
    assert ("SKZWS", "moreEqual") in names and ("JASLXDM", "equal") in names and ("SFYKT", "equal") in names
    assert not any(c["name"] == "KXRQ" for c in qs)
    assert any(c["name"] == "ZC" and c["value"] == "6,7" for c in qs)

    pages = {1: [{"JASMC": f"A{i}", "SKZWS": 120} for i in range(200)],
             2: [{"JASMC": "B1", "SKZWS": 150}, {"JASMC": "B2", "SKZWS": 50}]}

    class C:
        calls = []
        def enter_emap_app(self, *_): pass
        def post_raw(self, ep, form, ref):
            self.calls.append(form.get("pageNumber"))
            n = int(form.get("pageNumber") or 1)
            return 200, _j.dumps({"code": "0", "datas": {"cxkxjs": {"totalSize": 202, "rows": pages.get(n, [])}}}), ""
    c = C()
    meta = {"actions": ["kxjscx/cxkxjs.do"], "fields": [], "dicts": {}}
    found, prof = rooms.query_rooms(c, rooms.RoomQuery("01", "2026-10-15", 1, 2, seat_min=100), None, meta=meta)
    assert len(found) == 201 and prof["total"] == 202 and c.calls[:2] == ["1", "2"]


def test_filter_buildings_and_week_dates_and_model_controls():
    b = [{"code": "1", "name": "前卫-逸夫楼"}, {"code": "2", "name": "南岭-交通馆"}]
    assert rooms.filter_buildings(b, "前卫校区") == b[:1]
    assert rooms.filter_buildings(b, "未知校区") == b
    assert rooms.week_dates("6,7", 3, "2026-09-07") == ["2026-10-14", "2026-10-21"]
    meta = {"models": [{"url": "kxjscx/cxkxjs.do", "controls": [{"name": "XXXQDM", "caption": "学校校区"}]}]}
    assert rooms.parse_search_fields(meta) == []
    assert rooms.query_model_controls(meta)[0]["caption"] == "学校校区"


def test_cli_rooms_json_is_ascii(monkeypatch, capsys):
    from jlu_toolkit import cli
    import sys as _s
    monkeypatch.setattr(_s, "argv", ["jlu-toolkit", "rooms", "--json"])
    monkeypatch.setattr(jwxt.JwxtClient, "load_session", lambda self: True)
    import pytest as _p
    with _p.raises(SystemExit):
        cli.main()
    out = capsys.readouterr().out.strip()
    import json as _j
    assert out.isascii() and "error" in _j.loads(out)


def test_campuses_hardcoded_in_page_and_api(tmp_path, monkeypatch):
    from jlu_toolkit import ui
    from jlu_toolkit.ui_page import PAGE
    names = [c["name"] for c in ui.CAMPUSES]
    assert names == ["前卫校区", "南岭校区", "新民校区", "朝阳校区", "南湖校区", "和平校区"]
    for c in ui.CAMPUSES:
        assert f'code:"{c["code"]}",name:"{c["name"]}"' in PAGE
    assert 'id="rcl"' not in PAGE and "点“加载”" not in PAGE
    assert "localStorage" in PAGE and "superjlu.room.campus" in PAGE
    monkeypatch.setattr(ui, "_client", lambda: (_ for _ in ()).throw(RuntimeError("no session")))
    assert ui.campuses_api(ui.Store(tmp_path)) == {"campuses": ui.CAMPUSES}
