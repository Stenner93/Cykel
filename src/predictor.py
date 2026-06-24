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
# Default weights.
#
# Re-calibrated from a correlation analysis against 539 rider-stages with
# known actual results (Giro 2026, 18 finished stages). calibrate.py had
# only grid-searched the VeloScore weight; the odds/discipline/form split
# was a fixed manual prior, not data-fitted. Measured Pearson r² against
# actual points (only counting rows where the signal was actually present):
#   form        r²=0.220  (n=507) — strongest single predictor
#   discipline  r²=0.150  (n=539)
#   veloscore   r²=0.015  (n=71)  — weak, but small/uncertain sample
#   odds        n=0 in this sample — no Giro odds coverage to validate
# Form was previously underweighted (10%) relative to its measured
# predictive power; veloscore was overweighted (45%) relative to its weak
# (if noisily-measured) correlation. Odds kept a meaningful prior weight
# despite no validation data, since it's theoretically sound when present.
DEFAULT_WEIGHTS = {
    "veloscore":  0.20,   # was 0.27 — reduceret; r²=0.015 i kalibrering
    "odds_prob":  0.18,
    "discipline": 0.20,   # was 0.22
    "form":       0.22,   # was 0.23
    "ml":         0.08,   # was 0.10
    "pcs_rank":   0.12,   # NY — PCS 12-måneders rangerings-kvalitetssignal
}

# Mapping stage_type → CyclingOracle discipline key
# NOTE: "ttt" (team time trial) uses ITT as the best available proxy — we
# don't have team-level TTT strength data, so individual TT rating is the
# closest signal we can use. The type is still kept distinct from "tt" so
# the UI/labels correctly show "Holdtidskørsel" rather than "Enkeltstart".
STAGE_DISCIPLINE = {
    "sprint":    "SPR",
    "mountain":  "MTN",
    "tt":        "ITT",
    "ttt":       "ITT",
    "hilly":     "HLL",
    "cobbled":   "COB",
    "gc":        "GC",
}

# Empirically-informed discipline BLEND per stage_type — a single CO key
# (above) doesn't capture stage outcomes well in isolation. Sampled 42
# historical stages (2025 Giro + 2025 TdF) and looked at each winner's
# dominant PCS specialty vs. the stage's ProfileScore:
#   profile_score 100-200 ("hilly"):  44% onedayraces, 22% climber,
#                                      22% tt,  only 11% hills-dominant
#   profile_score  40-100 ("sprint"): 67% onedayraces,  8% sprint
#   profile_score   0-40  ("sprint"): 64% sprint — genuinely flat stages
#                                      behave as expected
# "Hilly" and the rolling end of "sprint" are won by classics/puncheur
# riders (COB) far more often than a pure HLL/SPR rating would suggest.
# Sample size is modest (9-12 stages per bucket) so treat these as
# directional, not precision-calibrated — but the gap is large enough
# (11% vs 44%) to be worth correcting rather than ignoring.
STAGE_DISCIPLINE_BLEND: dict[str, dict[str, float]] = {
    "sprint":    {"SPR": 0.65, "COB": 0.35},
    "mountain":  {"MTN": 1.0},
    "tt":        {"ITT": 1.0},
    "ttt":       {"ITT": 1.0},
    "hilly":     {"COB": 0.40, "HLL": 0.35, "MTN": 0.25},
    "cobbled":   {"COB": 1.0},
    "gc":        {"GC": 1.0},
}


def _dominant_disc_key(stage_type: str) -> str:
    """Heaviest-weighted CO key in this stage type's blend — used for display."""
    # For hilly, the computation blend has COB > HLL (empirically correct), but
    # showing "COB" as label on a hilly stage is confusing — override to "HLL".
    _display_overrides = {"hilly": "HLL", "sprint": "SPR"}
    if stage_type in _display_overrides:
        return _display_overrides[stage_type]
    blend = STAGE_DISCIPLINE_BLEND.get(stage_type, {"AVG": 1.0})
    return max(blend, key=blend.get)


def _disc_blend_value(cyclingoracle: dict[str, float] | None, stage_type: str) -> tuple[float, str]:
    """
    Compute the discipline rating for a stage as a weighted blend of CO
    discipline keys (see STAGE_DISCIPLINE_BLEND), and return the dominant
    component key for display/labelling purposes.
    """
    blend = STAGE_DISCIPLINE_BLEND.get(stage_type, {"AVG": 1.0})
    co = cyclingoracle or {}
    raw = sum(weight * co.get(key, 50.0) for key, weight in blend.items())
    return raw, _dominant_disc_key(stage_type)

# Expected fantasy points IF a rider wins (by stage type).
# Sprint recalibrated for TdF 2026: ASO øgede sprintpoint markant på 7 flade
# etaper — 70 pts til vinderen (mod ~45 før) og 2 bonusspurter á 20 pts (mod 1).
# Maksimalt: 200K (etape) + 210K (70×3K) + 120K (2×20×3K) = 530K. Typisk 470K.
# Mountain/tt/hilly beholder Giro 2026-kalibreringen.
WINNER_POINTS = {
    "sprint":   680_000,
    "mountain": 630_000,
    "tt":       380_000,
    "ttt":      380_000,
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
# TdF 2026 sprint: skaleret op ~65 % pga. højere sprintpoint (70/50/40 i mål,
# 2 bonusspurter á 20 pt) — grøntrøjeledere scorer markant mere per sprintetape.
_SPRINT_CLASS_BONUS: dict[str, list[int]] = {
    "sprint":   [50_000, 30_000, 16_000,  9_000, 5_000],
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
    pcs_form_long: dict[str, float] | None = None,  # multi-season form (slow decay)
    gc_rank: int | None = None,
    jerseys: list[str] | None = None,
    weights: dict[str, float] | None = None,
    # ── New signals ────────────────────────────────────────────────────
    profile_score: int | None = None,       # PCS ProfileScore for this stage
    sprint_class_rank: int | None = None,   # Position in sprint classification
    kom_class_rank: int | None = None,      # Position in KOM classification
    expected_team_bonus: int = 0,           # Expected holdbonus kr from recent stages
    winner_pts_override: dict[str, int] | None = None,  # Race-specific winner points
    disc_raw_override: float | None = None,  # pre-rescaled field-relative disc value
    ml_prob: float | None = None,            # ML felt-normaliseret score 0-100
    pcs_n_results: int = 0,            # Antal nylige resultater i form-cache
    pcs_rank_pts: float | None = None, # Felt-normaliseret PCS 12-mdr. pts (0-100)
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

    # --- Signal 3: Discipline match (empirically-blended, see STAGE_DISCIPLINE_BLEND) ---
    if disc_raw_override is not None:
        # Already computed + field-rescaled by predict_all() — use as-is.
        disc_raw, disc_key = disc_raw_override, _dominant_disc_key(stage_type)
    else:
        disc_raw, disc_key = _disc_blend_value(cyclingoracle, stage_type)
    disc_signal = disc_raw / 100.0

    # --- Signal 4: Recent + long-term form ---
    # pcs_form may be a float (old format) or a dict with per-type scores.
    #
    # Blending strategy: 50% stage-type-specific SHORT-term (current form,
    # ~1 month half-life) + 20% overall short-term + 30% stage-type-specific
    # LONG-term (multi-season, ~4 month half-life, see scrape_pcs.py's
    # compute_all_form_scores_long).
    #
    # Rationale: a rider who is "in good shape" (high overall) should get some
    # credit even on stages where they haven't scored type-specific results
    # recently. E.g. Narváez won 3 Giro stages (mountain/hilly/rolling) →
    # overall=44. On a mountain stage, mountain_form=11 but overall reflects
    # true fitness — pure type-specific would give 11, blend lifts it.
    #
    # The long-term component guards against a different failure mode: a
    # known climber who simply hasn't had a mountain stage to show for it
    # yet this season (early in the year, or racing classics) would
    # otherwise default to neutral 50 on type-specific AND short-term
    # overall, hiding real climbing pedigree from last season. Long-term
    # form, computed over ~18 months with a slower decay, still credits
    # that pedigree without letting it dominate (30% weight, decays over
    # ~1-2 seasons).
    if isinstance(pcs_form, dict):
        type_form    = float(pcs_form.get(stage_type) or 0.0)
        overall_form = float(pcs_form.get("overall") or 50.0)

        # GC-rider sprint correction: a rider with significant mountain form AND
        # significant sprint form is likely a GC/all-rounder who won a sprint-
        # classified stage in a preparation race (small-group finish, not a GT
        # bunch sprint). In a GT they will finish safely in the peloton rather
        # than contesting the sprint. Blend toward overall to moderate the signal.
        if stage_type == "sprint":
            mtn_form = float(pcs_form.get("mountain") or 0.0)
            if mtn_form >= 25 and type_form >= 25:
                type_form = 0.5 * type_form + 0.5 * (overall_form * 0.55)

        short_term   = 0.7 * type_form + 0.3 * overall_form
    else:
        short_term = float(pcs_form) if pcs_form is not None else 50.0

    if pcs_form_long:
        long_type_form = float(pcs_form_long.get(stage_type) or short_term)
        form_val = 0.7 * short_term + 0.3 * long_type_form
    else:
        form_val = short_term

    form_signal = form_val / 100.0

    # Sparse data protection: ryttere med få resultater har usikre form-estimater.
    # Blend form_signal mod neutral (0.5) proportionalt med antal resultater.
    # 0 resultater → form_signal=0.5 (neutral); 8+ resultater → fuldt signal.
    _sparsity = min(1.0, pcs_n_results / 8.0)
    form_signal = _sparsity * form_signal + (1.0 - _sparsity) * 0.5

    # --- Composite win-probability estimate ---
    # Dynamisk normalisering: kun tilstedeværende signaler bidrager, og
    # deres relative vægte bevares. Dette forhindrer at fraværende signaler
    # (VeloScore, odds, ML) spiser kapacitet og gør ryttere ens.
    has_vs       = veloscore_rank is not None or veloscore_score is not None
    has_odds     = odds_prob is not None
    has_ml       = ml_prob is not None
    has_pcs_rank = pcs_rank_pts is not None

    available_signals: dict[str, float] = {}
    if has_vs:
        available_signals["veloscore"]  = vs_signal
    if has_odds:
        available_signals["odds_prob"]  = odds_signal
    available_signals["discipline"] = disc_signal
    available_signals["form"]       = form_signal
    if has_ml:
        available_signals["ml"]       = ml_prob / 100.0  # 0-100 → 0-1
    if has_pcs_rank:
        available_signals["pcs_rank"] = pcs_rank_pts / 100.0

    total_w = sum(w.get(k, 0.0) for k in available_signals)
    if total_w > 0:
        composite = sum(w.get(k, 0.0) / total_w * v
                        for k, v in available_signals.items())
    else:
        composite = 0.0

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
        "winner_pts":    round(winner_pts),
        "composite_base_pts": round(composite * winner_pts),
        "variance":      round(variance),
        "reasoning":     ", ".join(reasons),
        # disc_key / disc_raw: felt-normaliseret disciplinrating (bedste i feltet → 100).
        # disc_co_raw: den absolutte CO-blend-værdi FØR felt-normalisering (tilføjes
        # af predict_all() efter dette kald — vises i tooltip som "CO: xx/100").
        "disc_key":      disc_key,           # e.g. "SPR", "MTN", "ITT", "HLL"
        "disc_raw":      round(disc_raw, 1), # 0-100 felt-relativt (bedste → 100)
        # form_score: the stage-type-blended form value actually used (0-100)
        # For form_by_type dicts: 70% type-specific + 30% overall
        "form_score":    round(form_val, 1),
        "signal_scores": {
            "veloscore":    round(vs_signal, 3),
            "odds":         round(odds_signal, 3),
            "discipline":   round(disc_signal, 3),
            "form":         round(form_signal, 3),
            "ml":           round(ml_prob / 100.0, 3) if ml_prob is not None else None,
            "pcs_rank":     round(pcs_rank_pts / 100.0, 3) if pcs_rank_pts is not None else None,
        },
        "profile_scale":     round(_profile_scale(profile_score, stage_type), 3),
        "sprint_class_rank": sprint_class_rank,
        "kom_class_rank":    kom_class_rank,
        "team_bonus_exp":    expected_team_bonus,
    }


STATUS_MULTIPLIERS: dict[str, float] = {
    "fresh":     1.15,
    "normal":    1.00,
    "defending": 0.85,
    "fatigued":  0.55,
    "sick":      0.30,
    "dns":       0.00,
}


def predict_all(
    riders: list[dict],
    stage_type: str,
    veloscore_data: list[dict] | None = None,
    odds_data: dict[str, float] | None = None,
    cyclingoracle_data: dict[str, dict] | None = None,
    pcs_form_data: dict[str, float] | None = None,
    pcs_form_long_data: dict[str, dict] | None = None,  # multi-season form (slow decay)
    current_gc: dict[str, int] | None = None,
    current_jerseys: dict[str, list[str]] | None = None,
    weights: dict[str, float] | None = None,
    # ── New signals ────────────────────────────────────────────────────
    profile_score: int | None = None,
    sprint_kom_data: dict[str, dict] | None = None,     # from holdet_sprint_kom.json
    team_bonus_data: dict[str, int] | None = None,      # from holdet_team_bonus.json
    winner_pts_override: dict[str, int] | None = None,  # Race-specific winner points
    rider_context: dict[str, dict] | None = None,       # {rider_id: {status, note}}
    pcs_specialty_data: dict[str, dict] | None = None,  # {rider_id: {climber, sprint, tt, ...}}
    ml_prob_data: dict[str, float] | None = None,       # {rider_id: felt-normaliseret 0-100}
    pcs_rank_data: dict[str, float] | None = None,      # {rider_id: pts_raw}
    pcs_n_results_data: dict[str, int] | None = None,   # {rider_id: n_results}
) -> list[dict]:
    """
    Predict expected points for ALL riders and return sorted list.
    """
    # ── PCS specialty → CyclingOracle normalisation ──────────────────────────
    # PCS specialty keys: climber, sprint, tt, hills, onedayraces, gc
    # CO keys:            MTN,     SPR,    ITT, HLL,  COB,         GC
    _PCS_TO_CO: dict[str, str] = {
        "climber":    "MTN",
        "sprint":     "SPR",
        "tt":         "ITT",
        "hills":      "HLL",
        "onedayraces":"COB",
        "gc":         "GC",
    }
    # Compute per-specialty 95th-percentile maxima — KUN baseret på ryttere
    # i det aktuelle felt (riders), ikke hele pcs_form-cachen. Derved er
    # Pogacar/Vingegaard referencepunktet (100), ikke det brede historiske dataset.
    pcs_field_maxima: dict[str, float] = {}
    if pcs_specialty_data:
        from collections import defaultdict
        field_rider_ids = {r["id"] for r in riders}
        vals: dict[str, list[float]] = defaultdict(list)
        for rid, spec_dict in pcs_specialty_data.items():
            if rid not in field_rider_ids:
                continue          # kun ryttere i dette løbs felt
            for k, v in spec_dict.items():
                vals[k].append(float(v))
        for k, lst in vals.items():
            lst.sort()
            idx = max(0, int(len(lst) * 0.95) - 1)
            pcs_field_maxima[k] = lst[idx] or 1.0

    def _pcs_to_co(rider_id: str) -> dict[str, float] | None:
        """Convert PCS specialty scores to CO-style dict (0-100 scale) for one rider."""
        spec = (pcs_specialty_data or {}).get(rider_id)
        if not spec or not pcs_field_maxima:
            return None
        co: dict[str, float] = {}
        for pcs_k, co_k in _PCS_TO_CO.items():
            if pcs_k in spec and pcs_k in pcs_field_maxima:
                co[co_k] = min(100.0, spec[pcs_k] / pcs_field_maxima[pcs_k] * 100.0)
        # AVG = mean of all available
        if co:
            co["AVG"] = round(sum(co.values()) / len(co), 1)
        return co or None

    # ── Build lookup maps ────────────────────────────────────────────────────
    vs_by_name: dict[str, dict] = {}
    if veloscore_data:
        for entry in veloscore_data:
            name = entry.get("rider", "").lower()
            vs_by_name[name] = entry

    # ── Pass 1: blend CO+PCS discipline data, then field-relative rescale ────
    # Raw CO/PCS discipline ratings for capable riders often cluster tightly
    # (e.g. 70-95 for ITT specialists). Left as-is, that narrow band barely
    # moves the composite score once divided by 100, so without VeloScore/
    # odds to spread riders out, everyone ends up within ~50-80k of each
    # other regardless of how strong they actually are. Min-max rescaling
    # the field's discipline values to the FULL 0-100 range before computing
    # the composite restores meaningful differentiation while preserving
    # rank order and relative magnitude (best in field → 100, worst → 0).
    rider_blended: dict[str, dict | None] = {}
    field_vals: dict[str, float] = {}   # per-rider STAGE_DISCIPLINE_BLEND value

    for rider in riders:
        co_entry  = (cyclingoracle_data or {}).get(rider["id"])
        pcs_entry = _pcs_to_co(rider["id"])
        if co_entry and pcs_entry:
            blended = {
                k: round(co_entry.get(k, 50.0) * 0.70 + pcs_entry.get(k, 50.0) * 0.30, 1)
                for k in set(co_entry) | set(pcs_entry)
            }
        elif pcs_entry:
            blended = dict(pcs_entry)
        elif co_entry:
            blended = dict(co_entry)
        else:
            blended = {}
        rider_blended[rider["id"]] = blended
        field_vals[rider["id"]], _ = _disc_blend_value(blended, stage_type)

    # ── TTT: replace individual rating with TEAM strength ────────────────
    # In a team time trial, the whole team finishes together and every rider
    # receives the SAME stage-position points in Holdet's scoring — an
    # individual's own ITT rating is the wrong signal. What matters is the
    # team's collective TTT strength. We use the average of each team's
    # 6 strongest riders (a TTT squad typically drops its weakest 1-2
    # riders, but the recorded time still reflects the front group's pace),
    # then give every rider on that team this same team-level value.
    if stage_type == "ttt":
        from collections import defaultdict
        team_to_rids: dict[str, list[str]] = defaultdict(list)
        for rider in riders:
            team_to_rids[rider.get("team", "")].append(rider["id"])

        for team, rids in team_to_rids.items():
            vals = sorted((field_vals[rid] for rid in rids if rid in field_vals), reverse=True)
            if not vals:
                continue
            top6 = vals[:6] if len(vals) >= 6 else vals
            team_avg = sum(top6) / len(top6)
            for rid in rids:
                if rid in field_vals:
                    field_vals[rid] = team_avg

    disc_rescaled: dict[str, float] = dict(field_vals)
    if field_vals:
        # Rank-based: best → 100, falling exponentially.
        # rank 1→100, rank 5→76, rank 10→50, rank 20→25
        sorted_rids = sorted(field_vals, key=field_vals.__getitem__, reverse=True)
        disc_rescaled = {
            rid: round(100.0 * math.exp(-0.07 * i), 1)
            for i, rid in enumerate(sorted_rids)
        }

    # ── Field-normaliser PCS 12-mdr. rankingpoint (0-100) ────────────────────
    pcs_rank_normalized: dict[str, float] = {}
    if pcs_rank_data:
        raw_pts = {rid: pts for rid, pts in pcs_rank_data.items() if pts > 0}
        if raw_pts:
            sorted_pts = sorted(raw_pts.values())
            idx90 = max(0, int(len(sorted_pts) * 0.90) - 1)
            max_pts = sorted_pts[idx90] or 1.0
            pcs_rank_normalized = {
                rid: round(min(100.0, pts / max_pts * 100.0), 1)
                for rid, pts in raw_pts.items()
            }

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
        blended = rider_blended.get(rider["id"]) or None

        pred = predict_rider(
            rider=rider,
            stage_type=stage_type,
            veloscore_rank=vs_entry.get("rank") if vs_entry else None,
            veloscore_score=vs_entry.get("veloscore") if vs_entry else None,
            odds_prob=(odds_data or {}).get(name_lower),
            cyclingoracle=blended,
            pcs_form=(pcs_form_data or {}).get(rider["id"]),
            pcs_form_long=(pcs_form_long_data or {}).get(rider["id"]),
            gc_rank=(current_gc or {}).get(rider["id"]),
            jerseys=(current_jerseys or {}).get(rider["id"]),
            weights=weights,
            profile_score=profile_score,
            sprint_class_rank=sk.get("sprint_rank"),
            kom_class_rank=sk.get("kom_rank"),
            expected_team_bonus=(team_bonus_data or {}).get(rider["id"], 0),
            winner_pts_override=winner_pts_override,
            disc_raw_override=disc_rescaled.get(rider["id"]),
            ml_prob=(ml_prob_data or {}).get(rider["id"]),
            pcs_n_results=(pcs_n_results_data or {}).get(rider["id"], 0),
            pcs_rank_pts=pcs_rank_normalized.get(rider["id"]),
        )
        # disc_co_raw = rå CO-blend-værdi FØR felt-normalisering (bruges til display,
        # så brugeren ser det absolutte CO-tal, ikke det felt-relative 0-100 tal).
        pred["disc_co_raw"] = round(field_vals.get(rider["id"], 0), 1)
        results.append(pred)

    # ── Rank-based calibration ───────────────────────────────────────────────
    # The composite signal ranks riders well but its absolute value clusters
    # at 0.60–0.75 when signals are sparse (e.g. TTT stage 1 with no
    # VeloScore/odds). Multiplying by winner_pts then gives a flat band
    # where 40+ riders all score ~260K, which is unrealistic.
    #
    # Fix: use composite ORDER to rank riders, then apply an exponential decay
    # based on field rank so the MAGNITUDE matches empirical Giro/Dauphiné
    # distributions (rank 1 → 35% of winner_pts, rank 5 → 29%, rank 20 → 17%,
    # rank 50 → 8%, rank 100 → 2%). GC/jersey/sprint/KOM bonuses are untouched.
    results.sort(key=lambda x: x["expected_pts"], reverse=True)
    _n = len(results)
    for _i, pred in enumerate(results):
        _frac = _i / max(_n - 1, 1)
        _wp   = pred.get("winner_pts", 500_000)
        _addon = pred["expected_pts"] - pred["composite_base_pts"]
        _calibrated_base = round(0.35 * math.exp(-2.44 * _frac) * _wp)
        pred["expected_pts"] = _calibrated_base + _addon

    # ── Context multipliers ──────────────────────────────────────────────────
    for pred in results:
        ctx = (rider_context or {}).get(pred["rider_id"])
        if ctx:
            status = ctx.get("status", "normal")
            if status not in STATUS_MULTIPLIERS:
                print(f"  [WARN] Ukendt status {status!r} for {pred['rider_id']}"
                      f" — brug: {list(STATUS_MULTIPLIERS)}")
                status = "normal"
            mult   = STATUS_MULTIPLIERS.get(status, 1.0)
            pred["expected_pts"]   = round(pred["expected_pts"] * mult)
            pred["context_status"] = status
            pred["context_note"]   = ctx.get("note", "")
            pred["context_mult"]   = mult
        else:
            pred["context_status"] = "normal"
            pred["context_note"]   = ""
            pred["context_mult"]   = 1.0

    results.sort(key=lambda x: x["expected_pts"], reverse=True)
    return results
