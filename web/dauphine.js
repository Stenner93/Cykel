/* Critérium du Dauphiné 2026 — Dashboard */

const PREDS_URL = './data/dauphine2026_predictions.json';

const STAGE_ICONS = {
  sprint:   '⚡',
  mountain: '⛰️',
  tt:       '⏱️',
  hilly:    '〰️',
  cobbled:  '🧱',
};

const STAGE_TYPE_NAMES = {
  sprint:   'Sprinteretape',
  mountain: 'Bjergetape',
  tt:       'Enkeltstart',
  hilly:    'Kuperet',
  cobbled:  'Brosten',
};

const DISC_LABELS = {
  SPR: 'Sprint', MTN: 'Bjerg', ITT: 'Enkeltstart',
  HLL: 'Bakket', COB: 'Brosten', GC: 'GC', AVG: 'Disciplin',
};

// ── State ──────────────────────────────────────────────────────────────────────
let PREDS       = null;
let activeTab   = 'hold';
let predStage   = null;   // currently selected stage number in preds tab

// ── Utilities ──────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function fmt(n)   { return n == null ? '–' : Number(n).toLocaleString('da-DK'); }
function fmtK(n)  { if (!n) return ''; return (Math.abs(n)>=1e6 ? (n/1e6).toFixed(1)+'M' : Math.round(n/1000)+'k'); }

function miniBar(signals) {
  const labels = ['VeloScore','Odds','Disciplin','Form'];
  return `<div class="mini-signals">${signals.map((v,i)=>
    `<div class="mini-seg ${v>0.3?'on':''}" title="${labels[i]}: ${(v*100).toFixed(0)}%"></div>`
  ).join('')}</div>`;
}

function actClass(actual, exp) {
  if (!actual || !exp) return 'pts-ok';
  if (actual >= exp)        return 'pts-beat';
  if (actual >= exp * 0.5)  return 'pts-ok';
  return 'pts-miss';
}

/** Find the stage to show by default in Hold tab:
 *  - If any stage has no actuals (upcoming) → show first upcoming
 *  - If all finished → show last stage */
function currentStage() {
  if (!PREDS?.stages) return null;
  const upcoming = PREDS.stages.find(s =>
    s.status !== 'finished' && !s.riders.some(r => r.actual != null)
  );
  return upcoming || PREDS.stages[PREDS.stages.length - 1];
}

// ── Data loading ───────────────────────────────────────────────────────────────
async function loadJSON(url) {
  const r = await fetch(url + '?t=' + Date.now());
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${url}`);
  return r.json();
}

// ── Init ───────────────────────────────────────────────────────────────────────
async function init() {
  try {
    PREDS = await loadJSON(PREDS_URL);
  } catch(e) {
    document.getElementById('holdContent').innerHTML =
      `<div class="info-box"><div class="icon">⚠️</div>
       <div style="color:#E53935;margin-bottom:8px">${esc(e.message)}</div>
       <div style="color:var(--muted);font-size:0.8rem">Kør: <code>python build_dauphine_web_data.py</code></div></div>`;
    document.getElementById('predContent').innerHTML = document.getElementById('holdContent').innerHTML;
    setupTabs();
    return;
  }

  const cur = currentStage();
  predStage = cur?.num ?? PREDS.stages[0]?.num;

  renderHoldTab(cur);
  buildPills();
  renderPredStage(predStage);
  setupTabs();
}

// ── Tab: Hold & Picks ──────────────────────────────────────────────────────────
function renderHoldTab(stage) {
  if (!stage) {
    document.getElementById('holdContent').innerHTML =
      `<div class="info-box"><div class="icon">🏁</div><div>Alle etaper afsluttede</div></div>`;
    return;
  }

  const icon   = STAGE_ICONS[stage.type] || '🚴';
  const tname  = STAGE_TYPE_NAMES[stage.type] || stage.type;
  const status = stage.status === 'finished' ? 'Afsluttet' :
                 stage.status === 'live'     ? 'Live ▶' : 'Kommende';
  const isUpcoming = stage.status !== 'finished';

  // Sort riders by expected points
  const sorted = [...stage.riders].sort((a,b) => b.exp - a.exp);
  const top25  = sorted.slice(0, 25);

  // Best team riders
  const bestIds = new Set(stage.best_team || []);

  // Team card
  const teamRiders = sorted.filter(r => bestIds.has(r.id));

  let html = `
    <div class="stage-info-bar">
      <div class="stage-type-badge">${icon}</div>
      <div>
        <div class="stage-title">Etape ${stage.num} — ${esc(stage.name || tname)}</div>
        <div class="stage-subtitle">${tname}${stage.profile_score ? ` · PCS score: ${stage.profile_score}` : ''}</div>
      </div>
      <div class="stage-status-chip ${isUpcoming ? 'upcoming' : ''}">${status}</div>
    </div>`;

  if (teamRiders.length) {
    html += `
    <div class="team-card">
      <div class="team-card-title">🏆 Optimalt hold (ubegrænset budget) — ${teamRiders.length} ryttere</div>
      <div class="team-grid">
        ${teamRiders.map(r => `
          <div class="rider-chip${r.is_cap ? ' is-cap' : ''}">
            <div class="rider-chip-name">${esc(r.name)}</div>
            <div class="rider-chip-meta">
              <span>${esc(r.team)}</span>
              <span>${r.price?.toFixed?.(1) ?? '?'}M</span>
              <span style="color:var(--green)">${fmtK(r.exp)}</span>
            </div>
          </div>
        `).join('')}
      </div>
    </div>`;
  }

  // Top picks table
  html += `
    <h2 style="margin-bottom:12px">Top 25 ryttere — forventet score</h2>
    <div class="table-wrapper">
    <table class="pred-table">
      <thead><tr>
        <th>#</th>
        <th>Rytter</th>
        <th>Hold</th>
        <th>Pris</th>
        <th>Forv. point</th>
        <th>Signal</th>
        <th>Disciplin</th>
        <th>Begrundelse</th>
      </tr></thead>
      <tbody>
        ${top25.map((r, i) => {
          const dk = (r.disc_key || 'AVG').toUpperCase();
          const dLabel = DISC_LABELS[dk] || dk;
          const rankCls = i < 3 ? 'rank-top' : 'rank-num';
          const rowCls  = [r.in_opt ? 'in-opt' : '', r.is_cap ? 'is-cap' : ''].filter(Boolean).join(' ');
          return `<tr class="${rowCls}">
            <td class="${rankCls}">${i+1}</td>
            <td class="col-name-p">${esc(r.name)}</td>
            <td style="color:var(--muted);font-size:0.75rem">${esc(r.team)}</td>
            <td style="font-size:0.78rem">${r.price?.toFixed?.(1) ?? '?'}M</td>
            <td class="pts-exp">${fmt(r.exp)}</td>
            <td>${miniBar(r.signals || [0,0,0,0])}</td>
            <td style="font-size:0.75rem;color:var(--muted)">${dLabel}: ${r.disc?.toFixed?.(0) ?? '?'}/100</td>
            <td style="font-size:0.75rem;color:var(--muted);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                title="${esc(r.reason)}">${esc(r.reason)}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>
    </div>`;

  const genTime = PREDS.generated
    ? new Date(PREDS.generated).toLocaleString('da-DK', {dateStyle:'short',timeStyle:'short'})
    : '';
  if (genTime) {
    html += `<p style="font-size:0.72rem;color:var(--muted);margin-top:12px;text-align:right">
      Beregnet: ${genTime}</p>`;
  }

  document.getElementById('holdContent').innerHTML = html;
}

// ── Tab: Forudsigelser ─────────────────────────────────────────────────────────
function buildPills() {
  if (!PREDS?.stages) return;
  const container = document.getElementById('stagePills');
  container.innerHTML = PREDS.stages.map(s => {
    const icon     = STAGE_ICONS[s.type] || '🚴';
    const finished = s.status === 'finished' || s.riders.some(r => r.actual != null);
    const cls      = [
      'stage-pill',
      s.num === predStage ? 'active' : '',
      finished ? 'finished' : '',
    ].filter(Boolean).join(' ');
    return `<button class="${cls}" data-stage="${s.num}"
      onclick="selectStage(${s.num})">
      <span class="pill-icon">${icon}</span> E${s.num}
    </button>`;
  }).join('');
}

function selectStage(num) {
  predStage = num;
  buildPills();
  renderPredStage(num);
}

function renderPredStage(num) {
  const stage = PREDS?.stages?.find(s => s.num === num);
  if (!stage) {
    document.getElementById('predContent').innerHTML =
      `<div class="info-box"><div class="icon">❓</div><div>Ingen data for etape ${num}</div></div>`;
    return;
  }

  const icon   = STAGE_ICONS[stage.type] || '🚴';
  const tname  = STAGE_TYPE_NAMES[stage.type] || stage.type;
  const sorted = [...stage.riders].sort((a,b) => b.exp - a.exp);
  const hasAny = sorted.some(r => r.actual != null);
  const bestIds = new Set(stage.best_team || []);

  let html = `
    <div style="margin-bottom:16px;display:flex;align-items:center;gap:12px">
      <span style="font-size:1.5rem">${icon}</span>
      <div>
        <strong>Etape ${stage.num} — ${esc(stage.name || tname)}</strong>
        <span style="color:var(--muted);font-size:0.8rem;margin-left:8px">${tname}</span>
      </div>
      ${stage.profile_score ? `<span style="font-size:0.75rem;color:var(--muted);margin-left:auto">PCS score: ${stage.profile_score}</span>` : ''}
    </div>
    <div class="table-wrapper">
    <table class="pred-table">
      <thead><tr>
        <th>#</th>
        <th>Rytter</th>
        <th>Hold</th>
        <th>Pris</th>
        <th>Forv. point</th>
        ${hasAny ? '<th>Faktisk</th><th>Diff</th>' : ''}
        <th>Signal</th>
        <th>Begrundelse</th>
      </tr></thead>
      <tbody>`;

  sorted.forEach((r, i) => {
    if (i >= 30 && !r.in_opt && !r.is_cap) return;  // show top 30 + optimal picks
    const rowCls  = [r.in_opt ? 'in-opt' : '', r.is_cap ? 'is-cap' : ''].filter(Boolean).join(' ');
    const rankCls = i < 3 ? 'rank-top' : 'rank-num';

    let actHtml = '';
    if (hasAny) {
      if (r.actual != null) {
        const cls  = actClass(r.actual, r.exp);
        const diff = r.actual - r.exp;
        const sign = diff >= 0 ? '+' : '';
        actHtml = `<td class="pts-act ${cls}">${fmt(r.actual)}</td>
                   <td class="${cls}" style="font-size:0.75rem">${sign}${fmtK(diff)}</td>`;
      } else {
        actHtml = `<td style="color:var(--muted)">–</td><td>–</td>`;
      }
    }

    html += `<tr class="${rowCls}">
      <td class="${rankCls}">${i+1}</td>
      <td class="col-name-p">${esc(r.name)}</td>
      <td style="color:var(--muted);font-size:0.75rem">${esc(r.team)}</td>
      <td style="font-size:0.78rem">${r.price?.toFixed?.(1) ?? '?'}M</td>
      <td class="pts-exp">${fmt(r.exp)}</td>
      ${actHtml}
      <td>${miniBar(r.signals || [0,0,0,0])}</td>
      <td style="font-size:0.75rem;color:var(--muted);max-width:200px;overflow:hidden;
          text-overflow:ellipsis;white-space:nowrap" title="${esc(r.reason)}">${esc(r.reason)}</td>
    </tr>`;
  });

  html += `</tbody></table></div>`;

  if (hasAny) {
    // Model accuracy summary for this stage
    const withBoth = sorted.filter(r => r.actual != null && r.exp > 0);
    if (withBoth.length >= 5) {
      const mae = withBoth.reduce((s, r) => s + Math.abs(r.actual - r.exp), 0) / withBoth.length;
      const top10pred  = sorted.slice(0, 10).map(r => r.id);
      const top10act   = [...sorted].sort((a,b) => (b.actual ?? -Infinity) - (a.actual ?? -Infinity)).slice(0,10).map(r => r.id);
      const overlap    = top10pred.filter(id => top10act.includes(id)).length;
      html += `
        <div style="margin-top:16px;padding:12px 16px;background:var(--card);border:1px solid var(--border);
            border-radius:8px;font-size:0.8rem;display:flex;gap:24px;flex-wrap:wrap">
          <div><span style="color:var(--muted)">Model MAE (${withBoth.length} ryttere):</span>
               <strong style="margin-left:6px">${fmtK(Math.round(mae))}</strong></div>
          <div><span style="color:var(--muted)">Top-10 overlap:</span>
               <strong style="margin-left:6px">${overlap}/10</strong></div>
        </div>`;
    }
  }

  document.getElementById('predContent').innerHTML = html;
}

// ── Tab switching ──────────────────────────────────────────────────────────────
function setupTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      activeTab = tab;
      document.querySelectorAll('.tab-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.tab === tab));
      document.getElementById('tab-hold').style.display  = tab === 'hold'  ? '' : 'none';
      document.getElementById('tab-preds').style.display = tab === 'preds' ? '' : 'none';
    });
  });
}

// ── Start ──────────────────────────────────────────────────────────────────────
init();
