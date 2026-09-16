#!/usr/bin/env python3
"""
Kilde-shootout — sammenligner de eksterne input-kilder mod HINANDEN på
realiseret holdet-vækst, uden nogen "model"-baseline. Kilder: Feltet.dk,
Simon K. Kjær (TheFantasyTool), VeloScore (ekstern konsensus) og Bookmaker-odds
(implied probability). For hver kilde: kaptajn-ramning (top1/top3) og en
"buys"-kvalitet (mean within-stage growth percentile af kildens top-8).

Ingen af disse kilder er "modellen" (exp/ML/signals) — den er bevidst udeladt
efter beslutning om at fokusere fremadrettet på at sammenligne rene kilder,
ikke på at slå eller bekræfte en intern model.

Outputs data/analysis/source_eval_<race>.json + printet sammendrag.
"""
import argparse
import json, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

RACES = {
    "vuelta2026": {
        "label": "Vuelta 2026",
        "pred": ROOT / "web/data/vuelta2026_predictions.json",
        "picks": {
            "feltet": ROOT / "data/analysis/optakt_picks_feltet_vuelta.json",
            "simon":  ROOT / "data/analysis/optakt_picks_simon_vuelta.json",
        },
        "veloscore": lambda n: ROOT / f"data/vuelta_stage_{n:02d}_veloscore.json",
        "odds": lambda n: ROOT / f"data/vuelta_stage_{n:02d}_odds.json",
        "out": ROOT / "data/analysis/source_eval_vuelta.json",
    },
}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum() or c == " ").strip()


def build_index(names, prominence):
    idx = {}
    for full in names:
        toks = norm(full).split()
        keys = {norm(full), toks[-1]}
        if len(toks) >= 2:
            keys.add(" ".join(toks[1:]))
            keys.add(" ".join(toks[-2:]))
        for k in keys:
            if not k:
                continue
            if k not in idx or prominence.get(full, 0) > prominence.get(idx[k], 0):
                idx[k] = full
    return idx


def match(name, idx):
    n = norm(name)
    if n in idx:
        return idx[n]
    toks = n.split()
    if toks and toks[-1] in idx:
        return idx[toks[-1]]
    if len(toks) >= 2 and " ".join(toks[-2:]) in idx:
        return idx[" ".join(toks[-2:])]
    return None


def stage_tables(st):
    rs = st["riders"]
    ranked = sorted(rs, key=lambda r: -(r.get("actual") or 0))
    best1 = ranked[0]["name"]
    best3 = {ranked[i]["name"] for i in range(min(3, len(ranked)))}
    n = len(rs)
    pct = {}
    for rank, r in enumerate(ranked):
        pct[r["name"]] = 1.0 - rank / (n - 1) if n > 1 else 1.0
    act = {r["name"]: (r.get("actual") or 0) for r in rs}
    return best1, best3, pct, act


def eval_picks_source(picks_path, stages, idx):
    if not picks_path.exists():
        return None
    picks = json.loads(picks_path.read_text())
    cap_top1 = cap_top3 = cap_n = 0
    buys_pcts = []
    per_stage = []
    umatch = set()
    for pk in picks:
        num = pk.get("stage")
        st = stages.get(num)
        if not st:
            continue
        best1, best3, pct, act = stage_tables(st)
        cap_name = pk.get("captain") or ""
        cap_rider = match(cap_name, idx) if cap_name else None
        if cap_name and not cap_rider:
            umatch.add(cap_name)
        row = {"stage": num}
        if cap_rider:
            cap_n += 1
            hit1 = cap_rider == best1
            hit3 = cap_rider in best3
            cap_top1 += hit1
            cap_top3 += hit3
            row.update(captain=cap_rider, cap_pct=round(pct.get(cap_rider, 0), 2))
        bpc = []
        for b in pk.get("buys", [])[:8]:
            rr = match(b, idx)
            if rr:
                bpc.append(pct.get(rr, 0))
            else:
                umatch.add(b)
        if bpc:
            buys_pcts.append(sum(bpc) / len(bpc))
        per_stage.append(row)
    return {
        "captain_top1_pct": round(100 * cap_top1 / cap_n, 1) if cap_n else None,
        "captain_top3_pct": round(100 * cap_top3 / cap_n, 1) if cap_n else None,
        "captain_stages": cap_n,
        "buys_mean_growth_percentile": round(sum(buys_pcts) / len(buys_pcts), 3) if buys_pcts else None,
        "buys_stages": len(buys_pcts),
        "unmatched_names": sorted(umatch),
    }


def eval_ranked_source(file_fn, rank_key, stages, idx, top_n_field):
    """VeloScore / odds: one JSON per stage with a ranked rider list."""
    cap_top1 = cap_top3 = cap_n = 0
    buys_pcts = []
    stages_covered = 0
    umatch = set()
    for num, st in stages.items():
        f = file_fn(num)
        if not f.exists():
            continue
        raw = json.loads(f.read_text())
        if rank_key == "veloscore":
            rows = raw.get("predictions") or raw.get("predictors") or []
            ranked_names = [r.get("rider") for r in sorted(rows, key=lambda r: -(r.get("veloscore") or 0))]
        else:  # odds: dict name(lowercase) -> implied prob
            odds = raw.get("odds") or {}
            ranked_names = [n for n, _ in sorted(odds.items(), key=lambda kv: -kv[1])]
        if not ranked_names:
            continue
        stages_covered += 1
        best1, best3, pct, act = stage_tables(st)
        cap_name = ranked_names[0]
        cap_rider = match(cap_name, idx)
        if not cap_rider:
            umatch.add(cap_name)
            continue
        cap_n += 1
        cap_top1 += cap_rider == best1
        cap_top3 += cap_rider in best3
        bpc = []
        for b in ranked_names[:8]:
            rr = match(b, idx)
            if rr:
                bpc.append(pct.get(rr, 0))
        if bpc:
            buys_pcts.append(sum(bpc) / len(bpc))
    return {
        "captain_top1_pct": round(100 * cap_top1 / cap_n, 1) if cap_n else None,
        "captain_top3_pct": round(100 * cap_top3 / cap_n, 1) if cap_n else None,
        "captain_stages": cap_n,
        "stages_with_data": stages_covered,
        "buys_mean_growth_percentile": round(sum(buys_pcts) / len(buys_pcts), 3) if buys_pcts else None,
        "buys_stages": len(buys_pcts),
        "unmatched_names": sorted(umatch),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--race", default="vuelta2026", choices=sorted(RACES))
    args = ap.parse_args()
    cfg = RACES[args.race]

    pred = json.loads(cfg["pred"].read_text())
    all_stages = pred["stages"]
    usable = [s for s in all_stages if any((r.get("actual") or 0) for r in s["riders"])]
    skipped = [s["num"] for s in all_stages if s not in usable]
    stages = {s["num"]: s for s in usable}
    names = {r["name"] for s in usable for r in s["riders"]}
    prominence = {}
    for s in usable:
        for r in s["riders"]:
            prominence[r["name"]] = prominence.get(r["name"], 0) + (r.get("actual") or 0)
    idx = build_index(names, prominence)

    results = {}
    for src, path in cfg["picks"].items():
        r = eval_picks_source(path, stages, idx)
        if r:
            results[src] = r
    results["veloscore"] = eval_ranked_source(cfg["veloscore"], "veloscore", stages, idx, 8)
    results["odds"] = eval_ranked_source(cfg["odds"], "odds", stages, idx, 8)

    out = {
        "note": f"Kilde-shootout, {cfg['label']}. Ingen model-baseline — kun eksterne kilder mod hinanden "
                "og mod realiseret holdet-vækst. buys percentile: 1.0=bedst i feltet, 0.5=feltets gennemsnit.",
        "skipped_stages_no_actual": skipped,
        "sources": results,
    }
    cfg["out"].write_text(json.dumps(out, indent=2, ensure_ascii=False))

    print(f"KILDE-SHOOTOUT — {cfg['label']} (ingen model-baseline)\n")
    print(f"{'kilde':14s} {'cap top1':>9s} {'cap top3':>9s} {'stages':>7s} {'buys pct':>9s}")
    order = sorted(results.items(), key=lambda kv: -(kv[1].get("captain_top1_pct") or 0))
    for src, r in order:
        print(f"{src:14s} {r.get('captain_top1_pct') or 0:>8.1f}% {r.get('captain_top3_pct') or 0:>8.1f}% "
              f"{r.get('captain_stages') or 0:>7} {r.get('buys_mean_growth_percentile') or 0:>9.3f}")
    print(f"\nWrote {cfg['out'].relative_to(ROOT)}")


if __name__ == "__main__":
    main()
