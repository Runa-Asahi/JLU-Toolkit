"""SuperJLU 桌面启动器（Windows 单文件 exe 的入口，也可在其它系统运行）。

- 在 127.0.0.1 的空闲端口后台启动 Web UI，自动打开浏览器
- 系统托盘图标（pystray + Pillow）：「打开 SuperJLU」「退出」；缺少 pystray 时无托盘，直接前台运行
- 单实例：config_dir()/desktop.json 记录端口；再次启动时若旧实例仍在响应，只打开浏览器后退出
- 窗口模式（无控制台）下 stdout 可能为 None，所有输出写到 config_dir()/desktop.log
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from . import jwxt

APP_NAME = "SuperJLU"
SERVER_HEADERS = ("SuperJLU-UI", "JLUToolkitUI")  # ui.make_handler 的 server_version（新/旧名）


def state_path() -> Path:
    return jwxt.config_dir() / "desktop.json"


def log_path() -> Path:
    return jwxt.config_dir() / "desktop.log"


def _setup_logging() -> logging.Logger:
    log = logging.getLogger("superjlu.desktop")
    if getattr(log, "_superjlu_path", None) != str(log_path()):
        for h0 in list(log.handlers):
            log.removeHandler(h0)
            h0.close()
        log._superjlu_path = str(log_path())  # type: ignore[attr-defined]
        try:
            log_path().parent.mkdir(parents=True, exist_ok=True)
            h: logging.Handler = logging.FileHandler(log_path(), encoding="utf-8")
        except OSError:
            h = logging.NullHandler()
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def url_for(port: int) -> str:
    return f"http://127.0.0.1:{port}/"


def read_state() -> Optional[dict]:
    try:
        d = json.loads(state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("port"), int) else None
    except (OSError, ValueError):
        return None


def write_state(port: int) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")


def clear_state(port: int) -> None:
    st = read_state()
    if st and st.get("port") == port:
        try:
            state_path().unlink()
        except OSError:
            pass


def is_running(port: int, timeout: float = 1.5) -> bool:
    """端口上是否是 SuperJLU 的 UI 服务（看 Server 头，避免把别的程序当成旧实例）。"""
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机地址不走代理
        with op.open(url_for(port) + "api/session", timeout=timeout) as r:
            srv = r.headers.get("Server") or ""
            return any(h in srv for h in SERVER_HEADERS)
    except Exception:  # noqa: BLE001
        return False


def existing_instance() -> Optional[int]:
    st = read_state()
    if st and is_running(st["port"]):
        return st["port"]
    return None


def start_server(root: Optional[Path] = None, port: int = 0):
    """后台线程启动 UI 服务，返回 (httpd, url)。"""
    from http.server import ThreadingHTTPServer
    from . import ui
    store = ui.Store(root or ui.data_dir())
    httpd = ThreadingHTTPServer(("127.0.0.1", port), ui.make_handler(store))
    t = threading.Thread(target=httpd.serve_forever, name="superjlu-ui", daemon=True)
    t.start()
    return httpd, url_for(httpd.server_address[1])


ICON_REL = Path("assets") / "brand" / "superjlu.ico"  # 官方品牌图标（PR #27）


def resource_path(rel) -> Path:
    """PyInstaller 打包后为 sys._MEIPASS/<rel>，开发时为仓库根目录/<rel>。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / rel


def icon_path() -> Optional[Path]:
    p = resource_path(ICON_REL)
    return p if p.is_file() else None


def make_icon(size: int = 64):
    """托盘图标：只用官方 assets/brand/superjlu.ico；缺失时返回 None（不画替代图标）。需要 Pillow。"""
    p = icon_path()
    if p is None:
        return None
    from PIL import Image
    img = Image.open(p)
    try:  # ICO 里挑最接近的尺寸
        img.size = max(img.ico.sizes(), key=lambda wh: (wh[0] <= max(size, 64), wh[0]))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    return img.convert("RGBA").resize((size, size), Image.LANCZOS)


def run_tray(url: str, on_quit: Callable[[], None], opener: Callable[[str], object]) -> bool:
    """显示托盘图标并阻塞直到「退出」；没有 pystray/Pillow 时返回 False。"""
    try:
        import pystray
        image = make_icon(64)
    except Exception:  # noqa: BLE001
        return False
    if image is None:
        logging.getLogger("superjlu.desktop").warning("缺少 %s，不显示托盘", ICON_REL)
        return False

    def _open(icon=None, item=None):
        opener(url)

    def _quit(icon, item=None):
        on_quit()
        icon.stop()

    icon = pystray.Icon(APP_NAME, image, f"{APP_NAME} · {url}", menu=pystray.Menu(
        pystray.MenuItem("打开 SuperJLU", _open, default=True),
        pystray.MenuItem("退出", _quit)))
    icon.run()
    return True


def main(argv=None, opener: Callable[[str], object] = webbrowser.open,
         tray: Callable = run_tray, wait: Optional[Callable[[], None]] = None) -> int:
    log = _setup_logging()
    old = existing_instance()
    if old:
        log.info("已有实例在端口 %s，打开浏览器", old)
        opener(url_for(old))
        return 0
    httpd, url = start_server()
    port = httpd.server_address[1]
    write_state(port)
    log.info("SuperJLU 已启动：%s", url)
    stopped = threading.Event()

    def shutdown():
        if not stopped.is_set():
            stopped.set()
            httpd.shutdown()
            httpd.server_close()
            clear_state(port)
            log.info("已退出")

    try:
        try:
            opener(url)
        except Exception:  # noqa: BLE001
            log.exception("打开浏览器失败")
        if not tray(url, shutdown, opener):
            log.info("无托盘（缺少 pystray/Pillow 或图标），前台运行；Ctrl+C 退出")
            if sys.stdout is not None:
                print(f"{APP_NAME} 已启动：{url}  （Ctrl+C 退出）", flush=True)
            (wait or (lambda: stopped.wait()))()
    except KeyboardInterrupt:
        pass
    except Exception:  # noqa: BLE001
        log.exception("启动器异常")
        raise
    finally:
        shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
