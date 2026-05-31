# TdF Manager 2026

Fantasy cycling optimizer for holdet.dk — Tour de France 2026.

## Daglig brug

```bash
# 0. (Én gang) Opdater dit nuværende hold i:
#    data/current_team.json  ← redigér manuelt med dine 8 ryttere + bank-saldo

# 1. Upload VeloScore-screenshot til Claude → Claude gemmer JSON til data/stage_XX_veloscore.json

# 2. Kør optimizer
python run_daily.py --stage 5 --type sprint

# 3. Push til GitHub → siden opdaterer automatisk
git add web/data/recommendations.json && git commit -m "Etape 5" && git push
```

### data/current_team.json
```json
{
  "bank_M": 5.2,
  "riders": [
    "Jonas Vingegaard",
    "Jonathan Milan",
    "..."
  ]
}
```
Hold-anbefalingerne beregner automatisk hvilke ryttere du skal købe/sælge og hvad det koster (inkl. 1% gebyr). Siden viser også et ubegrænset "Bedst muligt"-hold nederst.

## Etapetyper
- `sprint` — massespurt
- `mountain` — bjergetape
- `tt` — enkeltstart
- `hilly` — kuperet / punch
- `cobbled` — brosten

## Datakilder
- **VeloScore** — daglig prediction-konsensus (manuelt screenshot → Claude parser)
- **CyclingOracle** — disciplineratings (SPR/MTN/ITT/FLT/HLL) — automatisk scraping
- **ProcyclingStats** — form og historik — automatisk scraping  
- **The Odds API** — bookmaker win-odds → implicitte sandsynligheder (kræver gratis API-nøgle)

## Opdater CyclingOracle cache
```bash
python run_daily.py --stage 5 --type sprint --scrape-co
```

## Model
Kalibreret på 19 etaper fra Giro d'Italia 2026.
Vægte: VeloScore 45% · Odds 25% · Disciplin 20% · Form 10%

## GitHub Pages
Siden hostes automatisk på `https://[dit-brugernavn].github.io/tdf-manager/`
efter push til `main`-branchen.
