# Optakter vs. model — TdF 2026

Slog de kvalitative optakter (bag betalingsmur) vores kvantitative model på den
**faktiske** holdet-vækst? Feltet.dk's og Simon K. Kjærs daglige picks (etape
1–21) blev kodet til strukturerede signaler (kaptajn, køb, udbrydertips) af to
læse-agenter, og testet mod den realiserede `actual`-vækst pr. rytter pr. etape.

Kør selv: `python scripts/analysis/eval_optakter.py`
Kilder: `data/analysis/optakt_picks_{feltet,simon}.json`

## Resultat

| Kilde | Kaptajn top-1 | Kaptajn top-3 | Køb-percentil* |
|---|---|---|---|
| **Model** | 33 % | 57 % | 0.82 |
| Feltet.dk | 48 % | 62 % | 0.86 |
| **Simon K. Kjær** | **52 %** | **71 %** | 0.84 |

\* Køb-percentil: gennemsnitlig placering af de anbefalede ryttere i etapens
vækstfelt (1.0 = feltets bedste, 0.5 = gennemsnit). Alle tre vælger relevante
ryttere; optakterne en anelse skarpere.

### Konklusion: ja, dit instinkt holder
**Begge optakter slog modellen på kaptajnsvalg — Simon markant.** Simon (vinder
af Girospillet 2025) ramte den præcist bedste kaptajn hver anden etape (52 %) og
top-3 på 71 %, mod modellens 33 % / 57 %. Det er en reel, mærkbar forskel på det
valg der betyder mest i holdet (kaptajnens dobbeltpoint).

### Men nuancen: edgen ligger mest i de "oplagte" dage
Da vi ser *kun* på de etaper hvor optakten valgte en **anden** kaptajn end
modellen, er det tættere på lige:

| Kilde | Uenige med model | Vandt | Tabte |
|---|---|---|---|
| Feltet | 11 etaper | 6 | 5 |
| Simon | 12 etaper | 7 | 5 |

Dvs. optakternes forspring kommer dels af at ramme de oplagte kaptajner (Pogačar,
Merlier) lige så sikkert som modellen, dels af en lille — men reel — edge på
dømmekraft-dagene (7-5 / 6-5). De taber ikke terræn på jokerne; de vinder en
smule.

## Konsekvenser frem mod Vueltaen
1. **Brug optakterne som primær kaptajnskilde — især en betroet, dygtig
   skribent (Simon-typen).** Det var det stærkeste enkeltinput vi målte, foran
   både modellen og VeloScore alene.
2. **Modellen bør degraderes til tjekliste/backup**, ikke primær beslutning —
   den er god til at bekræfte konsensus, svag til at slå den.
3. **Kombinér:** brug modellen + VeloScore til at *screene* (fange oversete
   billige ryttere, undgå fælder) og optakten til det endelige kaptajns- og
   satsningsvalg. De to fejler på forskellige måder.

## Forbehold
- 21 etaper er et lille sample; forskellene er retningsgivende, ikke
  statistisk skudsikre.
- `actual` er den holdet-værdi modellen trænes mod (værdivækst). En
  point-baseret gentagelse følger når holdet-snapshottet er hentet.
- Køb-percentilen vægter alle anbefalinger lige og ser bort fra pris/budget —
  den måler rytter*udvælgelse*, ikke fuld holdoptimering.
- Kaptajns-picks er udtrukket af prosa af sprogmodeller; enkelte kan være
  fejltolket (1 umatchet navn skyldes en stavefejl i kilden).
