#!/usr/bin/env python3
"""
Engangs-probe, runde 3: brugeren fandt via browser DevTools (Network-fanen på
holdet.dk's egen holdside) det faktiske request:

    holdet.dk/api/season/games/628/players

Det afslører TO ting på én gang ift. den gamle BASE
(https://nexus-app-fantasy-fargate.holdet.dk/api/games/628/players):
  1. Domænet er nu holdet.dk selv (ikke en separat fargate-subdomæne)
  2. Stien har fået et "/season/"-præfiks: /api/season/games/... i stedet
     for /api/games/...

Dette script bekræfter den nøjagtige struktur og tjekker om det samme
mønster gælder cartridges/schedules-endpoints, som scrape_holdet.py også
er afhængig af.

Kør via .github/workflows/probe-holdet-cartridges.yml (workflow_dispatch) —
output læses i job-loggen, og både denne fil og workflow'en slettes bagefter.
"""
import requests

CANDIDATE_BASES = [
    "https://holdet.dk",
    "https://www.holdet.dk",
]

PATHS = [
    "/api/season/games/628/players",
    "/api/season/games/628",
    "/api/season/games/618",
    "/api/season/cartridges/vuelta-2026",
    "/api/season/cartridges/tour-de-france-2026",
    "/api/season/schedules/628",
    "/api/season/schedules/618",
    "/api/games/628/players",  # gammel sti-form, for en sikkerheds skyld
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CykelManagerBot/1.0)"}

print("=" * 60)
print("  Holdet /api/season/ probe")
print("=" * 60)

for base in CANDIDATE_BASES:
    print(f"\n-- {base} --")
    for path in PATHS:
        url = base + path
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            ctype = resp.headers.get("Content-Type", "")
            size = len(resp.content)
            print(f"  {resp.status_code}  {path}  ({ctype}, {size} bytes)")
            if resp.status_code == 200 and "json" in ctype:
                snippet = resp.text[:300].replace("\n", " ")
                print(f"      body: {snippet}")
        except requests.exceptions.RequestException as exc:
            print(f"  ERR   {path}  {exc.__class__.__name__}: {exc}")
