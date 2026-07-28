"""
train_placement_model.py

Træner LightGBM regression til at predicte normaliseret etapeplacing (norm_pos).

  norm_pos: 1.0 = vinderen · 0.0 = sidst placerede

Input:
  data/ml/placement_training_data.csv
  data/ml/placement_training_data_meta.json

Output:
  data/ml/placement_model.lgbm       — trænet model
  web/data/placement_ml_validation.json

Validering:
  Leave-one-race-out (LORO) på GT-løbene (tdf/giro/vuelta).
  Metric: Spearman-korrelation pr. etape + top-scorer accuracy.

Usage:
    python train_placement_model.py
    python train_placement_model.py --no-save
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

CSV_IN    = ML_DIR / "placement_training_data.csv"
META_IN   = ML_DIR / "placement_training_data_meta.json"
MODEL_OUT = ML_DIR / "placement_model.lgbm"
VALID_OUT = WEB_DIR / "placement_ml_validation.json"

TARGET = "norm_pos"

LGB_PARAMS = {
    "objective":         "regression",
    "metric":            "rmse",
    "verbosity":         -1,
    "n_estimators":      800,
    "learning_rate":     0.03,
    "num_leaves":        47,
    "min_child_samples": 20,
    "subsample":         0.8,
    "colsample_bytree":  0.8,
    "reg_alpha":         0.1,
    "reg_lambda":        1.0,
    "random_state":      42,
}

# GT-løb bruges til LORO-CV — et-ugersløb bruges kun som træning
GT_RACES = {"tdf", "giro", "vuelta"}


def stage_spearman(df: pd.DataFrame, pred_col: str = "pred") -> list[float]:
    rhos = []
    for _, grp in df.groupby(["race", "year", "stage"]):
        if len(grp) < 5:
            continue
        rho, _ = spearmanr(grp[pred_col], grp[TARGET])
        if not np.isnan(rho):
            rhos.append(rho)
    return rhos


def top_scorer_accuracy(df: pd.DataFrame, pred_col: str = "pred", k: int = 5) -> float:
    hits = 0
    total = 0
    for _, grp in df.groupby(["race", "year", "stage"]):
        if len(grp) < k * 2:
            continue
        pred_top   = set(grp.nlargest(k, pred_col)["rider_slug"])
        actual_top = set(grp.nlargest(k, TARGET)["rider_slug"])
        hits  += int(bool(pred_top & actual_top))
        total += 1
    return hits / total if total else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    if not CSV_IN.exists():
        print(f"Fejl: {CSV_IN} ikke fundet.")
        print("Kør først: python build_placement_training_data.py")
        return

    meta = json.loads(META_IN.read_text(encoding="utf-8"))
    df   = pd.read_csv(CSV_IN, dtype={"year": int, "stage": int})
    feat = meta["feature_cols"]

    # slug_id as categorical so LightGBM can learn rider-specific patterns
    if "slug_id" in feat:
        df["slug_id"] = df["slug_id"].astype("category")

    races_all = sorted(df["race"].unique())
    print(f"Data: {len(df):,} rækker  |  {len(feat)} features")
    print(f"Løb: {races_all}")
    print(f"År: {sorted(df['year'].unique())}")
    print(f"Target '{TARGET}': mean={df[TARGET].mean():.3f}  std={df[TARGET].std():.3f}\n")

    for race, cnt in df.groupby("race").size().items():
        print(f"  {race:<12}: {cnt:>6,} rækker")
    print()

    # ── Leave-one-GT-race-out cross-validation ────────────────────────────────
    gt_df    = df[df["race"].isin(GT_RACES)].copy()
    extra_df = df[~df["race"].isin(GT_RACES)].copy()
    gt_races = sorted(gt_df["race"].unique())

    loro_results: list[dict] = []

    for val_race in gt_races:
        train_df = pd.concat([
            gt_df[gt_df["race"] != val_race],
            extra_df,
        ], ignore_index=True)
        val_df = gt_df[gt_df["race"] == val_race].copy()

        if "slug_id" in feat:
            train_df["slug_id"] = train_df["slug_id"].astype("category")
            val_df["slug_id"]   = val_df["slug_id"].astype("category")

        cat_feat = ["slug_id"] if "slug_id" in feat else "auto"
        model_cv = lgb.LGBMRegressor(**LGB_PARAMS)
        model_cv.fit(
            train_df[feat], train_df[TARGET],
            eval_set=[(val_df[feat], val_df[TARGET])],
            categorical_feature=cat_feat,
            callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)],
        )

        val_df["pred"] = model_cv.predict(val_df[feat])

        rhos    = stage_spearman(val_df)
        top5acc = top_scorer_accuracy(val_df)
        rmse    = np.sqrt(((val_df["pred"] - val_df[TARGET]) ** 2).mean())

        result = {
            "val_race":      val_race,
            "n_train":       len(train_df),
            "n_val":         len(val_df),
            "mean_spearman": round(float(np.mean(rhos)), 4),
            "top5_accuracy": round(top5acc, 4),
            "rmse":          round(float(rmse), 4),
            "n_stages":      val_df[["year", "stage"]].drop_duplicates().shape[0],
        }

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
              f"rmse={result['rmse']:.4f}")

    print(f"\nGns. over {len(gt_races)} GT-fold:")
    print(f"  Spearman:  {np.mean([r['mean_spearman'] for r in loro_results]):.3f}")
    print(f"  Top-5 acc: {np.mean([r['top5_accuracy'] for r in loro_results]):.2%}")
    print(f"  RMSE:      {np.mean([r['rmse'] for r in loro_results]):.4f}")

    # ── Træn combined final model på ALLE data ─────────────────────────────────
    print(f"\nTræner combined model på alle {len(df):,} rækker…")
    cat_feat = ["slug_id"] if "slug_id" in feat else "auto"
    final_model = lgb.LGBMRegressor(**LGB_PARAMS)
    final_model.fit(df[feat], df[TARGET],
                    categorical_feature=cat_feat,
                    callbacks=[lgb.log_evaluation(0)])

    importances = (
        pd.Series(final_model.feature_importances_, index=feat)
        .sort_values(ascending=False)
    )
    imp_list = [{"feature": k, "importance": int(v)} for k, v in importances.items()]
    print("\nTop-10 features (combined model):")
    max_imp = max(r["importance"] for r in imp_list) if imp_list else 1
    for row in imp_list[:10]:
        bar = "█" * (row["importance"] * 30 // max_imp)
        print(f"  {row['feature']:<25} {row['importance']:>5}  {bar}")

    if not args.no_save:
        ML_DIR.mkdir(parents=True, exist_ok=True)
        final_model.booster_.save_model(str(MODEL_OUT))
        print(f"\nCombined model gemt: {MODEL_OUT}")

    # ── Træn separate modeller per etapetype ──────────────────────────────────
    STAGE_TYPES = ["sprint", "mountain", "hilly", "tt"]
    type_imp_lists: dict[str, list] = {}

    for stype in STAGE_TYPES:
        sdf = df[df["stage_type"] == stype].copy()
        if len(sdf) < 200:
            print(f"\n[!] For få rækker til {stype}-model ({len(sdf)}) — springer over")
            continue

        print(f"\nTræner {stype}-model på {len(sdf):,} rækker…")
        if "slug_id" in feat:
            sdf["slug_id"] = sdf["slug_id"].astype("category")

        m = lgb.LGBMRegressor(**LGB_PARAMS)
        m.fit(sdf[feat], sdf[TARGET],
              categorical_feature=cat_feat,
              callbacks=[lgb.log_evaluation(0)])

        type_imps = (
            pd.Series(m.feature_importances_, index=feat)
            .sort_values(ascending=False)
        )
        type_imp_list = [{"feature": k, "importance": int(v)} for k, v in type_imps.items()]
        type_imp_lists[stype] = type_imp_list

        top3 = ", ".join(f"{r['feature']}({r['importance']})" for r in type_imp_list[:3])
        print(f"  Top-3: {top3}")

        if not args.no_save:
            out_path = ML_DIR / f"placement_{stype}_model.lgbm"
            m.booster_.save_model(str(out_path))
            print(f"  Gemt: {out_path}")

    val_out = {
        "model":             "placement_model.lgbm",
        "target":            TARGET,
        "n_train_total":     len(df),
        "n_features":        len(feat),
        "min_year":          meta.get("min_year", "alle"),
        "loro_results":      loro_results,
        "avg_spearman":      round(float(np.mean([r["mean_spearman"] for r in loro_results])), 4),
        "avg_top5_accuracy": round(float(np.mean([r["top5_accuracy"] for r in loro_results])), 4),
        "avg_rmse":          round(float(np.mean([r["rmse"] for r in loro_results])), 4),
        "feature_importances": imp_list,
        "type_feature_importances": type_imp_lists,
        "races_in_training": races_all,
        "notes": (
            "norm_pos=1.0=vinder, 0.0=sidst. LORO CV på GT-løb (tdf/giro/vuelta). "
            "Separate modeller pr. etapetype: placement_{type}_model.lgbm. "
            "CO-blending i prediction (ml_signal.py) kompenserer for lav CO-dækning i træning."
        ),
        "generated": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    VALID_OUT.write_text(json.dumps(val_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nValiderings-JSON gemt: {VALID_OUT}")


if __name__ == "__main__":
    main()
