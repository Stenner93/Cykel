"""
ML-signal: LightGBM top-5 sandsynlighed for en GT-etape.

To lag:
  1. Historisk GT-styrke (etape 1-4 og manglende in-race data):
     Recency-vægtet gennemsnitlig placering pr. rytter på DENNE etapetype
     i 2021-2025 GT-løb. Felt-normaliseret 0-100 inden for feltet.
     Beregnet af train_model.py → data/cache/rider_historical_form.json.
     Vægte: 2025=5 · 2024=3 · 2023=2 · 2022=1 · 2021=1

  2. LightGBM rolling-form (fra etape 5+):
     Indlæser data/ml/model.lgbm og beregner per-rytter sandsynligheder
     baseret på gennemsnitlig placering i de seneste 5/10 etaper af
     det AKTUELLE løb. Felt-normaliseret 0-100.

Returnerer {rider_id: score 0-100}.
Returnerer tom dict hvis ingen model OG ingen historisk form er tilgængelig.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

ROOT       = Path(__file__).parent.parent
MODEL_PATH = ROOT / "data" / "ml" / "model.lgbm"
HIST_FORM_PATH = ROOT / "data" / "cache" / "rider_historical_form.json"

# Antal afviklede etaper krævet for at skifte til LightGBM rolling-form.
# Under denne grænse bruges historisk styrke i stedet.
MIN_GT_STAGES_FOR_MODEL = 5

# Skal matche rækkefølgen brugt i build_training_data.py / train_model.py
_FEATURE_COLS = [
    "profile_score",
    "is_sprint", "is_mountain", "is_hilly", "is_tt",
    "co_mtn", "co_spr", "co_hll", "co_itt", "co_cob", "co_gc",
    "spec_climber", "spec_sprint", "spec_tt", "spec_hills",
    "gt_form_5", "gt_form_10", "gt_wins_so_far",
]

_model = None
_model_loaded = False
_hist_form: dict[str, dict] | None = None
_hist_form_loaded = False


def _get_model():
    global _model, _model_loaded
    if not _model_loaded:
        try:
            import lightgbm as lgb
            if MODEL_PATH.exists():
                _model = lgb.Booster(model_file=str(MODEL_PATH))
        except ImportError:
            pass
        _model_loaded = True
    return _model


def _get_hist_form() -> dict[str, dict]:
    """Lazy-load recency-weighted historisk GT-form."""
    global _hist_form, _hist_form_loaded
    if not _hist_form_loaded:
        if HIST_FORM_PATH.exists():
            _hist_form = json.loads(HIST_FORM_PATH.read_text(encoding="utf-8"))
        else:
            _hist_form = {}
        _hist_form_loaded = True
    return _hist_form or {}


def _holdet_to_pcs_slug(rider_id: str, pcs_form_raw: dict | None) -> str:
    """
    Konverter Holdet-ID til PCS slug.
    Foretrækker pcs_url fra pcs_form.json (håndterer accenter korrekt).
    Fallback: erstat _ med - (virker for ASCII-navne).
    """
    if pcs_form_raw:
        entry = pcs_form_raw.get(rider_id, {})
        pcs_url = entry.get("pcs_url", "")
        if pcs_url and "/rider/" in pcs_url:
            return pcs_url.split("/rider/")[-1]
    return rider_id.replace("_", "-")


def _rolling_form(
    pcs_slug: str,
    stages: dict[str, list[dict]],
    before_stage: int,
) -> tuple[float, float, int]:
    """
    Beregn (gt_form_5, gt_form_10, gt_wins_so_far) for en rytter
    fra etaper INDEN den givne etape i det aktuelle løb.
    Returnerer (-1, -1, 0) hvis ingen data.
    """
    positions: list[int] = []
    wins = 0

    for skey in sorted(stages.keys(), key=int):
        if int(skey) >= before_stage:
            break
        for r in stages[skey]:
            if r.get("rider_slug") == pcs_slug:
                if not r.get("dnf"):
                    pos = r.get("position", 999)
                    positions.append(pos)
                    if pos == 1:
                        wins += 1
                break  # Max ét resultat per etape per rytter

    if not positions:
        return -1.0, -1.0, 0

    form_5  = round(sum(positions[-5:])  / len(positions[-5:]),  1)
    form_10 = round(sum(positions[-10:]) / len(positions[-10:]), 1)
    return form_5, form_10, wins


# Tættest beslægtede etapetyper som fallback
_TYPE_FALLBACKS: dict[str, list[str]] = {
    "mountain": ["hilly"],
    "sprint":   ["hilly"],
    "hilly":    ["mountain", "sprint"],
    "tt":       ["hilly"],
    "cobbled":  ["hilly", "sprint"],
}


def _historical_strength_scores(
    riders: list[dict[str, Any]],
    stage_type: str,
    pcs_form_raw: dict | None,
) -> dict[str, float]:
    """
    Felt-normaliseret historisk GT-styrke (0-100) baseret på type-specifik
    gennemsnitlig placering i 2021-2025.

    100 = bedste i feltet · 0 = svageste · 50 = ingen historik (neutral).
    Lavere historisk gennemsnitlig placering → højere score.
    """
    hist_form = _get_hist_form()
    hist_type = "tt" if stage_type == "ttt" else stage_type

    raw: dict[str, float | None] = {}
    for rider in riders:
        rid  = rider["id"]
        slug = _holdet_to_pcs_slug(rid, pcs_form_raw)
        h    = hist_form.get(slug, {})

        val = h.get(hist_type)
        if val is None:
            for fb in _TYPE_FALLBACKS.get(hist_type, []):
                val = h.get(fb)
                if val is not None:
                    break
        raw[rid] = val

    known = {rid: v for rid, v in raw.items() if v is not None}
    if not known:
        return {rider["id"]: 50.0 for rider in riders}

    lo, hi = min(known.values()), max(known.values())
    result: dict[str, float] = {}
    for rider in riders:
        rid = rider["id"]
        v   = raw[rid]
        if v is None:
            result[rid] = 50.0  # ingen historik → neutral
        elif hi > lo:
            # Inverter: lavere placering (bedre finish) → højere score
            result[rid] = round(100.0 - (v - lo) / (hi - lo) * 100.0, 1)
        else:
            result[rid] = 50.0
    return result


def _lgbm_scores(
    riders: list[dict[str, Any]],
    stage_type: str,
    stage_num: int,
    profile_score: int | None,
    stages: dict[str, list[dict]],
    pcs_form_raw: dict | None,
) -> dict[str, float] | None:
    """
    Kør LightGBM-modellen med aktuel in-race rolling-form.
    Returnerer None hvis model ikke er tilgængelig.
    """
    model = _get_model()
    if model is None:
        return None

    try:
        import numpy as np
    except ImportError:
        return None

    ps = float(profile_score or 0)
    is_sprint   = int(stage_type == "sprint")
    is_mountain = int(stage_type == "mountain")
    is_hilly    = int(stage_type == "hilly")
    is_tt       = int(stage_type in ("tt", "ttt"))

    rows: list[list[float]] = []
    rider_ids: list[str]    = []

    for rider in riders:
        rid  = rider["id"]
        slug = _holdet_to_pcs_slug(rid, pcs_form_raw)

        gt_form_5, gt_form_10, gt_wins = _rolling_form(slug, stages, stage_num)

        # co_* og spec_* var -1 i træningsdataen pga. key-mismatch — ignorer.
        row = [
            ps,
            is_sprint, is_mountain, is_hilly, is_tt,
            -1.0, -1.0, -1.0, -1.0, -1.0, -1.0,
            -1.0, -1.0, -1.0, -1.0,
            gt_form_5, gt_form_10, float(gt_wins),
        ]
        rows.append(row)
        rider_ids.append(rid)

    if not rows:
        return None

    X     = np.array(rows, dtype=np.float32)
    probs = model.predict(X)

    lo, hi = float(probs.min()), float(probs.max())
    if hi > lo:
        scores = [(float(p) - lo) / (hi - lo) * 100.0 for p in probs]
    else:
        scores = [50.0] * len(probs)

    return {rid: round(s, 1) for rid, s in zip(rider_ids, scores)}


def compute_ml_scores(
    riders: list[dict[str, Any]],
    stage_type: str,
    stage_num: int,
    profile_score: int | None,
    gt_results: dict | None,
    pcs_form_raw: dict | None = None,
) -> dict[str, float]:
    """
    Beregn ML/historisk styrke-scorer for alle ryttere.

    Strategi:
    - Etape 1-4 (< 5 afviklede etaper): historisk GT-styrke 2021-2025.
      Direkte felt-normaliseret scorering baseret på type-specifik gennemsnitlig
      placering — virker fra etape 1 og giver en klar "styrke-rangering".
    - Etape 5+ (>= 5 afviklede etaper): LightGBM med in-race rolling-form.
      Modellen bruger gennemsnitlig placering i de seneste 5/10 etaper og
      differentierer baseret på aktuel form i dette specifikke løb.

    Returnerer {rider_id: score 0-100} — felt-normaliseret.
    """
    stages     = (gt_results or {}).get("stages", {})
    n_done     = sum(1 for s in stages if int(s) < stage_num)

    if n_done >= MIN_GT_STAGES_FOR_MODEL:
        lgbm = _lgbm_scores(riders, stage_type, stage_num, profile_score, stages, pcs_form_raw)
        if lgbm is not None:
            return lgbm

    # Fallback: historisk styrke (etape 1-4, eller model ikke tilgængelig)
    hist = _historical_strength_scores(riders, stage_type, pcs_form_raw)
    if hist:
        return hist

    return {}
