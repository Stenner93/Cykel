#!/usr/bin/env python3
"""
Engangs-probe: Holdet.dk's discover_cartridge()-endpoint (BASE =
https://nexus-app-fantasy-fargate.holdet.dk) returnerer 404 for SAMTLIGE
kendte slugs — også "vuelta-2026" og "tour-de-france-2026", som begge har
virket hele sæsonen. Det tyder på at hele domænet/BASE er skiftet, ikke
bare at et enkelt spil er lukket.

En WebSearch efter "nexus-app-fantasy-fargate" på holdet.dk gav et hit på
domænet "nexus-app-fantasy.holdet.dk" (UDEN "-fargate") i en cookie-consent
metadata-reference — dette script tjekker om DET er den nye korrekte BASE.

Kør via .github/workflows/probe-holdet-cartridges.yml (workflow_dispatch) —
output læses i job-loggen, og både denne fil og workflow'en slettes bagefter.
"""
import requests

CANDIDATE_BASES = [
    "https://nexus-app-fantasy-fargate.holdet.dk",  # nuværende BASE i scrape_holdet.py
    "https://nexus-app-fantasy.holdet.dk",          # WebSearch-hit uden "-fargate"
    "https://app-fantasy.holdet.dk",
    "https://fantasy.holdet.dk",
    "https://api.holdet.dk",
]

PATHS = [
    "/api/cartridges/vuelta-2026",
    "/api/cartridges/tour-de-france-2026",
    "/api/games/628",
    "/api/games/618",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CykelManagerBot/1.0)"}

print("=" * 60)
print("  Holdet BASE-domæne probe")
print("=" * 60)

for base in CANDIDATE_BASES:
    print(f"\n-- {base} --")
    for path in PATHS:
        url = base + path
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            ctype = resp.headers.get("Content-Type", "")
            snippet = resp.text[:200].replace("\n", " ")
            print(f"  {resp.status_code}  {path}  ({ctype})")
            if resp.status_code == 200:
                print(f"      body: {snippet}")
        except requests.exceptions.RequestException as exc:
            print(f"  ERR   {path}  {exc.__class__.__name__}: {exc}")
