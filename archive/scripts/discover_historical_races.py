"""
Auto-discover historical Holdet.dk cycling race game IDs.

Prøver alle kendte slug-mønstre for Giro/TdF/Vuelta 2019-2025 og finder
de tilgængelige spil via Holdet API.

Kør lokalt (holdet.dk er blokeret i cloud):
    python discover_historical_races.py

Output:
    Printer en RACE_CONFIGS liste klar til kopiering ind i scrape_historical_holdet.py
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from typing import Optional

BASE = "https://nexus-app-fantasy-fargate.holdet.dk"

SLUG_PATTERNS = [
    # Giro d'Italia
    "giro-d-italia-{year}",
    "girospillet-{year}",
    "giro-{year}",
    # Tour de France
    "tour-de-france-{year}",
    "tourspillet-{year}",
    "tdf-{year}",
    # Vuelta a España
    "vuelta-{year}",
    "vuelta-a-espana-{year}",
    "vueltaspillet-{year}",
]

RACE_LABELS = {
    "giro": "Giro d'Italia",
    "girospillet": "Giro d'Italia",
    "tour": "Tour de France",
    "tourspillet": "Tour de France",
    "tdf": "Tour de France",
    "vuelta": "Vuelta a España",
    "vueltaspillet": "Vuelta a España",
}

PCS_RACES = {
    "giro": "giro-d-italia/{year}",
    "girospillet": "giro-d-italia/{year}",
    "tour": "tour-de-france/{year}",
    "tourspillet": "tour-de-france/{year}",
    "tdf": "tour-de-france/{year}",
    "vuelta": "vuelta-a-espana/{year}",
    "vuelta-a-espana": "vuelta-a-espana/{year}",
    "vueltaspillet": "vuelta-a-espana/{year}",
}


def fetch_cartridge(slug: str) -> Optional[dict]:
    url = f"{BASE}/api/cartridges/{slug}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
            games = data.get("_embedded", {}).get("games", {})
            if not games:
                return None
            game_id = int(list(games.keys())[0])
            return {"slug": slug, "game_id": game_id, "name": data.get("name", slug)}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        print(f"  HTTP {e.code} for {slug}")
        return None
    except Exception as exc:
        print(f"  Fejl for {slug}: {exc}")
        return None


def guess_race_type(slug: str) -> str:
    slug_lower = slug.lower()
    for key in RACE_LABELS:
        if key in slug_lower:
            return key
    return "unknown"


def guess_pcs_race(slug: str, year: int) -> str:
    slug_lower = slug.lower()
    for key, pattern in PCS_RACES.items():
        if key in slug_lower:
            return pattern.format(year=year)
    return ""


def main():
    years = list(range(2019, 2026))
    found = []

    print(f"Søger efter historiske Holdet-løb (2019–2025)…\n")

    for year in years:
        tried = set()
        for pattern in SLUG_PATTERNS:
            slug = pattern.format(year=year)
            if slug in tried:
                continue
            tried.add(slug)

            print(f"  {slug}… ", end="", flush=True)
            result = fetch_cartridge(slug)
            if result:
                race_type = guess_race_type(slug)
                label = RACE_LABELS.get(race_type, slug)
                pcs_race = guess_pcs_race(slug, year)
                entry = {
                    "cartridge": slug,
                    "game_id": result["game_id"],
                    "pcs_race": pcs_race,
                    "label": f"{label} {year}",
                    "n_stages": 21,
                }
                found.append(entry)
                print(f"FUNDET! game_id={result['game_id']}, name={result['name']!r}")
            else:
                print("ikke fundet")
            time.sleep(0.2)

    print(f"\n{'='*60}")
    print(f"Fandt {len(found)} løb:\n")

    # Print ready-to-paste RACE_CONFIGS
    print("RACE_CONFIGS: list[dict] = [")
    for e in found:
        print(f"    {{")
        print(f'        "cartridge": "{e["cartridge"]}",')
        print(f'        "game_id":   {e["game_id"]},')
        print(f'        "pcs_race":  "{e["pcs_race"]}",')
        print(f'        "label":     "{e["label"]}",')
        print(f'        "n_stages":  {e["n_stages"]},')
        print(f"    }},")
    print("]")

    # Also save to JSON
    out_path = "data/cache/historical_race_ids.json"
    import os
    os.makedirs("data/cache", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(found, f, ensure_ascii=False, indent=2)
    print(f"\nGemt til: {out_path}")
    print("\nNæste trin:")
    print("  Kopier RACE_CONFIGS ind i scrape_historical_holdet.py og kør det")


if __name__ == "__main__":
    main()
