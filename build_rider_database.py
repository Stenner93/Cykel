"""
Build a cross-race rider diagnostics database — every rider that has
appeared in any of our races (Giro/Dauphiné/TdF 2026), with the full
backend signal breakdown (CyclingOracle ratings, PCS specialties,
short-term + long-term form). Built for self-validation: lets you spot
data gaps, weird ratings, or stale entries without digging through cache
files manually.

Usage:
    python build_rider_database.py

Output:
    web/data/rider_database.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT    = Path(__file__).parent
DATA    = ROOT / "data"
WEB_DIR = ROOT / "web" / "data"
WEB_DIR.mkdir(parents=True, exist_ok=True)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main() -> None:
    print("=" * 60)
    print("  Byg rytterdatabase (tvaers af alle loeb)")
    print("=" * 60)

    # ── Source rider pools per race ──────────────────────────────────────
    riders_json   = _load(DATA / "riders.json") or []
    dauphine_pool = _load(DATA / "cache" / "dauphine2026_players.json") or []
    tdf_pool      = _load(DATA / "cache" / "tdf2026_players.json") or []

    # rider_id -> {name, team, races: set}
    pool: dict[str, dict] = {}

    def _add(entries: list[dict], race_tag: str):
        for e in entries:
            rid = e.get("id")
            if not rid:
                continue
            if rid not in pool:
                pool[rid] = {
                    "id":    rid,
                    "name":  e.get("full_name", rid),
                    "team":  e.get("team", ""),
                    "races": set(),
                }
            pool[rid]["races"].add(race_tag)
            # Prefer the most complete team/name info available
            if e.get("team") and not pool[rid]["team"]:
                pool[rid]["team"] = e["team"]

    _add(riders_json,   "giro")       # riders.json was built for Giro/Dauphiné base pool
    _add(dauphine_pool, "dauphine")
    _add(tdf_pool,       "tdf")

    print(f"  riders.json:              {len(riders_json)}")
    print(f"  dauphine2026_players.json: {len(dauphine_pool)}")
    print(f"  tdf2026_players.json:      {len(tdf_pool)}")
    print(f"  Unikke ryttere total:      {len(pool)}")

    # ── Join with CyclingOracle + PCS data ───────────────────────────────
    co_raw  = _load(DATA / "cache" / "cyclingoracle.json") or {}
    pcs_raw = _load(DATA / "cache" / "pcs_form.json") or {}

    out_riders = []
    n_with_co = n_with_pcs = n_with_spec = n_with_long = 0

    for rid, info in sorted(pool.items(), key=lambda x: x[1]["name"]):
        co_entry  = co_raw.get(rid, {})
        co_rating = co_entry.get("ratings", {})

        pcs_entry   = pcs_raw.get(rid, {})
        pcs_notfound = pcs_entry.get("not_found", False)
        form_short  = pcs_entry.get("form_by_type", {})
        form_long   = pcs_entry.get("form_long_by_type", {})
        specialties = pcs_entry.get("pcs_specialties", {})

        has_co    = bool(co_rating)
        has_pcs   = bool(form_short) and not pcs_notfound
        has_spec  = bool(specialties)
        has_long  = bool(form_long)

        if has_co:   n_with_co += 1
        if has_pcs:  n_with_pcs += 1
        if has_spec: n_with_spec += 1
        if has_long: n_with_long += 1

        out_riders.append({
            "id":           rid,
            "name":         info["name"],
            "team":         info["team"],
            "races":        sorted(info["races"]),
            "co_ratings":   co_rating,
            "pcs_specialties": specialties,
            "form_short":   form_short,
            "form_long":    form_long,
            "pcs_url":      pcs_entry.get("pcs_url", ""),
            "last_result_date": pcs_entry.get("last_result_date", ""),
            "n_results":    pcs_entry.get("n_results", 0),
            "has_co":       has_co,
            "has_pcs":      has_pcs,
            "has_specialties": has_spec,
            "has_long_form":   has_long,
        })

    print()
    print(f"  Har CyclingOracle:    {n_with_co}/{len(pool)}")
    print(f"  Har PCS form:         {n_with_pcs}/{len(pool)}")
    print(f"  Har PCS specialties:  {n_with_spec}/{len(pool)}")
    print(f"  Har langtids-form:    {n_with_long}/{len(pool)}")

    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "n_riders":  len(out_riders),
        "coverage": {
            "cyclingoracle": n_with_co,
            "pcs_form":      n_with_pcs,
            "pcs_specialties": n_with_spec,
            "pcs_form_long":   n_with_long,
        },
        "riders": out_riders,
    }

    out_path = WEB_DIR / "rider_database.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=None), encoding="utf-8")
    print(f"\n  Gemt: {out_path}  ({len(out_riders)} ryttere)")


if __name__ == "__main__":
    main()
