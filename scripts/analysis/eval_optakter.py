#!/usr/bin/env python3
"""
Post-race evaluation — did the OPTAKTER (qualitative expert previews) beat our
model on actual holdet growth?

Inputs:
  data/analysis/optakt_picks_feltet.json   (extracted picks, per stage)
  data/analysis/optakt_picks_simon.json
  web/data/tdf2026_predictions.json         (actual growth + model exp)

For each stage we compare, on the realised `actual` growth:
  - captain hit-rate (does the source's captain land the stage's actual top-1 / top-3)
  - "buys" quality: mean within-stage growth percentile of the recommended riders
    (0.5 = field average, 1.0 = best in field)
  - head-to-head captain: source's captain actual growth vs model's captain

Prints a summary and writes data/analysis/optakt_eval.json.
"""
import json, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRED = ROOT / "web/data/tdf2026_predictions.json"
PICKS = {
    "feltet": ROOT / "data/analysis/optakt_picks_feltet.json",
    "simon":  ROOT / "data/analysis/optakt_picks_simon.json",
}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum() or c == " ").strip()


def build_index(names, prominence):
    """Map surname / full-name keys -> canonical full name (ambiguity → most prominent)."""
    idx = {}
    for full in names:
        toks = norm(full).split()
        keys = {norm(full), toks[-1]}
        if len(toks) >= 2:
            keys.add(" ".join(toks[1:]))       # everything after first name
            keys.add(" ".join(toks[-2:]))       # last two (van der Poel → der poel? keep)
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


def main():
    pred = json.loads(PRED.read_text())
    stages = {s["num"]: s for s in pred["stages"]}
    names = {r["name"] for s in pred["stages"] for r in s["riders"]}
    prominence = {}
    for s in pred["stages"]:
        for r in s["riders"]:
            prominence[r["name"]] = prominence.get(r["name"], 0) + (r.get("actual") or 0)
    idx = build_index(names, prominence)

    # model captain per stage (argmax exp) + its actual
    def stage_tables(st):
        rs = st["riders"]
        ranked = sorted(rs, key=lambda r: -(r.get("actual") or 0))
        best1 = ranked[0]["name"]
        best3 = {ranked[i]["name"] for i in range(min(3, len(ranked)))}
        n = len(rs)
        # within-stage growth percentile (1.0 = best)
        pct = {}
        for rank, r in enumerate(ranked):
            pct[r["name"]] = 1.0 - rank / (n - 1) if n > 1 else 1.0
        act = {r["name"]: (r.get("actual") or 0) for r in rs}
        return best1, best3, pct, act

    results = {}
    unmatched = {}
    for src, path in PICKS.items():
        if not path.exists():
            print(f"[skip] {src}: {path.name} not found (run the extraction agent first)")
            continue
        picks = json.loads(path.read_text())
        cap_top1 = cap_top3 = cap_n = 0
        buys_pcts = []
        h2h_win = h2h_n = 0
        diff_win = diff_loss = diff_tie = 0   # only stages where cap != model cap
        umatch = set()
        per_stage = []
        for pk in picks:
            num = pk.get("stage")
            st = stages.get(num)
            if not st:
                continue
            best1, best3, pct, act = stage_tables(st)
            model_cap = max(st["riders"], key=lambda r: (r.get("exp") or 0))["name"]

            cap_name = pk.get("captain") or ""
            cap_rider = match(cap_name, idx) if cap_name else None
            if cap_name and not cap_rider:
                umatch.add(cap_name)
            row = {"stage": num, "captain_raw": cap_name, "captain_rider": cap_rider}
            if cap_rider:
                cap_n += 1
                hit1 = cap_rider == best1
                hit3 = cap_rider in best3
                cap_top1 += hit1
                cap_top3 += hit3
                row.update(cap_pct=round(pct.get(cap_rider, 0), 2), cap_top1=hit1, cap_top3=hit3)
                # head-to-head vs model captain
                h2h_n += 1
                if act.get(cap_rider, 0) > act.get(model_cap, 0):
                    h2h_win += 1
                # only where the source disagreed with the model's captain
                if cap_rider != model_cap:
                    a_src, a_mod = act.get(cap_rider, 0), act.get(model_cap, 0)
                    if a_src > a_mod:
                        diff_win += 1
                    elif a_src < a_mod:
                        diff_loss += 1
                    else:
                        diff_tie += 1

            bpc = []
            for b in pk.get("buys", []):
                rr = match(b, idx)
                if rr:
                    bpc.append(pct.get(rr, 0))
                else:
                    umatch.add(b)
            if bpc:
                buys_pcts.append(sum(bpc) / len(bpc))
                row["buys_mean_pct"] = round(sum(bpc) / len(bpc), 2)
            per_stage.append(row)

        results[src] = {
            "captain_top1_pct": round(100 * cap_top1 / cap_n, 1) if cap_n else None,
            "captain_top3_pct": round(100 * cap_top3 / cap_n, 1) if cap_n else None,
            "captain_stages": cap_n,
            "buys_mean_growth_percentile": round(sum(buys_pcts) / len(buys_pcts), 3) if buys_pcts else None,
            "buys_stages": len(buys_pcts),
            "captain_vs_model_winrate": round(100 * h2h_win / h2h_n, 1) if h2h_n else None,
            "disagreed_with_model_stages": diff_win + diff_loss + diff_tie,
            "disagree_win": diff_win, "disagree_loss": diff_loss, "disagree_tie": diff_tie,
            "per_stage": per_stage,
        }
        unmatched[src] = sorted(umatch)

    # model baseline (from indicator eval): captain 33.3/57.1; buys = top-8 by exp
    model_buys_pcts = []
    for st in pred["stages"]:
        best1, best3, pct, act = stage_tables(st)
        top8 = sorted(st["riders"], key=lambda r: -(r.get("exp") or 0))[:8]
        model_buys_pcts.append(sum(pct.get(r["name"], 0) for r in top8) / 8)
    model_baseline = {
        "captain_top1_pct": 33.3, "captain_top3_pct": 57.1,
        "buys_mean_growth_percentile": round(sum(model_buys_pcts) / len(model_buys_pcts), 3),
    }

    out = {"note": "Optakter vs model on actual holdet growth. buys percentile: "
                   "1.0=best in field, 0.5=field average.",
           "model_baseline": model_baseline, "sources": results, "unmatched_names": unmatched}
    outp = ROOT / "data/analysis/optakt_eval.json"
    outp.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    print("OPTAKTER vs MODEL — actual holdet growth\n")
    print(f"{'source':10s} {'cap top1':>9s} {'cap top3':>9s} {'buys pct':>9s} {'beats model cap':>16s}")
    print(f"{'MODEL':10s} {model_baseline['captain_top1_pct']:>8.1f}% "
          f"{model_baseline['captain_top3_pct']:>8.1f}% "
          f"{model_baseline['buys_mean_growth_percentile']:>9.3f} {'—':>16s}")
    for src, r in results.items():
        print(f"{src:10s} {r['captain_top1_pct'] or 0:>8.1f}% {r['captain_top3_pct'] or 0:>8.1f}% "
              f"{r['buys_mean_growth_percentile'] or 0:>9.3f} {str(r['captain_vs_model_winrate'])+'%':>16s}")
    print("\nWhen the source's captain DIFFERED from the model's (win / loss / tie on growth):")
    for src, r in results.items():
        print(f"  {src:10s} {r['disagree_win']}W / {r['disagree_loss']}L / {r['disagree_tie']}T "
              f"(of {r['disagreed_with_model_stages']} disagreements)")
    for src, u in unmatched.items():
        if u:
            print(f"\n[{src}] unmatched names ({len(u)}): {', '.join(u[:15])}")
    print(f"\nWrote {outp.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
