from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MLB AI Betting Tool</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0d1117;
    --surface: #161b22;
    --surface2: #21262d;
    --border: #30363d;
    --text: #e6edf3;
    --muted: #7d8590;
    --green: #3fb950;
    --green-dim: #1a4a25;
    --red: #f85149;
    --red-dim: #4a1a1a;
    --yellow: #d29922;
    --yellow-dim: #3d2c00;
    --blue: #58a6ff;
    --purple: #bc8cff;
    --accent: #1f6feb;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", sans-serif;
    font-size: 14px;
    line-height: 1.5;
  }
  a { color: var(--blue); text-decoration: none; }

  /* ── Header ── */
  .header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 0 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 56px;
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .header-left { display: flex; align-items: center; gap: 12px; }
  .logo { font-size: 18px; font-weight: 700; letter-spacing: -0.4px; }
  .logo span { color: var(--green); }
  .badge {
    background: var(--accent);
    color: #fff;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 12px;
    letter-spacing: 0.4px;
  }
  .header-right { display: flex; align-items: center; gap: 16px; }
  .last-updated { color: var(--muted); font-size: 12px; }
  .refresh-btn {
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 6px 14px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    transition: background 0.15s;
  }
  .refresh-btn:hover { background: var(--border); }

  /* ── Layout ── */
  .container { max-width: 1400px; margin: 0 auto; padding: 24px; }
  .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px; }
  .grid-3 { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; margin-bottom: 24px; }
  @media (max-width: 1100px) {
    .grid-4 { grid-template-columns: repeat(2, 1fr); }
    .grid-2, .grid-3 { grid-template-columns: 1fr; }
  }
  @media (max-width: 600px) {
    .grid-4 { grid-template-columns: 1fr; }
    .container { padding: 12px; }
  }

  /* ── Cards ── */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 20px;
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
  }
  .card-title {
    font-size: 13px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: var(--muted);
  }
  .card-action {
    font-size: 12px;
    color: var(--blue);
    cursor: pointer;
  }

  /* ── KPI tiles ── */
  .kpi-value {
    font-size: 32px;
    font-weight: 700;
    letter-spacing: -1px;
    line-height: 1.1;
  }
  .kpi-label {
    font-size: 12px;
    color: var(--muted);
    margin-top: 4px;
    font-weight: 500;
  }
  .kpi-delta {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: 12px;
    font-weight: 600;
    margin-top: 8px;
    padding: 2px 7px;
    border-radius: 4px;
  }
  .delta-up { background: var(--green-dim); color: var(--green); }
  .delta-down { background: var(--red-dim); color: var(--red); }
  .delta-neutral { background: var(--surface2); color: var(--muted); }
  .green { color: var(--green); }
  .red { color: var(--red); }
  .yellow { color: var(--yellow); }
  .blue { color: var(--blue); }
  .purple { color: var(--purple); }
  .muted { color: var(--muted); }

  /* ── Tables ── */
  .table-wrap { overflow-x: auto; }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  th {
    text-align: left;
    padding: 8px 12px;
    color: var(--muted);
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 1px solid var(--border);
  }
  td {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
    vertical-align: middle;
  }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: var(--surface2); }

  /* ── Rank badge ── */
  .rank {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    font-size: 11px;
    font-weight: 700;
  }
  .rank-1 { background: #b8860b; color: #fff; }
  .rank-2 { background: #71797e; color: #fff; }
  .rank-3 { background: #6e3b1e; color: #fff; }
  .rank-n { background: var(--surface2); color: var(--muted); }

  /* ── Play chip ── */
  .play-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 9px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .play-away_ml, .play-home_ml { background: #1a3a5c; color: var(--blue); }
  .play-over { background: #1a4a25; color: var(--green); }
  .play-under { background: #3d2c00; color: var(--yellow); }

  /* ── Confidence chip ── */
  .conf-chip {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
  }
  .conf-strong { background: var(--green-dim); color: var(--green); }
  .conf-medium { background: var(--yellow-dim); color: var(--yellow); }
  .conf-weak { background: var(--surface2); color: var(--muted); }

  /* ── Result chip ── */
  .result-win { background: var(--green-dim); color: var(--green); padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }
  .result-loss { background: var(--red-dim); color: var(--red); padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }
  .result-push { background: var(--surface2); color: var(--muted); padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }
  .result-no_bet { color: var(--muted); font-size: 11px; }

  /* ── Correct indicator ── */
  .correct { color: var(--green); font-size: 14px; }
  .incorrect { color: var(--red); font-size: 14px; }

  /* ── Movement ── */
  .move-sharp-away, .move-sharp-home { color: var(--purple); font-size: 11px; font-weight: 600; }
  .move-fade { color: var(--muted); font-size: 11px; }

  /* ── Game card ── */
  .game-card {
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 10px;
  }
  .game-card:last-child { margin-bottom: 0; }
  .game-teams { font-weight: 600; font-size: 14px; }
  .game-meta { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .game-status-live { color: var(--green); font-size: 11px; font-weight: 700; }
  .game-status-final { color: var(--muted); font-size: 11px; }
  .game-status-sched { color: var(--blue); font-size: 11px; }
  .game-score { font-size: 18px; font-weight: 700; text-align: right; }

  /* ── Chart ── */
  .chart-wrap { position: relative; height: 180px; }

  /* ── Feature bar ── */
  .feat-bar-wrap { margin-bottom: 10px; }
  .feat-bar-label { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 3px; }
  .feat-bar-track { background: var(--surface2); border-radius: 3px; height: 6px; }
  .feat-bar-fill { height: 6px; border-radius: 3px; background: var(--blue); }

  /* ── Loading / Empty ── */
  .loading { color: var(--muted); font-size: 13px; padding: 24px 0; text-align: center; }
  .empty { color: var(--muted); font-size: 13px; padding: 20px 0; text-align: center; }

  /* ── Section divider ── */
  .section-label {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--muted);
    margin-bottom: 14px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }

  /* ── Stat grid in backtest card ── */
  .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .stat-item { }
  .stat-val { font-size: 22px; font-weight: 700; }
  .stat-lbl { font-size: 11px; color: var(--muted); margin-top: 2px; }

  /* ── Pulse dot ── */
  .pulse { display: inline-block; width: 8px; height: 8px; background: var(--green); border-radius: 50%; margin-right: 4px; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }

  /* ── EV bar inline ── */
  .ev-bar { display: inline-block; height: 4px; border-radius: 2px; vertical-align: middle; margin-left: 6px; }
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <div class="logo">⚾ MLB<span>AI</span></div>
    <span class="badge">BETA</span>
  </div>
  <div class="header-right">
    <span class="last-updated" id="last-updated">Loading...</span>
    <button class="refresh-btn" onclick="loadAll()">↻ Refresh</button>
  </div>
</div>

<div class="container">

  <!-- KPI Row -->
  <div class="grid-4" id="kpi-row">
    <div class="card"><div class="loading">Loading stats…</div></div>
    <div class="card"><div class="loading">&nbsp;</div></div>
    <div class="card"><div class="loading">&nbsp;</div></div>
    <div class="card"><div class="loading">&nbsp;</div></div>
  </div>

  <!-- Top Bets + Chart -->
  <div class="grid-3">
    <div class="card" id="ranked-card">
      <div class="card-header">
        <span class="card-title">Today's Top Bets</span>
        <span class="muted" style="font-size:12px" id="ranked-snap"></span>
      </div>
      <div class="table-wrap" id="ranked-table"><div class="loading">Loading bets…</div></div>
    </div>
    <div class="card" id="chart-card">
      <div class="card-header">
        <span class="card-title">Win / Loss Split</span>
      </div>
      <div class="chart-wrap">
        <canvas id="wl-chart"></canvas>
      </div>
      <div id="chart-summary" style="margin-top:14px"></div>
    </div>
  </div>

  <!-- Today's Games + Recent Results -->
  <div class="grid-2">
    <div class="card">
      <div class="card-header">
        <span class="card-title">Today's Games</span>
        <span id="games-count" class="muted" style="font-size:12px"></span>
      </div>
      <div id="games-list"><div class="loading">Loading games…</div></div>
    </div>
    <div class="card">
      <div class="card-header">
        <span class="card-title">Recent Results</span>
        <span class="card-action" onclick="loadReviews()">Refresh</span>
      </div>
      <div class="table-wrap" id="reviews-table"><div class="loading">Loading results…</div></div>
    </div>
  </div>

  <!-- Backtest -->
  <div class="card" id="backtest-card">
    <div class="card-header">
      <span class="card-title">Backtest Model</span>
      <span id="backtest-meta" class="muted" style="font-size:12px"></span>
    </div>
    <div id="backtest-body"><div class="loading">Loading backtest…</div></div>
  </div>

</div><!-- /container -->

<script>
// ── helpers ────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const fmt = (n, d=2) => n == null ? '—' : Number(n).toFixed(d);
const pct = n => n == null ? '—' : (Number(n)*100).toFixed(1)+'%';

function colEV(v) {
  if (v == null) return '<span class="muted">—</span>';
  const n = Number(v);
  const cls = n > 0 ? 'green' : n < 0 ? 'red' : 'muted';
  const bar = n > 0
    ? `<span class="ev-bar" style="width:${Math.min(n*300,60)}px;background:var(--green)"></span>`
    : '';
  return `<span class="${cls}">${fmt(v,4)}</span>${bar}`;
}

function confidenceChip(c) {
  if (!c) return '<span class="muted" style="font-size:11px">—</span>';
  const cls = c === 'strong' ? 'conf-strong' : c === 'medium' ? 'conf-medium' : 'conf-weak';
  return `<span class="conf-chip ${cls}">${c}</span>`;
}

function rankBadge(r) {
  const cls = r === 1 ? 'rank-1' : r === 2 ? 'rank-2' : r === 3 ? 'rank-3' : 'rank-n';
  return `<span class="rank ${cls}">${r}</span>`;
}

function playChip(p) {
  if (!p) return '<span class="muted">—</span>';
  const cls = 'play-' + p;
  const label = p.replace('_', ' ').toUpperCase();
  return `<span class="play-chip ${cls}">${label}</span>`;
}

function resultChip(r) {
  if (!r || r === 'no_bet') return '<span class="result-no_bet">no bet</span>';
  return `<span class="result-${r}">${r.toUpperCase()}</span>`;
}

function statusClass(s) {
  if (!s) return 'game-status-sched';
  const sl = s.toLowerCase();
  if (sl.includes('final') || sl.includes('complet')) return 'game-status-final';
  if (sl.includes('progress') || sl.includes('live')) return 'game-status-live';
  return 'game-status-sched';
}

function parseTime(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleTimeString('en-US', { hour:'numeric', minute:'2-digit', timeZoneName:'short' });
  } catch { return iso; }
}

// ── KPI cards ──────────────────────────────────────────────────────────────
async function loadSummary() {
  try {
    const r = await fetch('/api/reviews/summary');
    const d = await r.json();
    const wr = d.win_rate != null ? (d.win_rate*100).toFixed(1)+'%' : '—';
    const roi = d.roi_flat_110 != null
      ? (d.roi_flat_110*100).toFixed(2)+'%'
      : '—';
    const dir = d.model_directional_accuracy != null
      ? (d.model_directional_accuracy*100).toFixed(1)+'%'
      : '—';
    const total = d.total_predictions ?? 0;

    const wrColor = d.win_rate == null ? 'text' : d.win_rate >= 0.55 ? 'green' : d.win_rate >= 0.48 ? 'yellow' : 'red';
    const roiColor = d.roi_flat_110 == null ? 'text' : d.roi_flat_110 > 0 ? 'green' : d.roi_flat_110 > -0.05 ? 'yellow' : 'red';

    const winDelta = d.wins != null
      ? `<span class="kpi-delta ${d.roi_flat_110 >= 0 ? 'delta-up':'delta-down'}">${d.wins}W / ${d.losses}L / ${d.pushes ?? 0}P</span>`
      : '';

    $('kpi-row').innerHTML = `
      <div class="card">
        <div class="card-title">Win Rate</div>
        <div class="kpi-value ${wrColor}" style="margin-top:12px">${wr}</div>
        <div class="kpi-label">Graded bets (${d.bets_graded ?? 0} total)</div>
        ${winDelta}
      </div>
      <div class="card">
        <div class="card-title">Flat-Bet ROI</div>
        <div class="kpi-value ${roiColor}" style="margin-top:12px">${roi}</div>
        <div class="kpi-label">-110 juice, 1u flat bet</div>
        <span class="kpi-delta delta-neutral">per unit wagered</span>
      </div>
      <div class="card">
        <div class="card-title">Total Predictions</div>
        <div class="kpi-value" style="margin-top:12px">${total}</div>
        <div class="kpi-label">All-time model outputs</div>
        <span class="kpi-delta delta-neutral">${d.no_bet ?? 0} no-bet skipped</span>
      </div>
      <div class="card">
        <div class="card-title">Directional Accuracy</div>
        <div class="kpi-value ${d.model_directional_accuracy >= 0.55 ? 'green' : d.model_directional_accuracy >= 0.5 ? 'yellow' : 'red'}" style="margin-top:12px">${dir}</div>
        <div class="kpi-label">Model picked winner</div>
        <span class="kpi-delta delta-neutral">all graded games</span>
      </div>
    `;

    // also render donut chart
    renderWLChart(d.wins ?? 0, d.losses ?? 0, d.pushes ?? 0);

    $('chart-summary').innerHTML = `
      <div class="stat-grid">
        <div class="stat-item"><div class="stat-val green">${d.wins ?? 0}</div><div class="stat-lbl">Wins</div></div>
        <div class="stat-item"><div class="stat-val red">${d.losses ?? 0}</div><div class="stat-lbl">Losses</div></div>
        <div class="stat-item"><div class="stat-val muted">${d.pushes ?? 0}</div><div class="stat-lbl">Pushes</div></div>
        <div class="stat-item"><div class="stat-val yellow">${d.no_bet ?? 0}</div><div class="stat-lbl">No Bet</div></div>
      </div>
    `;

  } catch(e) {
    $('kpi-row').innerHTML = `<div class="card"><div class="empty">Could not load summary: ${e.message}</div></div>`;
  }
}

// ── WL donut chart ──────────────────────────────────────────────────────────
let wlChartInstance = null;
function renderWLChart(wins, losses, pushes) {
  const ctx = document.getElementById('wl-chart').getContext('2d');
  if (wlChartInstance) wlChartInstance.destroy();
  const total = wins + losses + pushes;
  if (total === 0) {
    $('chart-card').innerHTML += '<div class="empty" style="margin-top:12px">No graded bets yet</div>';
    return;
  }
  wlChartInstance = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Wins', 'Losses', 'Pushes'],
      datasets: [{
        data: [wins, losses, pushes],
        backgroundColor: ['#3fb950', '#f85149', '#7d8590'],
        borderColor: '#161b22',
        borderWidth: 3,
        hoverOffset: 4,
      }]
    },
    options: {
      cutout: '68%',
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: '#7d8590', font: { size: 12 }, padding: 16 }
        },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.label}: ${ctx.raw} (${((ctx.raw/total)*100).toFixed(1)}%)`
          }
        }
      },
      animation: { duration: 600 }
    }
  });
}

// ── Ranked bets ─────────────────────────────────────────────────────────────
async function loadRanked() {
  try {
    const r = await fetch('/api/ranked/bets?limit=20&active_only=false');
    const bets = await r.json();
    if (!bets.length) {
      $('ranked-table').innerHTML = '<div class="empty">No ranked bets found for today.</div>';
      return;
    }
    const snap = bets[0]?.snapshot_type || '';
    $('ranked-snap').textContent = snap ? `[${snap}]` : '';

    const rows = bets.map(b => {
      const move = b.movement_direction
        ? `<span class="move-${b.movement_direction}" title="Line movement">${b.movement_direction === 'sharp_away' ? '↑sharp' : b.movement_direction === 'sharp_home' ? '↓sharp' : b.movement_direction}</span>`
        : '';
      return `<tr>
        <td>${rankBadge(b.rank)}</td>
        <td>
          <div style="font-weight:600">${b.away_team} @ ${b.home_team}</div>
          <div class="muted" style="font-size:11px">${b.away_probable_pitcher || '?'} vs ${b.home_probable_pitcher || '?'}</div>
        </td>
        <td>${playChip(b.play)}</td>
        <td>${colEV(b.ev)}</td>
        <td><span class="${b.edge_pct > 0 ? 'green' : 'red'}">${(b.edge_pct*100).toFixed(2)}%</span></td>
        <td>${confidenceChip(b.confidence)}</td>
        <td>${move}</td>
        <td class="muted" style="font-size:11px">${b.start_time ? parseTime(b.start_time) : '—'}</td>
      </tr>`;
    }).join('');

    $('ranked-table').innerHTML = `
      <table>
        <thead><tr>
          <th>#</th><th>Matchup</th><th>Play</th><th>EV</th><th>Edge</th><th>Conf</th><th>Move</th><th>Start</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch(e) {
    $('ranked-table').innerHTML = `<div class="empty">Error: ${e.message}</div>`;
  }
}

// ── Today's games ───────────────────────────────────────────────────────────
async function loadGames() {
  try {
    const r = await fetch('/api/games/today');
    const games = await r.json();
    $('games-count').textContent = `${games.length} game${games.length !== 1 ? 's' : ''}`;
    if (!games.length) {
      $('games-list').innerHTML = '<div class="empty">No games scheduled today.</div>';
      return;
    }
    const cards = games.map(g => {
      const isLive = (g.status||'').toLowerCase().includes('progress') || (g.status||'').toLowerCase().includes('live');
      const isFinal = (g.status||'').toLowerCase().includes('final') || (g.status||'').toLowerCase().includes('complet');
      const dot = isLive ? '<span class="pulse"></span>' : '';
      const score = isFinal && g.final_away_score != null
        ? `<div class="game-score">${g.final_away_score}–${g.final_home_score}</div>`
        : isLive ? `<div class="game-score green" style="font-size:14px">LIVE</div>`
        : `<div class="muted" style="font-size:12px">${g.start_time ? parseTime(g.start_time) : '—'}</div>`;
      const statusCls = statusClass(g.status);
      return `<div class="game-card">
        <div>
          <div class="game-teams">${g.away_team} <span class="muted">@</span> ${g.home_team}</div>
          <div class="game-meta">${g.venue || ''}</div>
          <div class="game-meta" style="margin-top:3px">${g.away_probable_pitcher || 'TBD'} vs ${g.home_probable_pitcher || 'TBD'}</div>
        </div>
        <div style="text-align:right">
          ${score}
          <div class="${statusCls}" style="margin-top:4px">${dot}${g.status || 'Scheduled'}</div>
        </div>
      </div>`;
    }).join('');
    $('games-list').innerHTML = cards;
  } catch(e) {
    $('games-list').innerHTML = `<div class="empty">Error: ${e.message}</div>`;
  }
}

// ── Recent reviews ──────────────────────────────────────────────────────────
async function loadReviews() {
  try {
    const r = await fetch('/api/reviews/recent?limit=15');
    const rows = await r.json();
    if (!rows.length) {
      $('reviews-table').innerHTML = '<div class="empty">No resolved bets yet.</div>';
      return;
    }
    const html = rows.map(row => {
      const correct = row.model_correct
        ? '<span class="correct">✓</span>'
        : '<span class="incorrect">✗</span>';
      const ev = row.ev != null ? colEV(row.ev) : '—';
      return `<tr>
        <td class="muted" style="font-size:11px">${row.date}</td>
        <td style="font-weight:500">${row.matchup}</td>
        <td>${playChip(row.predicted_side)}</td>
        <td>${ev}</td>
        <td>${resultChip(row.bet_result)}</td>
        <td style="text-align:center">${correct}</td>
        <td class="muted" style="font-size:11px">${row.final_score || '—'}</td>
      </tr>`;
    }).join('');
    $('reviews-table').innerHTML = `
      <table>
        <thead><tr>
          <th>Date</th><th>Matchup</th><th>Play</th><th>EV</th><th>Result</th><th>✓</th><th>Score</th>
        </tr></thead>
        <tbody>${html}</tbody>
      </table>`;
  } catch(e) {
    $('reviews-table').innerHTML = `<div class="empty">Error: ${e.message}</div>`;
  }
}

// ── Backtest ────────────────────────────────────────────────────────────────
async function loadBacktest() {
  try {
    const r = await fetch('/api/backtest/latest');
    const d = await r.json();
    if (!d) {
      $('backtest-body').innerHTML = `
        <div class="empty">No backtest data yet. Run <code style="background:var(--surface2);padding:2px 6px;border-radius:4px">POST /api/backtest/collect</code> then <code style="background:var(--surface2);padding:2px 6px;border-radius:4px">POST /api/backtest/run</code>.</div>`;
      return;
    }
    const runAt = d.run_at ? new Date(d.run_at).toLocaleDateString('en-US', {month:'short',day:'numeric',year:'numeric'}) : '—';
    $('backtest-meta').textContent = `Seasons: ${d.seasons} · Run ${runAt} · ${d.n_games?.toLocaleString()} games`;

    const accColor = d.accuracy >= 0.6 ? 'green' : d.accuracy >= 0.55 ? 'yellow' : 'red';
    const cvColor = d.cv_accuracy >= 0.58 ? 'green' : d.cv_accuracy >= 0.53 ? 'yellow' : 'red';

    // Feature importance bars
    const feats = d.feature_ranks || [];
    const maxW = feats.length ? Math.max(...feats.map(f => Math.abs(f.weight || 0))) : 1;
    const featHtml = feats.slice(0,8).map(f => {
      const pct = Math.abs(f.weight / maxW) * 100;
      const sign = f.weight >= 0 ? 'var(--blue)' : 'var(--red)';
      return `<div class="feat-bar-wrap">
        <div class="feat-bar-label">
          <span>${f.feature || f.name || '?'}</span>
          <span style="color:var(--muted)">${(f.weight||0).toFixed(4)}</span>
        </div>
        <div class="feat-bar-track">
          <div class="feat-bar-fill" style="width:${pct}%;background:${sign}"></div>
        </div>
      </div>`;
    }).join('');

    $('backtest-body').innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;align-items:start">
        <div>
          <div class="section-label">Model Accuracy</div>
          <div class="stat-grid">
            <div class="stat-item">
              <div class="stat-val ${accColor}">${pct(d.accuracy)}</div>
              <div class="stat-lbl">In-sample accuracy</div>
            </div>
            <div class="stat-item">
              <div class="stat-val ${cvColor}">${pct(d.cv_accuracy)}</div>
              <div class="stat-lbl">Cross-val accuracy</div>
            </div>
            <div class="stat-item">
              <div class="stat-val muted">${d.log_loss != null ? d.log_loss.toFixed(4) : '—'}</div>
              <div class="stat-lbl">Log loss</div>
            </div>
            <div class="stat-item">
              <div class="stat-val">${(d.n_games||0).toLocaleString()}</div>
              <div class="stat-lbl">Training games</div>
            </div>
          </div>
        </div>
        <div style="grid-column:span 2">
          <div class="section-label">Feature Importance</div>
          ${featHtml || '<div class="empty">No feature data</div>'}
        </div>
      </div>`;
  } catch(e) {
    $('backtest-body').innerHTML = `<div class="empty">Error: ${e.message}</div>`;
  }
}

// ── Load all ────────────────────────────────────────────────────────────────
async function loadAll() {
  $('last-updated').textContent = 'Refreshing…';
  await Promise.all([
    loadSummary(),
    loadRanked(),
    loadGames(),
    loadReviews(),
    loadBacktest(),
  ]);
  const now = new Date().toLocaleTimeString('en-US', { hour:'numeric', minute:'2-digit', second:'2-digit' });
  $('last-updated').textContent = `Updated ${now}`;
}

document.addEventListener('DOMContentLoaded', loadAll);
</script>
</body>
</html>
"""


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)
