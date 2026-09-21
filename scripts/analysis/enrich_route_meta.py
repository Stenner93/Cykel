#!/usr/bin/env python3
"""
Beriger en rutefil med etapemetadata der allerede ligger i repoet.

Rutefilen (data/routes/<race>.json) er den ene fil der skal udfyldes til et
nyt løb. Den bærer det pointmotoren skal bruge (kategori, stigninger,
målstigning, indlagte spurter), men ruteplanlæggeren vil også gerne vise
profilscore, højdemeter og etapenavn, så man kan skimme hele løbet.

De tal er også ren ruteinformation — de hentes fra procyclingstats før løbet
og ligger i data/cache/pcs_profile_scores.json. Dette script flytter dem ind
i rutefilen, så planlæggeren kun skal læse ét sted.

Brug:
    python scripts/analysis/enrich_route_meta.py data/routes/vuelta2026.json \
        --pcs-slug vuelta-a-espana/2026 \
        --names web/data/vuelta2026_scores.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/pcs_profile_scores.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("route")
    ap.add_argument("--pcs-slug", required=True,
                    help="fx vuelta-a-espana/2026 — nøglen i pcs_profile_scores.json")
    ap.add_argument("--names", help="JSON med stages[] der har num/name/type (fx et scores-datasæt)")
    args = ap.parse_args()

    path = Path(args.route)
    route = json.loads(path.read_text())
    cache = json.loads(CACHE.read_text())
    scores = cache.get(f"{args.pcs_slug}_scores", {})
    vmeters = cache.get(f"{args.pcs_slug}_vmeters", {})

    names, types = {}, {}
    if args.names:
        for s in json.loads(Path(args.names).read_text()).get("stages", []):
            names[s["num"]] = s.get("name")
            types[s["num"]] = s.get("type")

    hit = 0
    for st in route["stages"]:
        n = st["stage"]
        st["name"] = names.get(n)
        st["pcs_type"] = types.get(n)
        st["profile_score"] = scores.get(str(n))
        st["vmeters"] = vmeters.get(str(n))
        hit += st["profile_score"] is not None

    path.write_text(json.dumps(route, ensure_ascii=False, indent=2))
    print(f"{path}: profilscore/højdemeter på {hit}/{len(route['stages'])} etaper")


if __name__ == "__main__":
    main()
