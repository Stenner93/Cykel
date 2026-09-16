# På tværs af managerspil — TdF 2026 vs. Vuelta 2026

Formålet: hvilke fund fra TdF-analysen holder, og hvilke var TdF-specifikke?
Baseret på `indicator_eval[_vuelta].json`, `optakt_eval[_vuelta].json` og
`manager_eval[_vuelta].json`. Se de fire enkeltrapporter for detaljer og tal.

## Fund der GENTAGER SIG (robuste, ikke tilfældige)

1. **Ekstern konsensus (VeloScore) slår modellens egne signaler i begge løb.**
   TdF +0.56, Vuelta +0.33 — svagere absolut i Vuelta, men stadig klart
   stærkeste signal i begge, og i Vuelta endda foran i *alle* fire etapetyper
   (i TdF kun på sprint/kuperet).
2. **Det interne ML-signal (LightGBM) er anti-prædiktivt i begge løb** (TdF
   −0.16, Vuelta −0.12). To løb i træk er nok til at kalde det et reelt
   problem, ikke støj — laget bør enten gentrænes fra bunden med et langt
   større datasæt, eller fjernes/vægtes til ~0 i den blandede model.
3. **Optakterne (Feltet.dk, Simon K. Kjær) slår modellen på kaptajnsvalg i
   begge løb** — og gør det markant, ikke marginalt (model 20-33 % top-1 vs.
   optakter 35-52 %). Den "rensede" dømmekraftsscore (hvem rammer faktisk
   etapens bedste rytter, når kilderne er uenige) favoriserer optakterne
   endnu tydeligere end den rå vækst-optælling i begge løb.
4. **Sprint-etaper er systematisk sværest at forudsige** for alle kilder i
   begge løb — lavest korrelation, flest "kaos-dage" hvor ingen ramte rigtigt.
5. **Kaos-andelen findes i begge løb**, men var højere i Vuelta (8-9 af 14-16
   uenighedsdage) end TdF (4 af 11-12) — Vueltaen var samlet set sværere at
   "løse" for både model og optakter (lavere pooled Spearman: 0.29 mod 0.35).

## Fund der IKKE gentog sig (løbs- eller person-specifikke)

- **Kaptajn-effektivitet vendte fuldstændig**: du havde den bedste
  kaptajn-effektivitet af alle 14 hold i TdF (0.92), men den laveste af de 2
  sammenlignede hold i Vuelta (0.32) — drevet af gentagne Brennan-kaptajnvalg
  på dage der ikke gik hans vej. Dette er et *dit-valg*-mønster, ikke et
  model- eller løbsmønster — værd at være opmærksom på fremadrettet uafhængigt
  af værktøjerne.
- **placement_pred alene slog den blandede model i TdF, men ikke i Vuelta**
  (hvor blandingen var marginalt foran) — for tyndt et datagrundlag (21 og 20
  etaper) til at konkludere noget generelt her.

## Infrastruktur-fund (fix før Klassiker Manager i foråret)

Under arbejdet med at genskabe Vuelta-analysen dukkede tre reelle, tidligere
usynlige huller op i data-pipelinen:

1. **`build_analytics.py` har kørt hardcoded mod TdF-filer hele sæsonen** —
   den har ALDRIG læst Vuelta-data, selvom `daily.yml` kaldte den dagligt
   under hele Vueltaen. Konsekvens: "Analytik"-dashboardet (`tdf2026_analytics.json`)
   har vist frossen TdF-data i månedsvis uden at nogen så det, og Vueltaen
   fik aldrig sin egen version. **Fix:** generalisér scriptet med samme
   `--race`-mønster som de øvrige (nu rettede) analyse-scripts, FØR Klassiker
   Manager starter, og bekræft det rent faktisk kører mod det aktive løbs
   data i den daglige pipeline.
2. **`data/cache/stage_snapshots.json` (rytter-pris/ejerskab pr. etape) findes
   kun for TdF** — fordi den udelukkende skrives inde i samme hardcoded
   `build_analytics.py`. Det betyder at etape-niveau-analysen og
   best-team-MILP'en fra TdF-rapporten **ikke kan gentages for Vuelta** med
   det nuværende datagrundlag — den historik blev aldrig fanget. (Eneste
   resterende vej: udlede pris-forløb af de ~139 daglige bot-commits til
   `data/cache/holdet_players.json` i git-historikken — muligt, men ikke
   gjort i denne omgang.) Samme fix som ovenfor løser det fremadrettet.
3. **`eval_managers.py` slog fejl for TdF ved gentest** — den byggede
   spiller-ID→navn af den *aktuelle* `data/cache/holdet_players.json`, som nu
   indeholder Vuelta-data, ikke TdF's. Alle TdF-rytternavne slog fejl, og hele
   holdsammenligningen kollapsede stille til nuller uden fejlmelding. **Rettet
   i denne session**: scriptet bygger nu navne fra løbets egen gemte
   `reference/players.json` i stedet for den delte, foranderlige cache — en
   reel bug, ikke kun en generalisering.
4. **Holdet.dk's leaderboard-endpoint til top-10-opdagelse virker ikke
   ensartet på tværs af spil** — `games/628/leaderboard` (Vueltaspillet) gav
   ikke gyldig JSON, mens det formentlig virkede for TdF's spil-ID. Vueltaens
   manager-sammenligning dækker derfor kun dig og Kasper, ikke top-10. Løsning
   for Klassiker Manager: find og hardcod det rigtige endpoint tidligt, eller
   accepter manuel `--team-ids`-indtastning som fast rutine.

## Handling frem mod Klassiker Manager (forår 2027)

- Vægt ekstern konsensus (VeloScore) og optakt-kaptajnvalg tungere end
  modellens eget `exp`/ML-lag — det er nu bekræftet to løb i træk.
- Fjern eller gentræn ML-laget; det har kostet mere end det har givet begge
  gange.
- Ret de tre infrastrukturhuller ovenfor, **før** løbet starter — ikke
  bagefter. Særligt punkt 1+2 betyder at vi kun får ét forsøg pr. sæson på at
  fange etape-niveau-historikken; den kan ikke genskabes bagefter.
- Kør `snapshot_holdet_teams.py --race <næste-løb>` proaktivt undervejs i
  løbet (fx ugentligt), ikke kun som brandslukning lige inden spillet lukker.
