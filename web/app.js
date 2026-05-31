/* TdF Manager 2026 — Frontend logic */

const DATA_URL = './data/recommendations.json';

const STAGE_TYPE_META = {
  sprint:   { icon: '⚡', name: 'Sprintereape',       color: '#FFD700' },
  mountain: { icon: '⛰️', name: 'Bjergetape',         color: '#00C853' },
  tt:       { icon: '⏱️', name: 'Enkeltstart (TT)',    color: '#1E88E5' },
  hilly:    { icon: '〰️', name: 'Kuperet / Punch',    color: '#FF7043' },
  cobbled:  { icon: '🧱', name: 'Brosteenseape',      color: '#AB47BC' },
  unknown:  { icon: '❓', name: 'Ukendt etapetype',   color: '#9E9E9E' },
};

const LABEL_META = {
  safe:   { dk: 'SIKKER',  desc: 'Maksimér forventet score med lave risici' },
  value:  { dk: 'VÆRDI',   desc: 'Budget-ryttere frigiver penge til en stjerne' },
  attack: { dk: 'ANGREB',  desc: 'Høj variance — potentiel overraskelse vinder' },
};

function fmt(n) {
  if (n == null || isNaN(n)) return '–';
  return n.toLocaleString('da-DK');
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
function signalBar(signals) {
  const keys = ['veloscore', 'odds', 'discipline', 'form'];
  const segs = keys.map(k => {
    const v = signals?.[k] ?? 0;
    const filled = v > 0.3 ? 'filled' : '';
    return `<div class="signal-segment ${filled}" title="${k}: ${(v*100).toFixed(0)}%"></div>`;
  }).join('');
  return `<div class="signal-bar">${segs}</div>`;
}

async function loadData() {
  const res = await fetch(DATA_URL + '?t=' + Date.now());
  if (!res.ok) throw new Error('Kunne ikke hente data: ' + res.status);
  return await res.json();
}

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

function renderTeams(teams) {
  const grid = document.getElementById('teamsGrid');
  grid.innerHTML = '';

  teams.forEach((team, i) => {
    const lm = LABEL_META[team.label] || { dk: team.label.toUpperCase(), desc: '' };
    const ass = team.assessment || {};
    const captain = team.team.find(r => r.is_captain) || team.team[0];
    const sortedRiders = [...team.team].sort((a, b) => b.expected_pts - a.expected_pts);

    // Riders HTML
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

    // Risk colour
    const riskClass = { 'Lav': 'risk-low', 'Middel': 'risk-mid', 'Høj': 'risk-high' }[ass.risk_profile] || '';

    const card = document.createElement('div');
    card.className = 'team-card';
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
        <div class="meta-pill">💵 ${ass.budget_left_M}M tilbage</div>
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
          <div class="assess-lbl">Premium (≥9M)</div>
        </div>
        <div class="assess-item">
          <div class="assess-val">${ass.n_budget_riders}</div>
          <div class="assess-lbl">Budget (≤4M)</div>
        </div>
      </div>`;

    grid.appendChild(card);
  });
}

function renderVeloScore(predictions) {
  if (!predictions || predictions.length === 0) return;
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

function renderTopPicks(picks) {
  if (!picks || picks.length === 0) return;
  document.getElementById('picksSection').style.display = 'block';
  const tbody = document.getElementById('picksBody');
  tbody.innerHTML = picks.slice(0, 25).map((p, i) => `
    <tr>
      <td>${i + 1}</td>
      <td><strong>${p.full_name}</strong></td>
      <td>${p.team}</td>
      <td class="${priceClass(p.price)}">${p.price.toFixed(1)}M</td>
      <td style="font-weight:600;color:var(--green)">${fmtK(p.expected_pts)}</td>
      <td>${signalBar(p.signal_scores)}</td>
      <td style="font-size:0.78rem;color:var(--muted)">${p.reasoning || '–'}</td>
    </tr>`).join('');
}

async function init() {
  try {
    const data = await loadData();

    renderStageBadge(data.stage, data.stage_type);
    renderGeneratedAt(data.generated);
    renderTeams(data.teams || []);
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
