"""Fix tdf2026_predictions.json by applying proper rank-based calibration.

Problem: The file has raw composite*winner_pts values (uncalibrated) for 84/110 riders,
and partially-patched (analytics.json) values for 26 riders. The 4-signal signals
array (no ML, no PCS) means riders like Vingegaard are undervalued.

Fix:
1. For each stage: compute composite from signals, sort, apply exponential decay calibration.
2. For stage 1: override 6 matching riders with correct values from recommendations.json.
3. Fill disc_co for stage 1 from recommendations where possible.
"""

import json, math

WINNER_PTS = {
    "sprint": 680_000, "mountain": 630_000, "tt": 380_000, "ttt": 380_000,
    "hilly": 500_000, "cobbled": 500_000, "gc": 630_000,
}
# Weights: [veloscore, odds, disc, form, ml, pcs_rank]
SIG_WEIGHTS = [0.20, 0.18, 0.20, 0.22, 0.08, 0.12]


def profile_scale(ps, stype):
    if ps is None:
        return 1.0
    if stype in ("sprint", "tt", "ttt", "cobbled"):
        return 1.0
    if stype == "hilly":
        return max(0.80, min(1.20, 0.85 + ps / 300.0 * 0.30))
    if stype == "mountain":
        return max(0.80, min(1.40, 0.82 + (ps - 200) / 330.0 * 0.58))
    return 1.0


def compute_composite(r):
    """Compute composite from signals array (up to 6 signals)."""
    sigs = r.get("signals") or []
    # Pad or truncate to 6
    sigs = (list(sigs) + [None] * 6)[:6]
    total_w = 0.0
    total_ws = 0.0
    for i, s in enumerate(sigs):
        if s is not None:
            total_w += SIG_WEIGHTS[i]
            total_ws += SIG_WEIGHTS[i] * float(s)
    return total_ws / total_w if total_w > 1e-9 else 0.0


def main():
    with open("web/data/tdf2026_predictions.json") as f:
        pred = json.load(f)
    with open("web/data/recommendations.json") as f:
        rec = json.load(f)

    # Build rec lookup: rider_id → record
    rec_lookup = {r["rider_id"]: r for r in rec.get("top_picks", [])}
    # Team → disc_co lookup from rec (for filling stage 1 missing disc_co)
    team_disc_co = {}
    for r in rec.get("top_picks", []):
        team_disc_co[r.get("team", "")] = r.get("disc_co_raw")

    for stage in pred["stages"]:
        stype = stage["type"]
        ps = stage.get("profile_score")
        wp = WINNER_PTS.get(stype, 500_000)
        wp = round(wp * profile_scale(ps, stype))

        riders = stage["riders"]
        N = len(riders)
        if N == 0:
            continue

        # Compute composite for each rider
        for r in riders:
            r["_comp"] = compute_composite(r)

        # Sort descending by composite
        riders.sort(key=lambda r: r["_comp"], reverse=True)

        # Apply rank-based calibration (decay k=2.44 fitted from 63 stages of 2025 Holdet data)
        for i, r in enumerate(riders):
            frac = i / max(1, N - 1)
            r["exp"] = round(0.35 * math.exp(-2.44 * frac) * wp)

        # For stage 1: override with recommendations.json expected_pts and fill disc_co
        if stage["num"] == 1:
            for r in riders:
                if r["id"] in rec_lookup:
                    rec_r = rec_lookup[r["id"]]
                    r["exp"] = rec_r["expected_pts"]
                    # Fill disc_co if missing
                    if r.get("disc_co") is None and rec_r.get("disc_co_raw") is not None:
                        r["disc_co"] = rec_r["disc_co_raw"]
            # Also fill disc_co for remaining stage-1 riders using team lookup
            for r in riders:
                if r.get("disc_co") is None:
                    team = r.get("team", "")
                    # Try rec team lookup by rider team
                    for rec_r in rec.get("top_picks", []):
                        # Match by team abbreviation (pred team vs rec team)
                        if rec_r.get("team", "").startswith(team[:3]) or team.startswith(rec_r.get("team", "")[:3]):
                            if rec_r.get("disc_co_raw") is not None:
                                r["disc_co"] = rec_r["disc_co_raw"]
                                break

        # Final sort by exp
        riders.sort(key=lambda r: r["exp"], reverse=True)

        # Clean up temp field
        for r in riders:
            r.pop("_comp", None)

    with open("web/data/tdf2026_predictions.json", "w", encoding="utf-8") as f:
        json.dump(pred, f, ensure_ascii=False, separators=(",", ":"))

    print("Done. Sample stage 1 top 10:")
    s1 = pred["stages"][0]
    for r in s1["riders"][:10]:
        print(f"  {r['name']} ({r['team']}): exp={r['exp']}, disc={r['disc']}, disc_co={r.get('disc_co')}")

    print("\nSample stage 2 top 5:")
    s2 = pred["stages"][1]
    for r in s2["riders"][:5]:
        print(f"  {r['name']} ({r['team']}): exp={r['exp']}, disc={r['disc']}")


if __name__ == "__main__":
    main()
