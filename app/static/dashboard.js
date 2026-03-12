const form = document.getElementById("query-form");
const batchForm = document.getElementById("batch-form");
const statusBox = document.getElementById("status");
const batchStatusBox = document.getElementById("batch-status");
const metricsBox = document.getElementById("metrics");
const behaviorBox = document.getElementById("behavior");
const interpretationsBox = document.getElementById("interpretations");
const metaBox = document.getElementById("meta");
const qualityBox = document.getElementById("quality");
const tokensBox = document.getElementById("tokens");
const submitBtn = document.getElementById("submit-btn");
const batchSubmitBtn = document.getElementById("batch-submit-btn");
const batchTableBody = document.querySelector("#batch-table tbody");
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

function formatUsd(value) {
  if (value === null || value === undefined) return "N/A";
  return `$${Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function setStatus(target, message, isError = false) {
  target.textContent = message;
  target.style.color = isError ? "#fca5a5" : "#93c5fd";
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

function renderBehavior(behavior) {
  const items = [
    ["地址風格", behavior.style || "N/A"],
    ["回報來源", behavior.return_driver || "N/A"],
    ["淨轉入", formatUsd(behavior.net_flow_usd)],
    ["市場 PnL", formatUsd(behavior.market_pnl_usd)],
    ["周轉比", behavior.turnover_ratio === null || behavior.turnover_ratio === undefined ? "N/A" : `${behavior.turnover_ratio.toFixed(2)}x`],
    ["同日轉入轉出占比", formatPct(behavior.round_trip_ratio)],
  ];

  behaviorBox.innerHTML = items
    .map(([key, value]) => `<div class="kv-item"><strong>${key}</strong>：${value}</div>`)
    .join("");
}

function renderInterpretations(interpretations) {
  interpretationsBox.innerHTML = (interpretations || [])
    .map((text) => `<div class="note-item">${text}</div>`)
    .join("");
}

function renderMeta(meta, quality) {
  const items = [
    ["資料來源", meta.source || "N/A"],
    ["是否使用快取", meta.used_cache ? "是" : "否"],
    ["快取年齡", meta.cache_age_minutes === null || meta.cache_age_minutes === undefined ? "N/A" : `${meta.cache_age_minutes.toFixed(1)} 分鐘`],
    ["本次耗時", meta.runtime_seconds === null || meta.runtime_seconds === undefined ? "N/A" : `${meta.runtime_seconds.toFixed(2)} 秒`],
    ["價格來源", (quality.price_sources || []).join(", ") || "N/A"],
    ["標註來源", quality.label_source || "N/A"],
    ["疑似交易所", quality.suspected_exchange ? "是" : "否"],
    ["疑似合約", quality.suspected_contract ? "是" : "否"],
    ["缺價天數", String(quality.missing_price_days || 0)],
  ];

  metaBox.className = "kv";
  metaBox.innerHTML = items
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

function renderSingleAddress(payload) {
  renderChart(payload.nav_curve, payload.address);
  renderMetrics(payload.metrics);
  renderBehavior(payload.behavior);
  renderInterpretations(payload.interpretations);
  renderMeta(payload.meta, payload.quality);
  renderTokens(payload.selected_tokens);
}

function renderBatchResults(results) {
  batchTableBody.innerHTML = (results || [])
    .map((row) => {
      const status = row.success ? row.explanation || "" : row.error || "";
      return `
        <tr>
          <td>${row.address}</td>
          <td>${formatUsd(row.nav_end_usd)}</td>
          <td>${formatPct(row.total_return)}</td>
          <td>${formatPct(row.cagr)}</td>
          <td>${row.behavior_style || "N/A"}</td>
          <td>${row.return_driver || "N/A"}</td>
          <td>${row.used_cache === null || row.used_cache === undefined ? "N/A" : row.used_cache ? "是" : "否"}</td>
          <td>${row.runtime_seconds === null || row.runtime_seconds === undefined ? "N/A" : `${row.runtime_seconds.toFixed(2)} 秒`}</td>
          <td>${status}</td>
        </tr>
      `;
    })
    .join("");
}

async function handleSubmit(event) {
  event.preventDefault();

  const formData = new FormData(form);
  const address = String(formData.get("address") || "").trim();
  const startDate = String(formData.get("start_date") || "");
  const endDate = String(formData.get("end_date") || "");
  const topNTokens = Number(formData.get("top_n_tokens") || 10);
  const refresh = formData.get("refresh") === "on";

  if (!address) {
    setStatus(statusBox, "請輸入地址", true);
    return;
  }

  submitBtn.disabled = true;
  setStatus(statusBox, refresh ? "正在重抓鏈上與價格資料..." : "先檢查快取，必要時再同步網路資料...");

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
          refresh,
        }),
      }
    );

    if (!response.ok) {
      const errorBody = await response.json();
      throw new Error(errorBody.detail || "查詢失敗");
    }

    const payload = await response.json();
    renderSingleAddress(payload);
    setStatus(statusBox, payload.meta.used_cache ? "完成：已使用快取資料並重算展示。" : "完成：已同步網路資料並更新績效。");
  } catch (error) {
    setStatus(statusBox, `失敗：${error.message}`, true);
  } finally {
    submitBtn.disabled = false;
  }
}

async function handleBatchSubmit(event) {
  event.preventDefault();

  const formData = new FormData(form);
  const startDate = String(formData.get("start_date") || "");
  const endDate = String(formData.get("end_date") || "");
  const topNTokens = Number(formData.get("top_n_tokens") || 10);
  const refresh = formData.get("refresh") === "on";
  const addresses = String(document.getElementById("batch-addresses").value || "")
    .split("\n")
    .map((row) => row.trim())
    .filter(Boolean);

  if (!addresses.length) {
    setStatus(batchStatusBox, "請至少輸入一個地址", true);
    return;
  }

  batchSubmitBtn.disabled = true;
  setStatus(batchStatusBox, refresh ? "批量重抓中，這會比較慢..." : "批量分析中，會優先使用快取...");

  try {
    const response = await fetch("/api/v1/performance/batch/network/recompute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        addresses,
        start_date: startDate,
        end_date: endDate,
        top_n_tokens: topNTokens,
        refresh,
      }),
    });

    if (!response.ok) {
      const errorBody = await response.json();
      throw new Error(errorBody.detail || "批量分析失敗");
    }

    const payload = await response.json();
    renderBatchResults(payload.results);
    setStatus(
      batchStatusBox,
      `完成：共 ${payload.requested} 個地址，成功 ${payload.completed}、失敗 ${payload.failed}。`
    );
  } catch (error) {
    setStatus(batchStatusBox, `失敗：${error.message}`, true);
  } finally {
    batchSubmitBtn.disabled = false;
  }
}

form.addEventListener("submit", handleSubmit);
batchForm.addEventListener("submit", handleBatchSubmit);
