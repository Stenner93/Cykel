#!/usr/bin/env python3
"""
Engangs-probe: Holdet.dk's discover_cartridge()-endpoint returnerer 404 for
"vuelta-2026" (og games/628 + schedules/628 er også døde) fra og med
2026-09-08's kørsler. Formålet er at finde den FAKTISKE nuværende slug/
gameId for Vueltaspillet, så scrape_holdet.py's KNOWN_GAME_IDS kan rettes.

Kør via .github/workflows/probe-holdet-cartridges.yml (workflow_dispatch) —
output læses i job-loggen, og både denne fil og workflow'en slettes bagefter.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scrape_holdet import discover_cartridge, list_cycling_cartridges, KNOWN_GAME_IDS

print("=" * 60)
print("  Holdet cartridge-probe")
print("=" * 60)

print("\n-- Direkte tjek af kendte/mistænkte slugs --")
for slug in [
    "vuelta-2026", "vuelta-a-espana-2026", "vueltaspillet-2026",
    "la-vuelta-2026", "la-vuelta-a-espana-2026",
]:
    info = discover_cartridge(slug)
    if info:
        print(f"  FOUND: {slug!r}  gameId={info['game_id']}  leagueId={info['league_id']}  ({info['name']})")
    else:
        print(f"  miss:  {slug!r}")

print("\n-- Bred kandidatliste (list_cycling_cartridges) --")
list_cycling_cartridges()

print("\n-- Nuværende KNOWN_GAME_IDS i koden --")
for slug, gid in KNOWN_GAME_IDS.items():
    print(f"  {slug!r}: {gid}")
