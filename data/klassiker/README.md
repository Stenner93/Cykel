# Klassiker Manager — sådan holdes siden opdateret

Siderne `web/klassiker.html`, `web/klassiker-startlister.html` og
`web/klassiker-historik.html` bygges af ét script ud fra `config.json` her.
Indtil 2027-spillet åbner, kører de på 2026-data som skabelon.

```
python scripts/build/build_klassiker_web_data.py
```

Siden læser `web/data/klassiker.json` og `web/data/klassiker_riders.json`.
Der er intet årstal i filnavnene, så selve HTML-siderne skal ikke rettes fra
år til år.

## Den samlede opdatering, når 2027-spillet åbner på holdet.dk

1. **Spillet:** Sæt `holdet.cartridge` og `holdet.game_id` i `config.json`.
   Ret løbslisten, hvis kalenderen er ændret: rækkefølge, navne, `monument` og `group`.
2. **Kategorier fra Holdet:**
   `python scripts/analysis/import_holdet_categories.py --game-id <id> --year 2027`
   Sæt derefter `categories_year` til 2027.
3. **Ruteprofiler:** når PCS har 2027-profilerne, køres
   `python scripts/scrape/fetch_classics_profiles.py --year 2027`.
   Siden viser profilerne for `data_year`, så sæt `data_year` til 2027, når ruterne er ude.
   Kør derefter `python scripts/analysis/trace_classics_profiles.py --year 2027`. Det laver de
   mørke PCS-billeder og aflæser profilernes form og stregernes km.
   Piktogrammets stigninger og pavéer (`route_marks_2027.json`) aflæses i hånden fra billederne:
   navn, type (c, p, cp, g eller w), sektornummer og ★, med km fra `profile_trace_2027.json`.
   Bed assistenten om det. Findes filen ikke, viser siden bare PCS-billedet.
4. **Startlister:** filen fra fantasytool (som Kalender-fanen) importeres med
   `python scripts/analysis/import_klassiker_sheets.py startlists <fil> --sheet Kalender --year 2027`.
   Nye flueben flettes ind, og tomme celler sletter aldrig et gammelt flueben.
   Du kan også selv uploade filen på Startlister-siden. Den version ligger kun i din browser.
5. **Holdduel:** sæt de to hold-ID'er under `duel` i `config.json`.
6. **Resultater undervejs:** efter hvert løb køres
   `python scripts/scrape/scrape_classics_results.py --years 2027`.
   Læg 2027 til `history_years`, og byg siden igen.
7. **Optakter:** de lægges i `optakt_2027.json` med formen
   `{"races": {"<slug>": {"<kilde>": {"favoritter": [], "kaptajn": "", "outsidere": [], "noter": ""}}}}`.

## Filerne

| Fil | Hvad | Laves af |
|---|---|---|
| `config.json` | sæson, løb, blokke, grupper, regler, duel | i hånden |
| `data/sources/classics/results.json` | PCS-resultater 2021– | `scrape_classics_results.py` |
| `data/sources/classics/categories_<år>.json` | kategorier | `import_holdet_categories.py` / `import_classics_notes.py` |
| `data/sources/classics/startlists_<år>.json` | forventede startlister | `import_klassiker_sheets.py startlists` |
| `data/sources/classics/plan_<år>.json` | holdudkast til Planlæggeren | `import_klassiker_sheets.py plan` |
| `web/data/klassiker/profiles/<år>/` | profilbilleder (+ mørke `_dark.png`) | `fetch_classics_profiles.py` / `trace_classics_profiles.py` |
| `profile_trace_<år>.json` | profilform og stregernes km | `trace_classics_profiles.py` |
| `route_marks_<år>.json` | stigninger, pavéer og grus til piktogrammet | aflæst i hånden |
| `optakt_<sæson>.json` | optakter (valgfri) | assistenten |

Blokke, plan, egne startlister og tabelvalg, som du ændrer på siden, gemmes i
din browser pr. sæson. De overlever en ny opbygning af siden.
