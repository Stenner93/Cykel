"""
ML-signal: LightGBM top-5 sandsynlighed for en GT-etape.

Indlæser data/ml/model.lgbm og beregner per-rytter sandsynligheder
baseret på:
  - Etapetype og profil-score
  - Rullende GT-form (gennemsnitlig placering de seneste 5/10 etaper)

Returnerer {rider_id: score 0-100} — felt-normaliseret inden for feltet.
Returnerer tom dict hvis model ikke er tilgængelig (lgbm ikke installeret
eller model.lgbm ikke fundet).
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

ROOT       = Path(__file__).parent.parent
MODEL_PATH = ROOT / "data" / "ml" / "model.lgbm"

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
    fra etaper INDEN den givne etape.
    Returnerer (-1, -1, 0) hvis ingen data (begyndelsen af løbet).
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


def compute_ml_scores(
    riders: list[dict[str, Any]],
    stage_type: str,
    stage_num: int,
    profile_score: int | None,
    gt_results: dict | None,
    pcs_form_raw: dict | None = None,
) -> dict[str, float]:
    """
    Beregn ML top-5 sandsynlighedsscorer for alle ryttere.

    Parameters
    ----------
    riders       : rytterlisten fra riders.json
    stage_type   : etapetype ("sprint", "mountain", etc.)
    stage_num    : den etape der forudsiges (bruges til rolling-form cutoff)
    profile_score: PCS ProfileScore for etapen
    gt_results   : indhold af data/cache/gt_stage_results.json
    pcs_form_raw : rå indhold af data/cache/pcs_form.json (til slug-mapping)

    Returns
    -------
    {rider_id: score 0-100}  — felt-normaliseret.
    Tom dict hvis model ikke er tilgængelig.
    """
    model = _get_model()
    if model is None:
        return {}

    try:
        import numpy as np
    except ImportError:
        return {}

    stages = (gt_results or {}).get("stages", {})
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

        # co_* og spec_* var -1 i træningsdataen pga. key-mismatch;
        # modellen har lært at ignorere dem — brug -1 her også for konsistens.
        row = [
            ps,
            is_sprint, is_mountain, is_hilly, is_tt,
            -1.0, -1.0, -1.0, -1.0, -1.0, -1.0,  # co_mtn..co_gc
            -1.0, -1.0, -1.0, -1.0,               # spec_climber..spec_hills
            gt_form_5, gt_form_10, float(gt_wins),
        ]
        rows.append(row)
        rider_ids.append(rid)

    if not rows:
        return {}

    X     = np.array(rows, dtype=np.float32)
    probs = model.predict(X)  # shape (n_riders,), værdier 0-1

    # Felt-normalisér til 0-100 (bevarer rang-orden, max differentering)
    lo, hi = float(probs.min()), float(probs.max())
    if hi > lo:
        scores = [(float(p) - lo) / (hi - lo) * 100.0 for p in probs]
    else:
        scores = [50.0] * len(probs)

    return {rid: round(s, 1) for rid, s in zip(rider_ids, scores)}
