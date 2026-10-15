"""校园服务单点跳转：用已保存的 CAS cookie（站点 'cas'）向 CAS 换一张 ST，把浏览器 302 到带 ticket 的目标地址。

只读、只用已保存的 cookie，不提交密码/验证码；ticket 与 cookie 只出现在 302 Location 里，不进任何 JSON。
"""
from __future__ import annotations

import urllib.parse
from typing import Optional, Tuple

from . import jwxt, sessions

# site id → (CAS service, 直接打开时的目标 URL)
# ilearn：ilearntec → ilearn.jlu.edu.cn/cas-server → cas.jlu.edu.cn/tpass/login?service=jwcidentity…（2026-10-15 只读探测）
SSO_TARGETS = {
    "jwxt": (jwxt.CAS_SERVICE, jwxt.IEDU_PORTAL),
    "ilearn": ("https://jwcidentity.jlu.edu.cn/iplat-pass-jlu/thirdLogin/jlu/login",
               "https://ilearntec.jlu.edu.cn/coursecenter/main/index"),
}


def _jlu_https(url: str) -> bool:
    u = urllib.parse.urlparse(url or "")
    h = (u.hostname or "").lower()
    return u.scheme == "https" and (h == "jlu.edu.cn" or h.endswith(".jlu.edu.cn"))


def capable(site_id: str) -> bool:
    return site_id in SSO_TARGETS


def with_flag(url: str, flag: str = "sso=failed") -> str:
    return url + ("&" if "?" in url else "?") + flag


def jump_url(site_id: str, c: Optional[jwxt.JwxtClient] = None) -> Tuple[str, bool]:
    """返回 (浏览器应跳去的 URL, 是否拿到了 ticket)。失败时退回普通目标 URL 并带 ?sso=failed。"""
    service, fallback = SSO_TARGETS[site_id]
    fail = with_flag(fallback)
    try:
        sessions.migrate_cas_from_jwxt()
    except Exception:
        pass
    cas = sessions.load_cookies("cas")
    if not cas:
        return fail, False
    c = c or jwxt.JwxtClient()
    c.jar = jwxt.jar_from_list(cas)
    c._rebuild_opener()
    url = f"{jwxt.CAS_LOGIN}?service={urllib.parse.quote(service, safe='')}"
    try:
        code, html, final, _ = c._request(url, headers={"Accept": "text/html,*/*"}, allow_redirects=False)
    except jwxt.NetworkError:
        return fail, False
    q = urllib.parse.parse_qs(urllib.parse.urlparse(final or "").query)
    ticket = (q.get("ticket") or [""])[0]
    if code in (301, 302, 303, 307) and ticket.startswith("ST-") and _jlu_https(final) \
            and (urllib.parse.urlparse(final).hostname or "").lower() != sessions.SITES["cas"].host:
        try:
            sessions.save_cas_from_jar(c.jar)  # CAS 可能续发了 TGC
        except Exception:
            pass
        return final, True
    if jwxt.looks_like_login_redirect(final, html) or code == 200:
        try:
            sessions.mark_expired("cas", "统一认证登录已失效")
        except Exception:
            pass
    return fail, False
