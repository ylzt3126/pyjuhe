# -*- coding: utf-8 -*-
"""
橘汁（juziapp.hzhcbkj.cn）TVBox Spider —— AppDrama 协议实现

一、这套接口是什么样的（2026-10 実测，APK: 橘汁 3.0.7.0 / com.asjdhjhd.mgd.p3）
  1) 域名不在代码里写死，靠 ext.site 下发的 app.txt：
         GET https://123-1349250429.cos.ap-shanghai.myqcloud.com/app.txt
         -> {"domain":"https://juziapp.hzhcbkj.cn","enabled":true}
  2) 所有业务接口都要求一个 "公共参数请求头"：
         publicParams: {"paramsData":"<AES-CBC 的 hex>"}
     缺这个头时服务端直接返回：
         {"code":500,"msg":"公共参数请求头不能为空"}   （protobuf 接口则返回 38 字节的 ApiResult）
     明文是客户端设备信息 JSON，用固定 AES-128-CBC 密钥加密（key == iv == "ed5fdsgucxumegqa"）。
  3) 部分接口还要额外的动态签名（proto 系列）：
         sig   = RSA_PKCS1v15( timestamp + random_str + vApp, zoneKey )
         sig2  = AES-ECB( timestamp + random_str, key = dataIv )[0:8]
         sig3  = AES-ECB( timestamp + random_str, key = dataIv )[8:]
     其中 zoneKey 不是 ext.publicKey，而是运行时向服务端换的一次性公钥：
         POST /api/v5/find/app/zone   (body = RSARequest protobuf，sign 用 ext.publicKey 签)
         -> ApiResult{ data = RSAPublic{ str2..str5 } }，拼接后是一个 2048bit 公钥。
     ext.publicKey（1024bit）只用于这次密钥交换。
  4) 业务数据分两套通道：
     * JSON 通道：GET /api/v3/drama/getCategory、/api/ex/v3/security/tag/list ...
       - 加密响应时 data 字段是字符串，需要连续两次 AES-ECB 解密：
             plain = AES-ECB-Dec( AES-ECB-Dec(data, dataKey), dataIv )
         （ext.decrypt == "0" 时不解密，data 直接就是明文/列表）
     * protobuf 通道：POST /api/proto/v5/drama/{category,search,getDetail}、/api/proto/v5/videoUsableUrl
       - 请求体是 SecureRequest protobuf：
             aes_encrypt1 = (random8 + base64(AES-ECB(query + timestamp, dataKey)))[0:20]
             aes_encrypt2 = 上面的剩余部分
             aes_fakestr  = 20 位随机串（纯干扰）
             timestamp / random_str 原样带上
       - 响应体是 ApiResult{int64 code=1; string msg=2; bytes data=3}，data 里再套具体消息。
  5) 播放：详情里的 videos 每条给出 (source, source_cn, path)，path 不是直链而是一个票据；
     要再请求 /api/proto/v5/videoUsableUrl（vodPlayFrom=source, playUrl=path）换取真实 m3u8/mp4。
     实测 KZNB / zy1080_01 / qsvip / PAN1 / shvip 等源都能直接拿到可播地址。

二、本文件的实现约定
  * 不依赖第三方库：AES / RSA-PKCS1v1.5 / protobuf 编解码全部内置实现，
    只用标准库 urllib，方便在 TVBox 的 py 环境里直接跑。
  * 所有字段按《Drama.proto / DramaDetail.proto / DramaVideo.proto / ParsePlayUrl.proto》映射，
    输出 homeContent / homeVideoContent / categoryContent / detailContent / searchContent / playerContent
    六个标准方法，字段名沿用 TVBox 约定（vod_id / vod_name / vod_pic / vod_play_from / vod_play_url ...）。
  * zoneKey 进程内缓存，签名校验失败（code=500 且 msg 含 sign/key/token）时自动重新换取一次。
  * 播放 id 用 base64(JSON{"f":source,"p":path}) 传递，避免 path 里的 "/" "=" "#" 破坏 TVBox 的
    "名称$地址#名称$地址" 结构；path 本身已是直链时直接把直链当 id。

三、两个必须注意的数据坑（都是实测踩出来的）
  1) 列表接口（DramaBeanPage）里的封面只填 thumbnail_path，path 是空串；
     而详情接口常常反过来。所以 decode_cover 必须双向兜底，否则整页图片都是空的。
  2) tag/list 的轮播位 carousels[i].id 是"轮播位自己的 id"（140/141…），
     真正的剧集 id 在 link 里：/navigate/video?id=656399 —— 用错 id 会让"点推荐进详情"永远拿不到数据。
  3) 封面里有相当一部分是明文 http（m.ykimg.com、puui.qpic.cn 等），Android 9+ 客户端会拦，
     已按 CDN 白名单升级成 https；不支持 https 的（如 pic.findjs.top）保持原样。
"""

import sys
import re
import ssl
import json
import time
import gzip
import base64
import random
import string
import struct
import urllib.parse
import urllib.request
from urllib.parse import urlsplit
import urllib.error

sys.path.append('..')
try:
    from base.spider import Spider as _BaseSpider
except Exception:
    _BaseSpider = object


# ======================================================================================
# 配置（与 TVBox 配置里的 ext 保持一致；ext 为空时用这里的默认值）
# ======================================================================================
EXT = {
    "appName": "橘汁",
    "publicKey": "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCr8SzZhjYy+rsya1K09t8d2K50pWFoBkgUqMpKOiW+3IEVKd4eTdvg9RSOjQ82kypL6R9BnsmrS1V8s4PVDwjQbUtYhTPPC9Hz16qY7rpD6m0d2vr09/UpWQ5uOy9PR0QTrsioveZ+DIe9jc3C+zBCu/kZSY/R8stwJoiitki3gwIDAQAB",
    "dataKey": "ZU1DSFFWNENKMDYXVUQRN0ZJDUC4PQ==",
    "dataIv": "OC1A06E197EF10CF3F6058CA7A803B5E",
    "pkg": "com.asjdhjhd.mgd.p3",
    "host": "",
    "site": "https://123-1349250429.cos.ap-shanghai.myqcloud.com/app.txt",
    "version": "3.0.7.0",
    "decrypt": "1",
}

# 域名发现失败时的兜底（app.txt 里出现的正式域名）
DEFAULT_HOSTS = ["https://juziapp.hzhcbkj.cn"]

# 公共参数请求头用的固定 AES 密钥（key 与 iv 相同，全系列 AppDrama 客户端通用）
PARAMS_AES_KEY = "ed5fdsgucxumegqa"

UA = "okhttp/3.12.1"
PAGE_SIZE = 21                      # 实测一页就是 21 条
DIRECT_MEDIA_RE = re.compile(r'(?i)\.(mp4|m3u8|flv|mkv|avi|ts|mov|mpd|m4a|wmv|rmvb)(\?.*)?$')
WEBPAGE_URL_RE = re.compile(r'(?i)(youku\.com|v\.qq\.com|bilibili\.com|iqiyi\.com|mgtv\.com|sohu\.com|le\.com|pptv\.com)')

# 这些 CDN 实测支持 https，而部分客户端（Android 9+）会拦明文 http 图片，统一升级
HTTPS_CDN = ("qpic.cn", "iqiyipic.com", "hdslb.com", "ykimg.com", "sohu.com", "hitv.com",
             "alicdn.com", "mzstatic.com", "doubanio.com", "myqcloud.com", "aliyuncs.com",
             "qwapp.top", "bfvp26.com", "hxx2023.cc", "meilinvps.com")

# 分类里这些不是正常点播内容，首页不展示
SKIP_CATS = ("公告", "文章", "直播")

# converUrl 里的筛选项 -> (请求参数名, 中文名)
FILTER_MAP = [
    ("class", "vodClass", "类型"),
    ("area", "vodArea", "地区"),
    ("lang", "vodLang", "语言"),
    ("year", "vodYear", "年份"),
    ("star", "vodStar", "主演"),
    ("director", "vodDirector", "导演"),
    ("state", "vodState", "状态"),
    ("version", "vodVersion", "版本"),
    ("extend_sort", "vodOrderBy", "排序"),
]

# 播放源画质排序：4K > 2K > 超清 > 蓝光 > 臻彩 > 其他
SOURCE_RANK = [("4K", 0), ("2K", 1), ("超清", 2), ("高清", 3), ("蓝光", 4), ("臻彩", 5)]

# 进程内共享 zoneKey / host，避免每次重建实例都重新握手
_SHARED = {}


# ======================================================================================
# 纯 Python AES（ECB / CBC，仅需加密与解密单块，够本协议用）
# ======================================================================================
_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16"
)
_INV_SBOX = bytearray(256)
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i
_INV_SBOX = bytes(_INV_SBOX)
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _gmul(a, b):
    """GF(2^8) 乘法"""
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


_MUL = [[_gmul(x, y) for y in range(256)] for x in range(4)]   # 只需 2/3 两行，1 行恒等
_MUL9 = [_gmul(x, 9) for x in range(256)]
_MUL11 = [_gmul(x, 11) for x in range(256)]
_MUL13 = [_gmul(x, 13) for x in range(256)]
_MUL14 = [_gmul(x, 14) for x in range(256)]


def _expand_key(key):
    """AES 密钥扩展，返回轮密钥列表（每轮 16 字节）"""
    if len(key) not in (16, 24, 32):
        raise ValueError("AES key must be 16/24/32 bytes, got %d" % len(key))
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = list(w[i - 1])
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([w[i - nk][j] ^ t[j] for j in range(4)])
    return [bytes(sum(w[4 * r:4 * r + 4], [])) for r in range(nr + 1)]


def _add_round_key(s, k):
    return bytes(a ^ b for a, b in zip(s, k))


def _shift_rows(s):
    out = bytearray(16)
    for r in range(4):
        for c in range(4):
            out[r + 4 * c] = s[r + 4 * ((c + r) % 4)]
    return bytes(out)


def _inv_shift_rows(s):
    out = bytearray(16)
    for r in range(4):
        for c in range(4):
            out[r + 4 * c] = s[r + 4 * ((c - r) % 4)]
    return bytes(out)


def _mix_columns(s):
    out = bytearray(16)
    for c in range(4):
        a0, a1, a2, a3 = s[4 * c], s[4 * c + 1], s[4 * c + 2], s[4 * c + 3]
        out[4 * c] = _MUL[2][a0] ^ _MUL[3][a1] ^ a2 ^ a3
        out[4 * c + 1] = a0 ^ _MUL[2][a1] ^ _MUL[3][a2] ^ a3
        out[4 * c + 2] = a0 ^ a1 ^ _MUL[2][a2] ^ _MUL[3][a3]
        out[4 * c + 3] = _MUL[3][a0] ^ a1 ^ a2 ^ _MUL[2][a3]
    return bytes(out)


def _inv_mix_columns(s):
    out = bytearray(16)
    for c in range(4):
        a0, a1, a2, a3 = s[4 * c], s[4 * c + 1], s[4 * c + 2], s[4 * c + 3]
        out[4 * c] = _MUL14[a0] ^ _MUL11[a1] ^ _MUL13[a2] ^ _MUL9[a3]
        out[4 * c + 1] = _MUL9[a0] ^ _MUL14[a1] ^ _MUL11[a2] ^ _MUL13[a3]
        out[4 * c + 2] = _MUL13[a0] ^ _MUL9[a1] ^ _MUL14[a2] ^ _MUL11[a3]
        out[4 * c + 3] = _MUL11[a0] ^ _MUL13[a1] ^ _MUL9[a2] ^ _MUL14[a3]
    return bytes(out)


def _aes_encrypt_block(block, rk):
    s = _add_round_key(block, rk[0])
    for r in range(1, len(rk) - 1):
        s = _add_round_key(_mix_columns(_shift_rows(bytes(_SBOX[b] for b in s))), rk[r])
    s = _add_round_key(_shift_rows(bytes(_SBOX[b] for b in s)), rk[-1])
    return s


def _aes_decrypt_block(block, rk):
    s = _add_round_key(block, rk[-1])
    for r in range(len(rk) - 2, 0, -1):
        s = _inv_mix_columns(_add_round_key(_inv_shift_rows(bytes(_INV_SBOX[b] for b in s)), rk[r]))
    s = _add_round_key(_inv_shift_rows(bytes(_INV_SBOX[b] for b in s)), rk[0])
    return s


def _pkcs7_pad(data, block=16):
    n = block - len(data) % block
    return data + bytes([n]) * n


def _pkcs7_unpad(data):
    if not data:
        return data
    n = data[-1]
    return data[:-n] if 0 < n <= 16 else data


def _to_bytes(v):
    return v.encode("utf-8") if isinstance(v, str) else v


def aes_ecb_encrypt(plain, key):
    """AES/ECB/PKCS7 -> base64 字符串"""
    key, plain = _to_bytes(key), _pkcs7_pad(_to_bytes(plain))
    rk = _expand_key(key)
    out = b"".join(_aes_encrypt_block(plain[i:i + 16], rk) for i in range(0, len(plain), 16))
    return base64.b64encode(out).decode("utf-8")


def aes_ecb_decrypt(cipher_b64, key):
    """base64 字符串 -> 明文"""
    key = _to_bytes(key)
    raw = base64.b64decode(cipher_b64)
    rk = _expand_key(key)
    out = b"".join(_aes_decrypt_block(raw[i:i + 16], rk) for i in range(0, len(raw), 16))
    return _pkcs7_unpad(out).decode("utf-8", "ignore")


def aes_cbc_encrypt_hex(plain, key, iv):
    """AES/CBC/PKCS7 -> 小写 hex（publicParams 用这个格式）"""
    key, iv, plain = _to_bytes(key), _to_bytes(iv), _pkcs7_pad(_to_bytes(plain))
    rk = _expand_key(key)
    prev, out = iv, b""
    for i in range(0, len(plain), 16):
        blk = bytes(a ^ b for a, b in zip(plain[i:i + 16], prev))
        prev = _aes_encrypt_block(blk, rk)
        out += prev
    return out.hex()


# ======================================================================================
# 纯 Python RSA（PKCS#1 v1.5 加密，只做公钥运算，够签名用）
# ======================================================================================
def _der_next(data, pos):
    tag = data[pos]
    pos += 1
    ln = data[pos]
    pos += 1
    if ln & 0x80:
        n = ln & 0x7F
        ln = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    return tag, data[pos:pos + ln], pos + ln


def _rsa_pub_from_spki(der):
    """解析 SubjectPublicKeyInfo，取出 (n, e)"""
    _, spki, _ = _der_next(der, 0)                     # SEQUENCE
    tag, alg, pos = _der_next(spki, 0)                 # SEQUENCE(AlgorithmIdentifier)
    tag, bits, _ = _der_next(spki, pos)                # BIT STRING
    inner = bits[1:] if bits and bits[0] == 0 else bits  # 去掉 unused-bits 字节
    _, seq, _ = _der_next(inner, 0)                    # SEQUENCE(RSAPublicKey)
    tag, n_bytes, pos = _der_next(seq, 0)              # INTEGER n
    tag, e_bytes, _ = _der_next(seq, pos)              # INTEGER e
    return int.from_bytes(n_bytes, "big"), int.from_bytes(e_bytes, "big")


def rsa_encrypt_b64(plain, public_key_b64):
    """RSA/ECB/PKCS1Padding（等价 Java 的 Cipher.getInstance("RSA/ECB/PKCS1Padding")）"""
    n, e = _rsa_pub_from_spki(base64.b64decode(public_key_b64))
    msg = _to_bytes(plain)
    k = (n.bit_length() + 7) // 8
    if len(msg) > k - 11:
        raise ValueError("plaintext too long for RSA key")
    ps = bytes(random.randint(1, 255) for _ in range(k - len(msg) - 3))
    em = b"\x00\x02" + ps + b"\x00" + msg
    c = pow(int.from_bytes(em, "big"), e, n)
    return base64.b64encode(c.to_bytes(k, "big")).decode("utf-8")


# ======================================================================================
# protobuf 最小实现（proto3，只处理本协议用到的 wire type）
# ======================================================================================
def _varint(value):
    if value < 0:
        value += 1 << 64
    out = b""
    while value > 0x7F:
        out += bytes([0x80 | (value & 0x7F)])
        value >>= 7
    return out + bytes([value & 0x7F])


def _read_varint(data, pos):
    result, shift = 0, 0
    while True:
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _field(num, wire, value):
    out = _varint((num << 3) | wire)
    if wire == 0:
        return out + _varint(value)
    if isinstance(value, str):
        value = value.encode("utf-8")
    return out + _varint(len(value)) + value


def _parse_fields(data):
    fields, pos = [], 0
    while pos < len(data):
        tag, pos = _read_varint(data, pos)
        num, wire = tag >> 3, tag & 0x07
        if wire == 0:
            val, pos = _read_varint(data, pos)
        elif wire == 2:
            ln, pos = _read_varint(data, pos)
            val, pos = data[pos:pos + ln], pos + ln
        elif wire == 5:
            val, pos = struct.unpack("<I", data[pos:pos + 4])[0], pos + 4
        elif wire == 1:
            val, pos = struct.unpack("<Q", data[pos:pos + 8])[0], pos + 8
        else:
            break
        fields.append((num, wire, val))
    return fields


def _get(fields, num, default=None):
    for n, wire, val in fields:
        if n != num:
            continue
        if wire == 2:
            try:
                return val.decode("utf-8")
            except Exception:
                return val
        return val
    return default


def _get_raw(fields, num, default=b""):
    """取嵌套 message 的原始字节。_get() 会把合法的 UTF-8 字节解成 str，
    而封面这类嵌套结构里恰好可能是可解码的字节，用 _get 会拿到 str 导致解析失败。"""
    for n, wire, val in fields:
        if n == num and wire == 2:
            return val
    return default


def _all(fields, num):
    return [v for n, _, v in fields if n == num]


# ---- 按 proto 文件写的编解码 ----

def encode_rsa_request(timestamp, sign, fake1, random_str, fake2):
    """RSARequest{ timestamp=1, sign=2, fake1=3, random_str=4, fake2=5 }"""
    return (_field(1, 0, timestamp) + _field(2, 2, sign) + _field(3, 2, fake1)
            + _field(4, 2, random_str) + _field(5, 2, fake2))


def encode_secure_request(enc1, enc2, fakestr, timestamp, random_str):
    """SecureRequest{ aes_encrypt1=1, aes_encrypt2=2, aes_fakestr=3, timestamp=4, random_str=5 }"""
    return (_field(1, 2, enc1) + _field(2, 2, enc2) + _field(3, 2, fakestr)
            + _field(4, 0, timestamp) + _field(5, 2, random_str))


def decode_api_result(data):
    """ApiResult{ code=1, msg=2, data=3 }"""
    f = _parse_fields(data or b"")
    return _get(f, 1, 0), _get(f, 2, ""), _get_raw(f, 3, b"")


def decode_rsa_public(data):
    f = _parse_fields(data or b"")
    return [_get(f, i, "") for i in range(1, 6)]


def decode_cover(data):
    """
    DramaCoverImageBean{ path=1, thumbnail_path=2 }
    注意：列表接口（DramaBeanPage）里绝大多数条目只下发 thumbnail_path，path 为空，
    所以这里两边互相兜底，避免上层拿到空图片地址。
    """
    f = _parse_fields(data or b"")
    path = _get(f, 1, "")
    thumb = _get(f, 2, "")
    return {"path": path or thumb, "thumbnail": thumb or path}


def decode_drama(data):
    """DramaBean（列表用）"""
    f = _parse_fields(data or b"")
    cover = _get_raw(f, 2, b"")
    return {
        "id": _get(f, 3, 0),
        "name": _get(f, 5, ""),
        "cover": decode_cover(cover),
        "area": _get(f, 1, ""),
        "brief": _get(f, 4, ""),
        "stars": _get(f, 6, 0),
        "director": _get(f, 7, ""),
        "remark": _get(f, 13, ""),
        "year": _get(f, 14, 0),
    }


def decode_drama_page(data):
    """DramaBeanPage{ repeated DramaBean dramaBean=1 }"""
    return [decode_drama(v) for n, _, v in _parse_fields(data or b"") if n == 1]


def decode_video(data):
    """DramaVideoBean"""
    f = _parse_fields(data or b"")
    return {
        "id": _get(f, 1, 0),
        "title": _get(f, 2, ""),
        "path": _get(f, 4, ""),
        "format": _get(f, 7, ""),
        "source": _get(f, 9, ""),
        "source_cn": _get(f, 10, ""),
        "season": _get(f, 12, 0),
        "episode": _get(f, 13, 0),
        "is_vip": bool(_get(f, 14, 0)),
        "priority": _get(f, 16, 0),
        "sort": _get(f, 18, 0),
    }


def decode_detail(data):
    """DramaDetailBean"""
    f = _parse_fields(data or b"")
    cover = _get_raw(f, 2, b"")
    return {
        "id": _get(f, 4, 0),
        "name": _get(f, 9, ""),
        "cover": decode_cover(cover),
        "intro": _get(f, 6, ""),
        "brief": _get(f, 7, ""),
        "stars": _get(f, 10, 0),
        "director": _get(f, 12, ""),
        "tag": _get(f, 13, ""),
        "year": _get(f, 18, 0),
        "area": _get(f, 1, ""),
        "actor": _get(f, 25, ""),
        "remark": _get(f, 26, ""),
        "is_end": bool(_get(f, 27, 0)),
        "videos": [decode_video(v) for n, _, v in _parse_fields(data or b"") if n == 29],
    }


def decode_play_url(data):
    """ParsePlayUrlBean{ play_url=1, direct=5, headers=6(map), msg=9 }"""
    raw = data or b""
    f = _parse_fields(raw)
    headers = {}
    for n, _, v in f:
        if n == 6 and isinstance(v, bytes):
            entry = _parse_fields(v)
            k = _get(entry, 1, "")
            if k:
                headers[k] = _get(entry, 2, "")
    return {
        "url": _get(f, 1, ""),
        "direct": _get(f, 5, 0),
        "msg": _get(f, 9, ""),
        "headers": headers,
    }


# ======================================================================================
# 小工具
# ======================================================================================
def rand_str(n):
    """客户端同款随机串：n-1 位字母数字 + '=' 结尾"""
    chars = string.digits + string.ascii_letters
    return "".join(random.choice(chars) for _ in range(n - 1)) + "="


def _quality_rank(name):
    s = str(name or "").upper()
    for kw, rank in SOURCE_RANK:
        if kw.upper() in s:
            return rank
    return 9


# ======================================================================================
# Spider
# ======================================================================================
class Spider(_BaseSpider):
    def __init__(self):
        self.ext = dict(EXT)
        self.host = ""
        self.hosts = list(DEFAULT_HOSTS)
        self.public_key = EXT["publicKey"]
        self.data_key = EXT["dataKey"]
        self.data_iv = EXT["dataIv"]
        self.pkg = EXT["pkg"]
        self.app_name = EXT["appName"]
        self.version = EXT["version"]
        self.decrypt = str(EXT.get("decrypt", "1"))
        self.sites = [EXT["site"]]
        self.zone_key = _SHARED.get("zone_key", "")
        self.zone_t = _SHARED.get("zone_t", 0)
        self._hv, self._hv_t = None, 0
        self._ctx = ssl._create_unverified_context()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._ctx))

    # ------------------------------------------------------------------ #
    # 初始化
    # ------------------------------------------------------------------ #
    def init(self, extend=""):
        ext = None
        if extend:
            try:
                ext = json.loads(extend) if isinstance(extend, str) else extend
            except Exception:
                ext = None
        if isinstance(ext, dict):
            self.ext = ext
        else:
            self.ext = dict(EXT)

        cfg = self.ext
        self.public_key = str(cfg.get("publicKey") or EXT["publicKey"])
        self.data_key = str(cfg.get("dataKey") or EXT["dataKey"])
        self.data_iv = str(cfg.get("dataIv") or EXT["dataIv"])
        self.pkg = str(cfg.get("pkg") or EXT["pkg"])
        self.app_name = str(cfg.get("appName") or EXT["appName"])
        self.version = str(cfg.get("version") or EXT["version"])
        self.decrypt = str(cfg.get("decrypt", "1"))
        site = cfg.get("site") or []
        if isinstance(site, str):
            site = [site]
        self.sites = [s for s in site if s] or [EXT["site"]]

        # 1) 域名：ext.host 优先，其次 app.txt 下发
        self.host = str(cfg.get("host") or "").rstrip("/")
        if not self.host:
            self.host = self._discover_host()

        # 2) 换一次性签名公钥（失败不致命：退回 ext.publicKey 继续试）
        if not self.zone_key:
            self._exchange_zone_key()
        return ""

    def _discover_host(self):
        for site in self.sites:
            try:
                code, body = self._http("GET", site, {"User-Agent": UA}, timeout=10)
                if code != 200 or not body:
                    continue
                j = json.loads(body.decode("utf-8", "ignore"))
                if isinstance(j, str):
                    j = json.loads(j)
                domain = str((j or {}).get("domain") or "").rstrip("/")
                if domain:
                    if domain not in self.hosts:
                        self.hosts.insert(0, domain)
                    return domain
            except Exception:
                continue
        return self.hosts[0]

    # ------------------------------------------------------------------ #
    # HTTP 底座
    # ------------------------------------------------------------------ #
    def _http(self, method, url, headers=None, body=None, timeout=12):
        hdrs = {"User-Agent": UA, "Accept-Encoding": "identity"}
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with self._opener.open(req, timeout=timeout) as r:
                raw = r.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                return r.status, raw
        except urllib.error.HTTPError as e:
            try:
                raw = e.read()
            except Exception:
                raw = b""
            if raw[:2] == b"\x1f\x8b":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            return e.code, raw
        except Exception:
            return 0, b""

    # ------------------------------------------------------------------ #
    # 设备信息 / 公共参数头
    # ------------------------------------------------------------------ #
    def _device_info(self):
        uid = "%032X" % random.getrandbits(128)
        return {
            "country": "CN",
            "vName": self.version,
            "cpuId": "MT6893Z%2FCZA",
            "young": 0,
            "facturer": "Xiaomi",
            "pkg": self.pkg,
            "uuid": uid,
            "resolution": "1080x2272",
            "mac": "02%3A00%3A00%3A00%3A00%3A00",
            "abid": "397",
            "model": "M2012K11AC",
            "plat": "android",
            "udid": uid,
            "dpi": "440",
            "net": "1",
            "lang": "zh",
            "brand": "Xiaomi",
            "density": "2.75",
            "appName": self.app_name,
            "cpu": "arm64-v8a",
            "chid": "10000",
            "carrier": "%E8%81%94%E9%80%9A",
            "_vOsCode": 33,
            "vOs": "13",
            "v": 1,
            "tenantId": "",
            "vApp": self.version.replace(".", ""),
            "device": 0,
            "androidID": "a1b2c3d4e5f67890",
        }

    def _v_app(self):
        return self.version.replace(".", "") or "3070"

    def _params_header(self, device):
        raw = json.dumps(device, separators=(",", ":"), ensure_ascii=False)
        enc = aes_cbc_encrypt_hex(raw, PARAMS_AES_KEY, PARAMS_AES_KEY)
        return json.dumps({"paramsData": enc}, separators=(",", ":"))

    def _json_headers(self):
        dev = self._device_info()
        return {
            "User-Agent": UA,
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "publicParams": self._params_header(dev),
        }

    def _proto_headers(self):
        dev = self._device_info()
        ts = int(time.time() * 1000)
        rs = rand_str(16)
        key = self.zone_key or self.public_key
        try:
            sig = rsa_encrypt_b64("%s%s%s" % (ts, rs, dev["vApp"]), key)
        except Exception:
            sig = rsa_encrypt_b64("%s%s%s" % (ts, rs, dev["vApp"]), self.public_key)
        sig_enc = aes_ecb_encrypt("%s%s" % (ts, rs), self.data_iv)
        dev["sig"] = sig
        dev["random_str"] = rs
        dev["timestamp"] = ts
        dev["sig2"] = sig_enc[:8]
        dev["sig3"] = sig_enc[8:]
        return {
            "User-Agent": UA,
            "Accept": "application/x-protobuf",
            "Content-Type": "application/x-protobuf",
            "publicParams": self._params_header(dev),
        }

    # ------------------------------------------------------------------ #
    # 密钥交换
    # ------------------------------------------------------------------ #
    def _exchange_zone_key(self):
        ts = int(time.time() * 1000)
        rs = rand_str(16)
        try:
            sign = rsa_encrypt_b64("%s%s" % (ts, rs), self.public_key)
        except Exception:
            return False
        body = encode_rsa_request(ts, sign, rand_str(16), rs, rand_str(16))
        code, raw = self._http("POST", self.host + "/api/v5/find/app/zone",
                               self._proto_headers(), body, timeout=15)
        if code != 200 or not raw:
            return False
        acode, msg, data = decode_api_result(raw)
        if acode != 200 or not data:
            return False
        parts = decode_rsa_public(data)
        key = "".join(parts[1:])
        if not key:
            return False
        self.zone_key, self.zone_t = key, time.time()
        _SHARED["zone_key"], _SHARED["zone_t"] = key, self.zone_t
        return True

    # ------------------------------------------------------------------ #
    # 加密请求：protobuf 通道
    # ------------------------------------------------------------------ #
    def _secure_body(self, params):
        ts = int(time.time() * 1000)
        rs = rand_str(8)
        qs = "&".join("%s=%s" % (k, v) for k, v in params.items()
                      if v is not None and str(v) != "")
        enc = aes_ecb_encrypt(qs + str(ts), self.data_key)
        combo = rs + enc
        return encode_secure_request(combo[:20], combo[20:], rand_str(20), ts, rs)

    def _post_proto(self, path, params, retry=True):
        """返回 (code, msg, data_bytes)"""
        body = self._secure_body(params)
        code, raw = self._http("POST", self.host + path, self._proto_headers(), body, timeout=20)
        if code == 0 and retry:                       # 网络异常，换域名再来一次
            self._rotate_host()
            return self._post_proto(path, params, retry=False)
        if not raw:
            if retry:
                self._refresh_zone()
                return self._post_proto(path, params, retry=False)
            return 0, "empty response", b""
        acode, msg, data = decode_api_result(raw)
        low = str(msg or "").lower()
        if acode != 200 and retry and any(k in low for k in ("sign", "key", "token", "zone", "auth")):
            self._refresh_zone()
            return self._post_proto(path, params, retry=False)
        return acode, msg, data

    def _refresh_zone(self):
        self.zone_key = ""
        _SHARED.pop("zone_key", None)
        self._exchange_zone_key()

    def _rotate_host(self):
        if len(self.hosts) > 1:
            self.host = self.hosts[(self.hosts.index(self.host) + 1) % len(self.hosts)] \
                if self.host in self.hosts else self.hosts[0]

    # ------------------------------------------------------------------ #
    # 加密请求：JSON 通道
    # ------------------------------------------------------------------ #
    def _secure_json(self, path, retry=True):
        """GET 加密 JSON 接口；data 是双重 AES-ECB 密文时自动解开"""
        code, raw = self._http("GET", self.host + path, self._json_headers(), timeout=20)
        if code == 0 and retry:
            self._rotate_host()
            return self._secure_json(path, retry=False)
        if not raw:
            return {}
        try:
            j = json.loads(raw.decode("utf-8", "ignore"))
        except Exception:
            return {}
        if not isinstance(j, dict):
            return j
        data = j.get("data")
        if isinstance(data, str) and data and self.decrypt != "0" and j.get("code") == 200:
            for key_seq in ((self.data_key, self.data_iv), (self.data_iv, self.data_key)):
                try:
                    plain = aes_ecb_decrypt(aes_ecb_decrypt(data, key_seq[0]), key_seq[1])
                    return json.loads(plain)
                except Exception:
                    continue
            return data
        return data if data is not None else {}

    # ------------------------------------------------------------------ #
    # 字段映射
    # ------------------------------------------------------------------ #
    def _abs_pic(self, url):
        """图片地址归一化：补全相对路径、清理空白、https 可用时升级掉明文 http"""
        u = str(url or "").strip()
        if not u:
            return ""
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("/"):
            return self.host + u
        if u.startswith("http://"):
            try:
                host = urlsplit(u).hostname or ""
            except Exception:
                host = ""
            if host and host.endswith(HTTPS_CDN):
                u = "https://" + u[7:]
        return u

    def _vod(self, d):
        cover = d.get("cover") or {}
        return {
            "vod_id": str(d.get("id") or ""),
            "vod_name": d.get("name", ""),
            "vod_pic": self._abs_pic(cover.get("path") or cover.get("thumbnail")),
            "vod_remarks": d.get("remark", ""),
            "vod_year": str(d.get("year") or ""),
            "vod_area": d.get("area", ""),
        }

    # ------------------------------------------------------------------ #
    # 首页分类 + 筛选
    # ------------------------------------------------------------------ #
    def homeContent(self, filter=False):
        classes, filters = [], {}
        cats = self._secure_json("/api/v3/drama/getCategory?orderBy=type_id")
        if isinstance(cats, str):
            try:
                cats = json.loads(cats)
            except Exception:
                cats = []
        if not isinstance(cats, list):
            cats = []

        for cat in cats:
            if not isinstance(cat, dict):
                continue
            name = str(cat.get("name") or "").strip()
            if not name or name in SKIP_CATS:
                continue
            tid = str(cat.get("id") or "")
            if not tid:
                continue
            classes.append({"type_id": tid, "type_name": name})

            conver = cat.get("converUrl") or cat.get("conver_url") or ""
            if isinstance(conver, str) and conver.strip().startswith("{"):
                try:
                    conver = json.loads(conver)
                except Exception:
                    conver = {}
            if not isinstance(conver, dict):
                continue
            fl = []
            for src_key, req_key, cn in FILTER_MAP:
                raw = conver.get(src_key)
                if not raw:
                    continue
                vals = [v.strip() for v in str(raw).split(",") if v.strip()]
                if not vals:
                    continue
                fl.append({"key": req_key, "name": cn,
                           "value": [{"n": v, "v": v} for v in vals]})
            if fl:
                filters[tid] = fl

        # 部分客户端首页直接用 homeContent 的 list 渲染，这里把推荐位一并带上，
        # 避免只有分类没有内容（图片自然也就出不来）。
        try:
            home_list = self.homeVideoContent().get("list") or []
        except Exception:
            home_list = []
        return {"class": classes, "filters": filters, "list": home_list[:20]}

    # ------------------------------------------------------------------ #
    # 首页推荐（tag/list 的 carousels + sections）
    # ------------------------------------------------------------------ #
    def homeVideoContent(self):
        now = time.time()
        if self._hv and now - self._hv_t < 300:
            return self._hv

        lst, seen = [], set()
        tags = self._secure_json("/api/ex/v3/security/tag/list")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:
                tags = []
        if isinstance(tags, dict):
            tags = [tags]

        for tag in (tags if isinstance(tags, list) else []):
            if not isinstance(tag, dict):
                continue
            for car in (tag.get("carousels") or []):
                # 轮播的 id 是轮播位自身的 id（140/141...），真正的剧集 id 在 link 里：
                #   /navigate/video?id=656399   ->   656399
                # 取错了会导致「点进热门推荐拿不到详情」。
                vid = ""
                link = str(car.get("link") or "")
                if "id=" in link and "/video" in link:
                    vid = link.split("id=")[-1].split("&")[0].strip()
                if not vid:
                    continue          # 非视频类轮播（专题/活动页）直接跳过
                if vid in seen:
                    continue
                seen.add(vid)
                lst.append({
                    "vod_id": vid,
                    "vod_name": car.get("title") or car.get("description") or "",
                    "vod_pic": self._abs_pic(car.get("picLink") or car.get("cover")),
                    "vod_remarks": "推荐",
                })
            for sec in (tag.get("sections") or []):
                for vod in (sec.get("vodList") or []):
                    if not isinstance(vod, dict):
                        continue
                    vid = str(vod.get("id") or "")
                    if not vid or vid in seen:
                        continue
                    seen.add(vid)
                    cover = vod.get("coverImage") or {}
                    lst.append({
                        "vod_id": vid,
                        "vod_name": vod.get("name", ""),
                        "vod_pic": self._abs_pic(cover.get("path") or cover.get("thumbnailPath")),
                        "vod_remarks": vod.get("remark") or "",
                        "vod_year": str(vod.get("year") or ""),
                        "vod_area": vod.get("area") or "",
                    })
                    if len(lst) >= 60:
                        break

        self._hv, self._hv_t = {"list": lst}, now
        return self._hv

    # ------------------------------------------------------------------ #
    # 分类内容
    # ------------------------------------------------------------------ #
    def categoryContent(self, tid, pg=1, filter=False, extend=""):
        try:
            page = max(int(pg), 1)
        except Exception:
            page = 1
        ext = extend if isinstance(extend, dict) else {}
        if isinstance(extend, str) and extend.strip().startswith("{"):
            try:
                ext = json.loads(extend)
            except Exception:
                ext = {}

        params = {"pagesize": str(PAGE_SIZE), "typeId1": str(tid), "page": str(page)}
        for _, req_key, _cn in FILTER_MAP:
            val = ext.get(req_key)
            if val:
                params[req_key] = str(val)
        params.setdefault("vodOrderBy", "最新")

        acode, msg, data = self._post_proto("/api/proto/v5/drama/category", params)
        videos = [self._vod(d) for d in decode_drama_page(data)] if acode == 200 else []
        videos = [v for v in videos if v["vod_id"]]

        has_more = len(videos) >= PAGE_SIZE
        return {
            "page": page,
            "pagecount": page + 1 if has_more else page,
            "limit": PAGE_SIZE,
            "total": PAGE_SIZE * page if has_more else (PAGE_SIZE * (page - 1) + len(videos)),
            "list": videos,
        }

    # ------------------------------------------------------------------ #
    # 详情
    # ------------------------------------------------------------------ #
    @staticmethod
    def _pack_id(source, path):
        raw = json.dumps({"f": source, "p": path}, separators=(",", ":"))
        return base64.b64encode(raw.encode("utf-8")).decode("utf-8")

    @staticmethod
    def _unpack_id(vid):
        try:
            obj = json.loads(base64.b64decode(vid).decode("utf-8"))
            if isinstance(obj, dict) and obj.get("p"):
                return str(obj.get("f") or ""), str(obj.get("p"))
        except Exception:
            pass
        return "", ""

    def detailContent(self, ids):
        if isinstance(ids, (list, tuple)):
            vid = str(ids[0]) if ids else ""
        else:
            vid = str(ids or "")
        vid = vid.split("/")[0].strip()
        if not vid:
            return {"list": []}

        acode, msg, data = self._post_proto("/api/proto/v5/drama/getDetail", {"id": vid})
        if acode != 200:
            return {"list": []}
        d = decode_detail(data)

        # 按 source_cn 归组播放源（同一部剧多个线路），线路内按集数排序
        sources = {}
        for idx, v in enumerate(d["videos"]):
            path = (v.get("path") or "").strip()
            if not path:
                continue
            name = (v.get("source_cn") or v.get("source") or "橘汁").strip() or "橘汁"
            title = str(v.get("title") or "").strip()
            ep = v.get("episode") or 0
            if title.isdigit() or not title:
                label = "第%s集" % (title or ep or (idx + 1))
            else:
                label = title
            if path.lower().startswith("http"):
                url = path
            else:
                url = self._pack_id(v.get("source") or "", path)
            sources.setdefault(name, []).append((int(ep or 0), idx, label, url))

        names = sorted((n for n in sources if sources[n]), key=lambda n: (_quality_rank(n), len(n)))
        play_from = "$$$".join(names) if names else "橘汁"
        play_url = "$$$".join(
            "#".join("%s$%s" % (it[2], it[3])
                      for it in sorted(sources[n], key=lambda x: (x[0], x[1])))
            for n in names
        ) if names else ""

        cover = d.get("cover", {})
        return {"list": [{
            "vod_id": str(d.get("id") or vid),
            "vod_name": d.get("name", ""),
            "vod_pic": self._abs_pic(cover.get("path") or cover.get("thumbnail")),
            "vod_actor": d.get("actor", ""),
            "vod_director": d.get("director", ""),
            "vod_area": d.get("area", ""),
            "vod_year": str(d.get("year") or ""),
            "vod_remarks": d.get("remark", ""),
            "vod_tag": d.get("tag", ""),
            "vod_content": d.get("intro") or d.get("brief") or "",
            "vod_play_from": play_from,
            "vod_play_url": play_url,
        }]}

    # ------------------------------------------------------------------ #
    # 搜索
    # ------------------------------------------------------------------ #
    def searchContent(self, key, quick=False, pg="1"):
        return self.searchContentPage(key, quick, pg)

    def searchContentPage(self, key, quick=False, pg="1"):
        k = str(key or "").strip()
        try:
            page = max(int(pg), 1)
        except Exception:
            page = 1
        if not k:
            return {"list": [], "page": page, "pagecount": page, "limit": PAGE_SIZE, "total": 0}

        acode, msg, data = self._post_proto("/api/proto/v5/drama/search", {
            "searchKeys": k, "page": str(page), "pagesize": str(PAGE_SIZE),
        })
        videos = [self._vod(d) for d in decode_drama_page(data)] if acode == 200 else []
        videos = [v for v in videos if v["vod_id"]]
        has_more = len(videos) >= PAGE_SIZE
        return {
            "list": videos,
            "page": page,
            "pagecount": page + 1 if has_more else page,
            "limit": PAGE_SIZE,
            "total": PAGE_SIZE * page if has_more else (PAGE_SIZE * (page - 1) + len(videos)),
        }

    # ------------------------------------------------------------------ #
    # 播放
    # ------------------------------------------------------------------ #
    def playerContent(self, flag, id, vipFlags=None):
        pid = str(id or "").strip()
        if not pid:
            return {"parse": 0, "url": ""}
        if pid.lower().startswith("http"):
            return self._play_ret(pid)

        source, path = self._unpack_id(pid)
        if not path:
            return {"parse": 0, "url": pid}

        # 主通道：proto v5 换地址
        acode, msg, data = self._post_proto("/api/proto/v5/videoUsableUrl",
                                            {"vodPlayFrom": source, "playUrl": path})
        if acode == 200 and data:
            info = decode_play_url(data)
            url = (info.get("url") or "").strip()
            if url:
                return self._play_ret(url, info.get("headers"), info.get("direct"))

        # 备用通道：JSON 版 videoUsableUrl（部分版本只开放这一路）
        qs = urllib.parse.urlencode({"vodPlayFrom": source, "playUrl": path})
        jd = self._secure_json("/api/ex/v3/security/videoUsableUrl?" + qs)
        if isinstance(jd, str):
            url = jd.strip()
            if url.startswith("http"):
                return self._play_ret(url)
        elif isinstance(jd, dict):
            url = str(jd.get("playUrl") or jd.get("play_url") or jd.get("url") or "")
            if url.startswith("http"):
                hdr = jd.get("headers") if isinstance(jd.get("headers"), dict) else {}
                return self._play_ret(url, hdr, jd.get("direct"))

        # 兜底：把票据当网页交给嗅探
        return {"parse": 1, "url": path, "header": {}}

    def _play_ret(self, url, headers=None, direct=None):
        """m3u8/mp4 直链给 parse=0；优酷/腾讯这类网页地址交给嗅探（parse=1）"""
        u = str(url or "")
        hdr = {"User-Agent": UA}
        if isinstance(headers, dict):
            hdr.update({str(k): str(v) for k, v in headers.items() if k})
        if WEBPAGE_URL_RE.search(u):
            return {"parse": 1, "url": u, "header": hdr}
        return {"parse": 0, "url": u, "header": hdr}

    def localProxy(self, param):
        return None

    def isVideoFormat(self, url):
        return bool(DIRECT_MEDIA_RE.search(str(url or "")))

    def manualSniffer(self, needGoto=False):
        return False


# ======================================================================================
# 兼容层：TVBox 的 py 加载器既可能用类，也可能直接调模块函数
# ======================================================================================
_spider = None


def _sp():
    global _spider
    if _spider is None:
        _spider = Spider()
    return _spider


def init(extend=""):
    s = _sp()
    s.init(extend)
    return ""


def homeContent(filter=False):
    return _sp().homeContent(filter)


def homeVideoContent():
    return _sp().homeVideoContent()


def categoryContent(tid, pg=1, filter=False, extend=""):
    return _sp().categoryContent(tid, pg, filter, extend)


def detailContent(ids):
    return _sp().detailContent(ids)


def searchContent(key, quick=False, pg="1"):
    return _sp().searchContent(key, quick, pg)


def searchContentPage(key, quick=False, pg="1"):
    return _sp().searchContentPage(key, quick, pg)


def playerContent(flag, id, vipFlags=None):
    return _sp().playerContent(flag, id, vipFlags)


# ======================================================================================
# 自测：python 橘汁.py 直接跑，逐项打印接口结果
# ======================================================================================
if __name__ == "__main__":
    import re as _re
    s = Spider()
    s.init(json.dumps(EXT, ensure_ascii=False))
    print("host =", s.host, "| zoneKey len =", len(s.zone_key))

    hc = s.homeContent(True)
    print("\n[homeContent] 分类 %d 个，带筛选 %d 个" % (len(hc["class"]), len(hc["filters"])))
    for c in hc["class"]:
        print("   ", c["type_id"], c["type_name"], "筛选项:",
              [f["name"] for f in hc["filters"].get(c["type_id"], [])])

    hv = s.homeVideoContent()
    print("\n[homeVideoContent] %d 条" % len(hv["list"]))
    for v in hv["list"][:5]:
        print("   ", v["vod_id"], v["vod_name"], "|", v["vod_remarks"])

    cc = s.categoryContent("20", 1, True, {"vodOrderBy": "最新"})
    print("\n[categoryContent] 电影第1页 %d 条 (pagecount=%s)" % (len(cc["list"]), cc["pagecount"]))
    for v in cc["list"][:5]:
        print("   ", v["vod_id"], v["vod_name"], "|", v["vod_remarks"], "|", v["vod_year"])

    if cc["list"]:
        dc = s.detailContent([cc["list"][0]["vod_id"]])
        item = dc["list"][0]
        print("\n[detailContent]", item["vod_name"], "| 线路:",
              item["vod_play_from"].replace("$$$", " / "))
        first_from = item["vod_play_from"].split("$$$")[0]
        first_url = item["vod_play_url"].split("$$$")[0].split("#")[0]
        label, pid = first_url.split("$", 1)
        print("   第一集:", label)
        pc = s.playerContent(first_from, pid, None)
        print("   playerContent ->", pc.get("parse"), str(pc.get("url"))[:120])

    sc = s.searchContent("爱情", False, "1")
    print("\n[searchContent] 关键词=爱情 %d 条" % len(sc["list"]))
    for v in sc["list"][:5]:
        print("   ", v["vod_id"], v["vod_name"], "|", v["vod_remarks"])
