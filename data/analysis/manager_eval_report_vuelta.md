# Manager-sammenligning — Vuelta 2026

Dit hold (7271757) vs. Kasper (7272262) — runde for runde.

**Metode:** hver runde scorer et hold summen af sine 8 rytteres realiserede
`actual`-vækst, med kaptajnen talt dobbelt (holdets kaptajnsregel). Samme
metode som TdF-analysen. Kør selv: `python scripts/analysis/eval_managers.py --race vuelta2026`

> **Dækning: kun etape 1-20, ikke 21.** Holdet.dk's rundeliste
> (`reference/rounds.json`) rapporterede kun 20 runder på snapshot-tidspunktet
> — etape 21's lineup-lås/rundenummer var tilsyneladende ikke tilgængeligt via
> det endpoint endnu, selvom etapens point-facit (`actual` i predictions-filen)
> nu er der. Ubetydeligt for konklusionerne, men nævnes for gennemsigtighed.
>
> **Ingen top-10/rivaler denne gang.** Auto-discovery af Vueltaspillets
> leaderboard-endpoint fejlede (`games/628/leaderboard` gav ikke gyldig JSON —
> Holdet har tilsyneladende et andet leaderboard-endpoint for Vueltaspillet end
> for TdF). Vil du have en rigtig top-10-sammenligning, skal du selv finde 5-10
> hold-ID'er på Vueltaspillets slutstilling (holdet.dk → stilling → åbn et hold
> → aflæs ID i URL'en) og sende dem, så kører jeg
> `snapshot_holdet_teams.py --race vuelta2026 --team-ids ...` igen.

## Slutresultat

| # | Hold | Total (M) | Transfers | Kaptajn (M) | Kap-effektivitet |
|---|---|---|---|---|---|
| 1 | Kasper | 10.79 | 81 | 2.26 | 0.46 |
| 2 | **Os (Anders)** | **10.06** | 85 | **1.61** | **0.32** |

## De vigtigste fund

### 1. Kaptajnen var din SVAGHED denne gang — modsat Touren
I TdF havde du den **højeste** kaptajn-effektivitet af alle 14 hold (0.92). I
Vueltaen har du den **laveste** af de to sammenlignede hold (0.32 mod Kaspers
0.46) — du fangede kun ca. en tredjedel af den vækst, din bedste egen rytter
leverede den dag, når du gjorde ham til kaptajn.

Det bekræftes af de fem største kaptajn-fortrydelser:

| Runde | Kaptajn valgt | Kaptajns vækst | Bedste egen rytter | Tabt (M) |
|---|---|---|---|---|
| 4  | Matthew Brennan | −0.07 | +0.67 | 0.75 |
| 9  | Matthew Brennan | −0.09 | +0.30 | 0.39 |
| 15 | Matthew Brennan | +0.06 | +0.41 | 0.36 |
| 10 | Matthew Brennan | 0.00  | +0.35 | 0.35 |
| 12 | Wout van Aert   | +0.08 | +0.42 | 0.34 |

**Fire af de fem tabte kaptajnsvalg var Brennan** — et gennemgående mønster
(sprint-kaptajn på dage der ikke gik hans vej), ikke fire tilfældige floppede
dage. Værd at kigge på: sad du fast i en "Brennan er sikker kaptajn"-vane
længere end vækstdataene bakkede op om?

### 2. Bidrag: Pogačar, van Aert og Roglič bar holdet
| Bedste bidrag (M) | Værste / mest spildte plads (M) |
|---|---|
| +2.29 Tadej Pogačar | −0.19 Steven Kruijswijk |
| +1.62 Wout van Aert | −0.15 Vito Braet |
| +1.38 Primož Roglič | −0.15 Magnus Cort |
| +0.64 Jakob Omrzel | −0.14 Henri-François Renard-Haquin |
| +0.58 Joshua Tarling | −0.09 Axel Laurance |
| +0.44 Santiago Buitrago | −0.09 Thibau Nys |

De tre klassementsryttere (Pogačar, van Aert, Roglič) dominerer bidraget
massivt — konsistent med at Vueltaen (mere end TdF) blev afgjort på GC-vækst
snarere end enkeltstående sprint/udbrudsgevinster.

## Forbehold
- Vækst-attribution, ikke holdets præcise pris/bank-værdi (samme forbehold som
  TdF-analysen).
- Kun 2 hold sammenlignet (dig + Kasper) — ingen "hvor gode var vi relativt
  til feltet"-konklusion mulig uden top-10-data (se boks øverst).
