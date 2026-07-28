# Manager-sammenligning — TdF 2026 (Task 2)

Dit hold (7145433) vs. Kasper (7132927) vs. de to optakt-skribenters hold
(7157567, 7132842) vs. de 10 bedste slut-managere — runde for runde.

**Metode:** hver runde scorer et hold summen af sine 8 rytteres realiserede
`actual`-vækst, med kaptajnen talt dobbelt (holdets kaptajnsregel). Samme
`actual`-mål som i indikator- og optakt-analyserne, så hele evalueringen hænger
sammen. Kør selv: `python scripts/analysis/eval_managers.py`.

> Forbehold: dette er en **vækst-attribution**, ikke holdets præcise
> pris/bank-værdi (bank er ikke i snapshottet). Men krydstjekket holder:
> rangeringen efter attribution (vi = nr. 11) matcher rangeringen efter ren
> slut-rytterværdi (vi = nr. 11), så metoden fanger det rigtige.

## Slutresultat

| # | Hold | Total (M) | Transfers | Kaptajn (M) | Kap-effektivitet |
|---|---|---|---|---|---|
| 1 | top-10 (7189610) | 33.48 | 87 | 8.29 | 0.88 |
| … | *top-10-feltet* | 32.1–33.5 | 73–87 | ~8.1 | 0.83–0.91 |
| — | **top-10 median** | **32.79** | | | |
| **11** | **Os (Anders)** | **31.98** | 78 | **8.59** | **0.92** |
| 12 | Kasper | 31.16 | 82 | 8.18 | 0.89 |
| 13 | optakt-skribent (7132842) | 31.14 | 81 | 7.91 | 0.86 |
| 14 | optakt-skribent (7157567) | 30.44 | 83 | 8.24 | 0.90 |

## De tre vigtigste fund

### 1. Kaptajnen var din STYRKE — ikke der du tabte
Du havde **den højeste kaptajn-høst (8.59M) og den bedste kaptajn-effektivitet
(0.92) af alle 14 hold** — inkl. top-10. Du ramte gennemgående den rigtige
kaptajn blandt dine egne ryttere. Største enkelt-fejlvalg var beskedne: R11
kaptajn Merlier (+0.04M) hvor din bedste egne gav +0.47M (tabt 0.42M på en
sprint der floppede). Det er ikke her løbet blev tabt.

### 2. Gap'et til top-10 var få missede vækst-ryttere
Du endte **0.8M under top-10-medianen**. Forklaringen ligger i to ryttere,
top-holdene ejede, som du **aldrig** havde:

| Rytter | Sæson-vækst | Ejet i (top-10 runder) |
|---|---|---|
| **Lenny Martinez** | **+1.66M** | 6 |
| **Tom Pidcock** | **+1.19M** | 3 |

Bare én af dem havde stort set lukket hele gabet til medianen. Det er den
konkrete læring: din kerne var stærk, men du missede et par mellemklasse-vækst-
ryttere i bjergene som top-holdene fangede. (Jordan Jegat var ejet i 58 top-10-
runder som billig udfylder, men gav kun 0.43M — de parkerede ham for pladsen,
ikke for væksten.)

### 3. Dine dårligste valg kostede næsten intet
De værste bidrag var bittesmå minusser (Rickaert −0.14M, Nicolau −0.10M) —
billige hjælpe-ryttere der tabte en anelse værdi. Ingen dyre fejlkøb. Din
disciplin på bunden af holdet var fin.

## En interessant krølle: optakt-skribenternes egne hold endte sidst
De to optakt-hold vi hentede endte **13. og 14.** af de 14 — altså *under* dig
og Kasper. Det modsiger ikke Task 4 (deres *picks* slog modellen): Simon K.
Kjær kører selv **flere hold** (nævner "hold A og B" + fem øvrige i optakten), så
de ID'er er formentlig ikke hans primære/optimerede hold. Pointen står: deres
*råd* havde signal, men lige de her hold var ikke top. Vær kritisk med at antage
at en god skribents *tilfældige* hold er værd at kopiere — det er rådet, ikke
holdet, der er guld.

## Verificering af top-10-ID'erne
Alle 10 opgivne ID'er tjekker ud: 9 ligger i toppen på ren rytterværdi
(77.2–79.0M), og den ene der stak ud (7184171, lavest rytterværdi 74.74M) ender
**nr. 9 på vækst-attribution** — et helt normalt top-10-hold med bare mere bank.
Ingen tastefejl. (Jeg kunne ikke bekræfte 79.1M-grænsen direkte, da bank ikke er
i snapshottet, men intet ID er forkert.)

## Til Vueltaen
- **Behold din kaptajns-tilgang** — den var i topklasse.
- **Vær mere aggressiv på mellemklasse-vækst-ryttere i bjergene** (Martinez/
  Pidcock-typen) — det var her top-holdene løb fra dig, ikke på stjernerne.
- **Kopiér råd, ikke skribenternes tilfældige hold.**
