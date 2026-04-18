const freshnessGrid = document.getElementById("freshness-grid");
const backfillResult = document.getElementById("backfill-result");
const stamp = document.getElementById("stamp");

function fmt(value) {
  if (!value) return "Not run yet";
  try {
    return new Date(value).toLocaleString();
  } catch (_) {
    return value;
  }
}

function renderFreshness(data) {
  const items = [
    ["Games Today", data.games_today],
    ["Active Predictions", data.active_predictions_today],
    ["Active Edges", data.active_edges_today],
    ["Alerts Today", data.alerts_today],
    ["Last Game Sync", fmt(data.last_game_sync)],
    ["Last Prediction Run", fmt(data.last_prediction_run)],
    ["Last Open Odds Sync", fmt(data.last_open_odds_sync)],
    ["Last Pregame Odds Sync", fmt(data.last_pregame_odds_sync)],
    ["Last Edge Calc", fmt(data.last_edge_calc)],
    ["Last Alert Run", fmt(data.last_alert_run)],
  ];

  freshnessGrid.innerHTML = items.map(([label, value]) => `
    <div class="tile">
      <div class="label">${label}</div>
      <div class="value">${value ?? "0"}</div>
    </div>
  `).join("");
}

async function loadFreshness() {
  const response = await fetch("/api/admin/freshness");
  const data = await response.json();
  renderFreshness(data);
  stamp.textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

async function runBackfill() {
  backfillResult.textContent = "Running backfill…";
  const response = await fetch("/api/admin/backfill/prediction-dashboard-metrics", {
    method: "POST",
  });
  const data = await response.json();
  backfillResult.textContent = JSON.stringify(data, null, 2);
  await loadFreshness();
}

document.getElementById("refresh-btn").addEventListener("click", loadFreshness);
document.getElementById("backfill-btn").addEventListener("click", runBackfill);

loadFreshness().catch((error) => {
  freshnessGrid.innerHTML = `<div class="loading">Failed to load admin data: ${error.message}</div>`;
});
