# crypto_follower

`crypto_follower` 的第一版 MVP 聚焦在「地址資產淨值（NAV）績效追蹤」，先讓你可以可靠地追蹤單一地址，再逐步擴展到推薦清單與監控面板。

## MVP 目前能力

- 提供 dashboard 網頁（可輸入地址直接查詢）。
- 透過 Etherscan + CoinGecko 自動同步地址資料並計算績效。
- 手動加入地址 watchlist。
- 匯入地址每日持倉與代幣每日價格資料。
- 依「Top N + 穩定幣 + 原生幣」組合計算 NAV 曲線。
- 計算關鍵績效指標：`CAGR`、`MDD`、`Sharpe`、`交易頻率`、`最大單日跌幅`。
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
   ```

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
5. 於頁面顯示 NAV 曲線與關鍵指標

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
