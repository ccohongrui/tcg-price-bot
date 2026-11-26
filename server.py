# -*- coding: utf-8 -*-
"""
本機 TCG 查價（航海王 / 寶可夢 / 遊戲王）— 只看日版 PSA10
資料來源：SNKRDUNK（スニダン）成交紀錄
只用 Python 標準函式庫，不需要 pip install。

啟動：python server.py   然後瀏覽器開 http://127.0.0.1:8765
"""
import html as htmllib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import names as NAMES

# ───────────────────────── 設定 ─────────────────────────
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8765"))
BASE = "https://snkrdunk.com"
SEARCH_TTL = 10 * 60         # 搜尋結果快取 10 分鐘
CARD_TTL = 15 * 60           # 單卡行情快取 15 分鐘
RATE_TTL = 6 * 60 * 60       # 匯率快取 6 小時
FALLBACK_JPY_TWD = 0.21      # 抓不到匯率時用這個（可自行修改）
REQUEST_GAP = 0.2            # 每個請求之間至少間隔幾秒（對對方伺服器客氣一點）
PSA10 = "trading_card_single_psa10"

GAMES = {
    "onepiece": {"brand": "onepiece", "label": "航海王"},
    "pokemon":  {"brand": "pokemon",  "label": "寶可夢"},
    "yugioh":   {"brand": "yu-gi-oh", "label": "遊戲王"},
}
BRAND_TO_GAME = {v["brand"]: k for k, v in GAMES.items()}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


# ───────────────────────── 小工具 ─────────────────────────
class TTLCache:
    def __init__(self, max_items=3000):
        self._d = {}
        self._lock = threading.Lock()
        self._max = max_items

    def get(self, key):
        with self._lock:
            v = self._d.get(key)
            if v and v[0] > time.time():
                return v[1]
            return None

    def set(self, key, value, ttl):
        with self._lock:
            if len(self._d) >= self._max:
                now = time.time()
                for k in [k for k, v in self._d.items() if v[0] <= now]:
                    del self._d[k]
                if len(self._d) >= self._max:
                    self._d.clear()
            self._d[key] = (time.time() + ttl, value)


cache = TTLCache()
_throttle_lock = threading.Lock()
_last_request = [0.0]


class UpstreamError(Exception):
    pass


def http_get(url, accept="application/json", timeout=15, referer=BASE + "/", who="SNKRDUNK"):
    """對資料來源發請求，含簡單節流。"""
    with _throttle_lock:
        wait = _last_request[0] + REQUEST_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.time()
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": accept,
        "Accept-Language": "ja,zh-TW;q=0.9,en;q=0.8",
        **({"Referer": referer} if referer else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpstreamError(f"{who} 找不到這筆資料")
        raise UpstreamError(f"{who} 回應 HTTP {e.code}，請稍後再試")
    except (urllib.error.URLError, OSError) as e:
        raise UpstreamError(f"連不到 {who}：{getattr(e, 'reason', e)}")


def get_json(url):
    try:
        return json.loads(http_get(url))
    except ValueError:
        raise UpstreamError("SNKRDUNK 回傳的資料格式不對（可能改版了）")


def load_json(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ───────────────────────── 匯率 ─────────────────────────
def jpy_to_twd():
    hit = cache.get("rate")
    if hit:
        return hit
    try:
        req = urllib.request.Request("https://open.er-api.com/v6/latest/JPY",
                                     headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        rate = {"rate": float(data["rates"]["TWD"]), "source": "open.er-api.com", "live": True}
        ttl = RATE_TTL
    except Exception:  # noqa: BLE001
        rate = {"rate": FALLBACK_JPY_TWD, "source": "預設值（抓不到即時匯率）", "live": False}
        ttl = 5 * 60   # 抓失敗只記 5 分鐘，之後再試
    cache.set("rate", rate, ttl)
    return rate


# ───────────────────────── 中文 → 日文卡名 ─────────────────────────
HAN_RE = re.compile(r"[一-鿿]")
KANA_RE = re.compile(r"[぀-ヿ]")
S2T = load_json("s2t.json", {})


def to_trad(s):
    return "".join(S2T.get(c, c) for c in s)


def _expand(table):
    """{"魯夫|路飛": "ルフィ"} → [("魯夫","ルフィ"), ("路飛","ルフィ")]"""
    out = []
    for zhs, ja in table.items():
        for zh in zhs.split("|"):
            out.append((zh.strip(), ja))
    return out


class Dictionary:
    def __init__(self, entries, modifiers, display=None):
        self.entries = entries                       # 下拉建議用 [(zh, ja)]
        full = dict(entries)
        full.update(modifiers)
        keys = sorted(full, key=len, reverse=True)
        self._zh2ja = full
        self._zh_re = re.compile("|".join(map(re.escape, keys))) if keys else None
        # 反向（日→中）只取每個日文名的第一個中文名，用來顯示中文卡名
        rev = {}
        for zh, ja in list(entries) + list(modifiers.items()):
            rev.setdefault(ja, zh)
        rev.update(display or {})
        self._ja2zh = rev
        jkeys = sorted(rev, key=len, reverse=True)
        self._ja_re = re.compile("|".join(map(re.escape, jkeys))) if jkeys else None

    def translate(self, text):
        """把字串裡認得的中文名換成日文。回傳 (新字串, 是否有換)。"""
        if not self._zh_re:
            return text, False
        changed, tokens = False, []
        for tok in text.split(" "):
            # 整個詞都認得才換（「黑魔導女」這種打到一半的不硬翻）
            if HAN_RE.search(tok) and not HAN_RE.search(self._zh_re.sub("", tok)):
                tok = self._zh_re.sub(lambda m: self._zh2ja[m.group(0)], tok)
                changed = True
            tokens.append(tok)
        return " ".join(tokens), changed

    def to_zh(self, title):
        """日文卡名 → 中文卡名；翻不乾淨（還殘留假名）就回 None。"""
        if not self._ja_re:
            return None
        head = re.split(r"\s*[:：(（]", title, maxsplit=1)[0]   # 「:特典」「(コミパラ)」之後不翻
        out, n = self._ja_re.subn(lambda m: self._ja2zh[m.group(0)], head)
        if not n or KANA_RE.search(out):
            return None
        return out.strip()

    def suggest(self, part, limit=8):
        starts, contains = [], []
        for zh, ja in self.entries:
            if zh.startswith(part):
                starts.append((zh, ja))
            elif part in zh:
                contains.append((zh, ja))
        starts.sort(key=lambda x: len(x[0]))
        contains.sort(key=lambda x: len(x[0]))
        seen, out = set(), []
        for zh, ja in starts + contains:
            if ja in seen:
                continue
            seen.add(ja)
            out.append({"zh": zh, "ja": ja})
            if len(out) >= limit:
                break
        return out


DICTS = {
    "pokemon": Dictionary([tuple(x) for x in load_json("pokemon.json", [])] + _expand(NAMES.POKEMON_EXTRA),
                          NAMES.MODIFIERS["pokemon"], NAMES.DISPLAY["pokemon"]),
    "onepiece": Dictionary(_expand(NAMES.ONEPIECE), NAMES.MODIFIERS["onepiece"], NAMES.DISPLAY["onepiece"]),
    "yugioh": Dictionary(_expand(NAMES.YUGIOH), NAMES.MODIFIERS["yugioh"], NAMES.DISPLAY["yugioh"]),
}


def ygocdb_suggest(part):
    """遊戲王本機字典沒有時，問 ygocdb（百鴿）中文卡名資料庫。失敗就當沒有。"""
    key = "ygocdb:" + part
    hit = cache.get(key)
    if hit is not None:
        return hit
    out = []
    try:
        url = "https://ygocdb.com/api/v0/?search=" + urllib.parse.quote(part)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=5) as r:
            res = json.loads(r.read().decode("utf-8")).get("result") or []
        for x in res[:8]:
            if x.get("jp_name") and x.get("cn_name"):
                out.append({"zh": to_trad(x["cn_name"]), "ja": x["jp_name"]})
    except Exception:  # noqa: BLE001
        pass
    cache.set(key, out, 60 * 60)
    return out


def resolve(q, game):
    """使用者輸入 → (實際拿去搜的日文關鍵字, 中文名建議清單)"""
    q = " ".join(q.split())
    d = DICTS[game]
    if not HAN_RE.search(q) or KANA_RE.search(q):
        return q, []                      # 卡號、英文、日文：原樣搜尋
    tokens = q.split(" ")
    part, rest = tokens[0], " ".join(tokens[1:])
    names = d.suggest(part) if HAN_RE.search(part) else []
    if game == "yugioh" and len(part) >= 2 and len(names) < 4:
        have = {n["ja"] for n in names}
        names += [n for n in ygocdb_suggest(part) if n["ja"] not in have][:max(0, 5 - len(names))]
    translated, changed = d.translate(q)
    if changed:
        keyword = translated
    elif names:
        keyword = (names[0]["ja"] + " " + rest).strip()
    else:
        keyword = q                       # 字典沒有：交給 SNKRDUNK 自己猜
    for n in names:
        n["keyword"] = (n["ja"] + " " + rest).strip()
    return keyword, names


# ───────────────────────── SNKRDUNK：搜尋 ─────────────────────────
TILE_RE = re.compile(
    r'<a href="(?:https://snkrdunk\.com)?/apparels/(\d+)"[^>]*aria-label="([^"]*)"[^>]*>(.*?)</a>', re.S)
IMG_RE = re.compile(r'<img[^>]+src="(https://cdn\.snkrdunk\.com/[^"]+)"')
PRICE_SUFFIX_RE = re.compile(r"\s*-\s*¥([\d,]+)\s*$")
FOREIGN_RE = re.compile(r"【[^】]*語版】|英語版|中国語版|韓国語版|簡体字|繁体字")
NAME_RE = re.compile(r"^(.*?)\s*\[([^\]]+)\]\s*(?:[（(](.*)[）)])?")


def split_name(name, game):
    """'ブラッキーex SAR [SV8a 217/187](ハイクラスパック「…」)' → 卡名 / 卡號 / 系列 / 中文名"""
    m = NAME_RE.match(name)
    title, number, series = (m.group(1), m.group(2), m.group(3) or "") if m else (name, "", "")
    zh = DICTS[game].to_zh(title) if game in DICTS else None
    return {"title": title.strip(), "number": number.strip(), "series": series.strip(), "zh": zh}


def parse_tiles(page, game, mode):
    """mode: "hot"＝人氣排行在前；"first"＝搜尋第 1 頁（結果＋同關鍵字的人氣卡）；"next"＝第 2 頁以後"""
    ranked, normal = [], []
    for m in TILE_RE.finditer(page):
        aid, label, inner = m.group(1), htmllib.unescape(m.group(2)), m.group(3)
        price = None
        pm = PRICE_SUFFIX_RE.search(label)
        if pm:
            price = int(pm.group(1).replace(",", ""))
            label = label[:pm.start()]
        if FOREIGN_RE.search(label):
            continue                      # 只要日版
        im = IMG_RE.search(inner)
        card = {"id": int(aid), "name": label, "game": game, "listPrice": price,
                "image": htmllib.unescape(im.group(1)) if im else None}
        card.update(split_name(label, game))
        (ranked if "ランキングバッジ" in inner else normal).append(card)
    order = {"hot": ranked + normal, "first": normal + ranked, "next": normal}[mode]
    seen, out = set(), []
    for c in order:
        if c["id"] not in seen:
            seen.add(c["id"])
            out.append(c)
    return {"cards": out, "more": len(normal) >= 15}


def search_cards(keyword, game, page=1):
    key = f"search:{game}:{page}:{keyword}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    params = {"brandIds": GAMES[game]["brand"], "searchCategoryIds": "6/33", "page": page}
    if keyword:
        params["keywords"] = keyword
    page_html = http_get(f"{BASE}/search?{urllib.parse.urlencode(params)}", accept="text/html")
    res = parse_tiles(page_html, game, "hot" if not keyword else "first" if page == 1 else "next")
    cache.set(key, res, SEARCH_TTL)
    return res


# ───────────────────────── SNKRDUNK：單卡 PSA10 行情 ─────────────────────────
_meta_lock = threading.Lock()
_meta = load_json("card_meta.json", {})      # 卡片基本資料不太會變，存檔省請求


def card_meta(apparel_id):
    key = str(apparel_id)
    with _meta_lock:
        m = _meta.get(key)
    if m:
        return m
    a = get_json(f"{BASE}/v1/apparels/{apparel_id}")
    brand = ((a.get("brands") or [{}])[0]).get("id")
    m = {
        "id": int(apparel_id),
        "catalog": a.get("productCatalogId"),
        "name": a.get("localizedName") or a.get("name") or "",
        "image": (a.get("primaryMedia") or {}).get("imageUrl"),
        "game": BRAND_TO_GAME.get(brand),
        "releasedAt": a.get("displayReleasedAt") or "",
    }
    with _meta_lock:
        _meta[key] = m
        try:
            os.makedirs(DATA, exist_ok=True)
            tmp = os.path.join(DATA, "card_meta.json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_meta, f, ensure_ascii=False)
            os.replace(tmp, os.path.join(DATA, "card_meta.json"))
        except OSError:
            pass
    return m


def psa10_summary(trades, points):
    """trades：最近的 PSA10 成交（最多 20 筆）；points：每日均價 [[ms, 價]]"""
    trades = sorted(trades, key=lambda t: t.get("soldAt") or "", reverse=True)
    singles = [t for t in trades if (t.get("label") or "1枚") == "1枚"] or trades
    out = {"last": None, "lastAt": None, "high": None, "low": None, "avg": None,
           "count": len(singles), "days": 0, "change30": None}
    if singles:
        prices = [t["price"] for t in singles]
        out.update(last=prices[0], lastAt=singles[0].get("soldAt"), high=max(prices),
                   low=min(prices), avg=round(sum(prices) / len(prices)))
        days = {(t.get("soldAt") or "")[:10] for t in singles}
        first, last = min(days), max(days)
        try:
            span = (time.mktime(time.strptime(last, "%Y-%m-%d")) -
                    time.mktime(time.strptime(first, "%Y-%m-%d"))) / 86400
            out["days"] = int(round(span)) + 1
        except ValueError:
            out["days"] = len(days)
    elif points:
        out.update(last=points[-1][1], lastAt=None)
    if len(points) >= 2:
        now_ts, now_price = points[-1]
        target = now_ts - 30 * 86400 * 1000
        base = None
        for ts, p in reversed(points):
            if ts <= target:
                base = p
                break
        if base:
            out["change30"] = round((now_price - base) / base * 100, 1)
    return out


def card_detail(apparel_id):
    key = f"card:{apparel_id}"
    hit = cache.get(key)
    if hit:
        return hit
    m = card_meta(apparel_id)
    trades, points = [], []
    if m.get("catalog"):
        h = get_json(f"{BASE}/v3/products/{m['catalog']}/trading-history"
                     f"?range=all&condition_code={PSA10}")
        trades = [t for t in (h.get("trades") or []) if t.get("title") == "PSA10" and t.get("price")]
        lines = (h.get("chart") or {}).get("lines") or []
        if lines:
            points = [[p["timestamp"], p["price"]] for p in (lines[0].get("points") or [])
                      if p.get("price") and p.get("timestamp")]
    card = dict(m)
    card.pop("catalog", None)
    card.update(split_name(m["name"], m.get("game") or ""))
    card["url"] = f"{BASE}/apparels/{apparel_id}"
    card["psa10"] = psa10_summary(trades, points)
    card["trades"] = [{"price": t["price"], "soldAt": t.get("soldAt"), "label": t.get("label") or ""}
                      for t in sorted(trades, key=lambda t: t.get("soldAt") or "", reverse=True)]
    card["chart"] = points
    cache.set(key, card, CARD_TTL)
    return card


# ───────────────────────── NBA 球員卡：Yahoo! 拍賣 PSA10 落札紀錄 ─────────────────────────
YAHOO = "https://auctions.yahoo.co.jp/closedsearch/closedsearch"
YAHOO_CATEGORY = 21008            # トレーディングカード > スポーツ > バスケットボール
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
PSA10_TITLE_RE = re.compile(r"PSA\s*10", re.I)
NBA_ENTRIES = [(zh.strip(), kws) for zhs, kws in NAMES.NBA.items() for zh in zhs.split("|")]


def nba_suggest(part, limit=8):
    low = part.lower()
    starts = [e for e in NBA_ENTRIES if e[0].lower().startswith(low)]
    contains = [e for e in NBA_ENTRIES if low in e[0].lower() and e not in starts]
    seen, out = set(), []
    for zh, kws in sorted(starts, key=lambda e: len(e[0])) + sorted(contains, key=lambda e: len(e[0])):
        if kws[0] in seen:
            continue
        seen.add(kws[0])
        out.append({"zh": zh, "ja": " / ".join(kws[:2]), "kws": kws})
        if len(out) >= limit:
            break
    return out


def nba_norm(text):
    """比對標題用：去掉空白與中點、轉小寫（「クリス・ポール」「Chris Paul」寫法不一）"""
    return re.sub(r"[\s・･.\-]", "", text).lower()


def nba_ebay_url(words):
    """eBay「已售出」搜尋頁的網址（只是給使用者點開的連結，本程式不會去抓 eBay）"""
    return "https://www.ebay.com/sch/i.html?" + urllib.parse.urlencode(
        {"_nkw": words + " PSA 10", "LH_Sold": 1, "LH_Complete": 1})


def nba_resolve(q):
    """使用者輸入 → (Yahoo 搜尋字串, 球員建議清單, 標題必須符合的條件, eBay 已售出搜尋網址)

    條件是「每一組至少符合一個」的清單，用來濾掉 Yahoo 在結果太少時塞進來的不相關商品。"""
    tokens = q.split()
    if not tokens:
        return "PSA10", [], [], nba_ebay_url("basketball")
    part, rest = tokens[0], tokens[1:]
    low = nba_norm(part)
    exact = [e for e in NBA_ENTRIES if e[0].lower() == part.lower()] or \
            [e for e in NBA_ENTRIES if any(low in (nba_norm(k), k.split(" ")[-1].lower()) for k in e[1])]
    names = nba_suggest(part) if (HAN_RE.search(part) or exact) else []
    kws = exact[0][1] if exact else names[0]["kws"] if names else None
    for n in names:
        n.pop("kws", None)
    if kws:
        search = list(dict.fromkeys(k.split(" ")[-1] for k in kws))   # "Chris Paul" → 用 Paul 搜
        player = "(" + " ".join(search) + ")" if len(search) > 1 else search[0]
        must = [[nba_norm(k) for k in kws]]
    else:
        player, must = part, [[nba_norm(part)]]
    must += [[nba_norm(t)] for t in rest]
    # eBay 用英文搜尋：挑球員的英文名（沒有就用使用者原本打的字）
    english = next((k for k in (kws or []) if k.isascii()), part)
    return " ".join(["PSA10", player] + rest), names, must, nba_ebay_url(" ".join([english] + rest))


def nba_stats(items):
    out = {"last": None, "lastAt": None, "high": None, "low": None, "avg": None, "median": None,
           "count": len(items), "days": 0}
    if items:
        prices = [x["price"] for x in items]
        srt = sorted(prices)
        mid = len(srt) // 2
        out.update(last=prices[0], lastAt=items[0]["soldAt"], high=srt[-1], low=srt[0],
                   avg=round(sum(prices) / len(prices)),
                   median=srt[mid] if len(srt) % 2 else round((srt[mid - 1] + srt[mid]) / 2))
        days = sorted(x["soldAt"][:10] for x in items)
        try:
            span = (time.mktime(time.strptime(days[-1], "%Y-%m-%d")) -
                    time.mktime(time.strptime(days[0], "%Y-%m-%d"))) / 86400
            out["days"] = int(round(span)) + 1
        except ValueError:
            pass
    return out


def nba_search(q, page=1):
    keyword, names_, must, ebay = nba_resolve(q)
    key = f"nba:{page}:{keyword}"
    res = cache.get(key)
    if res is None:
        params = {"p": keyword, "auccat": YAHOO_CATEGORY, "n": 100, "b": (page - 1) * 100 + 1}
        html_ = http_get(f"{YAHOO}?{urllib.parse.urlencode(params)}", accept="text/html",
                         referer=None, who="Yahoo! 拍賣")
        m = NEXT_DATA_RE.search(html_)
        try:
            listing = json.loads(m.group(1))["props"]["pageProps"]["initialState"]["search"]["items"]["listing"]
        except (AttributeError, KeyError, TypeError, ValueError):
            raise UpstreamError("Yahoo! 拍賣回傳的資料格式不對（可能改版了）")
        raw = listing.get("items") or []
        items = []
        for x in raw:
            title = x.get("title") or ""
            if not x.get("price") or not x.get("endTime") or not PSA10_TITLE_RE.search(title):
                continue            # 搜尋會比對到商品說明，標題沒寫 PSA10 的不算
            norm = nba_norm(title)
            if not all(any(alt in norm for alt in group) for group in must):
                continue            # 標題裡沒有這位球員／關鍵字
            items.append({"id": x.get("auctionId"), "title": title, "price": int(x["price"]),
                          "soldAt": x["endTime"], "image": x.get("imageUrl"),
                          "url": f"https://auctions.yahoo.co.jp/jp/auction/{x.get('auctionId')}"})
        items.sort(key=lambda x: x["soldAt"], reverse=True)
        total = int(listing.get("totalResultsAvailable") or 0)
        res = {"items": items, "hasMore": bool(items) and page * 100 < total and len(raw) >= 100}
        cache.set(key, res, SEARCH_TTL)
    return {"query": q, "keyword": keyword, "names": names_, "page": page, "ebayUrl": ebay,
            "items": res["items"], "stats": nba_stats(res["items"]), "hasMore": res["hasMore"]}


# ───────────────────────── HTTP 伺服器 ─────────────────────────
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        try:
            sys.stdout.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))
        except Exception:  # noqa: BLE001  （主控台編碼印不出來也不要影響服務）
            pass

    def _send(self, code, body, ctype):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionError, OSError):
            pass                          # 瀏覽器先關掉連線

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def do_GET(self):
        try:
            self._route()
        except UpstreamError as e:
            self._json({"error": str(e)}, 502)
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"伺服器內部錯誤：{e}"}, 500)

    def _route(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)

        def arg(name, default=""):
            return (q.get(name) or [default])[0].strip()

        if u.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if u.path == "/api/rate":
            return self._json(jpy_to_twd())

        if u.path in ("/api/nba", "/api/nba/suggest"):
            text = " ".join(arg("q")[:80].split())
            if u.path == "/api/nba/suggest":
                keyword, names_, _, _ = nba_resolve(text)
                try:
                    items = nba_search(text)["items"][:5]
                except UpstreamError:
                    items = []
                return self._json({"query": text, "keyword": keyword, "names": names_, "items": items})
            try:
                page = max(1, min(10, int(arg("page", "1"))))
            except ValueError:
                page = 1
            return self._json(nba_search(text, page))

        if u.path in ("/api/search", "/api/suggest", "/api/hot"):
            game = arg("game")
            if game not in GAMES:
                return self._json({"error": "請先選擇卡種"}, 400)
            if u.path == "/api/hot":
                return self._json({"game": game, "cards": search_cards("", game)["cards"][:24]})
            text = arg("q")[:80]
            if not text:
                return self._json({"error": "請輸入卡名或卡號"}, 400)
            keyword, names_ = resolve(text, game)
            if u.path == "/api/suggest":
                try:
                    cards = search_cards(keyword, game)["cards"][:6]
                except UpstreamError:
                    cards = []
                return self._json({"query": text, "keyword": keyword, "names": names_, "cards": cards})
            try:
                page = max(1, min(20, int(arg("page", "1"))))
            except ValueError:
                page = 1
            res = search_cards(keyword, game, page)
            return self._json({"query": text, "keyword": keyword, "names": names_,
                               "page": page, "cards": res["cards"], "hasMore": res["more"]})

        if u.path == "/api/card":
            cid = arg("id")
            if not cid.isdigit():
                return self._json({"error": "卡片編號不正確"}, 400)
            card = card_detail(cid)
            if arg("lite") == "1":
                card = {k: v for k, v in card.items() if k not in ("chart", "trades")}
            return self._json(card)

        self._json({"error": "Not found"}, 404)


def main():
    srv, port = None, PORT
    for port in range(PORT, PORT + 10):   # 埠號被佔用就往後找
        try:
            srv = ThreadingHTTPServer((HOST, port), Handler)
            break
        except OSError:
            continue
    if srv is None:
        print(f"埠號 {PORT}~{PORT + 9} 都被佔用了，請關掉其他視窗再試。")
        return
    srv.daemon_threads = True
    url = f"http://{HOST}:{port}"
    print("=" * 50)
    print(" TCG 查價已啟動：" + url)
    print(" 關掉這個視窗就會停止")
    print("=" * 50)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
