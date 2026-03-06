# crypto_follower 開發紀錄

## 日期
- 2026-03-06

## 任務來源
- 依據 `development-task.md` 啟動 MVP。
- 目標：先支援單一地址，可靠計算 NAV 與績效指標。

## 本次完成項目（依時間序）

1. 需求拆解與 MVP 設計
   - 確認先做資料流：`watchlist -> holdings/prices ingest -> recompute NAV -> 查詢績效`。
   - 確認優先口徑為「資產淨值型（Equity Curve / NAV）」。
   - 設計先提供地址推薦資料表（候選地址可先手動匯入），避免一開始綁定外部 API。

2. 專案骨架建立（FastAPI + SQLAlchemy）
   - 新增 `app/main.py`、`app/core/config.py`、`app/db/session.py`。
   - 新增資料模型 `app/models.py`：
     - `WatchlistAddress`
     - `AddressCandidate`
     - `AddressDailyHolding`
     - `TokenDailyPrice`
     - `AddressDailyNAV`
   - 新增 schema `app/schemas.py`（請求/回應與 validator）。
   - 新增環境範例 `.env.example`，並在 `README.md` 補啟動說明。

3. NAV / 績效計算核心實作
   - 新增 `app/services/metrics.py`：
     - `calculate_daily_returns`
     - `calculate_cagr`
     - `calculate_max_drawdown`
     - `calculate_sharpe_ratio`
     - `calculate_trade_frequency`
     - `build_metrics`
   - 新增 `app/services/performance.py`：
     - 價格來源優先順序：`Chainlink > CoinGecko > manual`
     - 代幣選擇策略：`Top N + 穩定幣 + 原生幣`
     - NAV 曲線計算與落庫
     - 資料品質標籤組合（價格來源、缺價天數、label source、exchange/contract flag）

4. API 路由實作
   - `app/api/routes/watchlist.py`
     - `POST /api/v1/watchlist`
     - `GET /api/v1/watchlist`
     - `GET /api/v1/watchlist/recommendations`
   - `app/api/routes/data.py`
     - `POST /api/v1/data/holdings/upsert`
     - `POST /api/v1/data/prices/upsert`
     - `POST /api/v1/data/candidates/upsert`
   - `app/api/routes/performance.py`
     - `POST /api/v1/performance/{address}/recompute`
     - `GET /api/v1/performance/{address}`

5. 測試與修正
   - 新增 `tests/test_metrics.py`（MDD、交易頻率、metrics 基本欄位）。
   - 執行 `python3 -m py_compile` 首次遇到 cache 權限錯誤，改以 `PYTHONPYCACHEPREFIX=/tmp/pycache` 解決。
   - 發現執行環境為 Python 3.9，`|` 型別註解在執行時出錯。
     - 修正：在下列檔案加上 `from __future__ import annotations`
       - `app/models.py`
       - `app/schemas.py`
       - `app/services/metrics.py`
       - `app/services/performance.py`
   - 以內嵌 assert 腳本驗證 metrics 核心計算通過。

6. 文件補強
   - 在 `README.md` 補上單一地址從 watchlist 到 recompute 的完整 `curl` 範例。

## 目前專案狀態
- 已可支援 MVP 基本閉環：
  1) 新增地址
  2) 匯入日持倉與日價格
  3) 重算 NAV 與績效
  4) 讀取結果與資料品質標籤
- `pytest` 尚未在本地環境安裝（`No module named pytest`），目前以 `py_compile` + 內嵌 assert 驗證。

## 下次開發建議（優先）
1. 補真實資料來源接線
   - Price: Chainlink / CoinGecko connector
   - Address label: Etherscan / Nansen / Arkham connector

2. 補交易事件偵測（MVP 第 5 點）
   - 偵測新建倉
   - 偵測大額 swap（可先用 USD 門檻）
   - 推播通道（先 webhook，再擴展 Telegram/Slack）

3. 提升績效可靠性
   - 增加「缺價補值策略」與資料完整度分數
   - 增加 benchmark（如 BTC/ETH）相對績效
   - 補整合測試（API + DB）

4. 部署與資料層
   - 補 migration（Alembic）
   - 設定 MySQL 正式環境連線與索引優化

## 本次修改檔案清單
- `README.md`
- `.env.example`
- `requirements.txt`
- `app/main.py`
- `app/core/config.py`
- `app/db/session.py`
- `app/models.py`
- `app/schemas.py`
- `app/services/metrics.py`
- `app/services/performance.py`
- `app/api/router.py`
- `app/api/routes/watchlist.py`
- `app/api/routes/data.py`
- `app/api/routes/performance.py`
- `tests/test_metrics.py`
- `progress.md`

## 追加開發（同日第二輪，回應使用者要求介面 + 網路查詢）

### 使用者新需求
- 使用者指出僅 API 不足，MVP 至少要能：
  1) 有可輸入地址的介面
  2) 直接查詢網路資料
  3) 顯示績效圖表

### 本輪完成項目

1. Dashboard 首頁
   - 在 `app/main.py` 新增 `/` 路由，回傳模板頁面。
   - 新增靜態資源掛載：`/static`。
   - 新增檔案：
     - `app/templates/index.html`
     - `app/static/dashboard.css`
     - `app/static/dashboard.js`

2. 網路資料同步服務（Ethereum）
   - 新增 `app/services/network_sync.py`，完成：
     - 透過 Etherscan 抓 `txlist`、`tokentx`。
     - 還原地址每日持倉快照（含 ETH gas 成本扣減）。
     - 同 symbol 多合約時自動分流 token key（避免衝突）。
     - 透過 CoinGecko 抓每日 USD 價格（ETH + ERC20 合約價）。
     - 將 holdings/prices 寫回 DB，再呼叫既有 NAV 引擎重算。

3. 新增 API（前端使用）
   - `POST /api/v1/performance/{address}/network/recompute`
   - 路由位置：`app/api/routes/performance.py`

4. 設定與依賴補強
   - `requirements.txt` 新增：`httpx`、`jinja2`
   - `app/core/config.py` 新增外部資料設定：
     - `etherscan_base_url`
     - `etherscan_chain_id`
     - `etherscan_api_key`
     - `etherscan_max_pages`
     - `coingecko_base_url`
     - `coingecko_platform`
     - `http_timeout_seconds`
     - `default_lookback_days`
   - `.env.example` 補上述對應環境變數。

5. 測試補充
   - 新增 `tests/test_network_sync.py`：
     - 驗證 ETH + gas 的日持倉還原
     - 驗證同 symbol 多合約分流邏輯
   - 目前環境仍未安裝套件（包含 `sqlalchemy` / `pytest`），故僅做語法層級檢查。

### 本輪驗證
- 通過：`PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m py_compile $(find app tests -name '*.py' -type f)`
- 未能執行完整單元測試原因：本機尚未安裝 `requirements.txt` 依賴。

### 目前可對使用者交付內容
- 可直接開 `http://127.0.0.1:8000` 看 Dashboard。
- 可在介面輸入地址，呼叫網路同步 API，自動算 NAV 與績效，並繪製曲線圖。

### 風險與限制（需讓下位 AI 知道）
1. 目前網路同步只做 Ethereum。
2. Etherscan 需 `ETHERSCAN_API_KEY`；未設定會直接回錯。
3. 若地址歷史交易過多，會受 `ETHERSCAN_MAX_PAGES` 限制，可能截斷早期資料。
4. CoinGecko 可能對小幣或新幣無價格資料，會導致缺價。

### 下一步建議
1. 新增同步品質標記（如 `is_history_truncated`）回傳前端。
2. 對缺價 token 提供替代來源（Chainlink、Dex TWAP）。
3. 加入快取與背景任務，避免前端等待過久。
4. 擴展到多鏈（Base、Arbitrum、BSC）。
