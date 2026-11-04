# -*- coding: utf-8 -*-
"""
dongpian.ai (懂片帝AI) —— TVBox 采集模块（Python，仅依赖标准库）

约定
----
入口类 Spider，方法签名与 TVBox / catvod 的 Spider 接口一致：
    init(extend) / init(context, extend)
    getName()
    homeContent(filter)
    homeVideoContent()
    categoryContent(tid, pg, filter, extend)
    detailContent(ids)
    searchContent(key, quick, pg)
    playerContent(flag, id, vipFlags)

模块级同时导出同名函数（代理到默认 spider 实例），供不同 py 加载器调用。

返回结构
--------
homeContent      {"class": [...], "filters": {...}, "list": [...]}
homeVideoContent {"list": [...]}
categoryContent  {"list": [...], "page": int, "pagecount": int, "limit": int, "total": int}
detailContent    {"list": [{...}]}
searchContent    {"list": [...], "page": int, "pagecount": int, "limit": int, "total": int}
playerContent    {"parse": 0, "play": "", "url": "...", "header": {...}, ...}

所有公共方法均不抛出异常、不产生任何 stdout/stderr 输出；网络失败、异常响应、
字段缺失、空结果一律退化为结构完整的空结果。直接执行本文件时做一次静默自检，
以进程退出码表示结果（0 = 可用，1 = 不可用）。

站点接口需要 HMAC-SHA256 请求签名：
    message = METHOD + "\n" + pathname + search + "\n" + timestamp_ms + "\n" + nonce
    headers = x-ai-movie-timestamp / x-ai-movie-nonce / x-ai-movie-signature
分隔符为换行符而非空格，pathname+search 必须与最终请求的 URL 完全一致。

初始化与播放优选
----------------
1. 初始化阶段调用 /v1/users/password/login（账号与密码均使用 dongpian3126，
   可通过 init({"account": ..., "password": ...}) 覆盖），保存响应返回的
   Set-Cookie，并在之后所有接口请求头中统一携带该 Cookie；票据解析依赖此会话。
2. 线路优选：优先识别 url_kind == "resolve_ticket" 的视频源。存在这类源时，
   只保留并展示它们、过滤其余源，播放时通过
   POST /v1/playback/resolve-line?view=compact 二次解析换取真实可播地址；
   不存在票据源时，展示并使用其他视频源。
"""

import calendar
import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_tz
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "Spider", "spider", "init", "getName", "homeContent", "homeVideoContent",
    "categoryContent", "detailContent", "searchContent", "playerContent",
]

# --------------------------------------------------------------------------- #
# 站点常量（可通过 init(extend) 覆盖）
# --------------------------------------------------------------------------- #

_DEFAULT_HOST = "https://dongpian.ai"
_DEFAULT_SIGN_KEY = "8b9a908a05eac640e1ee06f52acaa741bfe4ba9e004eeffdbeb635e532e06666"
_DEFAULT_BUILD_VERSION = "dongpiandi-v2026.11.02.1-player-pause-gateway-464465fce4f1-web"
_CLIENT_NAME = "movie-search-frontend"
_CLIENT_VERSION = "1.0.0"
_PROTOCOL_VERSION = "2026-07-05.library-v2.playback-v1"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# 初始化登录（账号与密码一致；可通过 init(extend) 的 account/password 覆盖）
_DEFAULT_ACCOUNT = "dongpian3126"
_DEFAULT_PASSWORD = "dongpian3126"
_LOGIN_PATH = "/v1/users/password/login"
_RESOLVE_LINE_PATH = "/v1/playback/resolve-line?view=compact"
_LOGIN_RETRY_INTERVAL = 60.0

_KIND_LABELS = {
    "movie": "电影",
    "series": "电视剧",
    "anime": "动漫",
    "variety": "综艺",
    "short_drama": "短剧",
    "documentary": "纪录片",
    "sports": "体育",
}
_KIND_IDS = tuple(_KIND_LABELS.keys())

_FALLBACK_CATEGORIES = [
    {"type_id": "recommend", "type_name": "AI找剧"},
    {"type_id": "updates", "type_name": "最近更新"},
    {"type_id": "series", "type_name": "电视剧"},
    {"type_id": "movie", "type_name": "电影"},
    {"type_id": "anime", "type_name": "动漫"},
    {"type_id": "variety", "type_name": "综艺"},
    {"type_id": "short_drama", "type_name": "短剧"},
]

_FALLBACK_FILTERS = [
    {"key": "genre", "name": "分类", "value": [
        {"n": "全部", "v": ""},
        {"n": "喜剧", "v": "喜剧"}, {"n": "爱情", "v": "爱情"},
        {"n": "动作", "v": "动作"}, {"n": "科幻", "v": "科幻"},
        {"n": "悬疑", "v": "悬疑"}, {"n": "剧情", "v": "剧情"},
    ]},
    {"key": "area", "name": "地区", "value": [
        {"n": "全部", "v": ""},
        {"n": "大陆", "v": "中国大陆"}, {"n": "中国香港", "v": "中国香港"},
        {"n": "中国台湾", "v": "中国台湾"}, {"n": "日本", "v": "日本"},
        {"n": "韩国", "v": "韩国"}, {"n": "欧美", "v": "欧美"},
    ]},
    {"key": "year", "name": "年份", "value": [
        {"n": "全部", "v": ""},
        {"n": "2026", "v": "2026"}, {"n": "2025", "v": "2025"},
        {"n": "2024", "v": "2024"},
    ]},
    {"key": "sort", "name": "排序", "value": [
        {"n": "默认", "v": ""},
        {"n": "最新", "v": "updated_desc"}, {"n": "最热", "v": "heat_desc"},
    ]},
]

# 允许透传给 /v1/browse/catalog 的筛选参数（防止 extend 注入无关参数破坏签名语义）
_FILTER_KEYS = {
    "genre", "area", "language", "year", "year_gte", "year_lte",
    "sort", "status", "orientation", "q",
}
_MAX_PAGES = 10000
_MAX_EPISODE_PAGES = 30
_MAX_LINES = 12


# --------------------------------------------------------------------------- #
# 通用安全工具
# --------------------------------------------------------------------------- #

def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return str(value)
    return default


def _int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool) or value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _positive(value: Any, default: int, ceiling: int = 100) -> int:
    n = _int(value, default)
    if n < 1:
        n = default
    return min(n, ceiling)


def _first(*values: Any) -> str:
    for v in values:
        s = _text(v)
        if s:
            return s
    return ""


def _names(value: Any, limit: int = 0) -> List[str]:
    out = []
    for item in _as_list(value):
        s = _text(item)
        if s and s not in out:
            out.append(s)
    if limit > 0:
        out = out[:limit]
    return out


def _piece(value: Any) -> str:
    """清洗为 TVBox 播放列表安全的片段（不含 $ # 与换行）。"""
    s = _text(value)
    s = re.sub(r"[\r\n\t]+", " ", s)
    s = s.replace("$", "").replace("#", "")
    return s.strip()


def _absolute(url: Any, host: str) -> str:
    u = _text(url)
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    if u.startswith("/"):
        return host + u
    return ""


def _date_only(value: Any) -> str:
    s = _text(value)
    if not s:
        return ""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else s[:19]


def _quote(value: Any) -> str:
    return urllib.parse.quote(_text(value), safe="-_.~")


def _parse_extend(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("{"):
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    return obj
            except (ValueError, TypeError):
                pass
    return {}


def _single_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        for item in value:
            s = _text(item)
            if s:
                return s
        return ""
    return _text(value)


def _is_http(value: Any) -> bool:
    u = _text(value)
    return u.startswith("http://") or u.startswith("https://")


def _is_direct_line(line: Dict[str, Any]) -> bool:
    """线路是否已经是可直接播放的真实 URL。"""
    if not _is_http(line.get("url")):
        return False
    if line.get("resolved") is True:
        return True
    if line.get("resolve_required") is False:
        return True
    if _text(line.get("resolve_mode")) == "direct":
        return True
    if _text(line.get("url_kind")) in ("m3u8", "mp4", "url", "direct"):
        return True
    return False


def _encode_filter(params: Dict[str, Any], key: str) -> str:
    """筛选项 -> TVBox 的 v 值；多参数项编码为 query 串，由 categoryContent 还原。"""
    items = [(k, _text(v)) for k, v in params.items() if _text(k) and _text(v)]
    if not items:
        return ""
    if len(items) == 1 and items[0][0] == key:
        return items[0][1]
    return urllib.parse.urlencode(items)


def _empty_page(page: int = 1, limit: int = 20) -> Dict[str, Any]:
    return {"list": [], "page": page, "pagecount": 0, "limit": limit, "total": 0}


def _empty_detail() -> Dict[str, Any]:
    return {"list": []}


def _empty_player(flag: str = "", vid: str = "") -> Dict[str, Any]:
    return {
        "parse": 0,
        "play": "",
        "url": "",
        "header": {},
        "contentType": "",
        "danmaku": "",
        "subs": [],
        "flag": flag,
        "id": vid,
    }


# --------------------------------------------------------------------------- #
# 签名 HTTP 客户端
# --------------------------------------------------------------------------- #

class _Http(object):
    def __init__(self, host: str, key: str, build_version: str,
                 timeout: float, retries: int,
                 account: str = "", password: str = "") -> None:
        self.host = (host or _DEFAULT_HOST).rstrip("/")
        self.key = key or _DEFAULT_SIGN_KEY
        self.build_version = build_version or _DEFAULT_BUILD_VERSION
        self.timeout = float(timeout) if timeout and float(timeout) > 0 else 12.0
        self.retries = max(0, min(3, _int(retries, 1)))
        self.account = account or _DEFAULT_ACCOUNT
        self.password = password or _DEFAULT_PASSWORD
        # 登录返回的 cookie 统一保存在这里，并由 _base_headers 注入每次请求头
        self._cookie_map = {}    # type: Dict[str, str]
        self._auth_cookie = ""
        self._login_lock = threading.RLock()
        self._login_state = 0    # 0 未尝试 / 1 成功 / -1 失败
        self._login_at = 0.0
        # 不使用 CookieProcessor，避免与统一注入的 Cookie 头产生重复
        self._opener = urllib.request.build_opener()
        self._clock_offset = 0.0
        self._lock = threading.RLock()
        self._cache = {}  # type: Dict[str, Tuple[Any, float]]

    # -- 登录与会话 cookie ---------------------------------------------------- #

    def _absorb_cookies(self, headers: Any) -> None:
        """保存响应中的 Set-Cookie（同名覆盖，空值/Max-Age=0 视为清除）。"""
        try:
            raw_list = headers.get_all("Set-Cookie") if headers else None
        except Exception:
            raw_list = None
        if not raw_list:
            return
        with self._lock:
            changed = False
            for raw in raw_list:
                try:
                    first = str(raw).split(";", 1)[0].strip()
                    if "=" not in first:
                        continue
                    name, value = first.split("=", 1)
                    name, value = name.strip(), value.strip()
                    if not name:
                        continue
                    if value:
                        self._cookie_map[name] = value
                    else:
                        self._cookie_map.pop(name, None)
                    changed = True
                except Exception:
                    continue
            if changed:
                self._auth_cookie = "; ".join(
                    "%s=%s" % (k, v) for k, v in self._cookie_map.items() if v
                )

    def ensure_login(self, force: bool = False) -> bool:
        """初始化阶段登录；失败后一段时间内不重复尝试，绝不抛异常。"""
        with self._login_lock:
            if self._login_state == 1 and self._auth_cookie:
                return True
            now = time.time()
            if not force and now - self._login_at < _LOGIN_RETRY_INTERVAL:
                return False
            self._login_at = now
        try:
            obj, status = self.request(
                "POST", _LOGIN_PATH,
                body={"account": self.account, "password": self.password},
                need_auth=False, timeout=min(self.timeout, 8.0),
            )
            ok = status in (200, 201) and bool(obj) and bool(self._auth_cookie)
        except Exception:
            ok = False
        with self._login_lock:
            self._login_state = 1 if ok else -1
        return ok

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)

    # -- 时钟 ---------------------------------------------------------------- #

    def _apply_date(self, headers: Any) -> None:
        try:
            raw = headers.get("Date") if headers else None
            if not raw:
                return
            parsed = parsedate_tz(raw)
            if not parsed:
                return
            server_ts = calendar.timegm(parsed[:9])
            offset = server_ts - time.time()
            if abs(offset) < 86400:
                with self._lock:
                    self._clock_offset = offset
        except Exception:
            pass

    def _sync_clock(self) -> None:
        """失败/401 时用无签名接口重新对齐服务器时间。"""
        try:
            sync_headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
            if self._auth_cookie:
                sync_headers["Cookie"] = self._auth_cookie
            req = urllib.request.Request(
                self.host + "/v1/runtime/bootstrap", headers=sync_headers,
            )
            resp = self._opener.open(req, timeout=min(self.timeout, 8.0))
            try:
                self._apply_date(resp.headers)
                resp.read(64)
            finally:
                resp.close()
        except Exception:
            pass

    def _timestamp(self) -> str:
        with self._lock:
            offset = self._clock_offset
        return str(int((time.time() + offset) * 1000))

    # -- 请求 ---------------------------------------------------------------- #

    def _base_headers(self, signed: bool) -> Dict[str, str]:
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            "Referer": self.host + "/",
            "Origin": self.host,
            "x-ai-movie-client-name": _CLIENT_NAME,
            "x-ai-movie-client-version": _CLIENT_VERSION,
            "x-ai-movie-build-version": self.build_version,
            "x-ai-movie-protocol-version": _PROTOCOL_VERSION,
        }
        cookie = self._auth_cookie
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _signature(self, method: str, path: str) -> Dict[str, str]:
        ts = self._timestamp()
        nonce = secrets.token_hex(16)
        message = "\n".join((method.upper(), path, ts, nonce))
        signature = hmac.new(
            self.key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return {
            "x-ai-movie-timestamp": ts,
            "x-ai-movie-nonce": nonce,
            "x-ai-movie-signature": signature,
        }

    @staticmethod
    def _build_path(path: str, params: Optional[Dict[str, Any]]) -> str:
        if not params:
            return path
        items = []
        for key, value in params.items():
            if key is None or value is None:
                continue
            if isinstance(value, (list, tuple)):
                for sub in value:
                    if sub is not None and _text(sub):
                        items.append((_text(key), _text(sub)))
            else:
                s = _text(value)
                if s:
                    items.append((_text(key), s))
        if not items:
            return path
        sep = "&" if "?" in path else "?"
        return path + sep + urllib.parse.urlencode(items)

    def request(self, method: str, path: str,
                params: Optional[Dict[str, Any]] = None,
                body: Any = None, signed: bool = True,
                timeout: Optional[float] = None,
                need_auth: bool = True) -> Tuple[Dict[str, Any], int]:
        """返回 (JSON 对象, HTTP 状态码)；任何失败返回 ({}, code 或 0)。"""
        if need_auth and signed:
            self.ensure_login()
        full_path = self._build_path(path, params)
        url = self.host + full_path
        method = (method or "GET").upper()
        attempts = self.retries + 1
        status = 0
        for attempt in range(attempts):
            try:
                headers = self._base_headers(signed)
                if signed:
                    headers.update(self._signature(method, full_path))
                data = None
                if body is not None:
                    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                    headers["Content-Type"] = "application/json"
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                resp = self._opener.open(req, timeout=timeout or self.timeout)
                try:
                    status = _int(getattr(resp, "status", 0), 0) or _int(resp.getcode(), 0)
                    self._apply_date(resp.headers)
                    self._absorb_cookies(resp.headers)
                    raw = resp.read(8 * 1024 * 1024)
                finally:
                    resp.close()
                if not raw:
                    return {}, status or 204
                obj = json.loads(raw.decode("utf-8", "replace"))
                if not isinstance(obj, dict):
                    return {}, status or 200
                return obj, status or 200
            except urllib.error.HTTPError as exc:
                status = _int(exc.code, 0)
                self._apply_date(exc.headers)
                self._absorb_cookies(exc.headers)
                if status == 401 and signed:
                    self._sync_clock()
                if status in (401, 403) and signed and need_auth:
                    # 会话可能已失效：重置登录状态并重新登录后再重试
                    with self._login_lock:
                        self._login_state = 0
                        self._login_at = 0.0
                    self.ensure_login(force=True)
                if attempt + 1 < attempts and status in (401, 403, 408, 429, 500, 502, 503, 504):
                    continue
                return {}, status
            except Exception:
                if attempt + 1 < attempts:
                    if signed:
                        self._sync_clock()
                    continue
                return {}, 0
        return {}, status

    # -- 简单 TTL 缓存 ------------------------------------------------------- #

    def cached(self, key: str, ttl: float, producer) -> Any:
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[1] > now:
                return hit[0]
        try:
            value = producer()
        except Exception:
            value = None
        if value is None:
            value = {}
        # 空/失败结果只做极短缓存，避免故障期间长时间不可用
        effective = ttl if value else min(ttl, 8.0)
        with self._lock:
            if len(self._cache) > 256:
                for k in [k for k, v in self._cache.items() if v[1] <= now]:
                    self._cache.pop(k, None)
            self._cache[key] = (value, now + effective)
        return value


# --------------------------------------------------------------------------- #
# 采集主体
# --------------------------------------------------------------------------- #

class Spider(object):

    def __init__(self, extend: Any = None) -> None:
        self._host = _DEFAULT_HOST
        self._key = _DEFAULT_SIGN_KEY
        self._build_version = _DEFAULT_BUILD_VERSION
        self._timeout = 12.0
        self._retries = 1
        self._page_size = 20
        self._account = _DEFAULT_ACCOUNT
        self._password = _DEFAULT_PASSWORD
        self._http = self._new_http()
        self._meta = {}          # vod_id -> 列表卡片原始信息（补全详情缺失字段）
        self._meta_lock = threading.RLock()
        # 初始化阶段完成登录，cookie 随后统一注入所有请求头
        self._http.ensure_login(force=True)
        if extend is not None:
            self.init(extend)

    def _new_http(self) -> _Http:
        return _Http(self._host, self._key, self._build_version,
                     self._timeout, self._retries,
                     self._account, self._password)

    # -- 生命周期 ------------------------------------------------------------ #

    def init(self, *args: Any, **kwargs: Any) -> str:
        ext = None
        for arg in args:
            if isinstance(arg, dict):
                ext = arg
                break
            if isinstance(arg, str) and arg.strip().startswith("{"):
                parsed = _parse_extend(arg)
                if parsed:
                    ext = parsed
                    break
        if ext is None:
            ext = _parse_extend(kwargs.get("extend") or kwargs.get("cfg"))
        self._host = _text(ext.get("host"), _DEFAULT_HOST).rstrip("/") or _DEFAULT_HOST
        self._key = _text(ext.get("key") or ext.get("sign_key"), _DEFAULT_SIGN_KEY)
        self._build_version = _text(
            ext.get("build_version") or ext.get("build"), _DEFAULT_BUILD_VERSION
        )
        self._timeout = float(_text(ext.get("timeout"), "12") or "12")
        self._retries = _int(ext.get("retries"), 1)
        self._page_size = _positive(ext.get("page_size"), 20, 50)
        self._account = _text(ext.get("account") or ext.get("username"),
                              _DEFAULT_ACCOUNT) or _DEFAULT_ACCOUNT
        self._password = _text(ext.get("password"), _DEFAULT_PASSWORD) or _DEFAULT_PASSWORD
        self._http = self._new_http()
        self._http.ensure_login(force=True)
        return ""

    def getName(self) -> str:
        return "懂片帝AI"

    # -- 配置 / 分类 / 筛选 -------------------------------------------------- #

    def _bootstrap(self) -> Dict[str, Any]:
        def load():
            obj, _ = self._http.request("GET", "/v1/runtime/bootstrap", signed=False)
            return obj
        return self._http.cached("runtime.bootstrap", 300.0, load)

    def _categories(self) -> List[Dict[str, str]]:
        def load():
            ui = _as_dict(self._bootstrap().get("catalog_ui"))
            out = []
            for chan in _as_list(ui.get("channels")):
                c = _as_dict(chan)
                tid = _text(c.get("key"))
                if tid:
                    out.append({"type_id": tid,
                                "type_name": _first(c.get("label"), tid)})
            return out or [dict(x) for x in _FALLBACK_CATEGORIES]
        result = self._http.cached("categories", 300.0, load)
        return result if isinstance(result, list) and result else [dict(x) for x in _FALLBACK_CATEGORIES]

    def _filters(self) -> Dict[str, Any]:
        def load():
            ui = _as_dict(self._bootstrap().get("catalog_ui"))
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
                        value = _encode_filter(_as_dict(opt.get("params")), key)
                        if not value:
                            continue
                        label = _first(opt.get("label"), opt.get("key"), value)
                        values.append({"n": label, "v": value})
                    if len(values) > 1:
                        rows.append({"key": key,
                                     "name": _first(row.get("label"), key),
                                     "value": values})
                if rows:
                    out[kind] = rows
            if not out:
                return {"movie": _FALLBACK_FILTERS}
            common = out.get("movie") or next(iter(out.values()))
            out.setdefault("recommend", common)
            out.setdefault("updates", common)
            for entry in _FALLBACK_CATEGORIES:
                tid = _text(entry.get("type_id"))
                if tid and tid not in out:
                    out[tid] = common
            return out
        result = self._http.cached("filters", 300.0, load)
        return result if isinstance(result, dict) and result else {"movie": _FALLBACK_FILTERS}

    # -- 卡片 -> TVBox VOD --------------------------------------------------- #

    def _remember(self, vod_id: str, card: Dict[str, Any]) -> None:
        with self._meta_lock:
            if len(self._meta) > 800:
                self._meta.clear()
            self._meta[vod_id] = card

    def _recall(self, vod_id: str) -> Dict[str, Any]:
        with self._meta_lock:
            return dict(self._meta.get(vod_id) or {})

    def _vod_from_card(self, raw: Any) -> Optional[Dict[str, Any]]:
        c = _as_dict(raw)
        if not c:
            return None
        vod_id = _first(c.get("selected_variant_id"), c.get("default_variant_id"),
                        c.get("id"), c.get("variant_id"))
        if not vod_id:
            detail_url = _text(c.get("detail_url"))
            m = re.search(r"/v1/catalog/([^/?#]+)", detail_url)
            if m:
                vod_id = urllib.parse.unquote(m.group(1))
        title = _first(c.get("title"), c.get("normalized_title"))
        if not vod_id or not title:
            return None
        kind = _text(c.get("content_kind"))
        genres = _names(c.get("genres"), 8)
        availability = _as_dict(c.get("availability"))
        self._remember(vod_id, c)
        return {
            "vod_id": vod_id,
            "vod_name": title,
            "vod_pic": _absolute(_first(c.get("poster_url"), c.get("carousel_url"),
                                        c.get("backdrop_url")), self._host),
            "vod_remarks": _first(c.get("remarks"), availability.get("label"),
                                  c.get("episode_progress_text")),
            "vod_year": _first(c.get("year")),
            "vod_area": _first(c.get("area")),
            "vod_lang": _first(c.get("language")),
            "vod_actor": ",".join(_names(c.get("actors"), 20)),
            "vod_director": ",".join(_names(c.get("directors"), 10)),
            "vod_content": _first(c.get("overview"), c.get("description"),
                                  c.get("summary")),
            "type_name": _KIND_LABELS.get(kind, kind),
            "vod_class": ",".join(genres),
            "vod_time": _date_only(_first(c.get("updated_at"), c.get("recent_update"))),
            "vod_tag": _first(availability.get("label"), c.get("update_status")),
            "vod_score": "",
        }

    def _cards_to_vods(self, cards: List[Any]) -> List[Dict[str, Any]]:
        out, seen = [], set()
        for raw in _as_list(cards):
            vod = self._vod_from_card(raw)
            if vod and vod["vod_id"] not in seen:
                seen.add(vod["vod_id"])
                out.append(vod)
        return out

    # -- 首页 ---------------------------------------------------------------- #

    def _home_feed(self) -> Dict[str, Any]:
        def load():
            obj, _ = self._http.request("GET", "/v1/feed/home", {
                "scope": "public", "mode": "preview", "sections": 3, "cards": 10,
            })
            return obj
        result = self._http.cached("home.feed", 120.0, load)
        return result if isinstance(result, dict) else {}

    def _home_vods(self) -> List[Dict[str, Any]]:
        cards = []
        for section in _as_list(self._home_feed().get("sections")):
            cards.extend(_as_list(_as_dict(section).get("cards")))
        return self._cards_to_vods(cards)

    def homeContent(self, filter: Any = False) -> Dict[str, Any]:
        try:
            return {
                "class": self._categories(),
                "filters": self._filters(),
                "list": self._home_vods(),
            }
        except Exception:
            return {"class": [dict(x) for x in _FALLBACK_CATEGORIES],
                    "filters": {"movie": _FALLBACK_FILTERS}, "list": []}

    def homeVideoContent(self) -> Dict[str, Any]:
        try:
            vods, _ = self._browse({
                "page": 1, "limit": self._page_size, "sort": "updated_desc",
            })
            if not vods:
                vods = self._home_vods()
            return {"list": vods}
        except Exception:
            return {"list": []}

    # -- 列表 / 分类 / 搜索 -------------------------------------------------- #

    def _browse(self, params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        obj, _ = self._http.request("GET", "/v1/browse/catalog", params)
        # 异常响应（410 候选集失效 / 数据源失败）时退化为不带附加标记的纯查询
        if not _as_list(obj.get("cards")) and _text(obj.get("status")) == "410":
            retry = dict(params)
            for key in ("candidate_set_ref", "continue_search"):
                retry.pop(key, None)
            obj, _ = self._http.request("GET", "/v1/browse/catalog", retry)
        return self._cards_to_vods(obj.get("cards")), _as_dict(obj.get("pagination"))

    def _page_result(self, vods: List[Dict[str, Any]],
                     pagination: Dict[str, Any], page: int,
                     limit: int) -> Dict[str, Any]:
        if not vods:
            return _empty_page(page, limit)
        p = _as_dict(pagination)
        has_more = bool(p.get("has_more"))
        returned = _int(p.get("returned_count"), len(vods))
        total = _int(p.get("total"), -1)
        if total < 0:
            offset = _int(p.get("offset"), (page - 1) * limit)
            total = max(0, offset + returned + (limit if has_more else 0))
        pagecount = int(math.ceil(total / float(limit))) if total > 0 else 0
        if has_more and pagecount <= page:
            pagecount = page + 1
        return {"list": vods, "page": page, "pagecount": pagecount,
                "limit": limit, "total": total}

    @staticmethod
    def _extend_params(extend: Any) -> Dict[str, Any]:
        out = {}
        for key, raw in _parse_extend(extend).items():
            k = _text(key)
            if k not in _FILTER_KEYS:
                continue
            value = _single_value(raw)
            if not value:
                continue
            if "=" in value:
                try:
                    pairs = urllib.parse.parse_qsl(value, keep_blank_values=False)
                    if pairs and all(_text(a) in _FILTER_KEYS for a, _ in pairs):
                        for a, b in pairs:
                            if _text(b):
                                out[_text(a)] = _text(b)
                        continue
                except Exception:
                    pass
            out[k] = value
        return out

    def categoryContent(self, tid: Any, pg: Any = 1,
                        filter: Any = False, extend: Any = None) -> Dict[str, Any]:
        page = _positive(pg, 1, _MAX_PAGES)
        limit = self._page_size
        try:
            type_id = _text(tid)
            params = {"page": page, "limit": limit}
            if type_id == "updates":
                params["sort"] = "updated_desc"
            elif type_id == "recommend":
                params["sort"] = "trending"
                params["window"] = "day"
            elif type_id in _KIND_IDS:
                params["kind"] = type_id
                params["sort"] = "heat_desc"
            elif type_id:
                # 未知 tid：当作搜索词兜底，避免返回难以理解的错误
                params["q"] = type_id
            params.update(self._extend_params(extend))
            vods, pagination = self._browse(params)
            return self._page_result(vods, pagination, page, limit)
        except Exception:
            return _empty_page(page, limit)

    def searchContent(self, key: Any, quick: Any = False,
                      pg: Any = 1) -> Dict[str, Any]:
        page = _positive(pg, 1, _MAX_PAGES)
        word = _text(key)
        limit = 10 if quick else self._page_size
        if not word:
            return _empty_page(page, limit)
        try:
            vods, pagination = self._browse({
                "q": word, "page": page, "limit": limit, "intent": "catalog_search",
            })
            return self._page_result(vods, pagination, page, limit)
        except Exception:
            return _empty_page(page, limit)

    # -- 详情 / 剧集 --------------------------------------------------------- #

    def _fetch_episodes(self, vod_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        base = "/v1/catalog/" + _quote(vod_id)
        detail, _ = self._http.request("GET", base, {
            "episodes": "window", "episode_limit": 100,
        })
        if not detail:
            return {}, []
        episodes = [e for e in _as_list(detail.get("episodes")) if isinstance(e, dict)]
        pagination = _as_dict(detail.get("episode_pagination"))
        total = _int(pagination.get("total_count"), len(episodes))
        step = _positive(pagination.get("limit"), len(episodes) or 48, 100)
        offset = (_int(pagination.get("offset"), 0)
                  + _int(pagination.get("returned_count"), len(episodes)))
        guard = 0
        while (pagination.get("has_more") and offset < total
               and guard < _MAX_EPISODE_PAGES):
            guard += 1
            part, _ = self._http.request("GET", base + "/episodes", {
                "offset": offset, "limit": step,
            })
            chunk = [e for e in _as_list(part.get("episodes")) if isinstance(e, dict)]
            if not chunk:
                break
            episodes.extend(chunk)
            page2 = _as_dict(part.get("episode_pagination"))
            returned = _int(page2.get("returned_count"), len(chunk))
            if returned <= 0:
                break
            new_total = _int(page2.get("total_count"), total)
            if new_total > 0:
                total = new_total
            offset += returned
            pagination = page2
        # 去重：优先按 token，其次按剧集 id / 序号
        unique, seen = [], set()
        for ep in episodes:
            token = _text(ep.get("token"))
            number = _text(ep.get("number"))
            marker = token or _text(ep.get("id")) or ("n:" + number)
            if not marker or marker in seen:
                continue
            seen.add(marker)
            unique.append(ep)
        return detail, unique

    @staticmethod
    def _episode_parts(episode: Dict[str, Any]) -> Tuple[str, str]:
        number = _text(episode.get("number"))
        name = _piece(_first(episode.get("title"), episode.get("display_name"),
                             ("第" + number + "集") if number else "正片"))
        token = _piece(_first(episode.get("token"), episode.get("episode_token")))
        if token:
            return name or "正片", token
        urls = _as_dict(episode.get("urls"))
        for key in ("m3u8", "url", "play_url", "hls", "mp4"):
            candidate = _text(urls.get(key))
            if _is_http(candidate):
                return name or "正片", candidate
        for value in urls.values():
            if _is_http(value):
                return name or "正片", _text(value)
        return "", ""

    @staticmethod
    def _normalize_line(raw: Any) -> Dict[str, Any]:
        """兼容 compact 视图（id/label）与完整视图（playback_source_id/display_label）。"""
        line = dict(_as_dict(raw))
        line["playback_source_id"] = _first(
            line.get("playback_source_id"), line.get("id")
        )
        line["display_label"] = _first(line.get("display_label"), line.get("label"))
        line["provider_name"] = _first(line.get("provider_name"), line.get("label"))
        url = _text(line.get("url"))
        ticket = _text(line.get("resolve_ticket"))
        if not ticket and url.startswith("resolve://"):
            ticket = url[len("resolve://"):].strip()
        if not ticket and _text(line.get("url_kind")) == "resolve_ticket":
            ticket = url
        line["_ticket"] = ticket
        return line

    @staticmethod
    def _is_ticket_line(line: Dict[str, Any]) -> bool:
        """优先识别 url_kind == resolve_ticket 的视频源。"""
        if _text(line.get("url_kind")) == "resolve_ticket":
            return True
        url = _text(line.get("url"))
        return (_text(line.get("resolve_mode")) == "parse"
                and (url.startswith("resolve://") or bool(_text(line.get("_ticket")))))

    @staticmethod
    def _select_lines(lines: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool]:
        """有票据源时只保留票据源；否则展示其他视频源。"""
        tickets = [
            line for line in lines
            if Spider._is_ticket_line(line) and _text(line.get("_ticket"))
        ]
        if tickets:
            return tickets, True
        return lines, False

    def _lines(self, token: str, refresh: bool = False) -> List[Dict[str, Any]]:
        if not token or not token.startswith("av_"):
            return []
        key = "lines:" + token

        def load():
            obj, _ = self._http.request(
                "GET", "/v1/playback/resolve/" + _quote(token) + "?view=compact"
            )
            return obj

        if refresh:
            self._http.invalidate(key)
        obj = self._http.cached(key, 45.0, load)
        return [self._normalize_line(line)
                for line in _as_list(obj.get("line_options"))
                if isinstance(line, dict)]

    def _resolve_ticket_line(self, line: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """对 resolve_ticket 源做二次播放处理，换取真实可播地址。"""
        ticket = _text(line.get("_ticket"))
        if not ticket:
            return None
        cache_key = "ticket:" + hashlib.sha1(ticket.encode("utf-8")).hexdigest()

        def load():
            obj, status = self._http.request(
                "POST", _RESOLVE_LINE_PATH, body={"ticket": ticket}
            )
            if status not in (200, 201):
                return {}
            resolved = self._normalize_line(obj.get("line"))
            url = _text(resolved.get("url"))
            if not _is_http(url) or resolved.get("resolved") is False:
                return {}
            resolved["url"] = url
            return resolved

        result = self._http.cached(cache_key, 90.0, load)
        if isinstance(result, dict) and _is_http(result.get("url")):
            return result
        return None

    @staticmethod
    def _line_label(line: Dict[str, Any]) -> str:
        return _piece(_first(line.get("display_label"), line.get("label"),
                             line.get("provider_name"), line.get("play_from"),
                             line.get("provider_id"), line.get("id"), "线路"))

    def _flags(self, lines: List[Dict[str, Any]],
               ticket_mode: bool = False) -> List[str]:
        flags, seen = [], set()
        for line in lines:
            if ticket_mode:
                if not self._is_ticket_line(line):
                    continue
            elif not _is_direct_line(line):
                continue
            label = self._line_label(line)
            if not label or label in seen:
                continue
            seen.add(label)
            flags.append(label)
            if len(flags) >= _MAX_LINES:
                break
        return flags or ["懂片帝"]

    def detailContent(self, ids: Any) -> Dict[str, Any]:
        try:
            raw_ids = ids if isinstance(ids, (list, tuple)) else [ids]
            vod_id = ""
            for item in raw_ids:
                vod_id = _text(item)
                if vod_id:
                    break
            if not vod_id:
                return _empty_detail()
            detail, episodes = self._fetch_episodes(vod_id)
            card = self._recall(vod_id)
            if not detail and not card:
                return _empty_detail()

            merged = dict(card)
            merged.update(detail or {})

            entries = []
            for ep in episodes:
                name, play_id = self._episode_parts(ep)
                if name and play_id:
                    entries.append((name, play_id))
            if not entries:
                # 电影/单集：允许用 vod_id 自身触发播放解析
                entries = [("正片", vod_id)]

            first_token = ""
            for _, play_id in entries:
                if play_id.startswith("av_"):
                    first_token = play_id
                    break
            lines = self._lines(first_token) if first_token else []
            selected, ticket_mode = self._select_lines(lines)
            flags = self._flags(selected, ticket_mode)
            play_lists = []
            for _ in flags:
                play_lists.append("#".join(
                    "%s$%s" % (name, play_id) for name, play_id in entries
                ))

            progress = _first(merged.get("episode_progress_text"), merged.get("remarks"))
            update_status = _text(merged.get("update_status"))
            genres = _names(merged.get("genres"), 8)
            kind = _text(merged.get("content_kind"))
            availability = _as_dict(merged.get("availability"))
            seasons = _as_list(merged.get("seasons")) or _as_list(merged.get("variants"))
            description = _first(merged.get("description"), merged.get("overview"),
                                 merged.get("summary"), merged.get("vod_content"))
            if seasons and description:
                extra = "共%s季 / %s集" % (len(seasons), len(entries))
                if extra not in description:
                    description = description + "\n" + extra

            vod = {
                "vod_id": vod_id,
                "vod_name": _first(merged.get("title"), merged.get("normalized_title"),
                                   merged.get("vod_name")),
                "vod_pic": _absolute(_first(merged.get("poster_url"),
                                            merged.get("carousel_url"),
                                            merged.get("backdrop_url")), self._host),
                "vod_remarks": _first(progress, merged.get("vod_remarks"),
                                      availability.get("label")),
                "vod_year": _first(merged.get("year"), merged.get("vod_year")),
                "vod_area": _first(merged.get("area"), merged.get("vod_area")),
                "vod_lang": _first(merged.get("language"), merged.get("vod_lang")),
                "vod_actor": ",".join(_names(merged.get("actors"), 20)),
                "vod_director": ",".join(_names(merged.get("directors"), 10)),
                "vod_content": description,
                "type_name": _KIND_LABELS.get(kind, kind or _text(merged.get("type_name"))),
                "vod_class": ",".join(genres),
                "vod_time": _date_only(_first(merged.get("updated_at"),
                                              merged.get("vod_time"),
                                              merged.get("generated_at"))),
                "vod_tag": _first(availability.get("label"), update_status,
                                  merged.get("vod_tag")),
                "vod_score": "",
                "vod_play_from": "$$$".join(flags),
                "vod_play_url": "$$$".join(play_lists),
            }
            return {"list": [vod]}
        except Exception:
            return _empty_detail()

    # -- 播放地址 ------------------------------------------------------------- #

    @staticmethod
    def _clean_id(value: Any) -> str:
        s = _text(value)
        if not s:
            return ""
        # 兼容 TVBox 偶尔回传 "集名$id" 或 "a#b" 的形态
        if "$" in s:
            s = s.split("$")[-1]
        if "#" in s:
            s = s.split("#")[0]
        s = s.strip()
        if "%" in s:
            try:
                decoded = urllib.parse.unquote(s)
                if decoded:
                    s = decoded
            except Exception:
                pass
        return s

    def _pick_line_by_flag(self, lines: List[Dict[str, Any]],
                           flag: str) -> Optional[Dict[str, Any]]:
        """按用户选中的线路名匹配；匹配不到时退回优先级最高的线路。"""
        if not lines:
            return None

        def score(line: Dict[str, Any]) -> Tuple[int, int, int]:
            labels = {
                _text(line.get("display_label")), _text(line.get("label")),
                _text(line.get("provider_name")), _text(line.get("play_from")),
                _text(line.get("provider_id")), _text(line.get("id")),
                self._line_label(line),
            }
            match = 2 if flag and flag in labels else (1 if not flag else 0)
            selected = 1 if line.get("selected") is True else 0
            weight = _int(line.get("preference_weight"), 0)
            return match, selected, weight

        matched = [line for line in lines if score(line)[0] > 0] if flag else []
        pool = matched or lines
        pool = sorted(pool, key=lambda line: (
            -score(line)[0], -score(line)[1], -score(line)[2]
        ))
        return pool[0] if pool else None

    def _pick_line(self, lines: List[Dict[str, Any]],
                   flag: str) -> Optional[Dict[str, Any]]:
        direct = [line for line in lines if _is_direct_line(line)]
        return self._pick_line_by_flag(direct, flag) if direct else None

    @staticmethod
    def _line_headers(line: Dict[str, Any], host: str) -> Dict[str, str]:
        headers = {"User-Agent": _USER_AGENT, "Referer": host + "/"}
        for key in ("headers", "header", "requestHeaders", "request_headers"):
            raw = line.get(key)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    name, value = _text(k), _text(v)
                    if name and value:
                        headers[name] = value
            elif isinstance(raw, str) and raw.strip().startswith("{"):
                try:
                    obj = json.loads(raw)
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            name, value = _text(k), _text(v)
                            if name and value:
                                headers[name] = value
                except (ValueError, TypeError):
                    pass
        return headers

    def playerContent(self, flag: Any, id: Any,
                      vipFlags: Any = None) -> Dict[str, Any]:
        flag_text = _piece(flag)
        play_id = self._clean_id(id)
        result = _empty_player(flag_text, play_id)
        if not play_id:
            return result
        if _is_http(play_id):
            result["url"] = play_id
            result["header"] = {"User-Agent": _USER_AGENT}
            return result
        try:
            lines = self._lines(play_id)
            selected, ticket_mode = self._select_lines(lines)
            if ticket_mode:
                # 存在 resolve_ticket 源：只使用这类源，并完成二次播放解析
                line = self._pick_line_by_flag(selected, flag_text)
                resolved = self._resolve_ticket_line(line) if line else None
                if not resolved:
                    # 票据过期时刷新线路后重试一次
                    for stale in selected:
                        old_ticket = _text(stale.get("_ticket"))
                        if old_ticket:
                            self._http.invalidate(
                                "ticket:"
                                + hashlib.sha1(old_ticket.encode("utf-8")).hexdigest()
                            )
                    fresh = self._lines(play_id, refresh=True)
                    selected2, mode2 = self._select_lines(fresh)
                    if mode2:
                        retry_line = self._pick_line_by_flag(selected2, flag_text)
                        resolved = (self._resolve_ticket_line(retry_line)
                                    if retry_line else None)
                if not resolved:
                    return result
                line = resolved
            else:
                # 不存在票据源：展示并使用其他视频源
                line = self._pick_line(selected, flag_text)
                if not line:
                    return result
            url = _text(line.get("url"))
            if not _is_http(url):
                return result
            result["url"] = url
            result["header"] = self._line_headers(line, self._host)
            result["flag"] = self._line_label(line) or flag_text
            kind = _text(line.get("url_kind"))
            if kind == "m3u8" or ".m3u8" in url:
                result["contentType"] = "application/vnd.apple.mpegurl"
            elif kind == "mp4" or ".mp4" in url:
                result["contentType"] = "video/mp4"
            return result
        except Exception:
            return result


# --------------------------------------------------------------------------- #
# 模块级入口（兼容按函数名加载的 py 采集器）
# --------------------------------------------------------------------------- #

spider = Spider()


def init(*args: Any, **kwargs: Any) -> str:
    return spider.init(*args, **kwargs)


def getName() -> str:
    return spider.getName()


def homeContent(filter: Any = False) -> Dict[str, Any]:
    return spider.homeContent(filter)


def homeVideoContent() -> Dict[str, Any]:
    return spider.homeVideoContent()


def categoryContent(tid: Any, pg: Any = 1, filter: Any = False,
                    extend: Any = None) -> Dict[str, Any]:
    return spider.categoryContent(tid, pg, filter, extend)


def detailContent(ids: Any) -> Dict[str, Any]:
    return spider.detailContent(ids)


def searchContent(key: Any, quick: Any = False, pg: Any = 1) -> Dict[str, Any]:
    return spider.searchContent(key, quick, pg)


def playerContent(flag: Any, id: Any, vipFlags: Any = None) -> Dict[str, Any]:
    return spider.playerContent(flag, id, vipFlags)


if __name__ == "__main__":
    import sys
    try:
        instance = Spider()
        probe = instance.homeContent(False)
        ok = isinstance(probe, dict) and isinstance(probe.get("list"), list) \
            and isinstance(probe.get("class"), list) and bool(probe.get("class")) \
            and instance._http.ensure_login()
        sys.exit(0 if ok else 1)
    except Exception:
        sys.exit(1)
