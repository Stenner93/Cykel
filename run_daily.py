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

DATA_DIR = ROOT / "data"
WEB_DATA = ROOT / "web" / "data"
WEB_DATA.mkdir(parents=True, exist_ok=True)


STAGE_TYPES = ["sprint", "mountain", "tt", "hilly", "cobbled"]


def load_riders() -> list[dict]:
    return json.loads((DATA_DIR / "riders.json").read_text(encoding="utf-8"))


def load_veloscore(path: Path | None, stage: int) -> list[dict]:
    if path and path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("predictions", [])
    auto = DATA_DIR / f"stage_{stage:02d}_veloscore.json"
    if auto.exists():
        data = json.loads(auto.read_text(encoding="utf-8"))
        return data.get("predictions", [])
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
    parser.add_argument("--force-top", type=int, default=3,
                        help="Lock top-N consensus picks into SAFE+VALUE teams (default: 3). "
                             "Use 0 to let the LP decide freely.")
    parser.add_argument("--scrape-co", action="store_true",
                        help="Re-scrape CyclingOracle discipline data")
    args = parser.parse_args()

    # Interactive prompts for missing args
    stage = args.stage
    if not stage:
        stage = int(input("Etapenummer: ").strip())

    stage_type = args.type
    if not stage_type:
        print(f"Etapetype ({'/'.join(STAGE_TYPES)}): ", end="")
        stage_type = input().strip().lower()
        if stage_type not in STAGE_TYPES:
            stage_type = "hilly"

    print(f"\n{'='*60}")
    print(f"  TdF Manager — Etape {stage} ({stage_type.upper()})")
    print(f"{'='*60}\n")

    # ── Load data ─────────────────────────────────────────────
    riders = load_riders()
    print(f"  Ryttere indlæst: {len(riders)}")

    vs_path        = Path(args.veloscore) if args.veloscore else None
    veloscore_data = load_veloscore(vs_path, stage)
    print(f"  VeloScore-data: {len(veloscore_data)} ryttere")

    if args.scrape_co:
        print("  Henter CyclingOracle data...")
        from src.scrapers import scrape_cyclingoracle_all
        scrape_cyclingoracle_all()

    co_data = load_cyclingoracle()
    print(f"  CyclingOracle: {len(co_data)} ryttere i cache")

    # ── Run predictions ───────────────────────────────────────
    print("\n  Beregner forventede point...")
    predictions = predict_all(
        riders=riders,
        stage_type=stage_type,
        veloscore_data=veloscore_data,
        cyclingoracle_data=co_data,
    )

    # ── Load current team ─────────────────────────────────────
    current_team_data = load_current_team(predictions)

    # ── Optimize three transfer-aware teams ───────────────────
    print("  Optimerer holdsammensætninger...")
    teams = make_three_teams(
        predictions,
        current_team_data=current_team_data,
        transfer_budget_M=args.budget,
        force_top_n=args.force_top,
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
