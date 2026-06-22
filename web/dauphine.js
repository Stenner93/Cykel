/* Critérium du Dauphiné 2026 — Dashboard */

const SCORES_URL = './data/dauphine2026_scores.json';
const PREDS_URL  = './data/dauphine2026_predictions.json';

const STAGE_ICONS = {
  sprint:   '⚡',
  mountain: '⛰️',
  tt:       '⏱️',
  ttt:      '👥',
  hilly:    '〰️',
  cobbled:  '🧱',
};

const STAGE_TYPE_NAMES = {
  sprint:   'Sprinteretape',
  mountain: 'Bjergetape',
  tt:       'Enkeltstart',
  ttt:      'Holdtidskørsel',
  hilly:    'Kuperet',
  cobbled:  'Brosten',
};

const DISC_LABELS = {
  SPR: 'Sprint', MTN: 'Bjerg', ITT: 'Enkeltstart',
  HLL: 'Bakket', COB: 'Brosten', GC: 'GC', AVG: 'Disciplin',
};

// ── State ──────────────────────────────────────────────────────────────────────
let SCORES      = null;
let PREDS       = null;
let activeTab   = 'scores';
let predStage   = null;   // currently selected stage number in preds tab

// Matrix state
let sortBy      = 'total';   // 'total' | 'name' | 'price' | stage number
let sortDir     = -1;        // -1 = desc, 1 = asc
let filterText  = '';

// ── Utilities ──────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function fmt(n)   { return n == null ? '–' : Number(n).toLocaleString('da-DK'); }
function fmtK(n)  { if (!n) return ''; return (Math.abs(n)>=1e6 ? (n/1e6).toFixed(1)+'M' : Math.round(n/1000)+'k'); }

/** Render a context status badge for a rider */
function ctxBadge(status, note, mult) {
  if (!status || status === 'normal') return '';
  const CTX_LABELS = {
    fresh:     'Frisk',
    fatigued:  'Traet',
    defending: 'GC',
    sick:      'Syg',
    dns:       'DNS',
  };
  const label = CTX_LABELS[status] || status;
  const multStr = mult != null && mult !== 1.0 ? ` x${mult}` : '';
  return `<span class="ctx-badge ctx-${esc(status)}" title="${esc(note || status)}">${label}${multStr}</span>`;
}

function miniBar(signals, discRaw, discCo, discKey) {
  const dk = (discKey || 'DISC').toUpperCase();
  const discTitle = discRaw != null
    ? `${dk}: ${discRaw.toFixed(0)}/100 i felt` + (discCo ? ` (CO: ${discCo.toFixed(0)})` : '')
    : 'Disciplin';
  const labels = ['VeloScore','Odds', discTitle,'Form','ML','PCS Rang'];
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

function fmtFull(n) {
  if (n == null) return '–';
  return n.toLocaleString('da-DK');
}

/** Heatmap colour for a matrix cell */
function cellBg(pts) {
  if (!pts) return '';
  if (pts < 0) {
    const t = Math.min(1, Math.abs(pts) / 90_000);
    return `background: hsl(0, ${60 + t * 30}%, ${15 + t * 10}%)`;
  }
  const t   = Math.min(1, pts / 600_000);
  const hue = Math.round(220 - t * 220);
  const sat = Math.round(55 + t * 35);
  const lit = Math.round(16 + t * 22);
  return `background: hsl(${hue}, ${sat}%, ${lit}%)`;
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
  // Load both files in parallel; tolerate individual failures gracefully
  [SCORES, PREDS] = await Promise.all([
    loadJSON(SCORES_URL).catch(e => { console.warn('scores:', e); return null; }),
    loadJSON(PREDS_URL).catch(e  => { console.warn('preds:',  e); return null; }),
  ]);

  // Scores tab
  if (SCORES) {
    renderScoresTab();
  } else {
    document.getElementById('matrixWrap').innerHTML =
      `<div class="info-box"><div class="icon">⚠️</div>
       <div>Pointmatrix-data ikke fundet</div>
       <div style="color:var(--muted);font-size:0.8rem;margin-top:8px">Kør: <code>python build_dauphine_web_data.py</code></div></div>`;
  }

  // Hold & Predictions tabs
  if (PREDS) {
    const cur = currentStage();
    predStage = cur?.num ?? PREDS.stages[0]?.num;
    renderHoldTab(cur);
    buildPills();
    renderPredStage(predStage);
  } else {
    const msg = `<div class="info-box"><div class="icon">⚠️</div><div>Forudsigelsesdata ikke fundet</div></div>`;
    document.getElementById('holdContent').innerHTML = msg;
    document.getElementById('predContent').innerHTML = msg;
  }

  setupTabs();
  setupControls();
  setupModal();
}

// ── Tab: Pointmatrix ──────────────────────────────────────────────────────────
function renderScoresTab() {
  const note = document.getElementById('sourceNote');
  if (note && SCORES) {
    note.textContent = SCORES.summary_source === 'scoring-summary'
      ? 'Point: officielle Holdet-totaler'
      : 'Point: beregnet fra regler (approx.)';
  }

  // Default sort: latest finished stage descending
  if (SCORES?.stages) {
    const finished = SCORES.stages.filter(s => s.status === 'finished');
    if (finished.length) {
      const latestStage = finished[finished.length - 1].num;
      sortBy  = latestStage;
      sortDir = -1;
      // Sync the dropdown back to "total" display since we're sorting by a stage
      const sel = document.getElementById('sortSelect');
      if (sel) sel.value = 'total';   // keep dropdown on "total" (stage sort is via header click)
    }
  }

  renderMatrix();
}

function setupControls() {
  document.getElementById('riderFilter')?.addEventListener('input', e => {
    filterText = e.target.value.toLowerCase().trim();
    if (SCORES) renderMatrix();
  });
  document.getElementById('sortSelect')?.addEventListener('change', e => {
    sortBy  = e.target.value;
    sortDir = -1;
    if (SCORES) renderMatrix();
  });
}

function getFilteredSortedRiders() {
  let riders = [...(SCORES?.riders ?? [])];
  if (filterText) {
    riders = riders.filter(r =>
      r.name.toLowerCase().includes(filterText) ||
      (r.team || '').toLowerCase().includes(filterText)
    );
  }
  riders.forEach(r => {
    r._total = Object.values(r.pts ?? {}).reduce((a, b) => a + b, 0);
  });
  riders.sort((a, b) => {
    // sortDir = -1 means descending (highest first). Numeric comparators
    // use (a - b) so a "larger" rider sorts before a "smaller" one when
    // multiplied by -1 — the old (b - a) form was inverted.
    if (sortBy === 'name')  return a.name.localeCompare(b.name, 'da') * sortDir;
    if (sortBy === 'price') return ((a.price ?? 0) - (b.price ?? 0)) * sortDir;
    if (sortBy === 'total') return (a._total - b._total) * sortDir;
    // Sort by specific stage number
    const sk = String(sortBy);
    return (((a.pts ?? {})[sk] ?? 0) - ((b.pts ?? {})[sk] ?? 0)) * sortDir;
  });
  return riders;
}

function renderMatrix() {
  const wrap = document.getElementById('matrixWrap');
  if (!SCORES || !wrap) return;

  const stages = SCORES.stages;
  const riders = getFilteredSortedRiders();
  const labels = SCORES.rule_labels ?? {};

  // ── Header ──
  const totSort = sortBy === 'total' ? ` sort-${sortDir === -1 ? 'desc' : 'asc'}` : '';
  let header = `<thead><tr>
    <th class="col-name${sortBy === 'name' ? ' sort-' + (sortDir === -1 ? 'desc' : 'asc') : ''}"
        data-sort="name">Rytter</th>
    <th class="col-team" style="cursor:default">Hold</th>`;

  for (const s of stages) {
    const icon   = STAGE_ICONS[s.type] ?? '';
    const dotCls = s.status === 'finished' ? 'dot-finished'
                 : s.status === 'live'     ? 'dot-live' : 'dot-upcoming';
    const active = sortBy === s.num ? ` sort-${sortDir === -1 ? 'desc' : 'asc'}` : '';
    header += `<th class="stage-header${active}" data-sort="${s.num}"
                   title="Etape ${s.num} — ${esc(s.type)}: ${esc(s.name)}">
                 <span class="stage-dot ${dotCls}"></span>${icon}${s.num}
               </th>`;
  }
  header += `<th class="col-total${totSort}" data-sort="total" title="Sum">Total</th>
  </tr></thead>`;

  // ── Body ──
  let body = '<tbody>';
  for (const r of riders) {
    body += `<tr data-rid="${esc(r.id)}">
      <td class="col-name" title="${esc(r.name)}">${esc(r.name)}</td>
      <td class="col-team" title="${esc(r.team)}">${esc(r.team ?? '')}</td>`;
    for (const s of stages) {
      const skey = String(s.num);
      const pts  = (r.pts ?? {})[skey] ?? 0;
      const bg   = cellBg(pts);
      const cls  = pts < 0 ? 'cell-pts cell-neg' : pts > 0 ? 'cell-pts' : 'cell-pts cell-zero';
      body += `<td class="${cls}" style="${bg}"
                   data-pts="${pts}" data-stage="${s.num}" data-rid="${esc(r.id)}"
                   title="${pts ? esc(r.name) + ' E' + s.num + ': ' + fmtFull(pts) + ' pt' : ''}"
               >${pts ? fmtK(pts) : ''}</td>`;
    }
    const tbg = cellBg(r._total);
    body += `<td class="col-total" style="${tbg}" title="Total: ${fmtFull(r._total)} pt">${fmtK(r._total)}</td>
    </tr>`;
  }
  body += '</tbody>';

  wrap.innerHTML = `<table class="matrix-table">${header}${body}</table>`;
  const table = wrap.querySelector('.matrix-table');

  // Sort on header click
  table.querySelectorAll('th[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
      const key    = th.dataset.sort;
      const numKey = isNaN(key) ? key : parseInt(key);
      if (sortBy === numKey) sortDir = -sortDir;
      else { sortBy = numKey; sortDir = -1; }
      const sel = document.getElementById('sortSelect');
      if (sel && ['total','name','price'].includes(key)) sel.value = key;
      renderMatrix();
    });
  });

  // Cell click → breakdown modal
  table.querySelectorAll('.cell-pts:not(.cell-zero)').forEach(cell => {
    cell.addEventListener('click', () => {
      const rid  = cell.dataset.rid;
      const snum = parseInt(cell.dataset.stage);
      const pts  = parseInt(cell.dataset.pts);
      if (pts !== 0) openBreakdownModal(rid, snum, labels);
    });
  });
}

// ── Breakdown Modal ─────────────────────────────────────────────────────────
function setupModal() {
  const overlay = document.getElementById('modalOverlay');
  const closeBtn = document.getElementById('modalClose');
  closeBtn?.addEventListener('click', () => overlay?.classList.remove('open'));
  overlay?.addEventListener('click', e => {
    if (e.target === overlay) overlay.classList.remove('open');
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') overlay?.classList.remove('open');
  });
}

function openBreakdownModal(riderId, stageNum, labels) {
  const rider = SCORES?.riders?.find(r => r.id === riderId);
  if (!rider) return;
  const stageMeta = SCORES.stages.find(s => s.num === stageNum) ?? { type: 'hilly' };
  const skey      = String(stageNum);
  const total     = (rider.pts ?? {})[skey] ?? 0;
  const rules     = (rider.rules ?? {})[skey] ?? [];

  document.getElementById('modalTitle').textContent    = rider.name;
  document.getElementById('modalSubtitle').textContent =
    `Etape ${stageNum} — ${STAGE_ICONS[stageMeta.type] ?? ''} ${stageMeta.name ?? stageMeta.type}`;

  const sortedRules = [...rules].sort(([aId, aAmt], [bId, bAmt]) => {
    const ap = ruleDisplayAmount(aId, aAmt).pts;
    const bp = ruleDisplayAmount(bId, bAmt).pts;
    if (ap !== 0 && bp === 0) return -1;
    if (ap === 0 && bp !== 0) return 1;
    return Math.abs(bp) - Math.abs(ap);
  });

  const rulesHtml = sortedRules.length
    ? sortedRules.map(([ruleId, amount]) => {
        const desc    = labels[String(ruleId)] ?? `Regel ${ruleId}`;
        const display = ruleDisplayAmount(ruleId, amount);
        const negCls  = display.pts < 0 ? ' negative' : '';
        const info    = display.pts === 0;
        return `<div class="modal-rule" style="${info ? 'opacity:0.55;font-size:0.75rem' : ''}">
          <span class="rule-desc">${esc(desc)}${display.suffix
            ? ` <small style="color:var(--muted)">${esc(display.suffix)}</small>` : ''}</span>
          <span class="rule-amt${negCls}">${info ? '–'
            : (display.pts >= 0 ? '+' : '') + fmtFull(display.pts)}</span>
        </div>`;
      }).join('')
    : '<div style="color:var(--muted);font-size:0.82rem;padding:12px 0">Ingen regeldata tilgængeligt</div>';

  document.getElementById('modalRules').innerHTML = rulesHtml + `
    <div class="modal-total-row">
      <span>Total</span>
      <span style="color:${total < 0 ? 'var(--red)' : 'var(--yellow)'}">
        ${total >= 0 ? '+' : ''}${fmtFull(total)} pt
      </span>
    </div>`;

  document.getElementById('modalOverlay').classList.add('open');
}

function ruleDisplayAmount(ruleId, amount) {
  const SP  = {1:200000,2:150000,3:130000,4:120000,5:110000,
               6:100000,7:95000,8:90000,9:85000,10:80000,
               11:70000,12:55000,13:40000,14:30000,15:15000};
  const GC  = {1:100000,2:90000,3:80000,4:70000,5:60000,
               6:50000,7:40000,8:30000,9:20000,10:10000};
  const JRS = {leader:25000,sprint:25000,mountain:25000,youth:15000,most_aggressive:50000};
  const TM  = {1:60000,2:30000,3:20000};
  const a   = Math.round(amount);
  if (ruleId >= 849 && ruleId <= 863) return { pts: SP[ruleId-848] ?? 0, suffix: '' };
  if (ruleId >= 864 && ruleId <= 873) return { pts: GC[ruleId-863] ?? 0, suffix: '' };
  if (ruleId === 874) return { pts: a * 3000, suffix: `×${a} sprint-pt` };
  if (ruleId === 875) return { pts: a * 3000, suffix: `×${a} KOM-pt` };
  if (ruleId === 876) return { pts: JRS.leader,          suffix: '' };
  if (ruleId === 877) return { pts: JRS.sprint,          suffix: '' };
  if (ruleId === 878) return { pts: JRS.mountain,        suffix: '' };
  if (ruleId === 879) return { pts: JRS.youth,           suffix: '' };
  if (ruleId === 1044) return { pts: JRS.most_aggressive, suffix: '' };
  if (ruleId === 1080) return { pts: -50000,             suffix: '' };
  if (ruleId === 895)  return { pts: Math.max(-90000, a * -3000), suffix: `${a} min. forsinket` };
  if (ruleId === 891)  return { pts: 0,                  suffix: `GC #${a}` };
  if (ruleId === 885)  return { pts: TM[1],              suffix: '1. bedste hold' };
  if (ruleId === 886)  return { pts: TM[2],              suffix: '2. bedste hold' };
  if (ruleId === 887)  return { pts: TM[3],              suffix: '3. bedste hold' };
  if (ruleId === 888)  return { pts: 0,                  suffix: `etapepl. #${a}` };
  if (ruleId === 889)  return { pts: 0,                  suffix: `sprint-klass. ×${a}` };
  if (ruleId === 890)  return { pts: 0,                  suffix: `KOM-klass. ×${a}` };
  if (ruleId === 892)  return { pts: 0,                  suffix: `pointklass. #${a}` };
  if (ruleId === 893)  return { pts: 0,                  suffix: `bjergklass. #${a}` };
  if (ruleId === 894)  return { pts: 0,                  suffix: 'etapepræmie' };
  if (ruleId === 904)  return { pts: 0,                  suffix: 'særpræmie' };
  return { pts: a, suffix: `regel ${ruleId}` };
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
    <h2 style="margin-bottom:12px">Alle ryttere — forventet score</h2>
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
        ${sorted.map((r, i) => {
          const dk = (r.disc_key || 'AVG').toUpperCase();
          const dLabel = DISC_LABELS[dk] || dk;
          const rankCls = i < 3 ? 'rank-top' : 'rank-num';
          const rowCls  = [r.in_opt ? 'in-opt' : '', r.is_cap ? 'is-cap' : ''].filter(Boolean).join(' ');
          return `<tr class="${rowCls}">
            <td class="${rankCls}">${i+1}</td>
            <td class="col-name-p">${esc(r.name)}${ctxBadge(r.ctx_status, r.ctx_note, r.ctx_mult)}</td>
            <td style="color:var(--muted);font-size:0.75rem">${esc(r.team)}</td>
            <td style="font-size:0.78rem">${r.price?.toFixed?.(1) ?? '?'}M</td>
            <td class="pts-exp">${fmt(r.exp)}</td>
            <td>${miniBar(r.signals || [0,0,0,0,0,0], r.disc, r.disc_co, r.disc_key)}</td>
            <td style="font-size:0.75rem;color:var(--muted)" title="${dLabel}: ${r.disc?.toFixed?.(0) ?? '?'}/100 i felt${r.disc_co != null ? ` (CO: ${r.disc_co.toFixed(0)})` : ''}">${dLabel}: ${r.disc?.toFixed?.(0) ?? '?'}</td>
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
      <td class="col-name-p">${esc(r.name)}${ctxBadge(r.ctx_status, r.ctx_note, r.ctx_mult)}</td>
      <td style="color:var(--muted);font-size:0.75rem">${esc(r.team)}</td>
      <td style="font-size:0.78rem">${r.price?.toFixed?.(1) ?? '?'}M</td>
      <td class="pts-exp">${fmt(r.exp)}</td>
      ${actHtml}
      <td>${miniBar(r.signals || [0,0,0,0,0,0], r.disc, r.disc_co, r.disc_key)}</td>
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

// ── Tab: Odds & Kilder ─────────────────────────────────────────────────────────
function renderOddsTab() {
  const stages = PREDS?.stages ?? [];
  const SOURCE_META = {
    'spilxperten.com': { label: 'Spilxperten',    cls: 'source-spilxperten', desc: 'Bet365 decimalodds' },
    'TV2 Axelgaard':   { label: 'TV2 Axelgaard',   cls: 'source-tv2',         desc: 'Stjerne-ratings (1-5)' },
    'IDLProCycling':   { label: 'IDL Pro Cycling',  cls: 'source-idl',         desc: 'Tier-kategorier' },
    '':                { label: 'Ingen data',       cls: 'source-none',        desc: '' },
  };
  const SICONS = { sprint:'⚡', mountain:'⛰️', tt:'⏱️', ttt:'👥', hilly:'〰️', cobbled:'🧱' };

  let html = `
    <p style="font-size:0.78rem;color:var(--muted);margin-bottom:20px;line-height:1.6">
      Systemet prøver disse kilder automatisk i prioriteret rækkefølge:<br>
      <span style="color:#2ecc71;font-weight:600">1. Spilxperten.com</span> — faktiske Bet365 decimalodds →
      <span style="color:#e74c3c;font-weight:600">2. TV2 Axelgaard</span> — stjerneratings fra Axelgaards optakter →
      <span style="color:#3498db;font-weight:600">3. IDL Pro Cycling</span> — tier-bud (Top/Outsiders/Long shots).<br>
      Kommende etaper mangler data indtil artiklerne er skrevet (typisk dagen før).
    </p>
    <div class="odds-grid">`;

  for (const stage of stages) {
    const src    = stage.odds_source ?? '';
    const top    = stage.odds_top   ?? [];
    const meta   = SOURCE_META[src] ?? SOURCE_META[''];
    const icon   = SICONS[stage.type] ?? '🚴';
    const isFinished = stage.status === 'finished';
    const maxProb = top.length ? top[0].prob : 1;

    html += `
      <div class="odds-card">
        <div class="odds-card-header">
          <span class="odds-stage-badge">${icon} E${stage.num}</span>
          <span class="odds-stage-name" title="${esc(stage.name ?? '')}">${esc(stage.name ?? '')}</span>
          <span class="odds-source-badge ${meta.cls}" title="${esc(meta.desc)}">${meta.label}</span>
        </div>`;

    if (isFinished) {
      html += `<div class="odds-no-data">Afsluttet — ingen forudsigelse</div>`;
    } else if (!top.length) {
      html += `<div class="odds-no-data">Ingen data endnu</div>`;
    } else {
      top.forEach((r, i) => {
        const barPct = Math.round((r.prob / maxProb) * 100);
        const probPct = (r.prob * 100).toFixed(1) + '%';
        html += `
          <div class="odds-row">
            <span class="odds-rank">${i + 1}</span>
            <span class="odds-name">${esc(r.name)}</span>
            <div class="odds-bar-wrap"><div class="odds-bar-fill" style="width:${barPct}%"></div></div>
            <span class="odds-prob">${probPct}</span>
          </div>`;
      });
    }
    html += `</div>`;
  }

  html += `</div>`;
  const genTime = PREDS?.generated
    ? new Date(PREDS.generated).toLocaleString('da-DK', {dateStyle:'short', timeStyle:'short'})
    : '';
  if (genTime) html += `<p style="font-size:0.7rem;color:var(--muted);margin-top:16px;text-align:right">Beregnet: ${genTime}</p>`;
  document.getElementById('oddsContent').innerHTML = html;
}

// ── Tab switching ──────────────────────────────────────────────────────────────
function setupTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      activeTab = tab;
      document.querySelectorAll('.tab-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.tab === tab));
      document.getElementById('tab-scores').style.display = tab === 'scores' ? '' : 'none';
      document.getElementById('tab-hold').style.display   = tab === 'hold'   ? '' : 'none';
      document.getElementById('tab-preds').style.display  = tab === 'preds'  ? '' : 'none';
      document.getElementById('tab-odds').style.display   = tab === 'odds'   ? '' : 'none';
      if (tab === 'odds' && PREDS) renderOddsTab();
    });
  });
}

// ── Start ──────────────────────────────────────────────────────────────────────
init();
