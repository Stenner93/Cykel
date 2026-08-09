#!/usr/bin/env python3
"""Berig en *_predictions.json med hver rytters FULDE CyclingOracle-ratings.

Bygge-scripterne (build_tdf/build_dauphine, og fremtidige build_vuelta klonet
herfra) skriver nu selv feltet 'co' pr. rytter. Dette script efter-beriger
eksisterende/historiske eksport-filer med samme felt, så scenarievælgeren kan
om-rangere efter en hvilken som helst disciplin-blanding (fx reduceret spurt =
HLL+SPR) på ALLE etaper — ikke kun etapens egen disc_key.

Kør:  python3 scripts/build/enrich_predictions_co.py web/data/giro2026_predictions.json [flere...]
"""
import json, sys, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CO   = ROOT / "data" / "cache" / "cyclingoracle.json"

def norm(s): return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()

def load_co():
    raw = json.load(open(CO, encoding="utf-8"))
    by_id, by_name = {}, {}
    for key, d in raw.items():
        ratings = {k: round(v) for k, v in (d.get("ratings") or {}).items()}
        by_id[key] = ratings
        by_name[norm(d.get("name", ""))] = ratings
    return by_id, by_name

def enrich(path, by_id, by_name):
    doc = json.load(open(path, encoding="utf-8"))
    n = hit = 0
    for st in doc.get("stages", []):
        for r in st.get("riders", []):
            n += 1
            co = by_id.get(r.get("id")) or by_name.get(norm(r.get("name", "")))
            if co:
                r["co"] = co; hit += 1
            else:
                r.setdefault("co", {})
    json.dump(doc, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"{path}:  {hit}/{n} rytter-rækker fik CO-ratings ({100*hit/n:.0f}%)")

def main(argv):
    by_id, by_name = load_co()
    for p in (argv or ["web/data/giro2026_predictions.json"]):
        enrich(ROOT / p if not Path(p).is_absolute() else Path(p), by_id, by_name)

if __name__ == "__main__":
    main(sys.argv[1:])
