# coding=utf-8
"""
目标站: 搜剧AI (souju.ai)
模板: 影视聚合搜索 / 爬虫播放
站点类型: 综合影视
核心逻辑: HMAC-SHA256 签名 JSON API
支持: 首页, 分类(含二级筛选), 搜索, 详情(多线路), 播放
"""

import sys
import json
import time
import hmac
import hashlib
import os
import re
import urllib.parse

sys.path.append('..')
from base.spider import Spider

# 只保留 url_kind == "resolve_ticket" 的官方线路。
# 部分长尾片源（老片/冷门片）整条记录都没有官方线路，只有第三方 m3u8；
# 此时若不回退，该记录会完全无法播放。置为 False 则严格只显示官方线路。
DIRECT_FALLBACK_WHEN_NO_TICKET = True

# ---------------- 账号会话（全部内联，运行期不写任何文件） ----------------
# 站点官方线路第 1 集免费，第 2 集起要求登录（playback_user_session_required）。
# 下面三项就是默认凭据：Cookie 失效时会用账号密码自动重登，新会话只存在内存里。
# 站点按 ai_movie_browser 设备号维度维护会话，同设备重复登录会顶掉旧会话，
# 这里固定使用爬虫自己的设备号，避免把浏览器上的登录态挤下线。
DEFAULT_ACCOUNT = 'souju3126'
DEFAULT_PASSWORD = 'souju3126'
DEFAULT_DEVICE_ID = 'brw_PrZeOSXqyokMJK1cpOYdFbP4DRJFlkTI6jbGvQFs6g4'
DEFAULT_COOKIE = ('ai_movie_session=ums_hptqvUfGsu50tHYc90nztL38MDC9ehmm8kTLySpRT-Q; '
                  'ai_movie_browser=' + DEFAULT_DEVICE_ID)


class Spider(Spider):
    def init(self, extend=""):
        self.site_url = "https://souju.ai"
        self.api_secret = "f39d73aa7a6426203cdee1ef17b31d3b7ea8c23f4c59c62a3a8aa0f39ee5e79d"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': self.site_url + '/',
            'Origin': self.site_url,
        }
        self.default_pic = 'https://pic.rmb.bdstatic.com/bjh/user/default.png'
        # 会话来源优先级：配置项 cookie > 内置 DEFAULT_COOKIE；
        # 账号密码同理（配置项 > 内置），用于 Cookie 失效时自动重登。
        cookie, account, password = self._pick_credentials(extend)
        self._account = account or DEFAULT_ACCOUNT
        self._password = password or DEFAULT_PASSWORD
        self._device_id = DEFAULT_DEVICE_ID
        cookie = cookie or DEFAULT_COOKIE
        if cookie:
            self.headers['Cookie'] = cookie
        self.categories = {
            'movie': '电影',
            'series': '电视剧',
            'anime': '动漫',
            'variety': '综艺',
            'short_drama': '短剧',
        }
        self.filters = {
            'movie': [
                {'key': 'genre', 'name': '类型', 'value': [
                    {'n': '全部', 'v': ''}, {'n': '动作', 'v': '动作'}, {'n': '喜剧', 'v': '喜剧'},
                    {'n': '爱情', 'v': '爱情'}, {'n': '科幻', 'v': '科幻'}, {'n': '恐怖', 'v': '恐怖'},
                    {'n': '剧情', 'v': '剧情'}, {'n': '战争', 'v': '战争'}, {'n': '动画', 'v': '动画'},
                    {'n': '悬疑', 'v': '悬疑'}, {'n': '犯罪', 'v': '犯罪'}, {'n': '冒险', 'v': '冒险'},
                ]},
                {'key': 'region', 'name': '地区', 'value': [
                    {'n': '全部', 'v': ''}, {'n': '中国大陆', 'v': '中国大陆'}, {'n': '中国香港', 'v': '中国香港'},
                    {'n': '中国台湾', 'v': '中国台湾'}, {'n': '美国', 'v': '美国'}, {'n': '韩国', 'v': '韩国'},
                    {'n': '日本', 'v': '日本'}, {'n': '英国', 'v': '英国'}, {'n': '法国', 'v': '法国'},
                    {'n': '印度', 'v': '印度'}, {'n': '泰国', 'v': '泰国'},
                ]},
                {'key': 'year', 'name': '年份', 'value': [
                    {'n': '全部', 'v': ''}, {'n': '2026', 'v': '2026'}, {'n': '2025', 'v': '2025'},
                    {'n': '2024', 'v': '2024'}, {'n': '2023', 'v': '2023'}, {'n': '2022', 'v': '2022'},
                    {'n': '2021', 'v': '2021'}, {'n': '2020', 'v': '2020'}, {'n': '2019', 'v': '2019'},
                    {'n': '2018', 'v': '2018'}, {'n': '更早', 'v': '2010_2017'},
                ]},
                {'key': 'sort', 'name': '排序', 'value': [
                    {'n': '最新', 'v': 'newest'}, {'n': '最热', 'v': 'hottest'}, {'n': '好评', 'v': 'rating'},
                ]},
            ],
            'series': [
                {'key': 'genre', 'name': '类型', 'value': [
                    {'n': '全部', 'v': ''}, {'n': '古装', 'v': '古装'}, {'n': '都市', 'v': '都市'},
                    {'n': '悬疑', 'v': '悬疑'}, {'n': '武侠', 'v': '武侠'}, {'n': '科幻', 'v': '科幻'},
                    {'n': '战争', 'v': '战争'}, {'n': '喜剧', 'v': '喜剧'}, {'n': '爱情', 'v': '爱情'},
                    {'n': '家庭', 'v': '家庭'}, {'n': '历史', 'v': '历史'}, {'n': '谍战', 'v': '谍战'},
                ]},
                {'key': 'region', 'name': '地区', 'value': [
                    {'n': '全部', 'v': ''}, {'n': '中国大陆', 'v': '中国大陆'}, {'n': '韩国', 'v': '韩国'},
                    {'n': '美国', 'v': '美国'}, {'n': '日本', 'v': '日本'}, {'n': '中国香港', 'v': '中国香港'},
                    {'n': '中国台湾', 'v': '中国台湾'}, {'n': '英国', 'v': '英国'},
                ]},
                {'key': 'year', 'name': '年份', 'value': [
                    {'n': '全部', 'v': ''}, {'n': '2026', 'v': '2026'}, {'n': '2025', 'v': '2025'},
                    {'n': '2024', 'v': '2024'}, {'n': '2023', 'v': '2023'}, {'n': '2022', 'v': '2022'},
                    {'n': '2021', 'v': '2021'}, {'n': '2020', 'v': '2020'}, {'n': '2019', 'v': '2019'},
                ]},
                {'key': 'status', 'name': '状态', 'value': [
                    {'n': '全部', 'v': ''}, {'n': '连载', 'v': 'ongoing'}, {'n': '完结', 'v': 'completed'},
                ]},
            ],
            'anime': [
                {'key': 'genre', 'name': '类型', 'value': [
                    {'n': '全部', 'v': ''}, {'n': '热血', 'v': '热血'}, {'n': '恋爱', 'v': '恋爱'},
                    {'n': '校园', 'v': '校园'}, {'n': '奇幻', 'v': '奇幻'}, {'n': '科幻', 'v': '科幻'},
                    {'n': '搞笑', 'v': '搞笑'}, {'n': '冒险', 'v': '冒险'}, {'n': '运动', 'v': '运动'},
                ]},
                {'key': 'region', 'name': '地区', 'value': [
                    {'n': '全部', 'v': ''}, {'n': '日本', 'v': '日本'}, {'n': '中国', 'v': '中国'},
                    {'n': '欧美', 'v': '欧美'}, {'n': '韩国', 'v': '韩国'},
                ]},
                {'key': 'year', 'name': '年份', 'value': [
                    {'n': '全部', 'v': ''}, {'n': '2026', 'v': '2026'}, {'n': '2025', 'v': '2025'},
                    {'n': '2024', 'v': '2024'}, {'n': '2023', 'v': '2023'}, {'n': '2022', 'v': '2022'},
                ]},
            ],
            'variety': [
                {'key': 'region', 'name': '地区', 'value': [
                    {'n': '全部', 'v': ''}, {'n': '中国大陆', 'v': '中国大陆'}, {'n': '韩国', 'v': '韩国'},
                    {'n': '中国台湾', 'v': '中国台湾'}, {'n': '美国', 'v': '美国'}, {'n': '日本', 'v': '日本'},
                ]},
                {'key': 'year', 'name': '年份', 'value': [
                    {'n': '全部', 'v': ''}, {'n': '2026', 'v': '2026'}, {'n': '2025', 'v': '2025'},
                    {'n': '2024', 'v': '2024'}, {'n': '2023', 'v': '2023'},
                ]},
            ],
            'short_drama': [
                {'key': 'genre', 'name': '类型', 'value': [
                    {'n': '全部', 'v': ''}, {'n': '都市', 'v': '都市'}, {'n': '古装', 'v': '古装'},
                    {'n': '逆袭', 'v': '逆袭'}, {'n': '甜宠', 'v': '甜宠'}, {'n': '赘婿', 'v': '赘婿'},
                ]},
                {'key': 'year', 'name': '年份', 'value': [
                    {'n': '全部', 'v': ''}, {'n': '2026', 'v': '2026'}, {'n': '2025', 'v': '2025'}, {'n': '2024', 'v': '2024'},
                ]},
            ],
        }

    # ---------------- 会话：配置解析 / 本地缓存 / 账号登录 ----------------

    @staticmethod
    def _as_params(extend):
        """把配置项统一解析成 dict：支持 dict / JSON / 查询串。
        注意 Cookie 值自身含 ';'，所以只按 '&' 切分，避免把 Cookie 截断。"""
        if not extend:
            return {}
        if isinstance(extend, dict):
            return dict(extend)
        s = str(extend).strip()
        if not s:
            return {}
        if s.startswith('{'):
            try:
                obj = json.loads(s)
                return obj if isinstance(obj, dict) else {}
            except Exception:
                return {}
        out = {}
        for part in s.split('&'):
            if '=' in part:
                k, v = part.split('=', 1)
                out[k.strip().lower()] = urllib.parse.unquote(v.strip())
        return out

    @classmethod
    def _pick_credentials(cls, extend):
        """返回 (cookie, account, password)。"""
        p = cls._as_params(extend)
        cookie = p.get('cookie') or ''
        account = p.get('account') or p.get('user') or p.get('username') or ''
        password = p.get('password') or p.get('pass') or p.get('pwd') or ''
        if not cookie and not account and not password:
            # 没识别出任何已知键时，才把整串当作裸 Cookie
            s = str(extend or '').strip()
            if s and not s.startswith(('{', '[')) and '=' in s:
                cookie = s
        return cls._clean_cookie(cookie), account.strip(), password.strip()

    @staticmethod
    def _clean_cookie(raw):
        if not isinstance(raw, str):
            return ''
        raw = raw.strip()
        if raw.lower().startswith('cookie:'):
            raw = raw[7:].strip()
        raw = raw.replace('\r', '').replace('\n', '')
        # 配置位有时被填成接口地址等非 Cookie 内容，做基本校验避免污染请求头
        if '=' not in raw or '://' in raw or len(raw) > 4096:
            return ''
        return raw

    @staticmethod
    def _parse_set_cookie(resp):
        """从响应里取会话 Cookie。
        不同内核的 headers 行为不一致（多值 / 逗号拼接 / 单个字符串），
        这里统一拼成文本后按 ai_movie_* 名逐个匹配，后出现的非空值生效。"""
        hdrs = getattr(resp, 'headers', None)
        if hdrs is None:
            return {}
        chunks = []
        for getter in ('getlist', 'get_all'):
            fn = getattr(hdrs, getter, None)
            if callable(fn):
                try:
                    chunks.extend([str(x) for x in (fn('Set-Cookie') or [])])
                except Exception:
                    pass
                break
        if not chunks:
            # 大小写不敏感地扫描全部响应头（有的 CDN 直接下发小写 set-cookie）
            try:
                items = hdrs.items() if hasattr(hdrs, 'items') else []
                for k, v in items:
                    if str(k).strip().lower() == 'set-cookie' and v:
                        chunks.append(str(v))
            except Exception:
                pass
        if not chunks:
            try:
                for key in ('Set-Cookie', 'set-cookie'):
                    one = hdrs.get(key) if hasattr(hdrs, 'get') else None
                    if isinstance(one, str) and one:
                        chunks.append(one)
                        break
                    if one:
                        chunks.extend([str(x) for x in one])
                        break
            except Exception:
                pass
        if not chunks:
            return {}
        text = ' | '.join(chunks)
        jar = {}
        for m in re.finditer(r'(ai_movie_[a-z0-9_]+)[=:]\s*([^;,\s|]*)', text, re.I):
            name, value = m.group(1).strip(), m.group(2).strip()
            if value:
                jar[name] = value
        return jar

    def _merge_cookie(self, new_jar, base=None):
        pairs = {}
        for part in str(base if base is not None else self.headers.get('Cookie', '')).split(';'):
            if '=' in part:
                k, v = part.split('=', 1)
                if k.strip():
                    pairs[k.strip()] = v.strip()
        for k, v in (new_jar or {}).items():
            if v:
                pairs[k] = v
            else:
                pairs.pop(k, None)
        return '; '.join('{0}={1}'.format(k, v) for k, v in pairs.items())

    def _login(self):
        """用账号密码登录取会话，成功后只更新内存里的请求头（不落盘）。"""
        if not self._account or not self._password:
            return False
        path = '/v1/users/password/login'
        headers = self._sign_headers('POST', path)
        # 用爬虫自己的设备号登录，避免顶掉浏览器上的会话
        device = getattr(self, '_device_id', '') or DEFAULT_DEVICE_ID
        base_cookie = 'ai_movie_browser=' + device
        headers['Cookie'] = base_cookie
        headers['Content-Type'] = 'application/json'
        body = json.dumps({
            'account': self._account,
            'password': self._password,
        }, ensure_ascii=False).encode('utf-8')
        try:
            resp = self._fetch_post(self.site_url + path, headers, body)
        except Exception:
            return False
        code = getattr(resp, 'status_code', 0)
        if code and int(code) >= 400:
            return False
        jar = self._parse_set_cookie(resp)
        if not jar:
            return False
        if 'ai_movie_browser' not in jar:
            jar['ai_movie_browser'] = device
        else:
            # 服务端可能回发自己的设备号，记下来供下次登录复用
            self._device_id = jar['ai_movie_browser']
        cookie = self._merge_cookie(jar, base='')
        if '=' not in cookie:
            return False
        self.headers['Cookie'] = cookie
        gated = getattr(self, '_gated_tokens', None)
        if gated:
            gated.clear()
        return True

    def _sign_headers(self, method, path_with_search):
        ts = str(int(time.time() * 1000))
        nonce = os.urandom(16).hex()
        msg = '{0}\n{1}\n{2}\n{3}'.format(method, path_with_search, ts, nonce)
        sig = hmac.new(self.api_secret.encode('utf-8'), msg.encode('utf-8'), hashlib.sha256).hexdigest()
        return {
            **self.headers,
            'x-ai-movie-timestamp': ts,
            'x-ai-movie-nonce': nonce,
            'x-ai-movie-signature': sig,
        }

    @staticmethod
    def _to_json(resp):
        if resp is None:
            return {}
        try:
            raw = getattr(resp, 'content', None)
            if isinstance(raw, bytes) and raw:
                try:
                    return json.loads(raw.decode('utf-8', 'ignore'))
                except Exception:
                    return {}
        except Exception:
            pass
        try:
            text = getattr(resp, 'text', None)
            if text:
                try:
                    return json.loads(text)
                except Exception:
                    return {}
        except Exception:
            pass
        return {}

    def _fetch_post(self, url, headers, body):
        # 不同 TVBox 内核的 fetch 对 POST 支持不一致，逐级降级
        err = None
        for kwargs in ({'data': body, 'method': 'POST'},
                       {'data': body},
                       {'body': body, 'method': 'POST'}):
            try:
                return self.fetch(url, headers=headers, timeout=15000, **kwargs)
            except TypeError as e:
                err = e
                continue
        post = getattr(self, 'post', None)
        try:
            if callable(post):
                return post(url, headers=headers, data=body, timeout=15000)
        except TypeError:
            return post(url, headers=headers, data=body)
        if err:
            raise err
        raise RuntimeError('unsupported post')

    def _request(self, method, path, payload=None):
        url = self.site_url + path
        headers = self._sign_headers(method, path)
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        # GET 遇到空响应（限流/抖动）退避重试；POST 不重试，避免重复扣费
        attempts = 3 if method == 'GET' else 1
        for i in range(attempts):
            try:
                if method == 'GET':
                    resp = self.fetch(url, headers=headers, timeout=15000)
                else:
                    resp = self._fetch_post(url, headers, body)
                data = self._to_json(resp)
            except Exception:
                data = {}
            if data:
                return data
            if i + 1 < attempts:
                time.sleep(0.3 if i == 0 else 1.0)
        return {}

    def _api_get(self, path):
        return self._request('GET', path)

    def _api_post(self, path, payload):
        return self._request('POST', path, payload)

    def _parse_card(self, card):
        vid = card.get('id', '') or ''
        name = card.get('title', '') or ''
        pic = card.get('poster_url', '') or ''
        remark = card.get('remarks', '') or ''
        year = str(card.get('year', '')) if card.get('year') else ''
        area = card.get('area', '') or ''
        genres = card.get('genres', [])
        type_name = ' / '.join(genres[:3]) if genres else ''
        return {
            'vod_id': vid,
            'vod_name': name,
            'vod_pic': pic if pic else self.default_pic,
            'vod_remarks': remark,
            'vod_year': year,
            'vod_area': area,
            'vod_type': type_name,
        }

    def _calc_pagecount(self, pag, page, limit):
        total = pag.get('total', 0)
        if total and limit:
            return (total + limit - 1) // limit
        if pag.get('has_more'):
            return page + 1
        return page

    def _is_valid_video_url(self, url):
        if not url or not isinstance(url, str):
            return False
        url = url.strip()
        if not url.lower().startswith('http'):
            return False
        # 去掉 query 再看扩展名，避免被 CDN 签名参数干扰
        path = url.lower().split('?')[0].split('#')[0]
        for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.svg', '.ico']:
            if ext in path:
                return False
        for bad in ['/image.', 'image.image', '~tplv-']:
            if bad in path:
                return False
        return True

    @staticmethod
    def _ticket_of(opt):
        """取出 resolve_ticket 线路里的票据；非该类型返回空串。"""
        if (opt.get('url_kind') or '') != 'resolve_ticket':
            return ''
        url = (opt.get('url') or '').strip()
        if url.startswith('resolve://'):
            return url[10:].strip()
        if url and not url.startswith('http'):
            return url
        return ''

    def _resolve_ticket(self, ticket):
        """ticket -> (真实播放地址, 是否需要登录)。
        必须带 view=compact（该 query 参与签名），body 只传 ticket。"""
        if not ticket:
            return '', False
        path = '/v1/playback/resolve-line?view=compact'
        headers = self._sign_headers('POST', path)
        headers['Content-Type'] = 'application/json'
        body = json.dumps({'ticket': ticket}, ensure_ascii=False).encode('utf-8')
        try:
            resp = self._fetch_post(self.site_url + path, headers, body)
        except Exception:
            return '', False
        data = self._to_json(resp)
        code = getattr(resp, 'status_code', 0)
        biz = ''
        if isinstance(data, dict):
            biz = str(data.get('code') or '')
        if code == 401 or biz.startswith('playback_user_session') or biz == 'login_required':
            return '', True
        line = (data or {}).get('line') if isinstance(data, dict) else None
        if not line and isinstance(data, dict) and data.get('url'):
            line = data
        if not isinstance(line, dict):
            return '', False
        real = (line.get('url') or '').strip()
        if real and self._is_valid_video_url(real):
            return real, False
        return '', False

    def _probe_direct(self, url):
        """轻量探活：True=可用，False=明确失败，None=无法判断（按可用处理）。
        只用于 m3u8 清单，避免把整部 mp4 拉下来。"""
        if '.m3u8' not in (url or '').lower():
            return None
        try:
            resp = self.fetch(url, headers={
                'User-Agent': self.headers['User-Agent'],
                'Referer': self.site_url + '/',
            }, timeout=6000)
        except Exception:
            return None
        code = getattr(resp, 'status_code', None)
        if code is None:
            return None
        try:
            code = int(code)
        except Exception:
            return None
        return 200 <= code < 400

    def homeContent(self, filter):
        categories = [{'type_id': k, 'type_name': v} for k, v in self.categories.items()]
        data = self._api_get('/v1/feed/home')
        videos = []
        seen = set()
        for sec in data.get('sections', []):
            for card in sec.get('cards', []):
                vid = card.get('id', '')
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                videos.append(self._parse_card(card))
        filters = {}
        for k, v in self.categories.items():
            if k in self.filters:
                filters[k] = self.filters[k]
        return {'class': categories, 'list': videos[:30], 'filters': filters}

    def homeVideoContent(self):
        data = self._api_get('/v1/feed/home')
        videos = []
        seen = set()
        for sec in data.get('sections', []):
            for card in sec.get('cards', []):
                vid = card.get('id', '')
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                videos.append(self._parse_card(card))
        return {'list': videos[:30]}

    def categoryContent(self, tid, pg, filter, extend):
        page = int(pg) if pg else 1
        limit = 30
        params = ['kind={0}'.format(tid), 'page={0}'.format(page), 'limit={0}'.format(limit)]
        if extend:
            if extend.get('genre'):
                params.append('genre={0}'.format(urllib.parse.quote(extend['genre'])))
            if extend.get('region'):
                params.append('region={0}'.format(urllib.parse.quote(extend['region'])))
            if extend.get('year'):
                y = extend['year']
                if '_' in y:
                    parts = y.split('_')
                    params.append('year_from={0}&year_to={1}'.format(parts[0], parts[1]))
                else:
                    params.append('year={0}'.format(y))
            if extend.get('sort'):
                params.append('sort={0}'.format(extend['sort']))
            if extend.get('status'):
                params.append('status={0}'.format(extend['status']))
        path = '/v1/browse/catalog?' + '&'.join(params)
        data = self._api_get(path)
        cards = data.get('cards', []) or []
        videos = [self._parse_card(c) for c in cards if c.get('id')]
        pag = data.get('pagination', {}) or {}
        total = pag.get('total', 0) or len(videos)
        pagecount = self._calc_pagecount(pag, page, limit)
        return {
            'list': videos,
            'page': page,
            'pagecount': pagecount,
            'limit': limit,
            'total': total,
        }

    def searchContent(self, key, quick, pg='1'):
        page = int(pg) if pg else 1
        limit = 30
        encoded = urllib.parse.quote(key)
        path = '/v1/browse/catalog?q={0}&page={1}&limit={2}'.format(encoded, page, limit)
        data = self._api_get(path)
        cards = data.get('cards', []) or []
        videos = [self._parse_card(c) for c in cards if c.get('id')]
        pag = data.get('pagination', {}) or {}
        total = pag.get('total', 0) or len(videos)
        pagecount = self._calc_pagecount(pag, page, limit)
        return {
            'list': videos,
            'page': page,
            'pagecount': pagecount,
            'limit': limit,
            'total': total,
        }

    def detailContent(self, ids):
        if not ids:
            return {'list': []}
        vid = ids[0]
        data = self._api_get('/v1/catalog/{0}'.format(vid))
        if not data or 'id' not in data:
            # 详情拉取失败（限流/下架）时返回占位条目，避免列表项被整体丢弃
            return {'list': [{
                'vod_id': vid,
                'vod_name': vid,
                'vod_pic': self.default_pic,
                'vod_content': '详情获取失败，请稍后重试。',
                'vod_play_from': '默认线路',
                'vod_play_url': '播放${0}/player/{1}'.format(self.site_url, vid),
            }]}
        title = data.get('title', '') or ''
        pic = data.get('poster_url', '') or self.default_pic
        content = data.get('description', '') or ''
        actors = data.get('actors', [])
        actor = ' / '.join(actors[:20]) if actors else ''
        directors = data.get('directors', [])
        director = ' / '.join(directors[:10]) if directors else ''
        year = str(data.get('year', '')) if data.get('year') else ''
        area = data.get('area', '') or ''
        genres = data.get('genres', [])
        type_name = ' / '.join(genres[:5]) if genres else ''
        play_from = []
        play_url = []

        def extract_episodes(episodes, provider_id=''):
            ep_list = []
            # id 结构：token@@provider_id@@vod_id，便于 playerContent 在失败时回落到影片页
            suffix = '@@{0}@@{1}'.format(provider_id or '', vid)
            for ep in episodes:
                ep_title = ep.get('title', '') or ''
                if not ep_title:
                    num = ep.get('number')
                    if num is not None:
                        ep_title = '第{0}集'.format(num)
                    else:
                        ep_title = '播放'
                token = ep.get('token', '')
                if not token:
                    continue
                ep_list.append('{0}${1}{2}'.format(ep_title, token, suffix))
            return ep_list

        episodes = data.get('episodes', [])
        if not episodes:
            ep_data = self._api_get('/v1/catalog/{0}/episodes'.format(vid))
            episodes = ep_data.get('episodes', [])

        if episodes:
            first_token = ''
            for ep in episodes:
                if ep.get('token'):
                    first_token = ep.get('token')
                    break

            valid_lines = []
            if first_token:
                resolve_data = self._api_get('/v1/playback/resolve/{0}'.format(first_token))
                line_options = resolve_data.get('line_options', []) or []
                # 只保留 resolve_ticket 线路，按接口返回顺序，不去重、不截断
                valid_lines = [opt for opt in line_options if self._ticket_of(opt)]
                if not valid_lines and DIRECT_FALLBACK_WHEN_NO_TICKET:
                    # 长尾片源只有第三方直链，没有官方线路；此时退回直链，否则整条记录无法播放
                    valid_lines = [
                        opt for opt in line_options
                        if (opt.get('url') or '').startswith('http')
                        and self._is_valid_video_url(opt.get('url'))
                    ]

            if valid_lines:
                used_names = set()
                for line in valid_lines:
                    provider_name = line.get('provider_name') or line.get('label') or '默认线路'
                    provider_id = line.get('provider_id') or ''
                    ep_list = extract_episodes(episodes, provider_id)
                    if not ep_list:
                        continue
                    # play_from 与 play_url 必须一一对应，否则解析器会错位取不到线路
                    name = provider_name
                    idx = 2
                    while name in used_names:
                        name = '{0}({1})'.format(provider_name, idx)
                        idx += 1
                    used_names.add(name)
                    play_from.append(name)
                    play_url.append('#'.join(ep_list))

            if not play_from:
                ep_list = extract_episodes(episodes)
                if ep_list:
                    play_from.append('默认线路')
                    play_url.append('#'.join(ep_list))
        else:
            play_from.append('默认线路')
            play_url.append('播放${0}/player/{1}'.format(self.site_url, vid))

        result = [{
            'vod_id': vid,
            'vod_name': title,
            'vod_pic': pic,
            'vod_content': content,
            'vod_actor': actor,
            'vod_director': director,
            'vod_year': year,
            'vod_area': area,
            'vod_type': type_name,
            'vod_play_from': '$$$'.join(play_from),
            'vod_play_url': '$$$'.join(play_url),
        }]
        return {'list': result}

    def playerContent(self, flag, id, vipFlags):
        play_header = {
            'User-Agent': self.headers['User-Agent'],
            'Referer': self.site_url + '/',
        }

        raw_id = id or ''
        if '$' in raw_id:
            raw_id = raw_id.split('$')[-1]
        parts = raw_id.strip().split('@@')
        token = parts[0].strip()
        selected_provider = parts[1].strip() if len(parts) > 1 else ''
        vod_id = parts[2].strip() if len(parts) > 2 else ''

        def page_url():
            return '{0}/player/{1}'.format(self.site_url, vod_id or token)

        if not token:
            return {'parse': 1, 'url': id, 'header': play_header}

        # 1) 已经是直链媒体地址，直接交给播放器
        if token.startswith('http'):
            low = token.lower()
            if low.startswith(self.site_url) and ('/player/' in low or '/v1/' in low):
                # 这是站点页面/接口地址，不是媒体流，交给外部解析
                return {'parse': 1, 'url': token, 'header': play_header}
            if self._is_valid_video_url(token):
                return {'parse': 0, 'url': token, 'header': play_header}
            return {'parse': 1, 'url': token, 'header': play_header}

        # 2) 兼容旧版 20 位十六进制 token（站点早期格式，需补 YJ- 前缀）
        if len(token) == 20 and all(c in '0123456789abcdefABCDEF' for c in token):
            token = 'YJ-' + token

        # 3) 当前 token 形如 av_xxx，一律走 playback/resolve
        cache = getattr(self, '_resolve_cache', None)
        if cache is None:
            cache = self._resolve_cache = {}
        if token in cache:
            line_options = cache[token]
        else:
            resolve_path = '/v1/playback/resolve/{0}'.format(urllib.parse.quote(token, safe=''))
            resolve_data = self._api_get(resolve_path)
            line_options = (resolve_data or {}).get('line_options') or []
            if line_options:
                if len(cache) > 200:
                    cache.clear()
                cache[token] = line_options
        if not line_options:
            return {'parse': 1, 'url': page_url(), 'header': play_header}

        def is_selected(opt):
            if selected_provider:
                if opt.get('provider_id') == selected_provider:
                    return True
                if opt.get('play_from') == selected_provider:
                    return True
                if opt.get('provider_name') == selected_provider:
                    return True
            if flag and opt.get('provider_name') == flag:
                return True
            return False

        # 站点对部分内容要求登录：缓存已判定为受限的 token，避免每次播放都撞一串 401
        gated = getattr(self, '_gated_tokens', None)
        if gated is None:
            gated = self._gated_tokens = set()
        if token in gated:
            return {'parse': 1, 'url': page_url(), 'header': play_header}

        ticket_cands = []
        for line in line_options:
            ticket = self._ticket_of(line)
            if ticket:
                ticket_cands.append((line, ticket))

        if ticket_cands:
            # 有官方线路时只走官方线路，保持接口顺序，仅把「用户所选线路」提到最前
            cands = ticket_cands
            mode = 'ticket'
        else:
            # 长尾片源没有官方线路，退回直链，否则整条记录无法播放
            if not DIRECT_FALLBACK_WHEN_NO_TICKET:
                return {'parse': 1, 'url': page_url(), 'header': play_header}
            mode = 'direct'
            cands = []
            for line in line_options:
                url = (line.get('url') or '').strip()
                if url.startswith('http') and self._is_valid_video_url(url):
                    cands.append((line, url))
        if not cands:
            return {'parse': 1, 'url': page_url(), 'header': play_header}

        cands.sort(key=lambda x: 0 if is_selected(x[0]) else 1)  # 稳定排序，其余仍是接口顺序

        tried = 0
        relogin_tried = False
        for line, val in cands:
            if mode == 'direct':
                if tried >= 2:
                    break
                tried += 1
                if tried < len(cands) and self._probe_direct(val) is False:
                    continue
                return {'parse': 0, 'url': val, 'header': play_header}

            if tried >= 6:
                break
            tried += 1
            real_url, need_login = self._resolve_ticket(val)
            if need_login:
                # 会话失效/未登录：有账号就先自动登录一次，再重试当前线路
                if not relogin_tried and self._account and self._password and self._login():
                    relogin_tried = True
                    real_url, need_login = self._resolve_ticket(val)
                    if real_url:
                        return {'parse': 0, 'url': real_url, 'header': play_header}
                    if not need_login:
                        continue
                # 官方线路占绝大多数，一条 401 基本可判定该集需要登录
                gated.add(token)
                break
            if not real_url:
                continue
            # m3u8 清单做一次轻量探活，跳过已失效的 CDN；mp4 不探测，避免整片下载
            if tried < len(cands) and self._probe_direct(real_url) is False:
                continue
            return {'parse': 0, 'url': real_url, 'header': play_header}

        # 4) 全部线路都不可用（需要登录 / 无票），退回站点播放页
        return {'parse': 1, 'url': page_url(), 'header': play_header}
