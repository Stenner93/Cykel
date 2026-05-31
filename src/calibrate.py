"""
Calibration module — learns optimal predictor weights from Giro 2026 data.

Method:
  For each stage where we have BOTH VeloScore predictions AND actual FantasyTool
  results, we compute how well each signal (VeloScore rank, discipline match, etc.)
  correlates with actual points earned.

  We then use scipy.optimize.minimize to find weights that minimise
  Mean Absolute Error (MAE) between predicted and actual points.

Output:
  data/calibrated_weights.json  — replaces DEFAULT_WEIGHTS in predictor.py
  data/calibration_report.json  — full per-stage accuracy breakdown
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ROOT     = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

try:
    from scipy.optimize import minimize
    import numpy as np
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rank_scale(rank: int) -> float:
    """Same curve as predictor.py — must stay in sync."""
    if rank <= 0:
        return 0.0
    return max(0.0, 1.0 / (1.0 + 0.5 * (rank - 1) ** 1.1))


def _stage_winner_pts(stage_type: str) -> int:
    from .predictor import WINNER_POINTS
    return WINNER_POINTS.get(stage_type, 500_000)


# ---------------------------------------------------------------------------
# Build training pairs
# ---------------------------------------------------------------------------

def build_training_data(
    veloscore_stages: list[dict],
    result_stages: list[dict],
) -> list[dict]:
    """
    Match VeloScore predictions with actual results stage by stage.
    Returns list of training samples:
      {stage, stage_type, rider, vs_rank, vs_score, actual_pts, vs_signal}
    """
    results_by_stage: dict[int, dict] = {r["stage"]: r for r in result_stages}
    samples = []

    for vs_stage in veloscore_stages:
        stage_num  = vs_stage["stage"]
        stage_type = vs_stage["stage_type"]
        result     = results_by_stage.get(stage_num)
        if not result or not result.get("top_results"):
            continue

        # Build actual-points lookup (rider name → points)
        actual: dict[str, int] = {}
        for r in result["top_results"]:
            name = r["rider"].lower()
            pts  = r.get("points") or 0
            actual[name] = pts

        for pred in vs_stage["predictions"]:
            rider_lower = pred["rider"].lower()
            actual_pts  = actual.get(rider_lower, 0)

            # Try last-name match if full name not found
            if actual_pts == 0:
                last = rider_lower.split()[-1]
                for k, v in actual.items():
                    if last in k:
                        actual_pts = v
                        break

            vs_signal = 0.0
            if pred.get("veloscore") is not None:
                vs_signal = pred["veloscore"] / 10.0
            elif pred.get("rank") is not None:
                vs_signal = _rank_scale(pred["rank"])

            samples.append({
                "stage":      stage_num,
                "stage_type": stage_type,
                "rider":      pred["rider"],
                "vs_rank":    pred.get("rank", 99),
                "vs_score":   pred.get("veloscore"),
                "vs_signal":  vs_signal,
                "actual_pts": actual_pts,
                "winner_pts": _stage_winner_pts(stage_type),
            })

    return samples


# ---------------------------------------------------------------------------
# Accuracy metrics
# ---------------------------------------------------------------------------

def compute_metrics(samples: list[dict], weights: dict) -> dict:
    """
    Given samples and weights, compute prediction quality metrics.
    Simple model: predicted = vs_signal * w_vs * winner_pts
    (discipline/form/odds signals are 0.5 when no data)
    """
    w_vs   = weights.get("veloscore", 0.45)
    errors = []
    rank_corr_pairs = []  # (predicted_rank, actual_rank)

    by_stage: dict[int, list] = {}
    for s in samples:
        by_stage.setdefault(s["stage"], []).append(s)

    for stage_num, stage_samples in by_stage.items():
        predicted = []
        for s in stage_samples:
            pred_pts = s["vs_signal"] * w_vs * s["winner_pts"]
            predicted.append((s["rider"], pred_pts, s["actual_pts"]))

        predicted.sort(key=lambda x: x[1], reverse=True)
        actual_sorted = sorted(predicted, key=lambda x: x[2], reverse=True)
        actual_rank = {name: i+1 for i, (name, _, _) in enumerate(actual_sorted)}

        for i, (name, pred, act) in enumerate(predicted):
            errors.append(abs(pred - act))
            rank_corr_pairs.append((i+1, actual_rank.get(name, 99)))

    mae = sum(errors) / len(errors) if errors else 0

    # Spearman-like rank correlation
    if rank_corr_pairs:
        n = len(rank_corr_pairs)
        pred_ranks = [p for p, _ in rank_corr_pairs]
        act_ranks  = [a for _, a in rank_corr_pairs]
        mean_p = sum(pred_ranks) / n
        mean_a = sum(act_ranks) / n
        cov    = sum((p-mean_p)*(a-mean_a) for p,a in rank_corr_pairs) / n
        std_p  = math.sqrt(sum((p-mean_p)**2 for p in pred_ranks) / n)
        std_a  = math.sqrt(sum((a-mean_a)**2 for a in act_ranks) / n)
        corr   = cov / (std_p * std_a) if std_p * std_a > 0 else 0
    else:
        corr = 0

    return {"mae": round(mae), "rank_correlation": round(corr, 4), "n_samples": len(samples)}


# ---------------------------------------------------------------------------
# Per-stage accuracy report
# ---------------------------------------------------------------------------

def stage_accuracy_report(samples: list[dict]) -> list[dict]:
    """
    For each stage, show: top VeloScore pick vs actual winner.
    """
    by_stage: dict[int, list] = {}
    for s in samples:
        by_stage.setdefault(s["stage"], []).append(s)

    report = []
    for stage_num in sorted(by_stage.keys()):
        stage_samples = by_stage[stage_num]
        stage_type    = stage_samples[0]["stage_type"]

        # VeloScore top pick
        vs_top = min(stage_samples, key=lambda x: x["vs_rank"])

        # Actual winner
        actual_winner = max(stage_samples, key=lambda x: x["actual_pts"])

        # Was VeloScore top-1 correct?
        vs1_correct = vs_top["rider"].lower() == actual_winner["rider"].lower()

        # Was winner in VeloScore top 5?
        sorted_by_vs = sorted(stage_samples, key=lambda x: x["vs_rank"])
        top5_names   = [s["rider"].lower() for s in sorted_by_vs[:5]]
        winner_in_top5 = actual_winner["rider"].lower() in top5_names

        # Average points by VeloScore rank bucket
        bucket_pts: dict[str, list] = {"1-3": [], "4-8": [], "9-15": [], "16+": []}
        for s in stage_samples:
            r = s["vs_rank"]
            if r <= 3:   bucket_pts["1-3"].append(s["actual_pts"])
            elif r <= 8: bucket_pts["4-8"].append(s["actual_pts"])
            elif r <= 15:bucket_pts["9-15"].append(s["actual_pts"])
            else:        bucket_pts["16+"].append(s["actual_pts"])

        avg_by_bucket = {
            k: round(sum(v)/len(v)) if v else 0
            for k, v in bucket_pts.items()
        }

        report.append({
            "stage":             stage_num,
            "stage_type":        stage_type,
            "vs_top_pick":       vs_top["rider"],
            "actual_winner":     actual_winner["rider"],
            "actual_winner_pts": actual_winner["actual_pts"],
            "vs1_correct":       vs1_correct,
            "winner_in_vs_top5": winner_in_top5,
            "avg_pts_by_vs_rank": avg_by_bucket,
        })

    return report


# ---------------------------------------------------------------------------
# Weight optimisation
# ---------------------------------------------------------------------------

def optimise_weights(samples: list[dict]) -> dict:
    """
    Find the VeloScore weight that minimises MAE.
    (With only VeloScore signal in training data, we optimise just w_vs.)
    """
    if not HAS_SCIPY:
        print("  scipy not available — using default weights")
        return {"veloscore": 0.45, "odds_prob": 0.25, "discipline": 0.20, "form": 0.10}

    best_mae   = float("inf")
    best_w_vs  = 0.45

    # Grid search over veloscore weight (0.2 → 0.8)
    for w_vs_100 in range(20, 81, 5):
        w_vs = w_vs_100 / 100.0
        metrics = compute_metrics(samples, {"veloscore": w_vs})
        if metrics["mae"] < best_mae:
            best_mae  = metrics["mae"]
            best_w_vs = w_vs

    # Remaining weight distributed to others in fixed ratio
    remaining = 1.0 - best_w_vs
    weights = {
        "veloscore":  round(best_w_vs, 3),
        "odds_prob":  round(remaining * 0.42, 3),
        "discipline": round(remaining * 0.35, 3),
        "form":       round(remaining * 0.23, 3),
    }

    print(f"  Optimale vægte: VS={weights['veloscore']}, "
          f"Odds={weights['odds_prob']}, Disciplin={weights['discipline']}, "
          f"Form={weights['form']}")
    print(f"  Optimeret MAE: {best_mae:,.0f} kr")

    return weights


# ---------------------------------------------------------------------------
# Main calibration entry point
# ---------------------------------------------------------------------------

def run_calibration(verbose: bool = True) -> dict:
    """
    Load Giro 2026 data, calibrate, save results.
    Returns the calibrated weights.
    """
    print("\n" + "="*60)
    print("  KALIBRERING PÅ GIRO 2026-DATA")
    print("="*60)

    vs_path  = DATA_DIR / "giro2026" / "veloscore.json"
    res_path = DATA_DIR / "giro2026" / "results.json"

    if not vs_path.exists() or not res_path.exists():
        raise FileNotFoundError("Mangler giro2026/veloscore.json eller results.json")

    vs_stages  = json.loads(vs_path.read_text(encoding="utf-8"))
    res_stages = json.loads(res_path.read_text(encoding="utf-8"))

    print(f"\n  VeloScore-etaper: {len(vs_stages)}")
    print(f"  Resultat-etaper:  {len(res_stages)}")

    # Build training samples
    samples = build_training_data(vs_stages, res_stages)
    print(f"  Træningspar:      {len(samples)}")

    if not samples:
        print("  ADVARSEL: Ingen matchende data fundet!")
        return {"veloscore": 0.45, "odds_prob": 0.25, "discipline": 0.20, "form": 0.10}

    # Per-stage accuracy report
    report = stage_accuracy_report(samples)

    if verbose:
        print(f"\n  {'Etape':<8} {'Type':<10} {'VeloScore #1':<25} {'Faktisk vinder':<25} {'VS1 rigtig':<12} {'Vinder i top 5'}")
        print(f"  {'-'*100}")
        for r in report:
            v1  = "✓" if r["vs1_correct"]       else "✗"
            vt5 = "✓" if r["winner_in_vs_top5"] else "✗"
            print(f"  {r['stage']:<8} {r['stage_type']:<10} {r['vs_top_pick']:<25} "
                  f"{r['actual_winner']:<25} {v1:<12} {vt5}")

    # Summary stats
    n_stages     = len(report)
    vs1_accuracy = sum(1 for r in report if r["vs1_correct"])     / n_stages
    top5_accuracy = sum(1 for r in report if r["winner_in_vs_top5"]) / n_stages

    print(f"\n  ── VeloScore-nøjagtighed ──────────────────────────")
    print(f"  VeloScore #1 vandt:       {vs1_accuracy*100:.0f}% ({sum(1 for r in report if r['vs1_correct'])}/{n_stages} etaper)")
    print(f"  Vinder i VeloScore top 5: {top5_accuracy*100:.0f}% ({sum(1 for r in report if r['winner_in_vs_top5'])}/{n_stages} etaper)")

    # Points by VeloScore rank bucket (all stages combined)
    all_buckets: dict[str, list] = {"1-3": [], "4-8": [], "9-15": [], "16+": []}
    for r in report:
        for bucket, avg in r["avg_pts_by_vs_rank"].items():
            if avg > 0:
                all_buckets[bucket].append(avg)
    print(f"\n  ── Gennemsnitspoint per VeloScore-rankgruppe ──────")
    for bucket, vals in all_buckets.items():
        avg = sum(vals)/len(vals) if vals else 0
        print(f"  Rank {bucket:<6}: {avg:>8,.0f} kr")

    # Optimise weights
    print("\n  ── Optimerer vægte ────────────────────────────────")
    weights = optimise_weights(samples)

    # Baseline metrics (default weights)
    default_metrics = compute_metrics(samples, {"veloscore": 0.45})
    optim_metrics   = compute_metrics(samples, weights)
    print(f"\n  MAE (standard vægte): {default_metrics['mae']:,.0f} kr")
    print(f"  MAE (optimerede):     {optim_metrics['mae']:,.0f} kr")
    print(f"  Rank-korrelation:     {optim_metrics['rank_correlation']:.4f}")

    # Save calibrated weights
    weights_path = DATA_DIR / "calibrated_weights.json"
    weights_path.write_text(json.dumps(weights, indent=2), encoding="utf-8")
    print(f"\n  ✓ Kalibrerede vægte gemt → {weights_path}")

    # Save full report
    full_report = {
        "weights":         weights,
        "metrics":         optim_metrics,
        "vs1_accuracy":    round(vs1_accuracy, 4),
        "top5_accuracy":   round(top5_accuracy, 4),
        "n_stages":        n_stages,
        "n_samples":       len(samples),
        "per_stage":       report,
    }
    report_path = DATA_DIR / "calibration_report.json"
    report_path.write_text(
        json.dumps(full_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  ✓ Fuld rapport gemt    → {report_path}")
    print("="*60 + "\n")

    return weights


if __name__ == "__main__":
    run_calibration()
