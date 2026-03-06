const form = document.getElementById("query-form");
const statusBox = document.getElementById("status");
const metricsBox = document.getElementById("metrics");
const qualityBox = document.getElementById("quality");
const tokensBox = document.getElementById("tokens");
const submitBtn = document.getElementById("submit-btn");
const chartCtx = document.getElementById("nav-chart").getContext("2d");

let navChart = null;

function formatPct(value) {
  if (value === null || value === undefined) return "N/A";
  return `${(value * 100).toFixed(2)}%`;
}

function formatNum(value) {
  if (value === null || value === undefined) return "N/A";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function setStatus(message, isError = false) {
  statusBox.textContent = message;
  statusBox.style.color = isError ? "#fca5a5" : "#93c5fd";
}

function renderMetrics(metrics) {
  const rows = [
    ["CAGR", formatPct(metrics.cagr)],
    ["MDD", formatPct(metrics.mdd)],
    ["Sharpe", formatNum(metrics.sharpe)],
    ["交易頻率", formatPct(metrics.trade_frequency)],
    ["最大單日跌幅", formatPct(metrics.max_single_day_drop)],
    ["總報酬", formatPct(metrics.total_return)],
  ];

  metricsBox.innerHTML = rows
    .map(
      ([name, value]) =>
        `<article class="metric-card"><div class="metric-name">${name}</div><div class="metric-value">${value}</div></article>`
    )
    .join("");
}

function renderQuality(quality) {
  const items = [
    ["價格來源", (quality.price_sources || []).join(", ") || "N/A"],
    ["標註來源", quality.label_source || "N/A"],
    ["疑似交易所", quality.suspected_exchange ? "是" : "否"],
    ["疑似合約", quality.suspected_contract ? "是" : "否"],
    ["缺價天數", String(quality.missing_price_days || 0)],
  ];

  qualityBox.className = "kv";
  qualityBox.innerHTML = items
    .map(([key, value]) => `<div class="kv-item"><strong>${key}</strong>：${value}</div>`)
    .join("");
}

function renderTokens(tokens) {
  tokensBox.className = "tokens";
  tokensBox.innerHTML = `<strong>納入計算代幣</strong>：${(tokens || []).join(", ") || "N/A"}`;
}

function renderChart(navCurve, address) {
  const labels = navCurve.map((row) => row.date);
  const data = navCurve.map((row) => row.nav_usd);

  if (navChart) {
    navChart.destroy();
  }

  navChart = new Chart(chartCtx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: `${address} NAV (USD)`,
          data,
          borderColor: "#60a5fa",
          backgroundColor: "rgba(96, 165, 250, 0.15)",
          borderWidth: 2,
          fill: true,
          tension: 0.15,
          pointRadius: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      scales: {
        y: {
          ticks: {
            callback: (value) => `$${Number(value).toLocaleString()}`,
            color: "#e2e8f0",
          },
          grid: { color: "#1f2937" },
        },
        x: {
          ticks: { color: "#e2e8f0", maxTicksLimit: 8 },
          grid: { color: "#1f2937" },
        },
      },
      plugins: {
        legend: {
          labels: { color: "#e2e8f0" },
        },
      },
    },
  });
}

async function handleSubmit(event) {
  event.preventDefault();

  const formData = new FormData(form);
  const address = String(formData.get("address") || "").trim();
  const startDate = String(formData.get("start_date") || "");
  const endDate = String(formData.get("end_date") || "");
  const topNTokens = Number(formData.get("top_n_tokens") || 10);

  if (!address) {
    setStatus("請輸入地址", true);
    return;
  }

  submitBtn.disabled = true;
  setStatus("正在查詢 Etherscan 與 CoinGecko，請稍候...");

  try {
    const response = await fetch(
      `/api/v1/performance/${encodeURIComponent(address)}/network/recompute`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start_date: startDate,
          end_date: endDate,
          top_n_tokens: topNTokens,
        }),
      }
    );

    if (!response.ok) {
      const errorBody = await response.json();
      throw new Error(errorBody.detail || "查詢失敗");
    }

    const payload = await response.json();
    renderChart(payload.nav_curve, payload.address);
    renderMetrics(payload.metrics);
    renderQuality(payload.quality);
    renderTokens(payload.selected_tokens);
    setStatus("完成：已同步網路資料並更新績效。", false);
  } catch (error) {
    setStatus(`失敗：${error.message}`, true);
  } finally {
    submitBtn.disabled = false;
  }
}

form.addEventListener("submit", handleSubmit);

