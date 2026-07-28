#!/usr/bin/env python3
"""
Post-race evaluation — TdF 2026.

Which model indicators / sub-predictors best explained the ACTUAL holdet value
growth per rider per stage? Uses the fully-cached predictions file
(web/data/tdf2026_predictions.json), which carries, for every rider on every
stage: the 6 raw signals, the sub-model predictions, the composite expected
value, and the realised `actual` growth.

Outputs:
  data/analysis/indicator_eval.json   (machine-readable)
  prints a human summary

No network needed — all inputs are local.
"""
import json, math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRED = ROOT / "web/data/tdf2026_predictions.json"

# signals[] order, from src/predictor.py available_signals construction:
SIGNAL_NAMES = ["veloscore", "odds", "discipline", "form", "ml", "pcs_rank"]
SUBMODELS = ["exp", "holdet_est", "holdet_raw_pred", "placement_pred", "expected_pts"]


def spearman(xs, ys):
    """Spearman rank correlation with average-rank tie handling."""
    n = len(xs)
    if n < 3:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)))
    dy = math.sqrt(sum((ry[i] - my) ** 2 for i in range(n)))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def main():
    pred = json.loads(PRED.read_text())
    stages = pred["stages"]

    # ---- 1. Per-signal predictive power (per-stage Spearman, then averaged) ----
    sig_corr = {s: [] for s in SIGNAL_NAMES}
    sig_corr_by_type = {}
    sub_corr = {s: [] for s in SUBMODELS}

    for st in stages:
        typ = st["type"]
        riders = st["riders"]
        actual = [r.get("actual") or 0 for r in riders]

        # signals
        for idx, name in enumerate(SIGNAL_NAMES):
            vals = [(r.get("signals") or [0] * 6)[idx] if len(r.get("signals") or []) > idx else 0
                    for r in riders]
            if len(set(vals)) <= 1:   # signal absent / constant this stage
                continue
            c = spearman(vals, actual)
            if c is not None:
                sig_corr[name].append(c)
                sig_corr_by_type.setdefault(typ, {}).setdefault(name, []).append(c)

        # sub-models
        for name in SUBMODELS:
            vals = [r.get(name) or 0 for r in riders]
            if len(set(vals)) <= 1:
                continue
            c = spearman(vals, actual)
            if c is not None:
                sub_corr[name].append(c)

    def avg(xs):
        return round(sum(xs) / len(xs), 3) if xs else None

    signal_ranking = sorted(
        ((n, avg(v), len(v)) for n, v in sig_corr.items() if v),
        key=lambda t: -(t[1] or -9))
    submodel_ranking = sorted(
        ((n, avg(v), len(v)) for n, v in sub_corr.items() if v),
        key=lambda t: -(t[1] or -9))

    # ---- 2. Captain accuracy: model vs single-signal heuristics ----
    # For each stage, does the argmax of X land the actual best-growth rider?
    def argmax_name(riders, key):
        best = max(riders, key=lambda r: (r.get(key) or 0))
        return best["name"]

    def argmax_signal(riders, idx):
        best = max(riders, key=lambda r: (r.get("signals") or [0] * 6)[idx]
                   if len(r.get("signals") or []) > idx else 0)
        return best["name"]

    # NOTE: signals[0]=veloscore and signals[1]=odds are 0 on every row in this
    # web export (not persisted per-rider), so they are excluded here and
    # evaluated separately from the stage_XX_veloscore.json files below.
    strategies = {
        "model (exp)":     lambda rs: argmax_name(rs, "exp"),
        "form":            lambda rs: argmax_signal(rs, 3),
        "ml":              lambda rs: argmax_signal(rs, 4),
        "placement_pred":  lambda rs: argmax_name(rs, "placement_pred"),
    }
    cap_hits = {k: {"top1": 0, "top3": 0, "n": 0} for k in strategies}
    for st in stages:
        rs = st["riders"]
        ranked = sorted(rs, key=lambda r: -(r.get("actual") or 0))
        best1 = ranked[0]["name"]
        best3 = {ranked[i]["name"] for i in range(min(3, len(ranked)))}
        for k, fn in strategies.items():
            pick = fn(rs)
            cap_hits[k]["n"] += 1
            cap_hits[k]["top1"] += int(pick == best1)
            cap_hits[k]["top3"] += int(pick in best3)

    cap_summary = {
        k: {"top1_pct": round(100 * v["top1"] / v["n"], 1),
            "top3_pct": round(100 * v["top3"] / v["n"], 1),
            "top1": v["top1"], "n": v["n"]}
        for k, v in cap_hits.items()
    }
    cap_ranked = sorted(cap_summary.items(), key=lambda kv: -kv[1]["top1_pct"])

    # ---- 2b. VeloScore-as-a-source evaluation (stages with a stage JSON) ----
    # signals[0] wasn't persisted, so rate VeloScore directly: join the
    # transcribed stage_XX_veloscore.json (veloscore per rider) to actual growth.
    def norm(s):
        return "".join(c for c in (s or "").lower().strip() if c.isalnum() or c == " ")

    vs_corr, vs_cap_top1, vs_cap_top3, vs_n = [], 0, 0, 0
    for st in stages:
        num = st["num"]
        f = ROOT / f"data/stage_{num:02d}_veloscore.json"
        if not f.exists():
            continue
        vs = json.loads(f.read_text())
        rows = vs.get("predictions") or vs.get("predictors") or []
        vs_by_name = {norm(r.get("rider", "")): r.get("veloscore") for r in rows if r.get("rider")}
        act_by_name = {norm(r["name"]): (r.get("actual") or 0) for r in st["riders"]}
        pairs = [(vs_by_name[n], act_by_name[n]) for n in vs_by_name if n in act_by_name
                 and vs_by_name[n] is not None]
        if len(pairs) < 3:
            continue
        c = spearman([p[0] for p in pairs], [p[1] for p in pairs])
        if c is not None:
            vs_corr.append(c)
        # captain: does VeloScore's #1 land actual top1/top3 of the WHOLE stage?
        top_vs = max(rows, key=lambda r: (r.get("veloscore") or 0))["rider"]
        ranked = sorted(st["riders"], key=lambda r: -(r.get("actual") or 0))
        best1 = ranked[0]["name"]
        best3 = {ranked[i]["name"] for i in range(min(3, len(ranked)))}
        vs_cap_top1 += int(norm(top_vs) == norm(best1))
        vs_cap_top3 += int(norm(top_vs) in {norm(x) for x in best3})
        vs_n += 1

    veloscore_source = {
        "stages_covered": vs_n,
        "mean_spearman_vs_actual": avg(vs_corr),
        "captain_top1_pct": round(100 * vs_cap_top1 / vs_n, 1) if vs_n else None,
        "captain_top3_pct": round(100 * vs_cap_top3 / vs_n, 1) if vs_n else None,
    }

    # ---- 3. Overall model calibration (pooled exp vs actual) ----
    all_exp, all_act = [], []
    for st in stages:
        for r in st["riders"]:
            all_exp.append(r.get("exp") or 0)
            all_act.append(r.get("actual") or 0)
    overall = spearman(all_exp, all_act)

    out = {
        "note": "Post-race indicator evaluation, TdF 2026. Spearman rank corr vs "
                "actual holdet value growth. Signal corr = mean of per-stage "
                "correlations (stages where the signal was present).",
        "n_stages": len(stages),
        "n_rider_stage_rows": len(all_exp),
        "signal_ranking": [{"signal": n, "mean_spearman": c, "stages": k}
                           for n, c, k in signal_ranking],
        "submodel_ranking": [{"model": n, "mean_spearman": c, "stages": k}
                             for n, c, k in submodel_ranking],
        "captain_accuracy": cap_summary,
        "veloscore_source_eval": veloscore_source,
        "overall_exp_vs_actual_spearman_pooled": round(overall, 3) if overall else None,
        "signal_by_stage_type": {
            t: {n: avg(v) for n, v in d.items()}
            for t, d in sig_corr_by_type.items()
        },
    }
    outp = ROOT / "data/analysis/indicator_eval.json"
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # ---- human summary ----
    print(f"TdF 2026 — indicator evaluation ({len(stages)} stages, {len(all_exp)} rows)\n")
    print("SIGNAL predictive power (mean per-stage Spearman vs actual growth):")
    for n, c, k in signal_ranking:
        print(f"  {n:12s} {c:+.3f}   ({k} stages)")
    print("\nSUB-MODEL predictive power (mean per-stage Spearman):")
    for n, c, k in submodel_ranking:
        print(f"  {n:16s} {c:+.3f}   ({k} stages)")
    vse = veloscore_source
    print(f"\nVeloScore as a source ({vse['stages_covered']} stages w/ JSON): "
          f"mean Spearman {vse['mean_spearman_vs_actual']}, "
          f"captain top1 {vse['captain_top1_pct']}% / top3 {vse['captain_top3_pct']}%")
    print(f"\nOverall pooled exp vs actual Spearman: {out['overall_exp_vs_actual_spearman_pooled']}")
    print("\nCAPTAIN pick accuracy (argmax lands actual best / top-3 of stage):")
    for k, v in cap_ranked:
        print(f"  {k:16s} top1 {v['top1_pct']:5.1f}%   top3 {v['top3_pct']:5.1f}%   ({v['top1']}/{v['n']})")
    print("\nSIGNAL power by stage type (mean Spearman):")
    for t, d in out["signal_by_stage_type"].items():
        best = sorted(((n, c) for n, c in d.items() if c is not None), key=lambda x: -x[1])
        s = ", ".join(f"{n}:{c:+.2f}" for n, c in best[:3])
        print(f"  {t:9s} → {s}")
    print(f"\nWrote {outp.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
