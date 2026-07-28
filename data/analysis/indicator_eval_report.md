# Indikator-evaluering — TdF 2026

Hvilke indikatorer bag modellen forudsagde bedst den **faktiske** holdet-vækst?
Beregnet på 21 etaper × 184 ryttere (3.864 rækker) fra
`web/data/tdf2026_predictions.json`, som indeholder både modellens signaler og
den realiserede `actual`-vækst per rytter per etape. Mål: **Spearman
rang-korrelation** mellem signal og faktisk vækst (gennemsnit af per-etape
korrelationer). Kør selv: `python scripts/analysis/eval_indicators.py`.

## Hovedresultater

### Eksterne kilder slår modellens egne signaler
| Kilde/signal | Mean Spearman | Dækning |
|---|---|---|
| **VeloScore** (ekstern konsensus) | **+0.56** | 9 etaper* |
| discipline (etapetype-match) | +0.38 | 21 |
| pcs_rank (12-mdr. PCS-point) | +0.28 | 21 |
| form (nylig PCS-form) | +0.18 | 21 |
| **ml (LightGBM-signal)** | **−0.16** | 21 |

\* VeloScore-korrelationen er målt **kun blandt de ryttere VeloScore rangerer**
(typisk top ~20), mens de interne signaler måles på hele feltet på 184 — så
tallet er ikke 100 % æble-til-æble (VeloScore har fordelen af allerede at have
sorteret de irrelevante domestiquer fra). Men konklusionen holder: den
eksterne konsensus var det stærkeste enkeltsignal, vi havde.

### To vigtige, handlingsrettede fund
1. **ML-signalet var anti-prædiktivt (−0.16).** LightGBM-laget trak reelt den
   samlede model *ned* — det var værre end at gætte. Kandidat til at fjerne
   eller gentræne før Vueltaen.
2. **Den simple etapetype-matching (discipline, +0.38) bar læsset** — især på
   TTT (+0.72), bjerg (+0.52) og TT (+0.52). Grundmekanikken virker; det
   avancerede lag ovenpå gjorde ikke.

### Delmodeller (Spearman vs. faktisk vækst)
| Delmodel | Mean Spearman |
|---|---|
| **placement_pred** (placerings-model alene) | **+0.44** |
| exp / holdet_est / expected_pts (blandet) | +0.39 |

`placement_pred` **alene** slog den blandede model. Blandingen bliver trukket
ned — sandsynligvis af ml-signalet og den svage form-komponent.

### Kaptajns-ramning (argmax lander etapens faktisk bedste rytter)
| Strategi | Top-1 | Top-3 |
|---|---|---|
| model (exp) | 33 % | 57 % |
| placement_pred | 33 % | 57 % |
| VeloScore #1 | 33 % | 33 %* |
| form | 5 % | 33 % |
| ml | 5 % | 14 % |

Modellen ramte den *præcist* bedste kaptajn hver 3. etape og top-3 på >halvdelen
— reelt bedre end tilfældigt, men langt fra sikkert. Samlet model-kalibrering
(pooled exp vs. actual) = Spearman **0.35**: reel, men beskeden signalværdi.

### Signalstyrke per etapetype (mean Spearman, top-3)
- **TTT** → discipline +0.72, pcs_rank +0.34, ml +0.28
- **Bjerg** → discipline +0.52, pcs_rank +0.33, form +0.25
- **TT** → discipline +0.52, pcs_rank +0.19, form +0.17
- **Kuperet** → pcs_rank +0.33, discipline +0.30, form +0.20
- **Sprint** → discipline +0.23, pcs_rank +0.21, form +0.11
  (sprint sværest at forudsige — lavest korrelation hele vejen)

## Konsekvenser frem mod Vueltaen
- **Vægt VeloScore/ekstern konsensus tungere**; skru ned for (eller fjern) det
  interne ml-signal, der beviseligt skadede.
- **Overvej at bruge `placement_pred` som primær** frem for den nuværende
  blanding — den var bedre alene.
- **Sprint-etaper er iboende svære** — vær ydmyg omkring kaptajnsvalg dér; brug
  odds/VeloScore-konsensus frem for form.

> Bemærk: `actual` er den realiserede holdet-værdi modellen trænes mod. En
> senere version bør gentage analysen mod ren *pointvækst* når snapshot-data
> fra holdet.dk er hentet (afventer netadgang — se hovednote).
