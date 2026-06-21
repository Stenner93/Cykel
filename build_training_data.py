"""
Build ML training data from historical PCS stage results.

Input:
  data/ml/historical_results.json  — scraped by scrape_pcs_history.py
  data/cache/cyclingoracle.json    — CO ratings per rider
  data/cache/pcs_form.json         — PCS specialties per rider

Output:
  data/ml/training_data.csv        — one row per (rider, stage)
  data/ml/training_data_meta.json  — column metadata

Features per row:
  race, year, stage, stage_type, profile_score  — stage context
  co_mtn, co_spr, co_hll, co_itt, co_cob, co_gc — CyclingOracle ratings
  spec_climber, spec_sprint, spec_tt, spec_hills  — PCS specialties
  gt_form_5, gt_form_10                          — avg position in last N GT stages
  prev_wins_year                                  — GT stage wins so far this year
  position                                        — TARGET (or dnf flag)
  top5, top10                                    — derived targets

Usage:
    python build_training_data.py
    python build_training_data.py --min-year 2022  # skip 2021
"""
from __future__ import annotations
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT     = Path(__file__).parent
ML_DIR   = ROOT / "data" / "ml"
CACHE    = ROOT / "data" / "cache"
HIST     = ML_DIR / "historical_results.json"
OUT_CSV  = ML_DIR / "training_data.csv"
OUT_META = ML_DIR / "training_data_meta.json"

CO_KEYS   = ["MTN", "SPR", "HLL", "ITT", "COB", "GC"]
SPEC_KEYS = ["climber", "sprint", "tt", "hills"]
STAGE_TYPES = ["sprint", "hilly", "mountain", "tt", "ttt", "cobbled"]


def load_co() -> dict[str, dict]:
    path = CACHE / "cyclingoracle.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for rider_id, data in raw.items():
        ratings = data.get("ratings", {})
        out[rider_id] = {k.lower(): v for k, v in ratings.items()}
    return out


def load_specialties() -> dict[str, dict]:
    path = CACHE / "pcs_form.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for rider_id, data in raw.items():
        specs = data.get("specialties", {})
        if specs:
            out[rider_id] = {k.lower(): v for k, v in specs.items()}
    return out


def build_co_lookup(co_raw: dict) -> dict[str, dict]:
    """Build slug → CO ratings lookup, matching on last-name prefix."""
    return co_raw


def gt_rolling_form(records_before: list[dict], n: int) -> float | None:
    """Average position over last n GT stages (excluding DNF)."""
    valid = [r["position"] for r in records_before[-n:] if not r.get("dnf")]
    if not valid:
        return None
    return round(sum(valid) / len(valid), 1)


def prev_wins(records_before: list[dict]) -> int:
    return sum(1 for r in records_before if r["position"] == 1 and not r.get("dnf"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-year", type=int, default=2021)
    args = parser.parse_args()

    if not HIST.exists():
        print(f"Fejl: {HIST} ikke fundet.")
        print("Kør først: python scrape_pcs_history.py")
        return

    records = json.loads(HIST.read_text(encoding="utf-8"))
    print(f"Indlæst {len(records):,} historiske resultater")

    co_data   = load_co()
    spec_data = load_specialties()
    print(f"CO ratings: {len(co_data)} ryttere  Specialties: {len(spec_data)} ryttere")

    # Filter by year
    records = [r for r in records if r["year"] >= args.min_year]

    # Sort by (race, year, stage) for chronological rolling-form computation
    records.sort(key=lambda r: (r["year"], r["race"], r["stage"]))

    # Group per rider for rolling-form lookups
    # key: rider_slug, value: chronological list of {race,year,stage,position,dnf}
    rider_history: dict[str, list[dict]] = defaultdict(list)

    # Build stage-order index: (race, year, stage) → list of riders
    stage_groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        stage_groups[(r["race"], r["year"], r["stage"])].append(r)

    # Unique ordered stages
    stage_keys = sorted(stage_groups.keys(), key=lambda k: (k[1], k[0], k[2]))

    rows = []
    for (race, year, stage) in stage_keys:
        stage_records = stage_groups[(race, year, stage)]
        # Pick stage metadata from first record
        meta = stage_records[0]
        stype = meta.get("stage_type") or "hilly"
        pscore = meta.get("profile_score") or 0

        for r in stage_records:
            slug = r["rider_slug"]
            pos  = r["position"]
            dnf  = r["dnf"]

            # Rolling form from PREVIOUS stages (not including current)
            hist_before = rider_history[slug]

            row = {
                "race":          race,
                "year":          year,
                "stage":         stage,
                "stage_type":    stype,
                "profile_score": pscore,
                # Stage-type flags
                "is_sprint":   int(stype == "sprint"),
                "is_mountain": int(stype == "mountain"),
                "is_hilly":    int(stype == "hilly"),
                "is_tt":       int(stype in ("tt", "ttt")),
                # CO ratings (None → -1 for tree model)
                **{f"co_{k}": co_data.get(slug, {}).get(k, -1) for k in CO_KEYS},
                # PCS specialties (None → -1)
                **{f"spec_{k}": spec_data.get(slug, {}).get(k, -1) for k in SPEC_KEYS},
                # Rolling form within this GT
                "gt_form_5":   gt_rolling_form(hist_before, 5) or -1,
                "gt_form_10":  gt_rolling_form(hist_before, 10) or -1,
                "gt_wins_so_far": prev_wins(hist_before),
                # Target
                "position":    pos,
                "dnf":         int(dnf),
                "top5":        int(pos <= 5 and not dnf),
                "top10":       int(pos <= 10 and not dnf),
                "top20":       int(pos <= 20 and not dnf),
                "rider_slug":  slug,
            }
            rows.append(row)

            # Update history AFTER processing current stage
            rider_history[slug].append({
                "race": race, "year": year, "stage": stage,
                "position": pos, "dnf": dnf,
            })

    print(f"Bygget {len(rows):,} trænings-rækker  "
          f"({len(stage_keys)} etaper, {len(rider_history)} ryttere)")

    ML_DIR.mkdir(parents=True, exist_ok=True)

    # Write CSV
    if not rows:
        print("Ingen rækker — afbryder")
        return

    fieldnames = list(rows[0].keys())
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"CSV gemt: {OUT_CSV}  ({OUT_CSV.stat().st_size // 1024} KB)")

    # Write meta
    feature_cols = [c for c in fieldnames if c not in (
        "race", "year", "stage", "stage_type", "rider_slug",
        "position", "dnf", "top5", "top10", "top20",
    )]
    meta = {
        "n_rows":        len(rows),
        "n_stages":      len(stage_keys),
        "n_riders":      len(rider_history),
        "feature_cols":  feature_cols,
        "target_cols":   ["position", "dnf", "top5", "top10", "top20"],
        "years":         sorted({r["year"] for r in rows}),
        "races":         sorted({r["race"] for r in rows}),
    }
    OUT_META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Meta gemt: {OUT_META}")


if __name__ == "__main__":
    main()
