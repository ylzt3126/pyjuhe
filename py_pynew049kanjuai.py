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
  * 不再内置任何"抓包 cookie"：init 阶段直接调登录接口，靠响应的 Set-Cookie
    建立会话，把 cookie 收进实例 CookieJar 并缓存在进程内 _SESSION，后续所有
    请求复用；同一进程内多实例共享，换实例不重新登录；
  * 播放链路识别到 401 / login_required / *_session_required 时，
    自动调用登录接口 → 记录新 cookie → 用新 cookie 重试同一请求（只重试一次，避免死循环）；
  * cookie 每次变化都记录在 self._ck_hist 里（内存留痕，不写外部文件）；
  * 分类与筛选来自 /v1/runtime/bootstrap 的 catalog_ui（真实数据，5 分钟缓存），
    站点取不到时退回本文件内置的分类表，保证筛选条永远有内容；
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

# 说明：本文件不再预置抓包 cookie。
# 会话统一由 init() 调 POST /v1/users/password/login 建立：
#   登录响应头 Set-Cookie: ai_movie_session=ums_xxx; ai_movie_browser=brw_xxx
# 由 _absorb() 收进 self._jar，并写入进程内 _SESSION，后续请求复用、换实例不重登。
# 鉴权只认 ai_movie_session；ai_movie_browser 只是设备指纹，随登录响应一起拿到。

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


# --------------------------------------------------------------------------- #
# 筛选（filters）工具
# --------------------------------------------------------------------------- #

def _text(v, default=""):
    if v is None:
        return default
    s = str(v).strip()
    return s if s else default


def _first(*vals):
    for v in vals:
        s = _text(v)
        if s:
            return s
    return ""


def _as_dict(v):
    return v if isinstance(v, dict) else {}


def _as_list(v):
    return v if isinstance(v, list) else []


def _encode_filter(params, key):
    """筛选项 -> TVBox 的 v 值；多参数项编码成 query 串，由 categoryContent 还原。"""
    items = [(_text(k), _text(v)) for k, v in _as_dict(params).items()]
    items = [(k, v) for k, v in items if k and v]
    if not items:
        return ""
    if len(items) == 1 and items[0][0] == key:
        return items[0][1]
    return urllib.parse.urlencode(items)


def _clone_filter_rows(rows):
    """深拷贝筛选行，保证每个分类各持一份独立对象（不共享 list/引用）。"""
    out, seen_keys = [], set()
    for raw_row in _as_list(rows):
        row = _as_dict(raw_row)
        key = _text(row.get("key"))
        if not key or key in seen_keys:
            continue
        values, names = [], set()
        for raw_opt in _as_list(row.get("value")):
            opt = _as_dict(raw_opt)
            label = _text(opt.get("n"))
            if not label or label in names:
                continue
            names.add(label)
            values.append({"n": label, "v": _text(opt.get("v"))})
        if len(values) < 2:
            continue
        seen_keys.add(key)
        out.append({"key": key,
                    "name": _ROW_LABELS.get(key) or _first(row.get("name"), key),
                    "value": values})
    return out


# 筛选行的显示名：站点给 genre 行下发的是"分类"，部分播放端的筛选面板对这一行
# 不渲染行名（会留空），统一换成通用叫法"类型"，保证行名必定显示。
_ROW_LABELS = {
    "genre": "类型",
    "area": "地区",
    "language": "语言",
    "year": "年份",
    "sort": "排序",
    "status": "状态",
    "orientation": "形式",
}

# 允许透传给 /v1/browse/catalog 的筛选参数（其余 extend 键一律丢弃）
_FILTER_KEYS = (
    "genre", "area", "language", "year", "year_gte", "year_lte",
    "sort", "status", "orientation", "q",
)


def _parse_extend(extend):
    """TVBox 的 extend 可能是 dict / JSON 串 / query 串，统一成只含白名单键的 dict"""
    ex = {}
    if isinstance(extend, dict):
        ex = extend
    elif isinstance(extend, str) and extend.strip():
        s = extend.strip()
        try:
            parsed = json.loads(s)
            ex = parsed if isinstance(parsed, dict) else {}
        except Exception:
            try:
                ex = dict(urllib.parse.parse_qsl(s))
            except Exception:
                ex = {}
    out = {}
    for k in _FILTER_KEYS:
        v = _text(ex.get(k))
        if not v:
            continue
        if "=" in v:
            # 复合项（如 "2010_2017" 被编码成 "year_gte=2010&year_lte=2017"）：
            # 拆成多个真实查询参数，别把整个串塞进一个参数里
            pair = dict(urllib.parse.parse_qsl(v))
            if pair:
                for pk, pv in pair.items():
                    if pk in _FILTER_KEYS and pv:
                        out[pk] = pv
                continue
        out[k] = v
    return out


def _fallback_filters(kind):
    """站点不可用时的兜底筛选：类型/地区/语言/年份/排序（选项来自站点真实枚举）。"""
    return [
        {"key": "genre", "name": _ROW_LABELS["genre"], "value":
            [{"n": "全部", "v": ""}] + [{"n": g, "v": g} for g in GENRES.get(kind, [])]},
        {"key": "area", "name": _ROW_LABELS["area"], "value": [
            {"n": "全部", "v": ""}, {"n": "大陆", "v": "中国大陆"},
            {"n": "香港", "v": "中国香港"}, {"n": "台湾", "v": "中国台湾"},
            {"n": "日本", "v": "日本"}, {"n": "韩国", "v": "韩国"}, {"n": "欧美", "v": "欧美"},
        ]},
        {"key": "language", "name": _ROW_LABELS["language"], "value": [
            {"n": "全部", "v": ""}, {"n": "国语", "v": "国语"}, {"n": "粤语", "v": "粤语"},
            {"n": "英语", "v": "英语"}, {"n": "日语", "v": "日语"}, {"n": "韩语", "v": "韩语"},
        ]},
        {"key": "year", "name": _ROW_LABELS["year"], "value": [
            {"n": "全部", "v": ""}, {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
            {"n": "2024", "v": "2024"}, {"n": "更早", "v": "year_lte=2023"},
        ]},
        {"key": "sort", "name": _ROW_LABELS["sort"], "value": [
            {"n": "全部", "v": ""}, {"n": "最新", "v": "updated_desc"}, {"n": "最热", "v": "heat_desc"},
        ]},
    ]


class Spider(Spider):
    def init(self, extend=""):
        # ---- CookieJar：只放"登录接口真实下发"的会话，不再有预置 cookie ----
        self._jar = {}
        for k, v in (_SESSION.get("jar") or {}).items():
            self._jar[k] = v
        self._ck_hist = []          # 记录用：cookie 每次变化留痕
        self._login_t = _SESSION.get("t", 0)
        self._user = _SESSION.get("user", {}) or {}
        self._valid = bool(_SESSION.get("valid"))   # 会话是否已被服务端验证过
        self._record("init")

        # 1) 先选定可用域名（这一步不需要会话）
        self._hi = 0
        for i, h in enumerate(HOSTS):
            try:
                r = self.fetch(h, headers={"User-Agent": UA}, timeout=15000)
                if getattr(r, 'status_code', None) == 200:
                    self._hi = i
                    break
            except Exception:
                continue

        # 2) 用登录接口初始化会话：Set-Cookie 由 _absorb 收进 _jar，
        #    并写进 _SESSION，后续所有请求（含播放）复用同一份 cookie。
        self._login()
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

    # ------------------------------------------------------------------ #
    # 分类 / 筛选（数据来自 /v1/runtime/bootstrap 的 catalog_ui）
    # ------------------------------------------------------------------ #
    def _bootstrap(self):
        """运行时配置（含 catalog_ui 分类与筛选枚举），5 分钟缓存，取不到返回 {}"""
        now = time.time()
        if getattr(self, "_bs_t", 0) > now - 300 and isinstance(getattr(self, "_bs", None), dict):
            return self._bs
        code, j = self._api("GET", "/v1/runtime/bootstrap")
        if code not in (200, 201) or not isinstance(j, dict):
            j = {}
        self._bs, self._bs_t = j, now
        return j

    def _class_list(self):
        """分类标签：沿用本文件原有的 CATEGORIES + GENRES（subs 二级分类）方式"""
        return [{"type_id": k, "type_name": v,
                 "subs": [{"type_id": "%s:%s" % (k, g), "type_name": g}
                          for g in GENRES.get(k, [])]}
                for k, v in CATEGORIES.items()]

    def _filters_from(self, boot):
        """把站点的 catalog_ui.catalogs 转成 TVBox filters（真实枚举，非硬编码）"""
        ui = _as_dict(_as_dict(boot).get("catalog_ui"))
        out = {}
        for raw in _as_list(ui.get("catalogs")):
            cat = _as_dict(raw)
            kind = _text(cat.get("kind"))
            if not kind:
                continue
            rows = []
            for raw_row in _as_list(cat.get("rows")):
                row = _as_dict(raw_row)
                key = _text(row.get("key"))
                if not key:
                    continue
                values = [{"n": "全部", "v": ""}]
                for raw_opt in _as_list(row.get("options")):
                    opt = _as_dict(raw_opt)
                    if opt.get("enabled") is False:
                        continue
                    value = _encode_filter(opt.get("params"), key)
                    if not value:
                        # 站点没给参数（短剧的 status / orientation 就是这样），
                        # 这一行选了也不生效，直接不输出，避免出现"点了没反应"的空行
                        continue
                    label = _first(opt.get("label"), opt.get("key"), value)
                    if any(x["n"] == label for x in values):
                        continue
                    values.append({"n": label, "v": value})
                if len(values) > 1:
                    rows.append({"key": key,
                                 "name": _first(row.get("label"), key),
                                 "value": values})
            if rows:
                out[kind] = rows
        return out

    def _fill_filters(self, filters, categories):
        """给 class 里的每个 type_id 各配一份独立筛选。

        站点下发了对应 kind 的 catalog 就用它的真实枚举；没下发的（如 documentary）
        用该分类自己的真实枚举兜底，保证筛选条永远有内容、且各分类互不共享对象。
        """
        source = _as_dict(filters)
        tids = [_text(_as_dict(x).get("type_id")) for x in _as_list(categories)]
        tids = [t for t in tids if t]
        out = {}
        for tid in tids:
            if ":" in tid:          # subs 二级分类没有筛选，跳过
                continue
            cloned = _clone_filter_rows(source.get(tid)) \
                or _clone_filter_rows(_fallback_filters(tid))
            if cloned:
                out[tid] = cloned
        if not out:                 # 理论上不会发生，兜一层
            for tid, rows in source.items():
                tid = _text(tid)
                cloned = _clone_filter_rows(rows)
                if tid and cloned:
                    out[tid] = cloned
        return out

    def homeContent(self, filter=False):
        classes = self._class_list()
        filters = {}
        try:
            filters = self._fill_filters(self._filters_from(self._bootstrap()), classes)
        except Exception:
            filters = {}
        if not filters:
            filters = self._fill_filters({}, classes)
        return {"class": classes, "filters": filters, "list": []}

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
        if ":" in cat:                      # 兼容 "movie:喜剧" 这种旧式子分类
            cat, gen = cat.split(":", 1)
        ex = _parse_extend(extend)
        if gen and not ex.get("genre"):
            ex["genre"] = gen

        params = []
        if cat == "updates":                # 站点伪分类：全站最近更新
            params.append("sort=" + ex.pop("sort", "updated_desc"))
        elif cat == "recommend":            # 站点伪分类：AI找剧 / 热门
            params.append("sort=" + ex.pop("sort", "trending"))
            params.append("window=day")
        else:
            if cat not in CATEGORIES:
                cat = "movie"
            params.append("kind=" + cat)
        for k in _FILTER_KEYS:
            v = ex.get(k)
            if v:
                params.append("%s=%s" % (k, urllib.parse.quote(v)))
        params.append("page=%d" % pn)
        params.append("limit=40")

        j = self._req("GET", "/v1/browse/catalog?" + "&".join(params))
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
