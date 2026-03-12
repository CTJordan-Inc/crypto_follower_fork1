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

---

## 日期
- 2026-03-11

## 本次目標
- 針對單地址 MVP 做三件事：
  1) 降低重複查詢等待時間
  2) 區分「真的屯幣賺到」與「轉進轉出造成的倉位波動」
  3) 在 UI 補上更明確的詮釋說明
- 同時開始下一階段：支援最多 50 個地址的批量分析

## 本次完成項目（依時間序）

1. 單地址分析回應擴充
   - `app/schemas.py` 新增：
     - `NetworkRecomputeRequest`
     - `BatchRecomputeRequest`
     - `PerformanceBehavior`
     - `PerformanceMeta`
     - `BatchPerformanceItem`
     - `BatchPerformanceResponse`
   - `PerformanceResponse` 現在除了 NAV / metrics / quality，還會回：
     - `behavior`
     - `interpretations`
     - `meta`

2. 行為分析與回報來源拆解
   - 重寫 `app/services/performance.py` 的回應組裝流程。
   - 新增根據每日持倉變化與價格估算的資金流分析：
     - `gross_inflow_usd`
     - `gross_outflow_usd`
     - `net_flow_usd`
     - `market_pnl_usd`
     - `turnover_ratio`
     - `round_trip_ratio`
   - 新增地址風格分類：
     - `holding`
     - `accumulation`
     - `distribution`
     - `transit`
     - `mixed`
   - 新增回報來源分類：
     - `market_appreciation`
     - `net_transfers`
     - `mixed`
   - 新增中文詮釋說明，用來幫助判斷這個地址比較像屯幣、搬倉還是中轉。

3. 查詢速度優化（快取優先）
   - 新增 `AddressSyncState` 資料表於 `app/models.py`，記錄：
     - 最後同步區間
     - 最後同步時間
     - 最後耗時
     - 成功/失敗狀態
   - `app/services/performance.py` 新增：
     - `has_complete_cached_nav()`
     - `is_cached_sync_fresh()`
   - `app/services/network_sync.py` 新增：
     - `load_or_sync_address_performance()`
   - 行為：
     - 單地址查詢預設 `refresh=false`
     - 若 cache 仍新鮮且 NAV 資料完整，直接回傳快取，不重打 Etherscan / CoinGecko

4. 批量分析（最多 50 地址）
   - `app/services/network_sync.py` 新增：
     - `batch_load_or_sync_address_performance()`
   - `app/api/routes/performance.py` 新增：
     - `POST /api/v1/performance/batch/network/recompute`
   - 行為：
     - 最多 50 個地址
     - 預設逐個優先吃快取
     - 回傳每個地址的摘要結果與錯誤，不會因單一地址失敗而整批中斷

5. Dashboard UX 補強
   - `app/templates/index.html`
     - 新增「強制重抓鏈上與價格資料」選項
     - 新增地址行為判讀區
     - 新增詮釋說明區
     - 新增批量分析表單與結果表格
   - `app/static/dashboard.js`
     - 單地址查詢支援 `refresh`
     - 顯示 `behavior / interpretations / meta`
     - 新增批量分析請求與結果表格渲染
   - `app/static/dashboard.css`
     - 新增批量表格、說明卡片、checkbox row 樣式

6. 文件更新
   - `README.md` 補上：
     - 快取策略
     - 行為判讀口徑
     - 回報來源拆解
     - 批量分析 API 與使用方式

## 本次驗證
- 通過語法檢查：
  - `PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m py_compile $(find app tests -name '*.py' -type f)`
- 未能執行完整測試原因：
  - 目前 CLI 執行環境缺少 `fastapi` / `sqlalchemy` / `httpx` / `pytest`
  - 使用者本地已能啟動服務，但此 turn 的 shell 環境無對應套件

## 目前狀態
- 單地址 MVP 已補齊：
  - 可查單地址
  - 可顯示績效圖
  - 可判讀是否較偏屯幣或中轉
  - 可透過快取降低重複查詢時間
- 批量分析第一版已就位：
  - 可一次分析最多 50 個地址
  - 顯示摘要結果與行為分類

## 接下來建議
1. 若要真正提升 50 地址首輪分析速度，下一步應做背景任務佇列，而不是同步 HTTP 等待。
2. 若要更準確判斷「屯幣」與「中轉」，下一步可加入持倉存活天數或 7/30 天 retained balance 指標。
3. 若要長期使用，應加入 migration（目前 `create_all` 只適合 MVP）。
