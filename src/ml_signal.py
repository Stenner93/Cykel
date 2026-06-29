"""
ML-signal: LightGBM top-5 sandsynlighed for en GT-etape.

To lag:
  1. Historisk GT-styrke (etape 1-4 og manglende in-race data):
     Recency-vægtet gennemsnitlig placering pr. rytter på DENNE etapetype
     i 2021-2025 GT-løb. Felt-normaliseret 0-100 inden for feltet.
     Beregnet af train_model.py → data/cache/rider_historical_form.json.
     Vægte: 2025=5 · 2024=3 · 2023=2 · 2022=1 · 2021=1

  2. Holdet LightGBM (fra etape 5+):
     Indlæser data/ml/holdet_model.lgbm og predikter normaliserede Holdet-point
     (0-100 skala) baseret på CO-ratings, PCS specialties, PCS form og
     in-race / cross-race rolling form. Trænet direkte på faktiske Holdet-point.

  3. Legacy LightGBM (fallback hvis holdet_model.lgbm mangler):
     Indlæser data/ml/model.lgbm (top-10 classifier). Felt-normaliseret 0-100.

Returnerer {rider_id: score 0-100}.
Returnerer tom dict hvis ingen model OG ingen historisk form er tilgængelig.
"""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT                  = Path(__file__).parent.parent
MODEL_PATH            = ROOT / "data" / "ml" / "model.lgbm"
HOLDET_MODEL_PATH     = ROOT / "data" / "ml" / "holdet_model.lgbm"
PLACEMENT_MODEL_PATH  = ROOT / "data" / "ml" / "placement_model.lgbm"
HIST_FORM_PATH        = ROOT / "data" / "cache" / "rider_historical_form.json"
HIST_RESULTS_PATH     = ROOT / "data" / "ml" / "historical_results.json"

# Holdet-model kan bruges fra etape 1 — CO/PCS form og xrace_form er
# tilgængelige fra dag 1. gt_form_5=-1 bruges som sentinel for manglende
# in-race data, og modellen er trænet til at håndtere dette.
MIN_HOLDET_STAGES = 0

# Legacy model (top-10 klassifikator) kræver minimalt 0 etaper — CO og
# PCS specialty fungerer fra starten. Legacy brugt som blending-partner.
MIN_LEGACY_STAGES = 0

# Holdet-model feature order — skal matche build_holdet_training_data.py
_HOLDET_FEATURE_COLS = [
    "is_sprint", "is_mountain", "is_hilly", "is_tt",
    "co_mtn", "co_spr", "co_hll", "co_itt", "co_cob", "co_gc",
    "spec_climber", "spec_sprint", "spec_tt", "spec_hills",
    "form_overall", "form_sprint", "form_mountain", "form_hilly", "form_tt",
    "gt_form_5", "gt_form_10", "gt_wins_so_far",
    "xrace_form_10",
]

# Legacy-model feature order
_FEATURE_COLS = [
    "profile_score",
    "is_sprint", "is_mountain", "is_hilly", "is_tt",
    "co_mtn", "co_spr", "co_hll", "co_itt", "co_cob", "co_gc",
    "spec_climber", "spec_sprint", "spec_tt", "spec_hills",
    "gt_form_5", "gt_form_10", "gt_wins_so_far",
    "startlist_quality",
]

_model = None
_model_loaded = False
_holdet_model = None
_holdet_model_loaded = False
_placement_model = None
_placement_model_loaded = False
_hist_form: dict[str, dict] | None = None
_hist_form_loaded = False
_xrace_form: dict[str, float] | None = None   # {pcs_slug: {stage_type: avg_pos}}
_xrace_form_loaded = False
_xrace_rates: dict | None = None              # {pcs_slug: {stage_type: {top3_rate, top10_rate}}}
_xrace_rates_loaded = False


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


def _get_holdet_model():
    global _holdet_model, _holdet_model_loaded
    if not _holdet_model_loaded:
        try:
            import lightgbm as lgb
            if HOLDET_MODEL_PATH.exists():
                _holdet_model = lgb.Booster(model_file=str(HOLDET_MODEL_PATH))
        except ImportError:
            pass
        _holdet_model_loaded = True
    return _holdet_model


def _get_placement_model():
    global _placement_model, _placement_model_loaded
    if not _placement_model_loaded:
        try:
            import lightgbm as lgb
            if PLACEMENT_MODEL_PATH.exists():
                _placement_model = lgb.Booster(model_file=str(PLACEMENT_MODEL_PATH))
        except ImportError:
            pass
        _placement_model_loaded = True
    return _placement_model


_RACE_ORDER: dict[str, int] = {
    "pn":        1,
    "tirreno":   2,
    "catalunya": 3,
    "basque":    4,
    "romandie":  5,
    "giro":      6,
    "dauphine":  7,
    "suisse":    8,
    "tdf":       9,
    "vuelta":   10,
}


def _get_xrace_form() -> dict[str, dict[str, float]]:
    """
    Lazy-load type-specifik cross-race form.
    Returnerer {pcs_slug: {stage_type: avg_position_last10}}.

    Kun etaper af SAMME type bruges — sprint-form til spurtetaper osv.
    Forhindrer at en sprinters nr. 150 på bjergetaper forurener hans sprintform.

    Bygger også prefix-aliaser: 'magnus-cort' → 'magnus-cort-nielsen'-data,
    så holdet-IDs der mangler efternavn stadig finder historik.
    """
    global _xrace_form, _xrace_form_loaded
    if not _xrace_form_loaded:
        _xrace_form = {}
        if HIST_RESULTS_PATH.exists():
            records = json.loads(HIST_RESULTS_PATH.read_text(encoding="utf-8"))
            records_sorted = sorted(
                records,
                key=lambda r: (r["year"], _RACE_ORDER.get(r["race"], 9), r["stage"])
            )
            # {slug: {stage_type: [positions]}}
            by_slug: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
            for r in records_sorted:
                if not r.get("dnf") and r.get("position") and r.get("stage_type") != "ttt":
                    stype = r.get("stage_type", "hilly")
                    by_slug[r["rider_slug"]][stype].append(r["position"])

            # Build result: {slug: {type: avg_last10}}
            for slug, type_positions in by_slug.items():
                _xrace_form[slug] = {
                    stype: round(sum(pos[-10:]) / len(pos[-10:]), 1)
                    for stype, pos in type_positions.items()
                }

            # Slug-aliaser: 'magnus-cort' finder 'magnus-cort-nielsen'-data.
            # For slugs med 3+ dele registreres kortere prefix-aliaser (hvis
            # prefixet ikke allerede er et selvstændigt slug i databasen).
            all_slugs = set(_xrace_form.keys())
            aliases: dict[str, str] = {}
            for slug in sorted(all_slugs):
                parts = slug.split("-")
                for n in range(len(parts) - 1, 1, -1):
                    prefix = "-".join(parts[:n])
                    if prefix not in all_slugs and prefix not in aliases:
                        aliases[prefix] = slug
            for alias, real in aliases.items():
                if alias not in _xrace_form:
                    _xrace_form[alias] = _xrace_form[real]

        _xrace_form_loaded = True
    return _xrace_form or {}


def _get_xrace_rates() -> dict[str, dict[str, dict[str, float]]]:
    """
    Lazy-load type-specifik top-N hitrate for cross-race form.
    Returnerer {pcs_slug: {stage_type: {top3_rate: float, top10_rate: float}}}.
    Seneste 10 etaper af SAMME type bruges.
    """
    global _xrace_rates, _xrace_rates_loaded
    if not _xrace_rates_loaded:
        _xrace_rates = {}
        if HIST_RESULTS_PATH.exists():
            records = json.loads(HIST_RESULTS_PATH.read_text(encoding="utf-8"))
            records_sorted = sorted(
                records,
                key=lambda r: (r["year"], _RACE_ORDER.get(r["race"], 9), r["stage"])
            )
            by_slug: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
            for r in records_sorted:
                if not r.get("dnf") and r.get("position") and r.get("stage_type") != "ttt":
                    stype = r.get("stage_type", "hilly")
                    by_slug[r["rider_slug"]][stype].append(r["position"])

            for slug, type_positions in by_slug.items():
                _xrace_rates[slug] = {}
                for stype, pos in type_positions.items():
                    last10 = pos[-10:]
                    n = len(last10)
                    _xrace_rates[slug][stype] = {
                        "top3_rate":  round(sum(1 for p in last10 if p <= 3) / n, 4),
                        "top10_rate": round(sum(1 for p in last10 if p <= 10) / n, 4),
                    }

            # Prefix-aliaser (samme logik som _get_xrace_form)
            all_slugs = set(_xrace_rates.keys())
            aliases: dict[str, str] = {}
            for slug in sorted(all_slugs):
                parts = slug.split("-")
                for n in range(len(parts) - 1, 1, -1):
                    prefix = "-".join(parts[:n])
                    if prefix not in all_slugs and prefix not in aliases:
                        aliases[prefix] = slug
            for alias, real in aliases.items():
                if alias not in _xrace_rates:
                    _xrace_rates[alias] = _xrace_rates[real]

        _xrace_rates_loaded = True
    return _xrace_rates or {}


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
    pcs_rankings: dict | None = None,   # {pcs_slug: {rank, pts, name}}
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

    # Blend med PCS 12-mdr. rangering (20% vægt) for at korrigere for ryttere
    # der ikke har nylige GT-resultater men er topklassede (f.eks. Pogacar).
    if pcs_rankings:
        field_pts = [
            pcs_rankings.get(_holdet_to_pcs_slug(r["id"], pcs_form_raw), {}).get("pts", 0)
            for r in riders
        ]
        nonzero = [p for p in field_pts if p > 0]
        if nonzero:
            sorted_f = sorted(nonzero)
            max_f = sorted_f[max(0, int(len(sorted_f) * 0.90) - 1)] or 1.0
            for rider in riders:
                rid  = rider["id"]
                slug = _holdet_to_pcs_slug(rid, pcs_form_raw)
                pts  = pcs_rankings.get(slug, {}).get("pts", 0)
                pcs_q = min(100.0, pts / max_f * 100.0) if pts > 0 else 25.0
                result[rid] = round(0.80 * result.get(rid, 50.0) + 0.20 * pcs_q, 1)
            # Re-normalise after blending
            vals = list(result.values())
            lo2, hi2 = min(vals), max(vals)
            if hi2 > lo2:
                result = {rid: round((v - lo2) / (hi2 - lo2) * 100.0, 1) for rid, v in result.items()}

    return result


def _lgbm_scores(
    riders: list[dict[str, Any]],
    stage_type: str,
    stage_num: int,
    profile_score: int | None,
    stages: dict[str, list[dict]],
    pcs_form_raw: dict | None,
    co_data: dict | None = None,
    pcs_specialty_data: dict | None = None,
    startlist_quality: float = 1.0,
) -> dict[str, float] | None:
    """
    Kør LightGBM-modellen med aktuel in-race rolling-form.
    CO og specialty features er aktive efter retraining med de fixede training data.
    co_data er keyed by holdet rider_id (underscore), samme som `rid`.
    pcs_specialty_data er keyed by holdet rider_id (underscore), samme som `rid`.
    startlist_quality: PCS startlist quality score / 1000 (0-1+ scale).
      Default 1.0 = top-quality field (e.g. TdF).
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

        co_raw = (co_data or {}).get(rid, {})
        co   = {k.lower(): v for k, v in co_raw.items()}
        spec = (pcs_specialty_data or {}).get(rid, {})
        row = [
            ps,
            is_sprint, is_mountain, is_hilly, is_tt,
            co.get("mtn", -1), co.get("spr", -1), co.get("hll", -1),
            co.get("itt", -1), co.get("cob", -1), co.get("gc",  -1),
            spec.get("climber", -1), spec.get("sprint", -1),
            spec.get("tt", -1), spec.get("hills", -1),
            gt_form_5, gt_form_10, float(gt_wins),
            float(startlist_quality),   # feature 19: PCS field quality / 1000
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


def _holdet_lgbm_scores(
    riders: list[dict[str, Any]],
    stage_type: str,
    stage_num: int,
    stages: dict[str, list[dict]],
    pcs_form_raw: dict | None,
    co_data: dict | None = None,
    pcs_specialty_data: dict | None = None,
) -> dict[str, float] | None:
    """
    Kør holdet_model.lgbm — predikter normaliserede Holdet-point (0-100 skala).
    Features: CO, PCS specialty, PCS form by type, in-race rolling form, xrace form.
    Returnerer None hvis model ikke er tilgængelig.
    """
    model = _get_holdet_model()
    if model is None:
        return None

    try:
        import numpy as np
    except ImportError:
        return None

    is_sprint   = int(stage_type == "sprint")
    is_mountain = int(stage_type == "mountain")
    is_hilly    = int(stage_type == "hilly")
    is_tt       = int(stage_type in ("tt", "ttt"))

    xrace_cache = _get_xrace_form()

    rows: list[list[float]] = []
    rider_ids: list[str]    = []

    for rider in riders:
        rid  = rider["id"]
        slug = _holdet_to_pcs_slug(rid, pcs_form_raw)

        gt_form_5, gt_form_10, gt_wins = _rolling_form(slug, stages, stage_num)
        xrace_form_10 = xrace_cache.get(slug, {}).get(stage_type, float("nan"))

        co_raw = (co_data or {}).get(rid, {})
        co   = {k.lower(): v for k, v in co_raw.items()}
        spec = (pcs_specialty_data or {}).get(rid, {})

        pcs_entry = (pcs_form_raw or {}).get(rid, {})
        fbt = pcs_entry.get("form_by_type") or {}
        form_overall  = fbt.get("overall",  -1)
        form_sprint   = fbt.get("sprint",   -1)
        form_mountain = fbt.get("mountain", -1)
        form_hilly    = fbt.get("hilly",    -1)
        form_tt       = fbt.get("tt",       -1)

        row = [
            is_sprint, is_mountain, is_hilly, is_tt,
            co.get("mtn", -1), co.get("spr", -1), co.get("hll", -1),
            co.get("itt", -1), co.get("cob", -1), co.get("gc",  -1),
            spec.get("climber", -1), spec.get("sprint", -1),
            spec.get("tt", -1), spec.get("hills", -1),
            float(form_overall), float(form_sprint), float(form_mountain),
            float(form_hilly), float(form_tt),
            gt_form_5, gt_form_10, float(gt_wins),
            xrace_form_10,
        ]
        rows.append(row)
        rider_ids.append(rid)

    if not rows:
        return None

    X    = np.array(rows, dtype=np.float32)
    preds = model.predict(X)   # predikteret holdet_pts_norm 0-100

    # Normalize within field so the signal is always on 0-100 scale
    lo, hi = float(preds.min()), float(preds.max())
    if hi > lo:
        scores = [(float(p) - lo) / (hi - lo) * 100.0 for p in preds]
    else:
        scores = [50.0] * len(preds)

    return {rid: round(s, 1) for rid, s in zip(rider_ids, scores)}


def _holdet_lgbm_raw_preds(
    riders: list[dict[Any, Any]],
    stage_type: str,
    stage_num: int,
    stages: dict[str, list[dict]],
    pcs_form_raw: dict | None,
    co_data: dict | None = None,
    pcs_specialty_data: dict | None = None,
) -> dict[str, float | None] | None:
    """
    Kør holdet_model.lgbm og returner RAW predictions (0-100 skala, IKKE
    felt-normaliseret). 0=ingen point, 100=vinder af etapen.
    Bruges til at beregne konkrete K-point-estimater via AVG_MAX_K.
    Returnerer None hvis model ikke er tilgængelig.
    """
    model = _get_holdet_model()
    if model is None:
        return None

    try:
        import numpy as np
    except ImportError:
        return None

    is_sprint   = int(stage_type == "sprint")
    is_mountain = int(stage_type == "mountain")
    is_hilly    = int(stage_type == "hilly")
    is_tt       = int(stage_type in ("tt", "ttt"))

    xrace_cache = _get_xrace_form()

    rows: list[list[float]] = []
    rider_ids: list[str]    = []
    no_signal_set: set[str] = set()  # riders with no CO/PCS data

    for rider in riders:
        rid  = rider["id"]
        slug = _holdet_to_pcs_slug(rid, pcs_form_raw)

        gt_form_5, gt_form_10, gt_wins = _rolling_form(slug, stages, stage_num)
        xrace_form_10 = xrace_cache.get(slug, {}).get(stage_type, float("nan"))

        co_raw = (co_data or {}).get(rid, {})
        co   = {k.lower(): v for k, v in co_raw.items()}
        spec = (pcs_specialty_data or {}).get(rid, {})

        pcs_entry = (pcs_form_raw or {}).get(rid, {})
        fbt = pcs_entry.get("form_by_type") or {}
        form_overall  = fbt.get("overall",  -1)
        form_sprint   = fbt.get("sprint",   -1)
        form_mountain = fbt.get("mountain", -1)
        form_hilly    = fbt.get("hilly",    -1)
        form_tt       = fbt.get("tt",       -1)

        has_co   = bool(co)
        has_spec = bool(spec)
        has_form = any(v != -1 for v in [form_overall, form_sprint, form_mountain, form_hilly, form_tt])
        if not has_co and not has_spec and not has_form:
            no_signal_set.add(rid)

        row = [
            is_sprint, is_mountain, is_hilly, is_tt,
            co.get("mtn", -1), co.get("spr", -1), co.get("hll", -1),
            co.get("itt", -1), co.get("cob", -1), co.get("gc",  -1),
            spec.get("climber", -1), spec.get("sprint", -1),
            spec.get("tt", -1), spec.get("hills", -1),
            float(form_overall), float(form_sprint), float(form_mountain),
            float(form_hilly), float(form_tt),
            gt_form_5, gt_form_10, float(gt_wins),
            xrace_form_10,
        ]
        rows.append(row)
        rider_ids.append(rid)

    if not rows:
        return None

    X    = np.array(rows, dtype=np.float32)
    raw  = model.predict(X)   # raw 0-100 (ikke felt-normaliseret)

    result: dict[str, float] = {}
    for rid, p in zip(rider_ids, raw):
        if rid in no_signal_set:
            # No CO, no PCS specialty, no PCS form → rank-based fallback in predictor
            result[rid] = None  # type: ignore[assignment]
        else:
            result[rid] = round(max(0.0, float(p)), 2)
    return result


def compute_ml_scores(
    riders: list[dict[str, Any]],
    stage_type: str,
    stage_num: int,
    profile_score: int | None,
    gt_results: dict | None,
    pcs_form_raw: dict | None = None,
    pcs_rankings: dict | None = None,   # {pcs_slug: {rank, pts}}
    co_data: dict | None = None,
    pcs_specialty_data: dict | None = None,
    startlist_quality: float = 1.0,
) -> dict[str, float]:
    """
    Beregn ML/historisk styrke-scorer for alle ryttere (felt-normaliseret 0-100).

    Strategi — fase-baseret blending:
    - Etape 1-4  (n_done < 5):  20% holdet + 80% legacy  (begge mangler in-race form,
      men CO/PCS specialty/xrace_form bruges fra dag 1).
    - Etape 5-10 (n_done < 11): 55% holdet + 45% legacy  (holdet begynder at lære
      rytterens in-race form).
    - Etape 11+  (n_done >= 11): 70% holdet + 30% legacy  (holdet model har fuld form-
      historik og vægter tungere).

    Returnerer {rider_id: score 0-100} — felt-normaliseret.
    """
    stages = (gt_results or {}).get("stages", {})
    n_done = sum(1 for s in stages if int(s) < stage_num)

    # Phase-based blend weights (holdet_w + legacy_w = 1.0)
    if n_done < 5:
        holdet_w, legacy_w = 0.20, 0.80
    elif n_done < 11:
        holdet_w, legacy_w = 0.55, 0.45
    else:
        holdet_w, legacy_w = 0.70, 0.30

    holdet = _holdet_lgbm_scores(
        riders, stage_type, stage_num, stages, pcs_form_raw,
        co_data=co_data, pcs_specialty_data=pcs_specialty_data,
    )
    lgbm = _lgbm_scores(
        riders, stage_type, stage_num, profile_score, stages, pcs_form_raw,
        co_data=co_data, pcs_specialty_data=pcs_specialty_data,
        startlist_quality=startlist_quality,
    )

    if holdet is not None and lgbm is not None:
        return {
            rid: round(holdet_w * holdet.get(rid, 50.0) + legacy_w * lgbm.get(rid, 50.0), 1)
            for rid in holdet
        }
    if holdet is not None:
        return holdet
    if lgbm is not None:
        return lgbm

    # Fallback: historisk styrke (modeller ikke tilgængelige)
    hist = _historical_strength_scores(riders, stage_type, pcs_form_raw, pcs_rankings)
    if hist:
        return hist

    return {}


def _placement_lgbm_raw_preds(
    riders: list[dict[Any, Any]],
    stage_type: str,
    stage_num: int,
    stages: dict[str, list[dict]],
    pcs_form_raw: dict | None,
    co_data: dict | None = None,
    pcs_specialty_data: dict | None = None,
    startlist_quality: float = 1.0,
) -> dict[str, float | None] | None:
    """
    Kør placement_model.lgbm og returner RAW norm_pos predictions (0-1 skala).
    1.0 = model forudsiger rytteren vinder, 0.0 = sidst.
    TTT-etaper understøttes IKKE (returnerer None).
    Returnerer None hvis model ikke er tilgængelig.
    """
    if stage_type == "ttt":
        return None

    model = _get_placement_model()
    if model is None:
        return None

    try:
        import numpy as np
    except ImportError:
        return None

    is_sprint   = int(stage_type == "sprint")
    is_mountain = int(stage_type == "mountain")
    is_hilly    = int(stage_type == "hilly")
    is_tt       = int(stage_type == "tt")

    xrace_cache = _get_xrace_form()
    rates_cache = _get_xrace_rates()
    qual_norm   = min(2.0, startlist_quality / 1000.0)  # PCS 0-1000 → 0-1

    rows: list[list[float]] = []
    rider_ids: list[str]    = []
    no_signal_set: set[str] = set()

    for rider in riders:
        rid  = rider["id"]
        slug = _holdet_to_pcs_slug(rid, pcs_form_raw)

        gt_form_5, gt_form_10, gt_wins = _rolling_form(slug, stages, stage_num)
        xrace_form_10 = xrace_cache.get(slug, {}).get(stage_type, float("nan"))
        rates_data = rates_cache.get(slug, {}).get(stage_type, {})
        xrace_top3_rate_10  = rates_data.get("top3_rate",  float("nan"))
        xrace_top10_rate_10 = rates_data.get("top10_rate", float("nan"))

        co_raw = (co_data or {}).get(rid, {})
        co     = {k.lower(): v for k, v in co_raw.items()}
        spec   = (pcs_specialty_data or {}).get(rid, {})

        pcs_entry = (pcs_form_raw or {}).get(rid, {})
        fbt = pcs_entry.get("form_by_type") or {}
        form_overall  = fbt.get("overall",  -1)
        form_sprint   = fbt.get("sprint",   -1)
        form_mountain = fbt.get("mountain", -1)
        form_hilly    = fbt.get("hilly",    -1)
        form_tt       = fbt.get("tt",       -1)

        has_co   = bool(co)
        has_spec = bool(spec)
        has_form = any(v != -1 for v in [form_overall, form_sprint, form_mountain, form_hilly, form_tt])
        if not has_co and not has_spec and not has_form:
            no_signal_set.add(rid)

        row = [
            is_sprint, is_mountain, is_hilly, is_tt,
            co.get("mtn", -1), co.get("spr", -1), co.get("hll", -1),
            co.get("itt", -1), co.get("cob", -1), co.get("gc",  -1),
            spec.get("climber", -1), spec.get("sprint", -1),
            spec.get("tt", -1), spec.get("hills", -1),
            float(form_overall), float(form_sprint), float(form_mountain),
            float(form_hilly), float(form_tt),
            gt_form_5, gt_form_10, float(gt_wins),
            xrace_form_10,
            xrace_top3_rate_10,
            xrace_top10_rate_10,
            qual_norm,
        ]
        rows.append(row)
        rider_ids.append(rid)

    if not rows:
        return None

    X   = np.array(rows, dtype=np.float32)
    raw = model.predict(X)   # raw norm_pos 0-1

    result: dict[str, float] = {}
    for rid, p in zip(rider_ids, raw):
        if rid in no_signal_set:
            result[rid] = None  # type: ignore[assignment]
        else:
            result[rid] = round(max(0.0, min(1.0, float(p))), 4)
    return result


def compute_placement_scores(
    riders: list[dict[Any, Any]],
    stage_type: str,
    stage_num: int,
    gt_results: dict | None,
    pcs_form_raw: dict | None = None,
    co_data: dict | None = None,
    pcs_specialty_data: dict | None = None,
    startlist_quality: float = 1000.0,
) -> dict[str, float] | None:
    """
    Returner raw placement model predictions {rider_id: norm_pos (0-1)}.
    1.0=vinder, 0.0=sidst. None for ryttere uden CO/PCS-signal.
    Returnerer None hvis placement_model.lgbm ikke er indlæst.
    """
    stages = (gt_results or {}).get("stages", {})
    return _placement_lgbm_raw_preds(
        riders, stage_type, stage_num, stages, pcs_form_raw,
        co_data=co_data, pcs_specialty_data=pcs_specialty_data,
        startlist_quality=startlist_quality,
    )


def compute_holdet_raw_scores(
    riders: list[dict[str, Any]],
    stage_type: str,
    stage_num: int,
    gt_results: dict | None,
    pcs_form_raw: dict | None = None,
    co_data: dict | None = None,
    pcs_specialty_data: dict | None = None,
) -> dict[str, float] | None:
    """
    Returner RAW holdet-model predictions (0-100 skala, IKKE felt-normaliseret).
    0 = ingen point, 100 = vinder af etapen.

    Denormalisering: predicted_K = score / 100 × AVG_MAX_K[stage_type]
    AVG_MAX_K (gennemsnitlige maksimumpoint pr. etapetype, fra historik):
      sprint=400K, mountain=427K, hilly=430K, tt=399K

    Returnerer None hvis holdet-model ikke er indlæst.
    """
    stages = (gt_results or {}).get("stages", {})
    return _holdet_lgbm_raw_preds(
        riders, stage_type, stage_num, stages, pcs_form_raw,
        co_data=co_data, pcs_specialty_data=pcs_specialty_data,
    )
