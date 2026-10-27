# -*- coding: utf-8 -*-
"""
看剧 AI（kanju.ai）资源站 Spider —— 播放登录版

一、为什么要登录（实测结论，2026-10 复现）
  1) 首页 / 搜索 / 详情接口不带 cookie 也能正常返回；
  2) 第 1 集是片头/预告线路，未登录也能播；
  3) 从第 2 集起，POST /v1/playback/resolve-line 未登录时返回：
         HTTP 401
         {"statusCode":401,"code":"playback_user_session_required",
          "state":"login_required","message":"该内容需要登录后播放"}
     此时 line 为空，拿不到任何播放地址；
  4) 带上含 ai_movie_session 的 cookie 后，同样请求返回 201 + 真实播放地址。

二、登录接口（账号密码）
     POST /v1/users/password/login
     body: {"account": "<账号>", "password": "<密码>"}
     -> 201 {"object":"user.session","user":{...},"expires_at":"...","identity":{...}}
     -> Set-Cookie: ai_movie_session=ums_xxx; Max-Age=31535999; Domain=kanju.ai
  备用（密码登录失败时）：POST /v1/users/anonymous  {}  -> 201，游客会话

三、本文件的处理策略
  * 所有请求头统一携带 cookie：预置 COOKIE + 运行时 CookieJar，自动吸收响应 Set-Cookie；
  * 播放链路识别到 401 / login_required / *_session_required 时，
    自动调用登录接口 → 记录新 cookie → 用新 cookie 重试同一请求（只重试一次，避免死循环）；
  * cookie 每次变化都记录在 self._ck_hist 里（内存留痕，不写外部文件）；
  * 线路解析跳过"占位图"线路（官方接口存在 url_kind=mp4 但地址是 .jpg 的情况）。

所有逻辑均在本文件内实现，不引用任何外部文件。
"""

import sys
import json
import time
import hmac
import hashlib
import secrets
import urllib.parse
import urllib.request
import urllib.error
import math

sys.path.append('..')
try:
    from base.spider import Spider
except ImportError:
    class Spider:
        def fetch(self, url, headers=None, **kw):
            import requests as rq
            kw.pop('timeout', None)
            r = rq.get(url, headers=headers, timeout=15, **kw)
            r.encoding = 'utf-8'
            return r

try:
    import requests as _rq
except Exception:
    _rq = None

HOSTS = ["https://kanju.ai", "https://kanju20.com"]
KEY = "557d0e4ae929f438da6bd84412374e6086b8af09b3fed54bf22601d5bf8c54a0"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
CLIENT = {
    "x-ai-movie-client-name": "dianyingtiantang-frontend",
    "x-ai-movie-client-version": "1.0.0",
    "x-ai-movie-build-version": "dianyingtiantang-v2026.08.11.1-8cbb0b0d407e-672e67d62528",
    "x-ai-movie-protocol-version": "2026-07-05.library-v2.playback-v1",
}

# 预置 cookie（抓包所得，含登录态 ai_movie_session）。
# 仅作为 CookieJar 的初始值；登录成功后会被新会话覆盖并记录，不需要手工维护。
#
# 这 7 个 cookie 的真实来源（逐个抓包确认过，值都不来自登录响应体，而是 Set-Cookie 响应头
# 或前端 JS 的 document.cookie）：
#   ai_movie_session                  ← 登录接口的 Set-Cookie 响应头（HttpOnly，约 365 天）
#                                       POST /v1/users/password/login 或 /v1/users/anonymous
#                                       响应体只有 user/expires_at，会话票据只在这里出现
#   ai_movie_browser=brw_xxx          ← 同一登录响应的 Set-Cookie（约 400 天，设备指纹）
#   ai_movie_route_visitor=<32位hex>   ← 首次打开任意 HTML 页面时由前端网关(openresty)下发，30 天
#   ai_movie_route_seen=1             ← 同上，1 天
#   ai_movie_route_stay=1             ← 同上，1 天
#   ai_movie_route_target=<域名>       ← 前端 route-transfer.js 写入，记录从哪个镜像域名(如 kanju48.com)跳转进来，30 天
#   ai_movie_home_address_visited_v1  ← 前端 HomeAddressReminder 组件 document.cookie 写入的"已看过首页地址提醒"标记，365 天
#
# 实测权限只认 ai_movie_session：
#   只带 ai_movie_session            -> resolve-line 201，拿到播放地址
#   只带其余 6 个（无 session）        -> 401 playback_user_session_required
#   完全不带 cookie                  -> 401 playback_user_session_required
# 其余 6 个只为"看起来像真实浏览器"，不参与鉴权。
COOKIE = (
    "ai_movie_home_address_visited_v1=1; "
    "ai_movie_browser=brw_-syhLxLb2T4Q6Yxb7oLl0VZyaSNcO-kl2sM0O-47Q3k; "
    "ai_movie_route_visitor=52f0efcb699c8d5021824e0efbbc2df0; "
    "ai_movie_route_seen=1; "
    "ai_movie_route_target=kanju48.com; "
    "ai_movie_route_stay=1; "
    "ai_movie_session=ums_NtLodi5Nn-wSgZuZnLQYtxb-ZnVx1ROjyqjyzEO76Rg"
)

# ---- 登录相关 ----
ACCOUNT = "kanju3126"                       # 账号
PASSWORD = "kanju3126"                      # 密码
LOGIN_PATH = "/v1/users/password/login"     # 密码登录接口
GUEST_PATH = "/v1/users/anonymous"          # 游客登录（备用）
SESSION_KEY = "ai_movie_session"            # 会话 cookie 名
LOGIN_FLAGS = ("login_required", "session_required", "需要登录", "请先登录")
LOGIN_TTL = 3600                            # 登录成功后 1 小时内不重复登录
# 同一进程内跨实例共享会话，避免每次重建实例都重新登录
_SESSION = {}

CATEGORIES = {
    "movie": "电影", "series": "电视剧", "short_drama": "短剧",
    "anime": "动漫", "variety": "综艺", "documentary": "纪录片",
}

GENRES = {
    "movie": ["动作", "冒险", "剧情", "喜剧", "奇幻", "古装", "家庭", "科幻"],
    "series": ["动作", "冒险", "剧情", "刑侦", "古装", "历史", "台剧", "悬疑"],
    "short_drama": ["剧情", "动作", "反转爽剧", "古装仙侠", "喜剧", "女频恋爱", "家庭", "年代"],
    "anime": ["热血", "冒险", "奇幻", "日本动漫", "国产动漫", "爆笑", "武侠", "儿童"],
    "variety": ["大陆综艺", "真人秀", "情感", "爱情", "社交观察"],
    "documentary": ["历史", "纪录片"],
}

IMG_KIND = ("image", "img", "jpg", "jpeg", "png", "poster", "cover", "screenshot")


def _ck_parse(s):
    """'a=1; b=2' -> {'a':'1','b':'2'}（保序、丢弃空值）"""
    d = {}
    for part in str(s or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and v:
            d[k] = v
    return d


def _ck_join(d):
    return "; ".join("%s=%s" % (k, v) for k, v in (d or {}).items() if v)


def _is_img(url, line=None):
    """
    只依据接口显式声明的 url_kind 判断图片线路。
    注意：byteimg/heycan 的直链路径形如 xxx.jpg~tplv-xxx-image.image，
    但返回体其实是 MP4（实测 206 + ftypisom），所以绝不能用扩展名或
    token_scenario（第 1 集全部线路都会带 series_intro_zero）来过滤。
    """
    line = line or {}
    kind = str(line.get("url_kind") or line.get("kind") or "").lower()
    return kind in IMG_KIND


class Spider(Spider):
    def init(self, extend=""):
        # ---- CookieJar：预置 cookie + 跨实例缓存 + 响应 Set-Cookie ----
        self._jar = _ck_parse(COOKIE)
        for k, v in (_SESSION.get("jar") or {}).items():
            self._jar[k] = v
        self._ck_hist = []          # 记录用：cookie 每次变化留痕
        self._login_t = _SESSION.get("t", 0)
        self._user = _SESSION.get("user", {}) or {}
        self._valid = bool(_SESSION.get("valid"))   # 会话是否已被服务端验证过
        self._record("seed")

        for i, h in enumerate(HOSTS):
            try:
                r = self.fetch(h, headers={"User-Agent": UA, "Cookie": self._cookie_header()}, timeout=15000)
                if getattr(r, 'status_code', None) == 200:
                    self._hi = i
                    break
            except Exception:
                continue
        else:
            self._hi = 0

        # 没有会话就登录（优先账号密码）
        if not self._has_session():
            self._login(force=True)
        return ""

    # ------------------------------------------------------------------ #
    # cookie 管理
    # ------------------------------------------------------------------ #
    def _has_session(self):
        return bool((self._jar or {}).get(SESSION_KEY))

    def _cookie_header(self):
        return _ck_join(self._jar)

    def _record(self, why):
        """记录 cookie 值（内存留痕，便于排查；不落任何外部文件）"""
        ck = self._cookie_header()
        hist = getattr(self, "_ck_hist", None)
        if not isinstance(hist, list):
            hist = []
            self._ck_hist = hist
        hist.append({
            "t": int(time.time()),
            "why": why,
            "session": (self._jar or {}).get(SESSION_KEY, ""),
            "cookie": ck,
        })
        if len(hist) > 20:
            del hist[:-20]
        _SESSION["jar"] = dict(self._jar)
        _SESSION["t"] = getattr(self, "_login_t", 0)
        _SESSION["user"] = getattr(self, "_user", {}) or {}
        return ck

    def cookieDump(self):
        """调试用：查看当前 cookie 与变化记录"""
        return {"current": self._cookie_header(), "history": list(self._ck_hist), "user": self._user}

    def _absorb(self, headers):
        """吸收响应 Set-Cookie（兼容 requests 的 CaseInsensitiveDict 与 (k,v) 列表）"""
        if not headers:
            return False
        try:
            items = list(headers.items() if hasattr(headers, "items") else headers)
        except Exception:
            return False
        changed = False
        for k, v in items:
            if str(k).lower() != "set-cookie":
                continue
            ck = str(v or "")
            kv = ck.split(";")[0]
            if "=" not in kv:
                continue
            name, val = kv.split("=", 1)
            name, val = name.strip(), val.strip()
            if not name:
                continue
            low = ck.lower()
            dead = (not val) or ("max-age=0" in low) or ("1970" in low and "expires" in low)
            if dead:
                if self._jar.pop(name, None) is not None:
                    changed = True
            elif self._jar.get(name) != val:
                self._jar[name] = val
                changed = True
        if changed:
            self._record("set-cookie")
        return changed

    # ------------------------------------------------------------------ #
    # 基础请求
    # ------------------------------------------------------------------ #
    def _host(self):
        return HOSTS[getattr(self, '_hi', 0)]

    def _sign(self, method, path):
        ts = str(int(time.time() * 1000))
        nonce = secrets.token_hex(16)
        msg = "%s\n%s\n%s\n%s" % (method, path, ts, nonce)
        sig = hmac.new(KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()
        return ts, nonce, sig

    def _http(self, method, url, headers, body=None, timeout=10):
        """返回 (code, text, headers)；requests 缺失时回退 urllib"""
        if _rq is not None:
            if body is not None:
                r = _rq.post(url, json=body, headers=headers, timeout=timeout)
            else:
                r = _rq.get(url, headers=headers, timeout=timeout)
            return r.status_code, (r.text or ""), r.headers
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        rq = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(rq, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "ignore"), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "ignore"), e.headers

    def _raw(self, method, path, body=None, host=None, cookie=None, timeout=10):
        """基础请求。cookie=None 用 CookieJar，cookie='' 表示不带 cookie。"""
        host = host or self._host()
        ts, nonce, sig = self._sign(method, path)
        hdrs = {
            "User-Agent": UA, "Accept": "application/json",
            "Referer": host + "/", "Origin": host,
            "x-ai-movie-timestamp": ts, "x-ai-movie-nonce": nonce,
            "x-ai-movie-signature": sig,
        }
        hdrs.update(CLIENT)
        ck = self._cookie_header() if cookie is None else cookie
        if ck:
            hdrs["Cookie"] = ck
        if body is not None:
            hdrs["Content-Type"] = "application/json"
        code, text, h = self._http(method, host + path, hdrs, body, timeout)
        self._absorb(h)
        return code, text, h

    def _login_required(self, code, text):
        """根据返回结果判断是否需要登录（不登录播放会返回 401 + state=login_required）"""
        s = str(text or "").lower()
        if "playback_user_session_required" in s or "user_session_required" in s:
            return True
        for f in LOGIN_FLAGS:
            if f in s:
                return True
        return code in (401, 419)

    # ------------------------------------------------------------------ #
    # 登录
    # ------------------------------------------------------------------ #
    def _login(self, force=False):
        """账号密码登录；失败则退回游客登录。成功返回 True。"""
        now = time.time()
        # 只有"本进程内刚登录成功且会话已生效"才跳过，预置 cookie 一律视为未验证
        if (not force) and self._valid and self._has_session() \
                and (now - getattr(self, "_login_t", 0)) < LOGIN_TTL:
            return True

        ok = False
        # 1) 账号密码登录（不需要旧会话）
        if ACCOUNT and PASSWORD:
            try:
                code, txt, _ = self._raw("POST", LOGIN_PATH,
                                         {"account": ACCOUNT, "password": PASSWORD},
                                         cookie="", timeout=12)
            except Exception:
                code, txt = 0, ""
            if code in (200, 201):
                try:
                    j = json.loads(txt)
                except Exception:
                    j = {}
                if isinstance(j, dict):
                    self._user = {
                        "user_id": ((j.get("user") or {}).get("id") or ""),
                        "display_name": ((j.get("user") or {}).get("display_name") or ""),
                        "expires_at": j.get("expires_at") or "",
                        "identity": j.get("identity") or {},
                    }
                ok = self._has_session()
                if ok:
                    self._record("password-login")

        # 2) 游客登录兜底
        if not ok:
            try:
                code, txt, _ = self._raw("POST", GUEST_PATH, {}, cookie="", timeout=12)
            except Exception:
                code, txt = 0, ""
            if code in (200, 201):
                try:
                    j = json.loads(txt)
                except Exception:
                    j = {}
                if not self._has_session() and isinstance(j, dict):
                    for k in ("session", "session_id", "token"):
                        v = j.get(k) or ""
                        if v:
                            self._jar[SESSION_KEY] = v
                            break
                ok = self._has_session()
                if ok:
                    self._user = {"user_id": ((j.get("user") or {}).get("id") or ""),
                                  "display_name": ((j.get("user") or {}).get("display_name") or ""),
                                  "identity": j.get("identity") or {"provider": "anonymous"}}
                    self._record("guest-login")

        if ok:
            self._login_t = now
            self._valid = True
            _SESSION["t"] = now
            _SESSION["valid"] = True
            _SESSION["jar"] = dict(self._jar)
            _SESSION["user"] = self._user or {}
        return ok

    def _api(self, method, path, body=None, retry=True):
        """带登录自愈的 API 调用，返回 (code, dict)"""
        host = self._host()
        try:
            code, txt, _ = self._raw(method, path, body, host=host)
        except Exception:
            return 0, {}
        if self._login_required(code, txt) and retry:
            # 被拒说明当前会话不可信：先置无效，避免 _login 里的 TTL 短路
            self._valid = False
            _SESSION["valid"] = False
            if self._login():
                try:
                    code, txt, _ = self._raw(method, path, body, host=host)
                except Exception:
                    return 0, {}
                if self._login_required(code, txt):
                    self._valid = False      # 登录后仍拒，下次继续重新登录
        if code in (200, 201):
            self._valid = True
            _SESSION["valid"] = True
        try:
            j = json.loads(txt) if txt else {}
        except Exception:
            j = {}
        return code, (j if isinstance(j, dict) else {})

    def _req(self, method, path, body=None):
        """兼容旧行为：轮换 host，只返回 dict"""
        for _ in range(len(HOSTS)):
            code, j = self._api(method, path, body)
            if code in (200, 201) and j:
                return j
            if code in (401, 419):
                break       # 已尝试登录仍失败，别再切域名空转
            self._hi = (self._hi + 1) % len(HOSTS)
            time.sleep(1)
        return {}

    # ------------------------------------------------------------------ #
    # 数据映射
    # ------------------------------------------------------------------ #
    def _vod(self, c):
        return {
            "vod_id": c.get("id", ""),
            "vod_name": c.get("title", ""),
            "vod_pic": c.get("poster_url", ""),
            "vod_remarks": c.get("remarks") or str(c.get("year") or ""),
            "vod_year": c.get("year") or "",
            "vod_area": c.get("area") or "",
            "vod_actor": "/".join((c.get("actors") or [])[:3]),
            "vod_director": "/".join((c.get("directors") or [])[:2]),
        }

    def homeContent(self, filter=False):
        cls = []
        for k, v in CATEGORIES.items():
            cls.append({
                "type_id": k, "type_name": v,
                "subs": [{"type_id": "%s:%s" % (k, g), "type_name": g} for g in GENRES[k]],
            })
        return {"class": cls, "list": []}

    def homeVideoContent(self):
        now = time.time()
        if getattr(self, "_hv_t", 0) > now - 300 and getattr(self, "_hv", None):
            return self._hv
        seen, lst = set(), []
        paths = ["/v1/feed/home", "/v1/browse/catalog?kind=movie&sort=trending&window=day&page=1&limit=40", "/v1/browse/catalog?kind=series&sort=trending&window=day&page=1&limit=40"]
        for p in paths:
            j = self._req("GET", p)
            for s in (j.get("sections") or []):
                for c in (s.get("cards") or []):
                    v = self._vod(c)
                    if v["vod_id"] and v["vod_id"] not in seen:
                        seen.add(v["vod_id"])
                        lst.append(v)
            for c in (j.get("cards") or []):
                v = self._vod(c)
                if v["vod_id"] and v["vod_id"] not in seen:
                    seen.add(v["vod_id"])
                    lst.append(v)
        self._hv, self._hv_t = {"list": lst}, now
        return self._hv

    def categoryContent(self, tid, pg=1, filter=False, extend=""):
        pn = 1
        try:
            pn = max(int(str(pg)), 1)
        except Exception:
            pass
        cat, gen = str(tid), ""
        if ":" in cat:
            cat, gen = cat.split(":", 1)
        if cat not in CATEGORIES:
            cat = "movie"
        if gen:
            path = "/v1/browse/catalog?kind=%s&genre=%s&page=%d&limit=40" % (cat, urllib.parse.quote(gen), pn)
        else:
            path = "/v1/browse/catalog?kind=%s&page=%d&limit=40" % (cat, pn)
        j = self._req("GET", path)
        cards = j.get("cards") or []
        total = (j.get("pagination") or {}).get("total", 0) or 0
        return {"page": pn, "pagecount": max(math.ceil(total / 40), 1), "limit": 40, "total": total, "list": [self._vod(c) for c in cards]}

    def detailContent(self, ids):
        if isinstance(ids, list):
            vid = ids[0] if ids else ""
        else:
            vid = str(ids) if ids else ""
        vid = vid.split("/")[0]
        if not vid:
            return {"list": []}
        d = self._req("GET", "/v1/catalog/%s" % vid)
        eps = d.get("episodes") or []
        urls = []
        for e in eps:
            t = e.get("token")
            if t:
                urls.append("%s$%s" % (e.get("title") or "第%s集" % (e.get("number") or len(urls) + 1), t))
        play_from, play_url = "kanju", "#".join(urls)
        if eps and urls:
            tok0 = eps[0].get("token")
            if tok0:
                _, rj = self._api("GET", "/v1/playback/resolve/%s" % tok0)
                names, seen = [], set()
                for l in (rj.get("line_options") or []):
                    n = (l.get("provider_name") or "").strip()
                    if n and n not in seen:
                        seen.add(n)
                        names.append(n)
                names = names[:8]
                if names:
                    play_from = "$$$".join(names)
                    play_url = "$$$".join([play_url] * len(names))
        return {"list": [{
            "vod_id": vid, "vod_name": d.get("title", ""), "vod_pic": d.get("poster_url", ""),
            "vod_year": d.get("year", ""), "vod_area": d.get("area", ""),
            "vod_class": "/".join((d.get("genres") or [])[:3]),
            "vod_director": "/".join((d.get("directors") or [])[:2]),
            "vod_actor": "/".join((d.get("actors") or [])[:3]),
            "vod_content": d.get("description") or "",
            "vod_remarks": d.get("status") or d.get("remarks") or "",
            "vod_play_from": play_from, "vod_play_url": play_url,
        }]}

    def searchContent(self, key, quick=False, pg="1"):
        k = str(key or "").strip()
        ck = "q_" + k
        now = time.time()
        if getattr(self, "_sc_t", {}).get(ck, 0) > now - 300 and ck in (getattr(self, "_sc", {}) or {}):
            return self._sc[ck]
        j = self._req("GET", "/v1/browse/catalog?q=%s&page=1&limit=20" % urllib.parse.quote(k))
        r = {"list": [self._vod(c) for c in (j.get("cards") or [])]}
        if not hasattr(self, "_sc"):
            self._sc = {}
        if not hasattr(self, "_sc_t"):
            self._sc_t = {}
        self._sc[ck], self._sc_t[ck] = r, now
        return r

    # ------------------------------------------------------------------ #
    # 播放
    # ------------------------------------------------------------------ #
    def _ticket(self, lo):
        t = str(lo.get("url") or lo.get("ticket") or "").strip()
        if t.startswith("resolve://"):
            t = t[10:]
        return t

    def _rank(self, lines, wanted):
        """
        线路排序（剔除占位图）：
          flag 命中优先 → selected → default_priority → 其余
        """
        picked, rest = [], []
        for lo in (lines or []):
            if not self._ticket(lo):
                continue
            name = (lo.get("provider_name") or "").strip()
            if wanted and wanted not in ("kanju", "") and name == wanted:
                picked.append(lo)
            else:
                rest.append(lo)
        rest.sort(key=lambda x: (
            0 if x.get("selected") else 1,
            0 if x.get("default_priority") else 1,
            0 if str(x.get("url_kind") or "").lower() in ("mp4", "m3u8") else 1,
        ))
        return picked + rest

    def playerContent(self, flag, id, vipFlags=None, vipIds=None):
        tok = str(id or "").strip()
        if not tok:
            return {"url": ""}
        wanted = str(flag or "").strip()

        code, rj = self._api("GET", "/v1/playback/resolve/%s" % tok)
        lines = rj.get("line_options") or []

        # 少见情况：resolve 直接给地址
        direct = str(rj.get("url") or "")
        if not lines and direct:
            return self._ret(direct)

        forced = False
        fallback = ""          # 探测不通但接口给了地址时，兜底返回
        for lo in self._rank(lines, wanted)[:8]:
            t = self._ticket(lo)
            c, line = self._api("POST", "/v1/playback/resolve-line", {"ticket": t})
            url = self._line_url(line)
            if not url and c in (401, 419) and not forced:
                # 会话被顶号/失效 → 强制重新登录一次再试
                forced = True
                if self._login(force=True):
                    c, line = self._api("POST", "/v1/playback/resolve-line", {"ticket": t})
                    url = self._line_url(line)
            if not url:
                continue
            meta = (line.get("line") or {}) if isinstance(line, dict) else {}
            if _is_img(url, meta):
                continue
            if not fallback:
                fallback = url
            ok = self._probe(url)
            if ok != 0:                     # 1=确认可播，-1=探测受限（SSL/超时）也放行
                return self._ret(url)
        if fallback:
            return self._ret(fallback)
        return {"url": ""}

    def _line_url(self, line):
        if not isinstance(line, dict):
            return ""
        return str((line.get("line") or {}).get("url") or "")

    def _probe(self, url):
        """
        探测直链是否真的能播：1 可播 / 0 明确不可播 / -1 无法判断
        只看内容魔数（ftyp / #EXTM3U），不看 Content-Type —— 官方 CDN 会把
        MP4 标成 image/jpeg，也会用 .jpg~tplv 之类的路径。
        """
        u = str(url or "")
        if not u.startswith("http"):
            return 0
        is_m3u8 = ".m3u8" in u.lower()
        hdrs = {"User-Agent": UA, "Referer": self._host() + "/", "Accept": "*/*"}
        if not is_m3u8:
            hdrs["Range"] = "bytes=0-2047"
        code, body = 0, b""
        try:
            if _rq is not None:
                r = _rq.get(u, headers=hdrs, timeout=7, stream=True)
                code = r.status_code
                try:
                    body = r.raw.read(4096) or b""
                except Exception:
                    body = b""
                try:
                    r.close()
                except Exception:
                    pass
            else:
                rq = urllib.request.Request(u, headers=hdrs)
                try:
                    with urllib.request.urlopen(rq, timeout=7) as r:
                        code = r.status
                        body = r.read(4096)
                except urllib.error.HTTPError as e:
                    code = e.code
        except Exception:
            return -1
        if code in (403, 404, 410, 451):
            return 0
        if code not in (200, 206):
            return -1
        if is_m3u8:
            return 1 if b"#EXTM3U" in body else 0
        if b"ftyp" in body or b"#EXTM3U" in body or b"ID3" in body[:32]:
            return 1
        return 0

    def _ret(self, url):
        u = str(url)
        hdrs = {"User-Agent": UA, "Referer": self._host() + "/"}
        low = u.lower().split("?")[0]
        if ".m3u8" in low:
            hdrs["Accept"] = "*/*"
            hdrs["Origin"] = self._host()
        return {"parse": 0, "url": u, "header": hdrs}

    def localProxy(self, param):
        return None
