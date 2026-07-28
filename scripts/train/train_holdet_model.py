"""
train_holdet_model.py
Træner LightGBM til at predicte normaliserede Holdet-point (0-100 pr. etape).

Input:
  data/ml/holdet_training_data.csv        — bygget af build_holdet_training_data.py
  data/ml/holdet_training_data_meta.json

Output:
  data/ml/holdet_model.lgbm              — trænet regressionsmodel
  web/data/holdet_ml_validation.json     — validerings-data til web-UI

Validering:
  Leave-one-race-out (LORO): 3 fold (GiroCV, TdFCV, VueltaCV)
  Metric: Spearman-korrelation pr. etape + top-scorer accuracy

Usage:
    python train_holdet_model.py
    python train_holdet_model.py --no-save
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).parent.parent.parent
ML_DIR  = ROOT / "data" / "ml"
WEB_DIR = ROOT / "web" / "data"

CSV_IN   = ML_DIR / "holdet_training_data.csv"
META_IN  = ML_DIR / "holdet_training_data_meta.json"
MODEL_OUT = ML_DIR / "holdet_model.lgbm"
VALID_OUT = WEB_DIR / "holdet_ml_validation.json"

TARGET = "holdet_pts_norm"   # 0-100 normaliseret inden for etapefelt

LGB_PARAMS = {
    "objective":         "regression",
    "metric":            "rmse",
    "verbosity":         -1,
    "n_estimators":      500,
    "learning_rate":     0.04,
    "num_leaves":        31,
    "min_child_samples": 15,
    "subsample":         0.8,
    "colsample_bytree":  0.8,
    "reg_alpha":         0.1,
    "reg_lambda":        1.0,
    "random_state":      42,
}


def stage_spearman(df: pd.DataFrame, pred_col: str = "pred") -> list[float]:
    """Return Spearman rho per stage (rank correlation predicted vs actual pts)."""
    rhos = []
    for _, grp in df.groupby(["race", "stage"]):
        if len(grp) < 5:
            continue
        rho, _ = spearmanr(grp[pred_col], grp[TARGET])
        if not np.isnan(rho):
            rhos.append(rho)
    return rhos


def top_scorer_accuracy(df: pd.DataFrame, pred_col: str = "pred", k: int = 5) -> float:
    """Fraction of stages where ≥1 of top-k predicted riders is in actual top-k scorers."""
    hits = 0
    total = 0
    for _, grp in df.groupby(["race", "stage"]):
        if len(grp) < k * 2:
            continue
        pred_top = set(grp.nlargest(k, pred_col)["rider_id"])
        actual_top = set(grp.nlargest(k, TARGET)["rider_id"])
        hits += int(bool(pred_top & actual_top))
        total += 1
    return hits / total if total else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    if not CSV_IN.exists():
        print(f"Fejl: {CSV_IN} ikke fundet.")
        print("Kør først: python build_holdet_training_data.py")
        return

    meta = json.loads(META_IN.read_text(encoding="utf-8"))
    df   = pd.read_csv(CSV_IN, dtype={"year": int, "stage": int})
    feat = meta["feature_cols"]

    races = sorted(df["race"].unique())
    print(f"Data: {len(df):,} rækker  |  {len(feat)} features  |  løb: {races}")
    print(f"Target '{TARGET}': mean={df[TARGET].mean():.1f}  std={df[TARGET].std():.1f}\n")

    # ── Leave-one-race-out cross-validation ───────────────────────────────────
    loro_results: list[dict] = []
    oof_df = df.copy()
    oof_df["pred"] = np.nan

    for val_race in races:
        train_df = df[df["race"] != val_race].copy()
        val_df   = df[df["race"] == val_race].copy()

        model_cv = lgb.LGBMRegressor(**LGB_PARAMS)
        model_cv.fit(
            train_df[feat], train_df[TARGET],
            eval_set=[(val_df[feat], val_df[TARGET])],
            callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)],
        )

        val_df["pred"] = model_cv.predict(val_df[feat])
        oof_df.loc[val_df.index, "pred"] = val_df["pred"]

        rhos   = stage_spearman(val_df)
        top5acc = top_scorer_accuracy(val_df)
        rmse   = np.sqrt(((val_df["pred"] - val_df[TARGET]) ** 2).mean())

        result = {
            "val_race":        val_race,
            "n_val":           len(val_df),
            "mean_spearman":   round(float(np.mean(rhos)), 4),
            "top5_accuracy":   round(top5acc, 4),
            "rmse":            round(float(rmse), 2),
            "n_stages":        val_df["stage"].nunique(),
        }

        # Per-stage-type breakdown
        for stype in ["sprint", "mountain", "hilly", "tt"]:
            sdf = val_df[val_df["stage_type"] == stype]
            if len(sdf) >= 10:
                rhos_s = stage_spearman(sdf)
                acc_s  = top_scorer_accuracy(sdf)
                result[f"spearman_{stype}"] = round(float(np.mean(rhos_s)), 4) if rhos_s else None
                result[f"top5acc_{stype}"]  = round(acc_s, 4)
            else:
                result[f"spearman_{stype}"] = None
                result[f"top5acc_{stype}"]  = None

        loro_results.append(result)
        print(f"LORO val={val_race:8s}:  "
              f"spearman={result['mean_spearman']:.3f}  "
              f"top5acc={result['top5_accuracy']:.2%}  "
              f"rmse={result['rmse']:.1f}")

    print(f"\nGns. over {len(races)} fold:")
    print(f"  Spearman:     {np.mean([r['mean_spearman'] for r in loro_results]):.3f}")
    print(f"  Top-5 acc:    {np.mean([r['top5_accuracy'] for r in loro_results]):.2%}")
    print(f"  RMSE:         {np.mean([r['rmse'] for r in loro_results]):.1f}")

    # ── Træn final model på alt data ─────────────────────────────────────────
    print(f"\nTræner final model på alle {len(df):,} rækker…")
    final_model = lgb.LGBMRegressor(**LGB_PARAMS)
    final_model.fit(df[feat], df[TARGET],
                    callbacks=[lgb.log_evaluation(0)])

    # Feature importance
    importances = (
        pd.Series(final_model.feature_importances_, index=feat)
        .sort_values(ascending=False)
    )
    imp_list = [{"feature": k, "importance": int(v)} for k, v in importances.items()]
    print("\nTop-10 features:")
    max_imp = max(r["importance"] for r in imp_list) if imp_list else 1
    for row in imp_list[:10]:
        bar = "█" * (row["importance"] * 30 // max_imp)
        print(f"  {row['feature']:<25} {row['importance']:>5}  {bar}")

    # Save model
    if not args.no_save:
        ML_DIR.mkdir(parents=True, exist_ok=True)
        final_model.booster_.save_model(str(MODEL_OUT))
        print(f"\nModel gemt: {MODEL_OUT}")

    # Save validation JSON
    val_out = {
        "model":             "holdet_model.lgbm",
        "target":            TARGET,
        "n_train_total":     len(df),
        "n_features":        len(feat),
        "loro_results":      loro_results,
        "avg_spearman":      round(float(np.mean([r["mean_spearman"] for r in loro_results])), 4),
        "avg_top5_accuracy": round(float(np.mean([r["top5_accuracy"] for r in loro_results])), 4),
        "avg_rmse":          round(float(np.mean([r["rmse"] for r in loro_results])), 2),
        "feature_importances": imp_list,
        "notes": (
            "Spearman: rang-korrelation pr. etape (1.0 = perfekt rang-order). "
            "Top5_accuracy: andel etaper hvor ≥1 af top-5 predicted er i faktisk top-5. "
            "LORO = Leave-One-Race-Out: trænet på 2 løb, testet på det 3."
        ),
        "generated": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    VALID_OUT.write_text(json.dumps(val_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Validerings-JSON gemt: {VALID_OUT}")


if __name__ == "__main__":
    main()
