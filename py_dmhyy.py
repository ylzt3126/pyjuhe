# coding=utf-8
# !/usr/bin/python
"""
TVBox / FongMi Python 爬虫：大马猴影视（https://dmhyy.com/）

接口约定（宿主 chaquo 分发层固定，不可改名、不可改参数个数）：
    init(extend)                                   -> None
    homeContent(filter)                            -> dict
    homeVideoContent()                             -> dict
    categoryContent(tid, pg, filter, extend)       -> dict
    detailContent(ids)                             -> dict
    searchContent(key, quick, pg="1")              -> dict
    playerContent(flag, id, vipFlags)              -> dict
    isVideoFormat(url) / manualVideoCheck()        -> bool
    localProxy(param)                              -> list
返回值必须是 Python 原生对象，由 app.py 统一 json2str，禁止自己 json.dumps。

站点说明：
    前端是 Vue3 单页应用，页面本身没有可采集的 HTML，数据全部来自官方 JSON 接口
    /api.php/web/*。接口带一层签名校验，五个头缺一不可：
        x-aid  = appId
        x-ave  = 客户端版本号
        x-time = 毫秒时间戳
        x-nonc = 随机串（16 字节 hex）
        x-sign = SHA256("finger=..&id=..&nonce=..&sk=..&time=..&v=..").upper()
    不带签名时首页/分类/搜索仍能返回数据，但详情里的 vod_play_url 与 vodplayer
    会被清空（等于没有播放地址），所以本爬虫统一走签名请求。
"""
import re
import os
import sys
import json
import time
import random
import hashlib
from urllib.parse import urlencode, quote

sys.path.append('..')
from base.spider import Spider


class Spider(Spider):

    host = "https://dmhyy.com"
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

    # 签名参数（取自站点前端包 index-*.js 里的 ae 常量，站点改版时需要重新抓）
    appId = "cde75dbd61ce274e07e8ce29"
    finger = "505a0e62069a061e07698eb4b6327d88"
    secret = "9b1fccddb6f8e1b385de7e6e736e050144a7f3a208130fff"
    clientVer = "1"

    # 首页分类（type_id, 名称）。正常情况 init 时会用 /index/home 的返回覆盖
    categories = [
        ("22", "剧集"),
        ("23", "电影"),
        ("24", "动漫"),
        ("25", "综艺"),
    ]

    # 排序方式（接口 sort 参数）
    sorts = [
        ("hits", "人气"), ("time", "最新"), ("score", "评分"), ("year", "年份"),
    ]

    # 详情里可播线路少于该值时，再用聚合接口补几条备用线路
    minLines = 3
    enableAggregate = True

    # 分类页筛选项兜底（接口 filter_options 拿不到时用这份）
    defaultOptions = {
        "area": ["大陆", "香港", "台湾", "美国", "日本", "韩国", "泰国", "英国"],
        "class": ["剧情", "喜剧", "动作", "爱情", "科幻", "悬疑", "犯罪", "奇幻", "战争", "恐怖"],
        "year": ["2026", "2025", "2024", "2023", "2022", "2021", "2020-2016", "2015-2011", "2010-2000"],
    }

    def __init__(self):
        self.cache = {}
        self.metaCache = None

    def getName(self):
        return "大马猴影视"

    def init(self, extend=""):
        try:
            cfg = json.loads(extend) if isinstance(extend, str) and extend.strip().startswith("{") else {}
        except Exception:
            cfg = {}
        if cfg.get("host"):
            self.host = str(cfg["host"]).rstrip("/")
        # 允许外部覆盖签名参数，方便站点换 key 后不改代码
        for key in ("appId", "finger", "secret", "clientVer"):
            if cfg.get(key):
                setattr(self, key, str(cfg[key]))
        self.cache = {}
        self.metaCache = None

    # ------------------------------------------------------------------ 基础

    def nonce(self):
        try:
            return os.urandom(16).hex()
        except Exception:
            return "".join(random.choice("0123456789abcdef") for _ in range(32))

    def signHeaders(self):
        ts = str(int(time.time() * 1000))
        non = self.nonce()
        raw = "finger=%s&id=%s&nonce=%s&sk=%s&time=%s&v=%s" % (
            self.finger, self.appId, non, self.secret, ts, self.clientVer)
        sign = hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()
        return {
            "x-aid": self.appId,
            "x-ave": self.clientVer,
            "x-time": ts,
            "x-nonc": non,
            "x-sign": sign,
        }

    def header(self, referer=None):
        h = {
            "User-Agent": self.ua,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "x-platform": "web",
            "X-Client": "YOUR_PUBLIC_CLIENT_ID",
            "Referer": referer or (self.host + "/"),
        }
        h.update(self.signHeaders())
        return h

    def playHeader(self):
        """播放器请求直链时用，不带签名头。"""
        return {
            "User-Agent": self.ua,
            "Referer": self.host + "/",
        }

    def api(self, path, params=None, retry=2, referer=None):
        """请求 /api.php/web/* 并解出 JSON。失败返回 {}，由调用方兜底。"""
        url = self.host + "/api.php/web" + path
        if params:
            clean = [(k, v) for k, v in params.items() if v is not None and str(v) != ""]
            if clean:
                url = url + "?" + urlencode(clean, quote_via=quote)
        for i in range(retry + 1):
            try:
                rsp = self.fetch(url, headers=self.header(referer), timeout=15)
                text = (rsp.text or "").strip()
                code = getattr(rsp, "status_code", 0)
                if code == 200 and text.startswith("{"):
                    return json.loads(text)
                print("dmhyy api %s -> %s %s" % (url, code, text[:120]))
            except Exception as e:
                print("dmhyy api error %s %s" % (url, e))
            time.sleep(0.4 * (i + 1))
        return {}

    @staticmethod
    def dataOf(resp):
        data = resp.get("data") if isinstance(resp, dict) else None
        return data if isinstance(data, (list, dict)) else None

    @staticmethod
    def toInt(value, default=1):
        try:
            return int(str(value).strip())
        except Exception:
            return default

    @staticmethod
    def fixPic(url):
        url = (url or "").strip()
        if url.startswith("//"):
            return "https:" + url
        return url

    @staticmethod
    def text(value):
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            value = "/".join([str(v).strip() for v in value if str(v).strip()])
        s = str(value)
        s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"').replace("&#039;", "'")
        return re.sub(r"\s+", " ", s).strip()

    # ------------------------------------------------------------- 分类与首页

    def siteMeta(self):
        """拉一次首页接口，缓存分类与筛选项。"""
        now = time.time()
        if self.metaCache and self.metaCache[0] > now:
            return self.metaCache[1]
        meta = {"class": list(self.categories), "options": {}, "home": None}
        data = self.dataOf(self.api("/index/home"))
        if isinstance(data, dict):
            meta["home"] = data
            cats, options = [], {}
            for c in data.get("categories") or []:
                tid = str(c.get("type_id") or "").strip()
                name = self.text(c.get("type_name"))
                if not tid or not name:
                    continue
                cats.append((tid, name))
                opts = c.get("filter_options") or {}
                options[tid] = {
                    "area": [self.text(x) for x in (opts.get("area") or []) if self.text(x)],
                    "class": [self.text(x) for x in (opts.get("class") or []) if self.text(x)],
                    "year": [self.text(x) for x in (opts.get("year") or []) if self.text(x)],
                }
            if cats:
                meta["class"] = cats
            meta["options"] = options
        self.metaCache = (now + 1800, meta)
        return meta

    def homeContent(self, filter):
        meta = self.siteMeta()
        classes = [{"type_id": tid, "type_name": name} for tid, name in meta["class"]]
        result = {"class": classes}
        if not filter:
            return result
        sort = [{"n": n, "v": v} for v, n in self.sorts]
        filters = {}
        for tid, _ in meta["class"]:
            opts = meta["options"].get(tid) or self.defaultOptions
            item = []
            for key, label in (("class", "类型"), ("area", "地区"), ("year", "年份")):
                values = [{"n": "全部", "v": ""}]
                values += [{"n": v, "v": v} for v in (opts.get(key) or [])]
                if len(values) > 1:
                    item.append({"key": key, "name": label, "value": values})
            item.append({"key": "sort", "name": "排序", "value": sort})
            filters[tid] = item
        result["filters"] = filters
        return result

    def homeVideoContent(self):
        data = self.siteMeta().get("home")
        if not isinstance(data, dict):
            resp = self.api("/index/home")
            data = self.dataOf(resp)
        videos, seen = [], set()
        if isinstance(data, dict):
            pools = [data.get("webCarouselVideos"), data.get("recommend"), data.get("appCarouselVideos")]
            for c in data.get("categories") or []:
                pools.append(c.get("videos"))
            for pool in pools:
                for item in pool or []:
                    v = self.toVideo(item)
                    if v["vod_id"] and v["vod_id"] not in seen:
                        seen.add(v["vod_id"])
                        videos.append(v)
        return {"list": videos[:30]}

    # --------------------------------------------------------------- 列表解析

    def toVideo(self, item):
        if not isinstance(item, dict):
            return {"vod_id": "", "vod_name": "", "vod_pic": "", "vod_remarks": ""}
        vid = str(item.get("vod_id") or "").strip()
        name = self.text(item.get("vod_name"))
        remark = self.text(item.get("vod_remarks"))
        score = self.text(item.get("vod_douban_score"))
        try:
            if score and float(score) > 0:
                remark = (remark + " 豆瓣" + score).strip()
        except Exception:
            pass
        return {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": self.fixPic(item.get("vod_pic")),
            "vod_remarks": remark,
        }

    def listOf(self, resp):
        data = self.dataOf(resp)
        videos = []
        if isinstance(data, list):
            for item in data:
                v = self.toVideo(item)
                if v["vod_id"] and v["vod_name"]:
                    videos.append(v)
        return videos

    def catUrlParams(self, tid, pg, extend):
        name = ""
        for cid, cname in self.siteMeta()["class"]:
            if cid == str(tid):
                name = cname
                break
        ext = extend if isinstance(extend, dict) else {}
        params = {
            "type_name": name or "",
            "type_id": str(tid),
            "page": str(pg or "1"),
            "sort": self.text(ext.get("sort")) or "hits",
            "class": self.text(ext.get("class")),
            "area": self.text(ext.get("area")),
            "year": self.text(ext.get("year")),
        }
        return params

    def categoryContent(self, tid, pg, filter, extend):
        pg = str(pg or "1")
        params = self.catUrlParams(tid, pg, extend)
        if not params["type_name"]:
            # 未知分类：接口对无效 type 会返回全站数据，这里直接拦掉
            return {"list": [], "page": self.toInt(pg), "pagecount": 1, "limit": 24, "total": 0}
        resp = self.api("/filter/vod", params)
        videos = self.listOf(resp)
        try:
            count = int(resp.get("pageCount") or 0)
        except Exception:
            count = 0
        if not count:
            count = self.toInt(pg) if len(videos) < 18 else 999
        limit = 0
        try:
            limit = int(resp.get("limit") or 0)
        except Exception:
            limit = 0
        return {
            "list": videos,
            "page": self.toInt(pg),
            "pagecount": max(1, count),
            "limit": limit or 24,
            "total": 999999,
        }

    # ------------------------------------------------------------------ 详情

    def parseLines(self, node, players):
        """把 vod_play_from / vod_play_url / vodplayer 拆成 [(线路名, [集名$地址])]。

        decode_status 非 0 的线路（Ace、RR 等）地址是密文，必须走站点自己的
        服务端/客户端解码，直链播不了，这里直接丢掉，避免点开就报错。
        """
        metas = {}
        for p in players or []:
            if not isinstance(p, dict):
                continue
            code = self.text(p.get("from"))
            if code:
                metas[code] = p
        froms = [self.text(x) for x in str(node.get("vod_play_from") or "").split("$$$")]
        urls = str(node.get("vod_play_url") or "").split("$$$")
        lines, used = [], {}
        for code, raw in zip(froms, urls):
            if not code or not raw.strip():
                continue
            meta = metas.get(code) or {}
            status = str(meta.get("decode_status", "0")).strip()
            if status not in ("", "0"):
                continue
            if self.text(meta.get("display_platforms")) and "web" not in self.text(meta.get("display_platforms")):
                continue
            eps = []
            for item in raw.split("#"):
                item = item.replace("\t", " ").strip()
                if "$" not in item:
                    continue
                title, url = item.split("$", 1)
                title, url = self.text(title) or "正片", url.strip()
                if not url:
                    continue
                eps.append("%s$%s" % (title, url))
            if not eps:
                continue
            show = self.text(meta.get("show")) or code
            if show in used:
                used[show] += 1
                show = "%s%d" % (show, used[show])
            else:
                used[show] = 1
            lines.append((show, eps))
        return lines

    def aggregate(self, vid, name="", year=""):
        """详情里线路太少时，用聚合接口兜底补源：先按 vod_id 问，问不到再按片名+年份问。"""
        if not self.enableAggregate:
            return []
        ref = self.host + "/play/" + str(vid)
        resp = self.api("/internal/search_aggregate", {"vod_id": str(vid)}, referer=ref)
        data = self.dataOf(resp)
        if not data and name:
            resp = self.api("/internal/search_aggregate", {"name": name, "year": year}, referer=ref)
            data = self.dataOf(resp)
        lines, used = [], {}
        for item in data or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("decode_status", "0")).strip() not in ("", "0"):
                continue
            eps = []
            for seg in str(item.get("vod_play_url") or "").split("#"):
                seg = seg.replace("\t", " ").strip()
                if "$" not in seg:
                    continue
                title, url = seg.split("$", 1)
                if not url.strip():
                    continue
                eps.append("%s$%s" % (self.text(title) or "正片", url.strip()))
            if not eps:
                continue
            show = self.text(item.get("site_name")) or self.text(item.get("site_key")) or "线路"
            if show in used:
                used[show] += 1
                show = "%s%d" % (show, used[show])
            else:
                used[show] = 1
            lines.append((show, eps))
        return lines

    def detailContent(self, ids):
        if isinstance(ids, str):
            ids = [ids]
        vid = str(ids[0]).strip() if ids else ""
        m = re.search(r"(\d+)", vid)
        vid = m.group(1) if m else vid
        if not vid:
            return {"list": []}

        now = time.time()
        hit = self.cache.get("detail:" + vid)
        if hit and hit[0] > now:
            return hit[1]

        resp = self.api("/vod/get_detail", {"vod_id": vid}, referer=self.host + "/play/" + vid)
        data = self.dataOf(resp)
        node = data[0] if isinstance(data, list) and data else None
        if not isinstance(node, dict):
            return {"list": []}

        name = self.text(node.get("vod_name"))
        if not name:
            return {"list": []}

        typeName = ""
        tid = str(node.get("type_id") or "")
        for cid, cname in self.siteMeta()["class"]:
            if cid == tid:
                typeName = cname
                break

        lines = self.parseLines(node, resp.get("vodplayer"))
        if len(lines) < self.minLines:
            exists = set([x[0] for x in lines])
            for line in self.aggregate(vid, name, self.text(node.get("vod_year"))):
                if line[0] not in exists:
                    exists.add(line[0])
                    lines.append(line)

        remark = self.text(node.get("vod_remarks"))
        score = self.text(node.get("vod_douban_score"))
        try:
            if score and float(score) > 0:
                remark = (remark + " 豆瓣" + score).strip()
        except Exception:
            pass

        vod = {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": self.fixPic(node.get("vod_pic")),
            "type_name": typeName or self.text(node.get("type_name")),
            "vod_year": self.text(node.get("vod_year")),
            "vod_area": self.text(node.get("vod_area")),
            "vod_lang": self.text(node.get("vod_lang")),
            "vod_actor": self.text(node.get("vod_actor")),
            "vod_director": self.text(node.get("vod_director")),
            "vod_remarks": remark,
            "vod_content": self.text(node.get("vod_content")),
            "vod_play_from": "$$$".join([x[0] for x in lines]),
            "vod_play_url": "$$$".join(["#".join(x[1]) for x in lines]),
        }
        if score:
            vod["vod_douban_score"] = score
        result = {"list": [vod]}
        self.cache["detail:" + vid] = (now + 900, result)
        return result

    # ------------------------------------------------------------------ 搜索

    def searchContent(self, key, quick, pg="1"):
        pg = str(pg or "1")
        resp = self.api("/search/index", {"wd": str(key), "page": pg, "limit": "15"})
        videos = self.listOf(resp)
        try:
            count = int(resp.get("pageCount") or 0)
        except Exception:
            count = 0
        if not count:
            # 没有 pageCount 时用 hasMore 推：还有下一页就把页码往后挪一格
            count = self.toInt(pg) + 1 if resp.get("hasMore") else self.toInt(pg)
        return {
            "list": videos,
            "page": self.toInt(pg),
            "pagecount": max(1, count),
            "limit": 15,
            "total": 999999,
        }

    # ------------------------------------------------------------------ 播放

    def playerContent(self, flag, id, vipFlags):
        url = str(id or "").strip()
        if url.startswith("/"):
            url = self.host + url
        now = time.time()
        hit = self.cache.get("play:" + url)
        if hit and hit[0] > now:
            return hit[1]

        if url.startswith("http"):
            # 详情页给的已经是直链 m3u8/mp4，直接交给播放器；
            # 遇到非视频地址（少见）才让宿主去嗅探。
            result = {
                "parse": 0 if self.isVideoFormat(url) else 1,
                "playUrl": "",
                "url": url,
                "header": self.playHeader(),
            }
        else:
            # 密文/相对地址兜底：交给宿主解析站点播放页
            result = {
                "parse": 1,
                "playUrl": "",
                "url": self.host + "/play/" + quote(url, safe=""),
                "header": self.playHeader(),
            }
        self.cache["play:" + url] = (now + 600, result)
        return result

    def isVideoFormat(self, url):
        url = str(url or "")
        return ".m3u8" in url or ".mp4" in url or ".flv" in url

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        # 本爬虫不产出代理地址，保留兜底返回，避免宿主调用时取不到元素
        return [404, "text/plain", "not supported"]

    def destroy(self):
        self.cache = {}
        self.metaCache = None
