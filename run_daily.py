"""
Daily workflow entry point.

Usage:
    python run_daily.py --stage 5 --type sprint --veloscore data/stage_05_veloscore.json

Or interactively (prompts for missing parameters):
    python run_daily.py

Current team:
    Edit  data/current_team.json  with your 8 holdet.dk riders + bank balance
    before running. The 3 recommendations will account for transfer costs.

Output:
    web/data/recommendations.json  — read by GitHub Pages frontend
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from src.predictor import predict_all
from src.optimizer import make_three_teams, make_best_team
from src.strategy  import get_strategy, describe as describe_strategy
from scrape_holdet import CARTRIDGE_TO_PCS_RACE, DEFAULT_CARTRIDGE

DATA_DIR = ROOT / "data"
WEB_DATA = ROOT / "web" / "data"
WEB_DATA.mkdir(parents=True, exist_ok=True)


STAGE_TYPES = ["sprint", "mountain", "tt", "ttt", "hilly", "cobbled"]


def load_riders() -> list[dict]:
    return json.loads((DATA_DIR / "riders.json").read_text(encoding="utf-8"))


def load_veloscore(path: Path | None, stage: int) -> list[dict]:
    """
    Load VeloScore predictions for a specific stage.

    Accepts two file formats:
      • Single-stage: {"stage": N, "predictions": [...]}   (per-stage files)
      • Multi-stage:  [{"stage": 1, ...}, {"stage": 2, ...}]  (combined file)
    """
    def _extract(data, stage_num: int) -> list[dict]:
        if isinstance(data, list):
            # Multi-stage file — find the matching stage entry
            for entry in data:
                if entry.get("stage") == stage_num:
                    return entry.get("predictions", [])
            return []
        # Single-stage dict
        return data.get("predictions", [])

    if path and path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return _extract(data, stage)
    auto = DATA_DIR / f"stage_{stage:02d}_veloscore.json"
    if auto.exists():
        data = json.loads(auto.read_text(encoding="utf-8"))
        return _extract(data, stage)
    print(f"  [WARNING] No VeloScore data found for stage {stage}. Proceeding without it.")
    return []


def load_cyclingoracle() -> dict:
    co_path = DATA_DIR / "cache" / "cyclingoracle.json"
    if co_path.exists():
        raw = json.loads(co_path.read_text(encoding="utf-8"))
        result = {}
        for name, data in raw.items():
            result[name] = data.get("ratings", {})
        return result
    return {}


def load_pcs_form() -> dict[str, dict]:
    """
    Load PCS form scores from data/cache/pcs_form.json.

    Returns {rider_id: form_by_type_dict} where form_by_type_dict has keys:
      "overall", "sprint", "mountain", "hilly", "tt", "cobbled"  (all 0-100)

    Riders not in cache get no entry; predictor.py uses 50.0 as fallback.
    Old-format entries (with only "form_score") are wrapped transparently.
    """
    pcs_path = DATA_DIR / "cache" / "pcs_form.json"
    if not pcs_path.exists():
        return {}
    raw = json.loads(pcs_path.read_text(encoding="utf-8"))
    result = {}
    for rider_id, entry in raw.items():
        if entry.get("not_found"):
            continue
        if "form_by_type" in entry:
            result[rider_id] = entry["form_by_type"]
        else:
            # backward compat: old cache without per-type breakdown
            result[rider_id] = {"overall": entry.get("form_score", 50.0)}
    return result


def load_gc_standings() -> dict[str, int]:
    """Load GC standings from cache. Returns {rider_id: gc_rank}."""
    path = DATA_DIR / "cache" / "gc_standings.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_jerseys() -> dict[str, list[str]]:
    """Load jersey leaders from cache. Returns {rider_id: [jersey_codes]}."""
    path = DATA_DIR / "cache" / "jerseys.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_sprint_kom() -> dict[str, dict]:
    """Load sprint/KOM classification standings. Returns {rider_id: {sprint_rank, kom_rank, ...}}."""
    path = DATA_DIR / "cache" / "holdet_sprint_kom.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_team_bonus() -> dict[str, int]:
    """Load expected team bonus per rider. Returns {rider_id: expected_kr}."""
    path = DATA_DIR / "cache" / "holdet_team_bonus.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_gt_stage_results() -> dict | None:
    """Load accumulated GT stage results. Returns None if not available."""
    path = DATA_DIR / "cache" / "gt_stage_results.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_pcs_form_raw() -> dict:
    """Load raw pcs_form.json (for PCS slug extraction in ML signal)."""
    path = DATA_DIR / "cache" / "pcs_form.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_pcs_rankings() -> dict:
    """
    Load PCS 12-month individual ranking.
    Returns {pcs_slug: {rank, pts, name}} or {}.
    Maps back to rider_id using pcs_form.json pcs_url.
    """
    path = DATA_DIR / "cache" / "pcs_rankings.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_pcs_rank_by_rider(rankings: dict, pcs_form_raw: dict) -> tuple[dict, dict]:
    """
    Convert {pcs_slug: {rank, pts}} to:
      {rider_id: pts}       — for predict_all() field-normalization
      {rider_id: n_results} — for sparse data protection
    using pcs_form.json to map rider_id → pcs_slug.
    """
    pts_by_rider: dict[str, float] = {}
    n_results_by_rider: dict[str, int] = {}
    for rider_id, entry in pcs_form_raw.items():
        pcs_url = entry.get("pcs_url", "")
        if pcs_url and "/rider/" in pcs_url:
            slug = pcs_url.split("/rider/")[-1].strip("/")
            rank_entry = rankings.get(slug, {})
            if rank_entry.get("pts", 0) > 0:
                pts_by_rider[rider_id] = float(rank_entry["pts"])
        n_results_by_rider[rider_id] = entry.get("n_results", 0)
    return pts_by_rider, n_results_by_rider


def load_profile_score(stage: int) -> int | None:
    """
    Load PCS ProfileScore for a specific stage of the current race.
    Returns None if not available.
    """
    path = DATA_DIR / "cache" / "pcs_profile_scores.json"
    if not path.exists():
        return None
    cache     = json.loads(path.read_text(encoding="utf-8"))
    pcs_race  = CARTRIDGE_TO_PCS_RACE.get(DEFAULT_CARTRIDGE, "")
    scores    = cache.get(pcs_race + "_scores", {})
    return scores.get(stage) or scores.get(str(stage))


def load_current_team(predictions: list[dict]) -> dict | None:
    """
    Load the user's current holdet.dk team from data/current_team.json.
    Matches rider names (case-insensitive, with last-name fallback) to
    rider IDs from the predictions list.

    Expected JSON format:
      {
        "bank_M": 5.2,
        "riders": ["Jonas Vingegaard", "Jonathan Milan", ...]
      }
    """
    path = DATA_DIR / "current_team.json"
    if not path.exists():
        print("  [INFO] Ingen current_team.json — springer nuværende hold over")
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [WARNING] Kunne ikke læse current_team.json: {e}")
        return None

    rider_names = raw.get("riders", [])
    bank_M      = float(raw.get("bank_M", 0.0))

    if not rider_names:
        print("  [WARNING] current_team.json har ingen ryttere")
        return None

    # Build lookup maps from predictions
    preds_by_full  = {p["full_name"].lower(): p for p in predictions}
    preds_by_short = {p.get("short_name", "").lower(): p for p in predictions}

    rider_ids       = []
    matched_riders  = []
    unmatched       = []

    for name in rider_names:
        nl   = name.lower().strip()
        pred = preds_by_full.get(nl) or preds_by_short.get(nl)

        if not pred:
            # Try last-name match
            last = nl.split()[-1]
            for p in predictions:
                if last in p["full_name"].lower():
                    pred = p
                    break

        if pred:
            rider_ids.append(pred["rider_id"])
            matched_riders.append({
                "rider_id":  pred["rider_id"],
                "full_name": pred["full_name"],
                "team":      pred.get("team", ""),
                "price_M":   pred["price"],
            })
        else:
            unmatched.append(name)

    if unmatched:
        print(f"  [WARNING] Følgende ryttere fra current_team.json kunne ikke matches: "
              f"{', '.join(unmatched)}")

    n_matched = len(rider_ids)
    print(f"  Nuværende hold: {n_matched}/{len(rider_names)} ryttere matchet, "
          f"bank: {bank_M:.1f}M kr")

    if n_matched < 4:
        print("  [WARNING] For få matchede ryttere — ignorerer current_team")
        return None

    return {
        "bank_M":          bank_M,
        "rider_ids":       rider_ids,
        "rider_names":     rider_names,
        "matched_riders":  matched_riders,
    }


def main():
    parser = argparse.ArgumentParser(description="TdF Manager — daily team optimizer")
    parser.add_argument("--stage",     type=int,  help="Stage number (e.g. 5)")
    parser.add_argument("--type",      type=str,  choices=STAGE_TYPES, help="Stage type")
    parser.add_argument("--veloscore", type=str,  help="Path to VeloScore JSON file")
    parser.add_argument("--budget",    type=float, default=None,
                        help="Override available budget in millions (default: use current_team.json)")
    parser.add_argument("--force-top", type=int, default=None,
                        help="Lock top-N consensus picks into SAFE+VALUE teams. "
                             "Default: auto from stage-type strategy (sprint=3, mountain/hilly/tt/cobbled=2). "
                             "Use 0 to let the LP decide freely.")
    parser.add_argument("--scrape-co", action="store_true",
                        help="Re-scrape CyclingOracle discipline data")
    parser.add_argument("--scrape-pcs", action="store_true",
                        help="Re-scrape PCS recent form data (adds ~3 min)")
    parser.add_argument("--scrape-holdet", action="store_true",
                        help="Re-scrape Holdet.dk: priser, GC-stilling og trøjer")
    parser.add_argument("--scrape-gt", action="store_true",
                        help="Hent GT-etaperesultater fra PCS til ML rolling-form")
    parser.add_argument("--update-riders", action="store_true",
                        help="Skriv Holdet-priser tilbage til riders.json (kræver --scrape-holdet)")
    args = parser.parse_args()

    # Auto-detect stage + type from Holdet schedule if not supplied
    stage      = args.stage
    stage_type = args.type

    if not stage or not stage_type:
        print("  Auto-detekterer etape fra Holdet-program...")
        try:
            from scrape_holdet import detect_next_stage, DEFAULT_CARTRIDGE, KNOWN_GAME_IDS
            auto_game_id = KNOWN_GAME_IDS.get(DEFAULT_CARTRIDGE)
            if auto_game_id is None:
                print(f"  [WARN] Game ID ikke konfigureret for '{DEFAULT_CARTRIDGE}' "
                      f"— tilføj det til KNOWN_GAME_IDS i scrape_holdet.py")
                det_stage, det_type = None, None
            else:
                det_stage, det_type = detect_next_stage(auto_game_id, DEFAULT_CARTRIDGE)
        except Exception as exc:
            print(f"  [WARN] Auto-detect slog fejl: {exc}")
            det_stage, det_type = None, None

        if det_stage and not stage:
            stage = det_stage
        if det_type and not stage_type:
            stage_type = det_type

    # Interactive fallback if auto-detect also failed (local dev only)
    if not stage:
        stage = int(input("Etapenummer: ").strip())
    if not stage_type:
        print(f"Etapetype ({'/'.join(STAGE_TYPES)}): ", end="")
        stage_type = input().strip().lower()
        if stage_type not in STAGE_TYPES:
            stage_type = "hilly"

    # ── Resolve stage-type strategy ───────────────────────────
    strategy     = get_strategy(stage_type)
    force_top_n  = args.force_top if args.force_top is not None else strategy["force_top_n"]
    budget_boost = strategy["budget_rider_boost"]
    attack_out_n = strategy["attack_out_n"]

    print(f"\n{'='*60}")
    print(f"  TdF Manager — Etape {stage} ({stage_type.upper()})")
    print(f"{'='*60}")
    print(f"  Strategi:  force_top={force_top_n}  budget_boost={budget_boost}x"
          f"  attack_out={attack_out_n}")
    print(f"  {strategy['rationale']}")
    print()

    # ── Load data ─────────────────────────────────────────────
    riders = load_riders()
    print(f"  Ryttere indlæst: {len(riders)}")

    vs_path        = Path(args.veloscore) if args.veloscore else None
    veloscore_data = load_veloscore(vs_path, stage)
    print(f"  VeloScore-data: {len(veloscore_data)} ryttere")

    if args.scrape_co:
        print("  Henter CyclingOracle data (kør scrape_co.py for fuld opdatering)…")
        import scrape_co
        import sys as _sys
        _sys.argv = ["scrape_co.py"]
        scrape_co.main()

    if args.scrape_pcs:
        print("  Henter PCS form-data (kør scrape_pcs.py for fuld opdatering)…")
        import scrape_pcs
        import sys as _sys
        _sys.argv = ["scrape_pcs.py"]
        scrape_pcs.main()

    if args.scrape_holdet:
        print("  Henter Holdet-data (priser, GC, trøjer)…")
        import scrape_holdet
        import sys as _sys
        holdet_argv = ["scrape_holdet.py"]
        if args.update_riders:
            holdet_argv.append("--update-riders")
        _sys.argv = holdet_argv
        scrape_holdet.main()
        # Reload riders if prices were updated
        if args.update_riders:
            riders = load_riders()
            print(f"  Ryttere genindlæst: {len(riders)} (priser opdateret)")

    if args.scrape_gt:
        print("  Henter GT-etaperesultater fra PCS (ML rolling-form)…")
        import scrape_gt_results
        import sys as _sys
        _sys.argv = ["scrape_gt_results.py"]
        scrape_gt_results.main()

    co_data      = load_cyclingoracle()
    pcs_form     = load_pcs_form()
    gc_standings = load_gc_standings()
    jerseys      = load_jerseys()
    sprint_kom   = load_sprint_kom()
    team_bonus   = load_team_bonus()
    profile_sc   = load_profile_score(stage)

    print(f"  CyclingOracle: {len(co_data)} ryttere i cache")
    print(f"  PCS form:      {len(pcs_form)} ryttere i cache")
    if gc_standings:
        top3 = sorted(gc_standings.items(), key=lambda x: x[1])[:3]
        id_to_name = {r["id"]: r["full_name"] for r in riders}
        top3_names = ", ".join(f"{r}. {id_to_name.get(k, k)}" for k, r in top3)
        print(f"  GC-stilling:   {len(gc_standings)} ryttere  (top3: {top3_names})")
    else:
        print("  GC-stilling:   (ingen data — kør med --scrape-holdet)")
    if jerseys:
        id_to_name = {r["id"]: r["full_name"] for r in riders}
        jersey_str = ", ".join(
            f"{id_to_name.get(k, k)}: {'+'.join(v)}"
            for k, v in jerseys.items()
        )
        print(f"  Trøjer:        {jersey_str}")
    if sprint_kom:
        id_to_name = {r["id"]: r["full_name"] for r in riders}
        top_s = sorted([(r,d) for r,d in sprint_kom.items() if "sprint_rank" in d],
                       key=lambda x: x[1]["sprint_rank"])[:3]
        top_k = sorted([(r,d) for r,d in sprint_kom.items() if "kom_rank" in d],
                       key=lambda x: x[1]["kom_rank"])[:3]
        print(f"  Sprint-klass.: {', '.join(id_to_name.get(r,r) for r,_ in top_s)}")
        print(f"  Bjerg-klass.:  {', '.join(id_to_name.get(r,r) for r,_ in top_k)}")
    if profile_sc is not None:
        from src.predictor import _profile_scale
        scale = _profile_scale(profile_sc, stage_type)
        print(f"  Profile score: {profile_sc}  →  WINNER_POINTS × {scale:.2f}")

    # ── ML signal ────────────────────────────────────────────
    from src.ml_signal import compute_ml_scores
    gt_results   = load_gt_stage_results()
    pcs_form_raw = load_pcs_form_raw()
    pcs_rankings  = load_pcs_rankings()
    pcs_rank_data, pcs_n_results_data = build_pcs_rank_by_rider(pcs_rankings, pcs_form_raw)
    if pcs_rank_data:
        top3_rank = sorted(pcs_rank_data.items(), key=lambda x: -x[1])[:3]
        id_to_name = {r["id"]: r["full_name"] for r in riders}
        print(f"  PCS ranking:   {len(pcs_rank_data)} ryttere  Top3: "
              f"{', '.join(id_to_name.get(k,k) for k,_ in top3_rank)}")
    else:
        print(f"  PCS ranking:   (ingen data — kør scrape_pcs_rankings.py)")
    n_gt_stages  = len((gt_results or {}).get("stages", {}))
    pcs_specialty_data = {
        rid: entry["pcs_specialties"]
        for rid, entry in (pcs_form_raw or {}).items()
        if entry.get("pcs_specialties")
    }
    ml_scores    = compute_ml_scores(
        riders=riders,
        stage_type=stage_type,
        stage_num=stage,
        profile_score=profile_sc,
        gt_results=gt_results,
        pcs_form_raw=pcs_form_raw,
        pcs_rankings=pcs_rankings or None,
        co_data=co_data or None,
        pcs_specialty_data=pcs_specialty_data or None,
        startlist_quality=1.0,   # TdF/GT: top-tier field (~1000 PCS score → 1.0 normalised)
    )
    if ml_scores:
        top_ml = sorted(ml_scores.items(), key=lambda x: x[1], reverse=True)[:3]
        id_to_name = {r["id"]: r["full_name"] for r in riders}
        top_ml_str = ", ".join(f"{id_to_name.get(k, k)} ({v:.0f})" for k, v in top_ml)
        ml_source = "ML rolling-form" if n_gt_stages >= 5 else f"historisk styrke ({n_gt_stages} etaper kørt)"
        print(f"  ML-signal:     {len(ml_scores)} ryttere  "
              f"(GT-etaper: {n_gt_stages} — {ml_source})  Top3: {top_ml_str}")
    else:
        ml_source = "ikke tilgængelig"
        print(f"  ML-signal:     ikke tilgængelig (model ikke indlæst)")

    # ── Run predictions ───────────────────────────────────────
    print("\n  Beregner forventede point...")
    predictions = predict_all(
        riders=riders,
        stage_type=stage_type,
        veloscore_data=veloscore_data,
        cyclingoracle_data=co_data,
        pcs_form_data=pcs_form,
        current_gc=gc_standings or None,
        current_jerseys=jerseys or None,
        profile_score=profile_sc,
        sprint_kom_data=sprint_kom or None,
        team_bonus_data=team_bonus or None,
        ml_prob_data=ml_scores or None,
        pcs_rank_data=pcs_rank_data or None,
        pcs_n_results_data=pcs_n_results_data or None,
    )

    # ── Override expected_pts with holdet_est calibration ────────────────────
    # holdet_est (calibrated from Giro 2026 actual Holdet points) is more
    # reliable than the rank-decay formula. Override BEFORE team optimization
    # so the picked team also uses the better numbers.
    _preds_path = WEB_DATA / "tdf2026_predictions.json"
    if _preds_path.exists():
        _tdf_pred = json.loads(_preds_path.read_text(encoding="utf-8"))
        _stage_data = next(
            (s for s in _tdf_pred.get("stages", []) if s["num"] == stage), None
        )
        if _stage_data:
            _holdet_est = {
                r["id"]: r["holdet_est"]
                for r in _stage_data["riders"]
                if r.get("holdet_est")
            }
            updated = 0
            for p in predictions:
                est = _holdet_est.get(p["rider_id"])
                if est:
                    p["expected_pts"] = round(est * 1000)
                    updated += 1
            if updated:
                print(f"  Holdet kalibrering: {updated} ryttere opdateret med holdet_est")

    # ── Load current team ─────────────────────────────────────
    current_team_data = load_current_team(predictions)

    # ── Optimize three transfer-aware teams ───────────────────
    print("  Optimerer holdsammensætninger...")
    teams = make_three_teams(
        predictions,
        current_team_data=current_team_data,
        transfer_budget_M=args.budget,
        force_top_n=force_top_n,
        budget_rider_boost=budget_boost,
        attack_out_n=attack_out_n,
    )

    # ── Unconstrained best possible team ─────────────────────
    best_team = make_best_team(predictions)

    # ── Print results to terminal ─────────────────────────────
    label_map = {"safe": "SIKKER", "value": "VÆRDI", "attack": "ANGREB"}
    for i, team in enumerate(teams, 1):
        label = label_map.get(team["label"], team["label"].upper())
        ass   = team["assessment"]
        ta    = team.get("transfer_analysis", {})

        print(f"\n  ── Hold {i}: {label} {'─'*(40 - len(label))}")
        print(f"  Forventet score: {team['expected_pts']:,.0f} kr")
        print(f"  Budget brugt:    {ass['total_cost_M']:.1f}M / 50M")
        print(f"  Risikoprofil:    {ass['risk_profile']}")
        if ta:
            affordable = "✓" if ta["affordable"] else "⚠"
            print(f"  Udskiftninger:   {ta['n_transfers']} ryttere  "
                  f"(net {ta['net_cost_M']:+.2f}M)  Bank efter: "
                  f"{ta['balance_after_M']:.2f}M  {affordable}")
            if ta["to_sell"]:
                print(f"  Sælg: {', '.join(r['full_name'] for r in ta['to_sell'])}")
            if ta["to_buy"]:
                print(f"  Køb:  {', '.join(r['full_name'] for r in ta['to_buy'])}")
        print(f"  Kaptajn: {ass['captain_name']} ({ass['captain_reasoning']})")
        print(f"\n  {'Rytter':<28} {'Hold':>6} {'Pris':>5}M  {'Forv.':>8}  {'Kap?':>4}")
        print(f"  {'-'*65}")
        for r in sorted(team["team"], key=lambda x: x["expected_pts"], reverse=True):
            cap = "★" if r["is_captain"] else ""
            print(f"  {r['full_name']:<28} {r['team']:>6} {r['price']:>5.1f}M  "
                  f"{r['expected_pts']:>8,.0f}  {cap:>4}")

    if best_team:
        ass = best_team["assessment"]
        print(f"\n  ── Bedst muligt (ubegrænset) {'─'*28}")
        print(f"  Forventet score: {best_team['expected_pts']:,.0f} kr")
        print(f"  Budget brugt:    {ass['total_cost_M']:.1f}M / 50M")
        print(f"  Kaptajn: {ass['captain_name']}")

    # ── Save for web ──────────────────────────────────────────
    output = {
        "stage":        stage,
        "stage_type":   stage_type,
        "generated":    _now_iso(),
        "ml_source":    ml_source,
        "ml_gt_stages": n_gt_stages,
        "current_team": current_team_data,
        "teams":        teams,
        "best_team":    best_team,
        "veloscore":    veloscore_data[:20],
        "top_picks":    predictions[:20],
    }
    out_path = WEB_DATA / "recommendations.json"
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  ✓ Gemt til {out_path}")
    print("  Push til GitHub for at opdatere hjemmesiden:\n")
    print(f"    git add web/data/recommendations.json && "
          f"git commit -m 'Etape {stage}' && git push")


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
