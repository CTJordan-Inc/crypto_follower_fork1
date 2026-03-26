# crypto_follower

`crypto_follower` 的第一版 MVP 聚焦在「地址資產淨值（NAV）績效追蹤」，先讓你可以可靠地追蹤單一地址，再逐步擴展到推薦清單與監控面板。

## MVP 目前能力

- 提供 dashboard 網頁（可輸入地址直接查詢）。
- 透過 Etherscan + CoinGecko 自動同步地址資料並計算績效。
- 預設優先使用快取；重複查詢同地址同區間時可大幅縮短等待時間。
- 手動加入地址 watchlist。
- 匯入地址每日持倉與代幣每日價格資料。
- 依「Top N + 穩定幣 + 原生幣」組合計算 NAV 曲線。
- 計算關鍵績效指標：`CAGR`、`MDD`、`Sharpe`、`交易頻率`、`最大單日跌幅`。
- 拆解地址行為：`屯幣 / 累積 / 減倉 / 中轉 / 混合`。
- 拆解回報來源：`市場漲跌` vs `淨轉入/轉出`。
- 提供詮釋說明，避免把「搬倉造成的 NAV 變化」誤判成持幣績效。
- 支援最多 50 個地址的批量分析面板。
- 可從近期鏈上活動自動抽樣 50 個地址，直接帶入批量分析。
- 會自動保存每次分析結果，之後可直接回看同地址、同區間的結果。
- 提供資料品質標籤：價格來源、標註來源、疑似交易所/合約旗標。
- 提供候選地址推薦 API（以市值與穩定成長分數排序）。

## 技術棧

- Python
- FastAPI
- SQLAlchemy
- MySQL（可用 `DATABASE_URL` 切換；預設 SQLite 便於本地快速啟動）

## 啟動方式

1. 建立虛擬環境並安裝套件：

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. 設定環境變數（可複製 `.env.example`）：

   ```bash
   cp .env.example .env
   ```

   並至少填入：

   ```env
   ETHERSCAN_API_KEY=你的_api_key
   ETHERSCAN_PAGE_OFFSET=1000
   ETHERSCAN_MAX_PAGES=5
   ETHERSCAN_MAX_RETRIES=5
   ETHERSCAN_REQUEST_INTERVAL_SECONDS=0.35
   COINGECKO_API_KEY=你的_api_key_可選
   COINGECKO_MAX_RETRIES=4
   COINGECKO_REQUEST_INTERVAL_SECONDS=0.3
   COINGECKO_CONTRACT_CHUNK_SIZE=40
   SYNC_CACHE_TTL_MINUTES=720
   BATCH_ADDRESS_LIMIT=50
   SAVED_ANALYSIS_LIMIT=100
   RANDOM_ADDRESS_BLOCK_WINDOW=160
   RANDOM_ADDRESS_SAMPLE_BLOCKS=40
   ```

   註：Etherscan 會限制 `page * offset <= 10000`，建議維持預設 `offset=1000`。
   若遇到 Etherscan `Max calls per sec rate limit reached (3/sec)`，可提高 `ETHERSCAN_REQUEST_INTERVAL_SECONDS`（如 `0.6`）。
   若遇到 CoinGecko `429 Too Many Requests`，可設定 `COINGECKO_API_KEY` 並縮短日期區間。
   若遇到 CoinGecko `400`（token_price 批次查詢），系統會自動拆小批次重試並略過無效合約。
   若想改用 CoinCap 提供的價格資料，可將 `PRICE_PROVIDER=coincap` 並填入 `COINCAP_BASE_URL`/`COINCAP_API_KEY`（預設 header `Authorization`），系統會改用 CoinCap 的 `assets` 和 `history` API 取得 spot+daily 價格。

3. 啟動服務：

   ```bash
   uvicorn app.main:app --reload
   ```

4. 開啟文件：

   - Dashboard: `http://127.0.0.1:8000`
   - Swagger UI: `http://127.0.0.1:8000/docs`
   - Health check: `http://127.0.0.1:8000/health`

## Dashboard 查詢流程（推薦）

1. 打開 `http://127.0.0.1:8000`
2. 輸入 Ethereum 地址與日期區間
3. 點擊「查詢並計算績效」
4. 系統會呼叫 `/api/v1/performance/{address}/network/recompute`
5. 於頁面顯示 NAV 曲線、資金流拆解、行為判讀與詮釋說明

### 批量地址抽樣

- Dashboard 的「隨機產生 50 地址」會呼叫 `GET /api/v1/performance/random-addresses?count=50&exclude_saved=true`
- 來源是 Etherscan proxy 的近期區塊交易發送者（`from` address）
- 預設排除已保存過分析結果的地址，避免一直重複分析同一批
- 「最低地址市值」不再用於隨機抽樣階段，避免 random-address 請求因鏈上預篩而逾時
- 新流程是：
  1. 先快速抽 50 個地址
  2. 再跑批量分析
  3. 批量結果表依地址市值門檻隱藏低於門檻的成功結果
- 失敗結果不會因門檻被隱藏，避免使用者誤以為該地址根本沒跑
- 保存分析與正式績效頁顯示的地址市值，仍以該次完整分析的 NAV 結果為準
- 地址市值口徑可選：
  - `max_nav`：區間內最大 NAV（預設，較保守）
  - `average_nav`：區間內平均 NAV
- 若近期區塊內可用地址不足，回傳數量可能小於 50

### 快取策略

- `POST /api/v1/performance/{address}/network/recompute` 預設 `refresh=false`
- 若同地址與區間在 `SYNC_CACHE_TTL_MINUTES` 內已同步成功，會直接回傳快取結果
- 若要強制更新鏈上資料，勾選 dashboard 的「強制重抓鏈上與價格資料」
- CoinGecko 日價格也會重用本地已存在的 `coingecko` 價格資料；同一批熱門代幣在後續地址分析時通常會快很多
- sync 時現在會保存完整日持倉快照，而不是只保存 `Top N`，避免後續分析因舊快取缺 token 而失真
- 若某地址過去已同步過更早日期，系統會從最近的本地持倉快照接續增量同步，而不是每次都從 genesis replay
- 但對於第一次分析的全新地址，為了保證起始持倉正確，仍需走較慢的冷啟動全歷史路徑

### 行為判讀口徑

- `holding`: 倉位變動低，較接近長期持有
- `accumulation`: 有持續累積倉位，但仍需看回報是來自市場還是淨轉入
- `distribution`: 呈現逐步減倉
- `transit`: 轉入轉出頻繁，較像中轉或換倉地址
- `mixed`: 同時存在持幣與搬倉行為

回報來源拆解方式：

- `market_appreciation`: NAV 變動主要來自原有持倉漲跌
- `net_transfers`: NAV 變動主要來自淨轉入/轉出
- `mixed`: 兩者都有明顯貢獻

## MVP 資料流（單一地址）

1. `POST /api/v1/watchlist` 新增地址
2. `POST /api/v1/data/holdings/upsert` 匯入每日持倉
3. `POST /api/v1/data/prices/upsert` 匯入每日價格
4. `POST /api/v1/performance/{address}/recompute` 重新計算 NAV 與績效
5. `GET /api/v1/performance/{address}?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` 讀取結果

## 網路同步 API（前端實際使用）

- `POST /api/v1/performance/{address}/network/recompute`
  - 來源：Etherscan（交易/轉帳）、CoinGecko（每日 USD 價格）
  - 目前鏈別：Ethereum
  - request body: `{"start_date":"YYYY-MM-DD","end_date":"YYYY-MM-DD","top_n_tokens":10,"refresh":false}`

## 批量分析 API

- `POST /api/v1/performance/batch/network/recompute`
  - 一次最多 50 個地址，會排進背景任務後立即回 202
  - request body 與舊版相同，可指定 `market_cap_basis` 與 `refresh`；新增 `filter_empty_holdings` 參數，可先剔除在該日期範圍沒有持倉紀錄的地址（預設 false）
  - 回應範例：`{"batch_id": 123, "status": "pending", "requested": 2}`
- `GET /api/v1/performance/batch/{batch_id}`
  - 讀取 job 狀態、完成/失敗數、與已處理地址的摘要（含市值、行為、錯誤訊息）
  - Dashboard 會定期 poll 這個 endpoint，邊跑邊把結果更新到批量表格，並在 job 完成後自動刷新保存分析列表
- Dashboard UI 仍支援 `min_market_cap_usd` 過濾，會在結果表中隱藏低於門檻的項目，並顯示隱藏筆數
- `GET /api/v1/performance/random-addresses`
  - 從近期 Ethereum 區塊隨機抽樣地址
  - query params:
    - `count`：1~50
    - `exclude_saved`：是否排除已保存分析的地址，預設 `true`
  - `min_market_cap_usd` 只會原樣回傳給前端，實際門檻改在批量結果表套用，不在這個 endpoint 先過濾

範例：

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/performance/batch/network/recompute \
  -H "Content-Type: application/json" \
  -d '{
    "addresses": ["0xabc123", "0xdef456"],
    "start_date": "2025-01-01",
    "end_date": "2025-03-31",
    "top_n_tokens": 10,
    "market_cap_basis": "max_nav",
    "refresh": false,
    "filter_empty_holdings": true
  }'
```

收到 202 後，可用 job id 反覆 poll：

```bash
curl http://127.0.0.1:8000/api/v1/performance/batch/123
```

## 保存分析結果

- 每次單地址分析與批量分析，只要成功回傳結果，就會自動保存一份 snapshot
- 保存鍵為：`address + start_date + end_date + top_n_tokens`
- 若同一組條件再次分析，會更新既有 snapshot，不會重複新增
- Dashboard 下方的「已保存分析」可直接載入舊結果，不需要重新打外部 API
- 「已保存分析」可依 `保存時間 / 總報酬 / 地址市值` 排序
- 表格中的「地址市值」目前顯示區間內最大 NAV

相關 API：

- `GET /api/v1/performance/saved?limit=50&sort_by=saved_at&sort_order=desc`
  - 列出最近保存的分析摘要
- `GET /api/v1/performance/saved/{snapshot_id}`
  - 直接讀取當次保存的完整分析 payload

### 快速示例（先跑通單一地址績效）

```bash
# 1) 加入 watchlist
curl -X POST http://127.0.0.1:8000/api/v1/watchlist \
  -H "Content-Type: application/json" \
  -d '{
    "address": "0xabc123",
    "label": "demo_whale",
    "label_source": "manual",
    "is_exchange": false,
    "is_contract": false
  }'

# 2) 匯入持倉（ETH + USDC）
curl -X POST http://127.0.0.1:8000/api/v1/data/holdings/upsert \
  -H "Content-Type: application/json" \
  -d '[
    {"address":"0xabc123","date":"2025-01-01","token_symbol":"ETH","balance":10},
    {"address":"0xabc123","date":"2025-01-01","token_symbol":"USDC","balance":20000},
    {"address":"0xabc123","date":"2025-01-02","token_symbol":"ETH","balance":10},
    {"address":"0xabc123","date":"2025-01-02","token_symbol":"USDC","balance":20000},
    {"address":"0xabc123","date":"2025-01-03","token_symbol":"ETH","balance":9.5},
    {"address":"0xabc123","date":"2025-01-03","token_symbol":"USDC","balance":21000}
  ]'

# 3) 匯入價格
curl -X POST http://127.0.0.1:8000/api/v1/data/prices/upsert \
  -H "Content-Type: application/json" \
  -d '[
    {"token_symbol":"ETH","date":"2025-01-01","price_usd":3200,"source":"manual"},
    {"token_symbol":"ETH","date":"2025-01-02","price_usd":3300,"source":"manual"},
    {"token_symbol":"ETH","date":"2025-01-03","price_usd":3400,"source":"manual"},
    {"token_symbol":"USDC","date":"2025-01-01","price_usd":1,"source":"manual"},
    {"token_symbol":"USDC","date":"2025-01-02","price_usd":1,"source":"manual"},
    {"token_symbol":"USDC","date":"2025-01-03","price_usd":1,"source":"manual"}
  ]'

# 4) 重算績效
curl -X POST http://127.0.0.1:8000/api/v1/performance/0xabc123/recompute \
  -H "Content-Type: application/json" \
  -d '{"start_date":"2025-01-01","end_date":"2025-01-03","top_n_tokens":10}'
```

## 推薦地址 API

- `POST /api/v1/data/candidates/upsert` 匯入候選地址資料
- `GET /api/v1/watchlist/recommendations?min_market_cap=10000000&limit=20`

## 後續規劃

- 鏈上資料來源整合（Chainlink / CoinGecko / Etherscan / Nansen / Arkham）。
- 大額交易事件偵測與推播。
- 地址聚合（人/公司）與標註可信度模型。
