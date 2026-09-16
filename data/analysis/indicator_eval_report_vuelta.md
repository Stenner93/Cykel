# Indikator-evaluering — Vuelta 2026

Hvilke indikatorer bag modellen forudsagde bedst den **faktiske** holdet-vækst?
Beregnet på 20 etaper × 202 ryttere (4.040 rækker) fra
`web/data/vuelta2026_predictions.json` (etape 3 er en hviledag/mangler i
kilden; etape 21 er nu fuldt opdateret). Mål: **Spearman rang-korrelation**
mellem signal og faktisk vækst (gennemsnit af per-etape korrelationer).
Kør selv: `python scripts/analysis/eval_indicators.py --race vuelta2026`

## Hovedresultater

### Samme mønster som Touren: ekstern konsensus > interne signaler
| Kilde/signal | Mean Spearman | Dækning |
|---|---|---|
| **VeloScore** (ekstern konsensus) | **+0.33** | 15 etaper |
| discipline (etapetype-match) | +0.21 | 20 |
| pcs_rank (12-mdr. PCS-point) | +0.20 | 20 |
| form (nylig PCS-form) | +0.05 | 20 |
| **ml (LightGBM-signal)** | **−0.12** | 20 |

VeloScore vinder igen, men svagere end i TdF (+0.33 mod +0.56) — Vueltaens
etaper var generelt sværere at forudsige for alle kilder (se nedenfor).

### ML-signalet er anti-prædiktivt igen — ikke en TdF-tilfældighed
Andet løb i træk hvor det interne ml-signal trækker samlet model *ned*
(−0.12, mod −0.16 i TdF). Det er nu et **konsistent, tværgående** fund, ikke
støj fra én sæson — se `cross_race_report.md`.

### Delmodeller
| Delmodel | Mean Spearman |
|---|---|
| exp / holdet_est / expected_pts (blandet) | +0.31 |
| placement_pred (alene) | +0.26 |

Modsat TdF (hvor placement_pred alene slog blandingen) er den blandede model
her marginalt foran — men forskellen er lille og næppe robust over kun 20
etaper.

### Kaptajns-ramning (argmax lander etapens faktisk bedste rytter)
| Strategi | Top-1 | Top-3 |
|---|---|---|
| form | 25 % | 35 % |
| model (exp) | 20 % | 45 % |
| placement_pred | 20 % | 35 % |
| VeloScore #1 | 40 % | 53 % |
| ml | 10 % | 15 % |

VeloScores egen top-pick var klart bedst til at ramme etapens reelt bedste
rytter (40 %/53 %) — endnu tydeligere end i TdF. Modellens exp-kaptajn ramte
kun hver 5. etape (mod hver 3. i TdF): Vueltaen var sværere at "løse" for den
interne model.

### Signalstyrke per etapetype (top-3, mean Spearman)
- **TT** → veloscore +0.45, discipline +0.27, pcs_rank +0.14
- **Bjerg** → veloscore +0.38, discipline +0.36, pcs_rank +0.18
- **Kuperet** → veloscore +0.33, pcs_rank +0.22, discipline +0.19
- **Sprint** → veloscore +0.29, pcs_rank +0.22, discipline +0.12

Samme rækkefølge som i TdF (sprint sværest, TT/bjerg nemmest), men VeloScore
er nu det stærkeste signal i *alle* fire etapetyper — i TdF var discipline
foran på TTT/bjerg/TT. Endnu et argument for at veje ekstern konsensus tungere
fremadrettet, uanset etapetype.

## Forbehold
- Alle konklusioner bygger på 20 etaper — retningsgivende, ikke statistisk
  robuste enkeltvis.
- `actual` er holdet-*værdivækst*, ikke rene point — se `manager_eval_report_vuelta.md`
  for kontekst om hvorfor det stadig er det rigtige mål her.
