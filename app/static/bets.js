const $=id=>document.getElementById(id);
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const fmt=(n,d=1)=>n==null?'—':(+n).toFixed(d);
function gameTime(iso){if(!iso)return '—'; try{const d=new Date(iso); if(isNaN(d)) return iso; return d.toLocaleTimeString('en-US',{hour:'numeric',minute:'2-digit',timeZoneName:'short'});}catch{return iso;}}
function edgeChip(confidence, edgePct){const pctStr=edgePct!=null?` ${(edgePct*100).toFixed(1)}%`:''; if(!confidence||confidence==='weak') return `<span class="chip c-weak">WEAK${pctStr}</span>`; const cls=confidence==='strong'?'c-strong':'c-medium'; return `<span class="chip ${cls}">${confidence.toUpperCase()}${pctStr}</span>`;}

async function loadRecords(){
  const [reviewRes, paperRes] = await Promise.all([fetch('/api/reviews/summary'), fetch('/api/bets/summary')]);
  const review = await reviewRes.json();
  const paper = await paperRes.json();
  $('record-badge').textContent = `${review.wins||0}W-${review.losses||0}L-${review.pushes||0}P`;
  $('record-summary').innerHTML = `
    <div class="summary-bar">
      <div class="s-stat"><div class="s-stat-val">${review.total_predictions ?? 0}</div><div class="s-stat-lbl">Flagged Bets Reviewed</div></div>
      <div class="s-stat"><div class="s-stat-val green">${review.wins ?? 0}</div><div class="s-stat-lbl">Wins</div></div>
      <div class="s-stat"><div class="s-stat-val red">${review.losses ?? 0}</div><div class="s-stat-lbl">Losses</div></div>
      <div class="s-stat"><div class="s-stat-val">${review.win_rate != null ? (review.win_rate*100).toFixed(1)+'%' : '—'}</div><div class="s-stat-lbl">Win Rate</div></div>
      <div class="s-stat"><div class="s-stat-val">${review.roi_flat_110 != null ? (review.roi_flat_110*100).toFixed(1)+'%' : '—'}</div><div class="s-stat-lbl">ROI (-110 flat)</div></div>
    </div>`;
  $('paper-badge').textContent = `${paper.wins||0}W-${paper.losses||0}L-${paper.pushes||0}P`;
  $('paper-summary').innerHTML = `
    <div class="summary-bar" style="border-left-color:var(--purple);">
      <div class="s-stat"><div class="s-stat-val">${paper.bankroll != null ? '$'+paper.bankroll.toFixed(2) : '—'}</div><div class="s-stat-lbl">Paper Bankroll</div></div>
      <div class="s-stat"><div class="s-stat-val">${paper.pl_today != null ? '$'+paper.pl_today.toFixed(2) : '—'}</div><div class="s-stat-lbl">P/L Today</div></div>
      <div class="s-stat"><div class="s-stat-val">${paper.pl_all_time != null ? '$'+paper.pl_all_time.toFixed(2) : '—'}</div><div class="s-stat-lbl">P/L All Time</div></div>
      <div class="s-stat"><div class="s-stat-val">${paper.win_rate != null ? (paper.win_rate*100).toFixed(1)+'%' : '—'}</div><div class="s-stat-lbl">Paper Win Rate</div></div>
      <div class="s-stat"><div class="s-stat-val">${paper.open_bets ?? 0}</div><div class="s-stat-lbl">Open Bets</div></div>
    </div>`;
}

async function loadBets(){
  const res = await fetch('/api/ranked/bets?limit=10&active_only=true');
  const rows = await res.json();
  $('bets-badge').textContent = rows.length ? `${rows.length} LIVE RANKS` : 'NO LIVE BETS';
  if(!rows.length){$('bets-table').innerHTML = '<div class="empty">No ranked bets for today.</div>'; return;}
  $('bets-table').innerHTML = `<table><thead><tr><th>Rank</th><th>Matchup</th><th>Play</th><th>Edge</th><th>EV</th><th>Confidence</th><th>Book</th><th>Start</th></tr></thead><tbody>${
    rows.map(r=>`<tr><td class="mono green">#${r.rank}</td><td style="font-weight:600">${esc(r.matchup)}</td><td class="mono">${esc((r.play||'—').replace('_',' ').toUpperCase())}</td><td class="mono green">${(r.edge_pct*100).toFixed(1)}%</td><td class="mono ${r.ev >= 0 ? 'green' : 'red'}">${fmt(r.ev,3)}</td><td>${edgeChip(r.confidence,r.edge_pct)}</td><td class="mono">${esc(r.sportsbook||'—')}</td><td class="mono">${gameTime(r.start_time)}</td></tr>`).join('')
  }</tbody></table>`;
}

document.addEventListener('DOMContentLoaded', async () => {
  $('hdr-date').textContent = new Date().toLocaleDateString('en-US',{weekday:'short',month:'short',day:'numeric',year:'numeric'}).toUpperCase();
  await Promise.all([loadRecords(), loadBets()]);
});
