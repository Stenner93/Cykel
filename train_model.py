"""
Træn LightGBM-model på historiske GT-etaperesultater.

Input:
  data/ml/training_data.csv       — bygget af build_training_data.py
  data/ml/training_data_meta.json — kolonnemeta

Output:
  data/ml/model.lgbm              — trænet model
  web/data/ml_validation.json     — validerings-data til web-UI

Strategi:
  - Træn på 2021-2024, valider på 2025
  - Target: top5-klassifikation (binær)
  - Metric: ROC-AUC pr. etape-type + overall
  - Feature importances eksporteres til web

Usage:
    python train_model.py
    python train_model.py --train-years 2021 2022 2023 2024
    python train_model.py --val-year 2025
    python train_model.py --no-save   # valider kun, gem ikke model
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score

ROOT    = Path(__file__).parent
ML_DIR  = ROOT / "data" / "ml"
WEB_DIR = ROOT / "web" / "data"
CACHE_DIR   = ROOT / "data" / "cache"
CSV_IN  = ML_DIR / "training_data.csv"
META_IN = ML_DIR / "training_data_meta.json"
MODEL_OUT   = ML_DIR / "model.lgbm"
VALID_OUT   = WEB_DIR / "ml_validation.json"
HIST_FORM_OUT = CACHE_DIR / "rider_historical_form.json"

LGB_PARAMS = {
    "objective":        "binary",
    "metric":           "auc",
    "verbosity":        -1,
    "n_estimators":     400,
    "learning_rate":    0.05,
    "num_leaves":       31,
    "min_child_samples": 20,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "reg_alpha":        0.1,
    "reg_lambda":       1.0,
    "random_state":     42,
}


def load_data(meta: dict) -> pd.DataFrame:
    df = pd.read_csv(CSV_IN, dtype={"year": int, "stage": int})
    return df


def eval_predictions(df: pd.DataFrame, pred_col: str = "pred") -> dict:
    """Compute AUC / top-k accuracy metrics for a val set."""
    results = {}

    # Overall
    if df["top5"].nunique() > 1:
        results["auc_top5"] = round(roc_auc_score(df["top5"], df[pred_col]), 4)
        results["ap_top5"]  = round(average_precision_score(df["top5"], df[pred_col]), 4)

    # Per stage type
    for stype in ["sprint", "mountain", "hilly", "tt"]:
        sub = df[df["stage_type"] == stype]
        if len(sub) > 20 and sub["top5"].nunique() > 1:
            results[f"auc_{stype}"] = round(
                roc_auc_score(sub["top5"], sub[pred_col]), 4
            )

    return results


def stage_top_predictions(df: pd.DataFrame, n: int = 5) -> list[dict]:
    """Return per-stage top-n predicted riders vs actual result."""
    out = []
    for (race, year, stage), grp in df.groupby(["race", "year", "stage"], sort=False):
        grp_s = grp.sort_values("pred", ascending=False)
        predicted_top = grp_s.head(n)["rider_slug"].tolist()
        actual_winner = grp[grp["position"] == 1]["rider_slug"].tolist()
        actual_top5   = grp[grp["top5"] == 1]["rider_slug"].tolist()

        # How many of top-5 actual finishers did we predict in top-n?
        overlap = len(set(predicted_top) & set(actual_top5))

        out.append({
            "race":     race,
            "year":     year,
            "stage":    int(stage),
            "stype":    grp["stage_type"].iloc[0],
            "predicted_top5": predicted_top,
            "actual_winner":  actual_winner[:1],
            "actual_top5":    actual_top5[:5],
            "top5_overlap":   overlap,
            "winner_in_pred": int(bool(set(actual_winner) & set(predicted_top))),
        })
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-years", nargs="+", type=int,
                        default=[2021, 2022, 2023, 2024])
    parser.add_argument("--val-year",   type=int, default=2025)
    parser.add_argument("--no-save",    action="store_true")
    args = parser.parse_args()

    if not CSV_IN.exists():
        print(f"Fejl: {CSV_IN} ikke fundet.")
        print("Kør først:\n  python scrape_pcs_history.py\n  python build_training_data.py")
        return

    meta = json.loads(META_IN.read_text(encoding="utf-8"))
    df   = load_data(meta)
    feat = meta["feature_cols"]

    # Split
    train = df[df["year"].isin(args.train_years)].copy()
    val   = df[df["year"] == args.val_year].copy()

    print(f"Træningsdata:   {len(train):,} rækker  ({', '.join(str(y) for y in args.train_years)})")
    print(f"Valideringsdata:{len(val):,} rækker  ({args.val_year})")
    print(f"Features:       {len(feat)}")
    print(f"Top-5 rate:     {train['top5'].mean():.2%}")

    # Train
    model = lgb.LGBMClassifier(**LGB_PARAMS)
    model.fit(
        train[feat], train["top5"],
        eval_set=[(val[feat], val["top5"])],
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)],
    )

    # Predict
    val["pred"]   = model.predict_proba(val[feat])[:, 1]
    train["pred"] = model.predict_proba(train[feat])[:, 1]

    # Metrics
    train_metrics = eval_predictions(train)
    val_metrics   = eval_predictions(val)

    print("\nTrænings-metrics (in-sample):")
    for k, v in train_metrics.items():
        print(f"  {k:<20} {v:.4f}")
    print("\nValiderings-metrics (2025, out-of-sample):")
    for k, v in val_metrics.items():
        print(f"  {k:<20} {v:.4f}")

    # Feature importances
    importances = (
        pd.Series(model.feature_importances_, index=feat)
        .sort_values(ascending=False)
    )
    imp_list = [
        {"feature": k, "importance": int(v)}
        for k, v in importances.items()
    ]
    print(f"\nTop-10 features:")
    for row in imp_list[:10]:
        bar = "█" * (row["importance"] * 30 // max(imp_list[0]["importance"], 1))
        print(f"  {row['feature']:<25} {row['importance']:>5}  {bar}")

    # Per-stage predictions (val only)
    stage_preds = stage_top_predictions(val)

    # Winner-in-top5-prediction rate
    winner_hit_rate = (
        sum(s["winner_in_pred"] for s in stage_preds) / len(stage_preds)
        if stage_preds else 0.0
    )
    avg_top5_overlap = (
        sum(s["top5_overlap"] for s in stage_preds) / len(stage_preds)
        if stage_preds else 0.0
    )
    print(f"\nEtapevinder i top-5 pred:  {winner_hit_rate:.1%}  ({sum(s['winner_in_pred'] for s in stage_preds)}/{len(stage_preds)} etaper)")
    print(f"Gns. overlap i top-5:      {avg_top5_overlap:.2f}/5")

    # Save model
    if not args.no_save:
        ML_DIR.mkdir(parents=True, exist_ok=True)
        model.booster_.save_model(str(MODEL_OUT))
        print(f"\nModel gemt: {MODEL_OUT}")

    # Save recency-weighted historical form per rider (for ML signal on stage 1)
    # Weights: 2025=5, 2024=3, 2023=2, 2022=1, 2021=1
    YEAR_W = {2025: 5, 2024: 3, 2023: 2, 2022: 1, 2021: 1}
    hist_form: dict[str, dict] = {}
    for slug, grp in df[~df["dnf"].astype(bool)].groupby("rider_slug"):
        entry: dict[str, float] = {}
        for stype in ["sprint", "mountain", "hilly", "tt", "cobbled"]:
            sub = grp[grp["stage_type"].isin([stype] + (["ttt"] if stype == "tt" else []))]
            if len(sub) >= 3:  # kræver mindst 3 resultater for pålidelighed
                weights = sub["year"].map(lambda y: YEAR_W.get(y, 1))
                entry[stype] = round(float((sub["position"] * weights).sum() / weights.sum()), 1)
        if entry:
            hist_form[slug] = entry
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    HIST_FORM_OUT.write_text(json.dumps(hist_form, ensure_ascii=False), encoding="utf-8")
    print(f"Historisk form gemt: {HIST_FORM_OUT}  ({len(hist_form)} ryttere)")

    # Save validation JSON for web UI
    val_out = {
        "trained_on_years": args.train_years,
        "validated_on_year": args.val_year,
        "n_train": len(train),
        "n_val":   len(val),
        "n_features": len(feat),
        "train_metrics": train_metrics,
        "val_metrics":   val_metrics,
        "feature_importances": imp_list,
        "winner_hit_rate":     round(winner_hit_rate, 4),
        "avg_top5_overlap":    round(avg_top5_overlap, 4),
        "stage_predictions":   stage_preds,
        "generated": pd.Timestamp.now(tz="UTC").isoformat(),
    }

    WEB_DIR.mkdir(parents=True, exist_ok=True)
    VALID_OUT.write_text(json.dumps(val_out, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print(f"Validerings-JSON gemt: {VALID_OUT}")


if __name__ == "__main__":
    main()
