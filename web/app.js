/* TdF Manager 2026 — Frontend logic */

const DATA_URL = './data/recommendations.json';

const STAGE_TYPE_META = {
  sprint:   { icon: '⚡', name: 'Sprinteretape',      color: '#FFD700' },
  mountain: { icon: '⛰️', name: 'Bjergetape',         color: '#00C853' },
  tt:       { icon: '⏱️', name: 'Enkeltstart (TT)',    color: '#1E88E5' },
  hilly:    { icon: '〰️', name: 'Kuperet / Punch',    color: '#FF7043' },
  cobbled:  { icon: '🧱', name: 'Brosteenetape',      color: '#AB47BC' },
  unknown:  { icon: '❓', name: 'Ukendt etapetype',   color: '#9E9E9E' },
};

const LABEL_META = {
  safe:   { dk: 'SIKKER',       desc: 'Maksimér forventet score med lave risici' },
  value:  { dk: 'VÆRDI',        desc: 'Budget-ryttere frigiver penge til en stjerne' },
  attack: { dk: 'ANGREB',       desc: 'Høj variance — potentiel overraskelse vinder' },
  best:   { dk: 'BEDST MULIGT', desc: 'Ubegrænset hold — 50M fra scratch, ingen transferomkostninger' },
};

// CyclingOracle discipline key → human label
const DISC_LABELS = {
  SPR: 'Sprint', MTN: 'Bjerg', ITT: 'Enkeltstart',
  HLL: 'Bakket', COB: 'Brosten', GC: 'GC', AVG: 'Disciplin',
};

function fmt(n) {
  if (n == null || isNaN(n)) return '–';
  return n.toLocaleString('da-DK');
}
function fmtM(n, forceSign) {
  if (n == null || isNaN(n)) return '–';
  const s = Math.abs(n).toFixed(2) + 'M';
  if (forceSign) return (n >= 0 ? '+' : '−') + s;
  return s;
}
function fmtK(n) {
  if (n == null || isNaN(n)) return '–';
  return (n / 1000).toFixed(0) + 'k';
}
function stars(n) {
  return '★'.repeat(Math.max(0, Math.min(5, n || 0)));
}
function priceClass(p) {
  if (p >= 9) return 'price-high';
  if (p >= 6) return 'price-mid';
  return 'price-low';
}

/**
 * 4-segment signal bar.
 * disc_key:   "SPR" / "MTN" / "ITT" etc. — shown as tooltip label on disc segment.
 * disc_raw:   raw 0-100 CyclingOracle value — shown in tooltip.
 * form_score: 0-100 blended form score (70% type-specific + 30% overall) — shown in form tooltip.
 */
function signalBar(signals, discKey, discRaw, formScore) {
  const dk    = (discKey || 'AVG').toUpperCase();
  const label = DISC_LABELS[dk] || dk;
  const formLbl = formScore != null
    ? `Form: ${formScore.toFixed(0)}/100`
    : `Form: ${((signals?.form ?? 0) * 100).toFixed(0)}%`;
  const segs  = [
    { k: 'veloscore',  lbl: 'VeloScore' },
    { k: 'odds',       lbl: 'Odds' },
    { k: 'discipline', lbl: discRaw != null ? `${label}: ${discRaw.toFixed(0)}/100` : label },
    { k: 'form',       lbl: formLbl },
  ].map(({ k, lbl }) => {
    const v      = signals?.[k] ?? 0;
    const filled = v > 0.3 ? 'filled' : '';
    const title  = (k === 'discipline' || k === 'form')
      ? lbl   // already has the numeric value
      : `${lbl}: ${(v * 100).toFixed(0)}%`;
    return `<div class="signal-segment ${filled}" title="${title}"></div>`;
  }).join('');

  // Compact label beneath the bar showing the discipline type
  const discBadge = `<span class="disc-badge" title="${label}">${dk}</span>`;
  return `<div class="signal-wrap">${discBadge}<div class="signal-bar">${segs}</div></div>`;
}

async function loadData() {
  const res = await fetch(DATA_URL + '?t=' + Date.now());
  if (!res.ok) throw new Error('Kunne ikke hente data: ' + res.status);
  return await res.json();
}

// ── Stage badge ────────────────────────────────────────────────────────────
function renderStageBadge(stage, stageType) {
  const meta = STAGE_TYPE_META[stageType] || STAGE_TYPE_META.unknown;
  document.getElementById('stageBadge').textContent = `Etape ${stage}`;
  document.getElementById('stageBar').style.display = 'block';
  document.getElementById('stageTypeIcon').textContent = meta.icon;
  document.getElementById('stageTypeName').textContent = meta.name;
}

function renderGeneratedAt(iso) {
  if (!iso) return;
  const d = new Date(iso);
  document.getElementById('generatedAt').textContent =
    'Opdateret: ' + d.toLocaleString('da-DK');
}

// ── Current team ───────────────────────────────────────────────────────────
function renderCurrentTeam(currentTeam) {
  if (!currentTeam?.matched_riders?.length) return;
  document.getElementById('currentTeamSection').style.display = 'block';

  const grid = document.getElementById('currentTeamGrid');
  grid.innerHTML = currentTeam.matched_riders.map(r => `
    <div class="ct-rider">
      <div class="ct-name">${r.full_name}</div>
      <div class="ct-team">${r.team}</div>
      <div class="ct-price ${priceClass(r.price_M)}">${fmtM(r.price_M)}</div>
    </div>`).join('');

  const totalM = currentTeam.matched_riders.reduce((s, r) => s + (r.price_M || 0), 0);
  const bankM  = currentTeam.bank_M || 0;
  document.getElementById('currentTeamMeta').innerHTML = `
    <span class="meta-pill">💰 Holdværdi: ${fmtM(totalM)}</span>
    <span class="meta-pill">🏦 Bank: ${fmtM(bankM)}</span>
    <span class="meta-pill">📊 Total: ${fmtM(totalM + bankM)}</span>`;
}

// ── Transfer block HTML (appended at bottom of card) ───────────────────────
function buildTransferBlock(ta) {
  if (!ta) return '';

  const affordClass = ta.affordable ? 'transfer-ok' : 'transfer-warn';

  const sellChips = ta.to_sell?.length
    ? ta.to_sell.map(r => `<span class="tx-chip tx-sell">${r.full_name}<span class="tx-price">${fmtM(r.price_M)}</span></span>`).join('')
    : '<span class="tx-none">–</span>';

  const buyChips = ta.to_buy?.length
    ? ta.to_buy.map(r => `<span class="tx-chip tx-buy">${r.full_name}<span class="tx-price">${fmtM(r.price_M)}</span></span>`).join('')
    : '<span class="tx-none">–</span>';

  const header = ta.n_transfers === 0
    ? '<span class="tx-ok-text">✓ Ingen udskiftninger nødvendige</span>'
    : `<span class="tx-count">${ta.n_transfers} udskiftning${ta.n_transfers > 1 ? 'er' : ''}</span>`;

  // Financial breakdown
  const hasFee  = ta.fee_M != null;
  const feeRow  = hasFee
    ? `<div class="tx-fin-row"><span>Transfergebyr (1%)</span><span class="cost-pos">−${fmtM(ta.fee_M)}</span></div>`
    : '';
  const proceedsRow = ta.proceeds_M > 0
    ? `<div class="tx-fin-row"><span>Salgsindtægt</span><span class="cost-neg">+${fmtM(ta.proceeds_M)}</span></div>`
    : '';
  const buyRow  = ta.face_buy_M != null
    ? `<div class="tx-fin-row"><span>Købspris (listepris)</span><span class="cost-pos">−${fmtM(ta.face_buy_M)}</span></div>`
    : `<div class="tx-fin-row"><span>Køb inkl. gebyr</span><span class="cost-pos">−${fmtM(ta.buy_cost_M)}</span></div>`;

  const balanceClass = ta.affordable ? 'cost-neg' : 'cost-pos';
  const balanceMark  = ta.affordable ? '✓' : '⚠️';
  const balanceRow   = `
    <div class="tx-fin-row tx-fin-total ${ta.affordable ? '' : 'tx-unaffordable'}">
      <span>Bank efter</span>
      <span class="${balanceClass}">${fmtM(ta.balance_after_M)} ${balanceMark}</span>
    </div>`;

  return `
    <div class="transfer-block ${affordClass}">
      <div class="tx-header">${header}</div>
      ${ta.n_transfers > 0 ? `
      <div class="tx-riders">
        <div class="tx-row"><span class="tx-label">Sælg</span><div class="tx-chips">${sellChips}</div></div>
        <div class="tx-row"><span class="tx-label">Køb</span><div class="tx-chips">${buyChips}</div></div>
      </div>
      <div class="tx-fin">
        ${proceedsRow}
        ${buyRow}
        ${feeRow}
        ${balanceRow}
      </div>` : ''}
    </div>`;
}

// ── Team card builder (shared by 3-team grid and best-team grid) ───────────
function buildTeamCard(team) {
  const lm           = LABEL_META[team.label] || { dk: team.label.toUpperCase(), desc: '' };
  const ass          = team.assessment || {};
  const captain      = team.team.find(r => r.is_captain) || team.team[0];
  const sortedRiders = [...team.team].sort((a, b) => b.expected_pts - a.expected_pts);
  const ta           = team.transfer_analysis;

  const ridersHtml = sortedRiders.map(r => `
    <div class="rider-row">
      <div>
        <div class="rider-name ${r.is_captain ? 'is-captain' : ''}">${r.full_name}</div>
        <div class="rider-team">${r.team}</div>
      </div>
      <div class="rider-price ${priceClass(r.price)}">${r.price.toFixed(1)}M</div>
      <div class="rider-pts">${fmtK(r.expected_pts)}</div>
      <div></div>
    </div>`).join('');

  const riskClass = { 'Lav': 'risk-low', 'Middel': 'risk-mid', 'Høj': 'risk-high' }[ass.risk_profile] || '';

  const card = document.createElement('div');
  card.className = `team-card${team.label === 'best' ? ' team-card-best' : ''}`;
  card.innerHTML = `
    <div class="team-header ${team.label}">
      <div>
        <div class="team-label">${lm.dk}</div>
        <div style="font-size:0.78rem;color:var(--muted);margin-top:2px">${lm.desc}</div>
      </div>
      <div style="text-align:right">
        <div class="team-score">${fmtK(team.expected_pts)}</div>
        <div class="team-score-sub">forv. score inkl. kaptajn</div>
      </div>
    </div>

    <div class="team-meta">
      <div class="meta-pill">💰 ${ass.total_cost_M}M brugt</div>
      <div class="meta-pill">💵 ${fmtM(ass.budget_left_M)} tilbage</div>
      <div class="meta-pill">🏆 ~${ass.est_top15_count} i top 15</div>
      <div class="meta-pill">+${fmtK(ass.est_etapebonus)} etapebonus</div>
    </div>

    <div class="captain-strip">
      <div class="cap-label">★ Kaptajn</div>
      <div class="cap-name">${captain.full_name} (${captain.team})</div>
      <div class="cap-why">${captain.reasoning || '–'}</div>
    </div>

    <div class="rider-list">
      <div class="rider-row" style="font-size:0.7rem;color:var(--muted);padding-bottom:4px">
        <div>Rytter</div><div>Pris</div><div>Forv.</div><div></div>
      </div>
      ${ridersHtml}
    </div>

    <div class="assessment-bar">
      <div class="assess-item">
        <div class="assess-val ${riskClass}">${ass.risk_profile}</div>
        <div class="assess-lbl">Risiko</div>
      </div>
      <div class="assess-item">
        <div class="assess-val">${ass.n_premium_riders}</div>
        <div class="assess-lbl">Premium (≥8M)</div>
      </div>
      <div class="assess-item">
        <div class="assess-val">${ass.n_budget_riders}</div>
        <div class="assess-lbl">Budget (≤4M)</div>
      </div>
    </div>

    ${buildTransferBlock(ta)}`;

  return card;
}

// ── Render 3 strategy teams ────────────────────────────────────────────────
function renderTeams(teams) {
  const grid = document.getElementById('teamsGrid');
  grid.innerHTML = '';
  teams.forEach(team => grid.appendChild(buildTeamCard(team)));
}

// ── Render best-possible team ──────────────────────────────────────────────
function renderBestTeam(bestTeam) {
  if (!bestTeam) return;
  document.getElementById('bestTeamSection').style.display = 'block';
  const grid = document.getElementById('bestTeamGrid');
  grid.innerHTML = '';
  grid.appendChild(buildTeamCard(bestTeam));
}

// ── VeloScore table ────────────────────────────────────────────────────────
function renderVeloScore(predictions) {
  if (!predictions?.length) return;
  document.getElementById('veloscoreSection').style.display = 'block';
  const tbody = document.getElementById('vsBody');
  tbody.innerHTML = predictions.map(p => `
    <tr>
      <td>${p.rank}</td>
      <td><strong>${p.rider}</strong></td>
      <td class="total-val">${fmt(p.total)}</td>
      <td class="veloscore-val">${p.veloscore != null ? p.veloscore.toFixed(1) : '–'}</td>
      <td class="stars">${stars(p.stars)}</td>
    </tr>`).join('');
}

// ── Top picks table ────────────────────────────────────────────────────────
function renderTopPicks(picks) {
  if (!picks?.length) return;
  document.getElementById('picksSection').style.display = 'block';

  // Determine the discipline key from first pick that has it
  const firstKey = picks.find(p => p.disc_key)?.disc_key;
  const discLabel = firstKey ? (DISC_LABELS[firstKey] || firstKey) : 'Disciplin';

  // Update table header dynamically
  const thDisc = document.getElementById('thDisc');
  if (thDisc) thDisc.textContent = discLabel;

  const tbody = document.getElementById('picksBody');
  tbody.innerHTML = picks.slice(0, 25).map((p, i) => {
    const coVal = p.disc_raw != null
      ? `<span class="co-val">${p.disc_raw.toFixed(0)}</span>`
      : '<span class="co-val co-missing">–</span>';

    return `
    <tr>
      <td>${i + 1}</td>
      <td><strong>${p.full_name}</strong></td>
      <td>${p.team}</td>
      <td class="${priceClass(p.price)}">${p.price.toFixed(1)}M</td>
      <td style="font-weight:600;color:var(--green)">${fmtK(p.expected_pts)}</td>
      <td>${signalBar(p.signal_scores, p.disc_key, p.disc_raw, p.form_score)}</td>
      <td class="co-cell">${coVal}</td>
      <td style="font-size:0.78rem;color:var(--muted)">${p.reasoning || '–'}</td>
    </tr>`;
  }).join('');
}

// ── Init ───────────────────────────────────────────────────────────────────
async function init() {
  try {
    const data = await loadData();

    renderStageBadge(data.stage, data.stage_type);
    renderGeneratedAt(data.generated);
    renderCurrentTeam(data.current_team);
    renderTeams(data.teams || []);
    renderBestTeam(data.best_team);
    renderVeloScore(data.veloscore || []);
    renderTopPicks(data.top_picks || []);

  } catch (err) {
    document.getElementById('teamsGrid').innerHTML =
      `<div style="grid-column:1/-1;text-align:center;padding:48px;color:#E53935">
        ⚠️ ${err.message}<br>
        <small style="color:#7B82A0;margin-top:8px;display:block">
          Kør <code>python run_daily.py</code> og push til GitHub for at opdatere.
        </small>
      </div>`;
  }
}

init();
