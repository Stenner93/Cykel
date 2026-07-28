"""
Scraper til aktuelle GT-etaperesultater (TdF/Giro/Vuelta).

Henter resultater for allerede kørte etaper i det aktuelle GT og
gemmer dem i data/cache/gt_stage_results.json.

Bruges af src/ml_signal.py til at beregne rullende GT-form-features.
Stop automatisk når en etape ikke har resultater endnu (ikke kørt).

Usage:
    python scrape_gt_results.py              # aktuelle løb (DEFAULT_CARTRIDGE)
    python scrape_gt_results.py --stages 1 3 # kun bestemte etaper (test)
    python scrape_gt_results.py --reset      # ryd cache og hent alt
"""
from __future__ import annotations
import argparse
import json
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent.parent
CACHE_DIR = ROOT / "data" / "cache"
OUT_PATH  = CACHE_DIR / "gt_stage_results.json"

DELAY = 0.8

# Race config — synkroniseret med scrape_holdet.py CARTRIDGE_TO_PCS_RACE
RACE_CONFIG: dict[str, dict] = {
    "tour-de-france-2026": {
        "race_key":   "tdf",
        "race_slug":  "tour-de-france",
        "year":       2026,
        "max_stages": 21,
    },
    "giro-d-italia-2026": {
        "race_key":   "giro",
        "race_slug":  "giro-d-italia",
        "year":       2026,
        "max_stages": 21,
    },
    "vuelta-a-espana-2026": {
        "race_key":   "vuelta",
        "race_slug":  "vuelta-a-espana",
        "year":       2026,
        "max_stages": 21,
    },
}


def main() -> None:
    global DELAY
    from scrape_holdet import DEFAULT_CARTRIDGE

    parser = argparse.ArgumentParser()
    parser.add_argument("--cartridge", default=DEFAULT_CARTRIDGE,
                        help="Holdet-cartridge (default: %(default)s)")
    parser.add_argument("--stages", nargs="+", type=int,
                        help="Kun disse etapenumre (udelad = alle afviklede)")
    parser.add_argument("--reset",  action="store_true",
                        help="Ryd cache og hent alt forfra")
    parser.add_argument("--delay", type=float, default=DELAY)
    args = parser.parse_args()
    DELAY = args.delay

    config = RACE_CONFIG.get(args.cartridge)
    if not config:
        print(f"Ukendt cartridge: {args.cartridge!r}")
        print(f"Kendte: {list(RACE_CONFIG)}")
        return

    race_key  = config["race_key"]
    race_slug = config["race_slug"]
    year      = config["year"]

    # Indlæs eksisterende cache
    if OUT_PATH.exists() and not args.reset:
        existing = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        if existing.get("race") != race_key or existing.get("year") != year:
            print(f"Cache er for andet løb/år ({existing.get('race')} {existing.get('year')}) — rydder")
            existing = {"race": race_key, "race_slug": race_slug, "year": year, "stages": {}}
    else:
        existing = {"race": race_key, "race_slug": race_slug, "year": year, "stages": {}}

    stages_cache: dict[str, list] = existing.setdefault("stages", {})

    # Reuse fetch logic fra scrape_pcs_history.py
    from scrape_pcs_history import fetch_stage_results

    session = requests.Session()
    to_fetch = args.stages if args.stages else range(1, config["max_stages"] + 1)
    n_new = 0

    for sn in to_fetch:
        skey = str(sn)
        if skey in stages_cache and not args.reset:
            print(f"  Etape {sn:>2}: cache hit ({len(stages_cache[skey])} ryttere)")
            continue

        print(f"  Etape {sn:>2} ...", end=" ", flush=True)
        results = fetch_stage_results(session, race_slug, year, sn)

        if not results:
            print("ikke tilgængelig endnu — stopper")
            break  # Etaper er sekventielle — stop når en etape ikke er kørt endnu

        stages_cache[skey] = [
            {
                "rider_slug": r["rider_slug"],
                "position":   r["position"],
                "dnf":        r["dnf"],
            }
            for r in results
        ]
        print(f"{len(results)} ryttere")
        n_new += 1
        time.sleep(DELAY)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nFærdig: {n_new} nye etaper hentet → {len(stages_cache)} etaper totalt i cache")
    print(f"Gemt: {OUT_PATH}")


if __name__ == "__main__":
    main()
