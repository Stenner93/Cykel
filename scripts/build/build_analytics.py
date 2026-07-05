#!/usr/bin/env python3
"""
build_analytics.py
Generates web/data/tdf2026_analytics.json for the Analytik dashboard.

Sources:
  web/data/tdf2026_predictions.json  — per-stage predictions (holdet_est, signals)
  data/cache/tdf2026_players.json    — Holdet.dk riders (authoritative list + prices)
  data/cache/cyclingoracle.json      — CO discipline ratings
  data/cache/pcs_form.json           — PCS form + specialties
  data/cache/pcs_rankings.json       — PCS 12-month ranking points

Output: web/data/tdf2026_analytics.json  (multi-stage, Holdet-filtered)
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
WEB_DATA = ROOT / "web" / "data"
DATA     = ROOT / "data"
CACHE    = DATA / "cache"

SIGNAL_KEYS = ["veloscore", "odds", "disc", "form", "ml", "pcs_rank"]

# Stage type → primary CO key for discipline column
STAGE_CO_KEY = {
    "sprint":   "SPR",
    "mountain": "MTN",
    "tt":       "ITT",
    "ttt":      "ITT",
    "hilly":    "HLL",
    "cobbled":  "COB",
}
# Stage type → primary PCS specialty key
STAGE_PCS_KEY = {
    "sprint":   "sprint",
    "mountain": "climber",
    "tt":       "tt",
    "ttt":      "tt",
    "hilly":    "hills",
    "cobbled":  "onedayraces",
}

def load(path: Path):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def build():
    preds   = load(WEB_DATA / "tdf2026_predictions.json")
    players = load(CACHE / "tdf2026_players.json")
    co_raw  = load(CACHE / "cyclingoracle.json")
    pcs_raw = load(CACHE / "pcs_form.json")
    rank_raw = load(CACHE / "pcs_rankings.json")
    holdet_raw = load(CACHE / "holdet_players.json")

    if not preds or not players:
        print("ERROR: tdf2026_predictions.json or tdf2026_players.json missing")
        return

    # Holdet.dk rider set + price lookup (authoritative)
    holdet_ids  = {p["id"] for p in players}
    holdet_price = {p["id"]: p["price"] for p in players}
    holdet_team  = {p["id"]: p["team"] for p in players}
    holdet_name  = {p["id"]: p["full_name"] for p in players}

    # Ownership % (popularitet) + Holdet's own recent change, keyed by rider_id
    own_pct:        dict[str, float] = {}
    own_pct_change: dict[str, float] = {}
    if holdet_raw:
        for rid, h in holdet_raw.items():
            own_pct[rid]        = h.get("own_pct", 0)
            own_pct_change[rid] = h.get("own_pct_change", 0)

    # CO ratings: {rider_id: {ITT, SPR, MTN, HLL, COB, GC}}
    co_ratings: dict[str, dict] = {}
    if co_raw:
        for rid, entry in co_raw.items():
            co_ratings[rid] = entry.get("ratings", {})

    # PCS: form by type + specialties + pcs_url (for ranking lookup)
    pcs_form: dict[str, dict]  = {}
    pcs_spec: dict[str, dict]  = {}
    pcs_slug: dict[str, str]   = {}    # rider_id → PCS slug
    if pcs_raw:
        for rid, entry in pcs_raw.items():
            pcs_form[rid] = entry.get("form_by_type", {})
            spec = entry.get("pcs_specialties")
            if spec:
                pcs_spec[rid] = spec
            url = entry.get("pcs_url", "")
            if "/rider/" in url:
                pcs_slug[rid] = url.split("/rider/")[-1].strip("/")

    # PCS ranking: slug → pts
    rank_pts: dict[str, float] = {}
    if rank_raw:
        rank_pts = {slug: v.get("pts", 0) for slug, v in rank_raw.items()}

    # Normalize PCS specialties and ranking within field for display (0-100)
    # We normalize at the end when we know the full field.

    stages_out = []
    for stage in preds["stages"]:
        snum  = stage["num"]
        stype = stage.get("type", "")
        co_key  = STAGE_CO_KEY.get(stype, "HLL")
        pcs_key = STAGE_PCS_KEY.get(stype, "hills")

        # Filter to Holdet riders and augment
        riders_out = []
        for r in stage["riders"]:
            rid = r["id"]
            if rid not in holdet_ids:
                continue

            # CO ratings for this rider (all keys, raw 0-100)
            co = co_ratings.get(rid, {})

            # PCS specialty raw points
            spec = pcs_spec.get(rid, {})

            # PCS form by type (0-100 score)
            form = pcs_form.get(rid, {})

            # PCS ranking points (raw UCI points)
            slug = pcs_slug.get(rid, "")
            rank_p = rank_pts.get(slug, 0.0)

            # Signals array → dict
            sig_arr = r.get("signals", [0, 0, 0, 0, 0, 0])
            if len(sig_arr) < 6:
                sig_arr = list(sig_arr) + [0.0] * (6 - len(sig_arr))
            signals = {k: sig_arr[i] for i, k in enumerate(SIGNAL_KEYS)}

            riders_out.append({
                "id":            rid,
                "name":          holdet_name.get(rid, r.get("name", rid)),
                "team":          holdet_team.get(rid, r.get("team", "")),
                "price":         holdet_price.get(rid, r.get("price", 0)),
                "holdet_est":    r.get("holdet_est"),
                "disc":          r.get("disc"),
                "disc_key":      r.get("disc_key", co_key),
                "disc_co":       r.get("disc_co"),
                "signals":       signals,
                "form_score":    r.get("form", 0),
                "breakaway":     r.get("breakaway_specialist", False),
                "co":            co,           # {ITT, SPR, MTN, HLL, COB, GC}
                "pcs_spec":      spec,         # {tt, sprint, climber, hills, onedayraces, gc}
                "pcs_form":      form,         # {overall, sprint, mountain, hilly, tt}
                "pcs_rank_pts":  rank_p,
                "own_pct":       own_pct.get(rid, 0),          # ejerskabs-% på Holdet
                "own_pct_change": own_pct_change.get(rid, 0),  # Holdets egen ændring
            })

        # Sort by holdet_est desc, then price desc
        riders_out.sort(key=lambda r: (-(r.get("holdet_est") or 0), -r["price"]))

        # Normalize pcs_spec and pcs_rank_pts within stage field (0-100)
        # so columns are comparable regardless of absolute scale
        for key in ["pcs_rank_pts"] + list(STAGE_PCS_KEY.values()):
            if key == "pcs_rank_pts":
                vals = [r["pcs_rank_pts"] for r in riders_out if r["pcs_rank_pts"] > 0]
            else:
                vals = [r["pcs_spec"].get(key, 0) for r in riders_out if r["pcs_spec"].get(key, 0) > 0]
            if not vals:
                continue
            max_v = max(vals)
            if max_v <= 0:
                continue
            for r in riders_out:
                if key == "pcs_rank_pts":
                    r["pcs_rank_norm"] = round(r["pcs_rank_pts"] / max_v * 100, 1) if r["pcs_rank_pts"] else 0
                else:
                    raw = r["pcs_spec"].get(key, 0)
                    r.setdefault("pcs_spec_norm", {})[key] = round(raw / max_v * 100, 1) if raw else 0

        stages_out.append({
            "num":     snum,
            "type":    stype,
            "name":    stage.get("name", f"Etape {snum}"),
            "co_key":  co_key,
            "pcs_key": pcs_key,
            "riders":  riders_out,
        })

    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "stages":    stages_out,
    }
    out_path = WEB_DATA / "tdf2026_analytics.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(s["riders"]) for s in stages_out)
    print(f"tdf2026_analytics.json skrevet: {len(stages_out)} etaper, {total} rytter-poster")
    print(f"  Holdet-ryttere pr etape: {total // len(stages_out) if stages_out else 0}")


if __name__ == "__main__":
    build()
