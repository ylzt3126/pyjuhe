# -*- coding: utf-8 -*-
"""
影视猫
本文件由 SpiderGen 自动生成，可直接放入 T4 / 影视壳 的 py 源目录使用。

#@ ==================== 站点档案（备注信息） ====================
#@ 站点    : https://ysmao.cc
#@ 模式    : api
#@ 分类URL : {host}/api.php/provide/vod/?ac=list&t={tid}&pg={pg}
#@ 详情URL : {host}/api.php/provide/vod/?ac=detail&ids={id}
#@ 搜索URL : {host}/api.php/provide/vod/?wd={key}&pg={pg} (get)
#@ 生成器  : v7.13.19 / 生成时间 2026-10-15 17:54:49
#@ 制作人  : 屿烟
#@ 交流群  :
#@ 备注     : 未实测到播放链接正则，已使用模式默认正则
#@ ============================================================
"""
import re
import os
import json
import time
import base64
import urllib.parse as up
from html import unescape as _unesc

import requests
from base.spider import Spider
#@ 真基类别名：本文件会定义一个与基类同名的子类，子类体里那个标识符会绑定到
#@ 子类自身，所以 hook 里回调基类方法必须用这个别名（用基类名点方法 = 调用自己
#@ = 无限递归 = 栈溢出段错误；super() 在 hook 被注入到非直接子类时会指错）。
from base.spider import Spider as _BaseSpider
def _unescape_doc(text):
    if not text or len(text) < 40:
        return text
    s = text.strip()
    if not (s[0] in '"\'' and s[-1] in '"\''):
        return text
    if '\\"' not in s and '\\u' not in s and '\\/' not in s:
        return text
    for raw in (s, text):
        try:
            got = json.loads(raw)
            if isinstance(got, str) and len(got) > 40:
                return got
        except Exception:
            continue
    return text
def _js_unescape(s):
    if not s:
        return s
    def rep(m):
        h = m.group(1)
        try:
            if h[0] in 'uU':
                return chr(int(h[1:], 16))
            return chr(int(h, 16))
        except Exception:
            return m.group(0)
    # 定长解码：%uXXXX 或 %XX。不能贪婪，否则 %2Fabcd 会被吃掉 "ab"
    return re.sub(r'%(u[0-9a-fA-F]{4}|[0-9a-fA-F]{2})', rep, s)
def _b64decode(s):
    if not s:
        return ''
    t = re.sub(r'[^A-Za-z0-9+/=]', '', s)
    if not t:
        return ''
    t += '=' * (-len(t) % 4)
    try:
        return base64.b64decode(t).decode('utf-8', 'ignore')
    except Exception:
        try:
            return base64.b64decode(t).decode('gbk', 'ignore')
        except Exception:
            return ''
def _play_parts(v):
    if not v:
        return []
    out = []
    if '#' in v:
        for seg in v.split('#'):
            seg = seg.strip()
            if not seg:
                continue
            out.append(seg.rsplit('$', 1)[1] if '$' in seg else seg)
    elif '$' in v:
        out.append(v.rsplit('$', 1)[1])
    else:
        out.append(v)
    return [x.strip() for x in out if x and x.strip()]

class Spider(Spider):
    HOST = 'https://ysmao.cc'
    NAME = '影视猫'
    MODE = 'api'
    CAT_URL = '{host}/api.php/provide/vod/?ac=list&t={tid}&pg={pg}'
    DETAIL_URL = '{host}/api.php/provide/vod/?ac=detail&ids={id}'
    SEARCH_URL = '{host}/api.php/provide/vod/?wd={key}&pg={pg}'
    SEARCH_METHOD = 'get'
    # 搜索关键词的编码方式。
    #   ''      -> URL 百分号编码（默认，MacCMS 那套 ?wd=中文）
    #   'b64'   -> UTF-8 -> base64 -> URL-safe（1234sp.cc 实测：
    #              JS 里 encodeSearchToken = btoa(...).replace(+,-).replace(/, _).replace(=)
    #              搜索路径是 /search/{token}/{pg}.html，直接塞中文会 404）
    #   'hex'   -> UTF-8 -> hex
    SEARCH_ENC = ''
    _B64CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    def _search_key(self, key):
        s = str(key or '')
        enc = getattr(self, 'SEARCH_ENC', '') or ''
        if enc == 'b64':
            # 纯 Python 实现，避免依赖 base64 模块之外的东西（其实标准库够用，
            # 但这里保持"零依赖直觉"，也方便照抄进别的源）
            try:
                raw = s.encode('utf-8')
                out = []
                for i in range(0, len(raw), 3):
                    ch = raw[i:i + 3]
                    pad = 3 - len(ch)
                    n = 0
                    for j, b in enumerate(ch):
                        n |= b << (16 - 8 * j)
                    out.append(self._B64CHARS[(n >> 18) & 63])
                    out.append(self._B64CHARS[(n >> 12) & 63])
                    out.append('=' if pad >= 2 else self._B64CHARS[(n >> 6) & 63])
                    out.append('=' if pad >= 1 else self._B64CHARS[n & 63])
                b = ''.join(out).rstrip('=')
                # URL-safe 变体
                return b.replace('+', '-').replace('/', '_')
            except Exception:
                return up.quote(s)
        if enc == 'hex':
            try:
                return s.encode('utf-8').hex()
            except Exception:
                return up.quote(s)
        return up.quote(s)
    DETAIL_HREF_RE = r'(?P<href>/xq/(\d{1,10})\.html)'
    PLAY_RE = r'href="(?P<href>[^"]*?/index\.php/vod/play/id/\d+/sid/(?P<sid>\d+)/nid/(?P<nid>\d+)[^"]*)"'
    PLAY_RE2 = r'href="(?P<href>[^"]*?/(?:play|player|video)[^"]*?\d+(?:[-/]\d+){1,3}[^"]*?\.html)"[^>]*>\s*(?P<name>[^<]{1,80}?)\s*</a>'
    # 剧集名回退：maccms 常见形态是 <a ... title="播放凡人修仙传1"><span>1</span></a>，
    # 名字在 title 属性里，正文只有集数。PLAY_RE 的 name 组在这种形态下匹配不到。
    NAME_ATTR_RE = r'title="\s*(?:立即播放|在线观看|点击播放|播放|观看|正片)?\s*([^"]{1,60}?)\s*"'
    # 线路名：<div class="module-tab-item tab-item" data-dropdown-value="备用线"><span>备用线</span>
    FROM_NAME_RE = r'data-dropdown-value="([^"]{1,24})"'
    # 结构式线路名（tab-item 容器）。放宽点：
    #   - 名字里可能夹实体噪声（&nbsp;咕咕新线(国内高速)），先按原样取出，后面统一清洗；
    #   - 容器到文本之间可能隔着一层或多层标签、甚至是 i 图标，用 [\s\S]{0,300}? 跨过去；
    #   - 文本允许 24 字，且允许 < 前有空白。
    FROM_SPAN_RE = (r'class="[^"]*(?:module-tab-item|tab-item|playlist-source|'
                    r'source-tab|anthology-tab)[^"]*"[\s\S]{0,300}?>\s*([^<]{1,24}?)\s*<')
    # 线路名兜底：有些模板把线路名放在 a/div 的 title 属性上
    FROM_TITLE_RE = (r'class="[^"]*(?:module-tab-item|tab-item|playlist-source|'
                     r'source-tab|anthology-tab)[^"]*"[^>]*title="([^"]{1,24})"')
    PROXY = ''
    CATEGORIES = [
        # 只放一级分类；二级子分类改为每个一级下面的"类型"筛选条（见 _filters）
        {'type_id': '1', 'type_name': '电影'},
        {'type_id': '2', 'type_name': '电视剧'},
        {'type_id': '3', 'type_name': '综艺'},
        {'type_id': '4', 'type_name': '动漫'},
        {'type_id': '29', 'type_name': '短剧'},
    ]

    # 筛选：全局维度（地区 + 年份），TVBox 按分类展开 {tid: defs}
    FILTERS = []
    FILTER_TPL = ''
    # 站点指纹头（web-sign / X-Client / X-App-Name 等），不带就 403/空数据
    FINGERPRINT = {}
    # 线路健康度落盘位置（跨启动保留）。写不进去不影响使用，只是记忆退化成内存版。
    LINE_DB = '/storage/emulated/0/.spidergen_lines.json'
    #@ 播放 Referer 三态：None=站点自己 / ''=不带 / 'xxx'=指定值。
    #@ 实测 1234sp.cc 的 CDN 是"不带才行"（带主站 Referer 直接 403）。
    PLAY_REFERER = None
    # 最近一次请求失败原因。失败时给壳端/日志留一条可查的线索，
    # 避免"列表空了但不知道为什么"（静默失败最难查）。
    _last_err = ''
    def _apply_fingerprint(self):
        try:
            for _k, _v in (self.FINGERPRINT or {}).items():
                self.session.headers[str(_k)] = str(_v)
        except Exception:
            pass
        return True
    def _filters(self):
        # 【二级分类】把子分类作为每个一级分类下的"类型"筛选条。
        # TVBox / T4 / 影视壳 的 homeContent 只认扁平 class（一行栏目栏），
        # 不支持父子层级；唯一稳定生效的两级形态是：一级放栏目栏，二级放筛选条。
        # 选了「电影」再点「动作片」，等价于请求 t=6。
        SUB = {
            '1': [('动作片', '6'), ('喜剧片', '7'), ('爱情片', '8'), ('科幻片', '9'),
                  ('恐怖片', '10'), ('剧情片', '11'), ('战争片', '12'), ('伦理片', '28')],
            '2': [('国产剧', '13'), ('港台剧', '14'), ('日韩剧', '15'), ('欧美剧', '16')],
            '3': [('内地综艺', '20'), ('日韩综艺', '21'), ('港台综艺', '22'), ('欧美综艺', '23')],
            '4': [('大陆动漫', '24'), ('日韩动漫', '25'), ('港台动漫', '26'), ('欧美动漫', '27')],
        }
        ORDER = [('最新', '最新'), ('最热', '最热'), ('评分', '评分')]
        def mk(sub):
            f = []
            if sub:
                vals = [{'n': '全部', 'v': ''}] + [{'n': n, 'v': v} for n, v in sub]
                f.append({'key': 't', 'name': '类型', 'value': vals})
            f.append({'key': 'order', 'name': '排序', 'value': [{'n': n, 'v': v} for n, v in ORDER]})
            return f
        out = {}
        for c in (self.CATEGORIES or []):
            tid = str(c.get('type_id') or '')
            out[tid] = mk(SUB.get(tid, []))
        return out
    def _filter_url(self, tid, pg, ex):
        tpl = getattr(self, 'FILTER_TPL', '') or ''
        kw = {'host': self.HOST, 'tid': tid, 'pg': pg}
        try:
            names = set(re.findall(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', tpl))
        except Exception:
            names = set()
        for n in names:
            if n in kw:
                continue
            v = ex.get(n, '')
            kw[n] = '' if v is None else str(v)
        def _fallback():
            try:
                return self.CAT_URL.format(host=self.HOST, tid=tid, pg=pg)
            except Exception:
                return ''

        #~ ---- 先在「模板」上删掉空值维度对应的段，再 format ----
        # 必须在 format **之前**删：一旦先 format，`{area}` 就变成空串，
        # 剩下的 `/area/` 只是普通字面量，再也分不清哪些是该删的维度段，
        # 结果拼出 `/area/lang/year/letter/by/page/1.html` 这种必 0 条的垃圾 URL。
        # 判据：某路径段/查询项里的占位符**全部**取空值 -> 整段/整项丢弃；
        # 整段就是一个空占位符时，连它前面那个纯 key 名段（如 `area`）一起丢。
        # 与 analyzer._fill_filter_url 的生成期逻辑保持同一套规则。
        empty_ph = set(n for n in names
                       if n not in ('host', 'tid', 'pg') and not kw.get(n))
        tpl2 = tpl
        if empty_ph:
            proto = ''
            mm = re.match(r'^(https?://|//)', tpl2)
            if mm:
                proto, tpl2 = mm.group(1), tpl2[mm.end():]
            head, sep, query = tpl2.partition('?')
            kept = []
            for seg in head.split('/'):
                seg_ph = set(re.findall(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', seg))
                if seg_ph and seg_ph <= empty_ph:
                    if {seg.strip()} <= set('{%s}' % n for n in seg_ph) \
                            and kept and not re.findall(r'\{', kept[-1]):
                        kept.pop()          # 丢掉 `area` 这样的前缀 key 段
                    continue
                kept.append(seg)
            head = '/'.join([s for s in kept if s != ''])
            q2 = []
            for kv in query.split('&'):
                kv_ph = set(re.findall(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', kv))
                if kv_ph and kv_ph <= empty_ph:
                    continue
                q2.append(kv)
            tpl2 = proto + head + ('?' + '&'.join(q2) if q2 else '')

        try:
            out = tpl2.format(**kw)
        except Exception:
            # 兜底：占位符对不上也绝不抛异常，退回不带筛选的分类页，
            # 宁可不筛，也不能让整个分类接口崩掉。
            return _fallback()
        out = re.sub(r'(?<!:)//+', '/', out)
        out = re.sub(r'/+$', '', out)
        out = out.replace('?&', '?').replace('&&', '&').rstrip('?&')
        return out or _fallback()
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })
        if self.PROXY:
            self.session.proxies.update({'http': self.PROXY, 'https': self.PROXY})
        self.card_re = re.compile('<a\\s[^>]*href="' + self.DETAIL_HREF_RE + '[^"]*"[^>]*>(?P<block>[\\s\\S]{0,4000}?)</a>')
        try:
            self.play_re = re.compile(self.PLAY_RE)
        except Exception:
            self.play_re = re.compile(self.PLAY_RE2)
        self._apply_fingerprint()
    def init(self, extend=''):
        ext = {}
        try:
            ext = json.loads(extend) if extend else {}
        except Exception:
            ext = {}
        if not isinstance(ext, dict):
            ext = {}
        # 登录态自愈 / 指纹头等能力要读 extend，统一存一份
        self._ext = ext
        if isinstance(ext, dict):
            p = ext.get('proxy') or ''
            if p:
                if '://' not in p:
                    p = 'http://' + p
                self.session.proxies.update({'http': p, 'https': p})
        return self.getName()
    def getName(self):
        try:
            return self.NAME
        except Exception:
            return self.__class__.__name__

    #~ ---------------- 基础请求 ----------------
    def _get(self, url, retry=2, **kw):
        kw.setdefault('timeout', 12)
        _last = None
        for _ in range(retry + 1):
            try:
                r = self.session.get(url, **kw)
                _last = r
                if r.status_code == 200:
                    return self._decode(r)
                #@ _get 非 200 也带回正文：CF 站 403 的挑战页正文里写着
                #@ __cf_chl / cf-ray，丢了就既拿不到正文也判不了盾。
                # 旧写法只认 200，非 200 一律返回空串。详情页因此直接掉进
                # 「详情页抓取失败」占位 → App 六接口回归 detailContent FAIL。
                # 现在只要有正文就带回去，交给上层判定（依赖正文本身考虑）。
                try:
                    _t = r.text or ''
                except Exception:
                    _t = ''
                if _t.strip() and r.status_code < 500:
                    return self._decode(r)
                if r.status_code in (403, 429, 503):
                    time.sleep(0.6)
                    continue
            except Exception:
                pass
            time.sleep(0.3)
        if _last is not None:
            try:
                if (_last.text or '').strip() and _last.status_code < 500:
                    return self._decode(_last)
            except Exception:
                pass
        return ''
    def _post(self, url, data=None, retry=2, **kw):
        kw.setdefault('timeout', 12)
        for _ in range(retry + 1):
            try:
                r = self.session.post(url, data=data, **kw)
                if r.status_code == 200:
                    return self._decode(r)
            except Exception:
                pass
            time.sleep(0.3)
        return ''
    @staticmethod
    def _decode(r):
        # 【v7.13.18 修】不要走 r.apparent_encoding（chardet 全量扫描）。
        # 大页面（几十 KB 起）时 chardet 单次探测可跑几十秒，手机上表现为
        # 「分析/播放一直转圈卡死」。改为按 header charset -> meta charset
        # -> UTF-8 的顺序快速定编码，中文站绝大多数是 UTF-8 / GBK。
        raw = b''
        try:
            raw = r.content or b''
        except Exception:
            raw = b''
        enc = ''
        try:
            enc = (r.encoding or '').strip().lower()
        except Exception:
            enc = ''
        if not enc or enc in ('iso-8859-1', 'latin-1', 'ascii', 'none'):
            enc = ''
            head = raw[:4096]
            mm = re.search(br'charset=[\x22\x27]?([A-Za-z0-9_-]+)', head, re.I)
            if mm:
                enc = mm.group(1).decode('ascii', 'ignore').lower()
            if not enc:
                # 无声明：GBK 站的中文（ä-é 开头）用 UTF-8 解会出替换符，
                # 先用 UTF-8 试解，出现替换符再退 GBK。
                _t = raw.decode('utf-8', 'replace')
                enc = 'utf-8' if '\ufffd' not in _t[:2000] else 'gbk'
        if enc in ('gb2312', 'gb18030'):
            enc = 'gbk'
        try:
            txt = raw.decode(enc, 'ignore')
        except Exception:
            try:
                txt = raw.decode('utf-8', 'ignore')
            except Exception:
                txt = r.text or ''
        return _unescape_doc(txt)
    def _abs(self, u):
        if not u:
            return ''
        u = u.strip()
        if u.startswith('//'):
            return 'https:' + u
        if u.startswith('http'):
            return u
        return up.urljoin(self.HOST, u)
    @staticmethod
    def _txt(s):
        if not s:
            return ''
        s = re.sub(r'<[^>]+>', ' ', s)
        s = _unesc(s)
        # 【v7.13.18 修】清 iconfont 私有区字体字符（如 \ue62f）：很多主题把
        # 图标 <i class="iconfont">\ue62f</i> 直接写在线路名/集名旁边，
        # 取文本时就成了「\ue62f 云播资源」，TVBox 里显示成一串豆腐块。
        # 私有区 U+E000-U+F8FF 与常见符号区一并去掉。
        s = re.sub(r'[\ue000-\uf8ff\u200b-\u200f\ufeff]', '', s)
        return re.sub(r'\s+', ' ', s).strip()
    @staticmethod
    def _vid(href):
        if not href:
            return ''
        u = str(href).split('?')[0].split('#')[0]
        seg = u.rstrip('/').rsplit('/', 1)[-1]
        had_ext = bool(re.search(r'\.(?:html?|shtml)$', seg, flags=re.I))
        seg = re.sub(r'\.(?:html?|shtml)$', '', seg, flags=re.I)
        if not seg:
            return u
        if seg.isdigit():
            return seg
        # ① 带 .html 且以数字结尾（MacCMS /movie/index123.html）-> 取尾段数字。
        #    判据：尾段数字前面是**字母数字混排的模板词**（index / vod / detail 这类），
        #    而非 13 位随机串。用"数字段长度 <= 6"来区分：
        #    index123 的 123 是 3 位；A1fVkdZ3C1Nh7 的 7 只有 1 位但**前面带字母**。
        if had_ext:
            m_tail = re.search(r'^(?P<head>[A-Za-z_]+)(?P<num>\d{1,6})$', seg)
            if m_tail:
                return m_tail.group('num')
        # ② 纯字母数字串（含 slug）：整段返回，绝不截尾。
        #    必须在"取末尾数字"之前，否则 A1fVkdZ3C1Nh7 会被截成 7。
        if re.fullmatch(r'[A-Za-z0-9_-]{2,64}', seg):
            return seg
        m = re.search(r'id[/=](\d+)', href)
        if m:
            return m.group(1)
        ns = re.findall(r'\d+', seg)
        return ns[-1] if ns else seg
    @staticmethod
    def _vidsafe(vid):
        v = str(vid or '').strip()
        if not v:
            return ''
        md = re.search(r'/(?:vodplay|voddetail|detail|vod|movie|video|play)/[^/?#]*?([^/?#]+)', v)
        if md:
            v = md.group(1)
        v = re.sub(r'\.(?:html?|shtml)$', '', v, flags=re.I)
        if v.isdigit():
            return v
        m = re.match(r'^(\d{1,10})[-_]\d', v)
        if m:
            return m.group(1)
        return v
    @staticmethod
    def _ep_name(frag, idx):
        ts = [x.strip() for x in re.findall(r'>\s*([^<>]{1,40}?)\s*<', frag or '')]
        ts = [x for x in ts if x]
        if ts:
            return re.sub(r'\s+', ' ', ts[-1])
        return '第%d集' % idx
    def _attr_name(self, frag):
        m = re.search(self.NAME_ATTR_RE, frag or '')
        if not m:
            return ''
        v = re.sub(r'\s+', ' ', _unesc(m.group(1))).strip()
        # 兜底：没剥干净的动词前缀（如"立即播放凡人修仙传"）再削一次
        v = re.sub(r'^(?:立即播放|在线观看|点击播放|播放|观看)\s*', '', v).strip()
        return v
    def _split_by_from(self, ply, n_from):
        if not ply:
            return [ply]
        # ① sid 分组：只在 sid 真有多种取值时才用，单 sid 站保持原样，
        #    绝不把单线路站硬拆成"假多线"。
        sids = []
        for sid, _nm, href in ply:
            s = self._sid_of(sid, href)
            if s and s not in sids:
                sids.append(s)
        if len(sids) > 1:
            groups, order = {}, []
            for item in ply:
                s = self._sid_of(item[0], item[2]) or sids[0]
                if s not in groups:
                    groups[s] = []
                    order.append(s)
                groups[s].append(item)
            # 线路名缺失或重复时统一编号，避免 TVBox 里两条"线路1"
            return [groups[s] for s in order]

        if n_from <= 1:
            return [ply]
        segs, cur = [], []
        prev_nid = None
        for sid, nm, href in ply:
            m = re.search(r'/nid/(\d+)', href)
            nid = int(m.group(1)) if m else None
            if (nid is not None and prev_nid is not None and nid <= prev_nid
                    and len(segs) < n_from - 1):
                segs.append(cur)
                cur = []
            cur.append((sid, nm, href))
            if nid is not None:
                prev_nid = nid
        if cur:
            segs.append(cur)
        if len(segs) != n_from:
            # nid 没回绕（或回绕次数对不上）：按均分处理
            tot = len(ply)
            size = max(1, tot // n_from)
            segs = [ply[i * size:(i + 1) * size] for i in range(n_from)]
            if segs and segs[-1]:
                segs[-1] = ply[(n_from - 1) * size:]
        return [s for s in segs if s]
    @staticmethod
    def _sid_of(sid, href):
        h = str(href or '')

        m = re.search(r'/(?:vod)?play/[^/]*?(\d+)-(\d+)-(\d+)\.html', h, re.I)
        if m:
            return m.group(2)
        s = str(sid or '').strip()
        if s and s not in ('0', 'None'):
            return s
        h = str(href or '')
        m = re.search(r'/sid/(\d+)', h)
        if m:
            return m.group(1)
        m = re.search(r'[-/](\d+)[-/]\d+\.html', h)
        if m:
            return m.group(1)
        return ''
    @staticmethod
    def _info(html, keys):
        for k in keys:
            m = re.search(k + r'\s*[:：]\s*(?:<[^>]+>\s*)*([^<>\n]{1,60})', html)
            if m:
                v = re.sub(r'\s+', ' ', _unesc(m.group(1))).strip()
                if v:
                    return v
            # dl/dt/dd 式：<dt>主演</dt> 后面紧跟 <dd>值</dd>
            m2 = re.search(k + r'\s*</dt>\s*<dd[^>]*>([\s\S]{0,200}?)</dd>', html)
            if m2:
                v = re.sub(r'<[^>]*>', ' ', m2.group(1))
                v = re.sub(r'\s+', ' ', _unesc(v)).strip()
                if v:
                    return v
        return ''

    #~ ---------------- 列表解析 ----------------
    # 站点占位图（懒加载时 src 常是它，真实图在 data-original）
    PLACEHOLDER_RE = re.compile(
        r'(?:no[-_]?(?:pic|img|image|picture)|nopicture|default(?:[-_]?(?:pic|img|image))?'
        r'|placeholder|loading(?:[-_]?(?:img|pic))?|blank|empty|img[-_]?bj|pic[-_]?bj|moren)[^/]*'
        r'\.(?:jpg|jpeg|png|webp|gif|svg)', re.I)
    LAZY_PIC_RE = re.compile(
        r'(?:data-original|data-lazy-src|data-echo|data-url|data-thumb|data-bg|data-image|data-src)'
        r'="(?P<pic>[^"]+)"', re.I)
    IMG_PIC_RE = re.compile(r'<img[^>]*?\ssrc="(?P<pic>[^"]+)"', re.I)
    # 背景图：站点的写法不止 `background-image:url(...)`，
    # 更常见的是**简写属性** `background: url(https://x.png)center no-repeat`，
    # 而且 url 收尾的 `)` 后面紧贴 `center/cover/no-repeat` 等修饰词。
    # 原正则只认 `background-image:`，NT动漫首页 banner 的封面因此整批抓不到。
    # 取法：从 `url(` 起，**到第一个 `)` 或空白为止** —— url() 里不可能含这两者，
    # 所以这一步就天然切掉了后面所有 CSS 修饰词（center/cover/no-repeat…），
    # 不用再去猜尾巴有哪些形态（猜过，`center/cover` 就漏了）。
    BG_PIC_RE = re.compile(
        r'background(?:-image)?\s*:\s*[^;]*?url\(\s*["\']?(?P<pic>[^"\'\s)]+)', re.I)
    # 兜底清理：万一 url() 里带引号且有空格（极少），再清一遍尾部修饰词
    CSS_URL_TAIL_RE = re.compile(r'[)\s]+(?:center|left|right|top|bottom|cover|contain|'
                                 r'no-repeat|repeat|scroll|fixed|'
                                 r'[0-9.]+%|[0-9.]+px)(?:[\s,/].*)?$', re.I)
    # srcset 形如 "a.jpg 1x, b.jpg 2x"，取第一项
    SRCSET_PIC_RE = re.compile(r'srcset="(?P<pic>[^"\s,]+)', re.I)
    # 片名尾部噪声：站点常把 "xxx封面图" 写进 alt/title
    NAME_NOISE_RE = re.compile(r'(?:封面图|封面|海报|剧照|图片|缩略图|官方海报|高清封面)+$')
    # <title> 里的 SEO 话术，命中即从这里截断（只保留前面的真片名）
    # 只在"广告话术的典型上下文"处截断，别把片名本身命中的词也切掉：
    #   `电影下载器` 不能因为含"下载"就被切成"电影"（实测踩过）。
    # 因此要求 ① 高频组合词（高清完整版/在线观看…）直接命中，
    # 或者 ② 单独动词必须紧跟站名分隔符（`下载-站名`、`观看_…`）才算话术。
    NAME_SEO_RE = re.compile(
        r'(?:高清完整版|完整版|高清版|在线观看|免费观看|在线播放|全集|'
        r'迅雷下载|百度网盘|手机观看|剧情介绍'
        r'|(?:迅雷|下载|观看)(?=[_|｜\-—])'
        r'|(?:迅雷|下载|观看)$)')
    NAME_BLACKLIST = ('播放', '详情', '立即播放', '观看', '更多', '首页', '搜索', '收藏')
    # 站级噪声图：提示横幅 / 二维码 / logo / 广告位。这些**每页都有**，
    # 且常常排在真实封面之前，用"页面上任意一张 img"兜底时会先捡到它们。
    # 【实测】1234sp.cc 详情页第一张 img 是 <img class="tip-img"> 的网址二维码，
    # 于是 8 部片的封面全变成同一张京东 CDN 图 —— 列表里一眼就能看出是错的。
    JUNK_PIC_RE = re.compile(
        r'(?:tip-img|qrcode|qr-code|erweima|logo|banner|ad-|ads-|adv-|'
        r'placeholder|default|blank|loading|spacer|icon-|arrow|btn-|'
        r'weixin|wechat|appdown|download-app)', re.I)
    @classmethod
    def _is_placeholder(cls, url):
        u = str(url or '').strip()
        if not u or u.lower().startswith('data:'):
            return True
        return bool(cls.PLACEHOLDER_RE.search(u))
    @classmethod
    def _pick_pic(cls, tag):
        for rx in (cls.LAZY_PIC_RE, cls.IMG_PIC_RE, cls.SRCSET_PIC_RE, cls.BG_PIC_RE):
            for m in rx.finditer(tag or ''):
                u = (m.group('pic') or '').strip()
                if rx is cls.BG_PIC_RE:
                    u = cls.CSS_URL_TAIL_RE.sub('', u).strip()
                else:
                    u = u.split()[0] if u else u
                u = u.strip().strip('"\'')
                if cls._is_placeholder(u):
                    continue
                return u
        return ''
    @classmethod
    def _junk_pic(cls, tag):
        return bool(cls.JUNK_PIC_RE.search(tag or ''))
    @staticmethod
    def _nearby(html, pos, back=1200, fwd=1200):
        s = max(0, int(pos) - back)
        e = min(len(html or ''), int(pos) + fwd)
        return html[s:e]
    @classmethod
    def _sibling_name(cls, frag):
        for rx in (
            r'class="[^"]*(?:v-title|video-?title|banner-?title|slide-?title|'
            r'module-poster-item-title|module-item-title|vod-?name|video-?name|'
            r'thumb-?title|pic-?title)[^"]*"[\s\S]{0,200}?<span[^>]*>([^<]{1,60}?)</span>',
            r'class="[^"]*(?:v-title|video-?title|banner-?title|slide-?title|'
            r'module-poster-item-title|module-item-title|vod-?name|video-?name|'
            r'thumb-?title|pic-?title)[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{1,60}?)\s*<',
        ):
            m = re.search(rx, frag or '', re.I)
            if m:
                v = cls._clean_text_static(m.group(1))
                if v and len(v) >= 2 and not re.match(r'^\d+$', v):
                    return v
        return ''
    @staticmethod
    def _clean_text_static(raw):
        t = re.sub(r'<[^>]*>', ' ', str(raw or ''))
        try:
            t = _unesc(t)
        except Exception:
            pass
        t = t.replace('\u00a0', ' ')
        return re.sub(r'\s+', ' ', t).strip()
    def _fixpic(self, u):
        if not u:
            return ''
        u = self._abs(_unesc(str(u).strip()))
        if not u:
            return ''
        try:
            if 'baidu.com' in up.urlparse(u).netloc.lower():
                sm = re.search(r'[?&]src=(?P<s>[^&]+)', u)
                if sm:
                    s = up.unquote(sm.group('s'))
                    if s.startswith('http'):
                        return s
                    if re.match(r'[\w.-]+\.[a-z]{2,}/', s):
                        return 'https://' + s
        except Exception:
            pass
        return u
    @classmethod
    def _clean_name(cls, s):
        s = (s or '').strip()
        for _ in range(2):
            s = cls.NAME_NOISE_RE.sub('', s).strip()
        # 详情页兜底取的是 <title>，而 maccms 模板的 title 是 SEO 长串：
        #   《片名》番剧高清完整版在线观看_片名高清完整版下载-站名 - 在线日漫
        # 直接塞进 TVBox 会显示整行广告词（gugu3 实测如此，且该站页面里
        # 根本没有 <h1>，只能从 title 收拾）。这里把这类模板话术切掉。
        if s:
            # ① 书名号包起来的才是真片名，优先取它
            bm = re.match(r'^\s*[《【\[]([^》】\]]{1,60})[》】\]]', s)
            if bm:
                return bm.group(1).strip()
            # ② 无书名号：在第一个广告词处截断
            s2 = cls.NAME_SEO_RE.split(s, 1)[0].strip()
            # ③ 下划线/竖线/短横线连接的分段，取第一段（通常是纯片名）
            s2 = re.split(r'[_|｜]', s2, 1)[0].strip()
            if len(s2) >= 2:
                s = s2
        return s
    def _parse_cards(self, html, limit=0):
        raw = []
        for m in self.card_re.finditer(html or ''):
            # 【v7.13.18 修】跳过热门搜索/关键词建议块里的链接：这种块每个页面都有，
            # 点进去是搜索结果而非影片，当成卡片会污染列表（首页/各分类前几条永远一样）。
            # MacCMS 主题常见类名 head_hot_search / pops_list / hot_name 等。
            # 前向 400 字符兜底抓祖先容器类（块首张卡片的 head_hot_search 不在 <a> 内）。
            if re.search(r'class="[^"]*(?:head_hot_search|pops_list|hot_name|search_sug|search_hot|hotsearch|search_tit|suggest_list|hot_key)[^"]*"',
                         m.group(0) + html[max(0, m.start() - 400):m.start()], re.I):
                continue
            href = m.group('href')
            vid = self._vid(href)
            if not vid:
                continue
            block = m.group('block')
            tag = m.group(0)
            pic = self._fixpic(self._pick_pic(tag)) or self._fixpic(self._pick_pic(block))
            nm = re.search(r'title="(?P<t>[^"]{1,80})"', tag) or re.search(r'alt="(?P<t>[^"]{1,80})"', tag)
            name = self._clean_name(self._txt(nm.group('t'))) if nm else ''
            if not name:
                hm = re.search(r'<h[1-6][^>]*>(?P<t>[\s\S]{0,120}?)</h[1-6]>', block, re.I)
                if hm:
                    name = self._clean_name(self._txt(hm.group('t')))
            if not name:
                tm = re.search(r'class="[^"]*(?:vod-?name|video-?name|module-?title|thumb-?title|title|name)[^"]*"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{1,80}?)\s*<', block, re.I)
                if tm:
                    name = self._clean_name(self._txt(tm.group(1)))
            if not name:
                am = re.search(r'<a[^>]*>\s*([^<]{1,80}?)\s*</a>', block)
                if am:
                    name = self._clean_name(self._txt(am.group(1)))
            if not name:
                name = self._clean_name(self._txt(block)[:60])
            if name in self.NAME_BLACKLIST:
                name = ''
            #~ ---- 兄弟节点兜底 ----
            # 轮播/侧栏那种卡：<a href=/detail/id/5514 class=banner style="background:url(x)">
            # </a> 后面才是 <div class=mobile-v-info><span>凡人修仙传</span>。
            # 只读 <a> 内容会两头皆空，片名回退成纯数字 id —— 就是用户反馈的
            # "写出来都会有一个数字占据前面的源"。这里向两侧扩视野补名/补图。
            if not name:
                near = self._nearby(html, m.start(), back=200, fwd=1500)
                sn = self._sibling_name(near)
                if sn:
                    name = self._clean_name(sn)
            # 纯数字片名是"没抓到名字"的显式信号：宁可置空让壳端显示 id，
            # 也不要把它当片名（TVBox 会照原样显示一串数字，观感极差）。
            if name and re.match(r'^\d+$', name):
                name = ''
            if not pic:
                near = self._nearby(html, m.start(), back=120, fwd=1500)
                pic = self._fixpic(self._pick_pic(near))
            rm = re.search(r'class="[^"]*(?:pic-text|pic-tag|pic-remark|note|score|remark|subtitle|text-right|continu|tag)[^"]*"[^>]*>(?P<r>[^<]{1,30})<', block)
            remark = self._txt(rm.group('r')) if rm else ''
            # 备注兜底：排行榜类区块（module-paper-item / 热榜）没有封面，
            # 状态写在 <p>全12集</p> 里，类名不含 remark/note 等关键字。
            if not remark:
                pm = re.search(r'<p[^>]*>\s*([^<]{1,20}?)\s*</p>', block)
                if pm:
                    remark = self._txt(pm.group(1))
            # 评分：有名字 2 分 + 有图 1 分；备注同分时作加分项
            score = (2 if name else 0) + (1 if pic else 0) + (1 if remark else 0)
            raw.append((vid, score, {'vod_id': vid, 'vod_name': name,
                                     'vod_pic': pic, 'vod_remarks': remark}))

        # 同 id 取分最高；分数相同时保留**先出现**的那条（顺序即站点的推荐顺序）
        best = {}
        order = []
        for vid, score, item in raw:
            if vid not in best:
                best[vid] = (score, item)
                order.append(vid)
            elif score > best[vid][0]:
                best[vid] = (score, item)
        out = []
        for vid in order:
            item = best[vid][1]
            # 兜底：连名字都没有时才用 id（此时确实抓不到任何信息）
            if not item.get('vod_name'):
                item['vod_name'] = vid
            out.append(item)
            if limit and len(out) >= limit:
                break
        # 多部片子共用同一张图 => 站点占位图，置空让壳端用自己的默认图
        if len(out) >= 8:
            cnt = {}
            for v in out:
                if v.get('vod_pic'):
                    cnt[v['vod_pic']] = cnt.get(v['vod_pic'], 0) + 1
            for v in out:
                if v.get('vod_pic') and cnt.get(v['vod_pic'], 0) >= 3:
                    v['vod_pic'] = ''
        return self._fill_pics(out)
    def _fill_pics(self, items):
        try:
            need = [v for v in (items or []) if not v.get('vod_pic')]
        except Exception:
            return items
        if not need:
            return items
        cache = getattr(self, '_pic_cache', None)
        if cache is None:
            cache = {}
            try:
                self._pic_cache = cache
            except Exception:
                pass
        # 命中缓存
        todo = []
        for v in need[:40]:
            vid = str(v.get('vod_id') or '')
            if not vid:
                continue
            if vid in cache:
                if cache[vid]:
                    v['vod_pic'] = cache[vid]
                continue
            todo.append((v, vid))
        if not todo:
            return items
        def _one(pair):
            v, vid = pair
            try:
                url = self.DETAIL_URL.format(host=self.HOST, id=vid)
                html = self._get(url)
                if not html:
                    return None
                pic = ''
                pc = re.search(r'class="[^"]*(?:detail-pic|vod-pic|video-pic|lazy|thumb|cover)[^"]*"[\s\S]{0,600}?</?(?:img|a|div|li)[^>]*>', html, re.I)
                if pc:
                    pic = self._fixpic(self._pick_pic(pc.group(0)))
                if not pic:
                    om = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html, re.I)
                    if om:
                        pic = self._fixpic(om.group(1))
                if not pic:
                    for mm in re.finditer(r'<img\b[^>]*>', html, re.I):
                        u = self._fixpic(self._pick_pic(mm.group(0)))
                        if u:
                            pic = u
                            break
                return (vid, pic)
            except Exception:
                return None

        try:
            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=8) as ex:
                for res in ex.map(_one, todo):
                    if not res:
                        continue
                    vid, pic = res
                    cache[vid] = pic or ''
                    if pic:
                        for v in items:
                            if str(v.get('vod_id') or '') == vid and not v.get('vod_pic'):
                                v['vod_pic'] = pic
        except Exception:
            pass
        return items

    # 页码类 URL 里可能出现的参数名（用于识别 ?page=2 这类）
    PAGE_KEYS = r'(?:page|pg|pn|pageno|pageindex|p)'
    # 分页容器：class 里带 page / paginat / pager 的块
    PAGE_BOX_RE = (r'<(?:div|ul|nav|p)[^>]*class="[^"]*(?:page|paginat|pager)[^"]*"[^>]*>'
                   r'([\s\S]{0,3000}?)</(?:div|ul|nav|p)>')
    def _pagecount(self, html, pg, limit, count):
        html = html or ''
        def _mx_from(pats, blob=None):
            mx = 0
            for pat in pats:
                for m in re.finditer(pat, blob if blob is not None else html):
                    try:
                        v = int(m.group(1))
                    except Exception:
                        continue
                    if 1 <= v <= 5000 and v > mx:
                        mx = v
                if mx:
                    return mx
            return 0

        # ① 显式总页数。站点自己报的数最可信，直接采用。
        mx = _mx_from([
            r'共\s*(\d{1,4})\s*页',
            r'(?:pagecount|pageCount|page_count|totalPage|totalPages|total_page)'
            r'\s*[:=]\s*["\']?(\d{1,4})',
            r'data-(?:total|pages|pagecount|page-count)\s*=\s*["\']?(\d{1,4})',
        ])
        if mx:
            return max(mx, pg)

        # ② 分页区里的最大页码。取 max，不是数按钮。
        blob = "\n".join(m.group(1) for m in re.finditer(self.PAGE_BOX_RE, html, re.I))
        if blob:
            mx = _mx_from([
                # ?page=2 / &pg=2
                r'href="[^"]*?[?&]' + self.PAGE_KEYS + r'=(\d{1,4})',
                # /list/1/2.html  （本站形态，原实现漏掉的就是这条）
                r'href="[^"]*?/(?:list|vodtype|show|type)/\d+/(\d{1,4})\.html',
                # /vodshow/1--------2---.html 与 /vodsearch/xx-2---.html
                r'href="[^"]*?-+(\d{1,4})---\.html',
                # /page/2 或 /pg/2
                r'href="[^"]*?/' + self.PAGE_KEYS + r'/(\d{1,4})',
                # 其它 /a/数字-数字.html 形态
                r'href="[^"]*?/\d+/[^"/]*?-(\d{1,4})\.html',
                # JS 渲染分页
                r'changePage\(\s*(\d{1,4})\s*\)',
                # 纯数字按钮（末页常以文字标出，这条兜住数字形态）
                r'>\s*(\d{1,4})\s*<',
            ], blob)
            if mx:
                return max(mx, pg)

        # ③ 全文兜底：分页区类名不标准时，从整页 URL 里找最深页码
        mx = _mx_from([
            r'href="[^"]*?[?&]' + self.PAGE_KEYS + r'=(\d{1,4})',
            r'href="[^"]*?-+(\d{1,4})---\.html',
            r'href="[^"]*?/(?:list|vodtype|show|type)/\d+/(\d{1,4})\.html',
            r'changePage\(\s*(\d{1,4})\s*\)',
        ])
        if mx:
            return max(mx, pg)

        # ④ 有「下一页 / 末页」迹象 -> 至少还能往下翻一页
        if re.search(r'>\s*(?:下一页|下页|次页|Next|»|末页|尾页|最后一页)\s*<', html, re.I):
            return pg + 1

        # ⑤ 满页兜底：本页条数已满 -> 保守认为还有下一页。
        #    宁可多一次空请求，也不能让用户"翻不动"。
        if count >= limit:
            return pg + 1
        return pg

    # 分类链接的两段式匹配：① 先取整个 <a>；② 再从 href 里提 tid。
    # 【为什么要两段】单条大正则要同时管「href 形态」「标签结构」「文本位置」，
    # 任何一条不匹配就整条丢掉。实测单条正则只覆盖 4/11 种真实形态，
    # 尤其抓不到 1dm2.cc 自己的 /list/{tid}/ —— 分类因此只剩分析器硬编码的那几个，
    # 用户看到的现象就是「资源太少、拿不到全站」。
    # 拆成两段后每个条件解耦，13/13 种形态全中（含换行、嵌套标签、&nbsp; 噪声）。
    ANCHOR_RE = r'<a\s[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<txt>[\s\S]{0,200}?)</a>'
    # 分类 URL 只认这些路由形态；尾随数字就是 tid。
    # 注意 [/?&] 而不是 / —— MacCMS 的 ?tid=1 与 /frim/index1 都不带斜杠。
    TID_RE = (r'(?:/(?:show|type|list)/id/|/vodshow/|/vodtype/|/list/|/frim/index'
              r'|[?&]tid=|m=vod-(?:list|type)-id-)(\d+)')
    # 导航栏里的非分类入口，抓到也没用（点了是列表页首页）
    CAT_BLACK = ('首页', '更多', '全部', '最新', '排行', '专题', '留言', '公告')
    def _load_cats(self):
        html = self._get(self.HOST + '/')
        out, seen = [], set()
        try:
            anchors = re.findall(self.ANCHOR_RE, html or '', re.I)
        except Exception:
            anchors = []
        # findall 带命名组时返回的是 tuple 列表，用 finditer 更稳
        try:
            it = re.finditer(self.ANCHOR_RE, html or '', re.I)
        except Exception:
            it = []
        for m in it:
            href = m.group('href') or ''
            tm = re.search(self.TID_RE, href)
            if not tm:
                continue
            tid = tm.group(1)
            if not tid or tid in seen:
                continue
            name = self._txt(m.group('txt'))
            name = re.sub(r'^[\s\u00a0\-\|·]+', '', name or '').strip()
            if not name or len(name) > 20:
                continue
            if name in self.CAT_BLACK:
                continue
            # 纯数字/纯符号当成没抓到名字（去掉这类噪声免得分类名是一串数字）
            if re.fullmatch(r'[\d\W_]+', name):
                continue
            seen.add(tid)
            out.append({'type_id': tid, 'type_name': name})
        return out[:50]

    #~ ---------------- 首页 / 分类 / 搜索（采集 API） ----------------
    # 排序键：站点接口用 order_by 控制排序，值各家不同，这里做一次语义映射。
    # 参考次元城（React SPA + JSON API）写法：hits=最热、update_time=最新、score=高分。
    ORDER_MAP = {
        '最新': 'update_time', '最近更新': 'update_time', '最新更新': 'update_time',
        '最热': 'hits', '热门': 'hits', '人气': 'hits', '播放最多': 'hits',
        '高分': 'score', '评分': 'score', '好评': 'score',
    }
    def _getj(self, url, params=None):
        for _ in range(2):
            try:
                r = self.session.get(url, timeout=12, params=params)
                if r.status_code == 200:
                    try:
                        return r.json()
                    except Exception:
                        # 返回了 200 但不是 JSON：多半是被拦了或跳到验证页
                        self._last_err = '响应不是 JSON（可能被拦/需要验证）'
                        return {}
                self._last_err = 'HTTP %s' % r.status_code
            except Exception as e:
                self._last_err = '%s: %s' % (type(e).__name__, e)
            time.sleep(0.3)
        return {}
    def _jcards(self, data):
        out = []
        if not isinstance(data, dict):
            return out
        def pick(d, *keys):
            for k in keys:
                v = d.get(k)
                if v not in (None, '', [], {}):
                    return v
            return ''
        def rows(o, depth=0):
            if depth > 3 or not isinstance(o, dict):
                return []
            for k in ('list', 'videos', 'items', 'rows', 'data'):
                v = o.get(k)
                if isinstance(v, list):
                    return v
                if isinstance(v, dict):
                    got = rows(v, depth + 1)
                    if got:
                        return got
            return []

        for d in rows(data):
            if not isinstance(d, dict):
                continue
            vid = pick(d, 'vod_id', 'video_id', 'id', 'vid')
            nm = pick(d, 'vod_name', 'title', 'name', 'video_name')
            if vid in (None, '') or not nm:
                continue
            rem = pick(d, 'vod_remarks', 'vod_note', 'remarks', 'remark', 'note')
            if not rem:
                total = pick(d, 'total', 'total_episode', 'episode_count')
                if total:
                    rem = '%s集' % total
            score = pick(d, 'score', 'vod_score', 'rating', 'douban_score')
            pic = pick(d, 'vod_pic', 'pic', 'cover', 'vod_pic_thumb', 'image', 'poster')
            card = {
                'vod_id': str(vid),
                'vod_name': str(nm),
                # 【图片修复】ac=list 列表接口本身不带 vod_pic，先存详情里的，
                # 为空时由 _fill_pics_api 批量回查 ac=detail 补全。
                'vod_pic': self._abs(str(pic or '')),
                'vod_remarks': str(rem or ''),
            }
            if score:
                card['vod_score'] = str(score)
            out.append(card)
        # 【图片修复】列表/首页/搜索结果里 ac=list 不返回封面，批量补一次详情图。
        return self._fill_pics_api(out)

    # 【图片修复】API 模式下补全封面：ac=list 不含 vod_pic，按 id 批量回查
    # ac=detail（一次请求最多 60 个），把封面写回卡片。带 vid 级缓存，
    # 同一部片子翻页/切分类不会重复请求。
    def _fill_pics_api(self, items):
        need = [v for v in (items or []) if not v.get('vod_pic')]
        if not need:
            return items
        cache = getattr(self, '_pic_cache', None)
        if cache is None:
            cache = {}
            try:
                self._pic_cache = cache
            except Exception:
                pass
        todo = []
        for v in need:
            vid = str(v.get('vod_id') or '')
            if not vid:
                continue
            if vid in cache:
                if cache[vid]:
                    v['vod_pic'] = cache[vid]
                continue
            todo.append((v, vid))
        if not todo:
            return items
        ids = ','.join(t[1] for t in todo[:60])
        try:
            data = self._getj(self.DETAIL_URL.format(host=self.HOST, id=ids))
            for d in ((data or {}).get('list') or []):
                vid = str(d.get('vod_id') or '')
                pic = self._abs(d.get('vod_pic') or '')
                cache[vid] = pic or ''
                if pic:
                    for v in items:
                        if str(v.get('vod_id') or '') == vid and not v.get('vod_pic'):
                            v['vod_pic'] = pic
        except Exception:
            pass
        return items
        def pick(d, *keys):
            for k in keys:
                v = d.get(k)
                if v not in (None, '', [], {}):
                    return v
            return ''
        def rows(o, depth=0):
            if depth > 3 or not isinstance(o, dict):
                return []
            for k in ('list', 'videos', 'items', 'rows', 'data'):
                v = o.get(k)
                if isinstance(v, list):
                    return v
                if isinstance(v, dict):
                    got = rows(v, depth + 1)
                    if got:
                        return got
            return []

        for d in rows(data):
            if not isinstance(d, dict):
                continue
            vid = pick(d, 'vod_id', 'video_id', 'id', 'vid')
            nm = pick(d, 'vod_name', 'title', 'name', 'video_name')
            if vid in (None, '') or not nm:
                continue
            rem = pick(d, 'vod_remarks', 'vod_note', 'remarks', 'remark', 'note')
            if not rem:
                total = pick(d, 'total', 'total_episode', 'episode_count')
                if total:
                    rem = '%s集' % total
            score = pick(d, 'score', 'vod_score', 'rating', 'douban_score')
            pic = pick(d, 'vod_pic', 'pic', 'cover', 'vod_pic_thumb', 'image', 'poster')
            card = {
                'vod_id': str(vid),
                'vod_name': str(nm),
                'vod_pic': str(pic or ''),
                'vod_remarks': str(rem or ''),
            }
            if score:
                card['vod_score'] = str(score)
            out.append(card)
        return out
    def _order_by(self, ex):
        for k in ('order', 'sort', 'by', 'order_by'):
            v = str((ex or {}).get(k) or '').strip()
            if v:
                return self.ORDER_MAP.get(v, v)
        return ''
    @staticmethod
    def _jmeta(data):
        if not isinstance(data, dict):
            return 0, 0
        def num(o, *keys):
            for k in keys:
                try:
                    v = int(o.get(k) or 0)
                except Exception:
                    continue
                if v > 0:
                    return v
            return 0
        def find(o, depth=0):
            if depth > 3 or not isinstance(o, dict):
                return 0, 0
            pc = num(o, 'pagecount', 'page_count', 'pages', 'total_page')
            tt = num(o, 'total', 'count', 'total_count', 'totalCount')
            if pc or tt:
                return pc, tt
            inner = o.get('data')
            if isinstance(inner, dict):
                return find(inner, depth + 1)
            return 0, 0

        pc, tt = find(data)
        if not pc and tt:
            pc = (tt + 29) // 30
        return pc, tt
    def homeContent(self, filter):
        cats, lst = (self.CATEGORIES or []), []
        try:
            cats = cats or self._load_cats()
            lst = self._jcards(self._getj(self.CAT_URL.format(host=self.HOST, tid='', pg=1)))
        except Exception as e:
            self._last_err = '%s: %s' % (type(e).__name__, e)
        return {'class': cats, 'filters': self._filters(), 'list': lst}
    def homeVideoContent(self):
        try:
            lst = self._jcards(self._getj(self.CAT_URL.format(host=self.HOST, tid='', pg=1)))
        except Exception:
            lst = []
        return {'list': lst}
    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        limit = 30
        if isinstance(extend, str):
            try:
                ex = json.loads(extend) if extend else {}
            except Exception:
                ex = {}
        else:
            ex = extend or {}
        # 【二级分类】筛选里选中的子分类 type_id 覆盖一级 tid（如 动作片 t=6）
        sub = (ex.get('t') or ex.get('type') or '').strip()
        if sub:
            tid = sub
        if getattr(self, 'FILTER_TPL', '') and ex:
            url = self._filter_url(tid, pg, ex)
            data = self._getj(url)
        else:
            url = self.CAT_URL.format(host=self.HOST, tid=tid, pg=pg)
            by = self._order_by(ex)
            data = self._getj(url, params={'order_by': by} if by else None)
        lst = self._jcards(data)
        pc, total = self._jmeta(data)
        if pc <= 0:
            pc = pg if len(lst) < limit else pg + 1
        if total <= 0:
            total = pc * limit
        return {'page': pg, 'pagecount': pc, 'limit': limit, 'total': total, 'list': lst}
    def searchContent(self, key, quick, pg='1'):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        limit = 30
        url = self.SEARCH_URL.format(host=self.HOST, key=self._search_key(key), pg=pg)
        data = self._getj(url)
        lst = self._jcards(data)
        pc, total = self._jmeta(data)
        if pc <= 0:
            pc = pg if len(lst) < limit else pg + 1
        if total <= 0:
            total = pc * limit
        return {'page': pg, 'pagecount': pc, 'limit': limit, 'total': total, 'list': lst}
    @staticmethod
    def _fix_play_url(s, vid):
        outs = []
        for line in (s or '').split('$$$'):
            items = []
            for ep in line.split('#'):
                if '$' not in ep:
                    continue
                nm, u = ep.split('$', 1)
                u = (u or '').strip()
                if u.startswith('http'):
                    items.append('%s$%s' % (nm, u))
                elif u.startswith('//'):
                    items.append('%s$%s' % (nm, 'https:' + u))
                else:
                    items.append('%s$%s' % (nm, u))
            outs.append('#'.join(items))
        return '$$$'.join(outs)

    #~ ---------------- 详情（采集 API） ----------------
    def detailContent(self, ids):
        vid = ids[0] if ids else ''
        data = self._getj(self.DETAIL_URL.format(host=self.HOST, id=vid))
        arr = (data or {}).get('list') or []
        if not arr:
            return {'list': []}
        d = arr[0]
        vod = {
            'vod_id': str(d.get('vod_id') or vid),
            'vod_name': d.get('vod_name') or '',
            'vod_pic': self._abs(d.get('vod_pic') or ''),
            'type_name': d.get('type_name') or '',
            'vod_year': d.get('vod_year') or '',
            'vod_area': d.get('vod_area') or '',
            'vod_remarks': d.get('vod_remarks') or '',
            'vod_actor': d.get('vod_actor') or '',
            'vod_director': d.get('vod_director') or '',
            'vod_content': re.sub(r'<[^>]+>', ' ', d.get('vod_content') or '').strip(),
            'vod_play_from': d.get('vod_play_from') or '',
            'vod_play_url': self._fix_play_url(d.get('vod_play_url') or '', d.get('vod_id') or vid),
        }
        return {'list': [vod]}

    #~ ---------------- 播放 ----------------
    def playerContent(self, flag, id, vipFlags):
        try:
            return self._player_inner(flag, id, vipFlags)
        except Exception:
            # 解析失败也要把原地址还回去，交给壳端解析，不能返回空导致直接黑屏
            try:
                url = str(id or '').strip()
                if url.startswith('//'):
                    url = 'https:' + url
                if url and not url.startswith('http'):
                    url = self._abs(url)
            except Exception:
                url = str(id or '')
            return {'parse': 1, 'playUrl': '', 'url': url,
                    'header': self._h(), 'message': '解析异常，已回退到原地址'}
    def _player_inner(self, flag, id, vipFlags, _tried=None):
        url = str(id or '').strip()
        # ★v7.8【真机 zzoc「播放地址解析失败 + 404页」的直接原因】：detailContent
        # 走占位兜底时，选集地址是一段诊断文本而非 URL。旧版把它当播放页地址
        # 交给壳端 WebView 去导航，用户看到的就是莫名其妙的 404/错误页。
        # 现在直接拒收：返回 parse=0 + 空 url + message，壳端会展示这段文字。
        if ('抓不到' in url or '抓取失败' in url or '暂无' in url
                or '盾拦截' in url or '详情页无效' in url):
            return {'parse': 0, 'playUrl': '', 'url': '', 'header': {},
                    'message': url[:150]}
        if url.startswith('//'):
            url = 'https:' + url
        if not url.startswith('http'):
            url = self._abs(url)

        # 已经是直链，直接放行（不用探测：探测要多一次请求，
        # 而直链在多数情况下是好的，交给壳端播更划算）
        if re.search(r'\.(m3u8|mp4|m4v|flv|mkv|webm)(\?|$)', url, re.I):
            self._mark_line_ok(flag)
            return self._pack(url)

        html = self._get(url)
        u = self._extract_play(html, 2) if html else ''
        if u:
            self._mark_line_ok(flag)
            return self._pack(u)

        #~ ---- 本线路解析失败 ----
        self._mark_line_bad(flag)
        alt = self._next_line(flag, url, _tried)
        if alt:
            nflag, nurl = alt
            try:
                r = self._player_inner(nflag, nurl, vipFlags,
                                       (_tried or set()) | {str(flag)})
                # 【判断"切换是否真的成功"，不能只看有没有返回】
                # 递归返回的也可能是「下一条也失败了」的兜底结果。
                # 早先这里无条件盖成"已自动切到X"，于是三条线路全挂时
                # 壳端显示的却是"已自动切到线B" —— 把失败包装成了成功，
                # 用户以为能播、点进去是空的，比不给提示更误导。
                if isinstance(r, dict) and r.get('parse') == 0 and r.get('url'):
                    r['message'] = '原线路(%s)无响应，已自动切到%s' % (flag, nflag)
                    return r
                # 下一条也失败：把**内层的失败结论原样透传**上去。
                # 不要让每一层各自拼一句 —— 外层不知道总共试了几条
                #（最外层的 _tried 还是 None），自己拼就会写出"已尝试1条"
                # 而实际试了3条这种自相矛盾的话。最深那层的数字才是准的。
                if isinstance(r, dict):
                    return r
            except Exception:
                pass

        # 全部线路都没戏：合成一条「提示」线路把原因写清楚。
        # 返回空 playUrl 时壳端只会黑屏，用户不知道发生了什么；
        # 给出可读原因后至少能判断「是源坏了还是这部片没资源」。
        # 只在没有下层结论时才走到这里（即本轮只试了这一条）；
        # tried_n 由最深的那层算出 —— 只有它知道总共试了几条。
        tried_n = len(_tried or ()) + 1
        return {'parse': 1, 'playUrl': '', 'url': url, 'header': self._h(),
                'message': '所有线路均未解析出播放地址（已尝试 %d 条），'
                           '可能是站点临时故障或该资源已下架' % tried_n}

    #~ ---------------- 线路健康度 ----------------
    # 记「失败次数 + 最近失败时间」而不是只记时间：
    #   - 只看时间的话，一条线路偶发超时也会被沉底半小时，太激进；
    #   - 加计数后偶发失败(1次)影响很小，反复失败(>=2次)才排到后面。
    # 成功一次即清零 —— 线路恢复了就该立刻回到正常位置。
    LINE_BAD_COOLDOWN = 1800      # 失败记忆有效期（秒）
    LINE_BAD_THRESHOLD = 2        # 累计失败几次才沉底
    def _line_store(self):
        d = getattr(self, '_line_bad', None)
        if d is None:
            d = {}
            self._line_bad = d
            try:
                with open(self.LINE_DB, 'r', encoding='utf-8') as f:
                    old = json.load(f)
                if isinstance(old, dict):
                    now = time.time()
                    for k, v in old.items():
                        if not isinstance(v, dict):
                            continue
                        ts = float(v.get('t') or 0)
                        if now - ts < self.LINE_BAD_COOLDOWN * 4:
                            d[str(k)] = {'n': int(v.get('n') or 0), 't': ts}
            except Exception:
                # 读不到就当没有：磁盘只是加速器，绝不能因为它失败而影响播放
                pass
        return d
    def _line_save(self, d):
        try:
            tmp = self.LINE_DB + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(d, f)
            os.replace(tmp, self.LINE_DB)
        except Exception:
            pass
    def _mark_line_bad(self, flag):
        try:
            if not flag:
                return
            d = self._line_store()
            k = str(flag)
            old = d.get(k) or {}
            d[k] = {'n': int(old.get('n') or 0) + 1, 't': time.time()}
            self._line_save(d)
        except Exception:
            pass
    def _mark_line_ok(self, flag):
        try:
            if not flag:
                return
            d = self._line_store()
            k = str(flag)
            if k in d:
                d.pop(k, None)
                self._line_save(d)
        except Exception:
            pass
    def _line_score(self, name):
        try:
            v = self._line_store().get(str(name)) or {}
            n = int(v.get('n') or 0)
            t = float(v.get('t') or 0)
            if n >= self.LINE_BAD_THRESHOLD and (time.time() - t) < self.LINE_BAD_COOLDOWN:
                return 1
        except Exception:
            pass
        return 0
    def _cache_lines(self, vid, froms, urls):
        try:
            f = froms.split('$$$') if isinstance(froms, str) else list(froms or [])
            u = urls.split('$$$') if isinstance(urls, str) else list(urls or [])
            cache = getattr(self, '_line_cache', None)
            if cache is None:
                cache = {}
                self._line_cache = cache
            if len(cache) > 8:
                for _k in sorted(cache, key=lambda k: float((cache[k] or {}).get('t') or 0))[:-4]:
                    cache.pop(_k, None)
            cache[str(vid)] = {
                't': time.time(),
                'froms': f,
                # 每条线路的第一集地址：切换时从它开始试，不用重抓详情页
                'firsts': [(g.split('#')[0].split('$', 1)[1]
                            if '$' in g.split('#')[0] else '') for g in u],
            }
            self._line_cache_vid = str(vid)
        except Exception:
            pass
    def _sort_lines(self, froms, urls):
        try:
            idx = sorted(range(len(froms)), key=lambda i: self._line_score(froms[i]))
            return [froms[i] for i in idx], [urls[i] for i in idx]
        except Exception:
            return froms, urls
    def _next_line(self, flag, cur_url, tried):
        try:
            cache = getattr(self, '_line_cache', None) or {}
            ent = cache.get(str(getattr(self, '_line_cache_vid', '') or '')) or {}
            if not ent:
                # 老壳端调 playerContent 时不带 vid，退化成「取最近一次缓存的表」
                ent = list(cache.values())[-1] if cache else {}
            ts = float(ent.get('t') or 0)
            if not ent or (time.time() - ts) > 600:
                return None
            tried = set(str(x) for x in (tried or ()))
            tried.add(str(flag))
            for name, first in zip(ent.get('froms') or [], ent.get('firsts') or []):
                if str(name) in tried or not first:
                    continue
                if self._line_score(name):
                    continue      # 已知反复失败的线路不当备选，免得白等
                return str(name), str(first)
            # 全都被排除了（含沉底的）：退一步，允许试沉底线路，但只取第一条
            for name, first in zip(ent.get('froms') or [], ent.get('firsts') or []):
                if str(name) not in tried and first:
                    return str(name), str(first)
        except Exception:
            pass
        return None

    #~ ---------------- 标准可选接口 ----------------
    # 以下方法 TVBox/drpy 会按需调用，缺失时部分壳端会走降级分支，
    # 这里按主流源的约定补齐（参考 71us v7.5 / 遮天九秘 模板）。
    def isVideoFormat(self, url):
        try:
            u = str(url or '').lower()
            return bool(re.search(r'\.(m3u8|mp4|m4v|flv|mkv|webm|ts|avi|mov)(\?|$)', u))
        except Exception:
            return False
    def manualVideoCheck(self):
        return False
    def homeFilterContent(self, filter):
        return self._filters() if hasattr(self, '_filters') else (getattr(self, 'FILTERS', None) or {})
    def searchContentPage(self, key, quick, pg='1'):
        try:
            return self.searchContent(key, quick, pg)
        except Exception:
            return {'list': [], 'page': 1, 'pagecount': 1, 'limit': 0, 'total': 0}
    def destroy(self):
        try:
            self.session.close()
        except Exception:
            pass
    def _h(self):
        try:
            return json.dumps(dict(self.session.headers))
        except Exception:
            return ''
    def _vh(self):
        try:
            h = {
                'User-Agent': self.session.headers.get('User-Agent', ''),
                'Accept': '*/*',
                'Accept-Language': 'zh-CN,zh;q=0.9',
            }
            ref = getattr(self, 'PLAY_REFERER', None)
            if ref is None:
                h['Referer'] = self.HOST + '/'
            elif ref:
                h['Referer'] = ref
            # ref == '' 时**刻意不放 Referer 键**
            # 盾站：KEY/分片同样在盾后，播放头必须带 cf_clearance
            #（与过盾 UA 成对，缺 Cookie 会被盾直接 403，黑屏）。
            # v6.5：cookie 统一走 _cf_ck()（文件缓存 -> 源码内嵌种子兜底）；
            # 普通站没有这个方法，退回原逻辑。
            getter = getattr(self, '_cf_ck', None)
            ck = ''
            if getter is not None:
                try:
                    ck = getter() or ''
                except Exception:
                    ck = ''
            else:
                cf = getattr(self, 'cf', None)
                if cf is not None:
                    try:
                        ck = cf.cookie_header()
                    except Exception:
                        ck = ''
            if ck:
                h['Cookie'] = ck
            return json.dumps(h)
        except Exception:
            return ''
    def _pack(self, u):
        return {'parse': 0, 'playUrl': '', 'url': u, 'header': self._vh(), 'message': self.NAME}
    def _extract_play(self, html, depth=2):
        # 【v7.13.4】最开头先试解析接口：走 iframe -> /proxy/{v} 那类站，
        # 真地址只能从解析接口拿（播放器页只是个 artplayer 壳子，正文无地址）。
        # 这个方法由 TPL_PARSER_HOOK 注入的 _parser_early 提供；没装 parser
        # 特性时不存在，getattr 兜住，直接跳到下面的通用提取。
        try:
            _early = getattr(self, '_parser_early', None)
            if _early is not None:
                _u = _early(html)
                if _u:
                    return _u
        except Exception:
            pass
        cands = []
        # ★【踩过的坑 v7.5】播放配置变量名各站不同，不能写死。
        # 旧写法 player_(?:aaaa|data) 只认这两个名字，而 zzoc.cc（MacCMS v10 + myui）
        # 实测用的变量名不在这两个里 —— 于是整条解析直接跳到下面的通用 pats，
        # 但播放地址是 encrypt=2 的 base64，明文里压根没有 http 开头的串，
        # pats 一条都命中不了 → playerContent 拿不到直链，只能回退
        # 「parse=1 让壳端自己解析」→ App 六接口回归 playerContent / 线路可用性 FAIL。
        # 这里照抄用户那份实测能播的 zzoc 模板的写法做通配，并额外容忍结尾分号。
        m = (re.search(r'var\s+player_[A-Za-z0-9_]+\s*=\s*(\{[\s\S]{0,6000}?\})\s*;?\s*(?:</script>|$)', html)
             or re.search(r'player_[A-Za-z0-9_]+\s*=\s*(\{[\s\S]{0,6000}?"url"[\s\S]{0,6000}?\})', html)
             # 再宽一层：不叫 player_ 的播放配置对象（playerInfo / MacPlayer / playData ...）
             or re.search(r'var\s+(?:[A-Za-z0-9_]*[Pp]layer[A-Za-z0-9_]*|playData|videoConfig|playerConfig)\s*=\s*(\{[\s\S]{0,6000}?\})\s*;?\s*(?:</script>|$)', html))
        if m:
            raw = m.group(1)
            d = None
            try:
                d = json.loads(raw.replace('\\/', '/'))
            except Exception:
                d = None
            if not isinstance(d, dict):
                mm = re.search(r'"(?:url|link|playUrl|playurl)"\s*:\s*"([^"]+)"', raw)
                if mm:
                    cands.append(mm.group(1))
            else:
                # 照抄 maccms player.js：encrypt=1 转义；encrypt=2 base64 后再转义
                enc = str(d.get('encrypt') or '0')
                for k in ('url', 'url_next', 'link', 'playurl', 'playUrl', 'video_url'):
                    v = d.get(k)
                    if not isinstance(v, str) or not v:
                        continue
                    v = v.replace('\\/', '/')
                    if enc == '1':
                        v = _js_unescape(v)
                    elif enc == '2':
                        b = _b64decode(v)
                        if b:
                            v = _js_unescape(b)
                    if not v:
                        continue
                    for one in _play_parts(v):
                        if one.startswith(('http', '//')):
                            cands.append(one)
        pats = [
            r'var\s+(?:now|url|playurl|video_url|main)\s*=\s*["\']([^"\']{6,500})["\']',
            r'"(?:url|link|playUrl|playurl|m3u8|mp4)"\s*:\s*"([^"]{6,500})"',
            r'(?:src|url)\s*=\s*["\'](https?://[^"\']+\.m3u8[^"\']*)["\']',
            r'(https?://[^"\'\\\s<>]+\.m3u8[^"\'\\\s<>]*)',
            r'(https?://[^"\'\\\s<>]+\.mp4[^"\'\\\s<>]*)',
        ]
        for pat in pats:
            for v in re.findall(pat, html, re.I):
                if isinstance(v, tuple):
                    v = v[0]
                cands.append(v)
        others = []
        for c in cands:
            u = (c or '').strip().replace('\\/', '/').replace('\\u002F', '/')
            if not u:
                continue
            if u.startswith('//'):
                u = 'https:' + u
            if not u.startswith('http'):
                continue
            if re.search(r'\.m3u8(\?|$)', u, re.I):
                ok = self._is_m3u8(u)
                if ok:
                    return u
                if ok is None:
                    # 【v6.6】探测失败（超时/DNS/断网）≠ 确认不是 m3u8。
                    # 原写法把网络异常也当"不是 m3u8"丢弃 —— CDN 慢一点就把
                    # 好不容易解析出的直链扔了，playerContent 只能回退占位。
                    # 现在探测失败时保留 URL 兜底交给壳端播放器自行判断。
                    if u not in others:
                        others.append(u)
                continue
            if re.search(r'\.(mp4|m4v|flv|mkv|webm)(\?|$)', u, re.I):
                return u
            # 【v7.13.18 修】排除站点自身页面 URL：解析失败时 others 兜底
            # 会把首页/详情页地址当播放地址返回，壳端 WebView 打开首页，
            # 用户看到的是「播放失败」而不是明确原因。站内 URL 一律不作候选。
            if re.search(r'/play/|/player/|/detail/|/vod/|\.html(\?|$)', u, re.I):
                continue
            try:
                _host = (up.urlparse(u).netloc or '').lower()
            except Exception:
                _host = ''
            _sh = (self.HOST or '').lower().replace('https://', '').replace('http://', '').strip('/')
            if not _host or _host == _sh:
                continue
            if u not in others:
                others.append(u)
        if others:
            return others[0]
        if depth > 0:
            im = re.search(r'<iframe[^>]+src="([^"]+)"', html)
            if im:
                sub = self._abs(im.group(1))
                if sub.startswith('http'):
                    h2 = self._get(sub)
                    if h2:
                        return self._extract_play(h2, depth - 1)
        return ''
    def _is_m3u8(self, u):
        try:
            r = self.session.get(u, timeout=8, stream=True)
            head = ''
            for chunk in r.iter_content(256):
                if not chunk:
                    break
                head += chunk.decode('utf-8', 'ignore')
                if len(head) > 512:
                    break
            try:
                r.close()
            except Exception:
                pass
            return '#EXTM3U' in head
        except Exception:
            return None

    #~ ---------------- 图片代理 ----------------
    @staticmethod
    def _mime(data):
        if data.startswith(b'\xff\xd8\xff'):
            return 'image/jpeg'
        if data.startswith(b'\x89PNG\r\n\x1a\n'):
            return 'image/png'
        if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
            return 'image/webp'
        if data.startswith((b'GIF87a', b'GIF89a')):
            return 'image/gif'
        return ''
    def localProxy(self, param):
        try:
            url = param.get('url') if isinstance(param, dict) else str(param or '')
            if not url:
                return [404, 'text/plain', b'']
            headers = {'Referer': self.HOST, 'User-Agent': self.session.headers.get('User-Agent', '')}
            r = self.session.get(url, headers=headers, timeout=30)
            if r.status_code != 200 or not r.content:
                return [404, 'text/plain', b'']
            ct = self._mime(r.content) or r.headers.get('Content-Type', '').split(';')[0] or 'application/octet-stream'
            return [200, ct, r.content]
        except Exception:
            return [404, 'text/plain', b'']
    def proxyPic(self, pic_url):
        try:
            url = str(pic_url or '')
            if not url:
                return b''
            headers = {'Referer': self.HOST, 'User-Agent': self.session.headers.get('User-Agent', '')}
            r = self.session.get(url, headers=headers, timeout=30)
            return r.content if r.status_code == 200 and r.content else b''
        except Exception:
            return b''
