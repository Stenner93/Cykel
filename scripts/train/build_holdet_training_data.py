"""
build_holdet_training_data.py
Bygger ML-træningsdata med HOLDET-point som target i stedet for PCS-placering.

Kilder:
  data/cache/holdet_historical.json   — faktiske Holdet-point pr. rytter pr. etape (2025)
  data/ml/historical_results.json     — PCS GT-placeringer 2021-2025 (for rolling form)
  data/cache/cyclingoracle.json       — CO disciplin-ratings
  data/cache/pcs_form.json            — PCS specialties + current form

Output:
  data/ml/holdet_training_data.csv        — én række pr. (rytter, etape)
  data/ml/holdet_training_data_meta.json  — kolonne-metadata

Target:
  holdet_pts      — faktiske Holdet-point i K (normaliseret skala inden for etapen)
  holdet_pts_norm — normaliseret 0-100 (100 = max scorer i etapen)
  holdet_rank     — rang inden for etapefelt (1 = højest scorer)

Kørsel:
    python build_holdet_training_data.py
"""
from __future__ import annotations
import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT    = Path(__file__).parent
ML_DIR  = ROOT / "data" / "ml"
CACHE   = ROOT / "data" / "cache"

OUT_CSV  = ML_DIR / "holdet_training_data.csv"
OUT_META = ML_DIR / "holdet_training_data_meta.json"

CO_KEYS   = ["mtn", "spr", "hll", "itt", "cob", "gc"]
SPEC_KEYS = ["climber", "sprint", "tt", "hills"]

# Mapping: holdet_historical key → (pcs_race, year)
HOLDET_RACE_MAP: dict[str, tuple[str, int]] = {
    "giro-d-italia-2025":   ("giro",   2025),
    "tour-de-france-2025":  ("tdf",    2025),
    "vuelta-2025":          ("vuelta", 2025),
}

# Chronological race order for cross-race form (Giro < TdF < Vuelta each year)
RACE_ORDER = {"giro": 1, "tdf": 2, "vuelta": 3}


def _slug_to_id(slug: str) -> str:
    return slug.replace("-", "_")


def _id_to_slug(rid: str) -> str:
    return rid.replace("_", "-")


def load_co() -> dict[str, dict]:
    raw = json.loads((CACHE / "cyclingoracle.json").read_text(encoding="utf-8"))
    return {rid: {k.lower(): v for k, v in d.get("ratings", {}).items()} for rid, d in raw.items()}


def load_specs() -> dict[str, dict]:
    raw = json.loads((CACHE / "pcs_form.json").read_text(encoding="utf-8"))
    out = {}
    for rid, d in raw.items():
        specs = d.get("pcs_specialties") or {}
        if specs:
            out[rid] = {k.lower(): v for k, v in specs.items()}
    return out


def load_pcs_form() -> dict[str, dict]:
    """Current-snapshot PCS form by type — best proxy for 2025 pre-race form."""
    raw = json.loads((CACHE / "pcs_form.json").read_text(encoding="utf-8"))
    out = {}
    for rid, d in raw.items():
        fbt = d.get("form_by_type") or {}
        if fbt:
            out[rid] = fbt
    return out


def rolling_avg(history: list[dict], n: int) -> float:
    valid = [r["position"] for r in history[-n:] if not r.get("dnf")]
    return round(sum(valid) / len(valid), 1) if valid else -1.0


def prev_wins(history: list[dict]) -> int:
    return sum(1 for r in history if r["position"] == 1 and not r.get("dnf"))


def build_pcs_index(pcs_records: list[dict]) -> dict[tuple, list[dict]]:
    """Group PCS records by (race, year, stage) for fast lookup."""
    idx: dict[tuple, list[dict]] = defaultdict(list)
    for r in pcs_records:
        idx[(r["race"], r["year"], r["stage"])].append(r)
    return idx


def main() -> None:
    print("Indlæser data…")
    holdet_hist = json.loads((CACHE / "holdet_historical.json").read_text(encoding="utf-8"))
    pcs_records = json.loads((ML_DIR / "historical_results.json").read_text(encoding="utf-8"))
    co_data     = load_co()
    spec_data   = load_specs()
    pcs_form    = load_pcs_form()
    print(f"  Holdet historisk: {len(holdet_hist)} løb")
    print(f"  PCS historisk:    {len(pcs_records):,} resultater")
    print(f"  CO ratings:       {len(co_data)} ryttere")
    print(f"  PCS specialties:  {len(spec_data)} ryttere")

    # Build pcs_slug → rider_id mapping from pcs_form cache
    slug_to_rid: dict[str, str] = {}
    pcs_raw = json.loads((CACHE / "pcs_form.json").read_text(encoding="utf-8"))
    for rid, d in pcs_raw.items():
        url = d.get("pcs_url", "")
        if "/rider/" in url:
            slug = url.split("/rider/")[-1].strip("/")
            slug_to_rid[slug] = rid

    # Build per-rider PCS history sorted chronologically
    # Sort by (year, race_order, stage) so cross-race form is correctly ordered
    pcs_records_sorted = sorted(
        pcs_records,
        key=lambda r: (r["year"], RACE_ORDER.get(r["race"], 9), r["stage"])
    )
    # Index: slug → chronological list of results (for rolling form)
    rider_pcs_history: dict[str, list[dict]] = defaultdict(list)

    # Group PCS results by (race, year, stage)
    pcs_stage_idx = build_pcs_index(pcs_records)

    # We process Holdet historical stages in chronological order too
    holdet_rows_sorted = []
    for holdet_key, race_data in holdet_hist.items():
        if holdet_key not in HOLDET_RACE_MAP:
            print(f"  Springer over ukendt løb: {holdet_key}")
            continue
        pcs_race, year = HOLDET_RACE_MAP[holdet_key]
        meta = race_data.get("meta", {})
        stages_data = race_data.get("stages", {})
        for snum_str, riders in stages_data.items():
            snum = int(snum_str)
            stage_meta = meta.get(snum_str, {})
            stype = stage_meta.get("stage_type", "hilly")
            holdet_rows_sorted.append({
                "pcs_race": pcs_race,
                "year":     year,
                "stage":    snum,
                "stype":    stype,
                "riders":   riders,   # {rider_id: pts}
            })

    # Sort holdet stages chronologically
    holdet_rows_sorted.sort(key=lambda x: (x["year"], RACE_ORDER.get(x["pcs_race"], 9), x["stage"]))

    # Build output rows
    rows: list[dict] = []
    total_stages = 0
    total_riders_matched = 0
    total_co_hits = 0
    total_spec_hits = 0

    # For rolling PCS form, we maintain a per-slug running history
    # We also need to track which PCS records we've "seen" up to the current stage
    # Process PCS records chronologically too, updating rider_pcs_history as we go
    pcs_ptr = 0  # pointer into pcs_records_sorted

    for stage_info in holdet_rows_sorted:
        race  = stage_info["pcs_race"]
        year  = stage_info["year"]
        snum  = stage_info["stage"]
        stype = stage_info["stype"]
        riders_pts: dict[str, int | None] = stage_info["riders"]

        # Advance PCS history pointer up to (but not including) this stage
        while pcs_ptr < len(pcs_records_sorted):
            r = pcs_records_sorted[pcs_ptr]
            r_ord = (r["year"], RACE_ORDER.get(r["race"], 9), r["stage"])
            this_ord = (year, RACE_ORDER.get(race, 9), snum)
            if r_ord >= this_ord:
                break
            rider_pcs_history[r["rider_slug"]].append({
                "race": r["race"], "year": r["year"], "stage": r["stage"],
                "position": r["position"], "dnf": r["dnf"],
            })
            pcs_ptr += 1

        # Get PCS stage results for rolling in-race form
        pcs_stage = pcs_stage_idx.get((race, year, snum), [])
        pcs_stage_map: dict[str, dict] = {r["rider_slug"]: r for r in pcs_stage}

        # Also collect in-race history UP TO this stage (stages 1..snum-1 same race)
        in_race_hist: dict[str, list[dict]] = defaultdict(list)
        for prev_s in range(1, snum):
            for r in pcs_stage_idx.get((race, year, prev_s), []):
                in_race_hist[r["rider_slug"]].append({
                    "stage": prev_s, "position": r["position"], "dnf": r["dnf"]
                })

        # Normalize holdet pts within stage
        valid_pts = [v for v in riders_pts.values() if v is not None and v > 0]
        if not valid_pts:
            continue
        max_pts = max(valid_pts)

        total_stages += 1
        for rid, raw_pts in riders_pts.items():
            if raw_pts is None:
                raw_pts = 0
            pts_norm = round(raw_pts / max_pts * 100, 1)
            # Rank: how many riders have strictly more pts
            pts_rank = sum(1 for v in valid_pts if v > raw_pts) + 1

            # PCS slug for this rider
            pcs_slug = _id_to_slug(rid)

            # CO ratings (all 6)
            co = co_data.get(rid, {})
            co_mtn = co.get("mtn", -1)
            co_spr = co.get("spr", -1)
            co_hll = co.get("hll", -1)
            co_itt = co.get("itt", -1)
            co_cob = co.get("cob", -1)
            co_gc  = co.get("gc",  -1)
            if co:
                total_co_hits += 1

            # PCS specialties
            spec = spec_data.get(rid, {})
            spec_climber = spec.get("climber", -1)
            spec_sprint  = spec.get("sprint",  -1)
            spec_tt      = spec.get("tt",      -1)
            spec_hills   = spec.get("hills",   -1)
            if spec:
                total_spec_hits += 1

            # Pre-race PCS form by type (current snapshot, best proxy)
            form = pcs_form.get(rid, {})
            form_overall  = form.get("overall",  -1)
            form_sprint   = form.get("sprint",   -1)
            form_mountain = form.get("mountain", -1)
            form_hilly    = form.get("hilly",    -1)
            form_tt       = form.get("tt",       -1)

            # In-race rolling form
            in_race = in_race_hist.get(pcs_slug, [])
            gt_form_5  = rolling_avg(in_race, 5)
            gt_form_10 = rolling_avg(in_race, 10)
            gt_wins    = prev_wins(in_race)

            # Cross-race rolling form (last 10 GT stages before this race)
            cross_race = rider_pcs_history.get(pcs_slug, [])
            xrace_form_10 = rolling_avg(cross_race, 10)

            # PCS position in THIS stage (actual result — only for self-check; not used as feature)
            pcs_pos = pcs_stage_map.get(pcs_slug, {}).get("position", -1)

            total_riders_matched += 1

            rows.append({
                # Context
                "race":       race,
                "year":       year,
                "stage":      snum,
                "stage_type": stype,
                # Stage-type flags
                "is_sprint":   int(stype == "sprint"),
                "is_mountain": int(stype == "mountain"),
                "is_hilly":    int(stype == "hilly"),
                "is_tt":       int(stype in ("tt", "ttt")),
                # CO ratings
                "co_mtn": co_mtn, "co_spr": co_spr, "co_hll": co_hll,
                "co_itt": co_itt, "co_cob": co_cob, "co_gc":  co_gc,
                # PCS specialties
                "spec_climber": spec_climber, "spec_sprint": spec_sprint,
                "spec_tt": spec_tt, "spec_hills": spec_hills,
                # Pre-race form (current PCS snapshot)
                "form_overall": form_overall, "form_sprint": form_sprint,
                "form_mountain": form_mountain, "form_hilly": form_hilly,
                "form_tt": form_tt,
                # In-race rolling form
                "gt_form_5":   gt_form_5,
                "gt_form_10":  gt_form_10,
                "gt_wins_so_far": gt_wins,
                # Cross-race form (last 10 GT stages before this race)
                "xrace_form_10": xrace_form_10,
                # Targets
                "holdet_pts":      raw_pts,
                "holdet_pts_norm": pts_norm,
                "holdet_rank":     pts_rank,
                # Metadata
                "rider_id":  rid,
                "pcs_pos":   pcs_pos,   # for inspection; not a feature
            })

    print(f"\nBygget {len(rows):,} trænings-rækker")
    print(f"  Etaper: {total_stages}  |  Rytter-etape rækker: {total_riders_matched}")
    print(f"  CO hits: {total_co_hits}/{total_riders_matched} "
          f"({100*total_co_hits//max(1,total_riders_matched)}%)")
    print(f"  Spec hits: {total_spec_hits}/{total_riders_matched} "
          f"({100*total_spec_hits//max(1,total_riders_matched)}%)")

    if not rows:
        print("Ingen rækker — afbryder")
        return

    ML_DIR.mkdir(parents=True, exist_ok=True)

    # Write CSV
    fieldnames = list(rows[0].keys())
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"CSV gemt: {OUT_CSV}  ({OUT_CSV.stat().st_size // 1024} KB)")

    # Meta
    feature_cols = [c for c in fieldnames if c not in (
        "race", "year", "stage", "stage_type", "rider_id", "pcs_pos",
        "holdet_pts", "holdet_pts_norm", "holdet_rank",
    )]
    meta = {
        "n_rows":       len(rows),
        "n_stages":     total_stages,
        "feature_cols": feature_cols,
        "target_cols":  ["holdet_pts", "holdet_pts_norm", "holdet_rank"],
        "years":        sorted({r["year"] for r in rows}),
        "races":        sorted({r["race"] for r in rows}),
        "notes": (
            "holdet_pts: råværdi fra holdet_historical.json (K-format). "
            "holdet_pts_norm: 0-100 normaliseret inden for etapefeltet. "
            "holdet_rank: rang inden for feltet (1=bedst). "
            "form_*: nuværende PCS-form-snapshot — bedste approks. for 2025. "
            "pcs_pos: faktisk PCS-placering (ikke feature, kun til inspektion)."
        ),
    }
    OUT_META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Meta gemt: {OUT_META}")


if __name__ == "__main__":
    main()
