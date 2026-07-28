/* ML Validering — Placement + Holdet LightGBM modeller
   Primær kilde: web/data/placement_ml_validation.json  (placement_model.lgbm)
   Sekundær:     web/data/holdet_ml_validation.json     (holdet_model.lgbm)

   Arkitektur-konstanter (CO-blending, signalvægte) er hardkodet her og
   afspejler de aktuelle værdier i src/predictor.py + src/ml_signal.py.
   Opdatér disse hvis du ændrer konstanterne i Python-koden.
*/

const PLACEMENT_URL = './data/placement_ml_validation.json';
const HOLDET_URL    = './data/holdet_ml_validation.json';

// ── Arkitektur-konstanter (src/predictor.py + src/ml_signal.py) ─────────────

// DEFAULT_WEIGHTS (predictor.py linje ~37)
const SIGNAL_WEIGHTS = [
  { key: 'form',       label: 'PCS Form (90-dages)',         w: 0.22, color: 'var(--yellow)', note: 'r²=0.22 mod faktiske Holdet-point' },
  { key: 'discipline', label: 'Disciplin (CO-blanding)',     w: 0.20, color: 'var(--green)',  note: 'CO-ratings blandet pr. etapetype' },
  { key: 'veloscore',  label: 'VeloScore (odds-baseret)',    w: 0.20, color: 'var(--blue)',   note: 'Ekstern betalingstjeneste, bruges hvis tilgængelig' },
  { key: 'pcs_rank',   label: 'PCS Rangering (12-mdr.)',     w: 0.12, color: '#c792ea',       note: 'UCI-point de seneste 12 måneder' },
  { key: 'odds',       label: 'Betting-odds',                w: 0.18, color: '#f97316',       note: 'Sandsynlighed fra oddsmarkeder' },
  { key: 'ml',         label: 'ML-model score',              w: 0.08, color: '#64748b',       note: 'Placement-model via CO-blending' },
];

// STAGE_DISCIPLINE_BLEND (predictor.py linje ~75)
const CO_BLEND = {
  sprint:   { SPR: 0.85, HLL: 0.15 },
  mountain: { MTN: 1.00 },
  hilly:    { HLL: 0.90, SPR: 0.10 },
  tt:       { ITT: 1.00 },
  ttt:      { ITT: 1.00 },
  cobbled:  { COB: 1.00 },
};

// _CO_W: CO-vægt vs ML-vægt i placement-blending (ml_signal.py linje ~929)
const CO_VS_ML = {
  mountain: { co: 0.65, ml: 0.35 },
  sprint:   { co: 0.60, ml: 0.40 },
  tt:       { co: 0.70, ml: 0.30 },
  ttt:      { co: 0.70, ml: 0.30 },
  hilly:    { co: 0.50, ml: 0.50 },
};

// AVG_MAX_K: max Holdet-point til denormalisering (predictor.py linje ~111)
const AVG_MAX_K = {
  sprint: 400, mountain: 427, hilly: 430, tt: 399, cobbled: 500, gc: 630,
};

const CO_KEY_LABEL = { SPR: 'Sprint', MTN: 'Bjerg', HLL: 'Kuperet', ITT: 'Enkeltstart', COB: 'Brosten', GC: 'GC', AVG: 'Gennemsnit' };

// ── Feature names ───────────────────────────────────────────────────────────
const FEATURE_NAMES = {
  gt_form_5:      'In-race GT-form: snit placering (5 etaper)',
  xrace_form_10:  'Cross-race form: snit placering (10 etaper)',
  gt_form_10:     'In-race GT-form: snit placering (10 etaper)',
  gt_wins_so_far: 'GT etapesejre hidtil i løbet',
  co_spr:         'CyclingOracle: Sprint',
  co_mtn:         'CyclingOracle: Bjerg',
  co_hll:         'CyclingOracle: Kuperet',
  co_itt:         'CyclingOracle: Enkeltstart',
  co_gc:          'CyclingOracle: GC',
  co_cob:         'CyclingOracle: Brosten',
  form_overall:   'PCS Form: Overordnet (90 dage)',
  form_sprint:    'PCS Form: Sprint',
  form_mountain:  'PCS Form: Bjerg',
  form_hilly:     'PCS Form: Kuperet',
  form_tt:        'PCS Form: TT',
  spec_climber:   'PCS Specialties: Klatrer (karriere UCI-point)',
  spec_sprint:    'PCS Specialties: Sprint',
  spec_tt:        'PCS Specialties: Enkeltstart',
  spec_hills:     'PCS Specialties: Bakker',
  is_sprint:      'Etapetype = Sprint',
  is_mountain:    'Etapetype = Bjerg',
  is_hilly:       'Etapetype = Kuperet',
  is_tt:          'Etapetype = Enkeltstart',
};

const RACE_LABEL = { giro: "Giro d'Italia", tdf: 'Tour de France', vuelta: 'Vuelta a España' };
const RACE_COLOR = { giro: '#ff8a80', tdf: '#58a6ff', vuelta: '#3fb950' };
const STYPE_LABEL = { sprint: 'Sprint', mountain: 'Bjerg', hilly: 'Kuperet', tt: 'Enkeltstart' };
const STYPE_COLOR = { sprint: '#58a6ff', mountain: '#3fb950', hilly: '#f0a500', tt: '#c792ea' };
const STYPES = ['sprint', 'mountain', 'hilly', 'tt'];

function mlEsc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function spearmanColor(v) {
  if (v == null) return 'var(--muted)';
  if (v >= 0.65) return 'var(--green)';
  if (v >= 0.50) return '#6fcf5a';
  if (v >= 0.40) return 'var(--yellow)';
  return '#ff8a80';
}

function heatCell(v) {
  if (v == null) return { bg: 'var(--border)', fg: 'var(--muted)', text: '–' };
  if (v >= 0.75) return { bg: '#0d3320', fg: '#3fb950', text: v.toFixed(3) };
  if (v >= 0.65) return { bg: '#1a4d2e', fg: '#5ecf5a', text: v.toFixed(3) };
  if (v >= 0.55) return { bg: '#1f3d1a', fg: '#8fcf5a', text: v.toFixed(3) };
  if (v >= 0.45) return { bg: '#3d3210', fg: '#f0a500', text: v.toFixed(3) };
  if (v >= 0.35) return { bg: '#3d1f10', fg: '#e07040', text: v.toFixed(3) };
  return { bg: '#3d1010', fg: '#ff6b6b', text: v.toFixed(3) };
}

function fmtPct(v) { return v != null ? Math.round(v * 100) + '%' : '–'; }

// ── Init ────────────────────────────────────────────────────────────────────
let _placementData = null;
let _holdetData    = null;
let _activeModel   = 'placement';

async function mlInit() {
  const container = document.getElementById('mlContent');
  if (!container) return;

  try {
    const [pRes, hRes] = await Promise.all([
      fetch(PLACEMENT_URL, { cache: 'no-cache' }),
      fetch(HOLDET_URL,    { cache: 'no-cache' }),
    ]);
    _placementData = pRes.ok ? await pRes.json() : null;
    _holdetData    = hRes.ok ? await hRes.json() : null;
  } catch (e) {
    container.innerHTML = mlEmptyState(e.message);
    return;
  }

  if (!_placementData && !_holdetData) {
    container.innerHTML = mlEmptyState('Ingen valideringsdata fundet');
    return;
  }

  container.innerHTML = mlRender();
}

function mlEmptyState(errMsg) {
  return `
<div class="ml-empty">
  <div style="font-size:2.2rem;margin-bottom:14px">⚙️</div>
  <div style="font-size:1rem;font-weight:700;margin-bottom:8px;color:var(--text)">ML-validering ikke tilgængelig</div>
  <div style="font-size:0.82rem;color:var(--muted);max-width:480px;margin:0 auto;line-height:1.7">
    ${mlEsc(errMsg)}<br><br>
    Kør for at generere:<br>
    <code style="display:block;margin-top:8px;padding:10px 14px;background:var(--card);border-radius:6px;text-align:left;font-size:0.78rem">
      python scripts/train/build_placement_training_data.py<br>
      python scripts/train/train_placement_model.py
    </code>
  </div>
</div>`;
}

// ── Render ──────────────────────────────────────────────────────────────────
function mlRender() {
  const d = _activeModel === 'placement' ? _placementData : _holdetData;
  if (!d) return mlEmptyState('Valideringsdata for denne model mangler');

  return `
${renderModelSelector()}
${renderArchitecture()}
<hr style="border:none;border-top:1px solid var(--border);margin:20px 0">
${renderModelHeader(d)}
${renderSummary(d)}
${renderLoro(d)}
${renderHeatmap(d)}
${renderStageBreakdown(d)}
${renderFeatureImportance(d)}
${renderContext(d)}`;
}

// ── Model selector ───────────────────────────────────────────────────────────
function renderModelSelector() {
  const pDate = _placementData?.generated
    ? new Date(_placementData.generated).toLocaleDateString('da-DK', { day:'2-digit', month:'2-digit' })
    : '–';
  const hDate = _holdetData?.generated
    ? new Date(_holdetData.generated).toLocaleDateString('da-DK', { day:'2-digit', month:'2-digit' })
    : '–';

  return `
<div style="display:flex;align-items:center;gap:8px;margin-bottom:18px;flex-wrap:wrap">
  <span style="font-size:0.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;white-space:nowrap">Vis model:</span>
  <button onclick="mlSwitchModel('placement')" id="ml-btn-placement"
    style="padding:5px 14px;border-radius:20px;border:1px solid ${_activeModel==='placement'?'var(--yellow)':'var(--border)'};
           background:${_activeModel==='placement'?'rgba(240,165,0,0.12)':'transparent'};
           color:${_activeModel==='placement'?'var(--yellow)':'var(--muted)'};
           font-size:0.8rem;font-weight:700;cursor:pointer">
    Placement-model
    <span style="font-size:0.7rem;font-weight:400;opacity:0.7">(primær · ${pDate})</span>
  </button>
  <button onclick="mlSwitchModel('holdet')" id="ml-btn-holdet"
    style="padding:5px 14px;border-radius:20px;border:1px solid ${_activeModel==='holdet'?'var(--blue)':'var(--border)'};
           background:${_activeModel==='holdet'?'rgba(88,166,255,0.12)':'transparent'};
           color:${_activeModel==='holdet'?'var(--blue)':'var(--muted)'};
           font-size:0.8rem;font-weight:700;cursor:pointer">
    Holdet-model
    <span style="font-size:0.7rem;font-weight:400;opacity:0.7">(sekundær · ${hDate})</span>
  </button>
</div>`;
}

window.mlSwitchModel = function(model) {
  _activeModel = model;
  document.getElementById('mlContent').innerHTML = mlRender();
};

// ── Architecture section ─────────────────────────────────────────────────────
function renderArchitecture() {
  // Signal ensemble weights
  const totalW = SIGNAL_WEIGHTS.reduce((s, sw) => s + sw.w, 0);
  const signalRows = SIGNAL_WEIGHTS
    .slice()
    .sort((a, b) => b.w - a.w)
    .map(sw => {
      const pct = Math.round(sw.w / totalW * 100);
      return `
      <div style="display:grid;grid-template-columns:200px 44px 1fr 1fr;gap:8px;align-items:center;margin-bottom:6px">
        <span style="font-size:0.78rem;color:var(--text);font-weight:600">${mlEsc(sw.label)}</span>
        <span style="font-size:0.85rem;font-weight:800;color:${sw.color};text-align:right">${Math.round(sw.w*100)}%</span>
        <div style="background:var(--border);border-radius:3px;height:8px;overflow:hidden">
          <div style="width:${pct}%;height:100%;background:${sw.color};border-radius:3px"></div>
        </div>
        <span style="font-size:0.68rem;color:var(--muted)">${mlEsc(sw.note)}</span>
      </div>`;
    }).join('');

  // CO blending table
  const stageOrder = ['sprint','mountain','hilly','tt','cobbled'];
  const coRows = stageOrder.map(st => {
    const blend = CO_BLEND[st] || {};
    const vs    = CO_VS_ML[st] || { co: 0.50, ml: 0.50 };
    const coStr = Object.entries(blend)
      .map(([k, v]) => `<span style="color:var(--green)">${CO_KEY_LABEL[k]||k}</span> ${Math.round(v*100)}%`)
      .join(' + ');
    const col = STYPE_COLOR[st] || 'var(--text)';
    return `
    <tr>
      <td style="padding:7px 10px;font-weight:700;color:${col};white-space:nowrap;border-bottom:1px solid var(--border)">
        ${mlEsc(STYPE_LABEL[st] || st)}
      </td>
      <td style="padding:7px 10px;font-size:0.75rem;border-bottom:1px solid var(--border)">${coStr}</td>
      <td style="padding:7px 10px;text-align:center;font-weight:700;color:var(--green);border-bottom:1px solid var(--border)">${Math.round(vs.co*100)}%</td>
      <td style="padding:7px 10px;text-align:center;font-weight:700;color:var(--yellow);border-bottom:1px solid var(--border)">${Math.round(vs.ml*100)}%</td>
      <td style="padding:7px 10px;text-align:center;font-size:0.72rem;color:var(--muted);border-bottom:1px solid var(--border)">${AVG_MAX_K[st] || '–'}K</td>
    </tr>`;
  }).join('');

  return `
<div class="ml-card" style="margin-bottom:16px;border-left:3px solid var(--green)">
  <div class="ml-card-title">Modelarkitektur — 3-lags pipeline</div>

  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:18px">
    <div style="padding:12px;background:var(--surface);border-radius:8px;border:1px solid var(--border);border-top:3px solid var(--green)">
      <div style="font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.4px;color:var(--green);margin-bottom:8px">Lag 1: Signaler</div>
      <div style="font-size:0.72rem;color:var(--muted);line-height:1.6">
        6 signaler kombineres: <strong style="color:var(--text)">VeloScore</strong>,
        <strong style="color:var(--text)">Odds</strong>,
        <strong style="color:var(--text)">CO-Disciplin</strong>,
        <strong style="color:var(--text)">PCS Form</strong>,
        <strong style="color:var(--text)">ML-placering</strong>,
        <strong style="color:var(--text)">PCS Rangering</strong>
      </div>
    </div>
    <div style="padding:12px;background:var(--surface);border-radius:8px;border:1px solid var(--border);border-top:3px solid var(--yellow)">
      <div style="font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.4px;color:var(--yellow);margin-bottom:8px">Lag 2: Placement ML</div>
      <div style="font-size:0.72rem;color:var(--muted);line-height:1.6">
        LightGBM regression: norm_pos (1.0=vinder).
        CO-blanding (disciplin-specifik) + ML-score vægtes pr. etapetype.
        Sprintboost for CO_SPR ≥ 75 (max 1.5×).
      </div>
    </div>
    <div style="padding:12px;background:var(--surface);border-radius:8px;border:1px solid var(--border);border-top:3px solid var(--blue)">
      <div style="font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.4px;color:var(--blue);margin-bottom:8px">Lag 3: Holdet-est</div>
      <div style="font-size:0.72rem;color:var(--muted);line-height:1.6">
        norm_pos → stage_pts via kalibreret kurve → holdet_est (K-point).
        Sprint-etaper anvender sprintboost.
        GC-etaper bruger MTN-blanding (0.5×MTN + 0.3×GC + 0.2×HLL).
      </div>
    </div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">

    <div>
      <div style="font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:var(--muted);margin-bottom:10px">Signalvægte (src/predictor.py)</div>
      ${signalRows}
    </div>

    <div>
      <div style="font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:var(--muted);margin-bottom:10px">CO-blanding pr. etapetype (src/predictor.py + ml_signal.py)</div>
      <div style="overflow-x:auto">
        <table style="border-collapse:collapse;font-size:0.74rem;width:100%">
          <thead>
            <tr>
              <th style="padding:5px 10px;text-align:left;color:var(--muted);font-size:0.65rem;text-transform:uppercase;border-bottom:1px solid var(--border)">Type</th>
              <th style="padding:5px 10px;text-align:left;color:var(--muted);font-size:0.65rem;text-transform:uppercase;border-bottom:1px solid var(--border)">CO-disciplin blend</th>
              <th style="padding:5px 10px;text-align:center;color:var(--green);font-size:0.65rem;text-transform:uppercase;border-bottom:1px solid var(--border)">CO-vægt</th>
              <th style="padding:5px 10px;text-align:center;color:var(--yellow);font-size:0.65rem;text-transform:uppercase;border-bottom:1px solid var(--border)">ML-vægt</th>
              <th style="padding:5px 10px;text-align:center;color:var(--muted);font-size:0.65rem;text-transform:uppercase;border-bottom:1px solid var(--border)">Max K</th>
            </tr>
          </thead>
          <tbody>${coRows}</tbody>
        </table>
      </div>
      <div style="margin-top:8px;padding:8px 10px;background:rgba(255,215,0,0.06);border-radius:6px;border-left:2px solid var(--yellow);font-size:0.7rem;color:var(--muted);line-height:1.5">
        <strong style="color:var(--yellow)">Sprint-boost:</strong>
        Hvis CO_SPR ≥ 75: <span style="color:var(--text);font-family:monospace">boost = 1.0 + (SPR−75)/25 × 0.5</span>
        → SPR=75: 1.0×, SPR=87.5: 1.25×, SPR=100: 1.5×
      </div>
    </div>
  </div>
</div>`;
}

// ── Model header ─────────────────────────────────────────────────────────────
function renderModelHeader(d) {
  const genTime = d.generated
    ? new Date(d.generated).toLocaleString('da-DK', { dateStyle: 'short', timeStyle: 'short' })
    : '–';
  const isPrimary = _activeModel === 'placement';
  const modelColor = isPrimary ? 'var(--yellow)' : 'var(--blue)';
  const modelLabel = isPrimary ? 'Placement-model (primær)' : 'Holdet-model (sekundær)';
  const targetNote = isPrimary
    ? 'Target: norm_pos (1.0=vinder, 0.0=sidst) · denormaliseres til K-point'
    : 'Target: normaliserede Holdet-point (0–100) direkte';

  return `
<div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap">
  <span style="font-size:0.9rem;font-weight:700;color:${modelColor}">${mlEsc(modelLabel)}</span>
  <span class="ml-status-badge ready">Aktiv</span>
  <span style="font-size:0.72rem;color:var(--muted)">
    ${mlEsc(genTime)} · ${d.n_features} features · ${(d.n_train_total||0).toLocaleString('da-DK')} træningsrækker
  </span>
</div>
<div style="font-size:0.72rem;color:var(--muted);margin-bottom:14px;padding:8px 12px;background:var(--surface);border-radius:6px;border-left:2px solid ${modelColor}">
  ${mlEsc(targetNote)}
</div>`;
}

// ── Summary metrics ───────────────────────────────────────────────────────────
function renderSummary(d) {
  const avg = d.avg_spearman;
  const top5 = d.avg_top5_accuracy;
  const rmse = d.avg_rmse;
  const isPlacement = _activeModel === 'placement';

  const rmseNote = isPlacement
    ? `Normaliseret fejl på 0–1 skala (norm_pos). RMSE ≈ ${rmse?.toFixed(2)} svarer til ca. ${Math.round((rmse||0)*100)} placeringer fejl i et felt på 160.`
    : `Fejl på 0–100 Holdet-point skala. RMSE ≈ ${rmse?.toFixed(1)} svarer til ca. ${(rmse||0).toFixed(0)} normerede point fejl.`;

  return `
<div class="ml-grid" style="grid-template-columns:1fr 1fr 1fr;margin-bottom:16px">
  <div class="ml-card" style="text-align:center">
    <div class="ml-card-title">Spearman-korrelation (LORO gns.)</div>
    <div style="font-size:2.4rem;font-weight:800;color:${spearmanColor(avg)};margin:8px 0">
      ${avg != null ? avg.toFixed(3) : '–'}
    </div>
    <div style="font-size:0.72rem;color:var(--muted);line-height:1.55">
      Rang-korrelation pr. etape. 1.0 = perfekt rækkefølge.
      ${isPlacement
        ? `<br><strong style="color:var(--text)">0.64</strong> er stærkt for cykelsport — bjerg/tt er konsistente, sprint er kaotisk.`
        : `<br><strong style="color:var(--text)">0.51</strong> er solid for direkte Holdet-point-prediktion.`}
    </div>
    <div style="margin-top:10px;height:6px;background:var(--border);border-radius:3px;overflow:hidden">
      <div style="height:100%;width:${Math.round((avg||0)*100)}%;background:${spearmanColor(avg)};border-radius:3px"></div>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:0.65rem;color:var(--muted);margin-top:3px">
      <span>0</span><span>0.5</span><span>1</span>
    </div>
  </div>

  <div class="ml-card" style="text-align:center">
    <div class="ml-card-title">Top-5 Nøjagtighed</div>
    <div style="font-size:2.4rem;font-weight:800;color:${(top5||0) >= 0.5 ? 'var(--green)' : (top5||0) >= 0.4 ? 'var(--yellow)' : '#ff8a80'};margin:8px 0">
      ${fmtPct(top5)}
    </div>
    <div style="font-size:0.72rem;color:var(--muted);line-height:1.55">
      Andel etaper hvor ≥1 af modellens top-5 er i de faktiske top-5 Holdet-scorere.
      ${isPlacement
        ? `<br><strong style="color:var(--text)">67%</strong> = ca. 2/3 etaper rammer mindst én rigtig.`
        : `<br><strong style="color:var(--text)">43%</strong> — lavere pga. færre træningsdata (3 løb).`}
    </div>
    <div style="margin-top:10px;height:6px;background:var(--border);border-radius:3px;overflow:hidden">
      <div style="height:100%;width:${Math.round((top5||0)*100)}%;background:${(top5||0) >= 0.5 ? 'var(--green)' : 'var(--yellow)'};border-radius:3px"></div>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:0.65rem;color:var(--muted);margin-top:3px">
      <span>0%</span><span>50%</span><span>100%</span>
    </div>
  </div>

  <div class="ml-card" style="text-align:center">
    <div class="ml-card-title">RMSE</div>
    <div style="font-size:2.4rem;font-weight:800;color:var(--text);margin:8px 0">
      ${rmse != null ? rmse.toFixed(isPlacement ? 3 : 1) : '–'}
    </div>
    <div style="font-size:0.72rem;color:var(--muted);line-height:1.55">
      ${rmseNote}
    </div>
    <div style="font-size:0.65rem;color:var(--muted);margin-top:8px">Lavere er bedre</div>
  </div>
</div>`;
}

// ── LORO fold breakdown ───────────────────────────────────────────────────────
function renderLoro(d) {
  const loro = d.loro_results || [];
  const foldSizeNote = _activeModel === 'placement'
    ? '~100 etaper pr. fold (5 år × 3 GT × ~20 etaper)'
    : '21 etaper pr. fold (1 sæson per GT)';

  return `
<div class="ml-card" style="margin-bottom:16px">
  <div class="ml-card-title">Leave-One-Race-Out (LORO) — 3 fold</div>
  <div style="font-size:0.72rem;color:var(--muted);margin-bottom:14px;line-height:1.5">
    Hvert fold: modellen trænes på 2 af de 3 GT-løb og testes på det 3. (out-of-sample).
    ${mlEsc(foldSizeNote)}. Viser om modellen generaliserer på tværs af løb.
  </div>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:14px">
    ${loro.map(r => {
      const race  = r.val_race;
      const col   = RACE_COLOR[race] || 'var(--text)';
      const label = RACE_LABEL[race] || race;
      const sp    = r.mean_spearman;
      const acc   = r.top5_accuracy;
      const isP   = _activeModel === 'placement';
      return `
    <div style="padding:14px;background:var(--surface);border-radius:8px;border:1px solid var(--border);border-top:3px solid ${col}">
      <div style="font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.4px;color:${col};margin-bottom:10px">
        Test: ${mlEsc(label)}
      </div>
      <div class="ml-metric-row">
        <span class="ml-metric-label" style="font-size:0.72rem">Spearman</span>
        <span class="ml-metric-val" style="color:${spearmanColor(sp)}">${sp != null ? sp.toFixed(3) : '–'}</span>
      </div>
      <div class="ml-metric-row">
        <span class="ml-metric-label" style="font-size:0.72rem">Top-5 acc.</span>
        <span class="ml-metric-val">${fmtPct(acc)}</span>
      </div>
      <div class="ml-metric-row">
        <span class="ml-metric-label" style="font-size:0.72rem">RMSE</span>
        <span class="ml-metric-val">${r.rmse != null ? r.rmse.toFixed(isP ? 3 : 1) : '–'}</span>
      </div>
      <div class="ml-metric-row">
        <span class="ml-metric-label" style="font-size:0.72rem">Etaper (val)</span>
        <span class="ml-metric-val">${r.n_stages || '–'}</span>
      </div>
      <div style="margin-top:10px;height:4px;background:var(--border);border-radius:2px;overflow:hidden">
        <div style="height:100%;width:${Math.round((sp||0)*100)}%;background:${col};border-radius:2px"></div>
      </div>
    </div>`;
    }).join('')}
  </div>
  <div style="font-size:0.68rem;color:var(--muted);line-height:1.55;padding-top:10px;border-top:1px solid var(--border)">
    ${_activeModel === 'placement'
      ? 'Placement-modellen er stabil på tværs af løb (0.627–0.651 Spearman). Bjerg og TT er konsekvent stærkest — CO-ratings er præcise for disse typer. Sprint er svageste type (0.53) pga. kaotisk feltdynamik.'
      : 'Holdet-modellen varierer mere (0.447–0.609) — færre træningsdata (3 løb × 21 etaper = 63 etaper pr. CV) giver mere variabel generalisering. Giro scorer bedst fordi GC-scoringsmønsteret er mere forudsigeligt.'}
  </div>
</div>`;
}

// ── Heatmap: stage type × LORO fold ─────────────────────────────────────────
function renderHeatmap(d) {
  const loro = d.loro_results || [];
  if (loro.length === 0) return '';

  function avgStype(key) {
    const vals = loro.map(r => r[key]).filter(v => v != null);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  }

  return `
<div class="ml-card" style="margin-bottom:16px">
  <div class="ml-card-title">Heatmap: Spearman pr. etapetype × LORO-fold</div>
  <div style="font-size:0.72rem;color:var(--muted);margin-bottom:14px;line-height:1.5">
    Viser præcis nøjagtighed for hvert scenarie. Grønne celler er gode (≥ 0.65), gule er acceptable (0.45–0.65), røde er svage (&lt; 0.45).
  </div>
  <div style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:0.78rem">
      <thead>
        <tr>
          <th style="padding:8px 12px;text-align:left;color:var(--muted);font-weight:600;border-bottom:1px solid var(--border);font-size:0.7rem;text-transform:uppercase;letter-spacing:0.4px">
            Etapetype<br><span style="font-size:0.6rem;font-weight:400">Spearman · Top-5%</span>
          </th>
          ${loro.map(r => {
            const col = RACE_COLOR[r.val_race] || 'var(--text)';
            const lbl = RACE_LABEL[r.val_race] || r.val_race;
            return `<th style="padding:8px 12px;text-align:center;color:${col};font-weight:700;border-bottom:1px solid var(--border);font-size:0.72rem">${mlEsc(lbl)}</th>`;
          }).join('')}
          <th style="padding:8px 12px;text-align:center;color:var(--muted);font-weight:600;border-bottom:1px solid var(--border);font-size:0.7rem;text-transform:uppercase;letter-spacing:0.4px">Gns.</th>
        </tr>
      </thead>
      <tbody>
        ${STYPES.map(st => {
          const col  = STYPE_COLOR[st];
          const lbl  = STYPE_LABEL[st];
          const avg  = avgStype(`spearman_${st}`);
          const avgC = heatCell(avg);
          return `
        <tr>
          <td style="padding:10px 12px;font-weight:700;color:${col};border-bottom:1px solid var(--border);white-space:nowrap">
            <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${col};margin-right:6px;vertical-align:middle"></span>
            ${mlEsc(lbl)}
          </td>
          ${loro.map(r => {
            const sp  = r[`spearman_${st}`];
            const acc = r[`top5acc_${st}`];
            const c   = heatCell(sp);
            return `<td style="padding:10px 12px;text-align:center;border-bottom:1px solid var(--border)">
              <div style="display:inline-block;padding:4px 8px;border-radius:5px;background:${c.bg};color:${c.fg};font-weight:700;font-size:0.82rem;min-width:48px">${c.text}</div>
              ${acc != null ? `<div style="font-size:0.65rem;color:var(--muted);margin-top:2px">${fmtPct(acc)} top-5</div>` : ''}
            </td>`;
          }).join('')}
          <td style="padding:10px 12px;text-align:center;border-bottom:1px solid var(--border)">
            <div style="display:inline-block;padding:4px 8px;border-radius:5px;background:${avgC.bg};color:${avgC.fg};font-weight:700;font-size:0.82rem;min-width:48px;border:1px solid ${col}40">${avgC.text}</div>
          </td>
        </tr>`;
        }).join('')}
      </tbody>
    </table>
  </div>
  <div style="font-size:0.68rem;color:var(--muted);margin-top:12px;display:flex;gap:14px;flex-wrap:wrap;align-items:center">
    <span style="font-weight:600">Spearman:</span>
    <span><span style="color:#3fb950">■</span> ≥ 0.75</span>
    <span><span style="color:#5ecf5a">■</span> 0.65–0.75</span>
    <span><span style="color:#8fcf5a">■</span> 0.55–0.65</span>
    <span><span style="color:#f0a500">■</span> 0.45–0.55</span>
    <span><span style="color:#e07040">■</span> 0.35–0.45</span>
    <span><span style="color:#ff6b6b">■</span> &lt; 0.35</span>
    <span style="margin-left:6px;color:var(--muted)">Top-5%: andel etaper med ≥1 hit</span>
  </div>
</div>`;
}

// ── Stage breakdown cards ─────────────────────────────────────────────────────
function renderStageBreakdown(d) {
  const loro = d.loro_results || [];

  function avgStype(key) {
    const vals = loro.map(r => r[key]).filter(v => v != null);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  }

  // Interpretation note per type
  const stypeNotes = {
    sprint:   'Svageste type — feltdynamik, vind og taktik dominerer. CO_SPR-boost kompenserer delvist.',
    mountain: 'Stærkeste type — GC-hierarkiet er stabilt og CO MTN-ratings er meget præcise.',
    hilly:    'Solid præstation — bakke-specialister dominerer (HLL 90% blend).',
    tt:       'God præstation — ITT-ratings er præcise, lille og stabilt felt pr. etape.',
  };

  return `
<div class="ml-card" style="margin-bottom:16px">
  <div class="ml-card-title">Præcision pr. etapetype — gns. over alle LORO-fold</div>
  <div style="font-size:0.72rem;color:var(--muted);margin-bottom:14px;line-height:1.5">
    Gennemsnit over alle 3 LORO-fold. Grøn ≥ 0.65 · Gul 0.45–0.65 · Rød &lt; 0.45.
  </div>
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px">
    ${STYPES.map(st => {
      const col = STYPE_COLOR[st];
      const lbl = STYPE_LABEL[st];
      const sp  = avgStype(`spearman_${st}`);
      const acc = avgStype(`top5acc_${st}`);
      const spC = spearmanColor(sp);
      const blend = CO_BLEND[st] || {};
      const blendStr = Object.entries(blend)
        .map(([k, v]) => `${CO_KEY_LABEL[k]||k} ${Math.round(v*100)}%`)
        .join(' · ');
      return `
    <div style="padding:12px;background:var(--surface);border-radius:8px;border:1px solid var(--border);border-top:3px solid ${col}">
      <div style="font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.4px;color:${col};margin-bottom:10px">${mlEsc(lbl)}</div>
      <div class="ml-metric-row">
        <span class="ml-metric-label" style="font-size:0.72rem">Spearman</span>
        <span class="ml-metric-val" style="color:${spC}">${sp != null ? sp.toFixed(3) : '–'}</span>
      </div>
      <div class="ml-metric-row">
        <span class="ml-metric-label" style="font-size:0.72rem">Top-5 acc.</span>
        <span class="ml-metric-val">${fmtPct(acc)}</span>
      </div>
      <div style="margin-top:10px;height:4px;background:var(--border);border-radius:2px;overflow:hidden">
        <div style="height:100%;width:${Math.round((sp||0)*100)}%;background:${spC};border-radius:2px"></div>
      </div>
      <div style="margin-top:8px;font-size:0.65rem;color:var(--muted);line-height:1.4">
        <strong>CO:</strong> ${mlEsc(blendStr)}
      </div>
    </div>`;
    }).join('')}
  </div>
  <div style="font-size:0.7rem;color:var(--muted);line-height:1.7;padding-top:10px;border-top:1px solid var(--border);display:grid;grid-template-columns:1fr 1fr;gap:10px">
    ${STYPES.map(st => {
      const col = STYPE_COLOR[st];
      return `<div><strong style="color:${col}">${STYPE_LABEL[st]}:</strong> ${mlEsc(stypeNotes[st]||'')}</div>`;
    }).join('')}
  </div>
</div>`;
}

// ── Feature importance ────────────────────────────────────────────────────────
function renderFeatureImportance(d) {
  const imps   = (d.feature_importances || []).slice(0, 15);
  const maxImp = imps[0]?.importance || 1;
  if (imps.length === 0) return '';

  return `
<div class="ml-card" style="margin-bottom:16px">
  <div class="ml-card-title">Feature Importance — hvad driver modellen?</div>
  <div style="font-size:0.72rem;color:var(--muted);margin-bottom:14px;line-height:1.55">
    Antal gange en feature bruges i LightGBM's beslutningstræer (top 15 af ${d.n_features}).
    ${_activeModel === 'placement'
      ? '<strong style="color:var(--yellow)">In-race rolling form</strong> (gt_form_5) dominerer klart — ryttere der allerede scorer godt i dette løb fortsætter typisk. CO-ratings vægter tungt for disciplin-specifikke etaper.'
      : '<strong style="color:var(--yellow)">CO-ratings og form</strong> driver modellen — direct Holdet-point-prediktion er mere afhængig af disciplin-kapacitet.'}
  </div>
  ${imps.map((f, i) => {
    const name = FEATURE_NAMES[f.feature] || f.feature;
    const pct  = Math.round((f.importance / maxImp) * 100);
    const isForm = f.feature.startsWith('gt_form') || f.feature.startsWith('xrace') || f.feature.startsWith('form_');
    const isCO   = f.feature.startsWith('co_');
    const isSpec = f.feature.startsWith('spec_');
    const barColor = isForm ? 'var(--yellow)' : isCO ? 'var(--green)' : isSpec ? '#c792ea' : 'var(--blue)';
    return `
    <div class="ml-bar-wrap">
      <div class="ml-bar-label">
        <span style="color:${barColor}">${i+1}. ${mlEsc(name)}</span>
        <span>${f.importance}</span>
      </div>
      <div class="ml-bar-bg">
        <div class="ml-bar-fill" style="width:${pct}%;background:${barColor}"></div>
      </div>
    </div>`;
  }).join('')}
  <div style="margin-top:12px;font-size:0.68rem;display:flex;gap:16px;flex-wrap:wrap">
    <span><span style="color:var(--yellow)">■</span> In-race + cross-race + PCS korttidsform</span>
    <span><span style="color:var(--green)">■</span> CyclingOracle-ratings</span>
    <span><span style="color:#c792ea">■</span> PCS Specialties</span>
    <span><span style="color:var(--blue)">■</span> Etapetype-flag</span>
  </div>
</div>`;
}

// ── Context ──────────────────────────────────────────────────────────────────
function renderContext(d) {
  const isPlacement = _activeModel === 'placement';
  const sp = d.avg_spearman;
  return `
<div class="ml-card">
  <div class="ml-card-title">Kontekst: Er ${sp?.toFixed(2)} Spearman godt?</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;font-size:0.78rem;line-height:1.75">
    <div>
      <div style="color:var(--muted);font-size:0.7rem;text-transform:uppercase;letter-spacing:0.4px;margin-bottom:6px">Referencepunkter</div>
      <div style="display:flex;flex-direction:column;gap:6px">
        <div style="padding:6px 8px;background:rgba(30,77,46,0.3);border-radius:5px;border-left:2px solid var(--green)">
          <span style="color:var(--green);font-weight:700">≥ 0.70</span> — Excellent for sport
        </div>
        <div style="padding:6px 8px;background:rgba(30,77,46,0.15);border-radius:5px;border-left:2px solid #6fcf5a">
          <span style="color:#6fcf5a;font-weight:700">0.60–0.70</span> — Meget stærkt${isPlacement ? ' <strong style="color:var(--text)">← placement her</strong>' : ''}
        </div>
        <div style="padding:6px 8px;background:rgba(240,165,0,0.1);border-radius:5px;border-left:2px solid var(--yellow)">
          <span style="color:var(--yellow);font-weight:700">0.50–0.60</span> — Solid${!isPlacement ? ' <strong style="color:var(--text)">← holdet her</strong>' : ''}
        </div>
        <div style="padding:6px 8px;background:var(--surface);border-radius:5px;border-left:2px solid var(--muted)">
          <span style="color:var(--muted);font-weight:700">0.35–0.50</span> — Acceptabel for sport
        </div>
        <div style="padding:6px 8px;background:var(--surface);border-radius:5px;border-left:2px solid #ff8a80">
          <span style="color:#ff8a80;font-weight:700">&lt; 0.35</span> — Svag / tilfældig gætning
        </div>
      </div>
    </div>
    <div>
      <div style="color:var(--muted);font-size:0.7rem;text-transform:uppercase;letter-spacing:0.4px;margin-bottom:6px">Hvad modellen giver i praksis</div>
      <div style="color:var(--muted);font-size:0.73rem;line-height:1.7">
        Holdet-point er fundamentalt uforudsigelige — etapevindere afgøres af brudshold,
        vind, taktik og tilfælde. En model kan aldrig nå 1.0.
        <br><br>
        Placement-modellen bruger <strong style="color:var(--text)">84.000+ rækker</strong> fra 5 år med GT + 1-ugesløb
        og er betydeligt stærkere end holdet-modellen (kun 11.000 rækker fra 3 løb).
        <br><br>
        ML-scoren er <em>ét signal (8%)</em> i det samlede ensemble. CyclingOracle-blanding
        og PCS-form er de tunge signaler. ML bruges primært til at
        <strong style="color:var(--text)">opfange in-race form</strong> via gt_form_5/xrace_form.
        <br><br>
        <span style="color:var(--muted)">Valideringen er LORO (out-of-sample) — ikke in-sample overfitting.</span>
      </div>
    </div>
  </div>
</div>`;
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', mlInit);
} else {
  mlInit();
}
