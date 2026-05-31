"""
Expected-points prediction model.

Estimates each rider's expected fantasy score on the coming stage by combining:
  1. VeloScore consensus rank / normalised score
  2. Bookmaker implied win probability (from odds)
  3. CyclingOracle discipline rating vs. stage type
  4. Current GC / jersey position
  5. PCS recent form (optional)

Weights are calibrated on Giro 2026 data via calibrate.py.
Default weights are sensible priors based on manual analysis.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Default calibrated weights (updated by calibrate.py)
# ---------------------------------------------------------------------------
DEFAULT_WEIGHTS = {
    "veloscore":     0.45,   # VeloScore normalised 0-10
    "odds_prob":     0.25,   # Bookmaker implied win probability
    "discipline":    0.20,   # CyclingOracle discipline match 0-100
    "form":          0.10,   # PCS recent form 0-100
}

# Mapping stage_type → CyclingOracle discipline key
STAGE_DISCIPLINE = {
    "sprint":    "SPR",
    "mountain":  "MTN",
    "tt":        "ITT",
    "hilly":     "HLL",
    "cobbled":   "COB",
    "gc":        "GC",
}

# Expected fantasy points IF a rider wins (by stage type)
# Derived from Giro 2026 FantasyTool data
WINNER_POINTS = {
    "sprint":   560_000,
    "mountain": 630_000,
    "tt":       380_000,
    "hilly":    500_000,
    "cobbled":  500_000,
    "gc":       630_000,
}

# Scaling curve: expected points as fraction of winner points, by VeloScore rank
# rank 1 → ~100%, rank 5 → ~30%, rank 10 → ~12%, rank 20 → ~4%
def _rank_scale(rank: int) -> float:
    if rank <= 0:
        return 0.0
    return max(0.0, 1.0 / (1.0 + 0.5 * (rank - 1) ** 1.1))


# GC bonus model: expected GC points based on current standing + stage type
def _expected_gc_bonus(gc_rank: int | None, stage_type: str) -> float:
    from .scoring import GC_PTS
    if gc_rank is None or gc_rank > 20:
        return 0.0
    # On mountain stages, GC riders shift up; on sprints, they stay stable
    shift = {"mountain": -1, "gc": -2, "sprint": 1, "tt": 0, "hilly": 0, "cobbled": 1}
    effective_rank = max(1, gc_rank + shift.get(stage_type, 0))
    return GC_PTS.get(effective_rank, 0)


def predict_rider(
    rider: dict[str, Any],
    stage_type: str,
    veloscore_rank: int | None = None,
    veloscore_score: float | None = None,
    odds_prob: float | None = None,
    cyclingoracle: dict[str, float] | None = None,
    pcs_form: float | None = None,
    gc_rank: int | None = None,
    jerseys: list[str] | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Predict expected fantasy points for a rider on a specific stage.

    Returns dict with:
      expected_pts  – headline expected score
      win_prob      – combined win probability estimate
      reasoning     – human-readable explanation
      signal_scores – breakdown of each signal
    """
    w = weights or DEFAULT_WEIGHTS

    # --- Signal 1: VeloScore ---
    vs_signal = 0.0
    if veloscore_score is not None:
        vs_signal = veloscore_score / 10.0               # normalise to 0-1
    elif veloscore_rank is not None:
        vs_signal = _rank_scale(veloscore_rank)

    # --- Signal 2: Odds-implied win probability ---
    odds_signal = odds_prob if odds_prob is not None else 0.0

    # --- Signal 3: Discipline match ---
    disc_key = STAGE_DISCIPLINE.get(stage_type, "AVG")
    disc_raw = (cyclingoracle or {}).get(disc_key, 50.0)
    disc_signal = disc_raw / 100.0

    # --- Signal 4: Recent form ---
    form_signal = (pcs_form or 50.0) / 100.0

    # --- Composite win-probability estimate ---
    composite = (
        w["veloscore"]  * vs_signal  +
        w["odds_prob"]  * odds_signal +
        w["discipline"] * disc_signal +
        w["form"]       * form_signal
    )

    # Scale to expected points
    winner_pts   = WINNER_POINTS.get(stage_type, 500_000)
    expected_pts = composite * winner_pts

    # Add expected GC bonus
    gc_bonus = _expected_gc_bonus(gc_rank, stage_type)
    expected_pts += gc_bonus

    # Add jersey bonus (certain — rider currently wearing jersey gets it if they stay in group)
    jersey_bonus = 0
    from .scoring import JERSEY
    for jersey in (jerseys or []):
        # Leader jersey is nearly certain to carry over; be conservative
        jersey_bonus += JERSEY.get(jersey, 0) * 0.90
    expected_pts += jersey_bonus

    # --- Variance estimate (for team 3 aggressive picks) ---
    # High VeloScore + low discipline = underdog pick = high variance
    variance = (1 - disc_signal) * vs_signal * winner_pts * 0.5

    # --- Reasoning text ---
    reasons = []
    if veloscore_rank and veloscore_rank <= 5:
        reasons.append(f"VeloScore top-{veloscore_rank}")
    elif veloscore_rank and veloscore_rank <= 10:
        reasons.append(f"VeloScore rank {veloscore_rank}")
    if odds_prob and odds_prob > 0.15:
        reasons.append(f"bookmakerfavorit ({odds_prob*100:.0f}%)")
    if disc_raw >= 80:
        reasons.append(f"høj disciplinrating ({disc_key}: {disc_raw:.0f})")
    if gc_rank and gc_rank <= 5:
        reasons.append(f"GC-stilling #{gc_rank}")
    if jerseys:
        reasons.append(f"trøje: {', '.join(jerseys)}")
    if not reasons:
        reasons.append("budget-pick")

    return {
        "rider_id":      rider.get("id"),
        "full_name":     rider.get("full_name"),
        "team":          rider.get("team"),
        "price":         rider.get("price"),
        "expected_pts":  round(expected_pts),
        "win_prob":      round(composite, 4),
        "variance":      round(variance),
        "reasoning":     ", ".join(reasons),
        # disc_key / disc_raw expose the exact CyclingOracle rating used
        # so the UI can show e.g. "SPR: 87" instead of generic "discipline"
        "disc_key":      disc_key,          # e.g. "SPR", "MTN", "ITT"
        "disc_raw":      round(disc_raw, 1), # 0-100 raw CyclingOracle value
        "signal_scores": {
            "veloscore":  round(vs_signal, 3),
            "odds":       round(odds_signal, 3),
            "discipline": round(disc_signal, 3),
            "form":       round(form_signal, 3),
        },
    }


def predict_all(
    riders: list[dict],
    stage_type: str,
    veloscore_data: list[dict] | None = None,
    odds_data: dict[str, float] | None = None,
    cyclingoracle_data: dict[str, dict] | None = None,
    pcs_form_data: dict[str, float] | None = None,
    current_gc: dict[str, int] | None = None,
    current_jerseys: dict[str, list[str]] | None = None,
    weights: dict[str, float] | None = None,
) -> list[dict]:
    """
    Predict expected points for ALL riders and return sorted list.
    """
    # Build lookup maps
    vs_by_name: dict[str, dict] = {}
    if veloscore_data:
        for entry in veloscore_data:
            name = entry.get("rider", "").lower()
            vs_by_name[name] = entry

    results = []
    for rider in riders:
        name_lower = rider["full_name"].lower()
        short_lower = rider.get("short_name", "").lower()

        # Try to match by full name or short name
        vs_entry = vs_by_name.get(name_lower) or vs_by_name.get(short_lower)
        # Fuzzy: try last name
        last = name_lower.split()[-1]
        if not vs_entry:
            for k, v in vs_by_name.items():
                if last in k:
                    vs_entry = v
                    break

        pred = predict_rider(
            rider=rider,
            stage_type=stage_type,
            veloscore_rank=vs_entry.get("rank") if vs_entry else None,
            veloscore_score=vs_entry.get("veloscore") if vs_entry else None,
            odds_prob=(odds_data or {}).get(name_lower),
            cyclingoracle=(cyclingoracle_data or {}).get(rider["id"]),
            pcs_form=(pcs_form_data or {}).get(rider["id"]),
            gc_rank=(current_gc or {}).get(rider["id"]),
            jerseys=(current_jerseys or {}).get(rider["id"]),
            weights=weights,
        )
        results.append(pred)

    results.sort(key=lambda x: x["expected_pts"], reverse=True)
    return results
