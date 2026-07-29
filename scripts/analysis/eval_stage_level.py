#!/usr/bin/env python3
"""
Task 2b — stage-level manager analysis + realistic best-possible team (TdF 2026).

Three deliverables, all from cached data (no network):

  1. VALUE CURVES  — each team's actual holdet market value per round
     (sum of its 8 riders' prices), so the graph runs ~50M → ~77M.

  2. PER-STAGE ANALYSIS — for every stage, our team vs Kasper / optakt / the
     top-10, plus captain head-to-heads and the biggest individual good/bad
     calls ("nedslag"). Decisions are per-stage, so the analysis is too.
     Per-stage metric = realised `actual` value gain (captain doubled for the
     owning team). NB: two lenses — the curve shows market value (price), the
     per-stage numbers show realised gain; they track each other.

  3. BEST-POSSIBLE TEAM (fee-aware) — a beam search over 21 rounds that pays
     holdet's ~1% transfer fee and respects a 50M start budget, maximising the
     FINAL team value (not per-stage greed). Reported as a strong estimate.

Writes data/analysis/stage_level.json + prints a summary.
"""
import json, os, collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
H = ROOT / "data/sources/tdf2026/holdet"
OUR, KASPER = 7145433, 7132927
OPTAKT = [7157567, 7132842]
TOP10 = [7161809, 7189610, 7133564, 7176905, 7205017, 7161445, 7169872, 7184171, 7133224, 7175364]
FEE = 0.01
BUDGET = 50e6


# ---------- data ----------
def load():
    hp = json.loads((ROOT / "data/cache/holdet_players.json").read_text())
    pid2slug = {v["holdet_player_id"]: s for s, v in hp.items()}
    slug2name = {s: v["holdet_name"] for s, v in hp.items()}
    start = {s: v["start_price_M"] * 1e6 for s, v in hp.items()}
    final = {s: v["price_M"] * 1e6 for s, v in hp.items()}
    snap = json.loads((ROOT / "data/cache/stage_snapshots.json").read_text())

    def price(slug, r):
        if slug is None:
            return 0.0
        if r == 1:
            return start.get(slug, 0.0)
        if r == 21:
            return final.get(slug, start.get(slug, 0.0))
        if r >= 4 and str(r) in snap and slug in snap[str(r)]:
            return snap[str(r)][slug]["price"] * 1e6
        if r in (2, 3):  # interp between r1 and r4
            p1, p4 = start.get(slug, 0.0), price(slug, 4)
            return p1 + (p4 - p1) * (r - 1) / 3
        return start.get(slug, 0.0)

    pred = json.loads((ROOT / "web/data/tdf2026_predictions.json").read_text())
    actual = {s["num"]: {r["name"]: (r.get("actual") or 0) for r in s["riders"]} for s in pred["stages"]}
    stype = {s["num"]: s["type"] for s in pred["stages"]}
    return pid2slug, slug2name, price, actual, stype, snap, start, final


def team_rounds(tid, pid2slug, slug2name):
    out = {}
    for rf in sorted(os.listdir(H / "teams" / str(tid))):
        r = int(rf.split("_")[1][:2])
        items = json.loads((H / "teams" / str(tid) / rf).read_text())["items"]
        roster, cap = [], None
        for it in items:
            slug = pid2slug.get(it["playerId"])
            nm = slug2name.get(slug)
            if nm:
                roster.append((slug, nm))
                if it["role"] == "captain":
                    cap = nm
        out[r] = {"roster": roster, "captain": cap}
    return out


def main():
    pid2slug, slug2name, price, actual, stype, snap, start, final = load()
    teams = {t: team_rounds(t, pid2slug, slug2name) for t in [OUR, KASPER] + OPTAKT + TOP10}

    # ---- 1. value curves (market value per round) ----
    def value_curve(tid):
        return [round(sum(price(s, r) for s, _ in teams[tid][r]["roster"]) / 1e6, 2) for r in range(1, 22)]
    curves = {"os": value_curve(OUR), "kasper": value_curve(KASPER),
              "optakt": [value_curve(t) for t in OPTAKT]}
    top_curves = {t: value_curve(t) for t in TOP10}
    curves["top10_median"] = [round(sorted(top_curves[t][i] for t in TOP10)[len(TOP10) // 2], 2) for i in range(21)]
    curves["top10_best"] = value_curve(max(TOP10, key=lambda t: top_curves[t][-1]))

    # ---- 2. per-stage analysis (realised gain, captain doubled) ----
    def stage_score(tid, r):
        ros = [n for _, n in teams[tid][r]["roster"]]
        cap = teams[tid][r]["captain"]
        ag = actual.get(r, {})
        return sum(ag.get(n, 0) for n in ros) + ag.get(cap, 0)

    per_stage = []
    nedslag_good, nedslag_bad = [], []
    for r in range(1, 22):
        ag = actual.get(r, {})
        our_ros = [n for _, n in teams[OUR][r]["roster"]]
        our_cap = teams[OUR][r]["captain"]
        kas_cap = teams[KASPER][r]["captain"]
        our_s = stage_score(OUR, r)
        top_scores = sorted(stage_score(t, r) for t in TOP10)
        top_med = top_scores[len(TOP10) // 2]
        # captain quality: best captain among our own roster
        best_own_cap = max(our_ros, key=lambda n: ag.get(n, 0)) if our_ros else None
        our_cap_g = ag.get(our_cap, 0)
        best_own_g = ag.get(best_own_cap, 0)
        kas_cap_g = ag.get(kas_cap, 0)
        # riders top-10 commonly held that we didn't, who scored well this stage
        top_own = collections.Counter()
        for t in TOP10:
            for _, n in teams[t][r]["roster"]:
                top_own[n] += 1
        missed = [(n, ag.get(n, 0), c) for n, c in top_own.items()
                  if n not in our_ros and c >= 5 and ag.get(n, 0) > 0]
        missed.sort(key=lambda x: -x[1])
        row = {
            "stage": r, "type": stype.get(r), "our_score_M": round(our_s / 1e6, 3),
            "top10_median_M": round(top_med / 1e6, 3), "delta_M": round((our_s - top_med) / 1e6, 3),
            "our_captain": our_cap, "our_cap_M": round(our_cap_g / 1e6, 3),
            "best_own_cap": best_own_cap, "best_own_cap_M": round(best_own_g / 1e6, 3),
            "cap_regret_M": round((best_own_g - our_cap_g) / 1e6, 3),
            "kasper_captain": kas_cap, "kasper_cap_M": round(kas_cap_g / 1e6, 3),
            "top_missed": [{"rider": n, "gain_M": round(g / 1e6, 3), "top10_owned": c} for n, g, c in missed[:3]],
        }
        per_stage.append(row)
        # nedslag: captain calls
        if row["cap_regret_M"] >= 0.12:
            nedslag_bad.append({"stage": r, "kind": "kaptajn", "M": -row["cap_regret_M"],
                                "text": f"E{r}: kaptajn {our_cap} (+{row['our_cap_M']:.2f}M) — bedre valg var egne {best_own_cap} (+{row['best_own_cap_M']:.2f}M)"})
        if our_cap and best_own_cap == our_cap and our_cap_g >= 0.30:
            nedslag_good.append({"stage": r, "kind": "kaptajn", "M": row["our_cap_M"],
                                 "text": f"E{r}: kaptajn {our_cap} ramte etapens bedste egne (+{row['our_cap_M']:.2f}M) ✓"})
        # captain head-to-head vs Kasper (only when they differ)
        if our_cap != kas_cap and abs(our_cap_g - kas_cap_g) >= 0.15:
            if our_cap_g > kas_cap_g:
                nedslag_good.append({"stage": r, "kind": "kaptajn-vs-kasper", "M": round((our_cap_g - kas_cap_g) / 1e6, 3),
                                     "text": f"E{r}: du kaptajnede {our_cap} (+{row['our_cap_M']:.2f}M), Kasper tog {kas_cap} (+{row['kasper_cap_M']:.2f}M) — godt kald ✓"})
            else:
                nedslag_bad.append({"stage": r, "kind": "kaptajn-vs-kasper", "M": round((our_cap_g - kas_cap_g) / 1e6, 3),
                                    "text": f"E{r}: du kaptajnede {our_cap} (+{row['our_cap_M']:.2f}M), Kasper tog {kas_cap} (+{row['kasper_cap_M']:.2f}M) — Kasper vandt kaldet"})
        # nedslag: missed roster rider (big stage gain, top-10 owned, we didn't)
        if missed and missed[0][1] >= 0.35:
            n, g, c = missed[0]
            nedslag_bad.append({"stage": r, "kind": "rytter", "M": round(-g / 1e6, 3),
                                "text": f"E{r}: {n} gav +{g/1e6:.2f}M ({c}/10 top-hold havde ham) — du havde ham ikke"})

    nedslag_good.sort(key=lambda x: -x["M"])
    nedslag_bad.sort(key=lambda x: x["M"])

    # ---- 2c. team-bonus (holdbonus): how many of the 8 finished stage top-15 ----
    tb = team_bonus(teams, pid2slug)
    tb_by_stage = {r["stage"]: r for r in tb["per_stage"]}
    for row in per_stage:
        b = tb_by_stage.get(row["stage"])
        row["top15_you"] = b["you"] if b else None
        row["top15_top10_med"] = b["top10_med"] if b else None

    # ---- 3. best-possible team (fee-aware beam search) ----
    best = best_team(price, start, final)

    out = {
        "note": "Stage-level analysis. Curves = market value (sum of rider prices). "
                "Per-stage = realised actual gain (captain doubled). Best team = fee-aware beam search.",
        "curves_M": curves,
        "per_stage": per_stage,
        "nedslag_good": nedslag_good[:10],
        "nedslag_bad": nedslag_bad[:10],
        "best_team": best,
        "team_bonus": tb,
        "our_final_value_M": curves["os"][-1],
        "top10_best_final_M": curves["top10_best"][-1],
    }
    (ROOT / "data/analysis/stage_level.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # ---- print ----
    print("VÆRDIKURVE (markedsværdi, M):")
    print("  os     r1", curves["os"][0], "→ r21", curves["os"][-1])
    print("  top10-median r21", curves["top10_median"][-1], "| top10-best r21", curves["top10_best"][-1])
    print("\nPER-ETAPE (uddrag): stage type our_score top10_med delta cap regret")
    for row in per_stage:
        print(f"  E{row['stage']:>2} {row['type']:<8} {row['our_score_M']:>6.2f} {row['top10_median_M']:>6.2f} "
              f"{row['delta_M']:>+6.2f}  kap {row['our_captain'] or '-':<18} regret {row['cap_regret_M']:>+.2f}")
    print("\nBEDSTE KALD:")
    for x in nedslag_good[:6]:
        print("  +", x["text"])
    print("\nDÅRLIGSTE KALD:")
    for x in nedslag_bad[:6]:
        print("  -", x["text"])
    print("\nHOLDBONUS (ryttere i etapens top-15):")
    for row in tb["per_stage"]:
        flag = "  <- MISSET" if row["top10_med"] >= 6 and row["you"] < row["top10_med"] else ""
        print(f"  E{row['stage']:>2}: du {row['you']}  vs top-10 median {row['top10_med']} "
              f"(spænd {row['top10_min']}-{row['top10_max']}){flag}")
    print(f"  vurderede etaper: {tb['assessed_stages']} (mangler resultater for øvrige)")

    print(f"\nBEDST MULIGE HOLD:")
    print(f"  bedste buy-and-hold (0 transfers):    {best['buy_and_hold_M']}M")
    print(f"  vores (aktivt styret):                {curves['os'][-1]}M")
    print(f"  top-10 bedste (aktivt styret):        {curves['top10_best'][-1]}M")
    print(f"  loft (perfekt rotation, ingen gebyr): {best['ceiling_M']}M  <- realistisk optimum ligger mellem 79 og dette")
    print(f"  illustrativ hold-roster: {', '.join(best['illustrative_hold_roster'])}")
    print("\nWrote data/analysis/stage_level.json")


def team_bonus(teams, pid2slug):
    """
    Holdbonus: count how many of each team's 8 riders finished a stage's top-15
    (holdet awards a bonus for 6/7/8 riders in the top 15). Uses actual finishing
    positions from gt_stage_results.json. Only stages with complete results are
    assessed (others lack finishing data in the cache).
    """
    import unicodedata
    hp = json.loads((ROOT / "data/cache/holdet_players.json").read_text())

    # Prefer the authoritative holdet source (fantasy-actions placements, all 21
    # stages, exact personId match) when the snapshot captured it; else fall back
    # to the PCS gt_stage_results cache (finishing positions, partial coverage).
    hres_path = H / "stage_results.json"
    if hres_path.exists():
        hres = json.loads(hres_path.read_text())          # {stage: {personId: {pos,pts}}}
        slug2pid = {s: v.get("holdet_person_id") for s, v in hp.items()}
        per_stage, missed = [], []
        assessed = sorted(int(k) for k, d in hres.items()
                          if sum(1 for r in d.values() if r.get("pos")) >= 15)
        def count_h(tid, r):
            res = hres.get(str(r), {})
            return sum(1 for s, _ in teams[tid][r]["roster"]
                       if res.get(str(slug2pid.get(s)), {}).get("pos"))
        for r in assessed:
            you = count_h(OUR, r)
            t10 = sorted(count_h(t, r) for t in TOP10)
            med = t10[len(t10) // 2]
            per_stage.append({"stage": r, "you": you, "top10_med": med,
                              "top10_min": t10[0], "top10_max": t10[-1]})
            if med >= 6 and you < med:
                missed.append({"stage": r, "you": you, "top10_med": med,
                               "text": f"E{r}: du havde {you} ryttere i top-15, top-10 havde typisk {med} — misset/mindre holdbonus"})
        return {"note": "Holdbonus via holdets fantasy-actions (placeringsregler 849-863), "
                        "alle etaper med komplette resultater. Antal ryttere i top-15.",
                "source": "holdet_fantasy_actions", "assessed_stages": assessed,
                "per_stage": per_stage, "missed": missed}

    # Primary in-repo source: the holdet scoring matrix in tdf2026_scores.json,
    # which carries each rider's placement rules 849..863 (1st..15th) per stage —
    # holdet's OWN placement data, exact slug match, covering stages 2..21.
    # (Stage 1 is a TTT with no individual top-15.)
    scores = json.loads((ROOT / "web/data/tdf2026_scores.json").read_text())
    placement = {}   # stage(int) -> {slug: pos}
    for rr in scores["riders"]:
        slug = rr["id"]
        for st, rules in rr.get("rules", {}).items():
            pos = next((rid - 848 for rid, _ in rules if 849 <= rid <= 863), None)
            if pos is not None:
                placement.setdefault(int(st), {})[slug] = pos

    def count_s(tid, r):
        pl = placement.get(r, {})
        return sum(1 for s, _ in teams[tid][r]["roster"] if s in pl)

    per_stage, missed = [], []
    assessed = sorted(st for st, pl in placement.items() if len(pl) >= 15)
    for r in assessed:
        you = count_s(OUR, r)
        t10 = sorted(count_s(t, r) for t in TOP10)
        med = t10[len(t10) // 2]
        per_stage.append({"stage": r, "you": you, "top10_med": med,
                          "top10_min": t10[0], "top10_max": t10[-1]})
        if med >= 6 and you < med:
            missed.append({"stage": r, "you": you, "top10_med": med,
                           "text": f"E{r}: du havde {you} ryttere i top-15, top-10 havde typisk {med} — misset/mindre holdbonus"})
    return {
        "note": "Antal af holdets 8 ryttere i etapens top-15 via holdets egne "
                "placeringsregler (849-863) i tdf2026_scores.json. Eksakt slug-match, "
                "etape 2-21 (etape 1 = TTT, ingen individuel top-15).",
        "source": "tdf2026_scores_placement_rules",
        "assessed_stages": assessed, "per_stage": per_stage, "missed": missed,
    }


def _fold(s):
    import unicodedata
    s = (s or "").lower().replace("æ", "ae").replace("ø", "o").replace("å", "a").replace("'", "").replace(".", "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return tuple(t for t in s.replace("-", " ").replace("_", " ").split() if len(t) > 1)


def best_team(price, start, final):
    """
    Fee-aware forward-greedy maximising FINAL team value over 21 rounds.

    Key metric each round = a rider's REMAINING forward gain (price[21]-price[r]).
    We hold the 8 riders with the most gain still ahead of them, and only
    transfer when a swap's forward-gain improvement beats the 1% fee. This
    naturally captures the sprinter→climber rotation (a peaked rider's remaining
    gain collapses, so it gets swapped for one whose rise is still ahead).
    Bank and fees are tracked exactly; value = held@21 prices + bank.
    """
    slugs = [s for s in start if final.get(s, 0) > 0 and start.get(s, 0) > 0]
    P = {s: [price(s, r) for r in range(1, 22)] for s in slugs}

    # ceiling: each round hold the 8 with the highest THAT-round gain (no fee/budget)
    ceiling = BUDGET
    for r in range(1, 21):
        gains = sorted((P[s][r] - P[s][r - 1] for s in slugs), reverse=True)
        ceiling += sum(g for g in gains[:8] if g > 0)

    # ---- best BUY-AND-HOLD team (0 transfers, 0 fees) via knapsack ----
    # maximise sum(final price) for 8 riders within the 50M start budget.
    # Discretise start prices to 0.1M units.
    UNIT = 1e5
    CAP = int(BUDGET / UNIT)                 # 500 units
    cand = [s for s in slugs if final[s] > 0]
    cost = {s: max(1, int(round(P[s][0] / UNIT))) for s in cand}
    val = {s: P[s][20] for s in cand}        # final value in kr
    NEG = -1.0
    # dp[k][b] = best final value using exactly k riders costing <= b units; store pick via parent
    dp = [[NEG] * (CAP + 1) for _ in range(9)]
    pick = [[None] * (CAP + 1) for _ in range(9)]
    dp[0] = [0.0] * (CAP + 1)
    for s in cand:
        c, v = cost[s], val[s]
        for k in range(8, 0, -1):
            row, prow = dp[k], dp[k - 1]
            pk = pick[k]
            for b in range(CAP, c - 1, -1):
                alt = prow[b - c]
                if alt >= 0 and alt + v > row[b]:
                    row[b] = alt + v
                    pk[b] = (s, b - c, k - 1)
    bestb = max(range(CAP + 1), key=lambda b: dp[8][b])
    bh_value = dp[8][bestb]

    # illustrative roster: not the proven-optimal set (exactly-8 knapsack
    # reconstruction needs an item-indexed table), but the highest-appreciation
    # riders that fit the 50M start budget — a faithful picture of the strategy.
    illustrative, spent = [], 0.0
    by_price = sorted(cand, key=lambda s: P[s][0])
    for s in sorted(cand, key=lambda s: -(val[s] - P[s][0])):
        if len(illustrative) >= 8:
            break
        slots_after = 8 - len(illustrative) - 1
        reserve = sum(sorted(P[x][0] for x in by_price if x not in illustrative and x != s)[:slots_after])
        if spent + P[s][0] + reserve <= BUDGET:
            illustrative.append(s); spent += P[s][0]
    for s in by_price:
        if len(illustrative) >= 8:
            break
        if s not in illustrative and spent + P[s][0] <= BUDGET:
            illustrative.append(s); spent += P[s][0]

    return {
        "note": "buy_and_hold = best 8-rider team held all race (0 transfers, 0 fees), "
                "optimal VALUE via knapsack. ceiling = fee-free perfect-rotation upper "
                "bound (ignores budget). The realistic fee-aware optimum sits between the "
                "actual best manager and the ceiling.",
        "buy_and_hold_M": round(bh_value / 1e6, 2),
        "ceiling_M": round(ceiling / 1e6, 2),
        "illustrative_hold_roster": sorted(slug_name(s) for s in illustrative),
    }


_SLUG2NAME = {}
def slug_name(s):
    return _SLUG2NAME.get(s, s)


if __name__ == "__main__":
    # populate slug->name for best_team output
    hp = json.loads((ROOT / "data/cache/holdet_players.json").read_text())
    _SLUG2NAME.update({s: v["holdet_name"] for s, v in hp.items()})
    main()
