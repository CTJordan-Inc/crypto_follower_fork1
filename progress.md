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

## 日期
- 2026-03-13

## 本次 random-address timeout 修正
- 背景：
  - 使用者回報「隨機產生 50 地址」仍會出現 `失敗：請求逾時，請稍後再試`

## 本次調整
- `app/services/network_sync.py`
  - `generate_random_recent_addresses()` 對陌生地址的 network screening 增加硬上限
  - 快速預篩改成只抓區間內有限頁數事件，避免單次 random-address 請求變成多地址完整歷史同步
  - `_fetch_etherscan_events()` 新增 `max_pages` 參數供快速預篩使用
- `app/core/config.py`
  - 新增：
    - `random_address_network_screen_limit`
    - `random_address_screen_max_pages`
- `.env.example`
  - 補上對應設定
- `app/static/dashboard.js`
  - 隨機地址狀態文案改成「快速預篩（避免逾時）」
- `app/templates/index.html`
  - 批量分析提示文案補充：最低地址市值在這裡是快速預篩，不是完整精算

## 本次驗證
- `.venv/bin/pytest -q`
  - `23 passed`
- `PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m py_compile $(find app tests -name '*.py' -type f)`
- `node --check app/static/dashboard.js`

## 2026-03-13 隨機地址快速預篩優化

### 背景
- 使用者回報「隨機產生 50 地址」仍會出現前端 timeout
- 問題集中在：設定最低地址市值時，系統會對陌生地址做高成本鏈上預估

### 本次調整
- `app/services/network_sync.py`
  - `generate_random_recent_addresses()` 對陌生地址的 network screening 加上數量上限
  - 快速預篩改成只抓有限頁數事件，避免單次 random-address 請求變成多地址完整歷史同步
- `app/core/config.py`
  - 新增：
    - `random_address_network_screen_limit`
    - `random_address_screen_max_pages`
- `app/static/dashboard.js`
  - 隨機地址狀態文案改成「快速預篩（避免逾時）」
- `app/templates/index.html`
  - 批量分析提示文案補充：最低地址市值是快速預篩，不是完整精算

### 驗證
- `tests/test_network_sync.py`
  - 新增 `test_generate_random_recent_addresses_limits_unsaved_network_screening`

## 日期
- 2026-03-12

## 本次小幅 UI 調整
- 使用者要求 hover button 時要有變色特效
- 已在 `app/static/dashboard.css` 補上：
  - button hover 顏色變化
  - 陰影效果
  - 輕微上浮位移
  - transition 過渡動畫
- 範圍包含：
  - 主要按鈕
  - 次要按鈕（包含隨機地址、保存分析載入按鈕）

## 日期
- 2026-03-12

## 本次效能優化（單地址/批量共用）
- 背景：
  - 目前冷啟動查詢的主要瓶頸不是本地 NAV 計算，而是外部 API 抓取與重複抓價格
  - 先做不改資料來源口徑、但能顯著減少重複 I/O 的優化

## 本次完成項目

1. CoinGecko 價格快取重用
   - `app/services/network_sync.py` 新增：
     - `_load_recent_cached_coingecko_prices()`
     - `_load_cached_coingecko_price_series()`
     - `_series_covers_active_dates()`
     - `_build_token_active_dates()`
   - 行為：
     - spot ranking 前先看本地 DB 是否已有近期 `coingecko` 價格
     - 每個 selected token 在抓歷史價格前，先判斷 DB 內既有日價格是否已覆蓋持倉活躍日期
     - 若已覆蓋，就直接重用，不再重打 CoinGecko
     - 若只覆蓋一部分，就合併既有價格與新抓到的價格

2. 保存完整 holdings，而不是只存 Top N
   - 調整 `app/services/network_sync.py` 的 `_replace_holdings()`
   - 之前只把 selected tokens 寫入 `address_daily_holdings`
   - 現在會把該區間內所有正持倉都寫入 DB
   - 目的：
     - 避免未來重算/改區間時，舊快取少了 token 導致分析失真
     - 為後續做起始持倉 checkpoint / incremental sync 打基礎

3. 測試補充
   - `tests/test_network_sync.py`
     - 新增價格覆蓋判斷測試
     - 新增完整 holdings 寫入測試

4. 文件補充
   - `README.md`
     - 快取策略新增「跨地址共用 CoinGecko 價格快取」
     - 補充現在保存的是完整 holdings 快照

## 本次驗證
- 通過語法檢查：
  - `PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m py_compile $(find app tests -name '*.py' -type f)`
- 通過前端 JS 語法檢查：
  - `node --check app/static/dashboard.js`

## 日期
- 2026-03-12

## 本次持續優化與專案級檢視
- 使用者要求：
  1) 繼續優化效能
  2) 優化後重新謹慎檢視整個專案，確認是否有潛在邏輯錯誤

## 本次新增優化

1. 增量同步邏輯修正
   - `app/services/network_sync.py`
   - 修正點：
     - 原本若使用舊 holdings 快照作為 initial state，會漏掉 `快照日 + 1` 到 `start_date - 1` 之間的事件
     - 這會導致起始持倉錯誤，進而讓整段績效失真
   - 已修正：
     - `build_daily_balances_from_events()` 新增 `initial_snapshot_date`
     - 若有 initial snapshot，會先把 snapshot 之後、`start_date` 之前的 delta 補回去，再開始算區間

2. cache-hit 與 fresh recompute 口徑對齊
   - `app/services/performance.py`
   - 修正點：
     - 自從 holdings 改成保存完整快照後，`get_cached_address_performance()` 會把所有 token 都當成 selected tokens
     - 造成 cache-hit 結果與 fresh recompute 的 `top_n_tokens` 口徑不一致
   - 已修正：
     - `get_cached_address_performance()` 新增 `top_n_tokens`
     - cache-hit 現在也會重新跑同一套 `Top N + 穩定幣 + 原生幣` 選幣邏輯
     - `GET /api/v1/performance/{address}` 也補上 `top_n_tokens` query param

3. 零持倉日不再讓快取失效
   - `app/services/performance.py`
   - 修正點：
     - 原本若某幾天沒有持倉 row，NAV 曲線會少這些日期
     - `has_complete_cached_nav()` 會因此誤判快取不完整，導致重複同步
   - 已修正：
     - `_build_daily_holding_view()` 現在會預先補齊 `start_date ~ end_date` 的所有日期
     - 空倉日會以 `0 NAV / 空 snapshot` 保留在計算結果裡

4. sync 狀態錯誤資訊修正
   - `app/services/network_sync.py`
   - 修正點：
     - `_upsert_sync_state()` 之前只有在第一次失敗時才會記錄 error
     - 若地址曾成功同步過，之後再失敗，錯誤資訊不會更新
   - 已修正：
     - error 狀態現在每次都會覆蓋更新 `last_status / last_error / last_runtime_seconds`

5. 測試基礎修正
   - 新增 `pytest.ini`
   - 修正點：
     - 原本 `pytest` 在本機無法 import `app`
   - 已修正：
     - 補 `pythonpath = .`
     - 現在可直接跑 `.venv/bin/pytest`

6. 測試穩定性修正
   - `tests/test_metrics.py`
   - 修正點：
     - 原本用 `==` 比 float，容易被浮點誤差打爆
   - 已修正：
     - 改成 `pytest.approx`
   - `app/services/metrics.py`
     - 補 `_round_metric()`，讓 API 輸出的 metrics 更穩定整潔

## 本次 review 發現並已修復的潛在邏輯錯誤
1. 增量同步會漏算快照與查詢起點之間的事件
2. cache-hit 不遵守 `top_n_tokens`
3. 空倉日會讓快取永遠被判定為不完整
4. sync error state 在曾成功後不會再更新
5. pytest 基礎設定缺失，導致測試表面上存在、實際上不能直接跑

## 本次測試結果
- `.venv/bin/pytest -q`
  - `18 passed`
- `PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m py_compile $(find app tests -name '*.py' -type f)`
  - 通過
- `node --check app/static/dashboard.js`
  - 通過

## 日期
- 2026-03-13

## 本次功能優化（市值篩選 / 保存列表 / UI 說明）

1. 隨機地址支援地址市值過濾
   - `GET /api/v1/performance/random-addresses` 現在新增：
     - `start_date`
     - `end_date`
     - `top_n_tokens`
     - `min_market_cap_usd`
     - `market_cap_basis`
   - 流程：
     - 先從近期活躍地址抽樣
     - 再以同一區間、同一 `Top N` 預分析地址
     - 用地址市值門檻過濾
   - 目前地址市值口徑：
     - `max_nav`：區間內最大 NAV（預設）
     - `average_nav`：區間內平均 NAV

2. Performance meta 新增地址市值資料
   - `app/services/performance.py`
   - 現在每次分析都會回：
     - `address_market_cap_usd`
     - `address_market_cap_basis`
     - `address_peak_nav_usd`
     - `address_average_nav_usd`
   - 用途：
     - 單地址頁面顯示
     - 批量分析結果顯示
     - 保存分析摘要與排序

3. 批量分析 UX 補強
   - `app/templates/index.html`
   - 批量分析區現在有自己的設定欄位：
     - 開始日期
     - 結束日期
     - `Top N`
     - 最低地址市值
     - 市值口徑
   - 這樣批量分析不再偷偷共用單地址區的 `Top N`，避免使用者誤解

4. 已保存分析 UX 補強
   - 表格新增：
     - 地址市值欄位
     - 排序欄位 / 排序方向控制
   - 動作按鈕由 `載入` 改成 `查看詳情`
   - 文案明確說明：
     - 這個動作會把該筆保存分析顯示到上方圖表與指標區

5. 頁面上方增加定義說明
   - 新增靜態說明卡片，解釋：
     - 總報酬
     - 地址行為
     - 回報來源
     - 地址市值
   - 目的：
     - 降低使用者對欄位意義的理解成本

## 本次 debug（功能外）
- `etherscan block lookup failed: NOTOK Error! Block timestamp too far in the future`
- 已修正：
  - block lookup 會把 `end_date` clamp 到「現在 UTC 時間」
  - 若使用者選到今天或未來日期，不再把 `23:59:59 UTC` 直接丟給 Etherscan
  - `sync_address_from_network_and_recompute()` 也會使用有效的 `effective_end_date`
- 詳細 debug 紀錄另寫：
  - `debug_2026-03-13.md`

## 本次驗證
- `.venv/bin/pytest -q`
  - `20 passed`
- `PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m py_compile $(find app tests -name '*.py' -type f)`
  - 通過
- `node --check app/static/dashboard.js`
  - 通過

## 日期
- 2026-03-13

## 本次隨機地址 timeout 優化
- 問題：
  - 隨機地址若設最低地址市值，原本會對候選地址逐一跑完整 `load_or_sync_address_performance()`
  - 這會同步鏈上、抓歷史價格、重算 NAV，成本太高
  - 即使前端 timeout 拉長，也仍可能逾時

## 本次修正
1. 隨機地址改為快速市值預篩
   - `app/services/network_sync.py`
   - 新增：
     - `_estimate_market_cap_from_snapshots()`
     - `_estimate_address_market_cap_from_network()`
     - `_load_network_snapshots()`
   - 行為：
     - 對未保存分析的候選地址，不先跑完整績效
     - 只抓鏈上持倉快照 + 現貨價，快速估地址市值
     - 以此篩掉低於門檻的地址

2. 已保存分析優先重用
   - `app/services/analysis_store.py`
   - 新增 `get_saved_analysis_snapshot_by_key()`
   - 若該地址同區間同 `top_n_tokens` 已有保存結果，就直接用保存分析的地址市值，不再重抓

3. 隨機篩選時間預算
   - `app/core/config.py`
   - 新增 `random_address_screen_time_budget_seconds`
   - 目的：
     - 避免隨機地址取樣為了補滿 50 個，一直做預篩到前端逾時

4. 候選放大倍數調低
   - `random_address_candidate_multiplier` 從 `4` 下修到 `2`
   - 降低一次要預篩的地址數量

## 口徑說明
- 隨機地址的「最低地址市值」現在是快速預篩：
  - 對未保存地址，用區間內持倉 + 現貨價估值
- 正式分析後顯示在畫面與保存分析中的「地址市值」：
  - 仍以該次分析計算出的 NAV 指標為準

## 本次驗證
- `.venv/bin/pytest -q`
  - `21 passed`
- `PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m py_compile $(find app tests -name '*.py' -type f)`
  - 通過
- `node --check app/static/dashboard.js`
  - 通過

## 日期
- 2026-03-12

## 本次效能優化（第 2 波：增量同步基礎版）
- 背景：
  - 前一輪已減少重複抓價格
  - 這一輪要處理另一個大瓶頸：只要地址以前同步過，就不應再從 genesis 重播整段歷史

## 本次完成項目

1. 用本地 holdings 快照做增量同步起點
   - `app/services/network_sync.py` 新增：
     - `_load_latest_holding_snapshot_before()`
   - 行為：
     - 若 DB 內已經有此地址在 `start_date` 之前最近一天的完整 holdings
     - 下一次同步就直接從該快照隔天開始抓事件
     - 不再重播更早的歷史 delta

2. Etherscan 查詢加上 block range 限制
   - `app/services/network_sync.py` 新增：
     - `_etherscan_block_json()`
     - `_get_block_number_by_timestamp()`
     - `_resolve_block_range()`
   - `txlist` / `tokentx` 現在支援帶入 `start_block` / `end_block`
   - 用途：
     - 增量同步時，只抓快照後到 `end_date` 的區塊
     - 冷啟動時至少也會把 `endblock` 限縮到 `end_date`，不再抓到未來區塊

3. 日持倉還原支援初始快照
   - `build_daily_balances_from_events()` 新增：
     - `initial_balances`
     - `initial_token_contracts`
   - 行為：
     - 若有快照，直接以快照作為初始 state
     - 只套用後續新事件

4. 測試補充
   - `tests/test_network_sync.py`
     - 新增用 `initial_balances` 還原日持倉的測試
     - 新增讀取最近 holdings 快照的測試

## 口徑說明
- 這一輪優化主要加速：
  - 同地址重查
  - 同地址改較晚的 `end_date`
  - 已分析過一段期間、接著往後滾動查詢的情境
- 對「第一次分析一個全新地址」：
  - 仍需走冷啟動路徑
  - 因為沒有可信的起始持倉快照，不能直接砍掉更早歷史，否則績效口徑會錯

## 本次驗證
- 通過語法檢查：
  - `PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m py_compile $(find app tests -name '*.py' -type f)`
- 通過前端 JS 語法檢查：
  - `node --check app/static/dashboard.js`
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

## 日期
- 2026-03-12

## 本次小幅維護
- 新增 `.gitignore`，忽略：
  - `.env`
  - `.venv/`
  - `__pycache__/`
  - `*.pyc`
  - `*.db`
  - `.DS_Store`
  - 常見 Python 測試/覆蓋率/IDE 產物

## 補充說明
- 目前下列檔案已經被 Git 追蹤，所以新增 `.gitignore` 之後也不會自動停止追蹤：
  - `.env`
  - `.DS_Store`
  - `crypto_follower.db`
  - 部分 `app/__pycache__/*.pyc`
- 若後續要把這些檔案從版控中移除，需要再做一次 `git rm --cached`。

## 後續處理
- 已執行 `git rm --cached`，將下列已追蹤但應忽略的本地檔案從索引移除，並保留在本機：
  - `.env`
  - `.DS_Store`
  - `crypto_follower.db`
  - `app/**/__pycache__/*.pyc`

## 日期
- 2026-03-12

## 本次功能追加
- 需求：
  1) 增加一個按鈕，自動隨機產生 50 個地址，方便直接做批量分析
  2) 把跑完的分析結果保存起來，之後同一地址同一區間可以直接回看

## 本次完成項目

1. 分析結果保存
   - `app/models.py` 新增 `SavedAnalysisSnapshot` 資料表，欄位包含：
     - `address`
     - `start_date`
     - `end_date`
     - `top_n_tokens`
     - `nav_end_usd`
     - `total_return`
     - `cagr`
     - `sharpe`
     - `behavior_style`
     - `return_driver`
     - `analysis_source`
     - `payload`
     - `saved_at`
   - `app/services/analysis_store.py` 新增保存與查詢邏輯：
     - `save_analysis_snapshot()`
     - `list_saved_analysis_snapshots()`
     - `get_saved_analysis_snapshot()`
     - `get_saved_addresses()`
   - 保存策略：
     - 單地址分析成功後自動保存
     - 批量分析成功的每個地址也自動保存
     - 相同 `address + start_date + end_date + top_n_tokens` 會更新既有 snapshot，不重複插入

2. 保存分析 API
   - `app/api/routes/performance.py` 新增：
     - `GET /api/v1/performance/saved`
     - `GET /api/v1/performance/saved/{snapshot_id}`
   - 用途：
     - 列出最近保存的分析摘要
     - 載入某一筆已保存的完整分析結果

3. 隨機 50 地址產生
   - `app/services/network_sync.py` 新增 `generate_random_recent_addresses()`
   - 作法：
     - 使用 Etherscan proxy API 讀最新區塊號
     - 從近期區塊範圍內隨機抽樣區塊
     - 從交易 `from` address 收集可分析地址
     - 預設排除已保存過分析的地址，避免重複
   - `app/api/routes/performance.py` 新增：
     - `GET /api/v1/performance/random-addresses`

4. Dashboard 補強
   - `app/templates/index.html`
     - 批量分析區新增「隨機產生 50 地址」按鈕
     - 新增「已保存分析」表格
   - `app/static/dashboard.js`
     - 新增隨機地址填入批量 textarea 的流程
     - 新增讀取保存分析列表與載入單筆 snapshot 的流程
     - 單地址/批量分析完成後自動刷新保存分析列表
   - `app/static/dashboard.css`
     - 補上批量按鈕與保存分析表格樣式

5. 設定與文件
   - `app/core/config.py` 新增：
     - `saved_analysis_limit`
     - `random_address_block_window`
     - `random_address_sample_blocks`
   - `.env.example` 補上對應環境變數
   - `README.md` 補上：
     - 隨機地址 API 與行為說明
     - 保存分析 snapshot 的口徑與 API

6. 回歸測試
   - 新增 `tests/test_analysis_store.py`
   - 覆蓋：
     - 相同地址/區間/top_n 的 snapshot 會更新而不是重複插入
     - 已保存地址集合會正規化後回傳

## 本次驗證
- 通過語法檢查：
  - `PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m py_compile $(find app tests -name '*.py' -type f)`
- 目前 CLI shell 環境仍未安裝完整依賴，因此未在此處實跑 `pytest`

## 交付狀態
- 現在 dashboard 已可：
  1) 一鍵取 50 個隨機地址
  2) 直接做批量分析
  3) 保存結果
  4) 未來直接回看同地址、同區間的分析結果

## 日期
- 2026-03-12

## 本次 UX / 效能修正
- 背景：
  - 使用者回報「隨機產生 50 地址」按鈕看起來像沒反應
  - 也要求所有按鈕都要有 loading icon，避免誤會系統卡住

## 本次修正內容

1. 隨機地址取樣速度優化
   - `app/services/network_sync.py`
     - 新增 `_select_random_recent_blocks()`
     - `generate_random_recent_addresses()` 不再預設掃描過多區塊
   - 調整設定預設：
     - `random_address_sample_blocks` 從 `40` 下修到 `12`
   - 目的：
     - 減少 Etherscan proxy 請求數
     - 讓隨機地址按鈕在正常情況下更快返回，不會像長時間卡住

2. 前端按鈕 loading UX
   - `app/static/dashboard.js`
     - 新增 `setButtonLoading()`
     - 新增 `fetchJson()` 與統一錯誤處理
   - 套用範圍：
     - 單地址查詢按鈕
     - 批量分析按鈕
     - 隨機產生 50 地址按鈕
     - 已保存分析的「載入」按鈕
   - 行為：
     - 點擊後立即顯示 spinner
     - 按鈕文字切換成 `查詢中 / 分析中 / 取樣中 / 載入中`
     - 請求結束後恢復原狀

3. 前端逾時提示
   - 隨機地址按鈕加上前端 timeout 控制
   - 若後端過久未返回，前端會明確顯示「請求逾時，請稍後再試」
   - 避免使用者看到按鈕無限 loading 卻不知道發生什麼事

4. 測試補充
   - `tests/test_network_sync.py`
     - 新增 `_select_random_recent_blocks()` 的基本測試

## 本次驗證
- 通過語法檢查：
  - `PYTHONPYCACHEPREFIX=/tmp/pycache python3 -m py_compile $(find app tests -name '*.py' -type f)`
