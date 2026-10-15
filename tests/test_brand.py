"""SuperJLU 官方标志：网页 favicon / 页头、Android 图标都必须使用 assets/brand 里的标志。"""
import re
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from jlu_toolkit import ui
from jlu_toolkit.ui_page import PAGE

ROOT = Path(__file__).resolve().parents[1]


def _mark_path():
    return re.search(r'<path d="([^"]+)"', (ROOT / "assets/brand/superjlu-mark.svg").read_text()).group(1)


def test_brand_assets_exist():
    for n in ("superjlu-mark.png", "superjlu-wordmark.png", "superjlu-mark.svg",
              "superjlu-mark-black.png", "superjlu-mark-white.png", "superjlu.ico"):
        assert (ROOT / "assets/brand" / n).stat().st_size > 0


def test_page_uses_official_mark():
    assert _mark_path() in PAGE
    assert 'fill="currentColor"' in PAGE  # 深色主题跟随文字颜色
    assert 'href="/favicon.svg"' in PAGE
    assert '<span class="logo" aria-hidden="true">S</span>' not in PAGE


def test_favicon_routes(tmp_path):
    ui.seed_demo(tmp_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ui.make_handler(ui.Store(tmp_path)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        with urllib.request.urlopen(base + "/favicon.svg") as r:
            assert r.headers["Content-Type"] == "image/svg+xml"
            assert _mark_path() in r.read().decode()
        with urllib.request.urlopen(base + "/favicon.ico") as r:
            assert r.headers["Content-Type"] == "image/x-icon"
            assert r.read()[:4] == b"\x00\x00\x01\x00"
    finally:
        httpd.shutdown()


def test_android_icon_uses_mark():
    res = ROOT / "android/app/src/main/res"
    fg = (res / "drawable/ic_launcher_foreground.xml").read_text()
    assert _mark_path() in fg
    assert "#FFFFFF" in (res / "drawable/ic_launcher_background.xml").read_text()
    for q in ("mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi"):
        assert (res / f"mipmap-{q}/ic_launcher.png").exists()
        assert (res / f"mipmap-{q}/ic_launcher_round.png").exists()
