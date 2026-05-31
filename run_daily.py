"""
Daily workflow entry point.

Usage:
    python run_daily.py --stage 5 --type sprint --veloscore data/stage_05_veloscore.json

Or interactively (prompts for missing parameters):
    python run_daily.py

Output:
    web/data/recommendations.json  — read by GitHub Pages frontend
    web/data/stage_info.json       — current stage metadata
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from src.predictor import predict_all
from src.optimizer import make_three_teams

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
    # Try auto-discover
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
        # Flatten: rider_id → {SPR: x, MTN: x, ...}
        result = {}
        for name, data in raw.items():
            result[name] = data.get("ratings", {})
        return result
    return {}


def main():
    parser = argparse.ArgumentParser(description="TdF Manager — daily team optimizer")
    parser.add_argument("--stage",     type=int,   help="Stage number (e.g. 5)")
    parser.add_argument("--type",      type=str,   choices=STAGE_TYPES, help="Stage type")
    parser.add_argument("--veloscore", type=str,   help="Path to VeloScore JSON file")
    parser.add_argument("--budget",    type=float, default=50.0, help="Available budget in millions")
    parser.add_argument("--scrape-co", action="store_true", help="Re-scrape CyclingOracle data")
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

    # Load data
    riders = load_riders()
    print(f"  Ryttere indlæst: {len(riders)}")

    vs_path = Path(args.veloscore) if args.veloscore else None
    veloscore_data = load_veloscore(vs_path, stage)
    print(f"  VeloScore-data: {len(veloscore_data)} ryttere")

    # Optional: scrape CyclingOracle
    if args.scrape_co:
        print("  Henter CyclingOracle data...")
        from src.scrapers import scrape_cyclingoracle_all
        scrape_cyclingoracle_all()

    co_data = load_cyclingoracle()
    print(f"  CyclingOracle: {len(co_data)} ryttere i cache")

    # Run predictions
    print("\n  Beregner forventede point...")
    predictions = predict_all(
        riders=riders,
        stage_type=stage_type,
        veloscore_data=veloscore_data,
        cyclingoracle_data=co_data,
    )

    # Optimize three teams
    print("  Optimerer holdsammensætninger...")
    teams = make_three_teams(predictions, transfer_budget_M=args.budget)

    # Print results to terminal
    for i, team in enumerate(teams, 1):
        label_map = {"safe": "SIKKER", "value": "VÆRDI", "attack": "ANGREB"}
        label = label_map.get(team["label"], team["label"].upper())
        ass   = team["assessment"]
        print(f"\n  ── Hold {i}: {label} ──────────────────────────────")
        print(f"  Forventet score: {team['expected_pts']:,.0f} kr")
        print(f"  Budget brugt:    {ass['total_cost_M']:.1f}M / 50M")
        print(f"  Risikoprofil:    {ass['risk_profile']}")
        print(f"  Kaptajn: {ass['captain_name']} ({ass['captain_reasoning']})")
        print(f"\n  {'Rytter':<28} {'Hold':>6} {'Pris':>5}M  {'Forv.':>8}  {'Kap?':>4}")
        print(f"  {'-'*65}")
        for r in sorted(team["team"], key=lambda x: x["expected_pts"], reverse=True):
            cap = "★" if r["is_captain"] else ""
            print(f"  {r['full_name']:<28} {r['team']:>6} {r['price']:>5.1f}M  "
                  f"{r['expected_pts']:>8,.0f}  {cap:>4}")

    # Save for web
    output = {
        "stage":       stage,
        "stage_type":  stage_type,
        "generated":   _now_iso(),
        "teams":       teams,
        "veloscore":   veloscore_data[:20],
        "top_picks":   predictions[:20],
    }
    out_path = WEB_DATA / "recommendations.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  ✓ Gemt til {out_path}")
    print("  Push til GitHub for at opdatere hjemmesiden:\n")
    print("    git add web/data/recommendations.json && git commit -m 'Etape {stage}' && git push")


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
