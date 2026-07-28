# Etape-niveau analyse + bedste mulige hold — TdF 2026

Svar på tre ønsker: (1) graf med **faktisk holdværdi** (ikke delta), (2) analyse
**pr. etape** (ikke sæson-aggregat), (3) et **bedste mulige hold**. Kør:
`python scripts/analysis/eval_stage_level.py`.

## To metrikker — hold dem adskilt
- **Værdikurven** = holdets markedsværdi (sum af 8 rytteres priser) pr. runde,
  ~50M → ~77M. Det er "den faktiske værdi fra start til slut".
- **Per-etape-tallene** = realiseret `actual`-vækst pr. etape (kaptajn dobbelt).
  To linser på samme sag; de følges ad.

## Per-etape: hvor blev der vundet/tabt (uddrag)
| Etape | Type | Din score (M) | Top-10 median | Δ |
|---|---|---|---|---|
| E7 | sprint | 1.96 | 2.48 | **−0.51** |
| E12 | sprint | 2.05 | 2.60 | **−0.55** |
| E13 | sprint | 0.17 | 0.40 | −0.23 |
| E16 | TT | 1.60 | 1.28 | **+0.32** |
| E21 | sprint | 1.56 | 1.28 | **+0.28** |

**Mønster:** du tabte mest på **sprint-etaper** (E7, E12, E13) hvor top-holdene
havde spurtere du manglede — og vandt på **TT/afsluttende** etaper (E16 Remco-
kaptajn, E21 van der Poel).

### Konkrete kald (nedslag)
**Gode:** E6/E10/E14 Pogačar-kaptajn ramte dine bedste (+0.5M hver); E20 Carapaz-
kaptajn (+0.58M) ✓; E16 Remco-kaptajn på enkeltstart (+0.32M bedre end top-median).

**Dårlige:**
- **E11:** kaptajn Merlier floppede (+0.04M) — din egen Wærenskjold gav +0.47M. Største enkelt-fejl.
- **E7:** Søren Wærenskjold (+0.33M, 8/10 top-hold havde ham) — du havde ham ikke.
- **E12:** Biniam Girmay (+0.26M, **10/10** top-hold havde ham) — manglede.
- **E5, E8:** samme mønster (Girmay, Bittner) — sprint-værdi du gik glip af.

Det bekræfter din egen intuition: det var **ikke** de aggregerede "missede
gevinster", men **konkrete sprint-etaper** hvor du ikke havde dagens rette spurter.

## Bedste mulige hold
| Strategi | Slutværdi |
|---|---|
| Bedste **buy-and-hold** (0 transfers, 0 gebyr) | **62.4M** |
| Dit hold (aktivt styret) | 77.0M |
| Bedste top-10-manager (aktivt styret) | 79.0M |
| **Loft** — perfekt rotation, ingen gebyr/budget | **92.7M** |

**Den vigtigste indsigt:** et passivt "køb 8 gode og hold hele løbet" topper ved
**62M** — *lavere end du selv nåede*. Man **kan ikke** eje 8 vækst-ryttere på én
gang (budgettet rækker ikke), så **transfers er hele spillet**. Dit aktive
holdstyr (77M) slog buy-and-hold med 15M.

Det realistiske optimum ligger mellem den bedste manager (79M) og loftet (92.7M).
Det præcise gebyr-bevidste optimum er et svært sekventielt optimeringsproblem
(budget + 1% gebyr + rotation) — bevidst *ikke* estimeret med et enkelt tal her,
da et forkert tal ville vildlede. Loftet (92.7M) ignorerer budget og er derfor
et løst overtal; sandheden er tættere på 80'erne.

> Forbehold: værdikurven bruger etape-priser (etape 4–21) + start/slut; etape 2–3
> interpoleret. Buy-and-hold-optimum er korrekt (knapsack); den viste hold-roster
> er *illustrativ*, ikke bevist optimal.
