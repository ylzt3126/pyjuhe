# coding=utf-8
# !/usr/bin/python
"""
TVBox / FongMi Python 爬虫：厂长资源（https://www.4kcz.com/）

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
"""
import re
import sys
import json
import time
from urllib.parse import quote, unquote, urljoin, urlparse, parse_qs

sys.path.append('..')
from base.spider import Spider


class Spider(Spider):

    host = "https://www.4kcz.com"
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ua_mobile = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"

    # 首页分类（taxonomy=movie_bt_series 的真实归档路径）
    categories = [
        ("dyy", "电影"),
        ("dianshiju", "电视剧"),
        ("guochanju", "国产剧"),
        ("mj", "美剧"),
        ("hj", "韩剧"),
        ("rj", "日剧"),
        ("hwj", "海外剧"),
        ("dohua", "动画"),
        ("huayudianying", "华语电影"),
        ("meiguodianying", "欧美电影"),
        ("hanguodianying", "韩国电影"),
        ("ribendianying", "日本电影"),
    ]

    # 地区筛选项（movie_bt_cat）
    areas = [
        ("", "全部"), ("zh", "中国大陆"), ("hk", "中国香港"), ("tw", "中国台湾"),
        ("meiguo", "美国"), ("riben", "日本"), ("hggggg", "韩国"), ("yinguo", "英国"),
        ("fg", "法国"), ("dg", "德国"), ("ydl", "意大利"), ("xby", "西班牙"),
        ("yidu", "印度"), ("taiguo", "泰国"), ("adly", "澳大利亚"), ("jnd", "加拿大"),
        ("els", "俄罗斯"), ("qt", "其他"),
    ]

    # 类型筛选项（movie_bt_tags）
    classes = [
        ("", "全部"), ("juqing", "剧情"), ("dozuo", "动作"), ("xiju", "喜剧"),
        ("aiqing", "爱情"), ("kh", "科幻"), ("xuanyi", "悬疑"), ("kingsong", "惊悚"),
        ("kubu", "恐怖"), ("fanzui", "犯罪"), ("maoxian", "冒险"), ("qihuan", "奇幻"),
        ("zhanzheng", "战争"), ("wuxia", "武侠"), ("guzhuang", "古装"), ("jiating", "家庭"),
        ("lishi", "历史"), ("jlpp", "纪录片"), ("doman", "动漫"), ("zrx", "真人秀"),
    ]

    # 年份筛选项（movie_bt_year，取值为分类法 slug，不是纯年份）
    years = [
        ("", "全部"), ("2026", "2026"), ("2025", "2025"), ("2024", "2024"),
        ("2023", "2023"), ("2022", "2022"), ("2021", "2021"), ("2020", "2020"),
        ("2019", "2019"), ("2018", "2018"), ("2017", "2017"), ("2016", "2016"),
        ("2015", "2015"), ("2014", "2014"), ("2013", "2013"), ("2012", "2012"),
        ("2011nd", "2011"), ("20012010nd", "2001~2010"), ("19002000nd", "1900~2000"),
    ]

    def __init__(self):
        self.cache = {}

    def getName(self):
        return "厂长资源"

    def init(self, extend=""):
        try:
            cfg = json.loads(extend) if isinstance(extend, str) and extend.strip().startswith("{") else {}
        except Exception:
            cfg = {}
        if cfg.get("host"):
            self.host = str(cfg["host"]).rstrip("/")
        self.cache = {}

    # ------------------------------------------------------------------ 基础

    def header(self, referer=None, mobile=False):
        return {
            "User-Agent": self.ua_mobile if mobile else self.ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": referer or (self.host + "/"),
        }

    @staticmethod
    def mark(url):
        """给本站地址补一个无害参数。站点的 WAF 只挑战“无参数的干净 URL”，
        带上 f=_all 后请求会直接放行（实测分类页、详情页、播放页均有效）。"""
        if re.search(r"[?&]f=", url):
            return url
        return url + ("&" if "?" in url else "?") + "f=_all"

    def fetchText(self, url, referer=None, retry=3):
        """带 WAF 拦截识别的取页。雷池会随机插入 JS 挑战页，命中就换 UA 退避重试。"""
        target = self.mark(url) if str(url).startswith(self.host) else url
        for i in range(retry + 1):
            try:
                rsp = self.fetch(target, headers=self.header(referer, mobile=(i >= 2)), timeout=15)
                text = rsp.text or ""
                code = getattr(rsp, "status_code", 0)
            except Exception as e:
                text, code = "", 0
                print("4kcz fetch error %s %s" % (target, e))
            if code == 200 and len(text) > 1000 and not self.blocked(text):
                return text
            time.sleep(0.5 * (i + 1))
        return ""

    @staticmethod
    def blocked(text):
        head = text[:4000]
        return ("safeline" in head) or ("slg-box" in head) or ("Access Forbidden" in head)

    def html(self, text):
        return super().html(text) if text else None

    @staticmethod
    def clean(s):
        if not s:
            return ""
        s = re.sub(r"<[^>]+>", "", s)
        s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
        s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&#8217;", "'")
        return re.sub(r"\s+", " ", s).strip()

    @staticmethod
    def vidOf(href):
        m = re.search(r"/movie/(\d+)\.html", href or "")
        return m.group(1) if m else ""

    def pageCount(self, root, default=0):
        try:
            nums = []
            for a in root.xpath('//div[contains(@class,"pagenavi")]//a'):
                t = self.clean("".join(a.itertext()))
                if t.isdigit():
                    nums.append(int(t))
            return max(nums) if nums else default
        except Exception:
            return default

    # ------------------------------------------------------------------ 解析

    def parseList(self, text):
        videos = []
        if not text:
            return videos
        root = self.html(text)
        if root is None:
            return videos
        # 归档页 / 搜索页 / 相关推荐都用 <h3 class="dytit"> 包标题，统一按它定位
        nodes = root.xpath('//li[.//h3[contains(@class,"dytit")]]')
        seen = set()
        for li in nodes:
            a = li.xpath('.//h3[contains(@class,"dytit")]/a')
            if not a:
                continue
            href = a[0].get("href") or ""
            vid = self.vidOf(href)
            if not vid or vid in seen:
                continue
            seen.add(vid)
            name = self.clean(a[0].text) or self.clean("".join(a[0].itertext()))
            if not name:
                alt = li.xpath('.//img/@alt')
                name = self.clean(alt[0]) if alt else ""
            pics = li.xpath('.//img/@data-original') or li.xpath('.//img/@src')
            pic = pics[0].strip() if pics else ""
            remark = li.xpath('.//div[contains(@class,"jidi")]/span/text()')
            remark = self.clean(remark[0]) if remark else ""
            if not remark:
                rate = li.xpath('.//div[contains(@class,"rating")]/text()')
                remark = ("豆瓣 " + self.clean(rate[0])) if rate else ""
            videos.append({"vod_id": vid, "vod_name": name, "vod_pic": pic, "vod_remarks": remark})
        return videos

    # ------------------------------------------------------------------ 接口

    def homeContent(self, filter):
        classes = [{"type_id": tid, "type_name": name} for tid, name in self.categories]
        result = {"class": classes}
        if filter:
            area = [{"n": n, "v": v} for v, n in self.areas]
            cls = [{"n": n, "v": v} for v, n in self.classes]
            year = [{"n": n, "v": v} for v, n in self.years]
            result["filters"] = {
                tid: [
                    {"key": "area", "name": "地区", "value": area},
                    {"key": "class", "name": "类型", "value": cls},
                    {"key": "year", "name": "年份", "value": year},
                ]
                for tid, _ in self.categories
            }
        return result

    def homeVideoContent(self):
        for tid in ("dyy", "dianshiju"):
            videos = self.parseList(self.fetchText(self.catUrl(tid, 1, {})))
            if videos:
                return {"list": videos}
        return {"list": []}

    def catUrl(self, tid, pg, extend):
        url = "%s/movie_bt_series/%s" % (self.host, tid)
        pg = str(pg or "1")
        if pg not in ("", "1"):
            url += "/page/%s" % pg
        params = []
        mapping = (("area", "movie_bt_cat"), ("class", "movie_bt_tags"), ("year", "movie_bt_year"))
        for key, tax in mapping:
            value = (extend or {}).get(key) or ""
            if value:
                params.append("%s=%s" % (tax, quote(str(value), safe="")))
        if params:
            url += "?" + "&".join(params)
        return url

    def categoryContent(self, tid, pg, filter, extend):
        text = self.fetchText(self.catUrl(tid, pg, extend))
        videos = self.parseList(text)
        root = self.html(text) if text else None
        count = self.pageCount(root) if root is not None else 0
        if not count:
            # 没有分页控件：本页不满 20 条即视为末页
            count = int(pg) if len(videos) < 20 else 999
        return {"list": videos, "page": int(pg), "pagecount": count, "limit": 20, "total": 999999}

    def detailContent(self, ids):
        vid = ids[0] if ids else ""
        url = vid if str(vid).startswith("http") else "%s/movie/%s.html" % (self.host, vid)
        text = self.fetchText(url)
        if not text:
            return {"list": []}
        root = self.html(text)

        name = ""
        h1 = root.xpath('//div[contains(@class,"moviedteail_tt")]/h1')
        if not h1:
            h1 = root.xpath("//h1")
        if h1:
            name = self.clean("".join(h1[0].itertext()))

        pics = root.xpath('//div[contains(@class,"dyimg")]//img/@src')
        pic = pics[0].strip() if pics else ""

        # 站点对不存在的 id 会返回伪造页（标题为随机串、无 h1），这里直接丢弃
        if not name:
            return {"list": []}

        info = {}
        for li in root.xpath('//ul[contains(@class,"moviedteail_list")]/li'):
            label = self.clean("".join(li.xpath("./text()"))).rstrip("：:")
            raw = self.clean("".join(li.itertext()))
            if not label or not raw:
                continue
            vals = [self.clean("".join(a.itertext())) for a in li.xpath(".//a")]
            vals = [v for v in vals if v]
            if vals:
                info[label] = "/".join(vals)
            else:
                info[label] = raw[len(label):].lstrip("：: ").strip()

        content = ""
        nodes = root.xpath('//div[contains(@class,"yp_context")]')
        if nodes:
            content = self.clean("".join(nodes[0].itertext()))

        plays = []
        for a in root.xpath('//div[contains(@class,"paly_list_btn")]/a'):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            title = self.clean("".join(a.itertext())) or "播放"
            if href.startswith("/"):
                href = self.host + href
            elif not href.startswith("http"):
                href = urljoin(url, href)
            plays.append("%s$%s" % (title, href))

        vod = {
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": pic,
            "type_name": info.get("类型", ""),
            "vod_year": info.get("年份", ""),
            "vod_area": info.get("地区", ""),
            "vod_lang": info.get("语言", ""),
            "vod_remarks": ("豆瓣 " + info["豆瓣"]) if info.get("豆瓣") else (info.get("上映", "") or info.get("年份", "")),
            "vod_actor": info.get("主演", ""),
            "vod_director": info.get("导演", ""),
            "vod_content": content,
            "vod_play_from": "厂长资源",
            "vod_play_url": "#".join(plays),
        }
        return {"list": [vod]}

    def searchContent(self, key, quick, pg="1"):
        pg = str(pg or "1")
        url = "%s/nimasile/?q=%s&f=_all&p=%s" % (self.host, quote(str(key), safe=""), pg)
        text = self.fetchText(url)
        videos = self.parseList(text)
        root = self.html(text) if text else None
        count = self.pageCount(root, 1) if root is not None else 1
        # 搜索结果页直接给出总数，用它换算更准
        m = re.search(r"搜索结果[:：]\s*<span>(\d+)</span>", text or "")
        if m:
            total = int(m.group(1))
            count = max(1, (total + 19) // 20)
        return {"list": videos, "page": int(pg), "pagecount": count, "limit": 20, "total": 999999}

    def playerContent(self, flag, id, vipFlags):
        url = id if str(id).startswith("http") else urljoin(self.host + "/", id)
        now = time.time()
        hit = self.cache.get(url)
        if hit and hit[0] > now:
            return hit[1]

        text = self.fetchText(url)
        src = ""
        m = re.search(r'<iframe[^>]+src="([^"]+)"', text or "")
        if m:
            src = m.group(1).replace("\\/", "/").replace("&amp;", "&")

        if not text:
            # 播放页被拦：交给宿主解析器嗅探
            result = {"parse": 1, "playUrl": "", "url": url, "header": self.header(url)}
            self.cache[url] = (now + 120, result)
            return result

        media = ""
        if src:
            q = parse_qs(urlparse(src).query)
            raw = (q.get("url") or [""])[0]
            raw = unquote(raw)
            if raw.startswith("http"):
                media = raw
            else:
                ptext = self.fetchText(src, referer=url)
                media = self.findMedia(ptext)

        if not media:
            media = self.findMedia(text)

        result = {"parse": 0, "playUrl": "", "url": media, "header": self.header(url)}
        if not media:
            if src:
                result["parse"] = 1
                result["url"] = src
            else:
                result["url"] = url
        self.cache[url] = (now + 600, result)
        return result

    @staticmethod
    def findMedia(text):
        if not text:
            return ""
        text = text.replace("\\/", "/")
        patterns = (
            r"const\s+mysvg\s*=\s*'([^']+)'",
            r"art\.url\s*=\s*'([^']+)'",
            r"\"url\"\s*:\s*\"(https?://[^\"]+)\"",
            r"(https?://[^'\"\s]+?\.m3u8[^'\"\s]*)",
            r"(https?://[^'\"\s]+?\.mp4[^'\"\s]*)",
        )
        for p in patterns:
            m = re.search(p, text)
            if m:
                return m.group(1).strip()
        return ""

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
