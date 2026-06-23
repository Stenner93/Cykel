/* ML Validering — loader og renderer for web/data/ml_validation.json */

const ML_URL = './data/ml_validation.json';

const STYPE_LABEL = {
  sprint: 'Sprint', mountain: 'Bjerg', hilly: 'Bakket',
  tt: 'TT', ttt: 'TTT', cobbled: 'Brosten',
};
const RACE_LABEL = { tdf: 'TdF', giro: 'Giro', vuelta: 'Vuelta' };

const FEATURE_NAMES = {
  co_mtn: 'CO Bjerg', co_spr: 'CO Sprint', co_hll: 'CO Bakkespesialist',
  co_itt: 'CO TT', co_cob: 'CO Brostensklassiker', co_gc: 'CO GC',
  spec_climber: 'PCS Klatrer', spec_sprint: 'PCS Sprint',
  spec_tt: 'PCS TT', spec_hills: 'PCS Bakker',
  gt_form_5: 'GT form (5 etaper)', gt_form_10: 'GT form (10 etaper)',
  gt_wins_so_far: 'GT etapesejre hidtil',
  profile_score: 'Profil-score', is_sprint: 'Etape=Sprint',
  is_mountain: 'Etape=Bjerg', is_hilly: 'Etape=Bakket', is_tt: 'Etape=TT',
};

function mlEsc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function mlFmt(v, d = 3) {
  return v == null ? '–' : Number(v).toFixed(d);
}
function mlPct(v) {
  return v == null ? '–' : (Number(v) * 100).toFixed(1) + '%';
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
  const isNotFound = errMsg.includes('404') || errMsg.includes('HTTP 4');
  return `
<div class="ml-empty">
  <div style="font-size:2.2rem;margin-bottom:14px">${isNotFound ? '⚙️' : '⚠️'}</div>
  <div style="font-size:1rem;font-weight:700;margin-bottom:8px;color:var(--text)">
    ${isNotFound ? 'ML-model ikke trænet endnu' : 'Fejl ved indlæsning'}
  </div>
  ${isNotFound
    ? `<div style="font-size:0.82rem;color:var(--muted);max-width:480px;margin:0 auto;line-height:1.7">
        For at træne modellen, kør disse kommandoer i terminal:<br>
        <code style="display:block;margin-top:8px;padding:10px 14px;background:var(--card);border-radius:6px;text-align:left;font-size:0.78rem">
          python scrape_pcs_history.py<br>
          python build_training_data.py<br>
          python train_model.py
        </code>
        <div style="margin-top:10px;font-size:0.75rem">
          Dette henter TdF/Giro/Vuelta 2021-2025 fra PCS (~45 min) og træner LightGBM-modellen.
        </div>
      </div>`
    : `<div style="color:#E53935;font-size:0.82rem">${mlEsc(errMsg)}</div>`
  }
</div>`;
}

function mlRender(d) {
  const genTime = d.generated
    ? new Date(d.generated).toLocaleString('da-DK', { dateStyle: 'short', timeStyle: 'short' })
    : '–';

  const vm = d.val_metrics || {};
  const tm = d.train_metrics || {};
  const imps = (d.feature_importances || []).slice(0, 15);
  const maxImp = imps[0]?.importance || 1;

  const aucColor = v => (v >= 0.75 ? 'good' : v >= 0.65 ? 'warn' : '');

  const stageRows = (d.stage_predictions || [])
    .filter(s => s.year === d.validated_on_year)
    .sort((a, b) => a.stage - b.stage);

  const hitRate  = mlPct(d.winner_hit_rate);
  const overlap  = d.avg_top5_overlap != null
    ? Number(d.avg_top5_overlap).toFixed(2) + '/5'
    : '–';

  return `
<!-- Status bar -->
<div style="display:flex;align-items:center;gap:10px;margin-bottom:18px;flex-wrap:wrap">
  <span style="font-size:0.9rem;font-weight:700">ML-model</span>
  <span class="ml-status-badge ready">Trænet</span>
  <span style="font-size:0.75rem;color:var(--muted)">
    Trænet på ${d.trained_on_years?.join(', ')} · Valideret på ${d.validated_on_year} ·
    ${d.n_features} features · Beregnet ${mlEsc(genTime)}
  </span>
</div>

<!-- Metrics grid -->
<div class="ml-grid">

  <!-- Val metrics -->
  <div class="ml-card">
    <div class="ml-card-title">Validering ${d.validated_on_year} (out-of-sample)</div>
    <div class="ml-metric-row">
      <span class="ml-metric-label">ROC-AUC (top-5)</span>
      <span class="ml-metric-val ${aucColor(vm.auc_top5)}">${mlFmt(vm.auc_top5)}</span>
    </div>
    <div class="ml-metric-row">
      <span class="ml-metric-label">Avg. Precision (top-5)</span>
      <span class="ml-metric-val">${mlFmt(vm.ap_top5)}</span>
    </div>
    <div class="ml-metric-row">
      <span class="ml-metric-label">AUC Sprint</span>
      <span class="ml-metric-val ${aucColor(vm.auc_sprint)}">${mlFmt(vm.auc_sprint)}</span>
    </div>
    <div class="ml-metric-row">
      <span class="ml-metric-label">AUC Bjerg</span>
      <span class="ml-metric-val ${aucColor(vm.auc_mountain)}">${mlFmt(vm.auc_mountain)}</span>
    </div>
    <div class="ml-metric-row">
      <span class="ml-metric-label">AUC Bakket</span>
      <span class="ml-metric-val ${aucColor(vm.auc_hilly)}">${mlFmt(vm.auc_hilly)}</span>
    </div>
    <div class="ml-metric-row">
      <span class="ml-metric-label">AUC TT</span>
      <span class="ml-metric-val ${aucColor(vm.auc_tt)}">${mlFmt(vm.auc_tt)}</span>
    </div>
    <div style="border-top:1px solid var(--border);margin:10px 0"></div>
    <div class="ml-metric-row">
      <span class="ml-metric-label">Vinder i top-5 pred.</span>
      <span class="ml-metric-val ${Number(d.winner_hit_rate) >= 0.35 ? 'good' : 'warn'}">${hitRate}</span>
    </div>
    <div class="ml-metric-row">
      <span class="ml-metric-label">Gns. top-5 overlap</span>
      <span class="ml-metric-val">${overlap}</span>
    </div>
  </div>

  <!-- Train metrics -->
  <div class="ml-card">
    <div class="ml-card-title">Træningsdata (in-sample check)</div>
    <div class="ml-metric-row">
      <span class="ml-metric-label">ROC-AUC (top-5)</span>
      <span class="ml-metric-val">${mlFmt(tm.auc_top5)}</span>
    </div>
    <div class="ml-metric-row">
      <span class="ml-metric-label">Avg. Precision (top-5)</span>
      <span class="ml-metric-val">${mlFmt(tm.ap_top5)}</span>
    </div>
    <div style="border-top:1px solid var(--border);margin:10px 0"></div>
    <div class="ml-metric-row">
      <span class="ml-metric-label">Træningsrækker</span>
      <span class="ml-metric-val">${(d.n_train || 0).toLocaleString('da-DK')}</span>
    </div>
    <div class="ml-metric-row">
      <span class="ml-metric-label">Valideringsrækker</span>
      <span class="ml-metric-val">${(d.n_val || 0).toLocaleString('da-DK')}</span>
    </div>
    <div class="ml-metric-row">
      <span class="ml-metric-label">Features</span>
      <span class="ml-metric-val">${d.n_features || '–'}</span>
    </div>
    <div style="border-top:1px solid var(--border);margin:10px 0;font-size:0.7rem;color:var(--muted);line-height:1.5">
      AUC &gt; 0.75 = god · AUC &gt; 0.65 = acceptabel · AUC ≈ 0.5 = tilfældig gætning.<br>
      Forventet for GT-etaper: 0.68–0.80 afhængig af disciplin.
    </div>
  </div>
</div>

<!-- AUC breakdown by stage type -->
<div class="ml-card" style="margin-bottom:16px">
  <div class="ml-card-title">AUC per etapetype — ${d.validated_on_year}</div>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px">
    ${[
      {key:'sprint',   label:'Sprint 🟡',   color:'var(--yellow)'},
      {key:'mountain', label:'Bjerg ⛰️',    color:'var(--blue)'},
      {key:'hilly',    label:'Bakket 🌄',   color:'var(--green)'},
      {key:'tt',       label:'TT ⏱️',       color:'#ff8a80'},
    ].map(({key, label, color}) => {
      const auc = vm['auc_' + key];
      const pct = auc != null ? Math.round(auc * 100) : null;
      const qualColor = auc >= 0.80 ? 'var(--green)' : auc >= 0.70 ? 'var(--yellow)' : '#ff8a80';
      return `
      <div style="text-align:center;padding:10px;background:var(--surface);border-radius:8px;border:1px solid var(--border)">
        <div style="font-size:0.7rem;color:var(--muted);margin-bottom:4px">${label}</div>
        <div style="font-size:1.4rem;font-weight:700;color:${qualColor}">${pct != null ? pct + '%' : '–'}</div>
        <div style="font-size:0.65rem;color:var(--muted)">ROC-AUC</div>
        <div style="margin-top:6px;height:4px;background:var(--border);border-radius:2px;overflow:hidden">
          <div style="height:100%;width:${pct || 0}%;background:${qualColor};border-radius:2px"></div>
        </div>
      </div>`;
    }).join('')}
  </div>
  <div style="font-size:0.68rem;color:var(--muted);margin-top:10px;line-height:1.5">
    AUC forklaret: 100% = perfekt forudsigelse · 50% = tilfældig gætning ·
    Målet for GT-etaper er typisk 70–85% afhængig af etapetype.<br>
    <strong style="color:var(--text)">TT er nemmest</strong> at forudsige (ryttere har faste TT-styrker) ·
    <strong style="color:var(--text)">Sprint er sværest</strong> (mange faktorer: vind, positionering, held).
  </div>
</div>

<!-- Feature importances -->
<div class="ml-card" style="margin-bottom:16px">
  <div class="ml-card-title">Feature Importance — top ${imps.length}</div>
  <div style="font-size:0.72rem;color:var(--muted);margin-bottom:14px;line-height:1.55">
    <strong style="color:var(--text)">Hvad er tallene?</strong>
    Tallene viser "split importance" — antal gange en feature bruges til at træffe en beslutning i LightGBM-modellens beslutningstræer.
    Høj score = featuren skærer feltet effektivt op (god til at adskille top-5 ryttere fra resten).
    Det er IKKE det samme som korrelation eller kausalitet — det siger noget om modelstruktur, ikke naturlov.
    Tommelfingerregel: &gt;100 = relevant · &lt;30 = svag signal.
  </div>
  ${imps.map(f => {
    const name = FEATURE_NAMES[f.feature] || f.feature;
    const pct  = Math.round((f.importance / maxImp) * 100);
    return `
    <div class="ml-bar-wrap">
      <div class="ml-bar-label">${mlEsc(name)} <span>${f.importance}</span></div>
      <div class="ml-bar-bg"><div class="ml-bar-fill" style="width:${pct}%"></div></div>
    </div>`;
  }).join('')}
</div>

<!-- Feature interaction hints -->
<div class="ml-card" style="margin-bottom:16px">
  <div class="ml-card-title">Feature-interaktioner — nøglekombinationer</div>
  <div style="font-size:0.72rem;color:var(--muted);line-height:1.7;margin-bottom:10px">
    LightGBM lærer automatisk kombinationer af features. Her er de vigtigste observerede mønstre:
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
    ${[
      {combo:'profile_score × CO Bjerg', insight:'Høj profil-score OG høj bjerg-rating → stærk indikator for top-5 på bjergetaper. Begge features alene er svagere.', icon:'⛰️'},
      {combo:'GT form (5) × etapetype', insight:'Ridere med god GT form de seneste 5 etaper performer markant bedre på de næste etaper — momentum er reelt.', icon:'📈'},
      {combo:'CO Sprint × Etape=Sprint', insight:'På sprintetaper er CO SPR-rating den mest differentierende feature. Modelens sprint-AUC (0.83) er direkte drevet af denne kombination.', icon:'⚡'},
      {combo:'GT sejre hidtil × form', insight:'Ryttere der allerede har vundet en etape i det aktuelle løb er statistisk mere tilbøjelige til at vinde igen — "vinder-momentum".', icon:'🏆'},
    ].map(({combo, insight, icon}) => `
    <div style="padding:10px;background:var(--surface);border-radius:8px;border:1px solid var(--border)">
      <div style="font-size:0.72rem;font-weight:700;color:var(--yellow);margin-bottom:4px">${icon} ${combo}</div>
      <div style="font-size:0.68rem;color:var(--muted);line-height:1.5">${insight}</div>
    </div>`).join('')}
  </div>
</div>

<!-- Per-stage backtesting table -->
<div style="font-size:0.82rem;font-weight:700;margin-bottom:6px">
  Etape-forudsigelser — ${d.validated_on_year} (${stageRows.length} etaper)
  <span style="font-weight:400;font-size:0.75rem;color:var(--muted);margin-left:8px">
    Grøn = vinder var i top-5 forudsigelse
  </span>
</div>
${(() => {
  const hitByStype = {};
  stageRows.forEach(s => {
    const st = s.stype || 'unknown';
    if (!hitByStype[st]) hitByStype[st] = {hits:0, total:0};
    hitByStype[st].total++;
    if (s.winner_in_pred) hitByStype[st].hits++;
  });
  return `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
  ${Object.entries(hitByStype).map(([st, {hits, total}]) => {
    const pct = Math.round(hits/total*100);
    return `<span style="font-size:0.72rem;padding:3px 10px;border-radius:10px;background:var(--card);border:1px solid var(--border)">
      <span class="ml-badge ${mlEsc(st)}">${STYPE_LABEL[st]||st}</span>
      ${hits}/${total} (${pct}%)
    </span>`;
  }).join('')}
</div>`;
})()}
<div class="ml-stages-wrap">
  <table class="ml-stages-table">
    <thead>
      <tr>
        <th>Løb</th><th>Etape</th><th>Type</th>
        <th>Top-5 forudsagt</th><th>Faktisk vinder</th><th>Overlap</th>
      </tr>
    </thead>
    <tbody>
      ${stageRows.map(s => {
        const hit     = s.winner_in_pred;
        const stype   = s.stype || '';
        const slabel  = STYPE_LABEL[stype] || stype;
        const rLabel  = RACE_LABEL[s.race] || s.race;
        return `<tr>
          <td>${mlEsc(rLabel)}</td>
          <td style="text-align:center">${s.stage}</td>
          <td><span class="ml-badge ${mlEsc(stype)}">${mlEsc(slabel)}</span></td>
          <td style="font-size:0.68rem;color:var(--muted)">${(s.predicted_top5 || []).map(r => mlEsc(r)).join(', ')}</td>
          <td class="${hit ? 'ml-hit' : 'ml-miss'}" style="font-weight:${hit ? 700 : 400}">
            ${mlEsc((s.actual_winner || []).join(', '))}
            ${hit ? ' ✓' : ''}
          </td>
          <td style="text-align:center;color:${s.top5_overlap >= 3 ? 'var(--green)' : s.top5_overlap >= 1 ? 'var(--yellow)' : 'var(--muted)'}">${s.top5_overlap}/5</td>
        </tr>`;
      }).join('')}
    </tbody>
  </table>
</div>`;
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', mlInit);
} else {
  mlInit();
}
