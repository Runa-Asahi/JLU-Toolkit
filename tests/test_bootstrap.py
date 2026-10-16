"""首次登录后的全量同步标记。"""
from jlu_toolkit import ui


def test_needs_bootstrap_when_caches_missing(tmp_path):
    store = ui.Store(tmp_path)
    assert ui.needs_bootstrap(store) is True
    assert store.snapshot()["needs_bootstrap"] is True


def test_needs_bootstrap_clears_when_core_caches_exist(tmp_path):
    store = ui.Store(tmp_path)
    for name in ui.BOOTSTRAP_CACHE_FILES:
        path = store.p(name)
        if name.endswith(".json"):
            path.write_text("{}", encoding="utf-8")
        else:
            path.write_text("x\n", encoding="utf-8")
    assert ui.needs_bootstrap(store) is False
    assert ui.cache_info(store)["needs_bootstrap"] is False


def test_ui_page_auto_refresh_hooks():
    page = ui.PAGE
    assert "needs_bootstrap" in page
    assert "async function fullRefresh" in page
    assert "登录成功，正在刷新各页面" in page
    assert 'await lgDone()' in page


def test_encrypted_completion_counts_as_cached(tmp_path):
    from jlu_toolkit import secure_store
    store = ui.Store(tmp_path)
    for name in ui.BOOTSTRAP_CACHE_FILES:
        if name == "completion.json":
            secure_store.save_json(store.p(name), {"groups": []})
        elif name.endswith(".json"):
            store.p(name).write_text("{}", encoding="utf-8")
        else:
            store.p(name).write_text("x\n", encoding="utf-8")
    assert ui.needs_bootstrap(store) is False
