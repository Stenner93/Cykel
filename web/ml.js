/* ML Validering — renderer for web/data/holdet_ml_validation.json
   Den nye model er en LightGBM regressionsmodel der predikter normaliserede
   Holdet-point (0–100) baseret på faktiske 2025-resultater fra Holdet.dk. */

const ML_URL = './data/holdet_ml_validation.json';

const FEATURE_NAMES = {
  gt_form_5:      'GT-form: snit placering (5 etaper)',
  xrace_form_10:  'Cross-race form: snit placering (10 etaper)',
  gt_form_10:     'GT-form: snit placering (10 etaper)',
  co_spr:         'CyclingOracle: Sprint',
  co_mtn:         'CyclingOracle: Bjerg',
  co_hll:         'CyclingOracle: Kuperet',
  form_overall:   'PCS Form: Overordnet',
  co_itt:         'CyclingOracle: Enkeltstart',
  co_gc:          'CyclingOracle: GC',
  form_sprint:    'PCS Form: Sprint',
  form_mountain:  'PCS Form: Bjerg',
  form_hilly:     'PCS Form: Kuperet',
  form_tt:        'PCS Form: TT',
  spec_climber:   'PCS Specialties: Klatrer',
  spec_sprint:    'PCS Specialties: Sprint',
  spec_tt:        'PCS Specialties: TT',
  spec_hills:     'PCS Specialties: Bakker',
  co_cob:         'CyclingOracle: Brosten',
  gt_wins_so_far: 'GT etapesejre hidtil i løbet',
  is_sprint:      'Etapetype = Sprint',
  is_mountain:    'Etapetype = Bjerg',
  is_hilly:       'Etapetype = Kuperet',
  is_tt:          'Etapetype = Enkeltstart',
};

const RACE_LABEL = { giro: "Giro d'Italia", tdf: 'Tour de France', vuelta: 'Vuelta a España' };
const RACE_COLOR = { giro: '#ff8a80', tdf: '#58a6ff', vuelta: '#3fb950' };

function mlEsc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function spearmanColor(v) {
  if (v == null) return 'var(--muted)';
  if (v >= 0.55) return 'var(--green)';
  if (v >= 0.40) return 'var(--yellow)';
  return '#ff8a80';
}

async function mlInit() {
  const container = document.getElementById('mlContent');
  if (!container) return;

  let data;
  try {
    const res = await fetch(ML_URL, { cache: 'no-cache' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (e) {
    container.innerHTML = mlEmptyState(e.message);
    return;
  }

  container.innerHTML = mlRender(data);
}

function mlEmptyState(errMsg) {
  return `
<div class="ml-empty">
  <div style="font-size:2.2rem;margin-bottom:14px">⚙️</div>
  <div style="font-size:1rem;font-weight:700;margin-bottom:8px;color:var(--text)">
    ML-validering ikke tilgængelig
  </div>
  <div style="font-size:0.82rem;color:var(--muted);max-width:480px;margin:0 auto;line-height:1.7">
    ${mlEsc(errMsg)}<br><br>
    Kør for at generere:<br>
    <code style="display:block;margin-top:8px;padding:10px 14px;background:var(--card);border-radius:6px;text-align:left;font-size:0.78rem">
      python build_holdet_training_data.py<br>
      python train_holdet_model.py
    </code>
  </div>
</div>`;
}

function mlRender(d) {
  const genTime = d.generated
    ? new Date(d.generated).toLocaleString('da-DK', { dateStyle: 'short', timeStyle: 'short' })
    : '–';

  const loro    = d.loro_results || [];
  const imps    = (d.feature_importances || []).slice(0, 15);
  const maxImp  = imps[0]?.importance || 1;

  const avgSpearman = d.avg_spearman;
  const avgTop5     = d.avg_top5_accuracy;
  const avgRmse     = d.avg_rmse;

  // ── What does the model predict ──────────────────────────────────────────
  const explainHtml = `
<div class="ml-card" style="margin-bottom:16px;border-left:3px solid var(--yellow)">
  <div class="ml-card-title">Hvad gør modellen?</div>
  <div style="font-size:0.82rem;color:var(--text);line-height:1.75;margin-bottom:10px">
    Modellen predikter <strong style="color:var(--yellow)">normaliserede Holdet-point (0–100)</strong>
    for en given rytter i en given etape.
    <br><br>
    <strong>100</strong> = ryttere der scorede flest point i etapen (etapevinder + bonusser) ·
    <strong>0</strong> = ingen point ·
    <strong>50</strong> = halvvejs ift. topscorer i etapen.
    <br><br>
    Modellen er trænet direkte på faktiske Holdet.dk-point — det inkluderer
    <em>etapefinish, GC-stilling, bjergtrøje, sprintpoint og teambonus</em>.
    Outputtet denormaliseres med gennemsnitlige maksimumpoint pr. etapetype
    (sprint=400K, bjerg=427K, kuperet=430K, TT=399K) for at give et konkret K-point-estimat.
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;font-size:0.73rem;color:var(--muted)">
    <div style="padding:8px;background:var(--surface);border-radius:6px;border:1px solid var(--border)">
      <div style="font-weight:700;color:var(--text);margin-bottom:3px">Træningsdata</div>
      3 løb · 63 etaper · ${(d.n_train_total||0).toLocaleString('da-DK')} rytter-etape-rækker (Giro, TdF, Vuelta 2025)
    </div>
    <div style="padding:8px;background:var(--surface);border-radius:6px;border:1px solid var(--border)">
      <div style="font-weight:700;color:var(--text);margin-bottom:3px">Validering</div>
      LORO: trænet på 2 løb, testet på det 3. — gentaget 3 gange
    </div>
    <div style="padding:8px;background:var(--surface);border-radius:6px;border:1px solid var(--border)">
      <div style="font-weight:700;color:var(--text);margin-bottom:3px">Features</div>
      ${d.n_features} features: CO-ratings, PCS form, in-race rolling form, cross-race form
    </div>
  </div>
</div>`;

  // ── Summary metrics ───────────────────────────────────────────────────────
  const summaryHtml = `
<div class="ml-grid" style="margin-bottom:16px">
  <div class="ml-card" style="text-align:center">
    <div class="ml-card-title">Spearman-korrelation</div>
    <div style="font-size:2.2rem;font-weight:800;color:${spearmanColor(avgSpearman)};margin:8px 0">
      ${avgSpearman != null ? avgSpearman.toFixed(3) : '–'}
    </div>
    <div style="font-size:0.72rem;color:var(--muted);line-height:1.55">
      Rang-korrelation pr. etape: 1.0 = perfekt rækkefølge · 0.0 = tilfældig gætning.
      <br>0.51 betyder modellen fanger ca. halvdelen af den rigtige rækkefølge af point-scorere pr. etape.
    </div>
    <div style="margin-top:10px;height:6px;background:var(--border);border-radius:3px;overflow:hidden">
      <div style="height:100%;width:${Math.round((avgSpearman||0)*100)}%;background:${spearmanColor(avgSpearman)};border-radius:3px"></div>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:0.65rem;color:var(--muted);margin-top:3px">
      <span>0 (tilfældig)</span><span>1 (perfekt)</span>
    </div>
  </div>

  <div class="ml-card" style="text-align:center">
    <div class="ml-card-title">Top-5 Nøjagtighed</div>
    <div style="font-size:2.2rem;font-weight:800;color:${(avgTop5||0) >= 0.4 ? 'var(--green)' : 'var(--yellow)'};margin:8px 0">
      ${avgTop5 != null ? Math.round(avgTop5 * 100) + '%' : '–'}
    </div>
    <div style="font-size:0.72rem;color:var(--muted);line-height:1.55">
      Andel etaper hvor ≥1 af modellens top-5 ryttere er i de faktiske top-5 Holdet-scorere.
      <br>43% → i 43 ud af 100 etaper rammer modellen mindst én af de 5 bedste Holdet-scorere.
    </div>
    <div style="margin-top:10px;height:6px;background:var(--border);border-radius:3px;overflow:hidden">
      <div style="height:100%;width:${Math.round((avgTop5||0)*100)}%;background:${(avgTop5||0) >= 0.4 ? 'var(--green)' : 'var(--yellow)'};border-radius:3px"></div>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:0.65rem;color:var(--muted);margin-top:3px">
      <span>0%</span><span>100%</span>
    </div>
  </div>

  <div class="ml-card" style="text-align:center">
    <div class="ml-card-title">RMSE (fejl i point)</div>
    <div style="font-size:2.2rem;font-weight:800;color:var(--text);margin:8px 0">
      ${avgRmse != null ? avgRmse.toFixed(1) : '–'}
    </div>
    <div style="font-size:0.72rem;color:var(--muted);line-height:1.55">
      Gennemsnitlig fejl på 0–100 point-skalaen.
      <br>En RMSE på 21 svarer til at modellen typisk er ~21 point fra det sande normaliserede Holdet-score.
    </div>
    <div style="font-size:0.65rem;color:var(--muted);margin-top:8px">
      Lavere er bedre · skala 0–100 · typisk standardafvigelse i data: ~25 point
    </div>
  </div>
</div>`;

  // ── LORO cross-validation per fold ────────────────────────────────────────
  const loroHtml = `
<div class="ml-card" style="margin-bottom:16px">
  <div class="ml-card-title">Leave-One-Race-Out (LORO) — 3 fold</div>
  <div style="font-size:0.72rem;color:var(--muted);margin-bottom:14px;line-height:1.5">
    Hvert fold: modellen trænes på 2 af de 3 løb og testes på det 3. (out-of-sample).
    Viser om modellen generaliserer på tværs af løb og ikke blot memorerer 2025-data.
  </div>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:14px">
    ${loro.map(r => {
      const race  = r.val_race;
      const col   = RACE_COLOR[race] || 'var(--text)';
      const label = RACE_LABEL[race] || race;
      const sp    = r.mean_spearman;
      const acc   = r.top5_accuracy;
      return `
    <div style="padding:14px;background:var(--surface);border-radius:8px;border:1px solid var(--border);border-top:3px solid ${col}">
      <div style="font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.4px;color:${col};margin-bottom:10px">
        Testet på: ${mlEsc(label)}
      </div>
      <div class="ml-metric-row">
        <span class="ml-metric-label" style="font-size:0.72rem">Spearman</span>
        <span class="ml-metric-val" style="color:${spearmanColor(sp)}">${sp != null ? sp.toFixed(3) : '–'}</span>
      </div>
      <div class="ml-metric-row">
        <span class="ml-metric-label" style="font-size:0.72rem">Top-5 acc.</span>
        <span class="ml-metric-val">${acc != null ? Math.round(acc * 100) + '%' : '–'}</span>
      </div>
      <div class="ml-metric-row">
        <span class="ml-metric-label" style="font-size:0.72rem">RMSE</span>
        <span class="ml-metric-val">${r.rmse != null ? r.rmse.toFixed(1) : '–'}</span>
      </div>
      <div class="ml-metric-row">
        <span class="ml-metric-label" style="font-size:0.72rem">Etaper</span>
        <span class="ml-metric-val">${r.n_stages || '–'}</span>
      </div>
      <div style="margin-top:10px;height:4px;background:var(--border);border-radius:2px;overflow:hidden">
        <div style="height:100%;width:${Math.round((sp||0)*100)}%;background:${col};border-radius:2px"></div>
      </div>
    </div>`;
    }).join('')}
  </div>
  <div style="font-size:0.68rem;color:var(--muted);line-height:1.55;padding-top:10px;border-top:1px solid var(--border)">
    Variation på tværs af fold er forventet — løbene har forskellig uforudsigelighed.
    Giro scorer bedst (0.609) fordi GC-favoritter scorer mere konsistent i Giro; Vuelta scorer lavest (0.448) grundet mere kaotiske sprintetaper og breakaways.
  </div>
</div>`;

  // ── Per stage-type breakdown ──────────────────────────────────────────────
  const STYPE_LABEL = { sprint: 'Sprint', mountain: 'Bjerg', hilly: 'Kuperet', tt: 'Enkeltstart' };
  const STYPE_COLOR = { sprint: '#58a6ff', mountain: '#3fb950', hilly: '#f0a500', tt: '#c792ea' };

  function avgStype(key) {
    const vals = loro.map(r => r[key]).filter(v => v != null);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  }

  const stageBreakdownHtml = `
<div class="ml-card" style="margin-bottom:16px">
  <div class="ml-card-title">Præcision pr. etapetype</div>
  <div style="font-size:0.72rem;color:var(--muted);margin-bottom:14px;line-height:1.5">
    Gennemsnit over alle 3 LORO-fold. Viser hvornår modellen er stærkest og svagst —
    Spearman ≥ 0.55 (grøn) er solidt; under 0.40 (rød) er tæt på tilfældig gætning.
  </div>
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px">
    ${['sprint','mountain','hilly','tt'].map(st => {
      const col  = STYPE_COLOR[st];
      const lbl  = STYPE_LABEL[st];
      const sp   = avgStype(`spearman_${st}`);
      const acc  = avgStype(`top5acc_${st}`);
      const spColor = spearmanColor(sp);
      return `
    <div style="padding:12px;background:var(--surface);border-radius:8px;border:1px solid var(--border);border-top:3px solid ${col}">
      <div style="font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.4px;color:${col};margin-bottom:10px">${mlEsc(lbl)}</div>
      <div class="ml-metric-row">
        <span class="ml-metric-label" style="font-size:0.72rem">Spearman</span>
        <span class="ml-metric-val" style="color:${spColor}">${sp != null ? sp.toFixed(3) : '–'}</span>
      </div>
      <div class="ml-metric-row">
        <span class="ml-metric-label" style="font-size:0.72rem">Top-5 acc.</span>
        <span class="ml-metric-val">${acc != null ? Math.round(acc * 100) + '%' : '–'}</span>
      </div>
      <div style="margin-top:10px;height:4px;background:var(--border);border-radius:2px;overflow:hidden">
        <div style="height:100%;width:${Math.round((sp||0)*100)}%;background:${spColor};border-radius:2px"></div>
      </div>
    </div>`;
    }).join('')}
  </div>
  <div style="font-size:0.7rem;color:var(--muted);line-height:1.6;padding-top:10px;border-top:1px solid var(--border)">
    <strong style="color:var(--text)">Bjerg og kuperede etaper</strong> fanger modellen bedst — GC-hierarkiet er stabilt og CO-ratings er præcise.
    <strong style="color:var(--text)">Sprint og enkeltstart</strong> er sværere: spurterne afgøres af held/taktik, enkeltstart-feltet er lille (få rækker i LORO).
    Modellen bruges til at <em>rankordenere</em>, ikke til at predicte absolutte point med høj præcision.
  </div>
</div>`;

  // ── Feature importances ────────────────────────────────────────────────────
  const impHtml = `
<div class="ml-card" style="margin-bottom:16px">
  <div class="ml-card-title">Feature Importance — hvad driver modellen?</div>
  <div style="font-size:0.72rem;color:var(--muted);margin-bottom:14px;line-height:1.55">
    Antal gange en feature bruges i LightGBM's beslutningstræer.
    <strong style="color:var(--yellow)">Vigtigste observation:</strong>
    In-race rolling form (gt_form_5) dominerer — ryttere der allerede scorer godt i dette løb
    fortsætter typisk. CO- og PCS-data er vigtige på type-specifike etaper (sprint/bjerg),
    men form-signalerne vejer tungest.
  </div>
  ${imps.map((f, i) => {
    const name = FEATURE_NAMES[f.feature] || f.feature;
    const pct  = Math.round((f.importance / maxImp) * 100);
    const isForm = f.feature.startsWith('gt_form') || f.feature.startsWith('xrace') || f.feature.startsWith('form_');
    const isCO   = f.feature.startsWith('co_');
    const barColor = isForm ? 'var(--yellow)' : isCO ? 'var(--green)' : 'var(--blue)';
    return `
    <div class="ml-bar-wrap">
      <div class="ml-bar-label">
        <span>${i+1}. ${mlEsc(name)}</span>
        <span>${f.importance}</span>
      </div>
      <div class="ml-bar-bg">
        <div class="ml-bar-fill" style="width:${pct}%;background:${barColor}"></div>
      </div>
    </div>`;
  }).join('')}
  <div style="margin-top:12px;font-size:0.68rem;display:flex;gap:16px;flex-wrap:wrap">
    <span><span style="color:var(--yellow)">■</span> Form (in-race + cross-race + PCS korttidsform)</span>
    <span><span style="color:var(--green)">■</span> CyclingOracle-ratings</span>
    <span><span style="color:var(--blue)">■</span> PCS Specialties</span>
  </div>
</div>`;

  // ── Context / how to interpret ─────────────────────────────────────────────
  const contextHtml = `
<div class="ml-card">
  <div class="ml-card-title">Kontekst: Er 0.51 Spearman godt nok?</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;font-size:0.78rem;line-height:1.75">
    <div>
      <div style="color:var(--muted);font-size:0.7rem;text-transform:uppercase;letter-spacing:0.4px;margin-bottom:6px">Referencepunkter</div>
      <div style="display:flex;flex-direction:column;gap:6px">
        <div style="padding:6px 8px;background:var(--surface);border-radius:5px;border-left:2px solid var(--green)">
          <span style="color:var(--green);font-weight:700">≥ 0.65</span> — Excellent
        </div>
        <div style="padding:6px 8px;background:rgba(30,136,229,0.1);border-radius:5px;border-left:2px solid var(--yellow)">
          <span style="color:var(--yellow);font-weight:700">0.50–0.65</span> — Solid <strong style="color:var(--text)">← modellen er her</strong>
        </div>
        <div style="padding:6px 8px;background:var(--surface);border-radius:5px;border-left:2px solid var(--muted)">
          <span style="color:var(--muted);font-weight:700">0.35–0.50</span> — Acceptabel for sport
        </div>
        <div style="padding:6px 8px;background:var(--surface);border-radius:5px;border-left:2px solid #ff8a80">
          <span style="color:#ff8a80;font-weight:700">&lt; 0.35</span> — Svag / tæt på tilfældig
        </div>
      </div>
    </div>
    <div>
      <div style="color:var(--muted);font-size:0.7rem;text-transform:uppercase;letter-spacing:0.4px;margin-bottom:6px">Hvad modellen giver</div>
      <div style="color:var(--muted);font-size:0.73rem;line-height:1.7">
        Holdet-point er uforudsigelige — etapevindere afgøres af brudshold, vind og held.
        En model kan aldrig nå 1.0.
        <br><br>
        Modellen bruges fra <strong style="color:var(--text)">etape 1</strong> (CO-ratings, PCS-form fra
        optaktsløb og cross-race form er tilgængelige fra dag 1). In-race rolling form vægter
        gradvist tungere efterhånden som løbet skrider frem.
        <br><br>
        ML-outputtet denormaliseres direkte til K-point-estimater og bruges som primær signal
        for forventede Holdet-point — ikke kun som et lille tillægssignal.
      </div>
    </div>
  </div>
</div>`;

  return `
<div style="display:flex;align-items:center;gap:10px;margin-bottom:18px;flex-wrap:wrap">
  <span style="font-size:0.9rem;font-weight:700">ML-model: LightGBM Holdet-prediktor</span>
  <span class="ml-status-badge ready">Aktiv</span>
  <span style="font-size:0.75rem;color:var(--muted)">
    Beregnet ${mlEsc(genTime)} · ${d.n_features} features · ${(d.n_train_total||0).toLocaleString('da-DK')} træningsrækker
  </span>
</div>
${explainHtml}
${summaryHtml}
${loroHtml}
${stageBreakdownHtml}
${impHtml}
${contextHtml}`;
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', mlInit);
} else {
  mlInit();
}
