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
import urllib.parse

sys.path.append('..')
from base.spider import Spider


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
        # GET 遇到空响应（限流/抖动）重试一次；POST 不重试，避免重复扣费
        attempts = 2 if method == 'GET' else 1
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
                time.sleep(0.25)
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
    def _line_rank(opt):
        """线路优先级：直链媒体 > 其它 http 地址 > 需二次解析的 ticket。
        站点会给部分线路返回「第0集片头」图片/短视频（token_scenario=series_intro*），一律降为最低。"""
        kind = (opt.get('url_kind') or '').lower()
        url = (opt.get('url') or '').strip()
        scenario = opt.get('token_scenario') or ''
        if scenario.startswith('series_intro'):
            return 0
        low = url.lower()
        if low.startswith('http'):
            if kind in ('m3u8', 'mp4', 'hls', 'flv') or '.m3u8' in low or '.mp4' in low:
                return 3
            return 2
        if kind == 'resolve_ticket' or low.startswith('resolve://'):
            return 1
        return 0

    def _resolve_ticket(self, ticket):
        """ticket -> 真实播放地址。必须带 view=compact（该 query 参与签名），body 只传 ticket。"""
        if not ticket:
            return ''
        data = self._api_post('/v1/playback/resolve-line?view=compact', {'ticket': ticket})
        if not isinstance(data, dict):
            return ''
        line = data.get('line') or {}
        if not line and data.get('url'):
            line = data
        if (line.get('token_scenario') or '').startswith('series_intro'):
            return ''
        url = (line.get('url') or '').strip()
        return url if self._is_valid_video_url(url) else ''

    def _probe_direct(self, url):
        """轻量探活：True=可用，False=明确失败，None=无法判断（按可用处理）"""
        try:
            resp = self.fetch(url, headers={
                'User-Agent': self.headers['User-Agent'],
                'Referer': self.site_url + '/',
            }, timeout=5000)
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
                seen_providers = set()
                for opt in line_options:
                    if not opt.get('url'):
                        continue
                    pid = opt.get('provider_id')
                    if pid in seen_providers:
                        continue
                    seen_providers.add(pid)
                    valid_lines.append(opt)

                # 直链线路优先，需二次解析的 ticket 线路排后
                valid_lines.sort(
                    key=lambda x: (-self._line_rank(x), -int(x.get('preference_weight') or 0))
                )
                valid_lines = valid_lines[:12]

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

        ordered = sorted(
            line_options,
            key=lambda x: (
                not is_selected(x),
                -self._line_rank(x),
                -int(x.get('preference_weight') or 0),
            ),
        )

        direct_cands = []
        ticket_cands = []
        for line in ordered:
            raw_url = (line.get('url') or '').strip()
            if not raw_url:
                continue
            rank = self._line_rank(line)
            if rank <= 0:
                # 片头 / 无可用地址的线路，直接跳过，避免无谓的解析请求
                continue
            if raw_url.startswith('http') and rank >= 2 and self._is_valid_video_url(raw_url):
                direct_cands.append(raw_url)
                continue
            if raw_url.startswith('resolve://'):
                ticket_cands.append(raw_url[10:].strip())
            elif line.get('url_kind') == 'resolve_ticket' and not raw_url.startswith('http'):
                ticket_cands.append(raw_url)

        # 3.1 直链线路优先；有备选时做一次轻量探活，跳过上游已失效的地址
        probe_budget = 2 if len(direct_cands) > 1 else 0
        for url in direct_cands:
            if probe_budget > 0:
                probe_budget -= 1
                if self._probe_direct(url) is False:
                    continue
            return {'parse': 0, 'url': url, 'header': play_header}

        # 3.2 票据线路，POST 换真实地址（最多尝试 3 条，避免无谓请求）
        for ticket in ticket_cands[:3]:
            real_url = self._resolve_ticket(ticket)
            if real_url:
                return {'parse': 0, 'url': real_url, 'header': play_header}

        # 4) 全部线路都不可用（常见于需要登录的内容），退回站点播放页
        return {'parse': 1, 'url': page_url(), 'header': play_header}
