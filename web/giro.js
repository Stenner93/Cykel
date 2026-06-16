/* Giro d'Italia 2026 Dashboard — Pointmatrix + Forudsigelser */

const SCORES_URL = './data/giro2026_scores.json';
const PREDS_URL  = './data/giro2026_predictions.json';

const STAGE_ICONS = {
  sprint:   '⚡',
  mountain: '⛰️',
  tt:       '⏱️',
  hilly:    '〰️',
  cobbled:  '🧱',
};

const DISC_LABELS = {
  SPR: 'Sprint', MTN: 'Bjerg', ITT: 'Enkeltstart',
  HLL: 'Bakket', COB: 'Brosten', GC: 'GC', AVG: 'Disciplin',
};

// ── State ──────────────────────────────────────────────────────────────────────
let SCORES = null;
let PREDS  = null;
let activeTab      = 'scores';
let sortBy         = 'total';   // 'total' | 'name' | 'price' | stage number (int)
let sortDir        = -1;        // -1 = desc, 1 = asc
let filterText     = '';
let predStageNum   = null;      // currently selected predictions stage

// ── Utilities ──────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function fmt(n) {
  if (n == null || isNaN(n)) return '–';
  return n.toLocaleString('da-DK');
}

/** Format points as short string: 350k, 1.2M, -50k */
function fmtK(n) {
  if (!n) return '';
  if (Math.abs(n) >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (Math.abs(n) >= 1_000)     return Math.round(n / 1_000)        + 'k';
  return String(n);
}

/** Format points as full number with thousand-separators */
function fmtFull(n) {
  if (n == null) return '–';
  return n.toLocaleString('da-DK');
}

/** Background colour for a cell based on points magnitude */
function cellBg(pts) {
  if (!pts || pts === 0) return '';
  if (pts < 0) {
    // Negative: dark red tones
    const t = Math.min(1, Math.abs(pts) / 90_000);
    return `background: hsl(0, ${60 + t * 30}%, ${15 + t * 10}%)`;
  }
  // Positive: blue → teal → yellow → orange, scaling to 600k
  const t = Math.min(1, pts / 600_000);
  const hue = Math.round(220 - t * 220); // 220 (blue) → 0 (red/orange)
  const sat = Math.round(55 + t * 35);
  const lit = Math.round(16 + t * 22);
  return `background: hsl(${hue}, ${sat}%, ${lit}%)`;
}

/** Colour for actual vs expected comparison */
function actClass(actual, expected) {
  if (!actual || !expected) return 'pts-ok';
  if (actual >= expected)        return 'pts-beat';
  if (actual >= expected * 0.5)  return 'pts-ok';
  return 'pts-miss';
}

/** 4-segment mini signal bar */
function miniSignalBar(signals, discKey, formScore, discRaw) {
  const dk     = (discKey || 'AVG').toUpperCase();
  const labels = ['VeloScore', 'Odds', `${DISC_LABELS[dk] || dk}: ${discRaw?.toFixed(0) ?? '?'}/100`, `Form: ${formScore?.toFixed(0) ?? '?'}/100`];
  return signals.map((v, i) =>
    `<div class="mini-seg ${v > 0.3 ? 'on' : ''}" title="${labels[i]}: ${(v*100).toFixed(0)}%"></div>`
  ).join('');
}

// ── Data loading ───────────────────────────────────────────────────────────────
async function loadJSON(url) {
  const res = await fetch(url + '?t=' + Date.now());
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${url}`);
  return res.json();
}

// ── Init ───────────────────────────────────────────────────────────────────────
async function init() {
  try {
    [SCORES, PREDS] = await Promise.all([
      loadJSON(SCORES_URL).catch(e => { console.warn('scores:', e); return null; }),
      loadJSON(PREDS_URL).catch(e  => { console.warn('preds:', e);  return null; }),
    ]);

    if (SCORES) renderScoresTab();
    else        showError('matrixWrap', 'Pointmatrix-data ikke fundet.',
                          'python build_giro_web_data.py');

    if (PREDS) {
      buildStagePills();
      // Default to first stage
      predStageNum = PREDS.stages[0]?.num ?? null;
      renderPredStage(predStageNum);
    } else {
      showError('predContent', 'Forudsigelsesdata ikke fundet.',
                'python build_giro_web_data.py');
    }

  } catch (err) {
    showError('matrixWrap', err.message, 'python build_giro_web_data.py');
  }

  setupTabs();
  setupControls();
  setupModal();
}

function showError(containerId, msg, cmd) {
  document.getElementById(containerId).innerHTML = `
    <div class="info-box">
      <div class="icon">⚠️</div>
      <div style="color:#E53935;margin-bottom:8px">${esc(msg)}</div>
      ${cmd ? `<div style="color:var(--muted);font-size:0.8rem">Kør: <code>${esc(cmd)}</code></div>` : ''}
    </div>`;
}

// ── Tab switching ──────────────────────────────────────────────────────────────
function setupTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const tab = btn.dataset.tab;
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('tab-scores').style.display = tab === 'scores' ? ''     : 'none';
      document.getElementById('tab-preds').style.display  = tab === 'preds'  ? ''     : 'none';
    });
  });
}

// ── Controls ──────────────────────────────────────────────────────────────────
function setupControls() {
  const filter = document.getElementById('riderFilter');
  filter?.addEventListener('input', e => {
    filterText = e.target.value.toLowerCase().trim();
    if (SCORES) renderMatrix();
  });

  const sel = document.getElementById('sortSelect');
  sel?.addEventListener('change', e => {
    sortBy  = e.target.value;
    sortDir = -1;
    if (SCORES) renderMatrix();
  });
}

// ── Scores Tab ─────────────────────────────────────────────────────────────────
function renderScoresTab() {
  const note = document.getElementById('sourceNote');
  if (note) {
    const src = SCORES.summary_source === 'scoring-summary'
      ? 'Point: officielle Holdet-totaler'
      : 'Point: beregnet fra regler (approx.)';
    note.textContent = src;
  }
  renderMatrix();
}

function getFilteredSortedRiders() {
  let riders = [...(SCORES?.riders ?? [])];

  // Filter
  if (filterText) {
    riders = riders.filter(r =>
      r.name.toLowerCase().includes(filterText) ||
      r.team.toLowerCase().includes(filterText)
    );
  }

  // Compute total for each rider
  riders.forEach(r => {
    r._total = Object.values(r.pts ?? {}).reduce((a, b) => a + b, 0);
  });

  // Sort
  riders.sort((a, b) => {
    let va, vb;
    if (sortBy === 'total')  { va = a._total; vb = b._total; }
    else if (sortBy === 'name')  { va = a.name;   vb = b.name;   return va.localeCompare(vb, 'da') * sortDir; }
    else if (sortBy === 'price') { va = a.price;  vb = b.price;  }
    else {
      // Sort by specific stage
      const skey = String(sortBy);
      va = (a.pts ?? {})[skey] ?? 0;
      vb = (b.pts ?? {})[skey] ?? 0;
    }
    // sortDir = -1 means descending (highest first); (va - vb) ensures a
    // "larger" rider sorts before a "smaller" one when multiplied by -1.
    return (va - vb) * sortDir;
  });

  return riders;
}

function renderMatrix() {
  const wrap = document.getElementById('matrixWrap');
  if (!SCORES || !wrap) return;

  const stages  = SCORES.stages;
  const riders  = getFilteredSortedRiders();
  const labels  = SCORES.rule_labels ?? {};

  // ── Header ──
  let header = '<thead><tr>';
  header += `<th class="col-name${sortBy === 'name' ? ' sort-' + (sortDir === -1 ? 'desc' : 'asc') : ''}"
               data-sort="name" title="Sorter efter navn">Rytter</th>`;
  header += `<th class="col-team" style="cursor:default">Hold</th>`;

  for (const s of stages) {
    const icon   = STAGE_ICONS[s.type] ?? '';
    const dotCls = s.status === 'finished' ? 'dot-finished' : s.status === 'live' ? 'dot-live' : 'dot-upcoming';
    const active = (sortBy === s.num) ? ` sort-${sortDir === -1 ? 'desc' : 'asc'}` : '';
    header += `<th class="stage-header${active}" data-sort="${s.num}"
                 title="Etape ${s.num} — ${esc(s.type)}: ${esc(s.name)}">
                 <span class="stage-dot ${dotCls}"></span>${icon}${s.num}
               </th>`;
  }

  const totSort = sortBy === 'total' ? ` sort-${sortDir === -1 ? 'desc' : 'asc'}` : '';
  header += `<th class="col-total${totSort}" data-sort="total" title="Sum af alle etaper">Total</th>`;
  header += '</tr></thead>';

  // ── Body ──
  let body = '<tbody>';
  for (const r of riders) {
    body += `<tr data-rid="${esc(r.id)}">`;
    body += `<td class="col-name" title="${esc(r.name)}">${esc(r.name)}</td>`;
    body += `<td class="col-team" title="${esc(r.team)}">${esc(r.team)}</td>`;

    for (const s of stages) {
      const skey = String(s.num);
      const pts  = (r.pts ?? {})[skey] ?? 0;
      const bg   = cellBg(pts);
      const cls  = pts < 0 ? 'cell-pts cell-neg' : pts > 0 ? 'cell-pts' : 'cell-pts cell-zero';
      const tip  = pts ? `${esc(r.name)} Etape ${s.num}: ${fmtFull(pts)} pt` : '';
      body += `<td class="${cls}" style="${bg}"
                  data-pts="${pts}" data-stage="${s.num}" data-rid="${esc(r.id)}"
                  title="${tip}">${pts ? fmtK(pts) : ''}</td>`;
    }

    const bg = cellBg(r._total);
    body += `<td class="col-total" style="${bg}" title="Total: ${fmtFull(r._total)} pt">${fmtK(r._total)}</td>`;
    body += '</tr>';
  }
  body += '</tbody>';

  // Build final table
  const html = `<table class="matrix-table">${header}${body}</table>`;
  wrap.innerHTML = html;

  const table = wrap.querySelector('.matrix-table');

  // Sort on header click
  table.querySelectorAll('th[data-sort]').forEach(th => {
    th.style.cursor = 'pointer';
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      const numKey = isNaN(key) ? key : parseInt(key);
      if (sortBy === numKey) sortDir = -sortDir;
      else { sortBy = numKey; sortDir = -1; }
      // Update sort select if it's a named key
      const sel = document.getElementById('sortSelect');
      if (sel && (key === 'total' || key === 'name' || key === 'price')) {
        sel.value = key;
      }
      renderMatrix();
    });
  });

  // Cell click → breakdown modal
  table.querySelectorAll('.cell-pts[data-pts]:not(.cell-zero)').forEach(cell => {
    cell.addEventListener('click', e => {
      const rid   = cell.dataset.rid;
      const snum  = parseInt(cell.dataset.stage);
      const pts   = parseInt(cell.dataset.pts);
      if (pts !== 0) openBreakdownModal(rid, snum, labels);
    });
  });
}

// ── Breakdown Modal ────────────────────────────────────────────────────────────
function setupModal() {
  const overlay = document.getElementById('modalOverlay');
  const closeBtn = document.getElementById('modalClose');
  closeBtn?.addEventListener('click', () => overlay.classList.remove('open'));
  overlay?.addEventListener('click', e => {
    if (e.target === overlay) overlay.classList.remove('open');
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') overlay?.classList.remove('open');
  });
}

function openBreakdownModal(riderId, stageNum, labels) {
  const rider  = SCORES.riders.find(r => r.id === riderId);
  const stages = SCORES.stages;
  if (!rider) return;

  const stageMeta = stages.find(s => s.num === stageNum) ?? { type: 'hilly' };
  const skey      = String(stageNum);
  const total     = (rider.pts ?? {})[skey] ?? 0;
  const rules     = (rider.rules ?? {})[skey] ?? [];

  document.getElementById('modalTitle').textContent    = rider.name;
  document.getElementById('modalSubtitle').textContent =
    `Etape ${stageNum} — ${STAGE_ICONS[stageMeta.type] ?? ''} ${stageMeta.type}`;

  // Sort rules: scoring rules first, informational last
  const sortedRules = [...rules].sort(([aId, aAmt], [bId, bAmt]) => {
    const aPts = ruleDisplayAmount(aId, aAmt).pts;
    const bPts = ruleDisplayAmount(bId, bAmt).pts;
    if (aPts !== 0 && bPts === 0) return -1;
    if (aPts === 0 && bPts !== 0) return 1;
    return Math.abs(bPts) - Math.abs(aPts);
  });

  const rulesHtml = sortedRules.length
    ? sortedRules.map(([ruleId, amount]) => {
        const desc    = labels[String(ruleId)] ?? `Regel ${ruleId}`;
        const display = ruleDisplayAmount(ruleId, amount);
        const negCls  = display.pts < 0 ? ' negative' : '';
        const info    = display.pts === 0;
        return `
          <div class="modal-rule" style="${info ? 'opacity:0.55;font-size:0.75rem' : ''}">
            <span class="rule-desc">${esc(desc)}${display.suffix ? ` <small style="color:var(--muted)">${esc(display.suffix)}</small>` : ''}</span>
            <span class="rule-amt${negCls}">${info ? '–' : (display.pts >= 0 ? '+' : '') + fmtFull(display.pts)}</span>
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

/**
 * Given a rule ID and raw amount, return {pts, suffix}.
 * pts:    fantasy points awarded (0 if rule is purely informational)
 * suffix: extra display info like "×5 sprint-pt" or "etape #36"
 *
 * NOTE: pts values here are APPROXIMATE — actual totals come from
 * scoring-summary. This is only for the breakdown tooltip display.
 */
function ruleDisplayAmount(ruleId, amount) {
  const STAGE_PTS = {1:200000,2:150000,3:130000,4:120000,5:110000,
                     6:100000,7:95000,8:90000,9:85000,10:80000,
                     11:70000,12:55000,13:40000,14:30000,15:15000};
  const GC_PTS    = {1:100000,2:90000,3:80000,4:70000,5:60000,
                     6:50000,7:40000,8:30000,9:20000,10:10000};
  const JERSEY    = {leader:25000,sprint:25000,mountain:25000,youth:15000,most_aggressive:50000};
  const TEAM      = {1:60000,2:30000,3:20000};
  const SPT = 3000;
  const a   = Math.round(amount);

  if (ruleId >= 849 && ruleId <= 863) return { pts: STAGE_PTS[ruleId - 848] ?? 0, suffix: '' };
  if (ruleId >= 864 && ruleId <= 873) return { pts: GC_PTS[ruleId - 863] ?? 0,    suffix: '' };
  if (ruleId === 874) return { pts: a * SPT,            suffix: `×${a} sprint-pt` };
  if (ruleId === 875) return { pts: a * SPT,            suffix: `×${a} KOM-pt` };
  if (ruleId === 876) return { pts: JERSEY.leader,      suffix: '' };
  if (ruleId === 877) return { pts: JERSEY.sprint,      suffix: '' };
  if (ruleId === 878) return { pts: JERSEY.mountain,    suffix: '' };
  if (ruleId === 879) return { pts: JERSEY.youth,       suffix: '' };
  if (ruleId === 1044) return { pts: JERSEY.most_aggressive, suffix: '' };
  if (ruleId === 1080) return { pts: -50000,            suffix: '' };
  if (ruleId === 895)  return { pts: Math.max(-90000, a * -3000),
                                suffix: `${a} min. forsinket` };
  // Rule 891 = GC rank (informational) — bonus is via rule 864-873; show 0 here to avoid double-count
  if (ruleId === 891)  return { pts: 0,                 suffix: `GC #${a}` };
  // Hold bonuses
  if (ruleId === 885)  return { pts: TEAM[1],           suffix: '1. bedste hold' };
  if (ruleId === 886)  return { pts: TEAM[2],           suffix: '2. bedste hold' };
  if (ruleId === 887)  return { pts: TEAM[3],           suffix: '3. bedste hold' };
  // Informational rules (no extra points beyond what's already counted)
  if (ruleId === 888)  return { pts: 0,                 suffix: `etapepl. #${a}` };
  if (ruleId === 889)  return { pts: 0,                 suffix: `sprint-klass. ×${a}` };
  if (ruleId === 890)  return { pts: 0,                 suffix: `KOM-klass. ×${a}` };
  if (ruleId === 892)  return { pts: 0,                 suffix: `pointklass. #${a}` };
  if (ruleId === 893)  return { pts: 0,                 suffix: `bjergklass. #${a}` };
  if (ruleId === 894)  return { pts: 0,                 suffix: 'etapepræmie' };
  if (ruleId === 904)  return { pts: 0,                 suffix: 'særpræmie' };
  return { pts: a, suffix: `regel ${ruleId}` };
}

// ── Predictions Tab ────────────────────────────────────────────────────────────
function buildStagePills() {
  const bar = document.getElementById('stagePills');
  if (!PREDS || !bar) return;

  bar.innerHTML = PREDS.stages.map(s => {
    const icon = STAGE_ICONS[s.type] ?? '❓';
    return `<button class="stage-pill" data-stage="${s.num}">
              <span class="pill-icon">${icon}</span> Etape ${s.num}
            </button>`;
  }).join('');

  bar.querySelectorAll('.stage-pill').forEach(pill => {
    pill.addEventListener('click', () => {
      bar.querySelectorAll('.stage-pill').forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      predStageNum = parseInt(pill.dataset.stage);
      renderPredStage(predStageNum);
    });
  });

  // Activate first
  bar.querySelector('.stage-pill')?.classList.add('active');
}

function renderPredStage(stageNum) {
  const content = document.getElementById('predContent');
  if (!PREDS || !content) return;

  const stageData = PREDS.stages.find(s => s.num === stageNum);
  if (!stageData) {
    content.innerHTML = '<div class="info-box"><div class="icon">❓</div><div>Ingen data for denne etape</div></div>';
    return;
  }

  const riders = [...stageData.riders];
  const hasActual = riders.some(r => r.actual != null);
  const maxExp = Math.max(...riders.map(r => r.exp ?? 0));
  const maxAct = hasActual ? Math.max(...riders.map(r => r.actual ?? 0)) : 0;

  // ── Table ──
  let html = `
  <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px;font-size:0.78rem;color:var(--muted)">
    <span>📅 Etape ${stageNum}</span>
    <span>${STAGE_ICONS[stageData.type] ?? ''} ${stageData.type}</span>
    <span>🏍 ${riders.length} ryttere</span>
    ${hasActual ? '<span style="color:var(--green)">✓ Faktiske resultater tilgængelige</span>' : ''}
    <span style="margin-left:auto;font-style:italic">Optimal hold = blå baggrund, kaptajn = ★</span>
  </div>
  <div class="table-wrapper">
  <table class="pred-table">
    <thead>
      <tr>
        <th style="width:32px">#</th>
        <th class="col-name-p">Rytter</th>
        <th>Hold</th>
        <th>Pris</th>
        <th>Forventet</th>
        <th>Signal</th>
        <th>Disc</th>
        <th>Form</th>
        <th>Begrundelse</th>
        ${hasActual ? '<th>Faktisk</th>' : ''}
      </tr>
    </thead>
    <tbody>`;

  riders.forEach((r, i) => {
    const rowCls = r.is_cap ? 'is-cap' : r.in_opt ? 'in-opt' : '';
    const rankCls = i < 3 ? 'rank-top' : 'rank-num';
    const signals = r.signals ?? [0,0,0,0];
    const miniBar = `<div class="mini-signals">${miniSignalBar(signals, r.disc_key, r.form, r.disc)}</div>`;

    let actCell = '';
    if (hasActual) {
      if (r.actual != null) {
        const cls = actClass(r.actual, r.exp);
        actCell = `<td class="pts-act ${cls}">${fmtK(r.actual)}</td>`;
      } else {
        actCell = `<td style="color:var(--muted);font-size:0.72rem">–</td>`;
      }
    }

    html += `
      <tr class="${rowCls}">
        <td><span class="${rankCls}">${i + 1}</span></td>
        <td class="col-name-p" style="font-weight:500">${esc(r.name)}</td>
        <td style="color:var(--muted);font-size:0.75rem">${esc(r.team)}</td>
        <td style="color:var(--muted);font-size:0.78rem">${(r.price ?? 0).toFixed(1)}M</td>
        <td class="pts-exp">${fmtK(r.exp)}</td>
        <td title="VeloScore / Odds / Disciplin / Form">${miniBar}</td>
        <td style="font-size:0.75rem;color:var(--yellow)" title="${esc(DISC_LABELS[r.disc_key] ?? r.disc_key)}: ${r.disc?.toFixed(0) ?? '?'}/100">${r.disc_key ?? '–'}: ${r.disc?.toFixed(0) ?? '–'}</td>
        <td style="font-size:0.75rem;color:var(--muted)" title="Form score (70% etapetype + 30% overall)">${r.form?.toFixed(0) ?? '–'}</td>
        <td style="font-size:0.75rem;color:var(--muted);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(r.reason)}">${esc(r.reason)}</td>
        ${actCell}
      </tr>`;
  });

  html += `</tbody></table></div>`;

  // ── Summary stats ──
  if (hasActual) {
    const withActual   = riders.filter(r => r.actual != null);
    const hits         = withActual.filter(r => r.actual >= r.exp * 0.7).length;
    const optTeam      = riders.filter(r => r.in_opt || r.is_cap);
    const optActual    = optTeam.reduce((s, r) => s + (r.actual ?? 0), 0);
    const optExpected  = optTeam.reduce((s, r) => s + (r.exp ?? 0), 0);

    html += `
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:20px">
      <div class="assess-item" style="text-align:center;padding:14px;background:var(--card);border-radius:8px;border:1px solid var(--border)">
        <div class="assess-val" style="color:var(--yellow)">${hits}/${withActual.length}</div>
        <div class="assess-lbl">Ryttere ≥ 70% forv.</div>
      </div>
      <div class="assess-item" style="text-align:center;padding:14px;background:var(--card);border-radius:8px;border:1px solid var(--border)">
        <div class="assess-val" style="color:var(--green)">${fmtK(optActual)}</div>
        <div class="assess-lbl">Opt. hold faktisk</div>
      </div>
      <div class="assess-item" style="text-align:center;padding:14px;background:var(--card);border-radius:8px;border:1px solid var(--border)">
        <div class="assess-val" style="color:var(--muted)">${fmtK(optExpected)}</div>
        <div class="assess-lbl">Opt. hold forventet</div>
      </div>
      <div class="assess-item" style="text-align:center;padding:14px;background:var(--card);border-radius:8px;border:1px solid var(--border)">
        <div class="assess-val" style="color:${optActual >= optExpected * 0.7 ? 'var(--green)' : 'var(--red)'}">
          ${optExpected ? Math.round(optActual / optExpected * 100) : '–'}%
        </div>
        <div class="assess-lbl">Effektivitet</div>
      </div>
    </div>`;
  }

  content.innerHTML = html;
}

// ── Boot ───────────────────────────────────────────────────────────────────────
init();
