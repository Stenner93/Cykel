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

## Holdbonus — ryttere i etapens top-15 (alle 20 etaper)
Holdet giver en bonus for at have 6/7/8 af dine 8 ryttere i etapens top-15. Kilde:
holdets **egne placeringsregler (849–863 = 1.–15. plads)** i
`tdf2026_scores.json` — eksakt slug-match, alle etaper 2–21 (E1 er TTT, ingen
individuel top-15). Din frygt var **berettiget**, og med de fulde data ses den
klart: du leakede på **sprint-etaperne**.

| Etape | Type | Dine i top-15 | Top-10 median | |
|---|---|---|---|---|
| E7 | sprint | **5** | 8 | **misset bonus** |
| E8 | sprint | **5** | 7 | **misset bonus** |
| E11 | sprint | **6** | 7 | **misset (mindre)** |
| E12 | sprint | **6** | 8 | **misset (mindre)** |
| E15 | bjerg | **4** | 6 | **misset bonus** |
| E21 | sprint | **3** | 5 | under (ingen nåede 6) |

**Men i bjergene ramte du tærsklen:** E14, E18, E19, E20 havde du **6** ryttere i
top-15 — på niveau med top-10. Problemet er **specifikt bunch-spurterne**, hvor du
lå på 5 mens top-holdene havde 7–8. Samme rod som stjerne-fundet: de spurtere du
manglede (Wærenskjold, Girmay) gav både etape-vækst *og* holdbonus.

> Fikset: tidligere stod 10 etaper som "n/a" fordi PCS-resultatcachen kun dækkede
> 11 etaper. Men holdets egen scoring-matrix (`tdf2026_scores.json`) havde
> placeringsreglerne hele tiden — nu bruges de, så 20 af 21 etaper er dækket
> eksakt. Kun de præcise bonus-*kroner* mangler stadig (kræver holdets
> fantasy-actions); tallene her er antal ryttere + tærskler.

## Bedste mulige hold — hvorfor vi IKKE giver et præcist tal
Det tidligere "loft" på 92,7M var forkert: det lagde bare de 8 største stigninger
sammen hver etape og ignorerede alle regler (8 ryttere, maks 2 pr. rigtige hold,
budget, transfergebyrer). Vi byggede derfor en rigtig MILP (PuLP/CBC) med alle
reglerne — men stødte på en **data-begrænsning der gør et troværdigt tal umuligt**:

Simulerer man de *faktiske* hold under en korrekt cash-flow-model med vores
etape-priser, ender **alle** (også top-10) med en **bank på −10 til −11M** — altså
umuligt (man kan ikke gå i gæld). Årsag: managere køber vækst-ryttere *før* etapen,
men vores `stage_snapshots` registrerer prisen *efter* stigningen. Vores priser er
altså for høje på købstidspunktet, så budget/gebyr-regnestykket ikke holder.

| Kendt & korrekt | Slutværdi |
|---|---|
| Bedste **buy-and-hold** (0 transfers, knapsack, korrekt) | **62.4M** |
| Dit hold (aktivt styret, faktisk) | 77.0M |
| Bedste top-10-manager (faktisk) | 79.0M |

Vi prøvede også at **lagge priserne med én etape** (køb til prisen efter forrige
etape, som man reelt gør) — men det gjorde det værre (bank −19 til −20M), fordi
salg *også* sker til den lavere laggede pris. Ingen af varianterne holder.

**Konklusion:** et præcist "fejlfrit hold"-tal kræver de **faktiske
transaktionspriser** (hvad ryttere kostede da man reelt købte dem), som vi ikke
har. Derfor er hele afsnittet **fjernet fra visualiseringen** (efter aftale). Det
vi ved: passiv buy-and-hold topper ~62M, og de bedste managere nåede 77–79M nær
det praktisk opnåelige. Der var **ikke** et skjult 90M-hold.
> `scripts/analysis/best_team_milp.py` + `best_team_milp.json` dokumenterer
> forsøget og data-begrænsningen (inkl. lag-varianten).

## Holdbonus — den præcise missede værdi
Med de eksakte bonus-beløb (`etapebonus`: 8→400k, 7→220k, 6→120k, 5→65k, 4→35k):
du tjente **1,13M** i holdbonus, top-10-medianen **2,19M** — du **missede 1,07M**
(0,95M alene på de 5 fremhævede sprint-/bjergetaper: E7 −335k, E12 −280k, E8 −155k,
E11 −100k, E15 −85k). Det er på størrelse med hele dit efterslæb — sprint-
under-satsningen var altså en *reelt dyr* fejl, ikke en detalje.

> Forbehold: værdikurven bruger etape-priser (etape 4–21) + start/slut; etape 2–3
> interpoleret. Buy-and-hold-optimum er korrekt (knapsack); den viste hold-roster
> er *illustrativ*, ikke bevist optimal.

## Validering af væksten (mod jeres scraping)
Er væksten overhovedet regnet rigtigt? Krydstjek af de priser kurven bygger på
(`stage_snapshots`) mod de **autoritative slutpriser fra holdet-snapshottet**
(`players.json`): gennemsnitlig afvigelse **0,035M** (35.000 kr), 155/183 ryttere
inden for 0,1M. De største afvigelser (~0,45M) er på de hurtigst stigende ryttere
(van der Poel, Philipsen), hvor snapshottet er taget lige før den allersidste
pris. Slutværdien i runde 21 bruger den eksakte snapshot-pris (77,03M). **Væksten
er altså solid.**

## Fiks af de manglende top-15-data (ikke en omgåelse)
Top-15 var ufuldstændig fordi resultat-cachen (`gt_stage_results`, fra PCS) kun
dækker 11 etaper. Den rigtige kilde er holdets egne **fantasy-actions**
(placeringsregler 849–863 = 1.–15. plads), som også driver `actual`-væksten —
den var komplet for alle 21 etaper under løbet, men blev ikke gemt rå.
`snapshot_holdet_teams.py` er nu rettet (den brugte et forkert schedule-format og
fik 0 events): den henter nu fantasy-actions pr. etape og skriver
`stage_results.json` (placering + point pr. rytter, alle 21 etaper, eksakt
personId-match — ingen navne-gætteri). Kør scriptet igen lokalt og push, så
færdiggøres holdbonus-analysen for **alle 21 etaper** automatisk, og vi kan
samtidig krydstjekke `actual`-væksten mod holdets egne point.
> PCS og holdet er begge blokeret fra dette miljø, så genindhentningen skal ske
> lokalt (som sidst).
