# coding=utf-8
# !/usr/bin/python
"""
TVBox / FongMi Python 爬虫：看看鸭（https://www.kankanya.me/）

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

站点特征（本脚本的三处关键处理）：
1. 全站前置了 JS 人机验证：无 cookie 的请求一律返回 4KB 的挑战页（内含
   var token = encrypt("...")）。挑战页的 encrypt 是可逆的字符表位移 + 随机
   填充，本地复刻后 POST /robot.php 即拿到放行的 PHPSESSID。
2. 分类页走 MacCMS 的 /show/{type}-...-{page}---.html 路径规则，字段以 "-"
   分隔共 12 段（index: 1=地区 2=排序 3=类型 4=语言 5=字母 8=页码 11=年份），
   所以筛选和翻页都靠拼这 12 段完成，而不是 query 参数。
3. 播放页只给加密的 vid，真实地址要 POST /kkyplayer/api.php 拿。返回的 url 有
   两种混淆，都已还原成直链：
     urlmode=1「base64 → md5('test') 循环异或 → base64 → 按 / 切三段（两段字母
                 置换表 + 载荷）」
     urlmode=2「base64 → 每 3 位取第 1 位 → 按挑战页字符表回退 3 位」，正是挑战页
                 encrypt() 的逆运算，站点把同一套算法复用了两次。
   两者都失败（例如接口直接回 403 线路维护）时才退化成 parse=1，交给宿主嗅探
   /kkyplayer/index.php?vid= 播放页——该页本身不校验人机验证。
"""
import re
import sys
import json
import time
import gzip
import random
import base64
import hashlib
import urllib.parse
import urllib.request
from urllib.parse import urljoin

sys.path.append('..')
from base.spider import Spider


class Spider(Spider):

    host = "https://www.kankanya.me"
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

    # 挑战页里写死的 62 字符置换表，encrypt() 依赖它，站点换皮时可能变
    verify_chars = "PXhw7UT1B0a9kQDKZsjIASmOezxYG4CHo5Jyfg2b8FLpEvRr3WtVnlqMidu6cN"

    # 分类：前四个是 MacCMS 的 show 归档页（可筛选、可分页），后两个是单页榜单
    categories = [
        ("dianying", "电影"),
        ("juji", "剧集"),
        ("zongyi", "综艺"),
        ("dongman", "动漫"),
        ("new", "今日更新"),
        ("hot", "热榜"),
    ]

    # 归档页 URL 的 12 段里，各筛选项落在第几段（实测得出，非猜测）
    slots = {"area": 1, "order": 2, "class": 3, "lang": 4, "letter": 5, "year": 11}
    slot_names = {"area": "地区", "order": "排序", "class": "类型", "lang": "语言", "letter": "字母", "year": "年份"}

    def __init__(self):
        self.cache = {}
        self.filters = {}
        self.filters_at = 0
        self.cookies = {}

    def getName(self):
        return "看看鸭"

    def init(self, extend=""):
        try:
            cfg = json.loads(extend) if isinstance(extend, str) and extend.strip().startswith("{") else {}
        except Exception:
            cfg = {}
        if cfg.get("host"):
            self.host = str(cfg["host"]).rstrip("/")
        self.cache = {}
        self.filters = {}
        self.filters_at = 0
        self.cookies = {}

    # ------------------------------------------------------------------ 基础

    def header(self, referer=None):
        return {
            "User-Agent": self.ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": referer or (self.host + "/"),
        }

    @staticmethod
    def clean(s):
        if not s:
            return ""
        s = re.sub(r"<[^>]+>", "", s)
        s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
        s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
        return re.sub(r"\s+", " ", s).strip()

    @staticmethod
    def remember(cookies, raw):
        """从 Set-Cookie 里抠出键值对，兼容一条响应带多个 cookie 的情况。"""
        for part in str(raw or "").split(","):
            kv = part.split(";")[0].strip()
            if "=" in kv:
                k, v = kv.split("=", 1)
                if k.strip() and v.strip():
                    cookies[k.strip()] = v.strip()

    def http(self, url, data=None, referer=None, retry=2):
        """统一的取页入口：urllib 主用（自己管 cookie），失败再退回宿主 fetch。"""
        for i in range(retry + 1):
            text = self.raw(url, data, referer)
            if text and self.challenged(text):
                if self.verify(url, text):
                    text = self.raw(url, data, referer)
            if text and len(text) > 500 and not self.challenged(text):
                return text
            time.sleep(0.4 * (i + 1))
        return ""

    def api(self, url, data, referer=None, retry=2):
        """取流接口要的是 JSON，长度门槛和挑战页判定都得单独处理。"""
        for i in range(retry + 1):
            text = self.raw(url, data, referer)
            if text and self.challenged(text):
                self.verify(url, text)
                text = self.raw(url, data, referer)
            if text and text.strip().startswith("{"):
                return text
            time.sleep(0.3 * (i + 1))
        return ""

    def raw(self, url, data=None, referer=None):
        hdr = self.header(referer)
        if self.cookies:
            hdr["Cookie"] = "; ".join("%s=%s" % (k, v) for k, v in self.cookies.items())
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            hdr["Content-Type"] = "application/x-www-form-urlencoded"
            hdr["X-Requested-With"] = "XMLHttpRequest"

        try:
            req = urllib.request.Request(url, data=body, headers=hdr)
            rsp = urllib.request.urlopen(req, timeout=15)
            for k, v in rsp.getheaders():
                if k.lower() == "set-cookie":
                    self.remember(self.cookies, v)
            buf = rsp.read()
            if buf[:2] == b"\x1f\x8b":
                buf = gzip.decompress(buf)
            return buf.decode("utf-8", "ignore")
        except Exception as e:
            print("kankanya http error %s %s" % (url, e))

        # 宿主通道兜底：GET 用 fetch，POST 用 post（chaquo 里 okhttp 常常比 urllib 稳）
        try:
            fn = getattr(self, "post" if data is not None else "fetch", None)
            if callable(fn):
                kw = {"headers": hdr, "timeout": 15, "cookies": dict(self.cookies)}
                if data is not None:
                    kw["params"] = data
                try:
                    rsp = fn(url, **kw)
                except TypeError:
                    kw.pop("params", None)
                    kw["data"] = data
                    rsp = fn(url, **kw)
                for k, v in getattr(rsp, "headers", {}).items():
                    if k.lower() == "set-cookie":
                        self.remember(self.cookies, v)
                return getattr(rsp, "text", "") or ""
        except Exception as e:
            print("kankanya fallback error %s %s" % (url, e))
        return ""

    @staticmethod
    def challenged(text):
        head = text[:2000]
        return ("var token = encrypt(" in text) or ("防火墙正在检查" in head) or ("人机验证" in head)

    def encrypt(self, s):
        """复刻挑战页的 encrypt()：查表 +3 位移，每个字符前后各插一个随机字符，再 base64。"""
        out = []
        for ch in s:
            n = self.verify_chars.find(ch)
            code = self.verify_chars[(n + 3) % 62] if n >= 0 else ch
            out.append(self.verify_chars[random.randrange(62)] + code + self.verify_chars[random.randrange(62)])
        return base64.b64encode("".join(out).encode("utf-8")).decode()

    def verify(self, url, page):
        """过掉人机验证：用挑战页给的 token + 当前 URL 换放行 cookie。"""
        m = re.search(r'var token = encrypt\("([^"]+)"\)', page or "")
        if not m:
            return False
        try:
            text = self.raw(self.host + "/robot.php",
                            data={"value": self.encrypt(url), "token": self.encrypt(m.group(1))},
                            referer=url)
            print("kankanya verify %s %s" % (url, text[:40]))
            return '"ok"' in (text or "") or "ok" in (text or "")
        except Exception as e:
            print("kankanya verify error %s" % e)
            return False

    # ------------------------------------------------------------------ 解析

    def tree(self, text):
        """统一的 lxml 入口。

        两个坑都得绕开：
        1) 宿主（chaquo + lxml）往下传 Python str 时会走 UTF-32LE 编码，libxml2 看到
           首字节 3C 00 00 00 就判成 UCS-4 little endian，直接抛
           "encoding not supported USC4 little endian" → 必须先转 utf-8 字节。
        2) 传字节后 lxml 会自己嗅探编码，而本站用的是 HTML5 的 <meta charset>，
           libxml2 认不出来会退回 latin-1，中文全成乱码 → 必须显式指定 utf-8 解析器。
        """
        if not text:
            return None
        if isinstance(text, bytes):
            data = text
        else:
            data = str(text).replace("\ufeff", "").replace("\x00", "").encode("utf-8", "ignore")
        if not data:
            return None

        root = self.parse(data)
        if root is not None:
            return root
        try:
            return self.html(data)
        except Exception as e:
            print("kankanya html error %s" % e)
            return None

    @staticmethod
    def parse(data):
        try:
            from lxml import etree
        except Exception as e:
            print("kankanya etree import error %s" % e)
            return None
        try:
            return etree.HTML(data, etree.HTMLParser(encoding="utf-8", recover=True))
        except Exception as e:
            print("kankanya etree error %s" % e)
        try:
            return etree.HTML(data, etree.HTMLParser())
        except Exception as e:
            print("kankanya etree error %s" % e)
            return None

    @staticmethod
    def vidOf(href):
        m = re.search(r"/detail/([^/?#]+)\.html", href or "")
        return m.group(1) if m else ""

    def pageCount(self, root, default=1):
        """取尾页链接里的页码（第 8 段），取不到就看数字翻页的最大值。"""
        try:
            tails = root.xpath('//div[@id="page"]//a[@title="尾页"]/@href')
            if tails:
                path = tails[0].split("?")[0]
                if path.endswith(".html"):
                    path = path[:-5]
                parts = path.split("-")
                if len(parts) > 8 and parts[8].isdigit():
                    return max(1, int(parts[8]))
            nums = [int(n) for n in root.xpath('//div[@id="page"]//a/text()') if str(n).strip().isdigit()]
            return max(nums) if nums else default
        except Exception:
            return default

    def parseList(self, text):
        """列表页统一解析：归档页/首页用 poster 卡片，搜索页用 card 卡片。"""
        videos, seen = [], set()
        if not text:
            return videos
        root = self.tree(text)
        if root is None:
            return videos

        for a in root.xpath('//a[contains(concat(" ",normalize-space(@class)," ")," module-poster-item ")]'):
            vid = self.vidOf(a.get("href"))
            if not vid or vid in seen:
                continue
            seen.add(vid)
            note = a.xpath('.//div[contains(@class,"module-item-note")]/text()')
            title = a.xpath('.//div[contains(@class,"module-poster-item-title")]/text()')
            imgs = a.xpath('.//img/@data-original') or a.xpath('.//img/@src')
            videos.append({
                "vod_id": vid,
                "vod_name": self.clean(a.get("title")) or self.clean(title[0] if title else ""),
                "vod_pic": self.picOf(imgs),
                "vod_remarks": self.clean(note[0] if note else ""),
            })

        for box in root.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," module-card-item ")]'):
            a = box.xpath('.//a[contains(@class,"module-card-item-poster")]')
            if not a:
                continue
            vid = self.vidOf(a[0].get("href"))
            if not vid or vid in seen:
                continue
            seen.add(vid)
            note = box.xpath('.//div[contains(@class,"module-item-note")]/text()')
            title = box.xpath('.//div[contains(@class,"module-card-item-title")]//a//text()')
            imgs = box.xpath('.//img/@data-original') or box.xpath('.//img/@src')
            seasons = box.xpath('.//div[contains(@class,"module-info-item-content")]/text()')
            videos.append({
                "vod_id": vid,
                "vod_name": self.clean("".join(title)),
                "vod_pic": self.picOf(imgs),
                "vod_remarks": self.clean(note[0] if note else "") or self.clean("".join(seasons)),
            })
        return videos

    @staticmethod
    def picOf(imgs):
        for i in imgs or []:
            i = (i or "").strip()
            if i.startswith("http"):
                return i
        return ""

    # ------------------------------------------------------------------ 接口

    def homeContent(self, filter):
        result = {"class": [{"type_id": tid, "type_name": name} for tid, name in self.categories]}
        if not filter:
            return result

        filters = self.loadFilters()
        result["filters"] = {tid: list(groups.values()) for tid, groups in filters.items()
                             if groups and self.isShow(tid)}
        return result
    def loadFilters(self):
        """从归档页真实 DOM 里扒筛选项（6 小时内缓存），失败退回只有年份。"""
        now = time.time()
        if self.filters and now - self.filters_at < 21600:
            return self.filters

        found = {}
        for tid, _ in self.categories:
            if not self.isShow(tid):
                continue
            root = self.tree(self.http(self.catUrl(tid, 1, {})))
            if root is None:
                continue
            found = self.parseFilters(root)
            if found:
                break
        if not found:
            years = [{"n": "全部", "v": ""}] + [{"n": str(y), "v": str(y)} for y in range(2026, 2009, -1)]
            found = {"year": {"key": "year", "name": "年份", "value": years}}

        self.filters = {tid: found for tid, _ in self.categories}
        self.filters_at = now
        return self.filters

    def parseFilters(self, root):
        """一个 category 的筛选项对所有 tid 通用，所以这里只解析一次即复用。"""
        groups = {}
        cache = {}
        for box in root.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," module-class-item ")]'):
            links = box.xpath('.//div[contains(@class,"module-item-box")]/a')
            if not links:
                continue
            slot, values = None, []
            for a in links:
                href = a.get("href") or ""
                path = href.split("?")[0]
                if path.endswith(".html"):
                    path = path[:-5]
                parts = [urllib.parse.unquote(p) for p in path.split("-")]
                filled = [(i, p) for i, p in enumerate(parts) if p and i > 0]
                name = self.clean("".join(a.itertext())) or self.clean(a.get("title"))
                if len(filled) == 1:
                    slot = filled[0][0]
                    values.append((name, filled[0][1]))
                elif not filled and name == "全部":
                    values.append((name, ""))
            if slot is None:
                continue
            key = next((k for k, v in self.slots.items() if v == slot), None)
            if not key or any("-" in v for _, v in values):
                continue  # 值里带 "-" 会破坏 12 段规则（如首字母的 0-9），整组丢弃
            if not any(v == "" for _, v in values):
                values.insert(0, ("全部", ""))
            cache[key] = [{"n": n, "v": v} for n, v in values if n]
        for key in ("class", "area", "lang", "year", "order"):
            if cache.get(key):
                groups[key] = {"key": key, "name": self.slot_names[key], "value": cache[key]}
        return groups

    def isShow(self, tid):
        return tid in ("dianying", "juji", "zongyi", "dongman")

    def catUrl(self, tid, pg, extend):
        if not self.isShow(tid):
            return "%s/label/%s.html" % (self.host, tid)
        parts = [""] * 12
        parts[0] = tid
        for key, idx in self.slots.items():
            value = (extend or {}).get(key) or ""
            if value:
                parts[idx] = urllib.parse.quote(str(value), safe="")
        pg = str(pg or "1")
        if pg not in ("", "1"):
            parts[8] = pg
        return "%s/show/%s.html" % (self.host, "-".join(parts))

    def homeVideoContent(self):
        videos = self.parseList(self.http(self.host + "/"))
        if not videos:
            # 首页模板改版时退一步，用电影归档页兜住首屏
            videos = self.parseList(self.http(self.catUrl("dianying", 1, {})))
        return {"list": videos}

    def categoryContent(self, tid, pg, filter, extend):
        pg = int(pg or 1)
        text = self.http(self.catUrl(tid, pg, extend))
        videos = self.parseList(text)
        root = self.tree(text)
        count = self.pageCount(root, 1) if root is not None else 1
        if not self.isShow(tid):
            count = 1  # 榜单页只有一页
        return {"list": videos, "page": pg, "pagecount": count, "limit": 40, "total": 999999}

    def detailContent(self, ids):
        vid = ids[0] if ids else ""
        url = vid if str(vid).startswith("http") else "%s/detail/%s.html" % (self.host, vid)
        text = self.http(url)
        if not text:
            return {"list": []}
        root = self.tree(text)
        if root is None:
            return {"list": []}

        names = root.xpath('//div[contains(@class,"module-info-heading")]/h1')
        if not names:
            names = root.xpath("//h1")
        name = self.clean("".join(names[0].itertext())) if names else ""
        if not name:
            return {"list": []}

        pics = root.xpath('//div[contains(@class,"module-info-poster")]//img/@data-original')
        if not pics:
            pics = root.xpath('//div[contains(@class,"module-info-poster")]//img/@src')

        info = {}
        for item in root.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," module-info-item ")]'):
            label = self.clean("".join(item.xpath('./span[contains(@class,"module-info-item-title")]//text()'))).rstrip("：:")
            if not label:
                continue
            vals = [self.clean("".join(a.itertext())) for a in item.xpath('.//div[contains(@class,"module-info-item-content")]//a')]
            vals = [v for v in vals if v]
            info[label] = "/".join(vals) if vals else self.clean("".join(item.itertext()))[len(label):].lstrip("：: ").strip()

        tags = [self.clean("".join(a.itertext())) for a in root.xpath('//div[contains(@class,"module-info-tag")]//a')]
        tags = [t for t in tags if t]

        content = ""
        nodes = root.xpath('//div[contains(@class,"module-info-introduction-content")]')
        if nodes:
            content = self.clean("".join(nodes[0].itertext()))

        sources = []
        for tab in root.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," module-tab-item ")]'):
            # tab 里是 <span>自营c</span><small>18</small>，只取 span，别把集数拼进线路名
            line = self.clean("".join(tab.xpath(".//span//text()")))
            sources.append(line or self.clean("".join(tab.itertext())))
        panels = root.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," module-play-list ")]')

        play_from, play_url = [], []
        for i, panel in enumerate(panels):
            episodes = []
            for a in panel.xpath('.//a[contains(@class,"module-play-list-link")]'):
                href = (a.get("href") or "").strip()
                if not href:
                    continue
                title = self.clean("".join(a.itertext())) or ("第%d集" % (len(episodes) + 1))
                episodes.append("%s$%s" % (title, urljoin(self.host, href)))
            if not episodes:
                continue
            play_from.append(sources[i] if i < len(sources) and sources[i] else ("线路%d" % (i + 1)))
            play_url.append("#".join(episodes))

        if not play_url:
            return {"list": []}

        total = max(len(p.split("#")) for p in play_url)
        vod = {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": self.picOf(pics),
            "type_name": info.get("类型", "") or "/".join(tags[2:]) or "",
            "vod_year": info.get("年份", "") or (tags[0] if tags else ""),
            "vod_area": info.get("地区", "") or (tags[1] if len(tags) > 1 else ""),
            "vod_lang": info.get("语言", ""),
            "vod_remarks": ("%d集" % total) if total > 1 else "正片",
            "vod_actor": info.get("主演", ""),
            "vod_director": info.get("导演", ""),
            "vod_content": content,
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }
        return {"list": [vod]}

    def searchContent(self, key, quick, pg="1"):
        url = "%s/search/-------------.html?wd=%s" % (self.host, urllib.parse.quote(str(key), safe=""))
        videos = self.parseList(self.http(url))
        return {"list": videos, "page": 1, "pagecount": 1, "limit": 40, "total": 999999}

    # ------------------------------------------------------------------ 播放

    def playerContent(self, flag, id, vipFlags):
        url = id if str(id).startswith("http") else urljoin(self.host + "/", id)
        now = time.time()
        hit = self.cache.get(url)
        if hit and hit[0] > now:
            return hit[1]

        result = self.resolve(url)
        self.cache[url] = (now + (600 if result.get("parse") == 0 else 120), result)
        return result

    def resolve(self, url):
        text = self.http(url)
        m = re.search(r"var player_aaaa=(\{.*?\});?\s*</script>", text or "", re.S)
        if not m:
            return {"parse": 1, "playUrl": "", "url": url, "header": self.header(url)}

        try:
            cfg = json.loads(m.group(1))
        except Exception:
            return {"parse": 1, "playUrl": "", "url": url, "header": self.header(url)}

        vid = cfg.get("url") or ""
        media = self.media(vid, url) if vid else ""
        if media:
            return {"parse": 0, "playUrl": "", "url": media, "header": self.header(url)}

        # 解不出来就把 kkyplayer 的解析页丢给宿主嗅探（该页不校验人机验证）
        watch = "%s/kkyplayer/index.php?vid=%s" % (self.host, urllib.parse.quote(vid, safe="")) if vid else url
        return {"parse": 1, "playUrl": "", "url": watch, "header": self.header(url)}

    def media(self, vid, referer):
        """走 kkyplayer 的取流接口，再把返回的混淆地址还原成直链。"""
        try:
            resp = self.api(self.host + "/kkyplayer/api.php", {"vid": vid}, referer)
            data = json.loads(resp or "{}")
        except Exception as e:
            print("kankanya api error %s" % e)
            return ""
        if data.get("code") != 200:
            return ""
        payload = (data.get("data") or {})
        mode = str(payload.get("urlmode") or "1")
        link = self.decode(payload.get("url") or "", mode)
        if link.startswith("http"):
            return link
        return self.locate(link)

    @staticmethod
    def locate(link):
        """解码结果偶尔会带上前缀噪声，从第一个 http 起截断兜底。"""
        m = re.search(r"https?://", link or "")
        return link[m.start():] if m else ""

    def decode(self, value, mode="1"):
        """两种混淆都还原成直链；mode=2 是站点复用给挑战页 encrypt() 的逆运算。"""
        try:
            if mode == "2":
                return self.locate(self.decodePair(value))
            return self.locate(self.decodeTable(value))
        except Exception as e:
            print("kankanya decode error mode=%s %s" % (mode, e))
            return ""

    def decodePair(self, value):
        """urlmode=2：base64 后按 3 步长取第 1/4/7... 位，再按字符表回退 3 位。"""
        raw = self.b64(value).decode("latin1")
        out = []
        for i in range(1, len(raw), 3):
            ch = raw[i]
            idx = self.verify_chars.find(ch)
            out.append(self.verify_chars[(idx + 59) % 62] if idx >= 0 else ch)
        return "".join(out)

    def decodeTable(self, value):
        """urlmode=1：base64 → 循环异或 → base64 → 两段字母置换表 + 载荷。"""
        raw = self.b64(value)
        key = hashlib.md5(b"test").hexdigest()
        stage1 = bytes(b ^ ord(key[i % 32]) for i, b in enumerate(raw))
        stage2 = self.b64(stage1.decode("latin1")).decode("utf-8", "replace")
        chunks = stage2.split("/")
        if len(chunks) < 3:
            return ""
        left = "".join(json.loads(self.b64(chunks[0]).decode("utf-8", "replace")))
        right = "".join(json.loads(self.b64(chunks[1]).decode("utf-8", "replace")))
        body = self.b64("/".join(chunks[2:])).decode("utf-8", "replace")
        out = []
        for ch in body:
            i = right.find(ch)
            out.append(left[i] if i >= 0 else ch)
        return "".join(out)

    @staticmethod
    def b64(value):
        s = re.sub(r"[^A-Za-z0-9+/=]", "", (value or "").replace("-", "+").replace("_", "/"))
        return base64.b64decode(s + "=" * (-len(s) % 4))

    def isVideoFormat(self, url):
        url = str(url or "")
        return ".m3u8" in url or ".mp4" in url

    def manualVideoCheck(self):
        return False

    def localProxy(self, param):
        # 本爬虫不产出代理地址，保留兜底返回，避免宿主调用时取不到元素
        return [404, "text/plain", "not supported"]

    def destroy(self):
        self.cache = {}
        self.filters = {}
