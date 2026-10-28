# coding=utf-8
"""
目标站: 4kcz.com（厂长资源 / WordPress）
策略: 页面解析
  搜索  : GET /nimasile?q=关键词 -> 抓 /movie/{id}.html
  详情  : GET /movie/{id}.html   -> 抓 /v_play/... 播放链接
  播放  : GET /v_play/...        -> 从 iframe src 的 url= 参数提取 m3u8
骨架: init → homeContent / homeVideoContent → categoryContent
       → searchContent → detailContent → playerContent
"""

import sys
import re
import urllib.parse
import html as _html

sys.path.append('..')
try:
    from base.spider import Spider
except ImportError:
    class Spider:
        pass

try:
    import requests as _rq
except Exception:
    _rq = None

BASE = "https://www.4kcz.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _text(s):
    """去标签、反转义、压缩空白"""
    s = re.sub(r'<[^>]+>', '', s or '')
    s = _html.unescape(s)
    return re.sub(r'\s+', ' ', s).strip()


def _abs(href):
    if not href:
        return ''
    if href.startswith('http'):
        return href
    return BASE.rstrip('/') + '/' + href.lstrip('/')


def _title_from_html(html, fallback=''):
    """从 <title> 提取标题，去掉站点后缀"""
    m = re.search(r'<title[^>]*>(?P<t>.*?)</title>', html or '', re.S | re.I)
    if not m:
        return fallback
    t = _text(m.group('t'))
    # 常见分隔符：_ - | ——
    for sep in ['_', ' - ', '|', '—', '–']:
        if sep in t:
            t = t.split(sep)[0].strip()
            break
    return t or fallback


class Spider(Spider):
    def init(self, extend=""):
        self.headers = {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": BASE + "/",
        }
        self._nav_cache = None
        return ""

    # ------------------------------------------------------------------ #
    # 基础 HTTP
    # ------------------------------------------------------------------ #
    def _get(self, url, timeout=12):
        if _rq is not None:
            try:
                r = _rq.get(url, headers=self.headers, timeout=timeout)
                return r.status_code, r.text or ''
            except Exception:
                return 0, ''
        import urllib.request, urllib.error
        req = urllib.request.Request(url, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode('utf-8', 'ignore')
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode('utf-8', 'ignore')
        except Exception:
            return 0, ''

    # ------------------------------------------------------------------ #
    # 首页
    # ------------------------------------------------------------------ #
    def homeContent(self, filter=False):
        # 分类：从首页导航抓 /movie_category/ 或 /category/ 链接；抓不到就用常见分类兜底
        classes = self._nav_classes()
        if not classes:
            classes = [
                {"type_id": "movie", "type_name": "电影"},
                {"type_id": "tv", "type_name": "电视剧"},
                {"type_id": "anime", "type_name": "动漫"},
                {"type_id": "variety", "type_name": "综艺"},
            ]
        return {"class": classes, "filters": {}}

    def _nav_classes(self):
        if self._nav_cache is not None:
            return self._nav_cache
        code, html = self._get(BASE + '/')
        out, seen = [], set()
        if code == 200 and html:
            # 常见分类路径：/movie_category/xxx 或 /category/xxx 或 /vodtype/xxx
            for m in re.finditer(
                    r'<a[^>]+href="(?P<href>[^"]*?/(?:movie_category|category|vodtype)/[^"]+)"[^>]*>(?P<name>.*?)</a>',
                    html, re.S | re.I
            ):
                href = m.group('href')
                name = _text(m.group('name'))
                if not name or name in seen:
                    continue
                # 用路径段作为 type_id，便于 categoryContent 直接拼 URL
                seg = href.strip('/').split('/')
                tid = '/'.join(seg[-2:]) if len(seg) >= 2 else href.strip('/')
                if tid in seen:
                    continue
                seen.add(name)
                seen.add(tid)
                out.append({"type_id": tid, "type_name": name})
        self._nav_cache = out[:20]
        return self._nav_cache

    def homeVideoContent(self):
        code, html = self._get(BASE + '/')
        videos = self._parse_article_list(html, limit=30)
        return {"list": videos}

    def _parse_article_list(self, html, limit=40):
        """从列表页/首页抓文章卡片：链接 /movie/{id}.html + 标题 + 封面"""
        out, seen = [], set()
        if not html:
            return out
        # 用一个相对宽松的模式：先定位所有 /movie/{id}.html 的 <a>，再回溯标题/图片
        for m in re.finditer(
                r'<a[^>]+href="(?P<href>[^"]*?/movie/(?P<id>\d+)\.html)"[^>]*>(?P<inner>.*?)</a>',
                html, re.S | re.I
        ):
            vid = m.group('id')
            if vid in seen:
                continue
            inner = m.group('inner') or ''
            # 标题：优先 h2/h3/h4，其次 title 属性，其次 a 文本
            name = ''
            tm = re.search(r'<h\d[^>]*>(?P<t>.*?)</h\d>', inner, re.S | re.I)
            if tm:
                name = _text(tm.group('t'))
            if not name:
                tm = re.search(r'title="(?P<t>[^"]+)"', inner, re.I)
                if tm:
                    name = _text(tm.group('t'))
            if not name:
                name = _text(inner)
            # 封面
            pic = ''
            pm = re.search(r'<img[^>]+(?:data-src|data-original|src)="(?P<p>[^"]+)"', inner, re.I)
            if pm:
                pic = _abs(pm.group('p'))
            if not name:
                continue
            seen.add(vid)
            out.append({
                "vod_id": vid,
                "vod_name": name,
                "vod_pic": pic,
                "vod_remarks": "",
            })
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------ #
    # 分类
    # ------------------------------------------------------------------ #
    def categoryContent(self, tid, pg=1, filter=False, extend=""):
        page = int(pg) if pg else 1
        # tid 形如 "movie_category/xxx" 或 "category/xxx"
        path = tid.strip('/')
        if not path:
            return {"list": [], "page": page, "pagecount": 0, "limit": 0, "total": 0}
        if page > 1:
            url = "{0}/{1}/page/{2}/".format(BASE, path, page)
        else:
            url = "{0}/{1}/".format(BASE, path)
        code, html = self._get(url)
        videos = self._parse_article_list(html, limit=40)
        pagecount = page + (1 if videos else 0)
        return {"list": videos, "page": page, "pagecount": pagecount,
                "limit": len(videos), "total": len(videos)}

    # ------------------------------------------------------------------ #
    # 搜索：/nimasile?q=关键词
    # ------------------------------------------------------------------ #
    def searchContent(self, key, quick=False, pg="1"):
        page = int(pg) if pg else 1
        url = "{0}/nimasile?q={1}".format(BASE, urllib.parse.quote(key))
        if page > 1:
            url += "&page={0}".format(page)
        code, html = self._get(url)
        videos = self._parse_article_list(html, limit=40)
        return {"list": videos, "page": page,
                "pagecount": page + (1 if videos else 0),
                "limit": len(videos), "total": len(videos)}

    # ------------------------------------------------------------------ #
    # 详情：/movie/{id}.html -> 提取 /v_play/... 播放链接
    # ------------------------------------------------------------------ #
    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        vid = str(ids[0]) if isinstance(ids, list) else str(ids)
        vid = re.sub(r'[^\d]', '', vid)
        if not vid:
            return {"list": []}

        url = "{0}/movie/{1}.html".format(BASE, vid)
        code, html = self._get(url)
        if code != 200 or not html:
            return {"list": [{
                "vod_id": vid, "vod_name": vid, "vod_pic": "",
                "vod_content": "详情页获取失败",
                "vod_play_from": "", "vod_play_url": "",
            }]}

        title = _title_from_html(html, fallback=vid)

        # 封面：优先 og:image
        pic = ''
        pm = re.search(r'<meta[^>]+property="og:image"[^>]+content="(?P<p>[^"]+)"', html, re.I)
        if pm:
            pic = _abs(pm.group('p'))

        # 简介：优先 meta description
        content = ''
        dm = re.search(r'<meta[^>]+name="description"[^>]+content="(?P<c>[^"]+)"', html, re.I)
        if dm:
            content = _text(dm.group('c'))

        # 播放列表：所有 href 含 /v_play/ 的链接，按出现顺序作为集数
        eps = []
        seen_href = set()
        for m in re.finditer(
                r'<a[^>]+href="(?P<href>[^"]*?/v_play/[^"]+)"[^>]*>(?P<name>.*?)</a>',
                html, re.S | re.I
        ):
            href = _abs(m.group('href'))
            if href in seen_href:
                continue
            seen_href.add(href)
            ep_name = _text(m.group('name'))
            if not ep_name:
                ep_name = "第{0}集".format(len(eps) + 1)
            eps.append("{0}${1}".format(ep_name, href))

        if not eps:
            return {"list": [{
                "vod_id": vid, "vod_name": title, "vod_pic": pic,
                "vod_content": content,
                "vod_play_from": "", "vod_play_url": "",
            }]}

        return {"list": [{
            "vod_id": vid,
            "vod_name": title,
            "vod_pic": pic,
            "vod_content": content,
            "vod_actor": "",
            "vod_director": "",
            "vod_year": "",
            "vod_area": "",
            "vod_play_from": "厂长线路",
            "vod_play_url": "#".join(eps),
        }]}

    # ------------------------------------------------------------------ #
    # 播放：/v_play/... -> iframe src 的 url= 参数 -> m3u8
    # ------------------------------------------------------------------ #
    def playerContent(self, flag, id, vipFlags=None):
        play_header = {
            "User-Agent": UA,
            "Referer": BASE + "/",
        }

        raw = str(id or '').strip()
        if '$' in raw:
            raw = raw.split('$')[-1]
        if not raw:
            return {"parse": 1, "url": "", "header": play_header}

        low = raw.lower()
        # 1) 已经是 m3u8 / mp4 直链
        if low.startswith('http') and ('.m3u8' in low or '.mp4' in low):
            return {"parse": 0, "url": raw, "header": play_header}

        # 2) 相对地址补全
        if not raw.startswith('http'):
            raw = _abs(raw)

        # 3) v_play 页面：抓 iframe 里的 url= 参数
        if '/v_play/' in raw:
            code, html = self._get(raw)
            if code == 200 and html:
                m3u8 = self._extract_m3u8_from_play_page(html)
                if m3u8:
                    return {"parse": 0, "url": m3u8, "header": play_header}
            # 提取失败，交给外部解析器
            return {"parse": 1, "url": raw, "header": play_header}

        # 4) 其他页面地址，也尝试抓一次 m3u8
        code, html = self._get(raw)
        if code == 200 and html:
            m3u8 = self._extract_m3u8_from_play_page(html)
            if m3u8:
                return {"parse": 0, "url": m3u8, "header": play_header}

        return {"parse": 1, "url": raw, "header": play_header}

    @staticmethod
    def _extract_m3u8_from_play_page(html):
        """从播放页 HTML 提取 m3u8：
        优先 iframe src 的 url= 参数（py.php?code=cs&if=1&url=...m3u8），
        其次匹配页面中任意 m3u8 字符串。"""
        if not html:
            return ''
        # 1) iframe src 中的 url= 参数
        for m in re.finditer(r'<iframe[^>]+src="(?P<src>[^"]+)"', html, re.I):
            src = _html.unescape(m.group('src'))
            um = re.search(r'[?&]url=(?P<u>https?://[^&"\']+)', src, re.I)
            if um:
                u = urllib.parse.unquote(um.group('u'))
                if '.m3u8' in u.lower() or '.mp4' in u.lower():
                    return u
        # 2) 页面任意 m3u8（含 JS 字符串、JSON 字段）
        for pat in [
            r'"url"\s*:\s*"(?P<u>https?://[^"]+?\.m3u8[^"]*)"',
            r"url\s*[:=]\s*['\"](?P<u>https?://[^'\"]+?\.m3u8[^'\"]*)['\"]",
            r'(?P<u>https?://[^\s"\'<>]+?\.m3u8[^\s"\'<>]*)',
        ]:
            m = re.search(pat, html, re.I)
            if m:
                return m.group('u').replace('\\/', '/').replace('&amp;', '&')
        return ''

    def localProxy(self, param):
        return None