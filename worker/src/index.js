// 卡價 KAKAKU — Cloudflare Worker 版後端（對應本機的 server.py）
// 只查日版 PSA10，資料來源：SNKRDUNK 成交紀錄
import DATA from "./data.json";

const BASE = "https://snkrdunk.com";
const SEARCH_TTL = 10 * 60;        // 搜尋結果快取（秒）
const CARD_TTL = 15 * 60;          // 單卡行情快取
const META_TTL = 7 * 24 * 60 * 60; // 卡片基本資料快取
const RATE_TTL = 6 * 60 * 60;      // 匯率快取
const FALLBACK_JPY_TWD = 0.21;
const PSA10 = "trading_card_single_psa10";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36";

const GAMES = {
  onepiece: { brand: "onepiece", label: "航海王" },
  pokemon: { brand: "pokemon", label: "寶可夢" },
  yugioh: { brand: "yu-gi-oh", label: "遊戲王" },
};
const BRAND_TO_GAME = Object.fromEntries(Object.entries(GAMES).map(([k, v]) => [v.brand, k]));

class UpstreamError extends Error {}

// ───────────── 快取過的對外請求 ─────────────
async function cachedFetch(url, { ttl, accept = "application/json", ctx, headers = {} }) {
  const cache = caches.default;
  const key = new Request("https://cache.internal/" + encodeURIComponent(url));
  const hit = await cache.match(key);
  if (hit) return hit.text();
  let res;
  try {
    res = await fetch(url, {
      headers: { "User-Agent": UA, Accept: accept, "Accept-Language": "ja,zh-TW;q=0.9,en;q=0.8", ...headers },
    });
  } catch (e) {
    throw new UpstreamError("連不到資料來源：" + e.message);
  }
  if (res.status === 404) throw new UpstreamError("SNKRDUNK 找不到這張卡");
  if (!res.ok) throw new UpstreamError(`SNKRDUNK 回應 HTTP ${res.status}，請稍後再試`);
  const text = await res.text();
  const put = cache.put(key, new Response(text, { headers: { "Cache-Control": `max-age=${ttl}` } }));
  ctx ? ctx.waitUntil(put) : await put;
  return text;
}
async function snkJson(url, ttl, ctx) {
  const text = await cachedFetch(url, { ttl, ctx, headers: { Referer: BASE + "/" } });
  try { return JSON.parse(text); } catch (e) { throw new UpstreamError("SNKRDUNK 回傳的資料格式不對（可能改版了）"); }
}

// ───────────── 中文 → 日文卡名 ─────────────
const HAN = /[一-鿿]/;
const KANA = /[぀-ヿ]/;
const escRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const toTrad = s => [...s].map(c => DATA.s2t[c] || c).join("");

class Dictionary {
  constructor(entries, modifiers, display) {
    this.entries = entries;
    this.zh2ja = new Map([...entries, ...Object.entries(modifiers)]);
    this.ja2zh = new Map();
    for (const [zh, ja] of [...entries, ...Object.entries(modifiers)]) if (!this.ja2zh.has(ja)) this.ja2zh.set(ja, zh);
    for (const [ja, zh] of Object.entries(display || {})) this.ja2zh.set(ja, zh);
    const alt = m => [...m.keys()].sort((a, b) => b.length - a.length).map(escRe).join("|");
    this.zhRe = this.zh2ja.size ? new RegExp(alt(this.zh2ja), "g") : null;
    this.jaRe = this.ja2zh.size ? new RegExp(alt(this.ja2zh), "g") : null;
  }
  translate(text) {
    if (!this.zhRe) return [text, false];
    let changed = false;
    const out = text.split(" ").map(tok => {
      // 整個詞都認得才換（「黑魔導女」這種打到一半的不硬翻）
      if (HAN.test(tok) && !HAN.test(tok.replace(this.zhRe, ""))) {
        changed = true;
        return tok.replace(this.zhRe, m => this.zh2ja.get(m));
      }
      return tok;
    });
    return [out.join(" "), changed];
  }
  toZh(title) {
    if (!this.jaRe) return null;
    const head = title.split(/\s*[:：(（]/)[0];
    let n = 0;
    const out = head.replace(this.jaRe, m => { n++; return this.ja2zh.get(m); });
    return !n || KANA.test(out) ? null : out.trim();
  }
  suggest(part, limit = 8) {
    const starts = [], contains = [];
    for (const [zh, ja] of this.entries) {
      if (zh.startsWith(part)) starts.push([zh, ja]);
      else if (zh.includes(part)) contains.push([zh, ja]);
    }
    const byLen = (a, b) => a[0].length - b[0].length;
    const seen = new Set(), out = [];
    for (const [zh, ja] of [...starts.sort(byLen), ...contains.sort(byLen)]) {
      if (seen.has(ja)) continue;
      seen.add(ja);
      out.push({ zh, ja });
      if (out.length >= limit) break;
    }
    return out;
  }
}
const DICTS = {};
for (const g of Object.keys(GAMES)) DICTS[g] = new Dictionary(DATA.entries[g], DATA.modifiers[g] || {}, DATA.display[g] || {});

async function ygocdbSuggest(part, ctx) {
  try {
    const text = await cachedFetch("https://ygocdb.com/api/v0/?search=" + encodeURIComponent(part), { ttl: 3600, ctx });
    return (JSON.parse(text).result || []).slice(0, 8)
      .filter(x => x.jp_name && x.cn_name)
      .map(x => ({ zh: toTrad(x.cn_name), ja: x.jp_name }));
  } catch (e) { return []; }
}

async function resolve(input, game, ctx) {
  const q = input.split(/\s+/).filter(Boolean).join(" ");
  const d = DICTS[game];
  if (!HAN.test(q) || KANA.test(q)) return [q, []];   // 卡號、英文、日文：原樣搜尋
  const tokens = q.split(" ");
  const part = tokens[0], rest = tokens.slice(1).join(" ");
  let names = HAN.test(part) ? d.suggest(part) : [];
  if (game === "yugioh" && part.length >= 2 && names.length < 4) {
    const have = new Set(names.map(n => n.ja));
    const extra = (await ygocdbSuggest(part, ctx)).filter(n => !have.has(n.ja));
    names = names.concat(extra.slice(0, Math.max(0, 5 - names.length)));
  }
  const [translated, changed] = d.translate(q);
  const keyword = changed ? translated : names.length ? (names[0].ja + " " + rest).trim() : q;
  for (const n of names) n.keyword = (n.ja + " " + rest).trim();
  return [keyword, names];
}

// ───────────── SNKRDUNK：搜尋 ─────────────
const TILE_RE = /<a href="(?:https:\/\/snkrdunk\.com)?\/apparels\/(\d+)"[^>]*aria-label="([^"]*)"[^>]*>([\s\S]*?)<\/a>/g;
const IMG_RE = /<img[^>]+src="(https:\/\/cdn\.snkrdunk\.com\/[^"]+)"/;
const PRICE_SUFFIX_RE = /\s*-\s*¥([\d,]+)\s*$/;
const FOREIGN_RE = /【[^】]*語版】|英語版|中国語版|韓国語版|簡体字|繁体字/;
const NAME_RE = /^(.*?)\s*\[([^\]]+)\]\s*(?:[（(](.*)[）)])?/;
const ENT = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
const unescapeHtml = s => s.replace(/&(#x?[0-9a-f]+|\w+);/gi, (m, e) =>
  e[0] === "#" ? String.fromCodePoint(e[1].toLowerCase() === "x" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10)) : (ENT[e] ?? m));

function splitName(name, game) {
  const m = NAME_RE.exec(name);
  const [title, number, series] = m ? [m[1], m[2], m[3] || ""] : [name, "", ""];
  return { title: title.trim(), number: number.trim(), series: series.trim(), zh: DICTS[game] ? DICTS[game].toZh(title) : null };
}

function parseTiles(page, game, mode) {
  const ranked = [], normal = [];
  for (const m of page.matchAll(TILE_RE)) {
    let label = unescapeHtml(m[2]);
    const inner = m[3];
    let price = null;
    const pm = PRICE_SUFFIX_RE.exec(label);
    if (pm) { price = parseInt(pm[1].replace(/,/g, ""), 10); label = label.slice(0, pm.index); }
    if (FOREIGN_RE.test(label)) continue;   // 只要日版
    const im = IMG_RE.exec(inner);
    const card = { id: +m[1], name: label, game, listPrice: price, image: im ? unescapeHtml(im[1]) : null, ...splitName(label, game) };
    (inner.includes("ランキングバッジ") ? ranked : normal).push(card);
  }
  const order = { hot: [...ranked, ...normal], first: [...normal, ...ranked], next: normal }[mode];
  const seen = new Set(), cards = [];
  for (const c of order) if (!seen.has(c.id)) { seen.add(c.id); cards.push(c); }
  return { cards, more: normal.length >= 15 };
}

async function searchCards(keyword, game, page, ctx) {
  const params = new URLSearchParams({ brandIds: GAMES[game].brand, searchCategoryIds: "6/33", page: String(page) });
  if (keyword) params.set("keywords", keyword);
  const html = await cachedFetch(`${BASE}/search?${params}`, { ttl: SEARCH_TTL, accept: "text/html", ctx, headers: { Referer: BASE + "/" } });
  return parseTiles(html, game, !keyword ? "hot" : page === 1 ? "first" : "next");
}

// ───────────── SNKRDUNK：單卡 PSA10 行情 ─────────────
function psa10Summary(trades, points) {
  trades = [...trades].sort((a, b) => (b.soldAt || "").localeCompare(a.soldAt || ""));
  let singles = trades.filter(t => (t.label || "1枚") === "1枚");
  if (!singles.length) singles = trades;
  const out = { last: null, lastAt: null, high: null, low: null, avg: null, count: singles.length, days: 0, change30: null };
  if (singles.length) {
    const prices = singles.map(t => t.price);
    Object.assign(out, {
      last: prices[0], lastAt: singles[0].soldAt || null, high: Math.max(...prices), low: Math.min(...prices),
      avg: Math.round(prices.reduce((a, b) => a + b, 0) / prices.length),
    });
    const days = [...new Set(singles.map(t => (t.soldAt || "").slice(0, 10)))].sort();
    const span = (Date.parse(days[days.length - 1]) - Date.parse(days[0])) / 864e5;
    out.days = Number.isFinite(span) ? Math.round(span) + 1 : days.length;
  } else if (points.length) {
    out.last = points[points.length - 1][1];
  }
  if (points.length >= 2) {
    const [nowTs, nowPrice] = points[points.length - 1];
    const target = nowTs - 30 * 864e5;
    for (let i = points.length - 1; i >= 0; i--) {
      if (points[i][0] <= target) { out.change30 = Math.round((nowPrice - points[i][1]) / points[i][1] * 1000) / 10; break; }
    }
  }
  return out;
}

async function cardDetail(id, ctx) {
  const a = await snkJson(`${BASE}/v1/apparels/${id}`, META_TTL, ctx);
  const game = BRAND_TO_GAME[((a.brands || [{}])[0] || {}).id] || null;
  const name = a.localizedName || a.name || "";
  let trades = [], points = [];
  if (a.productCatalogId) {
    const h = await snkJson(`${BASE}/v3/products/${a.productCatalogId}/trading-history?range=all&condition_code=${PSA10}`, CARD_TTL, ctx);
    trades = (h.trades || []).filter(t => t.title === "PSA10" && t.price);
    const lines = (h.chart || {}).lines || [];
    if (lines.length) points = (lines[0].points || []).filter(p => p.price && p.timestamp).map(p => [p.timestamp, p.price]);
  }
  return {
    id: +id, name, image: (a.primaryMedia || {}).imageUrl || null, game, releasedAt: a.displayReleasedAt || "",
    ...splitName(name, game || ""),
    url: `${BASE}/apparels/${id}`,
    psa10: psa10Summary(trades, points),
    trades: [...trades].sort((x, y) => (y.soldAt || "").localeCompare(x.soldAt || ""))
      .map(t => ({ price: t.price, soldAt: t.soldAt || null, label: t.label || "" })),
    chart: points,
  };
}

async function rate(ctx) {
  try {
    const d = JSON.parse(await cachedFetch("https://open.er-api.com/v6/latest/JPY", { ttl: RATE_TTL, ctx }));
    return { rate: Number(d.rates.TWD), source: "open.er-api.com", live: true };
  } catch (e) {
    return { rate: FALLBACK_JPY_TWD, source: "預設值（抓不到即時匯率）", live: false };
  }
}

// ───────────── HTTP ─────────────
function corsOrigin(request, env) {
  const origin = request.headers.get("Origin");
  if (!origin) return null;
  if (/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin)) return origin;
  const allowed = (env.ALLOWED_ORIGINS || "").split(",").map(s => s.trim()).filter(Boolean);
  return allowed.includes("*") ? "*" : allowed.includes(origin) ? origin : null;
}

async function route(url, ctx) {
  const arg = (k, d = "") => (url.searchParams.get(k) || d).trim();
  const p = url.pathname;
  if (p === "/" || p === "") return [{ ok: true, service: "tcg-price-api" }, 200];
  if (p === "/api/rate") return [await rate(ctx), 200];
  if (p === "/api/search" || p === "/api/suggest" || p === "/api/hot") {
    const game = arg("game");
    if (!GAMES[game]) return [{ error: "請先選擇卡種" }, 400];
    if (p === "/api/hot") return [{ game, cards: (await searchCards("", game, 1, ctx)).cards.slice(0, 24) }, 200];
    const text = arg("q").slice(0, 80);
    if (!text) return [{ error: "請輸入卡名或卡號" }, 400];
    const [keyword, names] = await resolve(text, game, ctx);
    if (p === "/api/suggest") {
      let cards = [];
      try { cards = (await searchCards(keyword, game, 1, ctx)).cards.slice(0, 6); } catch (e) { if (!(e instanceof UpstreamError)) throw e; }
      return [{ query: text, keyword, names, cards }, 200];
    }
    const page = Math.max(1, Math.min(20, parseInt(arg("page", "1"), 10) || 1));
    const res = await searchCards(keyword, game, page, ctx);
    return [{ query: text, keyword, names, page, cards: res.cards, hasMore: res.more }, 200];
  }
  if (p === "/api/card") {
    const id = arg("id");
    if (!/^\d+$/.test(id)) return [{ error: "卡片編號不正確" }, 400];
    const card = await cardDetail(id, ctx);
    if (arg("lite") === "1") { delete card.chart; delete card.trades; }
    return [card, 200];
  }
  return [{ error: "Not found" }, 404];
}

export default {
  async fetch(request, env, ctx) {
    const origin = corsOrigin(request, env);
    const headers = { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", Vary: "Origin" };
    if (origin) headers["Access-Control-Allow-Origin"] = origin;
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: { ...headers, "Access-Control-Allow-Methods": "GET, OPTIONS", "Access-Control-Max-Age": "86400" } });
    }
    if (request.method !== "GET") return new Response(JSON.stringify({ error: "Method not allowed" }), { status: 405, headers });
    // 有帶 Origin 但不在允許清單：不替別人的網站代抓資料
    if (request.headers.get("Origin") && !origin) {
      return new Response(JSON.stringify({ error: "這個來源未被允許使用此 API" }), { status: 403, headers });
    }
    let body, status;
    try {
      [body, status] = await route(new URL(request.url), ctx);
    } catch (e) {
      [body, status] = e instanceof UpstreamError ? [{ error: e.message }, 502] : [{ error: "伺服器內部錯誤：" + e.message }, 500];
    }
    return new Response(JSON.stringify(body), { status, headers });
  },
};
