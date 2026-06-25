"""
Retrooptimizer: evaluate model quality on Giro 2026 data.

For each stage where both VeloScore predictions AND actual results exist:
  1. Oracle team  — MILP using actual fantasy points (hindsight-optimal team)
  2. Model team   — MILP using predicted points from VeloScore + predictor.py
  3. Efficiency   — actual pts scored by model team ÷ actual pts by oracle team

Run:
    python retro_optimizer.py

Output:
    data/retro_report.json   (machine-readable)
    terminal table           (human-readable summary)

Notes:
  - Prices are TdF 2026 approximations; actual Giro 2026 prices unavailable.
    Oracle and model teams are therefore approximate — mainly useful for
    comparing relative model quality stage by stage.
  - Stage numbers are matched directly (VS stage N ↔ results stage N).
    Type mismatches (e.g. VeloScore predicted sprint, actual was mountain)
    are flagged with ✗ and will naturally show low efficiency.
  - Riders not in the top results of a stage score 0 pts in the evaluation.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from src.predictor import predict_all
from src.optimizer import _solve, BUDGET   # _solve is the core LP (private by convention only)

DATA = ROOT / "data"


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _norm(name: str) -> str:
    return name.lower().strip()

def _last(name: str) -> str:
    return _norm(name).split()[-1]

def _lookup_pts(full_name: str, pts_map: dict[str, int]) -> int:
    """Return actual fantasy pts for a rider, trying full name then last name."""
    nl = _norm(full_name)
    if nl in pts_map:
        return pts_map[nl]
    last = _last(full_name)
    for k, v in pts_map.items():
        if _last(k) == last:
            return v
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Data loaders
# ─────────────────────────────────────────────────────────────────────────────

def load_results() -> dict[int, dict]:
    """
    Returns {stage_num: {stage_type, note, pts_map, top_results}}
    Stages with missing results (e.g. stage 12) are excluded.
    """
    raw = json.loads((DATA / "giro2026" / "results.json").read_text(encoding="utf-8"))
    out: dict[int, dict] = {}
    for s in raw:
        top = s.get("top_results")
        if not top:
            continue
        pts_map: dict[str, int] = {}
        for r in top:
            pts = r.get("points")
            if pts is not None:
                pts_map[_norm(r["rider"])] = int(pts)
        out[s["stage"]] = {
            "stage_type":  s["stage_type"],
            "note":        s.get("note", ""),
            "pts_map":     pts_map,
            "top_results": top,
        }
    return out


def load_veloscore() -> dict[int, dict]:
    """Returns {stage_num: {stage_type, predictions}}"""
    raw = json.loads((DATA / "giro2026" / "veloscore.json").read_text(encoding="utf-8"))
    return {s["stage"]: s for s in raw}


# ─────────────────────────────────────────────────────────────────────────────
# Oracle team builder
# ─────────────────────────────────────────────────────────────────────────────

def build_oracle_preds(riders: list[dict], pts_map: dict[str, int]) -> list[dict]:
    """
    Build a predictions list where expected_pts = actual fantasy pts.
    Riders not in pts_map get 0 pts (the MILP will correctly not pick them).
    """
    out = []
    for r in riders:
        pts = _lookup_pts(r["full_name"], pts_map)
        out.append({
            "rider_id":    r["id"],
            "full_name":   r["full_name"],
            "team":        r["team"],
            "price":       r["price"],
            "expected_pts": pts,
            "variance":    0,
        })
    # Sort descending so _solve's greedy fallback picks the best first
    out.sort(key=lambda x: x["expected_pts"], reverse=True)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Score evaluator
# ─────────────────────────────────────────────────────────────────────────────

def score_team(team_result: dict, pts_map: dict[str, int]) -> dict:
    """
    Evaluate a team using ACTUAL fantasy points.

    The captain's points are doubled (base counted once + captain bonus once).
    Returns a summary dict.
    """
    base  = 0
    cap   = 0
    scored = 0
    top15  = 0

    for r in team_result["team"]:
        pts = _lookup_pts(r["full_name"], pts_map)
        base += pts
        if pts > 0:
            scored += 1
        if pts >= 100_000:      # proxy for top-15 finish
            top15 += 1
        if r.get("is_captain"):
            cap = pts

    return {
        "base_pts":      base,
        "captain_pts":   cap,       # counted once extra = doubling effect
        "total_pts":     base + cap,
        "riders_scored": scored,
        "riders_top15":  top15,
    }


def team_rows(team_result: dict, pts_map: dict[str, int]) -> list[dict]:
    """Return per-rider breakdown with both predicted and actual pts."""
    rows = []
    for r in sorted(team_result["team"], key=lambda x: x["expected_pts"], reverse=True):
        rows.append({
            "name":        r["full_name"],
            "team":        r["team"],
            "price_M":     r["price"],
            "is_captain":  r["is_captain"],
            "pred_pts":    r["expected_pts"],
            "actual_pts":  _lookup_pts(r["full_name"], pts_map),
        })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Per-stage analysis
# ─────────────────────────────────────────────────────────────────────────────

def analyze_stage(
    stage_num: int,
    res: dict,
    vs: dict,
    riders: list[dict],
    weights: dict,
    co_data: dict | None = None,
    pcs_form_data: dict | None = None,
) -> dict:
    pts_map       = res["pts_map"]
    vs_type       = vs["stage_type"]
    actual_type   = res["stage_type"]
    type_match    = (vs_type == actual_type)

    # ── Oracle team: MILP maximises actual pts ────────────────
    oracle_preds  = build_oracle_preds(riders, pts_map)
    oracle_result = _solve(oracle_preds, budget=BUDGET, label="oracle")
    if oracle_result:
        oracle_sc    = score_team(oracle_result, pts_map)
        oracle_rows  = team_rows(oracle_result, pts_map)
    else:
        oracle_sc    = {"total_pts": 0, "riders_scored": 0, "riders_top15": 0}
        oracle_rows  = []

    # ── Model team: VeloScore + CyclingOracle + PCS form → MILP ─
    model_preds  = predict_all(
        riders=riders,
        stage_type=vs_type,           # use VS stage type (what model knew at the time)
        veloscore_data=vs["predictions"],
        cyclingoracle_data=co_data,   # discipline ratings (career stats, so applicable historically)
        pcs_form_data=pcs_form_data,  # recent form (note: uses 2026 form for historical stages)
        weights=weights,
    )
    model_result = _solve(model_preds, budget=BUDGET, label="model")
    if model_result:
        model_sc     = score_team(model_result, pts_map)
        model_rows   = team_rows(model_result, pts_map)
        model_pred_total = model_result["expected_pts"]
    else:
        model_sc     = {"total_pts": 0, "riders_scored": 0, "riders_top15": 0}
        model_rows   = []
        model_pred_total = 0

    # ── Efficiency ────────────────────────────────────────────
    oracle_pts = oracle_sc["total_pts"]
    model_pts  = model_sc["total_pts"]
    efficiency = round(model_pts / oracle_pts, 4) if oracle_pts > 0 else None

    # ── Did the model team include the actual stage winner? ───
    winner_name = next(
        (r["rider"] for r in res.get("top_results", []) if r.get("rank") == 1),
        "",
    )
    winner_in_model = (
        any(_last(winner_name) in _norm(r["name"]) for r in model_rows)
        if winner_name else False
    )

    return {
        "stage":              stage_num,
        "vs_type":            vs_type,
        "actual_type":        actual_type,
        "type_match":         type_match,
        "note":               res["note"],
        "winner":             winner_name,
        "winner_in_model":    winner_in_model,
        # Oracle
        "oracle_pts":         oracle_pts,
        "oracle_riders_scored": oracle_sc.get("riders_scored", 0),
        "oracle_team":        oracle_rows,
        # Model
        "model_pred_pts":     model_pred_total,
        "model_actual_pts":   model_pts,
        "model_riders_scored": model_sc.get("riders_scored", 0),
        "model_riders_top15":  model_sc.get("riders_top15", 0),
        "model_team":         model_rows,
        # Headline metric
        "efficiency":         efficiency,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Retrooptimizer -- Giro 2026")
    parser.add_argument(
        "--with-pcs-form", action="store_true",
        help=(
            "Include PCS form data (WARNING: uses current 2026 form, "
            "which leaks future information into early-Giro stages. "
            "Efficiency will be inflated (~+10pp). Omit for clean evaluation."
        ),
    )
    args = parser.parse_args()

    print("-" * 60)
    print("  Retrooptimizer -- Giro 2026")
    print("-" * 60)
    print("  Indlæser data…")

    riders  = json.loads((DATA / "riders.json").read_text(encoding="utf-8"))
    weights = json.loads((DATA / "calibrated_weights.json").read_text(encoding="utf-8"))
    results = load_results()
    vs_data = load_veloscore()

    # Load CyclingOracle discipline ratings if available
    co_path = DATA / "cache" / "cyclingoracle.json"
    co_data = json.loads(co_path.read_text(encoding="utf-8")) if co_path.exists() else {}
    print(f"  CyclingOracle: {len(co_data)} ryttere i cache")

    # PCS form data — excluded by default to avoid future leakage.
    # The cache contains form computed from 2026-06-01; historical Giro stages
    # from early May would never have had access to this data. Pass
    # --with-pcs-form to include it (useful to measure upper-bound impact).
    if args.with_pcs_form:
        pcs_path = DATA / "cache" / "pcs_form.json"
        if pcs_path.exists():
            pcs_raw  = json.loads(pcs_path.read_text(encoding="utf-8"))
            pcs_form: dict | None = {}
            for rid, v in pcs_raw.items():
                if v.get("not_found"):
                    continue
                if "form_by_type" in v:
                    pcs_form[rid] = v["form_by_type"]
                else:
                    pcs_form[rid] = {"overall": v.get("form_score", 50.0)}
        else:
            pcs_form = None
        n_pcs = len(pcs_form) if pcs_form else 0
        print(f"  PCS form:      {n_pcs} ryttere (ADVARSEL: fremtidig lækage!)")
    else:
        pcs_form = None
        print(f"  PCS form:      udeladt (brug --with-pcs-form for at inkludere)")

    matched = sorted(set(results) & set(vs_data))
    print(f"  Matchede etaper: {matched}  ({len(matched)} stk.)\n")

    stages_out = []
    for sn in matched:
        print(f"  Analyserer etape {sn:>2}…", end=" ", flush=True)
        try:
            s = analyze_stage(sn, results[sn], vs_data[sn], riders, weights, co_data,
                               pcs_form_data=pcs_form)
            stages_out.append(s)
            eff = f"{s['efficiency']*100:.1f}%" if s["efficiency"] is not None else " N/A"
            tm  = "J" if s["type_match"] else "N"
            print(f"{tm}  model={s['model_actual_pts']:>10,.0f}  "
                  f"oracle={s['oracle_pts']:>10,.0f}  eff={eff}")
        except Exception as exc:
            print(f"FEJL: {exc}")
            import traceback; traceback.print_exc()

    # ── Summary statistics ────────────────────────────────────
    valid     = [s for s in stages_out if s["efficiency"] is not None]
    matched_t = [s for s in valid if s["type_match"]]

    avg_eff_all   = (sum(s["efficiency"] for s in valid) / len(valid)) if valid else 0
    avg_eff_match = (sum(s["efficiency"] for s in matched_t) / len(matched_t)) if matched_t else 0
    winner_rate   = (sum(1 for s in valid if s["winner_in_model"]) / len(valid)) if valid else 0
    avg_scored    = (sum(s["model_riders_scored"] for s in valid) / len(valid)) if valid else 0
    avg_top15     = (sum(s["model_riders_top15"] for s in valid) / len(valid)) if valid else 0

    summary = {
        "n_stages_analyzed":      len(stages_out),
        "n_type_matched":         len(matched_t),
        "n_type_mismatched":      len(valid) - len(matched_t),
        "avg_efficiency_all":     round(avg_eff_all,   4),
        "avg_efficiency_matched": round(avg_eff_match, 4),
        "winner_in_model_rate":   round(winner_rate,   4),
        "avg_riders_scored":      round(avg_scored, 1),
        "avg_riders_top15":       round(avg_top15, 1),
        "caveat": (
            "Priser er TdF 2026-tilnærmelser (Giro 2026-priser utilgængelige). "
            "Efficiency-tal er vejledende, ikke absolutte. "
            "Stage-numre matches direkte (VS nr. N ↔ resultater nr. N); "
            "type-mismatch (✗) afspejler fejlforudsagte etapetyper."
        ),
    }

    output = {"summary": summary, "stages": stages_out}
    out_path = DATA / "retro_report.json"
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ── Pretty-print table ────────────────────────────────────
    print()
    hdr = (f"{'Etape':>6}  {'VS-type':>8}  {'Akt-type':>8}  {'M':>1}  "
           f"{'Model pts':>12}  {'Oracle pts':>12}  {'Eff%':>7}  {'Vinder?':>8}")
    print(hdr)
    print("-" * len(hdr))
    for s in stages_out:
        tm  = "J" if s["type_match"]      else "N"
        w   = "*" if s["winner_in_model"] else " "
        eff = f"{s['efficiency']*100:.1f}%" if s["efficiency"] is not None else "  N/A"
        print(f"{s['stage']:>6}  {s['vs_type']:>8}  {s['actual_type']:>8}  {tm}  "
              f"{s['model_actual_pts']:>12,.0f}  {s['oracle_pts']:>12,.0f}  "
              f"{eff:>7}  {w:>8}")
    print("-" * len(hdr))
    print(f"  Gns. efficiency (alle):           {avg_eff_all   * 100:.1f}%")
    print(f"  Gns. efficiency (type-match):     {avg_eff_match * 100:.1f}%")
    print(f"  Vinder i modelholdets hold:        {winner_rate   * 100:.1f}%")
    print(f"  Gns. ryttere der scorede (model):  {avg_scored:.1f} / 8")
    print(f"  Gns. ryttere i top15 (model):      {avg_top15:.1f} / 8")
    print()
    print(f"  Rapport gemt til {out_path}")
    print()


if __name__ == "__main__":
    main()
