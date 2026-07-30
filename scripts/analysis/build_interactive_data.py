#!/usr/bin/env python3
"""
Build a compact per-stage JSON for the interactive artifact features:
stage explorer, captain trainer, gap-waterfall. Writes data/analysis/interactive.json.
"""
import json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
H = ROOT / "data/sources/tdf2026/holdet"
OUR, TOPCMP = 7145433, 7175364   # us vs the best-final-value top-10 team
TOP10 = [7161809, 7189610, 7133564, 7176905, 7205017, 7161445, 7169872, 7184171, 7133224, 7175364]


def main():
    hp = json.loads((ROOT / "data/cache/holdet_players.json").read_text())
    pid2slug = {v["holdet_player_id"]: s for s, v in hp.items()}
    slug2name = {s: v["holdet_name"] for s, v in hp.items()}
    pred = json.loads((ROOT / "web/data/tdf2026_predictions.json").read_text())
    actual = {s["num"]: {r["name"]: (r.get("actual") or 0) for r in s["riders"]} for s in pred["stages"]}
    stype = {s["num"]: s["type"] for s in pred["stages"]}
    scores = json.loads((ROOT / "web/data/tdf2026_scores.json").read_text())
    placement = {}
    for rr in scores["riders"]:
        for st, rules in rr.get("rules", {}).items():
            pos = next((rid - 848 for rid, _ in rules if 849 <= rid <= 863), None)
            if pos is not None:
                placement.setdefault(int(st), {})[rr["id"]] = pos
    eb = {int(k): v for k, v in json.loads((ROOT / "data/scoring_rules.json").read_text())["etapebonus"].items()}
    optakt = {p["stage"]: p["captain"] for p in json.loads((ROOT / "data/analysis/optakt_picks_simon.json").read_text())}
    felt = {p["stage"]: p["captain"] for p in json.loads((ROOT / "data/analysis/optakt_picks_feltet.json").read_text())}

    def veloscore_top(n):
        f = ROOT / f"data/stage_{n:02d}_veloscore.json"
        if f.exists():
            rows = json.loads(f.read_text()).get("predictions", [])
            if rows:
                return max(rows, key=lambda r: r.get("veloscore", 0))["rider"]
        return None

    def team_round(tid, r):
        items = json.loads((H / "teams" / str(tid) / f"round_{r:02d}.json").read_text())["items"]
        ros, cap = [], None
        for it in items:
            nm = slug2name.get(pid2slug.get(it["playerId"]))
            if nm:
                ros.append((pid2slug.get(it["playerId"]), nm))
                if it["role"] == "captain":
                    cap = nm
        return ros, cap

    stages = []
    for r in range(1, 22):
        ag = actual.get(r, {})
        pl = placement.get(r, {})
        our_ros, our_cap = team_round(OUR, r)
        top_ros, top_cap = team_round(TOPCMP, r)

        def enrich(ros):
            return sorted([{"name": nm, "gain": round(ag.get(nm, 0) / 1e6, 2),
                            "t15": pl.get(slug), "cap": False} for slug, nm in ros],
                          key=lambda x: -x["gain"])
        our = enrich(our_ros); top = enrich(top_ros)
        for d in our:
            d["cap"] = (d["name"] == our_cap)
        for d in top:
            d["cap"] = (d["name"] == top_cap)
        # captain trainer: candidates = our roster, best = max gain among them
        cand = sorted(({"name": nm, "gain": round(ag.get(nm, 0) / 1e6, 2)} for _, nm in our_ros),
                      key=lambda x: -x["gain"])
        winner = None
        for nm, pos in ((slug2name.get(s), p) for s, p in pl.items()):
            if pos == 1:
                winner = nm
        stages.append({
            "stage": r, "type": stype.get(r),
            "our": our, "top": top,
            "our_cap": our_cap, "top_cap": top_cap,
            "cand": cand,
            "best_cap": cand[0]["name"] if cand else None,
            "best_cap_gain": cand[0]["gain"] if cand else 0,
            "optakt_cap": optakt.get(r), "feltet_cap": felt.get(r),
            "veloscore_top": veloscore_top(r),
            "winner": winner,
        })
    # team-bonus counts computed cleanly here
    def top15_count(tid, r):
        ros, _ = team_round(tid, r)
        pl = placement.get(r, {})
        return sum(1 for sl, _ in ros if sl in pl)
    for st in stages:
        r = st["stage"]
        st["our_t15"] = top15_count(OUR, r)
        t10c = sorted(top15_count(t, r) for t in TOP10)
        st["top10_t15"] = t10c[len(t10c) // 2]
        st["bonus_missed_kr"] = max(0, eb.get(st["top10_t15"], 0) - eb.get(st["our_t15"], 0))

    out = {"cmp_team": TOPCMP, "stages": stages}
    (ROOT / "data/analysis/interactive.json").write_text(json.dumps(out, ensure_ascii=False))
    print(f"wrote interactive.json — {len(stages)} stages, ~{len(json.dumps(out))//1024}KB")


if __name__ == "__main__":
    main()
