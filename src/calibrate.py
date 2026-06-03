"""
Calibration module — learns optimal predictor weights from Giro 2026 data.

Method:
  For each stage where we have BOTH VeloScore predictions AND actual FantasyTool
  results, AND where the stage_type matches between the two files, we compute
  how well the VeloScore signal correlates with actual points earned.

  We then grid-search the VeloScore weight that minimises MAE; the remaining
  weight is distributed to discipline/form/odds in a fixed ratio.

Type-mismatch handling:
  Some stage NUMBERS map to different real-world stages in veloscore.json vs
  results.json (e.g. a sprint VeloScore prediction numbered "5" that actually
  ran against a mountain result also numbered "5").  These pairs are silently
  wrong: all VS sprint picks score 0 pts against mountain results, which
  corrupts the calibration.  Fix: only include stage pairs where
  vs_stage_type == result_stage_type.  Mismatched pairs are reported but
  excluded from weight optimisation.

Output:
  data/calibrated_weights.json  — replaces DEFAULT_WEIGHTS in predictor.py
  data/calibration_report.json  — full per-stage accuracy breakdown
"""
from __future__ import annotations

import json
import math
from pathlib import Path

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


def _norm(name: str) -> str:
    return name.lower().strip()


def _lookup_pts(rider_name: str, actual: dict[str, int]) -> int:
    """
    Match a rider name to the actual-points dict.
    Tries exact lower-case match, then exact last-name match.
    Avoids the old substring bug ('last in k') that could match
    e.g. 'christen' against 'jan christen' AND 'fabio christen'.
    """
    nl = _norm(rider_name)
    if nl in actual:
        return actual[nl]
    last = nl.split()[-1]
    candidates = [(k, v) for k, v in actual.items() if k.split()[-1] == last]
    if len(candidates) == 1:
        return candidates[0][1]
    return 0


# ---------------------------------------------------------------------------
# Build training pairs
# ---------------------------------------------------------------------------

def build_training_data(
    veloscore_stages: list[dict],
    result_stages: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Match VeloScore predictions with actual results stage by stage.

    Returns:
      (samples, skipped_stages)

    samples        — training records used for weight optimisation;
                     only from stages where VS stage_type == result stage_type.
    skipped_stages — stages excluded due to type mismatch or missing data,
                     reported in the calibration report for transparency.

    Each sample:
      {stage, stage_type, rider, vs_rank, vs_score, vs_signal,
       actual_pts, winner_pts}
    """
    results_by_stage: dict[int, dict] = {r["stage"]: r for r in result_stages}
    samples: list[dict] = []
    skipped: list[dict] = []

    for vs_stage in veloscore_stages:
        stage_num = vs_stage["stage"]
        vs_type   = vs_stage["stage_type"]
        result    = results_by_stage.get(stage_num)

        # No results for this stage number
        if not result:
            skipped.append({
                "stage":   stage_num,
                "vs_type": vs_type,
                "reason":  "Ingen resultater fundet for etape-nr.",
            })
            continue

        # Results exist but are missing (e.g. stage 12 in Giro 2026 data)
        if not result.get("top_results"):
            skipped.append({
                "stage":       stage_num,
                "vs_type":     vs_type,
                "result_type": result.get("stage_type", "?"),
                "reason":      "Resultater mangler (top_results tom)",
            })
            continue

        result_type = result["stage_type"]

        # Stage number matches but types differ → different real-world stages
        if vs_type != result_type:
            skipped.append({
                "stage":       stage_num,
                "vs_type":     vs_type,
                "result_type": result_type,
                "reason": (
                    f"Etapetype-mismatch: VeloScore={vs_type}, "
                    f"Resultater={result_type}. "
                    "Forskelligt etapenummer i de to datasæt."
                ),
            })
            continue

        # Build actual-points lookup (lower-case name → pts)
        actual: dict[str, int] = {}
        for r in result["top_results"]:
            pts = r.get("points")
            if pts is not None:
                actual[_norm(r["rider"])] = int(pts)

        for pred in vs_stage["predictions"]:
            vs_signal = 0.0
            if pred.get("veloscore") is not None:
                vs_signal = float(pred["veloscore"]) / 10.0
            elif pred.get("rank") is not None:
                vs_signal = _rank_scale(int(pred["rank"]))

            samples.append({
                "stage":      stage_num,
                "stage_type": vs_type,
                "rider":      pred["rider"],
                "vs_rank":    pred.get("rank", 99),
                "vs_score":   pred.get("veloscore"),
                "vs_signal":  vs_signal,
                "actual_pts": _lookup_pts(pred["rider"], actual),
                "winner_pts": _stage_winner_pts(vs_type),
            })

    return samples, skipped


# ---------------------------------------------------------------------------
# Accuracy metrics
# ---------------------------------------------------------------------------

def compute_metrics(samples: list[dict], weights: dict) -> dict:
    """
    Given samples and weights, compute prediction quality metrics.
    Simple model: predicted = vs_signal * w_vs * winner_pts
    (discipline / form / odds signals default to 0.5 when no training data)
    """
    w_vs   = weights.get("veloscore", 0.45)
    errors = []
    rank_corr_pairs: list[tuple[int, int]] = []

    by_stage: dict[int, list] = {}
    for s in samples:
        by_stage.setdefault(s["stage"], []).append(s)

    for stage_samples in by_stage.values():
        predicted = [
            (s["rider"],
             s["vs_signal"] * w_vs * s["winner_pts"],
             s["actual_pts"])
            for s in stage_samples
        ]
        predicted.sort(key=lambda x: x[1], reverse=True)
        actual_sorted = sorted(predicted, key=lambda x: x[2], reverse=True)
        actual_rank   = {name: i + 1 for i, (name, _, _) in enumerate(actual_sorted)}

        for i, (name, pred, act) in enumerate(predicted):
            errors.append(abs(pred - act))
            rank_corr_pairs.append((i + 1, actual_rank.get(name, 99)))

    mae = sum(errors) / len(errors) if errors else 0

    # Pearson correlation on ranks (Spearman-like)
    if rank_corr_pairs:
        n      = len(rank_corr_pairs)
        p_vals = [p for p, _ in rank_corr_pairs]
        a_vals = [a for _, a in rank_corr_pairs]
        mean_p = sum(p_vals) / n
        mean_a = sum(a_vals) / n
        cov    = sum((p - mean_p) * (a - mean_a) for p, a in rank_corr_pairs) / n
        std_p  = math.sqrt(sum((p - mean_p) ** 2 for p in p_vals) / n)
        std_a  = math.sqrt(sum((a - mean_a) ** 2 for a in a_vals) / n)
        corr   = cov / (std_p * std_a) if std_p * std_a > 0 else 0
    else:
        corr = 0

    return {
        "mae":              round(mae),
        "rank_correlation": round(corr, 4),
        "n_samples":        len(samples),
    }


# ---------------------------------------------------------------------------
# Per-stage accuracy report
# ---------------------------------------------------------------------------

def stage_accuracy_report(
    samples: list[dict],
    result_stages: list[dict],
    skipped_stages: list[dict],
) -> list[dict]:
    """
    Build a per-stage accuracy breakdown.

    Actual winner is looked up directly from result_stages (not inferred
    from max(actual_pts) in the VS sample pool, which gives wrong answers
    when the real winner wasn't included in the VeloScore predictions).
    """
    results_by_stage: dict[int, dict] = {r["stage"]: r for r in result_stages}
    by_stage: dict[int, list] = {}
    for s in samples:
        by_stage.setdefault(s["stage"], []).append(s)

    report: list[dict] = []

    # ── Type-matched stages (used for calibration) ────────────────────────
    for stage_num in sorted(by_stage.keys()):
        stage_samples = by_stage[stage_num]
        stage_type    = stage_samples[0]["stage_type"]

        # VeloScore #1 pick
        vs_top = min(stage_samples, key=lambda x: x["vs_rank"])

        # Actual winner from results.json (rank == 1 entry)
        result           = results_by_stage.get(stage_num, {})
        actual_winner    = ""
        actual_winner_pts = 0
        for r in result.get("top_results", []):
            if r.get("rank") == 1:
                actual_winner     = r["rider"]
                actual_winner_pts = r.get("points") or 0
                break

        # Was VS #1 the actual winner?
        vs1_correct = (
            _norm(vs_top["rider"]) == _norm(actual_winner)
            if actual_winner else False
        )

        # Was the actual winner in VS top-5?
        sorted_vs   = sorted(stage_samples, key=lambda x: x["vs_rank"])
        top5_names  = [_norm(s["rider"]) for s in sorted_vs[:5]]
        winner_last = _norm(actual_winner).split()[-1] if actual_winner else ""
        winner_in_top5 = bool(actual_winner) and (
            _norm(actual_winner) in top5_names
            or any(winner_last == n.split()[-1] for n in top5_names)
        )

        # Average pts by VeloScore rank bucket
        bucket_pts: dict[str, list[int]] = {
            "1-3": [], "4-8": [], "9-15": [], "16+": []
        }
        for s in stage_samples:
            r = s["vs_rank"]
            if r <= 3:    bucket_pts["1-3"].append(s["actual_pts"])
            elif r <= 8:  bucket_pts["4-8"].append(s["actual_pts"])
            elif r <= 15: bucket_pts["9-15"].append(s["actual_pts"])
            else:         bucket_pts["16+"].append(s["actual_pts"])
        avg_by_bucket = {
            k: round(sum(v) / len(v)) if v else 0
            for k, v in bucket_pts.items()
        }

        report.append({
            "stage":              stage_num,
            "stage_type":         stage_type,
            "type_matched":       True,
            "vs_top_pick":        vs_top["rider"],
            "actual_winner":      actual_winner,
            "actual_winner_pts":  actual_winner_pts,
            "vs1_correct":        vs1_correct,
            "winner_in_vs_top5":  winner_in_top5,
            "avg_pts_by_vs_rank": avg_by_bucket,
        })

    # ── Skipped (type-mismatched / missing) stages ────────────────────────
    for skipped in skipped_stages:
        stage_num = skipped["stage"]
        result    = results_by_stage.get(stage_num, {})
        actual_winner     = ""
        actual_winner_pts = 0
        for r in result.get("top_results", []):
            if r.get("rank") == 1:
                actual_winner     = r["rider"]
                actual_winner_pts = r.get("points") or 0
                break

        report.append({
            "stage":             stage_num,
            "stage_type":        skipped.get("vs_type", "?"),
            "actual_type":       skipped.get("result_type", "?"),
            "type_matched":      False,
            "skipped_reason":    skipped["reason"],
            "vs_top_pick":       None,
            "actual_winner":     actual_winner,
            "actual_winner_pts": actual_winner_pts,
            "vs1_correct":       False,
            "winner_in_vs_top5": False,
            "avg_pts_by_vs_rank": {},
        })

    report.sort(key=lambda x: x["stage"])
    return report


# ---------------------------------------------------------------------------
# Weight optimisation
# ---------------------------------------------------------------------------

def optimise_weights(samples: list[dict]) -> dict:
    """
    Find the VeloScore weight that minimises MAE on the type-matched samples.
    Remaining weight is split to discipline / form / odds in a fixed ratio
    (those signals have no historical training data).
    """
    if not samples:
        print("  ADVARSEL: ingen kalibreringsprøver — bruger standardvagte")
        return {
            "veloscore":  0.45,
            "odds_prob":  0.25,
            "discipline": 0.20,
            "form":       0.10,
        }

    best_mae  = float("inf")
    best_w_vs = 0.45

    for w_vs_100 in range(20, 81, 5):
        w_vs    = w_vs_100 / 100.0
        metrics = compute_metrics(samples, {"veloscore": w_vs})
        if metrics["mae"] < best_mae:
            best_mae  = metrics["mae"]
            best_w_vs = w_vs

    remaining = 1.0 - best_w_vs
    weights   = {
        "veloscore":  round(best_w_vs,         3),
        "odds_prob":  round(remaining * 0.42,  3),
        "discipline": round(remaining * 0.35,  3),
        "form":       round(remaining * 0.23,  3),
    }

    print(f"  Optimale veloscore-vaegt: {best_w_vs:.2f}  (MAE: {best_mae:,.0f} kr)")
    print(f"  Fuld vaegtsaet: VS={weights['veloscore']}, "
          f"Odds={weights['odds_prob']}, "
          f"Disciplin={weights['discipline']}, "
          f"Form={weights['form']}")
    return weights


# ---------------------------------------------------------------------------
# Main calibration entry point
# ---------------------------------------------------------------------------

def run_calibration(verbose: bool = True) -> dict:
    """
    Load Giro 2026 data, calibrate weights, save results.
    Returns the calibrated weights dict.
    """
    print("\n" + "=" * 60)
    print("  KALIBRERING PAA GIRO 2026-DATA")
    print("=" * 60)

    vs_path  = DATA_DIR / "giro2026" / "veloscore.json"
    res_path = DATA_DIR / "giro2026" / "results.json"

    if not vs_path.exists() or not res_path.exists():
        raise FileNotFoundError(
            "Mangler giro2026/veloscore.json eller results.json"
        )

    vs_stages  = json.loads(vs_path.read_text(encoding="utf-8"))
    res_stages = json.loads(res_path.read_text(encoding="utf-8"))

    print(f"\n  VeloScore-etaper: {len(vs_stages)}")
    print(f"  Resultat-etaper:  {len(res_stages)}")

    # Build training samples (type-matched only)
    samples, skipped = build_training_data(vs_stages, res_stages)

    n_matched  = len({s["stage"] for s in samples})
    n_skipped  = len(skipped)
    print(f"  Type-matchede etaper:     {n_matched}")
    print(f"  Udeladte (type-mismatch): {n_skipped}")
    print(f"  Traeningspar:             {len(samples)}")

    if skipped and verbose:
        print("\n  Udeladte etaper:")
        for sk in sorted(skipped, key=lambda x: x["stage"]):
            print(f"    Etape {sk['stage']:>2}: {sk['reason']}")

    if not samples:
        print("\n  ADVARSEL: Ingen matchende data — bruger standardvagte.")
        return {
            "veloscore":  0.45,
            "odds_prob":  0.25,
            "discipline": 0.20,
            "form":       0.10,
        }

    # Per-stage accuracy report
    report = stage_accuracy_report(samples, res_stages, skipped)

    if verbose:
        print(f"\n  {'Etape':<6} {'Type':<10} {'M':<2} "
              f"{'VeloScore #1':<25} {'Faktisk vinder':<25} "
              f"{'VS1?':<5} {'Top5?'}")
        print(f"  {'-' * 90}")
        for r in report:
            m    = "J" if r["type_matched"] else "N"
            v1   = "J" if r.get("vs1_correct")      else "N"
            vt5  = "J" if r.get("winner_in_vs_top5") else "N"
            vs1  = r.get("vs_top_pick") or "(udeladt)"
            win  = r.get("actual_winner") or "?"
            note = f"  <- {r.get('skipped_reason','')[:40]}" if not r["type_matched"] else ""
            print(f"  {r['stage']:<6} {r['stage_type']:<10} {m:<2} "
                  f"{vs1:<25} {win:<25} {v1:<5} {vt5}{note}")

    # Summary stats (only type-matched stages)
    matched_report = [r for r in report if r["type_matched"]]
    n_stages      = len(matched_report)
    vs1_accuracy  = (
        sum(1 for r in matched_report if r["vs1_correct"]) / n_stages
        if n_stages else 0
    )
    top5_accuracy = (
        sum(1 for r in matched_report if r["winner_in_vs_top5"]) / n_stages
        if n_stages else 0
    )

    print(f"\n  -- VeloScore-nojagtighed (kun type-matchede etaper) --")
    n_vs1  = sum(1 for r in matched_report if r["vs1_correct"])
    n_top5 = sum(1 for r in matched_report if r["winner_in_vs_top5"])
    print(f"  VeloScore #1 vandt:       {vs1_accuracy  * 100:.0f}%"
          f" ({n_vs1}/{n_stages} etaper)")
    print(f"  Vinder i VeloScore top 5: {top5_accuracy * 100:.0f}%"
          f" ({n_top5}/{n_stages} etaper)")

    # Points by VeloScore rank bucket (type-matched only)
    all_buckets: dict[str, list[int]] = {
        "1-3": [], "4-8": [], "9-15": [], "16+": []
    }
    for r in matched_report:
        for bucket, avg in r["avg_pts_by_vs_rank"].items():
            if avg > 0:
                all_buckets[bucket].append(avg)
    print(f"\n  -- Gennemsnitspoint per VeloScore-ranggruppe --")
    for bucket, vals in all_buckets.items():
        avg = sum(vals) / len(vals) if vals else 0
        bar = "#" * int(avg / 20_000)
        print(f"  Rank {bucket:<6}: {avg:>9,.0f} kr  {bar}")

    # Optimise weights on type-matched data
    print("\n  -- Optimerer vagte --")
    weights = optimise_weights(samples)

    # Metrics comparison
    default_metrics = compute_metrics(samples, {"veloscore": 0.45})
    optim_metrics   = compute_metrics(samples, weights)
    print(f"\n  MAE (standardvagte w_vs=0.45): {default_metrics['mae']:>10,.0f} kr")
    print(f"  MAE (optimerede):              {optim_metrics['mae']:>10,.0f} kr")
    print(f"  Rank-korrelation (optimerede): {optim_metrics['rank_correlation']:>10.4f}")

    # Save calibrated weights
    weights_path = DATA_DIR / "calibrated_weights.json"
    weights_path.write_text(json.dumps(weights, indent=2), encoding="utf-8")
    print(f"\n  Kalibrerede vagte gemt -> {weights_path}")

    # Save full report
    full_report = {
        "weights":          weights,
        "metrics":          optim_metrics,
        "vs1_accuracy":     round(vs1_accuracy,  4),
        "top5_accuracy":    round(top5_accuracy, 4),
        "n_stages":         n_stages,
        "n_stages_skipped": n_skipped,
        "n_samples":        len(samples),
        "note": (
            "Kun etaper med matchende stage_type i veloscore.json og results.json "
            "bruges til kalibrering.  Udeladte etaper er stadig listet med "
            "type_matched=false."
        ),
        "per_stage": report,
    }
    report_path = DATA_DIR / "calibration_report.json"
    report_path.write_text(
        json.dumps(full_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  Fuld rapport gemt        -> {report_path}")
    print("=" * 60 + "\n")

    return weights


if __name__ == "__main__":
    run_calibration()
