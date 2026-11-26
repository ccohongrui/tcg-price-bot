# 卡價 KAKAKU — 日版 PSA10 查價（本機版）

查 **航海王 / 寶可夢 / 遊戲王** 日版單卡的 **PSA10** 行情。資料來自 SNKRDUNK（スニダン）實際成交紀錄，自動換算台幣。

## 啟動

1. 雙擊 `start.bat`
2. 瀏覽器會自動開 http://127.0.0.1:8765 （埠號被佔用時會自動往後找，以黑色視窗顯示的網址為準）
3. 不用時關掉黑色視窗即可

只用 Python 標準函式庫，不需要 `pip install`。需要 Python 3.8 以上。
Mac / Linux：在資料夾裡執行 `python3 server.py`。

## 怎麼用

- 上排四個按鈕：**首頁 / 航海王 / 寶可夢 / 遊戲王**
- 搜尋框**打幾個中文字**就會跳出對應的卡名與卡片（例如打「月亮」→ 月亮伊布），不用全部打完
  - 點「卡名」那一列：看這個角色的全部卡片
  - 點有圖片的那一列：直接進這張卡的 PSA10 行情
- 也可以輸入卡號（最準）：`sv8a 217`、`OP05-119`、`QCCU-JP001`，或日文卡名
- 中文名後面可以加稀有度：`噴火龍 SAR`、`魯夫 SEC`
- 首頁會顯示最近查看的卡片、最近搜尋、三種卡的熱門排行

## 單卡頁顯示什麼

- 最新一筆 PSA10 成交價（台幣＋日圓）
- 最近 20 筆單張成交的 高 / 均 / 低，以及「幾天內成交幾張」
- PSA10 每日成交均價走勢圖（1 個月 / 3 個月 / 6 個月 / 1 年 / 全部，滑鼠移上去看當日價格）
- 近期每日成交張數、最近成交明細

## 中文卡名對照

| 卡種 | 來源 |
|---|---|
| 寶可夢 | `data/pokemon.json`（全圖鑑 1025 隻，繁中→日文）＋ `names.py` 裡的訓練家 |
| 航海王 | `names.py` 的 `ONEPIECE`（台灣譯名） |
| 遊戲王 | `names.py` 的 `YUGIOH`（台灣譯名），查不到時再問 ygocdb.com 中文卡名資料庫 |

想加新角色：打開 `names.py` 照格式加一行（`"中文名|別名": "日文關鍵字"`），重新啟動即可。
出新世代寶可夢時執行 `python tools/build_data.py` 更新圖鑑。

## 可調整的設定（`server.py` 最上面）

| 變數 | 預設 | 說明 |
|---|---|---|
| `PORT` | 8765 | 網頁埠號 |
| `SEARCH_TTL` / `CARD_TTL` | 10 / 15 分鐘 | 搜尋結果與單卡行情的快取時間 |
| `FALLBACK_JPY_TWD` | 0.21 | 抓不到即時匯率時用的匯率 |
| `REQUEST_GAP` | 0.2 秒 | 對 SNKRDUNK 的請求間隔 |

## 注意

- 個人查價用的小工具，請別調低請求間隔或拿去大量抓資料；SNKRDUNK 的使用條款以對方網站為準
- SNKRDUNK 只提供最近 20 筆成交明細，所以「每日成交張數」只涵蓋最近幾天；走勢圖則是完整的每日均價
- SNKRDUNK 改版時搜尋或 API 可能會失效，畫面會顯示錯誤訊息
- 匯率來自 open.er-api.com，僅供參考
- `data/card_meta.json` 是自動產生的卡片基本資料快取，刪掉也沒關係

## 線上版（GitHub Pages＋Cloudflare Worker）

- 網站：https://ccohongrui.github.io/tcg-price-bot/ （`index.html`，推到 `main` 分支就會自動更新）
- 後端：https://tcg-price-api.tcg-price-api.workers.dev （`worker/` 資料夾，功能等同 `server.py`）

GitHub Pages 只能放靜態檔案，所以線上版的資料由 Cloudflare Worker 代抓；用 `start.bat` 在本機開時則走 `server.py`，兩邊互不影響。

更新方式：

| 改了什麼 | 要做什麼 |
|---|---|
| `index.html` | `git add -A && git commit -m "..." && git push` |
| `worker/src/index.js` | 在 `worker` 資料夾執行 `npx wrangler deploy` |
| `names.py`（中文卡名） | 先 `python tools/build_worker_data.py`，再 `npx wrangler deploy` |

Worker 只接受來自 `https://ccohongrui.github.io` 與本機（localhost / 127.0.0.1）網頁的請求，要加其他網址請改 `worker/wrangler.toml` 的 `ALLOWED_ORIGINS`。
