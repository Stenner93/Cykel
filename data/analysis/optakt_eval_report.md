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

### Uenigheds-dagene: pas på det misvisende "6-5 / 7-5"
Ser vi *kun* på de etaper hvor optakten valgte en **anden** kaptajn end modellen,
er den rå vækst-optælling tæt (Simon 7-5, Feltet 6-5) — men det tal **pynter
kraftigt på modellen**. Det tæller nemlig "min middelmådige kaptajn voksede en
tøddel mere end din middelmådige kaptajn" som en sejr til modellen, selv når
*ingen* af de to ramte etapens faktisk bedste rytter.

Renser man for de dage (kaos-/udbryderdage hvor ingen af tilgangene duede) og
ser på **hvem der faktisk ramte etapens bedste kaptajn**, er billedet langt mere
ensidigt:

| Kilde | Uenige | Kilde ramte #1 | Model ramte #1 | Kaos (ingen ramte) | Rå vækst-optælling |
|---|---|---|---|---|---|
| Feltet | 11 | **5** | 2 | 4 | 6-5 |
| Simon | 12 | **6** | 2 | 4 | 7-5 |

**Den reelle dømmekrafts-score er 5-2 og 6-2 til optakterne**, ikke 6-5/7-5.
Modellens "5 tabte sejre" er hule: kun 2 af dem ramte faktisk etapens bedste
rytter — de øvrige 3 er bare mindre-dårlige gæt på dage hvor begge tog fejl.
Det er også præcis derfor `cap top1` (ram plet) adskiller model og optakt så
meget (33 % vs 52 %), mens den rå win/loss ikke gør: **top-1 belønner at ramme
den rigtige; win/loss belønner også bare at tabe mindre grimt end den anden.**

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
