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


def _profile_scale(profile_score: int | None, stage_type: str) -> float:
    """
    Scale WINNER_POINTS up or down based on stage difficulty (PCS ProfileScore).

    Profile score guide:
      0–40:   flat/sprint        sprint stages: scale = 1.0 (unchanged)
      40–100: slightly rolling
      100–200: hilly
      200–350: mountain (p4, often no summit finish → now classified as hilly)
      350+:   high mountain summit finish

    Scale factors:
      sprint / tt / cobbled  → always 1.0 (profile score not relevant)
      hilly  → 0.85 at score=0  …  1.0 at score=180  …  1.15 at score=300
      mountain → 0.82 at score=200 … 1.0 at score=330 … 1.35 at score=500
    """
    if profile_score is None:
        return 1.0
    if stage_type in ("sprint", "tt", "cobbled"):
        return 1.0
    if stage_type == "hilly":
        # Linear: score 0 → 0.85, score 300 → 1.15
        return max(0.80, min(1.20, 0.85 + profile_score / 300.0 * 0.30))
    if stage_type == "mountain":
        # Anchored at 330 → 1.0; steep climb above and below
        return max(0.80, min(1.40, 0.82 + (profile_score - 200) / 330.0 * 0.58))
    return 1.0


# Expected sprint/KOM classification bonus per stage, by classification rank (1-5)
# Reflects the expected fantasy pts from ongoing sprint/KOM point accumulation.
# Higher on the stage type where specialists score most.
_SPRINT_CLASS_BONUS: dict[str, list[int]] = {
    "sprint":   [30_000, 18_000, 10_000,  6_000, 3_000],
    "hilly":    [12_000,  7_000,  4_000,  2_000, 1_000],
    "cobbled":  [ 6_000,  4_000,  2_000,  1_000,   500],
    "mountain": [ 3_000,  2_000,  1_000,    500,   200],
    "tt":       [     0,      0,      0,      0,     0],
}
_KOM_CLASS_BONUS: dict[str, list[int]] = {
    "mountain": [24_000, 15_000,  9_000,  5_000, 2_000],
    "hilly":    [ 9_000,  6_000,  3_000,  1_500,   750],
    "cobbled":  [ 3_000,  2_000,  1_000,    500,   200],
    "sprint":   [ 1_500,  1_000,    500,    200,   100],
    "tt":       [     0,      0,      0,      0,     0],
}


# GC bonus model: expected GC points based on current standing + stage type.
# Extended to include probabilistic entry into top-10 for ranks 11-20.
def _expected_gc_bonus(gc_rank: int | None, stage_type: str) -> float:
    from .scoring import GC_PTS
    if gc_rank is None or gc_rank > 20:
        return 0.0

    # Deterministic rank shift: mountain stages pull GC riders up
    shift = {"mountain": -1, "gc": -2, "sprint": 1, "tt": 0, "hilly": 0, "cobbled": 1}
    effective_rank = max(1, gc_rank + shift.get(stage_type, 0))

    if effective_rank <= 10:
        return GC_PTS.get(effective_rank, 0)

    # Ranks 11-20: probabilistic chance of entering top-10 on hard stages
    # (e.g. a GC breakaway, time bonus, or a rival losing time)
    if stage_type in ("mountain", "gc"):
        if gc_rank <= 12:
            return 0.20 * GC_PTS.get(10, 10_000)   # 20% chance → ~2000 kr
        if gc_rank <= 15:
            return 0.08 * GC_PTS.get(10, 10_000)   # 8% chance  → ~800 kr
    elif stage_type == "hilly":
        if gc_rank <= 12:
            return 0.08 * GC_PTS.get(10, 10_000)   # 8% chance

    return 0.0


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
    # ── New signals ────────────────────────────────────────────────────
    profile_score: int | None = None,       # PCS ProfileScore for this stage
    sprint_class_rank: int | None = None,   # Position in sprint classification
    kom_class_rank: int | None = None,      # Position in KOM classification
    expected_team_bonus: int = 0,           # Expected holdbonus kr from recent stages
    winner_pts_override: dict[str, int] | None = None,  # Race-specific winner points
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
    # pcs_form may be a float (old format) or a dict with per-type scores.
    #
    # Blending strategy: 70% stage-type-specific + 30% overall form.
    # Rationale: a rider who is "in good shape" (high overall) should get some
    # credit even on stages where they haven't scored type-specific results.
    # E.g. Narváez won 3 Giro stages (mountain/hilly/rolling) → overall=44.
    # On a mountain stage, mountain_form=11 but overall reflects true fitness.
    # Pure type-specific would give 11; blend gives 0.7*11 + 0.3*44 ≈ 21.
    if isinstance(pcs_form, dict):
        type_form    = float(pcs_form.get(stage_type) or 0.0)
        overall_form = float(pcs_form.get("overall") or 50.0)
        form_val = 0.7 * type_form + 0.3 * overall_form
    else:
        form_val = float(pcs_form) if pcs_form is not None else 50.0
    form_signal = form_val / 100.0

    # --- Composite win-probability estimate ---
    # If external signals (VeloScore, odds) are absent, normalize the remaining
    # weights so discipline + form carry the full weight.  This prevents the
    # ~70% dead-weight that would otherwise make every rider look identical when
    # running on discipline/form data alone (e.g. Dauphiné without VeloScore).
    # When VeloScore IS available the original weights apply unchanged.
    has_vs   = veloscore_rank is not None or veloscore_score is not None
    has_odds = odds_prob is not None

    if not has_vs and not has_odds:
        # Only discipline + form available → normalize to full capacity
        total_avail = w["discipline"] + w["form"]
        nw_disc = w["discipline"] / total_avail if total_avail else 0.5
        nw_form = w["form"]       / total_avail if total_avail else 0.5
        composite = nw_disc * disc_signal + nw_form * form_signal
    elif not has_vs:
        # Odds + discipline + form — normalize (no VeloScore)
        total_avail = w["odds_prob"] + w["discipline"] + w["form"]
        composite = (
            w["odds_prob"]  / total_avail * odds_signal +
            w["discipline"] / total_avail * disc_signal +
            w["form"]       / total_avail * form_signal
        )
    else:
        # Normal path — VeloScore present, use configured weights as-is
        composite = (
            w["veloscore"]  * vs_signal  +
            w["odds_prob"]  * odds_signal +
            w["discipline"] * disc_signal +
            w["form"]       * form_signal
        )

    # Scale winner_pts by stage difficulty (ProfileScore)
    # Use race-specific override if provided (e.g. Dauphiné has different point scale)
    _wp_table  = winner_pts_override if winner_pts_override else WINNER_POINTS
    winner_pts = _wp_table.get(stage_type, _wp_table.get("hilly", 500_000))
    winner_pts *= _profile_scale(profile_score, stage_type)

    # Scale to expected points
    expected_pts = composite * winner_pts

    # Add expected GC bonus (probabilistic for ranks 11–20)
    gc_bonus = _expected_gc_bonus(gc_rank, stage_type)
    expected_pts += gc_bonus

    # Add jersey bonus (certain — rider currently wearing jersey gets it if they stay in group)
    jersey_bonus = 0
    from .scoring import JERSEY
    for jersey in (jerseys or []):
        # Leader jersey is nearly certain to carry over; be conservative
        jersey_bonus += JERSEY.get(jersey, 0) * 0.90
    expected_pts += jersey_bonus

    # Add expected sprint classification bonus
    if sprint_class_rank and 1 <= sprint_class_rank <= 5:
        bonus_table = _SPRINT_CLASS_BONUS.get(stage_type, [])
        expected_pts += bonus_table[sprint_class_rank - 1] if bonus_table else 0

    # Add expected KOM classification bonus
    if kom_class_rank and 1 <= kom_class_rank <= 5:
        bonus_table = _KOM_CLASS_BONUS.get(stage_type, [])
        expected_pts += bonus_table[kom_class_rank - 1] if bonus_table else 0

    # Add expected team bonus (from recent stage history)
    expected_pts += expected_team_bonus

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
    if sprint_class_rank and sprint_class_rank <= 3:
        reasons.append(f"sprint-klass. #{sprint_class_rank}")
    if kom_class_rank and kom_class_rank <= 3:
        reasons.append(f"bjerg-klass. #{kom_class_rank}")
    if expected_team_bonus >= 20_000:
        reasons.append(f"holdbonus ~{expected_team_bonus//1000}k")
    if profile_score is not None:
        scale = _profile_scale(profile_score, stage_type)
        if scale >= 1.15:
            reasons.append(f"hård etape (score {profile_score})")
        elif scale <= 0.90:
            reasons.append(f"let etape (score {profile_score})")
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
        "disc_key":      disc_key,           # e.g. "SPR", "MTN", "ITT"
        "disc_raw":      round(disc_raw, 1), # 0-100 raw CyclingOracle value
        # form_score: the stage-type-blended form value actually used (0-100)
        # For form_by_type dicts: 70% type-specific + 30% overall
        "form_score":    round(form_val, 1),
        "signal_scores": {
            "veloscore":    round(vs_signal, 3),
            "odds":         round(odds_signal, 3),
            "discipline":   round(disc_signal, 3),
            "form":         round(form_signal, 3),
        },
        "profile_scale":     round(_profile_scale(profile_score, stage_type), 3),
        "sprint_class_rank": sprint_class_rank,
        "kom_class_rank":    kom_class_rank,
        "team_bonus_exp":    expected_team_bonus,
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
    # ── New signals ────────────────────────────────────────────────────
    profile_score: int | None = None,
    sprint_kom_data: dict[str, dict] | None = None,     # from holdet_sprint_kom.json
    team_bonus_data: dict[str, int] | None = None,      # from holdet_team_bonus.json
    winner_pts_override: dict[str, int] | None = None,  # Race-specific winner points
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

        # 1. Exact full name or short name match
        vs_entry = vs_by_name.get(name_lower) or vs_by_name.get(short_lower)

        # 2. Fuzzy last-name match (handles missing first name in VeloScore data)
        if not vs_entry:
            last  = name_lower.split()[-1]
            first = name_lower.split()[0] if " " in name_lower else ""
            # Find all VS entries whose last word equals our last name
            candidates = [
                (k, v) for k, v in vs_by_name.items()
                if k.split()[-1] == last
            ]
            if len(candidates) == 1:
                # Unique last-name match — safe to use
                vs_entry = candidates[0][1]
            elif len(candidates) > 1 and first:
                # Multiple riders share last name → require first-name match
                for k, v in candidates:
                    k_words = k.split()
                    if k_words[0] == first:        # exact first name
                        vs_entry = v
                        break
                if not vs_entry:
                    for k, v in candidates:
                        if k.split()[0][0] == first[0]:    # first initial
                            vs_entry = v
                            break

        sk = (sprint_kom_data or {}).get(rider["id"], {})
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
            profile_score=profile_score,
            sprint_class_rank=sk.get("sprint_rank"),
            kom_class_rank=sk.get("kom_rank"),
            expected_team_bonus=(team_bonus_data or {}).get(rider["id"], 0),
            winner_pts_override=winner_pts_override,
        )
        results.append(pred)

    results.sort(key=lambda x: x["expected_pts"], reverse=True)
    return results
