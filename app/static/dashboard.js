const form = document.getElementById("query-form");
const batchForm = document.getElementById("batch-form");
const statusBox = document.getElementById("status");
const batchStatusBox = document.getElementById("batch-status");
const savedStatusBox = document.getElementById("saved-status");
const metricsBox = document.getElementById("metrics");
const behaviorBox = document.getElementById("behavior");
const interpretationsBox = document.getElementById("interpretations");
const metaBox = document.getElementById("meta");
const qualityBox = document.getElementById("quality");
const tokensBox = document.getElementById("tokens");
const submitBtn = document.getElementById("submit-btn");
const batchSubmitBtn = document.getElementById("batch-submit-btn");
const randomBatchBtn = document.getElementById("random-batch-btn");
const batchStartDateInput = document.getElementById("batch_start_date");
const batchEndDateInput = document.getElementById("batch_end_date");
const batchTopNTokensInput = document.getElementById("batch_top_n_tokens");
const batchMinMarketCapInput = document.getElementById("batch_min_market_cap_usd");
const batchMarketCapBasisInput = document.getElementById("batch_market_cap_basis");
const batchExcludeSavedInput = document.getElementById("batch_exclude_saved");
const batchTextarea = document.getElementById("batch-addresses");
const batchTableBody = document.querySelector("#batch-table tbody");
const savedTableBody = document.querySelector("#saved-table tbody");
const savedSortByInput = document.getElementById("saved_sort_by");
const savedSortOrderInput = document.getElementById("saved_sort_order");
const chartCtx = document.getElementById("nav-chart").getContext("2d");

let navChart = null;

async function extractErrorMessage(response, fallbackMessage) {
  try {
    const rawText = await response.text();
    if (!rawText) return fallbackMessage;

    try {
      const payload = JSON.parse(rawText);
      if (payload && typeof payload.detail === "string" && payload.detail) {
        return payload.detail;
      }
    } catch (error) {
      return rawText;
    }
    return rawText;
  } catch (error) {
    return fallbackMessage;
  }
}

async function fetchJson(url, options = {}) {
  const { timeoutMs = 0, ...fetchOptions } = options;
  const controller = timeoutMs > 0 ? new AbortController() : null;
  let timeoutId = null;

  if (controller) {
    fetchOptions.signal = controller.signal;
    timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  }

  try {
    const response = await fetch(url, fetchOptions);
    if (!response.ok) {
      throw new Error(await extractErrorMessage(response, `HTTP ${response.status}`));
    }
    return await response.json();
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("請求逾時，請稍後再試");
    }
    throw error;
  } finally {
    if (timeoutId !== null) {
      window.clearTimeout(timeoutId);
    }
  }
}

function setButtonLoading(button, isLoading, loadingText) {
  if (!(button instanceof HTMLButtonElement)) return;

  if (isLoading) {
    if (!button.dataset.originalText) {
      button.dataset.originalText = button.textContent || "";
    }
    button.disabled = true;
    button.classList.add("is-loading");
    if (loadingText) {
      button.textContent = loadingText;
    }
    return;
  }

  button.disabled = false;
  button.classList.remove("is-loading");
  if (button.dataset.originalText) {
    button.textContent = button.dataset.originalText;
  }
}

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

function formatDateTime(value) {
  if (!value) return "N/A";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatMarketCapBasis(value) {
  if (value === "average_nav") return "平均 NAV";
  return "最大 NAV";
}

function setStatus(target, message, isError = false) {
  target.textContent = message;
  target.style.color = isError ? "#fca5a5" : "#93c5fd";
}

function getSharedQueryParams() {
  const formData = new FormData(form);
  return {
    startDate: String(formData.get("start_date") || ""),
    endDate: String(formData.get("end_date") || ""),
    topNTokens: Number(formData.get("top_n_tokens") || 10),
    refresh: formData.get("refresh") === "on",
  };
}

function getBatchQueryParams() {
  return {
    startDate: String(batchStartDateInput?.value || ""),
    endDate: String(batchEndDateInput?.value || ""),
    topNTokens: Number(batchTopNTokensInput?.value || 10),
    minMarketCapUsd: Number(batchMinMarketCapInput?.value || 0),
    marketCapBasis: String(batchMarketCapBasisInput?.value || "max_nav"),
    excludeSaved: batchExcludeSavedInput?.checked ?? true,
    refresh: document.getElementById("refresh")?.checked ?? false,
  };
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
    ["地址市值", formatUsd(meta.address_market_cap_usd)],
    ["地址市值口徑", formatMarketCapBasis(meta.address_market_cap_basis)],
    ["區間最大 NAV", formatUsd(meta.address_peak_nav_usd)],
    ["區間平均 NAV", formatUsd(meta.address_average_nav_usd)],
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
  document.getElementById("address").value = payload.address;
  document.getElementById("start_date").value = payload.start_date;
  document.getElementById("end_date").value = payload.end_date;
}

function renderBatchResults(results) {
  batchTableBody.innerHTML = (results || [])
    .map((row) => {
      const status = row.success ? row.explanation || "" : row.error || "";
      return `
        <tr>
          <td>${row.address}</td>
          <td>${formatUsd(row.market_cap_usd)}</td>
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

function renderSavedAnalyses(items) {
  savedTableBody.innerHTML = (items || [])
    .map(
      (item) => `
        <tr>
          <td>${item.address}</td>
          <td>${item.start_date} ~ ${item.end_date}</td>
          <td>${formatUsd(item.market_cap_usd)}</td>
          <td>${formatPct(item.total_return)}</td>
          <td>${item.behavior_style || "N/A"}</td>
          <td>${item.return_driver || "N/A"}</td>
          <td>${formatDateTime(item.saved_at)}</td>
          <td><button type="button" class="secondary-button saved-load-btn" data-snapshot-id="${item.id}">查看詳情</button></td>
        </tr>
      `
    )
    .join("");
}

async function loadSavedAnalyses() {
  try {
    const params = new URLSearchParams({
      limit: "50",
      sort_by: String(savedSortByInput?.value || "saved_at"),
      sort_order: String(savedSortOrderInput?.value || "desc"),
    });
    const payload = await fetchJson(`/api/v1/performance/saved?${params.toString()}`, { timeoutMs: 10000 });
    renderSavedAnalyses(payload.items);
    setStatus(savedStatusBox, `已載入 ${payload.items.length} 筆保存分析。`);
  } catch (error) {
    setStatus(savedStatusBox, `失敗：${error.message}`, true);
  }
}

async function loadSavedSnapshot(snapshotId, button = null) {
  setButtonLoading(button, true, "查看中...");
  setStatus(savedStatusBox, "正在把這筆保存分析顯示到上方圖表...");
  try {
    const payload = await fetchJson(`/api/v1/performance/saved/${snapshotId}`, { timeoutMs: 10000 });
    renderSingleAddress(payload);
    setStatus(savedStatusBox, "已在上方顯示這筆保存分析。");
  } catch (error) {
    setStatus(savedStatusBox, `失敗：${error.message}`, true);
  } finally {
    setButtonLoading(button, false);
  }
}

async function handleRandomBatchAddresses() {
  const { startDate, endDate, topNTokens, minMarketCapUsd, marketCapBasis, excludeSaved } = getBatchQueryParams();
  setButtonLoading(randomBatchBtn, true, "取樣中...");
  setStatus(
    batchStatusBox,
    minMarketCapUsd > 0
      ? `正在取樣並做快速地址市值預篩（避免逾時）...`
      : "正在從近期鏈上活動取樣地址..."
  );
  try {
    const params = new URLSearchParams({
      count: "50",
      start_date: startDate,
      end_date: endDate,
      top_n_tokens: String(topNTokens),
      min_market_cap_usd: String(minMarketCapUsd),
      market_cap_basis: marketCapBasis,
      exclude_saved: String(excludeSaved),
    });
    const payload = await fetchJson(`/api/v1/performance/random-addresses?${params.toString()}`, {
      timeoutMs: 180000,
    });
    batchTextarea.value = (payload.addresses || []).join("\n");
    setStatus(
      batchStatusBox,
      payload.count < 50
        ? `已填入 ${payload.count} 個地址；快速預篩後不足 50。地址市值口徑：${formatMarketCapBasis(payload.market_cap_basis)}，最低門檻：${formatUsd(payload.min_market_cap_usd)}`
        : `已填入 ${payload.count} 個地址。地址市值口徑：${formatMarketCapBasis(payload.market_cap_basis)}，最低門檻：${formatUsd(payload.min_market_cap_usd)}`
    );
  } catch (error) {
    setStatus(batchStatusBox, `失敗：${error.message}`, true);
  } finally {
    setButtonLoading(randomBatchBtn, false);
  }
}

async function handleSubmit(event) {
  event.preventDefault();

  const formData = new FormData(form);
  const address = String(formData.get("address") || "").trim();
  const { startDate, endDate, topNTokens, refresh } = getSharedQueryParams();

  if (!address) {
    setStatus(statusBox, "請輸入地址", true);
    return;
  }

  setButtonLoading(submitBtn, true, "查詢中...");
  setStatus(statusBox, refresh ? "正在重抓鏈上與價格資料..." : "先檢查快取，必要時再同步網路資料...");

  try {
    const payload = await fetchJson(
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
    renderSingleAddress(payload);
    setStatus(statusBox, payload.meta.used_cache ? "完成：已使用快取資料並重算展示。" : "完成：已同步網路資料並更新績效。");
    await loadSavedAnalyses();
  } catch (error) {
    setStatus(statusBox, `失敗：${error.message}`, true);
  } finally {
    setButtonLoading(submitBtn, false);
  }
}

async function handleBatchSubmit(event) {
  event.preventDefault();

  const { startDate, endDate, topNTokens, refresh } = getBatchQueryParams();
  const addresses = String(batchTextarea.value || "")
    .split("\n")
    .map((row) => row.trim())
    .filter(Boolean);

  if (!addresses.length) {
    setStatus(batchStatusBox, "請至少輸入一個地址", true);
    return;
  }

  setButtonLoading(batchSubmitBtn, true, "分析中...");
  setStatus(batchStatusBox, refresh ? "批量重抓中，這會比較慢..." : "批量分析中，會優先使用快取...");

  try {
    const payload = await fetchJson("/api/v1/performance/batch/network/recompute", {
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
    renderBatchResults(payload.results);
    setStatus(
      batchStatusBox,
      `完成：共 ${payload.requested} 個地址，成功 ${payload.completed}、失敗 ${payload.failed}。`
    );
    await loadSavedAnalyses();
  } catch (error) {
    setStatus(batchStatusBox, `失敗：${error.message}`, true);
  } finally {
    setButtonLoading(batchSubmitBtn, false);
  }
}

savedTableBody.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const button = target.closest(".saved-load-btn");
  if (!button) return;
  const snapshotId = Number(button.getAttribute("data-snapshot-id"));
  if (!snapshotId) return;
  await loadSavedSnapshot(snapshotId, button);
});

form.addEventListener("submit", handleSubmit);
batchForm.addEventListener("submit", handleBatchSubmit);
randomBatchBtn.addEventListener("click", handleRandomBatchAddresses);
savedSortByInput?.addEventListener("change", loadSavedAnalyses);
savedSortOrderInput?.addEventListener("change", loadSavedAnalyses);
loadSavedAnalyses();
