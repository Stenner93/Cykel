#!/usr/bin/env python3
"""
build_holdet_calibration.py
Calibrates TdF 2026 predictions with estimated Holdet points.

Uses Giro 2026 actual scores vs predictions to derive linear calibrations
per stage type. Updates tdf2026_predictions.json with:
  - holdet_est: estimated Holdet points (100-450 range)
  - breakaway_specialist: bool flag for riders who appear often in stage
    top-10 results in pcs_race_results but are not GC contenders
"""

import json
import math
from collections import defaultdict

# --------------------------------------------------------------------------
# Hardcoded GC contenders for TdF 2026
# --------------------------------------------------------------------------
GC_CONTENDERS = {
    'jonas_vingegaard', 'tadej_pogacar', 'remco_evenepoel', 'primoz_roglic',
    'juan_ayuso', 'egan_bernal', 'carlos_rodriguez', 'adam_yates',
    'felix_gall', 'michael_storer', 'joao_almeida', 'david_gaudu',
}

# --------------------------------------------------------------------------
# Load data
# --------------------------------------------------------------------------
with open('web/data/giro2026_scores.json') as f:
    giro_scores = json.load(f)

with open('web/data/giro2026_predictions.json') as f:
    giro_preds = json.load(f)

with open('web/data/pcs_race_results.json') as f:
    pcs_results = json.load(f)

with open('web/data/tdf2026_predictions.json') as f:
    tdf_preds = json.load(f)

# --------------------------------------------------------------------------
# Step 1 — Linear regression helpers
# --------------------------------------------------------------------------

def linear_regression(pairs):
    """Return (slope, intercept, r) for list of (x, y) pairs."""
    n = len(pairs)
    if n < 2:
        return 0.0, 150.0, 0.0
    x = [p[0] for p in pairs]
    y = [p[1] for p in pairs]
    mx = sum(x) / n
    my = sum(y) / n
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    if sxx == 0:
        return 0.0, my, 0.0
    slope = sxy / sxx
    intercept = my - slope * mx
    r = sxy / math.sqrt(sxx * syy) if sxx * syy > 0 else 0.0
    return slope, intercept, r


# --------------------------------------------------------------------------
# Step 2 — Build calibration from Giro 2026 data
# --------------------------------------------------------------------------

# Map rider id -> disc per stage num (string)
giro_pred_by_stage: dict[str, dict] = {}
for stage in giro_preds['stages']:
    snum = str(stage['num'])
    giro_pred_by_stage[snum] = {r['id']: r for r in stage['riders']}

# Map stage num -> type for Giro
giro_stage_types = {str(s['num']): s['type'] for s in giro_scores['stages']}

# Collect (disc, actual_pts) pairs per stage type
sprint_pairs: list[tuple[float, float]] = []
mountain_pairs: list[tuple[float, float]] = []
tt_pairs: list[tuple[float, float]] = []
hilly_pairs: list[tuple[float, float]] = []

for rider in giro_scores['riders']:
    rid = rider['id']
    for snum, pts_raw in rider['pts'].items():
        if pts_raw is None or pts_raw <= 0:
            continue
        pts = pts_raw / 1000.0          # convert to actual Holdet pts
        stype = giro_stage_types.get(snum)
        if snum not in giro_pred_by_stage or rid not in giro_pred_by_stage[snum]:
            continue
        pred = giro_pred_by_stage[snum][rid]
        disc = pred.get('disc', 0)
        if disc == 0:
            continue
        if stype == 'sprint':
            sprint_pairs.append((disc, pts))
        elif stype == 'mountain':
            mountain_pairs.append((disc, pts))
        elif stype == 'tt':
            tt_pairs.append((disc, pts))
        elif stype == 'hilly':
            hilly_pairs.append((disc, pts))

# Fit regressions
spr_slope, spr_intercept, spr_r = linear_regression(sprint_pairs)
mtn_slope, mtn_intercept, mtn_r = linear_regression(mountain_pairs)
tt_slope,  tt_intercept,  tt_r  = linear_regression(tt_pairs)
hll_slope, hll_intercept, hll_r = linear_regression(hilly_pairs)

print("=== Regression results ===")
print(f"Sprint:   slope={spr_slope:.4f}, intercept={spr_intercept:.2f}, r={spr_r:.4f}  (n={len(sprint_pairs)})")
print(f"Mountain: slope={mtn_slope:.4f}, intercept={mtn_intercept:.2f}, r={mtn_r:.4f}  (n={len(mountain_pairs)})")
print(f"TT:       slope={tt_slope:.4f}, intercept={tt_intercept:.2f},  r={tt_r:.4f}   (n={len(tt_pairs)})")
print(f"Hilly:    slope={hll_slope:.4f}, intercept={hll_intercept:.2f}, r={hll_r:.4f}  (n={len(hilly_pairs)})")


def calibrate(disc, slope, intercept, lo=80, hi=500):
    """Apply regression and clamp to [lo, hi]."""
    val = intercept + slope * disc
    return max(lo, min(hi, val))


# --------------------------------------------------------------------------
# Step 3 — Mountain stage: GC contender vs. breakaway estimate
# --------------------------------------------------------------------------
# For GC contenders on mountain stages, disc is a good predictor.
# For non-GC riders we use a lower floor estimate (they get fewer GC pts
# and Etapeplacering is winner-takes-most, so expected is lower).

# Derive GC contender mean on mountain stages from Giro data
gc_mtn_pairs = []
non_gc_mtn_pairs = []
for rider in giro_scores['riders']:
    rid = rider['id']
    # Use Giro GC contender-like check: disc >= 85 on MTN stages
    for snum, pts_raw in rider['pts'].items():
        if giro_stage_types.get(snum) != 'mountain':
            continue
        if pts_raw is None or pts_raw <= 0:
            continue
        if snum not in giro_pred_by_stage or rid not in giro_pred_by_stage[snum]:
            continue
        pred = giro_pred_by_stage[snum][rid]
        disc = pred.get('disc', 0)
        if disc == 0:
            continue
        pts = pts_raw / 1000.0
        if disc >= 88:
            gc_mtn_pairs.append((disc, pts))
        else:
            non_gc_mtn_pairs.append((disc, pts))

gc_mtn_slope, gc_mtn_int, _ = linear_regression(gc_mtn_pairs)
non_gc_mtn_slope, non_gc_mtn_int, _ = linear_regression(non_gc_mtn_pairs)

# GC contenders score ~60 pts above the overall mountain regression.
# Rather than using the overly steep separate GC regression (disc range too narrow),
# we apply the overall mountain regression + GC_BOOST for GC riders.
GC_MTN_BOOST = 60.0

print(f"\nMountain GC (disc>=88):    slope={gc_mtn_slope:.4f}, intercept={gc_mtn_int:.2f}  (n={len(gc_mtn_pairs)})")
print(f"Mountain non-GC (disc<88): slope={non_gc_mtn_slope:.4f}, intercept={non_gc_mtn_int:.2f}  (n={len(non_gc_mtn_pairs)})")
print(f"GC mountain boost: +{GC_MTN_BOOST:.0f} pts over overall mtn regression")


# --------------------------------------------------------------------------
# Step 4 — Identify breakaway specialists from pcs_race_results
# --------------------------------------------------------------------------
# Count top-10 stage finishes per rider across all races
rider_top10_count: dict[str, int] = defaultdict(int)

for race, stages in pcs_results.items():
    for stage_num, results in stages.items():
        for result in results:
            if result.get('pos', 999) <= 10:
                rider_top10_count[result['rider_id']] += 1

# Breakaway specialist = appears > 3 times in top-10 AND is NOT a GC contender
BREAKAWAY_THRESHOLD = 3
breakaway_specialists = {
    rid for rid, cnt in rider_top10_count.items()
    if cnt > BREAKAWAY_THRESHOLD and rid not in GC_CONTENDERS
}

print(f"\nBreakaway specialists found: {len(breakaway_specialists)}")
# Show top 20
top_bwa = sorted(
    [(cnt, rid) for rid, cnt in rider_top10_count.items() if rid in breakaway_specialists],
    reverse=True
)[:20]
for cnt, rid in top_bwa:
    print(f"  {cnt} top-10s: {rid}")


# --------------------------------------------------------------------------
# Step 5 — Holdet estimate function
# --------------------------------------------------------------------------

def estimate_holdet(rider_id: str, disc: float, stage_type: str) -> float:
    """
    Returns estimated Holdet points for a rider/stage combination.
    Values are clamped to [100, 450].
    """
    lo, hi = 100, 450

    if stage_type == 'sprint':
        return calibrate(disc, spr_slope, spr_intercept, lo, hi)

    elif stage_type == 'mountain':
        # Use overall mountain regression for all riders; GC contenders
        # get an extra boost because they also collect GC standing points.
        base = calibrate(disc, mtn_slope, mtn_intercept, lo, hi)
        if rider_id in GC_CONTENDERS:
            base = min(hi, base + GC_MTN_BOOST)
        return base

    elif stage_type == 'tt':
        return calibrate(disc, tt_slope, tt_intercept, lo, hi)

    elif stage_type in ('ttt',):
        # Team time trial — disc = team ability (all teammates share same disc).
        # GC contenders also score GC standing points; domestiques don't.
        # Apply same GC_MTN_BOOST as mountain stages to account for this.
        base = calibrate(disc, tt_slope, tt_intercept, lo, hi)
        if rider_id in GC_CONTENDERS:
            base = min(hi, base + GC_MTN_BOOST)
        else:
            # Domestiques: reduce by ~40 (no GC pts, lower expected stage placement pts)
            base = max(lo, base - 40)
        return base
        return calibrate(disc, tt_slope, tt_intercept, lo, hi)

    elif stage_type in ('hilly', 'cobbled'):
        return calibrate(disc, hll_slope, hll_intercept, lo, hi)

    else:
        # Fallback: simple average
        return 150.0


# --------------------------------------------------------------------------
# Step 6 — Update tdf2026_predictions.json
# --------------------------------------------------------------------------

for stage in tdf_preds['stages']:
    stype = stage.get('type', '')
    for rider in stage['riders']:
        rid = rider['id']
        disc = rider.get('disc', 0.0)
        est = estimate_holdet(rid, disc, stype)
        rider['holdet_est'] = round(est, 1)
        rider['breakaway_specialist'] = rid in breakaway_specialists

# Save back
with open('web/data/tdf2026_predictions.json', 'w') as f:
    json.dump(tdf_preds, f, indent=2, ensure_ascii=False)

print("\ntdf2026_predictions.json updated successfully.")

# --------------------------------------------------------------------------
# Step 7 — Report top riders for Stage 1 (TTT) and Stage 5 (Sprint)
# --------------------------------------------------------------------------

def top_riders(stage_num: int, n: int = 10):
    stage = next(s for s in tdf_preds['stages'] if s['num'] == stage_num)
    riders = sorted(stage['riders'], key=lambda r: -r.get('holdet_est', 0))
    print(f"\n=== Stage {stage_num} ({stage.get('type')}: {stage.get('name')}) — Top {n} by holdet_est ===")
    for r in riders[:n]:
        bwa = ' [BWA]' if r.get('breakaway_specialist') else ''
        gc  = ' [GC]'  if r['id'] in GC_CONTENDERS else ''
        print(f"  {r.get('holdet_est', 0):5.0f} pts | disc={r.get('disc', 0):5.1f} | {r['name']}{gc}{bwa}")

top_riders(1)
top_riders(5)
