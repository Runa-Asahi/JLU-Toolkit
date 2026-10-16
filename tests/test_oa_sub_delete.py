from jlu_toolkit import oa_subscribe


def test_delete_rule_roundtrip(tmp_path):
    code, obj = oa_subscribe.api("POST", "/api/oa/subscriptions", {}, {"keywords": "讲座"}, tmp_path)
    rid = obj["rule"]["id"]
    assert oa_subscribe.api("DELETE", f"/api/oa/subscriptions/{rid}", {}, None, tmp_path)[0] == 200
    assert oa_subscribe.api("GET", "/api/oa/subscriptions", {}, None, tmp_path)[1]["rules"] == []


def test_ui_delete_does_not_depend_on_confirm():
    from jlu_toolkit import ui_page
    src = ui_page.PAGE if hasattr(ui_page, "PAGE") else open(ui_page.__file__, encoding="utf-8").read()
    assert 'confirm("删除这条订阅？")' not in src
