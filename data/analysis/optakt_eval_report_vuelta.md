# Optakter vs. model — Vuelta 2026

Slog de kvalitative optakter (Feltet.dk og Simon K. Kjær/TheFantasyTool)
vores kvantitative model på den **faktiske** holdet-vækst, ligesom i TdF?
Picks er udtrukket direkte fra den allerede strukturerede
`web/data/vuelta2026_optakt.json` (ingen ekstraktions-agent nødvendig denne
gang — Vuelta-optakterne blev kodet løbende hen over sæsonen). "Køb" =
kildens eksplicitte hold (`team.buys`) når det var angivet i teksten, ellers
top-8 favoritter.

Kør selv: `python scripts/analysis/eval_optakter.py --race vuelta2026`
Kilder: `data/analysis/optakt_picks_{feltet,simon}_vuelta.json`

## Resultat

| Kilde | Kaptajn top-1 | Kaptajn top-3 | Køb-percentil |
|---|---|---|---|
| **Model** | 20 % | 45 % | 0.82 |
| Feltet.dk | **42 %** | 47 % | 0.86 |
| Simon K. Kjær | 35 % | 45 % | **0.88** |

### Konklusion: mønsteret fra Touren gentager sig
**Begge optakter slog modellen på kaptajn-ramning igen** — Feltet.dk endnu
mere markant end i TdF (42 % mod 33 % dengang), Simon lidt svagere relativt
(35 % mod 52 % i TdF, men stadig foran modellens 20 %). Køb-percentilen er
også højere hos begge optakter end modellen, ligesom i TdF.

### Uenigheds-dagene, renset for kaos
| Kilde | Uenige | Kilde ramte #1 | Model ramte #1 | Kaos (ingen ramte) | Rå vækst-optælling |
|---|---|---|---|---|---|
| Feltet | 14 | **5** | 1 | 8 | 10-4 |
| Simon | 16 | **5** | 2 | 9 | 10-6 |

Samme billede som i TdF: den rå vækst-optælling (10-4/10-6) overdriver
modellens andel af "sejre" — den reelle dømmekrafts-score, når man kun tæller
hvem der faktisk landede etapens bedste rytter, er **5-1 og 5-2 til
optakterne**. Kaos-andelen (8-9 af 14-16 uenighedsdage) er også højere end i
TdF (4 af 11-12) — Vueltaen havde flere dage hvor *ingen* af kilderne ramte
rigtigt, hvilket matcher indikator-evalueringens fund om at Vueltaen generelt
var sværere at forudsige.

## Konsekvenser
Uændret fra TdF-konklusionen, nu bekræftet på tværs af to løb:
1. **Brug optakterne som primær kaptajnskilde.**
2. **Modellen er en tjekliste/backup, ikke en primær beslutning.**
3. **Kombinér model/VeloScore (screening) med optakt (endeligt valg).**

## Forbehold
- 20 etaper, lille sample — retningsgivende.
- Køb-percentilen blander to forskellige kildetyper (eksplicit `team.buys` vs.
  favorit-liste) afhængigt af hvor eksplicit den enkelte etapes optakt var —
  se schema-noten i `vuelta2026_optakt.json`.
