#!/usr/bin/env python3
"""
Realistic best-possible team over 21 rounds — a proper MILP (PuLP/CBC).

Replaces the earlier bogus "ceiling" (which just summed the 8 biggest risers each
round, ignoring every rule). This respects holdet's actual constraints:
  - exactly 8 riders each round
  - at most 2 riders from any one real cycling team
  - 50M start budget; roster cost each round <= current team value
  - 1% transfer fee on riders bought after round 1

Objective: maximise FINAL market value = 50M + captured price-appreciation - fees
(final value = V[21]). This is the dominant component of holdet value; per-stage
point bonuses would only add to it, so this is a realistic, rules-respecting
estimate (a lower bound on the true optimum, unlike the old ceiling which was a
loose upper bound).

Writes data/analysis/best_team_milp.json.
"""
import json
from pathlib import Path
import pulp

ROOT = Path(__file__).resolve().parents[2]
BUDGET = 50e6
FEE = 0.01
NR = 21
TIME_LIMIT = 240


def load_prices():
    hp = json.loads((ROOT / "data/cache/holdet_players.json").read_text())
    snap = json.loads((ROOT / "data/cache/stage_snapshots.json").read_text())
    start = {s: v["start_price_M"] * 1e6 for s, v in hp.items()}
    final = {s: v["price_M"] * 1e6 for s, v in hp.items()}
    name = {s: v["holdet_name"] for s, v in hp.items()}
    # rider -> real cycling team, via snapshot players.json (playerId->teamId)
    players = json.loads((ROOT / "data/sources/tdf2026/holdet/reference/players.json").read_text())["items"]
    pid_team = {p["id"]: p["teamId"] for p in players}
    slug_pid = {s: v["holdet_player_id"] for s, v in hp.items()}
    team = {s: pid_team.get(slug_pid.get(s), -1) for s in hp}

    def price(slug, r):
        if r == 1:
            return start.get(slug, 0.0)
        if r == 21:
            return final.get(slug, start.get(slug, 0.0))
        if r >= 4 and str(r) in snap and slug in snap[str(r)]:
            return snap[str(r)][slug]["price"] * 1e6
        if r in (2, 3):
            p1, p4 = start.get(slug, 0.0), price(slug, 4)
            return p1 + (p4 - p1) * (r - 1) / 3
        return start.get(slug, 0.0)

    return hp, name, team, start, final, price


def main():
    hp, name, team, start, final, price = load_prices()
    slugs_all = [s for s in hp if final.get(s, 0) > 0 and start.get(s, 0) > 0]
    P = {s: [price(s, r) for r in range(1, NR + 1)] for s in slugs_all}

    # Universe = every rider any of the 14 snapshot teams actually held at any
    # point (guarantees the real teams' solutions are feasible, so the optimum
    # can't be below what they achieved) PLUS meaningful movers. Keeps it small
    # enough to solve while never excluding a rider that mattered.
    import os
    H = ROOT / "data/sources/tdf2026/holdet/teams"
    slug_pid_local = {v["holdet_player_id"]: s for s, v in hp.items()}
    ever_held = set()
    for tid in os.listdir(H):
        for rf in os.listdir(H / tid):
            for it in json.loads((H / tid / rf).read_text())["items"]:
                s = slug_pid_local.get(it["playerId"])
                if s in P:
                    ever_held.add(s)
    appr = {s: max(P[s]) - min(P[s]) for s in slugs_all}
    movers = {s for s in slugs_all if appr[s] > 0.15e6}
    universe = sorted(ever_held | movers)
    print(f"universe: {len(universe)} riders ({len(ever_held)} ever-held + movers)")

    teams = sorted(set(team[s] for s in universe))

    m = pulp.LpProblem("best_team", pulp.LpMaximize)
    x = {(s, r): pulp.LpVariable(f"x_{s}_{r}", cat="Binary") for s in universe for r in range(NR)}
    buy = {(s, r): pulp.LpVariable(f"b_{s}_{r}", lowBound=0, upBound=1) for s in universe for r in range(1, NR)}
    bank = {r: pulp.LpVariable(f"bank_{r}", lowBound=0) for r in range(NR)}

    # CASH-FLOW budget: net cash change for a rider at round r is
    # (x[r-1]-x[r])*price  (+price if sold, -price if bought), plus a 1% fee on buys.
    # Holding an appreciated rider costs nothing, so roster value can grow past 50M.
    # buy is fee-penalised so the LP keeps it tight at max(0, x[r]-x[r-1]).
    m += bank[0] == BUDGET - pulp.lpSum(x[s, 0] * P[s][0] for s in universe)   # initial buy, no fee
    for r in range(1, NR):
        m += bank[r] == bank[r - 1] \
            + pulp.lpSum((x[s, r - 1] - x[s, r]) * P[s][r] for s in universe) \
            - pulp.lpSum(buy[s, r] * P[s][r] * FEE for s in universe)
        for s in universe:
            m += buy[s, r] >= x[s, r] - x[s, r - 1]

    for r in range(NR):
        m += pulp.lpSum(x[s, r] for s in universe) == 8
        for t in teams:
            grp = [x[s, r] for s in universe if team[s] == t]
            if len(grp) > 2:
                m += pulp.lpSum(grp) <= 2

    # objective: maximise FINAL total value = round-21 roster market value + bank
    m += pulp.lpSum(x[s, NR - 1] * P[s][NR - 1] for s in universe) + bank[NR - 1]

    status = m.solve(pulp.PULP_CBC_CMD(msg=1, timeLimit=TIME_LIMIT))
    final_value = pulp.value(m.objective)
    total_fees = sum(pulp.value(buy[s, r]) * P[s][r] * FEE for s in universe for r in range(1, NR))
    n_transfers = int(round(sum(pulp.value(buy[s, r]) for s in universe for r in range(1, NR))))

    # extract roster timeline
    timeline = []
    for r in range(NR):
        held = [s for s in universe if pulp.value(x[s, r]) > 0.5]
        timeline.append({"round": r + 1,
                         "value_M": round((sum(P[s][r] for s in held)) / 1e6, 2),
                         "roster": sorted(name[s] for s in held)})

    out = {
        "note": "Realistisk bedst mulige hold — MILP (PuLP/CBC) med holdets regler: "
                "8 ryttere, maks 2 pr. rigtige hold, 50M budget, 1% transfergebyr. "
                "Maksimerer slutmarkedsværdi. Erstatter det tidligere fejlagtige 'loft'.",
        "status": pulp.LpStatus[status],
        "final_value_M": round(final_value / 1e6, 2),
        "fees_M": round((total_fees or 0) / 1e6, 2),
        "transfers": n_transfers,
        "universe_size": len(universe),
        "time_limit_s": TIME_LIMIT,
        "final_roster": timeline[-1]["roster"],
        "timeline": timeline,
    }
    (ROOT / "data/analysis/best_team_milp.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nstatus={out['status']}  final_value={out['final_value_M']}M  "
          f"transfers={n_transfers}  fees={out['fees_M']}M")
    print("final roster:", ", ".join(out["final_roster"]))


if __name__ == "__main__":
    main()
