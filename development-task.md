當前必做（MVP 核心）的依序為：

1. 地址 watchlist（手動加入 + 以排名推薦）
2. 日頻淨值曲線（至少 Top N 代幣 + 穩定幣 + 原生幣）
3. 指標面板：CAGR、MDD、Sharpe、交易頻率、最大單日跌幅（可作為 MDD 的局部代理）
4. 資料品質標籤：價格來源（Chainlink/CoinGecko）、標註來源（Etherscan/Nansen/Arkham）、是否疑似交易所/合約。
5. 交易提醒：目標地址發生「新建倉/大額 swap」推播

語言可以用 python + FAST-API，資料庫用 mysql db

目標是 追蹤 長期持倉型（Position trader / HODL）地址：交易頻率低，但淨值隨市場趨勢上升

地址的標注可以「先吃現成標註，再逐步自建聚合」

績效計算口徑可以「資產淨值型（Equity curve / NAV）：把地址在各時間點持有的代幣，用價格換成某一計價幣（常見 USD），得到淨值曲線，再算報酬與風險。」為優先進行開發。