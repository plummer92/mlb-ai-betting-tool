from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

SHARED_STYLE = r"""
/* ── Reset ── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:          #0a0e1a;
  --surface:     #0e1425;
  --surface2:    #131d30;
  --surface3:    #18253d;
  --border:      #1e3050;
  --border-glow: #2a4a7f;
  --text:        #e2e8f0;
  --muted:       #5a7a9c;
  --dim:         #2d3748;
  --cyan:        #06b6d4;
  --cyan-dim:    #052836;
  --purple:      #a855f7;
  --purple-dim:  #2d1b4e;
  --green:       #10b981;
  --green-dim:   #052e1d;
  --yellow:      #f59e0b;
  --yellow-dim:  #2d1d00;
  --red:         #ef4444;
  --red-dim:     #2d0b0b;
  --orange:      #f97316;
  --orange-dim:  #2a1100;
  --blue:        #3b82f6;
}

body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  min-height: 100vh;
}

/* subtle scanlines */
body::after {
  content: '';
  position: fixed;
  inset: 0;
  background: repeating-linear-gradient(
    0deg,
    transparent,
    transparent 3px,
    rgba(0,0,0,0.04) 3px,
    rgba(0,0,0,0.04) 4px
  );
  pointer-events: none;
  z-index: 9999;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--border-glow); }

/* ── Header ── */
.header {
  background: linear-gradient(180deg, #0d1526 0%, var(--bg) 100%);
  border-bottom: 1px solid var(--border-glow);
  padding: 0 32px;
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 100;
  box-shadow: 0 1px 24px rgba(6,182,212,0.07);
}

.header-brand {
  display: flex;
  align-items: center;
  gap: 20px;
}

.war-room-title {
  font-family: 'Courier New', monospace;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 4px;
  text-transform: uppercase;
  color: var(--cyan);
  text-shadow: 0 0 18px rgba(6,182,212,0.45);
}

.header-nav {
  display: flex;
  align-items: center;
  gap: 12px;
  font-family: 'Courier New', monospace;
  font-size: 13px;
  font-weight: 700;
}
.nav-link {
  color: var(--muted);
  text-decoration: none;
  transition: color 0.2s;
}
.nav-link:hover, .nav-link.active {
  color: var(--cyan);
  text-shadow: 0 0 8px rgba(6,182,212,0.4);
}
.nav-sep { color: var(--border); }

.live-badge {
  display: flex;
  align-items: center;
  gap: 5px;
  font-family: monospace;
  font-size: 10px;
  font-weight: 700;
  color: var(--green);
  letter-spacing: 2px;
  border: 1px solid rgba(16,185,129,0.3);
  padding: 3px 9px;
  border-radius: 3px;
  background: rgba(16,185,129,0.05);
}

.pulse-dot {
  width: 7px;
  height: 7px;
  background: var(--green);
  border-radius: 50%;
  box-shadow: 0 0 6px var(--green);
  animation: blink 2s ease-in-out infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; box-shadow: 0 0 6px var(--green); }
  50%       { opacity: 0.35; box-shadow: none; }
}

.header-meta {
  display: flex;
  align-items: center;
  gap: 20px;
}

.header-date {
  font-family: monospace;
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 1px;
}

.refresh-info {
  font-family: monospace;
  font-size: 11px;
  color: var(--muted);
  display: flex;
  align-items: center;
  gap: 5px;
}
.refresh-info .cdown { color: var(--cyan); font-weight: 700; min-width: 22px; display: inline-block; }

.refresh-btn {
  background: transparent;
  border: 1px solid var(--border-glow);
  color: var(--cyan);
  padding: 5px 14px;
  border-radius: 4px;
  cursor: pointer;
  font-family: monospace;
  font-size: 11px;
  letter-spacing: 1px;
  transition: all 0.15s;
}
.refresh-btn:hover {
  background: var(--cyan-dim);
  box-shadow: 0 0 10px rgba(6,182,212,0.15);
}

/* ── Layout ── */
.container {
  max-width: 1680px;
  margin: 0 auto;
  padding: 28px 32px;
}

/* ── Section ── */
.section { margin-bottom: 36px; }

.section-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}

.section-label {
  font-family: 'Courier New', monospace;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 5px;
  color: var(--cyan);
}

.section-rule {
  flex: 1;
  height: 1px;
  background: linear-gradient(90deg, var(--border-glow) 0%, transparent 100%);
}

.section-badge {
  font-family: monospace;
  font-size: 10px;
  color: var(--muted);
  border: 1px solid var(--border);
  padding: 2px 9px;
  border-radius: 3px;
  letter-spacing: 1px;
  white-space: nowrap;
}

/* ── Card ── */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 20px;
}

/* ── Summary bar ── */
.summary-bar {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-left: 3px solid var(--cyan);
  border-radius: 6px;
  padding: 14px 22px;
  display: flex;
  align-items: center;
  gap: 0;
  margin-bottom: 14px;
  flex-wrap: wrap;
  row-gap: 10px;
}

.s-stat {
  padding: 0 22px;
  border-right: 1px solid var(--border);
}
.s-stat:first-child { padding-left: 0; }
.s-stat:last-child  { border-right: none; }

.s-stat-val {
  font-family: monospace;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.1;
}
.s-stat-lbl {
  font-size: 10px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.8px;
  margin-top: 2px;
}

/* ── Table ── */
.tbl-wrap { overflow-x: auto; }

table { width: 100%; border-collapse: collapse; font-size: 13px; }

th {
  text-align: left;
  padding: 8px 12px;
  color: var(--muted);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  font-family: monospace;
}

td {
  padding: 9px 12px;
  border-bottom: 1px solid rgba(30,48,80,0.5);
  vertical-align: middle;
}

tr:last-child td { border-bottom: none; }
tr:hover td { background: rgba(6,182,212,0.025); }

/* ── Game grid ── */
.game-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}
@media (max-width: 1200px) { .game-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 768px)  { .game-grid { grid-template-columns: 1fr; } }

.commentary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
@media (max-width: 1100px) { .commentary-grid { grid-template-columns: 1fr; } }

.commentary-card {
  background: linear-gradient(180deg, var(--surface) 0%, var(--surface2) 100%);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px 18px;
}

.commentary-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.commentary-matchup {
  font-size: 14px;
  font-weight: 700;
  color: var(--text);
}

.commentary-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}

.commentary-text {
  color: #c9d6e6;
  font-size: 13px;
  line-height: 1.6;
}

.commentary-foot {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid rgba(30,48,80,0.7);
  font-family: monospace;
  font-size: 10px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.6px;
}

/* ── Game card ── */
.game-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 15px 16px;
  transition: all 0.2s;
  cursor: pointer;
  position: relative;
}
.game-card:hover { 
  border-color: var(--cyan); 
  box-shadow: 0 0 20px rgba(6,182,212,0.1);
  transform: translateY(-2px);
}
.game-card.is-live {
  border-color: rgba(16,185,129,0.45);
  box-shadow: 0 0 14px rgba(16,185,129,0.07);
}
.game-card.is-final { opacity: 0.72; }

.gc-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 9px;
}

.gc-matchup {
  font-size: 14px;
  font-weight: 700;
  line-height: 1.2;
}
.gc-matchup .aw { color: var(--cyan); }
.gc-matchup .hw { color: var(--purple); }
.gc-matchup .at { color: var(--muted); margin: 0 4px; font-weight: 400; }

.gc-status-live {
  font-family: monospace;
  font-size: 10px;
  font-weight: 700;
  color: var(--green);
  display: flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.gc-status-final {
  font-family: monospace;
  font-size: 10px;
  color: var(--muted);
  white-space: nowrap;
}
.gc-status-time {
  font-family: monospace;
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
}

.gc-score {
  font-family: monospace;
  font-size: 26px;
  font-weight: 700;
  text-align: center;
  margin: 6px 0;
}
.gc-score .sep { color: var(--muted); font-size: 18px; margin: 0 7px; }

.gc-pitchers {
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* win prob bar */
.wp-bar-wrap { margin-bottom: 10px; }
.wp-bar {
  display: flex;
  height: 7px;
  border-radius: 4px;
  overflow: hidden;
  background: var(--surface2);
  margin-bottom: 4px;
}
.wp-away { background: var(--cyan); transition: width 0.5s ease; }
.wp-home { background: var(--purple); transition: width 0.5s ease; }
.wp-labels {
  display: flex;
  justify-content: space-between;
  font-family: monospace;
  font-size: 10px;
}
.wp-lbl-a { color: var(--cyan); font-weight: 700; }
.wp-lbl-h { color: var(--purple); font-weight: 700; }

.gc-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 9px;
  border-top: 1px solid var(--border);
  flex-wrap: wrap;
  gap: 6px;
}

.gc-totals {
  font-family: monospace;
  font-size: 11px;
  color: var(--muted);
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.gc-totals strong { color: var(--text); }

.gc-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}

/* ── Chips ── */
.chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  font-family: monospace;
  white-space: nowrap;
}
.c-strong { background: var(--green-dim);  color: var(--green);  border: 1px solid rgba(16,185,129,0.3); }
.c-medium { background: var(--yellow-dim); color: var(--yellow); border: 1px solid rgba(245,158,11,0.3); }
.c-weak   { background: var(--surface2);   color: var(--muted);  border: 1px solid var(--border); }
.c-none   { background: var(--surface2);   color: var(--dim);    border: 1px solid var(--border); }
.c-win    { background: var(--green-dim);  color: var(--green);  border: 1px solid rgba(16,185,129,0.3); }
.c-loss   { background: var(--red-dim);    color: var(--red);    border: 1px solid rgba(239,68,68,0.3); }
.c-push   { background: var(--surface2);   color: var(--muted);  border: 1px solid var(--border); }
.c-no_bet { color: var(--dim); font-size: 10px; }

/* movement */
.mv-toward  { color: var(--green);  font-family: monospace; font-size: 10px; }
.mv-away    { color: var(--red);    font-family: monospace; font-size: 10px; }
.mv-sharp   { color: var(--orange); font-family: monospace; font-size: 10px; font-weight: 700; }
.mv-neutral { color: var(--muted);  font-family: monospace; font-size: 10px; }

/* correct/incorrect */
.ok   { color: var(--green); font-weight: 700; }
.fail { color: var(--red);   font-weight: 700; }

/* ── Status panels ── */
.status-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
}
@media (max-width: 900px) { .status-grid { grid-template-columns: 1fr; } }

/* terminal */
.term {
  background: #060a12;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.term-bar {
  background: var(--surface2);
  border-bottom: 1px solid var(--border);
  padding: 8px 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.tb-dot { width: 10px; height: 10px; border-radius: 50%; }
.tb-red    { background: #ff5f57; }
.tb-yellow { background: #ffbd2e; }
.tb-green  { background: #28c940; }
.term-title {
  font-family: monospace;
  font-size: 11px;
  color: var(--muted);
  margin-left: 4px;
  letter-spacing: 1px;
}
.term-body {
  padding: 14px 16px;
  font-family: 'Courier New', Courier, monospace;
  font-size: 12px;
  line-height: 1.75;
  color: #4ade80;
  min-height: 300px;
  max-height: 400px;
  overflow-y: auto;
}
.term-body .t-prompt  { color: #06b6d4; }
.term-body .t-cmd     { color: #e2e8f0; }
.term-body .t-dim     { color: #1e3050; }
.term-body .t-name    { color: #4ade80; }
.term-body .t-time    { color: #f59e0b; }
.term-body .t-soon    { color: #f97316; }
.term-body .t-pending { color: #5a7a9c; }
.term-body .t-ok      { color: #10b981; }
.term-body .t-err     { color: #ef4444; }

/* model stats */
.model-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 20px;
}
.mp-header {
  font-family: monospace;
  font-size: 10px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 2px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 14px;
}
.mv-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: var(--cyan-dim);
  border: 1px solid rgba(6,182,212,0.25);
  color: var(--cyan);
  padding: 4px 12px;
  border-radius: 4px;
  font-family: monospace;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.5px;
  margin-bottom: 14px;
}
.ms-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.ms-tile {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 11px 13px;
}
.ms-val {
  font-family: monospace;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.2;
}
.ms-lbl {
  font-size: 10px;
  color: var(--muted);
  margin-top: 3px;
  letter-spacing: 0.3px;
}
.ms-footnote {
  margin-top: 13px;
  font-size: 11px;
  color: var(--muted);
  font-family: monospace;
}

/* ── Outcome note ── */
.outcome-note {
  font-size: 11px;
  color: var(--muted);
  font-style: italic;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── Modal ── */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.85);
  display: none;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(4px);
}
.modal-content {
  background: var(--surface);
  border: 1px solid var(--border-glow);
  border-radius: 12px;
  width: 90%;
  max-width: 650px;
  max-height: 90vh;
  overflow-y: auto;
  padding: 32px;
  position: relative;
  box-shadow: 0 0 40px rgba(6,182,212,0.15);
}
.modal-close {
  position: absolute;
  top: 16px;
  right: 16px;
  background: transparent;
  border: none;
  color: var(--muted);
  font-size: 24px;
  cursor: pointer;
}
.modal-close:hover { color: var(--text); }

.modal-title {
  font-family: 'Courier New', monospace;
  font-size: 20px;
  font-weight: 700;
  color: var(--cyan);
  margin-bottom: 24px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 12px;
}

.dive-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}
.dive-section {
  margin-bottom: 20px;
}
.dive-label {
  font-size: 10px;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 1px;
  margin-bottom: 6px;
}
.dive-val {
  font-family: monospace;
  font-size: 18px;
  font-weight: 700;
}
.dive-val.large { font-size: 24px; }

/* ── Loading / Empty ── */
.loading {
  color: var(--muted);
  font-family: monospace;
  font-size: 12px;
  padding: 36px;
  text-align: center;
  letter-spacing: 1px;
}
.empty {
  color: var(--muted);
  font-family: monospace;
  font-size: 12px;
  padding: 24px;
  text-align: center;
}

/* ── Color utils ── */
.green  { color: var(--green); }
.red    { color: var(--red); }
.yellow { color: var(--yellow); }
.cyan   { color: var(--cyan); }
.purple { color: var(--purple); }
.muted  { color: var(--muted); }
.mono   { font-family: monospace; }

@media (max-width: 600px) {
  .container { padding: 14px 16px; }
  .header { padding: 0 16px; }
  .war-room-title { font-size: 13px; letter-spacing: 2px; }
  .header-date { display: none; }
  .dive-grid { grid-template-columns: 1fr; }
}
"""

SHARED_JS_HELPERS = r"""
const $  = id => document.getElementById(id);
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const fmt = (n, d=1) => n == null ? '—' : (+n).toFixed(d);
const pct0_1 = (n, d=1) => n == null ? '—' : (+n * 100).toFixed(d) + '%';
const pct100 = (n, d=1) => n == null ? '—' : (+n).toFixed(d) + '%';

function todayStr() {
  const d = new Date();
  return [d.getFullYear(), String(d.getMonth()+1).padStart(2,'0'), String(d.getDate()).padStart(2,'0')].join('-');
}
function friendlyDate(yyyymmdd) {
  if (!yyyymmdd) return '';
  const p = yyyymmdd.split('-');
  return new Date(+p[0], +p[1]-1, +p[2]).toLocaleDateString('en-US', {weekday:'short',month:'short',day:'numeric',year:'numeric'});
}
function gameTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleTimeString('en-US', {hour:'numeric',minute:'2-digit',timeZoneName:'short'});
  } catch { return iso; }
}
function colorClass(val, good, warn) {
  if (val == null) return 'muted';
  return val >= good ? 'green' : val >= warn ? 'yellow' : 'red';
}
"""

def get_header(active_page: str):
    war_room_active = "active" if active_page == "dashboard" else ""
    system_active = "active" if active_page == "system" else ""
    simulator_active = "active" if active_page == "simulator" else ""
    return f"""
<header class="header">
  <div class="header-brand">
    <div class="war-room-title">MLB AI WAR ROOM</div>
    <nav class="header-nav">
      <a href="/dashboard" class="nav-link {war_room_active}">[ WAR ROOM ]</a>
      <span class="nav-sep">|</span>
      <a href="/system" class="nav-link {system_active}">[ SYSTEM STATUS ]</a>
      <span class="nav-sep">|</span>
      <a href="/simulator" class="nav-link {simulator_active}">[ SIMULATOR ]</a>
    </nav>
  </div>
  <div class="header-meta">
    <span class="header-date" id="hdr-date"></span>
    <div class="refresh-info">↻ NEXT <span class="cdown" id="cdown">60</span>s</div>
    <button class="refresh-btn" onclick="loadAll()">⟳ REFRESH</button>
  </div>
</header>
"""

DASHBOARD_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MLB AI WAR ROOM</title>
<style>{SHARED_STYLE}</style>
</head>
<body>

{get_header("dashboard")}

<div class="container">

  <!-- ═══════════════ 1. YESTERDAY'S DEBRIEF ═══════════════ -->
  <div class="section">
    <div class="section-head">
      <div class="section-label" id="debrief-title">Debrief</div>
      <div class="section-rule"></div>
      <div class="section-badge" id="debrief-badge">—</div>
    </div>
    <div id="market-performance"></div>
    <div id="confidence-performance"></div>
    <div id="debrief-summary"></div>
    <div class="card" style="padding:0;margin-top:12px">
      <div class="tbl-wrap" id="debrief-table">
        <div class="loading">Loading debrief</div>
      </div>
    </div>
  </div>

  <!-- ═══════════════ 2. TODAY'S ACTIVE INTEL ═══════════════ -->
  <div class="section">
    <div class="section-head">
      <div class="section-label">AI Commentary</div>
      <div class="section-rule"></div>
      <div class="section-badge" id="commentary-badge">—</div>
    </div>
    <div class="commentary-grid" id="commentary-grid">
      <div class="loading">Loading commentary</div>
    </div>
  </div>

  <!-- ═══════════════ 3. TODAY'S ACTIVE INTEL ═══════════════ -->
  <div class="section">
    <div class="section-head">
      <div class="section-label">Today's Active Intel</div>
      <div class="section-rule"></div>
      <div class="section-badge" id="intel-badge">—</div>
    </div>
    <div class="game-grid" id="game-grid">
      <div class="loading">Loading intel</div>
    </div>
  </div>

</div><!-- /container -->

<!-- DEEP DIVE MODAL -->
<div id="deep-dive-modal" class="modal-overlay" onclick="closeModal(event)">
  <div class="modal-content" onclick="event.stopPropagation()">
    <button class="modal-close" onclick="closeModal()">×</button>
    <div id="modal-body-content"></div>
  </div>
</div>

<script>
{SHARED_JS_HELPERS}

let currentIntel = {{ games: [], edges: {{}}, preds: {{}} }};

function resolveTeam(side, away, home) {{
  if (side === 'away_ml') return away || 'Away';
  if (side === 'home_ml') return home || 'Home';
  if (side === 'over')    return 'Over';
  if (side === 'under')   return 'Under';
  return side || '—';
}}

function movementHtml(dir) {{
  if (!dir) return '';
  const map = {{
    toward_model:    ['mv-toward',  '↑ w/ model'],
    away_from_model: ['mv-away',    '↓ vs model'],
    sharp_away:      ['mv-sharp',   '⚡ sharp away'],
    sharp_home:      ['mv-sharp',   '⚡ sharp home'],
    neutral:         ['mv-neutral', '→ neutral'],
  }};
  const [cls, lbl] = map[dir] || ['mv-neutral', dir];
  return `<span class="${{cls}}">${{lbl}}</span>`;
}}

function edgeChip(confidence, edgePct) {{
  const pctStr = edgePct != null ? ` ${{ (edgePct*100).toFixed(1) }}%` : '';
  if (!confidence || confidence === 'weak') {{
    return `<span class="chip c-weak">WEAK${{pctStr}}</span>`;
  }}
  const cls = confidence === 'strong' ? 'c-strong' : 'c-medium';
  return `<span class="chip ${{cls}}">${{confidence.toUpperCase()}}${{pctStr}}</span>`;
}}

function betChip(result) {{
  if (!result || result === 'no_bet') return '<span class="chip c-no_bet">NO BET</span>';
  return `<span class="chip c-${{result}}">${{result.toUpperCase()}}</span>`;
}}

async function loadCommentary() {{
  try {{
    const res = await fetch('/api/commentary/today');
    const data = await res.json();
    const items = data.items || [];
    const source = data.source || 'alerts';

    $('commentary-badge').textContent = items.length
      ? `${{items.length}} NOTE${{items.length !== 1 ? 'S' : ''}}`
      : 'STANDBY';

    if (!items.length) {{
      $('commentary-grid').innerHTML = `
        <div class="card">
          <div class="empty">No commentary yet. This panel will populate from today’s alerts or live edge reads as the board develops.</div>
        </div>`;
      return;
    }}

    const sourceLabel = source === 'alerts' ? 'alert-backed' : 'live edge read';
    const cards = items.map(item => {{
      const edgeLabel = item.edge_pct != null ? `${{(item.edge_pct * 100).toFixed(1)}}% edge` : 'watchlist';
      const timeLabel = item.alert_time ? gameTime(item.alert_time) : 'latest';
      return `
        <div class="commentary-card">
          <div class="commentary-head">
            <div class="commentary-matchup">${{esc(item.matchup || `${{item.away_team}} @ ${{item.home_team}}`)}}</div>
            ${{edgeChip(item.confidence, item.edge_pct)}}
          </div>
          <div class="commentary-meta">
            <span class="chip c-none">${{esc((item.play || 'watch').replace('_', ' '))}}</span>
            <span class="chip c-none">${{esc(sourceLabel)}}</span>
            ${{item.status ? `<span class="chip c-none">${{esc(item.status)}}</span>` : ''}}
          </div>
          <div class="commentary-text">${{esc(item.synopsis || 'No commentary available.')}}</div>
          <div class="commentary-foot">
            <span>${{esc(edgeLabel)}}</span>
            <span>${{esc(timeLabel)}}</span>
          </div>
        </div>`;
    }}).join('');

    $('commentary-grid').innerHTML = cards;
  }} catch(e) {{
    $('commentary-grid').innerHTML = `<div class="card"><div class="empty">Error: ${{esc(e.message)}}</div></div>`;
  }}
}}

async function loadAccuracy() {{
  try {{
    const res = await fetch('/api/reviews/accuracy');
    const data = await res.json();
    
    const mkStat = (label, stats) => {{
      if (!stats || stats.bets === 0) {{
        return `<div class="s-stat">
          <div class="s-stat-val muted">0%</div>
          <div class="s-stat-lbl">${{label}} (0)</div>
        </div>`;
      }}
      const val = stats.win_rate * 100;
      const cls = colorClass(val, 55, 48);
      const odds = stats.avg_odds ? `Odds: ${{stats.avg_odds.toFixed(2)}}` : '';
      const edge = stats.avg_edge ? `Edge: ${{ (stats.avg_edge*100).toFixed(1) }}%` : '';

      return `<div class="s-stat">
        <div class="s-stat-val ${{cls}}">${{val.toFixed(1)}}%</div>
        <div class="s-stat-lbl">${{label}} (${{stats.bets}})</div>
        <div style="font-size:9px;color:var(--muted)">${{odds}} | ${{edge}}</div>
      </div>`;
    }};

    $('market-performance').innerHTML = `
      <div class="summary-bar" style="border-left-color: var(--purple); background: var(--surface3); margin-bottom: 12px;">
        <div style="font-family: monospace; font-size: 9px; color: var(--muted); text-transform: uppercase; letter-spacing: 2px; width: 100%; margin-bottom: 8px; border-bottom: 1px solid var(--border); padding-bottom: 4px;">
          Lifetime Market Performance — Model: ${{data.current_model}}
        </div>
        ${{mkStat('Moneyline', data.moneyline)}}
        ${{mkStat('Totals', data.totals)}}
        ${{mkStat('Run Line', data.run_line)}}
        <div style="margin-left:auto"></div>
        ${{mkStat('Overall', data.overall)}}
      </div>
    `;

    const renderConfRow = (label, marketData, color) => {{
      if (!marketData || !marketData.confidence_bins) return '';
      const bins = marketData.confidence_bins;
      const binNames = ["50-59%", "60-69%", "70-79%", "80%+"];
      const hasBets = binNames.some(n => bins[n] && bins[n].bets > 0);
      if (!hasBets) return '';

      const binsHtml = binNames.map(name => {{
        const stats = bins[name];
        if (!stats || stats.bets === 0) return '';
        const val = stats.win_rate * 100;
        const cls = colorClass(val, 55, 48);
        const odds = stats.avg_odds ? `Odds: ${{stats.avg_odds.toFixed(2)}}` : '';
        const edge = stats.avg_edge ? `Edge: ${{ (stats.avg_edge*100).toFixed(1) }}%` : '';
        return `
          <div class="s-stat" style="min-width: 200px;">
            <div class="s-stat-val ${{cls}}">${{val.toFixed(1)}}%</div>
            <div class="s-stat-lbl">${{name}} Confidence: ${{stats.wins}}W - ${{stats.losses}}L</div>
            <div style="font-size:9px;color:var(--muted)">${{odds}} | ${{edge}}</div>
          </div>
        `;
      }}).join('');

      return `
        <div class="summary-bar" style="border-left-color: ${{color}}; background: var(--surface2); margin-bottom: 12px;">
          <div style="font-family: monospace; font-size: 9px; color: var(--muted); text-transform: uppercase; letter-spacing: 2px; width: 100%; margin-bottom: 8px; border-bottom: 1px solid var(--border); padding-bottom: 4px;">
            ${{label}} Win Rate by Model Confidence
          </div>
          ${{binsHtml}}
        </div>
      `;
    }};

    $('confidence-performance').innerHTML = `
      ${{renderConfRow('Moneyline', data.moneyline, 'var(--cyan)')}}
      ${{renderConfRow('Totals', data.totals, 'var(--purple)')}}
      ${{renderConfRow('Run Line', data.run_line, 'var(--orange)')}}
    `;
  }} catch(e) {{ console.error("Error loading accuracy", e); }}
}}

async function loadDebrief() {{
  try {{
    const res = await fetch('/api/reviews/recent?limit=100');
    const all = await res.json();
    const reviewDateOf = r => r.date || (r.created_at ? String(r.created_at).slice(0,10) : null);
    const latestDate = all.reduce((max, r) => {{
      const reviewDate = reviewDateOf(r);
      if (!reviewDate) return max;
      if (!max) return reviewDate;
      return reviewDate > max ? reviewDate : max;
    }}, null);

    $('debrief-title').textContent = latestDate
      ? `Debrief — ${{friendlyDate(latestDate)}}`
      : 'Debrief';
    $('debrief-badge').textContent = latestDate ? friendlyDate(latestDate) : '—';

    const rows = latestDate ? all.filter(r => reviewDateOf(r) === latestDate) : [];

    if (!rows.length) {{
      $('debrief-summary').innerHTML = '<div class="empty">No graded games available.</div>';
      $('debrief-table').innerHTML = '';
      return;
    }}

    const correct   = rows.filter(r => (r.model_correct ?? r.was_model_correct)).length;
    const wins      = rows.filter(r => r.bet_result === 'win').length;
    const losses    = rows.filter(r => r.bet_result === 'loss').length;
    const pushes    = rows.filter(r => r.bet_result === 'push').length;
    const graded    = wins + losses + pushes;
    const winRate   = (wins + losses) > 0 ? wins / (wins + losses) : null;
    const corrRate  = rows.length > 0 ? correct / rows.length : null;
    const wrPct     = winRate  != null ? (winRate*100).toFixed(1)+'%' : '—';
    const crPct     = corrRate != null ? (corrRate*100).toFixed(1)+'%' : '—';
    const wrCls     = colorClass(winRate  != null ? winRate*100  : null, 55, 48);
    const crCls     = colorClass(corrRate != null ? corrRate*100 : null, 55, 50);

    $('debrief-summary').innerHTML = `
      <div class="summary-bar">
        <div class="s-stat">
          <div class="s-stat-val ${{wrCls}}">${{correct}}/${{rows.length}}</div>
          <div class="s-stat-lbl">Model Correct</div>
        </div>
        <div class="s-stat">
          <div class="s-stat-val ${{wrCls}}">${{crPct}}</div>
          <div class="s-stat-lbl">Correct Rate</div>
        </div>
        <div class="s-stat">
          <div class="s-stat-val ${{wrCls}}">${{wrPct}}</div>
          <div class="s-stat-lbl">Bet Win Rate</div>
        </div>
        <div class="s-stat">
          <div class="s-stat-val green">${{wins}}</div>
          <div class="s-stat-lbl">Wins</div>
        </div>
        <div class="s-stat">
          <div class="s-stat-val red">${{losses}}</div>
          <div class="s-stat-lbl">Losses</div>
        </div>
        <div class="s-stat">
          <div class="s-stat-val muted">${{pushes}}</div>
          <div class="s-stat-lbl">Pushes</div>
        </div>
        <div class="s-stat">
          <div class="s-stat-val">${{graded}}</div>
          <div class="s-stat-lbl">Bets Graded</div>
        </div>
      </div>`;

    const trows = rows.map(r => {{
      const summary = r.actual_outcome_summary || '';
      const finalTeams = summary.match(/^Final:\s(.+?)\s\d+,\s(.+?)\s\d+\./);
      const away = r.away_team || (r.matchup || '').split(' @ ')[0] || finalTeams?.[1] || '?';
      const home = r.home_team || (r.matchup || '').split(' @ ')[1] || finalTeams?.[2] || '?';
      const matchup = r.matchup || `${{away}} @ ${{home}}`;
      const predicted = resolveTeam(r.predicted_side || r.recommended_play, away, home);
      const actualWinner = r.actual_winner || ((summary.match(/Winner:\s([^\.]+)\./) || [])[1]) || null;
      const actual = actualWinner === 'away' ? away
                   : actualWinner === 'home' ? home
                   : (actualWinner || '—');
      const modelCorrect = Boolean(r.model_correct ?? r.was_model_correct);
      const ci = modelCorrect ? '<span class="ok">✓</span>' : '<span class="fail">✗</span>';

      let proj = '—';
      if (r.model_total != null)                                    proj = fmt(r.model_total,1);
      else if (r.projected_away_score != null && r.projected_home_score != null)
        proj = fmt(r.projected_away_score + r.projected_home_score, 1);
      const actualTotal = r.actual_total != null
        ? r.actual_total
        : ((r.final_away_score != null && r.final_home_score != null)
            ? (r.final_away_score + r.final_home_score)
            : null);
      const act = (actualTotal != null && actualTotal > 0) ? actualTotal : '—';
      const totStr = proj === '—' ? '—' : `${{proj}} → ${{act}}`;

      const epStr   = r.edge_pct != null ? (r.edge_pct*100).toFixed(1)+'%' : '—';
      const epCls   = r.edge_pct != null && r.edge_pct > 0 ? 'green' : 'muted';
      const noteRaw = summary.slice(0, 120);
      const note    = noteRaw ? `<div class="outcome-note" title="${{esc(summary)}}">${{esc(noteRaw)}}</div>` : '';

      const scoreDisplay = r.final_score
        || ((r.final_away_score != null && r.final_home_score != null) ? `${{r.final_away_score}}-${{r.final_home_score}}` : '—');
      const hasRealScore = scoreDisplay !== '—';

      return `<tr>
        <td style="font-weight:600;white-space:nowrap">${{esc(matchup||'—')}}</td>
        <td class="mono" style="font-size:12px">${{esc(predicted)}}</td>
        <td class="mono" style="font-size:12px">${{esc(actual)}}</td>
        <td style="text-align:center">${{ci}}</td>
        <td class="mono" style="font-size:12px">${{totStr}}</td>
        <td class="mono ${{epCls}}" style="font-size:12px">${{epStr}}</td>
        <td class="mono" style="font-size:12px">${{scoreDisplay}}</td>
        <td>${{betChip(r.bet_result)}}</td>
        <td>${{note}}</td>
      </tr>`;
    }}).join('');

    $('debrief-table').innerHTML = `
      <table>
        <thead><tr>
          <th>Matchup</th><th>Predicted</th><th>Actual</th>
          <th style="text-align:center">✓/✗</th>
          <th>Proj→Act Total</th><th>Edge %</th>
          <th>Score</th><th>Bet</th><th>Note</th>
        </tr></thead>
        <tbody>${{trows}}</tbody>
      </table>`;

  }} catch(e) {{
    $('debrief-summary').innerHTML = `<div class="empty">Error: ${{esc(e.message)}}</div>`;
    $('debrief-table').innerHTML = '';
  }}
}}

async function loadIntel() {{
  try {{
    const [gr, er, pr] = await Promise.all([
      fetch('/api/games/today'),
      fetch('/api/edges/today'),
      fetch('/api/model/predictions/today'),
    ]);
    const games = await gr.json();
    const edges = await er.json();
    const preds = await pr.json();

    currentIntel.games = games;
    edges.forEach(e => currentIntel.edges[e.game_id] = e);
    preds.forEach(p => currentIntel.preds[p.game_id] = p);

    const em = currentIntel.edges, pm = currentIntel.preds;

    $('intel-badge').textContent = `${{games.length}} GAME${{games.length !== 1 ? 'S' : ''}}`;

    if (!games.length) {{
      $('game-grid').innerHTML = '<div class="empty">No games scheduled today.</div>';
      return;
    }}

    const cards = games.map(g => {{
      const edge = em[g.game_id];
      const pred = pm[g.game_id];

      const sl = (g.status||'').toLowerCase();
      const isLive  = sl.includes('progress') || sl.includes('live') || sl === 'in_progress';
      const isFinal = sl.includes('final') || sl.includes('complet');

      let statusHtml;
      if (isLive) {{
        statusHtml = `<div class="gc-status-live"><span class="pulse-dot" style="width:6px;height:6px"></span>LIVE</div>`;
      }} else if (isFinal) {{
        statusHtml = `<div class="gc-status-final">FINAL</div>`;
      }} else {{
        statusHtml = `<div class="gc-status-time">${{gameTime(g.start_time)}}</div>`;
      }}

      let scoreHtml = '';
      if ((isLive || isFinal) && g.final_away_score != null) {{
        const ac = g.final_away_score > g.final_home_score ? 'cyan' : 'muted';
        const hc = g.final_home_score > g.final_away_score ? 'purple' : 'muted';
        scoreHtml = `<div class="gc-score">
          <span class="${{ac}}">${{g.final_away_score}}</span>
          <span class="sep">—</span>
          <span class="${{hc}}">${{g.final_home_score}}</span>
        </div>`;
      }}

      let wpHtml = '';
      if (pred) {{
        const ap = Math.round(pred.away_win_pct * 100);
        const hp = 100 - ap;
        wpHtml = `<div class="wp-bar-wrap">
          <div class="wp-bar">
            <div class="wp-away" style="width:${{ap}}%"></div>
            <div class="wp-home" style="width:${{hp}}%"></div>
          </div>
          <div class="wp-labels">
            <span class="wp-lbl-a">${{ap}}% ${{esc(g.away_team)}}</span>
            <span class="wp-lbl-h">${{esc(g.home_team)}} ${{hp}}%</span>
          </div>
        </div>`;
      }}

      let totLines = [];
      const projT = pred?.projected_total ?? edge?.model_total;
      if (projT != null) totLines.push(`Proj: <strong>${{fmt(projT,1)}}</strong>`);
      if (edge?.book_total != null) totLines.push(`Line: <strong>${{fmt(edge.book_total,1)}}</strong>`);

      const echip = (edge && edge.confidence) ? edgeChip(edge.confidence, edge.edge_pct) : `
        <div style="font-size: 9px; font-family: monospace; white-space: nowrap;">
          <span style="color: var(--cyan)">K-BB Adv: ${{((edge?.kbb_adv || 0) * 100).toFixed(1)}}%</span> | 
          <span style="color: var(--purple)">Pythag: ${{((edge?.pythagorean_win_pct_adv || 0) * 100).toFixed(1)}}%</span>
        </div>`;
      let playHtml = '';
      if (edge?.play) {{
        const pl = edge.play.replace('_',' ').toUpperCase();
        playHtml = `<span class="mono" style="font-size:10px;color:var(--cyan)">${{pl}}</span>`;
      }}
      const mvHtml = movementHtml(edge?.movement_direction);

      const cls = isLive ? 'game-card is-live' : isFinal ? 'game-card is-final' : 'game-card';

      return `<div class="${{cls}}" onclick="openDeepDive(${{g.game_id}})">
        <div class="gc-header">
          <div class="gc-matchup">
            <span class="aw">${{esc(g.away_team)}}</span>
            <span class="at">@</span>
            <span class="hw">${{esc(g.home_team)}}</span>
          </div>
          ${{statusHtml}}
        </div>
        ${{scoreHtml}}
        <div class="gc-pitchers">${{esc(g.away_probable_pitcher||'TBD')}} vs ${{esc(g.home_probable_pitcher||'TBD')}}</div>
        ${{wpHtml}}
        <div class="gc-footer">
          <div class="gc-totals">
            ${{totLines.map(l => `<div>${{l}}</div>`).join('')}}
            ${{playHtml}}
          </div>
          <div class="gc-right">
            ${{echip}}
            ${{mvHtml}}
          </div>
        </div>
        ${{edge ? `
        <div style="margin-top:8px; padding-top:4px; border-top:1px solid var(--border); display:flex; justify-content:space-between; font-size:9px; font-family:monospace; color:var(--muted);">
          <span>KBB: ${{ (edge.kbb_adv*100).toFixed(1) }}%</span>
          <span>PYTH: ${{ (edge.pythagorean_win_pct_adv*100).toFixed(1) }}%</span>
          <span>PARK: ${{ (edge.park_factor_adv*100).toFixed(1) }}%</span>
        </div>
        ` : ''}}
      </div>`;
    }}).join('');

    $('game-grid').innerHTML = cards;

  }} catch(e) {{
    $('game-grid').innerHTML = `<div class="empty">Error: ${{esc(e.message)}}</div>`;
  }}
}}

function openDeepDive(gameId) {{
  const g = currentIntel.games.find(x => x.game_id == gameId);
  const e = currentIntel.edges[gameId];
  const p = currentIntel.preds[gameId];
  if (!g) return;

  const content = `
    <div class="modal-title">${{esc(g.away_team)}} @ ${{esc(g.home_team)}} — DEEP DIVE</div>
    <div class="dive-grid">
      <div class="dive-section">
        <div class="dive-label">Implied Probabilities (Book)</div>
        <div class="dive-val">
          <span class="cyan">AWAY: ${{e ? pct0_1(e.implied_away_pct) : '—'}}</span><br>
          <span class="purple">HOME: ${{e ? pct0_1(e.implied_home_pct) : '—'}}</span>
        </div>
      </div>
      <div class="dive-section">
        <div class="dive-label">Expected Value (EV)</div>
        <div class="dive-val">
          <span class="${{e?.ev_away > 0 ? 'green' : 'red'}}">ML AWAY: ${{e ? fmt(e.ev_away, 3) : '—'}}</span><br>
          <span class="${{e?.ev_home > 0 ? 'green' : 'red'}}">ML HOME: ${{e ? fmt(e.ev_home, 3) : '—'}}</span>
        </div>
      </div>
      <div class="dive-section">
        <div class="dive-label">Totals EV</div>
        <div class="dive-val">
          <span class="${{e?.ev_over > 0 ? 'green' : 'red'}}">OVER: ${{e ? fmt(e.ev_over, 3) : '—'}}</span><br>
          <span class="${{e?.ev_under > 0 ? 'green' : 'red'}}">UNDER: ${{e ? fmt(e.ev_under, 3) : '—'}}</span>
        </div>
      </div>
      <div class="dive-section">
        <div class="dive-label">Model Confidence Score</div>
        <div class="dive-val large cyan">${{p ? fmt(p.confidence_score, 2) : '—'}}</div>
      </div>
      <div class="dive-section">
        <div class="dive-label">${{esc(g.away_team)}} Starter xERA</div>
        <div class="dive-val yellow">${{p ? fmt(p.away_starter_xera, 2) : '—'}}</div>
      </div>
      <div class="dive-section">
        <div class="dive-label">${{esc(g.home_team)}} Starter xERA</div>
        <div class="dive-val yellow">${{p ? fmt(p.home_starter_xera, 2) : '—'}}</div>
      </div>
    </div>
    <div style="margin-top: 24px; font-family: monospace; font-size: 11px; color: var(--muted);">
      * All EV values represent unit return per unit wagered. Positive EV indicates a theoretical edge.
    </div>
  `;
  
  $('modal-body-content').innerHTML = content;
  $('deep-dive-modal').style.display = 'flex';
}}

function closeModal(e) {{
  $('deep-dive-modal').style.display = 'none';
}}

async function loadAll() {{
  await Promise.all([loadAccuracy(), loadDebrief(), loadCommentary(), loadIntel()]);
}}

let countdown = 60;
function tick() {{
  countdown--;
  $('cdown').textContent = countdown;
  if (countdown <= 0) {{ loadAll(); countdown = 60; }}
}}

document.addEventListener('DOMContentLoaded', () => {{
  $('hdr-date').textContent = new Date().toLocaleDateString('en-US', {{
    weekday:'short', month:'short', day:'numeric', year:'numeric',
  }}).toUpperCase();
  loadAll();
  setInterval(tick, 1000);
}});
</script>
</body>
</html>
"""

SYSTEM_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SYSTEM STATUS | MLB AI</title>
<style>{SHARED_STYLE}</style>
</head>
<body>

{get_header("system")}

<div class="container">

  <!-- ═══════════════ SYSTEM STATUS ═══════════════ -->
  <div class="section">
    <div class="section-head">
      <div class="section-label">System Status</div>
      <div class="section-rule"></div>
      <div class="section-badge" id="status-badge">—</div>
    </div>
    <div class="status-grid">
      <div class="term">
        <div class="term-bar">
          <span class="tb-dot tb-red"></span>
          <span class="tb-dot tb-yellow"></span>
          <span class="tb-dot tb-green"></span>
          <span class="term-title">SCHEDULER — AMERICA/NEW_YORK</span>
        </div>
        <div class="term-body" id="term-body">
          <div><span class="t-pending">Connecting…</span></div>
        </div>
      </div>
      <div class="model-panel">
        <div class="mp-header">◉ Model Intelligence</div>
        <div id="model-body"><div class="loading">Loading</div></div>
      </div>
    </div>
  </div>

  <!-- ═══════════════ v0.4 SANDBOX ═══════════════ -->
  <div class="section" style="border-left: 3px solid var(--purple);">
    <div class="section-head">
      <div class="section-label" style="color: var(--purple);">🧪 v0.4 SANDBOX — Paper Trading</div>
      <div class="section-rule" style="background: linear-gradient(90deg, var(--purple), transparent)"></div>
      <div class="section-badge" id="sandbox-badge">—</div>
    </div>

    <!-- Stat tiles -->
    <div class="summary-bar" id="sandbox-stats" style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px;"></div>

    <!-- Chart: rolling 7-day win rate (canvas only rendered if data exists) -->
    <div class="card" style="margin-bottom:20px;">
      <div style="font-family:monospace;font-size:11px;color:var(--muted);text-transform:uppercase;margin-bottom:12px;">
        Rolling 7-Day Win Rate
      </div>
      <div id="sandbox-chart-wrap">
        <div class="empty" style="padding:24px 0;text-align:center;color:var(--muted);">No data yet</div>
      </div>
    </div>

    <!-- High Conviction plays -->
    <div class="card" style="margin-bottom:20px;">
      <div class="section-head" style="margin-bottom:12px;">
        <div class="section-label" style="color:var(--yellow);">⚡ High Conviction Plays</div>
        <div class="section-rule" style="background:linear-gradient(90deg,var(--yellow),transparent)"></div>
      </div>
      <div id="sandbox-conviction" class="tbl-wrap">
        <div class="loading">Loading…</div>
      </div>
    </div>

    <!-- Umpire table -->
    <div class="card">
      <div class="section-head" style="margin-bottom:12px;">
        <div class="section-label">Umpire Run Impact</div>
        <div class="section-rule"></div>
      </div>
      <div id="sandbox-umpires" class="tbl-wrap">
        <div class="loading">Loading…</div>
      </div>
    </div>
  </div>

</div><!-- /container -->

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
{SHARED_JS_HELPERS}

function nextRunHtml(iso) {{
  if (!iso) return '<span class="t-pending">no next run</span>';
  try {{
    const d = new Date(iso), now = new Date();
    const diffMin = Math.round((d - now) / 60000);
    const ts = d.toLocaleTimeString('en-US', {{hour:'numeric', minute:'2-digit', timeZoneName:'short'}});
    if (d < now)    return `<span class="t-pending">${{ts}}</span>`;
    if (diffMin < 60) return `<span class="t-soon">${{ts}} (${{diffMin}}m)</span>`;
    return `<span class="t-time">${{ts}}</span>`;
  }} catch {{ return `<span class="t-time">${{iso}}</span>`; }}
}}

async function loadStatus() {{
  try {{
    const res = await fetch('/api/status');
    const data = await res.json();
    const jobs    = data.jobs    || [];
    const model   = data.model   || {{}};
    const bt      = data.backtest;

    $('status-badge').textContent = `${{jobs.length}} JOBS`;

    const sorted = [...jobs].sort((a,b) => {{
      if (!a.next_run_time) return 1;
      if (!b.next_run_time) return -1;
      return new Date(a.next_run_time) - new Date(b.next_run_time);
    }});

    const lines = [
      `<div><span class="t-prompt">$</span> <span class="t-cmd">scheduler --list --tz=America/New_York</span></div>`,
      `<div><span class="t-dim">${{'─'.repeat(52)}}</span></div>`,
    ];

    if (!sorted.length) {{
      lines.push(`<div><span class="t-pending">no jobs registered</span></div>`);
    }} else {{
      sorted.forEach(j => {{
        const name = (j.name || j.id || '?').replace(/_/g, '-').padEnd(38);
        lines.push(`<div><span class="t-name">${{esc(name)}}</span>${{nextRunHtml(j.next_run_time)}}</div>`);
      }});
    }}

    lines.push(`<div><span class="t-dim">${{'─'.repeat(52)}}</span></div>`);
    lines.push(`<div><span class="t-prompt">$</span> <span class="t-ok">scheduler running ✓</span></div>`);

    $('term-body').innerHTML = lines.join('');

    const accCls = bt?.accuracy != null ? colorClass(bt.accuracy * 100, 60, 55) : 'muted';
    const cvCls  = bt?.cv_accuracy != null ? colorClass(bt.cv_accuracy * 100, 58, 53) : 'muted';
    const winCls = model.winner_accuracy_pct != null ? colorClass(model.winner_accuracy_pct, 55, 50) : 'muted';
    const betCls = model.bet_win_rate != null ? colorClass(model.bet_win_rate, 55, 48) : 'muted';

    $('model-body').innerHTML = `
      <div class="mv-badge">◈ ${{esc(model.version || 'unknown')}}</div>
      <div class="ms-grid">
        <div class="ms-tile">
          <div class="ms-val ${{accCls}}">${{bt?.accuracy != null ? pct0_1(bt.accuracy) : '—'}}</div>
          <div class="ms-lbl">Backtest Accuracy</div>
        </div>
        <div class="ms-tile">
          <div class="ms-val ${{cvCls}}">${{bt?.cv_accuracy != null ? pct0_1(bt.cv_accuracy) : '—'}}</div>
          <div class="ms-lbl">Cross-Val Accuracy</div>
        </div>
        <div class="ms-tile">
          <div class="ms-val">${{model.total_predictions ?? '—'}}</div>
          <div class="ms-lbl">Total Predictions</div>
        </div>
        <div class="ms-tile">
          <div class="ms-val ${{winCls}}">${{model.winner_accuracy_pct != null ? pct100(model.winner_accuracy_pct) : '—'}}</div>
          <div class="ms-lbl">Winner Accuracy %</div>
        </div>
        <div class="ms-tile">
          <div class="ms-val">${{model.bets_graded ?? '—'}}</div>
          <div class="ms-lbl">Bets Graded</div>
        </div>
        <div class="ms-tile">
          <div class="ms-val ${{betCls}}">${{model.bet_win_rate != null ? pct100(model.bet_win_rate) : '—'}}</div>
          <div class="ms-lbl">Bet Win Rate</div>
        </div>
      </div>
      ${{bt?.seasons ? `<div class="ms-footnote">Seasons: ${{esc(bt.seasons)}} · ${{ (bt.n_games || 0).toLocaleString() }} games trained</div>` : ''}}`;

  }} catch(e) {{
    $('term-body').innerHTML  = `<div class="t-err">Error: ${{esc(e.message)}}</div>`;
    $('model-body').innerHTML = `<div class="empty">Error loading model stats</div>`;
  }}
}}

async function loadSandbox() {{
  try {{
    const [perfRes, convRes, umpiresRes, todayRes] = await Promise.all([
      fetch('/api/sandbox/performance'),
      fetch('/api/sandbox/convergence/today'),
      fetch('/api/sandbox/umpires'),
      fetch('/api/sandbox/predictions/today'),
    ]);
    const perf    = await perfRes.json();
    const conv    = await convRes.json();
    const umpires = await umpiresRes.json();
    const today   = await todayRes.json();

    $('sandbox-badge').textContent = `${{today.length}} GAMES TODAY`;

    // ── Stat tiles ──
    const tiles = [
      {{ label:'F5 Record',          val: perf.f5_record       || '0W-0L',   color:'var(--cyan)' }},
      {{ label:'F5 Win Rate',         val: perf.f5_win_rate != null ? (perf.f5_win_rate*100).toFixed(1)+'%' : '—', color:'var(--green)' }},
      {{ label:'Full Game Record',    val: perf.full_game_record || '0W-0L', color:'var(--cyan)' }},
      {{ label:'Full Game Win Rate',  val: perf.full_game_win_rate != null ? (perf.full_game_win_rate*100).toFixed(1)+'%' : '—', color:'var(--green)' }},
      {{ label:'Convergence Win Rate',val: perf.convergence_win_rate != null ? (perf.convergence_win_rate*100).toFixed(1)+'%' : '—', color:'var(--purple)' }},
      {{ label:'v3/v4 Agreement Rate',val: perf.v3_agreement_win_rate != null ? (perf.v3_agreement_win_rate*100).toFixed(1)+'%' : '—', color:'var(--yellow)' }},
    ];
    $('sandbox-stats').innerHTML = tiles.map(t => `
      <div class="card" style="padding:16px;border-left:3px solid ${{t.color}}">
        <div style="font-size:11px;font-family:monospace;color:var(--muted);text-transform:uppercase;margin-bottom:6px;">${{esc(t.label)}}</div>
        <div style="font-size:22px;font-weight:700;color:${{t.color}};font-family:monospace;">${{esc(t.val)}}</div>
      </div>
    `).join('');

    // ── Rolling 7-day chart (only init if data exists) ──
    const weekly = perf.weekly_roi || [];
    const wrap = document.getElementById('sandbox-chart-wrap');
    if (weekly.length === 0) {{
      wrap.innerHTML = '<div class="empty" style="padding:24px 0;text-align:center;color:var(--muted);">No data yet</div>';
    }} else {{
      const labels = weekly.map(w => w.date);
      const f5Data  = weekly.map(w => w.f5_win_rate != null ? +(w.f5_win_rate*100).toFixed(1) : null);
      const fgData  = weekly.map(w => w.full_game_win_rate != null ? +(w.full_game_win_rate*100).toFixed(1) : null);
      wrap.innerHTML = '<canvas id="sandbox-chart" height="80"></canvas>';
      const ctx = document.getElementById('sandbox-chart').getContext('2d');
      if (window._sandboxChart) window._sandboxChart.destroy();
      window._sandboxChart = new Chart(ctx, {{
        type: 'line',
        data: {{
          labels,
          datasets: [
            {{ label:'F5 Win %', data:f5Data, borderColor:'#06b6d4', tension:0.3, pointRadius:4, fill:false }},
            {{ label:'Full Game Win %', data:fgData, borderColor:'#a855f7', tension:0.3, pointRadius:4, fill:false }},
          ],
        }},
        options: {{
          responsive:true, maintainAspectRatio:false,
          plugins:{{ legend:{{ labels:{{ color:'#e2e8f0', font:{{ family:'monospace' }} }} }} }},
          scales:{{
            x:{{ ticks:{{ color:'#5a7a9c' }}, grid:{{ color:'#1e3050' }} }},
            y:{{ ticks:{{ color:'#5a7a9c', callback: v => v+'%' }}, grid:{{ color:'#1e3050' }}, min:0, max:100 }},
          }},
        }},
      }});
    }}

    // ── High Conviction plays ──
    if (!conv.length) {{
      $('sandbox-conviction').innerHTML = '<div class="empty">No high conviction plays today</div>';
    }} else {{
      $('sandbox-conviction').innerHTML = `
        <table>
          <thead><tr>
            <th>Matchup</th><th>F5 Pick</th><th>F5 Proj</th>
            <th>v4 Total</th><th>Umpire</th><th>Conviction</th>
          </tr></thead>
          <tbody>${{conv.map(r => `
            <tr>
              <td class="mono">${{esc(r.away_team)}} @ ${{esc(r.home_team)}}</td>
              <td class="mono" style="color:var(--cyan)">${{esc(r.f5_pick||'—')}} ${{r.f5_line||''}}</td>
              <td class="mono">${{r.f5_projected_total?.toFixed(1)||'—'}}</td>
              <td class="mono">${{r.full_game_projected_total?.toFixed(1)||'—'}}</td>
              <td class="mono">${{esc(r.umpire_name||'—')}}</td>
              <td><span style="color:var(--yellow);font-weight:700;">⚡ HIGH CONVICTION</span></td>
            </tr>
          `).join('')}}</tbody>
        </table>
      `;
    }}

    // ── Umpire table ──
    const topUmpires = umpires.filter(u => u.game_id !== 0).slice(0, 10);
    if (!topUmpires.length) {{
      $('sandbox-umpires').innerHTML = '<div class="empty">No umpire assignments yet today</div>';
    }} else {{
      $('sandbox-umpires').innerHTML = `
        <table>
          <thead><tr><th>Umpire</th><th>Run Impact</th><th>K Rate Delta</th><th>Season</th></tr></thead>
          <tbody>${{topUmpires.map(u => {{
            const imp = u.run_expectancy_impact || 0;
            const cls = imp > 0.1 ? 'red' : imp < -0.1 ? 'green' : '';
            return `<tr>
              <td class="mono">${{esc(u.umpire_name)}}</td>
              <td class="mono ${{cls}}" style="font-weight:700;">${{imp >= 0 ? '+' : ''}}${{imp.toFixed(2)}}</td>
              <td class="mono">${{u.historical_k_rate_delta >= 0 ? '+' : ''}}${{(u.historical_k_rate_delta||0).toFixed(3)}}</td>
              <td class="mono">${{u.season||'—'}}</td>
            </tr>`;
          }}).join('')}}</tbody>
        </table>
      `;
    }}
  }} catch(e) {{
    $('sandbox-badge').textContent = 'ERROR';
    console.error('[sandbox]', e);
  }}
}}

async function loadAll() {{
  await loadStatus();
  // Sandbox makes 4 separate API calls — defer it so the critical status
  // section renders first and the browser doesn't block on parallel fetches.
  setTimeout(loadSandbox, 500);
}}

let countdown = 60;
function tick() {{
  countdown--;
  $('cdown').textContent = countdown;
  if (countdown <= 0) {{ loadAll(); countdown = 60; }}
}}

document.addEventListener('DOMContentLoaded', () => {{
  $('hdr-date').textContent = new Date().toLocaleDateString('en-US', {{
    weekday:'short', month:'short', day:'numeric', year:'numeric',
  }}).toUpperCase();
  loadAll();
  setInterval(tick, 1000);
}});
</script>
</body>
</html>
"""

SIMULATOR_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BANKROLL SIMULATOR | MLB AI</title>
<style>{SHARED_STYLE}</style>
</head>
<body>

{get_header("simulator")}

<div class="container">

  <!-- ═══════════════ BANKROLL SIMULATOR ═══════════════ -->
  <div class="section">
    <div class="section-head">
      <div class="section-label">Bankroll Simulator</div>
      <div class="section-rule"></div>
      <div class="section-badge">Historical Backtest</div>
    </div>
    
    <div class="summary-bar" style="border-left-color: var(--orange); background: var(--surface2); padding: 24px;">
      <div style="display: flex; gap: 32px; align-items: flex-end; flex-wrap: wrap;">
        <div style="display: flex; flex-direction: column; gap: 8px;">
          <label style="font-family: monospace; font-size: 11px; color: var(--muted); text-transform: uppercase;">Starting Bankroll ($)</label>
          <input type="number" id="bankroll-input" value="10000" style="background: var(--bg); border: 1px solid var(--border); color: var(--cyan); padding: 8px 12px; border-radius: 4px; font-family: monospace; font-size: 16px; width: 150px;">
        </div>
        <div style="display: flex; flex-direction: column; gap: 8px;">
          <label style="font-family: monospace; font-size: 11px; color: var(--muted); text-transform: uppercase;">Base Unit Size ($)</label>
          <input type="number" id="unit-input" value="100" style="background: var(--bg); border: 1px solid var(--border); color: var(--cyan); padding: 8px 12px; border-radius: 4px; font-family: monospace; font-size: 16px; width: 150px;">
        </div>
        <button class="refresh-btn" style="height: 40px; padding: 0 24px;" onclick="runSimulation()">⚡ CALCULATE PROFIT</button>
      </div>
    </div>

    <div id="simulator-results">
        <div class="loading">Enter values and click calculate to see historical performance.</div>
    </div>

  </div>

</div>

<script>
{SHARED_JS_HELPERS}

async function runSimulation() {{
    const bankroll = parseFloat($('bankroll-input').value) || 0;
    const unit = parseFloat($('unit-input').value) || 0;

    try {{
        const res = await fetch('/api/reviews/accuracy');
        const data = await res.json();
        
        const renderSimulatorTable = (label, marketData, color) => {{
            if (!marketData || !marketData.confidence_bins) return '';
            const bins = marketData.confidence_bins;
            const binNames = ["50-59%", "60-69%", "70-79%", "80%+"];

            let totalMarketProfit = 0;
            let totalMarketBets = 0;

            const rowsHtml = binNames.map(name => {{
                const stats = bins[name];
                if (!stats || stats.bets === 0) return '';
                
                const wins = stats.wins;
                const losses = stats.losses;
                const avgOdds = stats.avg_odds;
                
                const profitPerWin = unit * (avgOdds - 1);
                const profit = (wins * profitPerWin) - (losses * unit);
                totalMarketProfit += profit;
                totalMarketBets += stats.bets;

                return `
                    <tr>
                        <td class="mono" style="font-weight: 700; color: var(--text)">${{name}}</td>
                        <td class="mono">${{stats.bets}}</td>
                        <td class="mono">${{wins}}W - ${{losses}}L</td>
                        <td class="mono">${{avgOdds.toFixed(1)}}</td>
                        <td class="mono ${{profit >= 0 ? 'green' : 'red'}}" style="font-weight: 700;">
                            $${{profit.toFixed(2)}}
                        </td>
                    </tr>
                `;
            }}).filter(row => row !== '').join('');

            if (totalMarketBets === 0) return '';

            const roi = (totalMarketProfit / (totalMarketBets * unit)) * 100;

            return `
                <div class="card" style="margin-bottom: 24px; border-left: 4px solid ${{color}};">
                    <div class="section-head">
                        <div class="section-label" style="color: ${{color}}">${{label}} Simulator</div>
                        <div class="section-rule" style="background: linear-gradient(90deg, ${{color}}, transparent)"></div>
                        <div class="section-badge" style="color: ${{totalMarketProfit >= 0 ? 'var(--green)' : 'var(--red)'}}">
                            Profit: $${{totalMarketProfit.toFixed(2)}} | ROI: ${{roi.toFixed(2)}}%
                        </div>
                    </div>
                    <div class="tbl-wrap">
                        <table>
                            <thead>
                                <tr>
                                    <th>Confidence Bin</th>
                                    <th>Total Bets</th>
                                    <th>Record (W-L)</th>
                                    <th>Avg Odds</th>
                                    <th>Net Profit</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${{rowsHtml}}
                            </tbody>
                        </table>
                    </div>
                </div>
            `;
        }};

        $('simulator-results').innerHTML = `
            ${{renderSimulatorTable('Moneyline', data.moneyline, 'var(--cyan)')}}
            ${{renderSimulatorTable('Totals', data.totals, 'var(--purple)')}}
            ${{renderSimulatorTable('Run Line', data.run_line, 'var(--orange)')}}
            <div style="margin-top: 32px; font-family: monospace; font-size: 12px; color: var(--muted); border-top: 1px solid var(--border); padding-top: 16px;">
                * Backtest results based on fixed unit size bet on every game in the bucket.
                * Total profit is calculated using historical average odds for each confidence bin.
            </div>
        `;
    }} catch(e) {{
        $('simulator-results').innerHTML = `<div class="empty">Error: ${{esc(e.message)}}</div>`;
    }}
}}

function loadAll() {{
    // Required by shared header
}}

document.addEventListener('DOMContentLoaded', () => {{
  $('hdr-date').textContent = new Date().toLocaleDateString('en-US', {{
    weekday:'short', month:'short', day:'numeric', year:'numeric',
  }}).toUpperCase();
  
  $('cdown').parentElement.style.display = 'none';
}});
</script>
</body>
</html>
"""


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)

@router.get("/system", response_class=HTMLResponse)
def system_dashboard():
    return HTMLResponse(content=SYSTEM_HTML)

@router.get("/simulator", response_class=HTMLResponse)
def simulator():
    return HTMLResponse(content=SIMULATOR_HTML)
