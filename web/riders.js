/* Rytterdatabase — diagnostik-tabel med alle baggrundssignaler */

const RD_URL = './data/rider_database.json';

let RD_DATA = null;
let rdSortBy  = 'name';
let rdSortDir = 1;     // 1 = asc, -1 = desc
let rdFilter  = '';
let rdRaceFilter = '';
let rdGapFilter  = '';

const CO_KEYS   = ['AVG', 'SPR', 'MTN', 'ITT', 'HLL', 'COB', 'GC'];
const SPEC_KEYS = ['climber', 'sprint', 'tt', 'hills', 'onedayraces', 'gc'];
const FORM_KEYS = ['overall', 'sprint', 'mountain', 'hilly', 'tt'];

function rdEsc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function rdNum(v, decimals = 0) {
  if (v == null || v === '') return '<span class="rd-na">–</span>';
  return `<span class="rd-num">${Number(v).toFixed(decimals)}</span>`;
}
function rdFlag(b) {
  return b ? '<span class="rd-flag rd-yes">✓</span>' : '<span class="rd-flag rd-no">✗</span>';
}
function rdRaceBadges(races) {
  const ALL = ['giro', 'dauphine', 'tdf'];
  const LABELS = { giro: 'G', dauphine: 'D', tdf: 'T' };
  return `<div class="rd-races">${ALL.map(r =>
    `<span class="rd-race-badge ${races.includes(r) ? 'active-' + r : ''}" title="${r}">${LABELS[r]}</span>`
  ).join('')}</div>`;
}

async function rdLoadJSON(url) {
  const r = await fetch(url + '?t=' + Date.now());
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${url}`);
  return r.json();
}

async function rdInit() {
  try {
    RD_DATA = await rdLoadJSON(RD_URL);
  } catch (e) {
    document.getElementById('rdWrap').innerHTML =
      `<div class="info-box" style="text-align:center;padding:60px;color:var(--muted)">
        <div style="font-size:2rem;margin-bottom:12px">⚠️</div>
        <div style="color:#E53935;margin-bottom:8px">${rdEsc(e.message)}</div>
        <div style="font-size:0.8rem">Kør: <code>python build_rider_database.py</code></div>
       </div>`;
    return;
  }
  rdRenderStats();
  rdRenderTable();
  rdSetupControls();
}

function rdRenderStats() {
  const c = RD_DATA.coverage;
  const genTime = RD_DATA.generated
    ? new Date(RD_DATA.generated).toLocaleString('da-DK', {dateStyle:'short', timeStyle:'short'})
    : '';
  document.getElementById('rdStats').textContent =
    `${RD_DATA.n_riders} ryttere · CO ${c.cyclingoracle} · PCS ${c.pcs_form} · ` +
    `Specialties ${c.pcs_specialties} · Langtid ${c.pcs_form_long} · Beregnet ${genTime}`;
}

function rdSetupControls() {
  document.getElementById('rdFilter')?.addEventListener('input', e => {
    rdFilter = e.target.value.toLowerCase().trim();
    rdRenderTable();
  });
  document.getElementById('rdRaceFilter')?.addEventListener('change', e => {
    rdRaceFilter = e.target.value;
    rdRenderTable();
  });
  document.getElementById('rdGapFilter')?.addEventListener('change', e => {
    rdGapFilter = e.target.value;
    rdRenderTable();
  });
}

function rdGetFilteredSorted() {
  let riders = [...(RD_DATA?.riders ?? [])];

  if (rdFilter) {
    riders = riders.filter(r =>
      r.name.toLowerCase().includes(rdFilter) ||
      (r.team || '').toLowerCase().includes(rdFilter)
    );
  }
  if (rdRaceFilter) {
    riders = riders.filter(r => r.races.includes(rdRaceFilter));
  }
  if (rdGapFilter) {
    const gapMap = {
      no_co:   r => !r.has_co,
      no_pcs:  r => !r.has_pcs,
      no_spec: r => !r.has_specialties,
      no_long: r => !r.has_long_form,
    };
    riders = riders.filter(gapMap[rdGapFilter]);
  }

  riders.sort((a, b) => {
    let va, vb;
    if (rdSortBy === 'name') { va = a.name; vb = b.name; return va.localeCompare(vb, 'da') * rdSortDir; }
    if (rdSortBy === 'team') { va = a.team || ''; vb = b.team || ''; return va.localeCompare(vb, 'da') * rdSortDir; }
    if (rdSortBy.startsWith('co_'))   { const k = rdSortBy.slice(3);   va = a.co_ratings?.[k] ?? -1;       vb = b.co_ratings?.[k] ?? -1; }
    else if (rdSortBy.startsWith('spec_')) { const k = rdSortBy.slice(5); va = a.pcs_specialties?.[k] ?? -1; vb = b.pcs_specialties?.[k] ?? -1; }
    else if (rdSortBy.startsWith('fs_'))   { const k = rdSortBy.slice(3); va = a.form_short?.[k] ?? -1;     vb = b.form_short?.[k] ?? -1; }
    else if (rdSortBy.startsWith('fl_'))   { const k = rdSortBy.slice(3); va = a.form_long?.[k] ?? -1;      vb = b.form_long?.[k] ?? -1; }
    else { va = 0; vb = 0; }
    return (va - vb) * rdSortDir;
  });

  return riders;
}

function rdRenderTable() {
  const wrap = document.getElementById('rdWrap');
  if (!RD_DATA || !wrap) return;

  const riders = rdGetFilteredSorted();

  const sortCls = (key) => rdSortBy === key ? ` sort-${rdSortDir === 1 ? 'asc' : 'desc'}` : '';

  let header = `<thead><tr>
    <th class="col-name${sortCls('name')}" data-sort="name">Rytter</th>
    <th class="col-team${sortCls('team')}" data-sort="team">Hold</th>
    <th>Løb</th>`;

  for (const k of CO_KEYS) {
    header += `<th class="rd-section-divider${sortCls('co_'+k)}" data-sort="co_${k}" title="CyclingOracle ${k}">CO ${k}</th>`;
  }
  for (const k of SPEC_KEYS) {
    header += `<th class="${k==='climber'?'rd-section-divider':''}${sortCls('spec_'+k)}" data-sort="spec_${k}" title="PCS specialty (karriere UCI-point): ${k}">Sp ${k.slice(0,4)}</th>`;
  }
  for (const k of FORM_KEYS) {
    header += `<th class="${k==='overall'?'rd-section-divider':''}${sortCls('fs_'+k)}" data-sort="fs_${k}" title="Kortsigtet form: ${k}">FK ${k.slice(0,4)}</th>`;
  }
  for (const k of FORM_KEYS) {
    header += `<th class="${k==='overall'?'rd-section-divider':''}${sortCls('fl_'+k)}" data-sort="fl_${k}" title="Langsigtet form: ${k}">FL ${k.slice(0,4)}</th>`;
  }
  header += `<th class="rd-section-divider" title="Har data fra denne kilde?">CO✓</th>
             <th title="Har PCS form?">PCS✓</th>
             <th title="Har PCS specialties?">Sp✓</th>
             <th title="Har langtids-form?">FL✓</th>
             <th title="Sidste registrerede resultat">Senest</th>
  </tr></thead>`;

  let body = '<tbody>';
  for (const r of riders) {
    body += `<tr>
      <td class="col-name" title="${rdEsc(r.name)}">${rdEsc(r.name)}</td>
      <td class="col-team">${rdEsc(r.team)}</td>
      <td>${rdRaceBadges(r.races)}</td>`;

    for (const k of CO_KEYS) {
      body += `<td class="rd-section-divider">${rdNum(r.co_ratings?.[k])}</td>`;
    }
    for (const k of SPEC_KEYS) {
      body += `<td class="${k==='climber'?'rd-section-divider':''}">${rdNum(r.pcs_specialties?.[k])}</td>`;
    }
    for (const k of FORM_KEYS) {
      body += `<td class="${k==='overall'?'rd-section-divider':''}">${rdNum(r.form_short?.[k], 1)}</td>`;
    }
    for (const k of FORM_KEYS) {
      body += `<td class="${k==='overall'?'rd-section-divider':''}">${rdNum(r.form_long?.[k], 1)}</td>`;
    }
    body += `<td class="rd-section-divider">${rdFlag(r.has_co)}</td>
             <td>${rdFlag(r.has_pcs)}</td>
             <td>${rdFlag(r.has_specialties)}</td>
             <td>${rdFlag(r.has_long_form)}</td>
             <td style="font-size:0.7rem;color:var(--muted)">${rdEsc(r.last_result_date || '–')}</td>
    </tr>`;
  }
  body += '</tbody>';

  wrap.innerHTML = `<table class="rd-table">${header}${body}</table>`;

  wrap.querySelectorAll('th[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (rdSortBy === key) rdSortDir = -rdSortDir;
      else { rdSortBy = key; rdSortDir = 1; }
      rdRenderTable();
    });
  });
}

rdInit();
