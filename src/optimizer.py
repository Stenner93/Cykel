"""
LP team optimizer — finds the best holdet.dk team compositions.

Uses PuLP (COIN-BC) for integer programming:
  - Maximize expected fantasy points
  - Budget ≤ 50M kr (or transfer-adjusted budget if current team provided)
  - Exactly 8 riders
  - Max 2 riders per real-world team
  - 1 captain (doubles expected points for that rider)

Returns:
  make_three_teams()  → 3 transfer-aware strategies (SAFE / VALUE / ATTACK)
  make_best_team()    → unconstrained best possible team (fresh 50M budget)
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

try:
    import pulp
    HAS_PULP = True
except ImportError:
    HAS_PULP = False


BUDGET            = 50_000_000   # kr
TEAM_SIZE         = 8
MAX_PER_TEAM      = 2
BUDGET_PRICE_THRESHOLD = 4_000_000   # riders at ≤4M = "budget picks"
TRANSFER_FEE      = 0.01             # 1% fee on purchases


# ---------------------------------------------------------------------------
# Core MILP solver
# ---------------------------------------------------------------------------

def _solve(
    predictions: list[dict],
    budget: float = BUDGET,
    forced_in: list[str] | None = None,
    forced_out: list[str] | None = None,
    objective_key: str = "expected_pts",
    label: str = "safe",
) -> dict[str, Any] | None:
    """
    Core LP solver. Returns selected team or None if infeasible.
    forced_in / forced_out: rider IDs to include / exclude.
    """
    if not HAS_PULP:
        return _greedy_fallback(predictions, budget, forced_in, forced_out, label)

    prob = pulp.LpProblem(f"tdf_{label}", pulp.LpMaximize)

    n   = len(predictions)
    x   = [pulp.LpVariable(f"x_{i}", cat="Binary") for i in range(n)]
    c   = [pulp.LpVariable(f"c_{i}", cat="Binary") for i in range(n)]

    # Objective: sum(expected_pts * x) + sum(expected_pts * c)  [captain doubles]
    prob += pulp.lpSum(
        predictions[i][objective_key] * (x[i] + c[i])
        for i in range(n)
    )

    # Constraints
    prob += pulp.lpSum(x) == TEAM_SIZE                                # 8 riders
    prob += pulp.lpSum(c) == 1                                        # 1 captain
    prob += pulp.lpSum(
        predictions[i]["price"] * 1_000_000 * x[i] for i in range(n)
    ) <= budget                                                        # budget
    for i in range(n):
        prob += c[i] <= x[i]                                          # captain on team

    # Max 2 per real-world team
    rteams: dict[str, list[int]] = {}
    for i, p in enumerate(predictions):
        rteams.setdefault(p.get("team", "UNK"), []).append(i)
    for t, idxs in rteams.items():
        prob += pulp.lpSum(x[i] for i in idxs) <= MAX_PER_TEAM

    # Forced inclusions / exclusions
    for rid in (forced_in or []):
        idx = next((i for i, p in enumerate(predictions) if p["rider_id"] == rid), None)
        if idx is not None:
            prob += x[idx] == 1

    for rid in (forced_out or []):
        idx = next((i for i, p in enumerate(predictions) if p["rider_id"] == rid), None)
        if idx is not None:
            prob += x[idx] == 0

    prob.solve(pulp.PULP_CBC_CMD(msg=0))

    if pulp.LpStatus[prob.status] != "Optimal":
        return None

    selected   = []
    captain_id = None
    for i in range(n):
        if pulp.value(x[i]) > 0.5:
            is_cap = pulp.value(c[i]) > 0.5
            entry  = dict(predictions[i])
            entry["is_captain"] = is_cap
            selected.append(entry)
            if is_cap:
                captain_id = predictions[i]["rider_id"]

    total_pts   = sum(p["expected_pts"] for p in selected)
    captain_pts = next((p["expected_pts"] for p in selected if p["is_captain"]), 0)
    total_cost  = sum(p["price"] for p in selected)

    return {
        "label":         label,
        "team":          selected,
        "captain_id":    captain_id,
        "expected_pts":  total_pts + captain_pts,
        "total_cost_M":  round(total_cost, 2),
        "budget_left_M": round(BUDGET / 1_000_000 - total_cost, 2),
    }


def _greedy_fallback(predictions, budget, forced_in, forced_out, label):
    """Simple greedy fallback when PuLP is not installed."""
    pool = [p for p in predictions if p["rider_id"] not in (forced_out or [])]
    selected: list[dict] = []
    team_counts: dict[str, int] = {}
    spent = 0.0

    for p in pool:
        if p["rider_id"] in (forced_in or []):
            selected.append(dict(p))
            team_counts[p["team"]] = team_counts.get(p["team"], 0) + 1
            spent += p["price"] * 1_000_000

    for p in sorted(pool, key=lambda x: x["expected_pts"], reverse=True):
        if len(selected) >= TEAM_SIZE:
            break
        if p["rider_id"] in [s["rider_id"] for s in selected]:
            continue
        if team_counts.get(p["team"], 0) >= MAX_PER_TEAM:
            continue
        if spent + p["price"] * 1_000_000 > budget:
            continue
        selected.append(dict(p))
        team_counts[p["team"]] = team_counts.get(p["team"], 0) + 1
        spent += p["price"] * 1_000_000

    if not selected:
        return None

    best_cap = max(selected, key=lambda x: x["expected_pts"])
    for p in selected:
        p["is_captain"] = (p["rider_id"] == best_cap["rider_id"])

    total_pts   = sum(p["expected_pts"] for p in selected)
    captain_pts = best_cap["expected_pts"]
    return {
        "label":         label,
        "team":          selected,
        "captain_id":    best_cap["rider_id"],
        "expected_pts":  total_pts + captain_pts,
        "total_cost_M":  round(spent / 1_000_000, 2),
        "budget_left_M": round((budget - spent) / 1_000_000, 2),
    }


# ---------------------------------------------------------------------------
# Transfer analysis helper
# ---------------------------------------------------------------------------

def _transfer_analysis(
    team: dict,
    current_ids: set,
    preds_by_id: dict,
    bank_M: float,
) -> dict:
    """
    Calculate what transfers are needed to go from current team to recommended team.

    Returns:
      to_sell / to_buy — rider lists
      n_transfers       — number of new riders to acquire
      proceeds_M        — money received from selling riders
      buy_cost_M        — money paid for new riders (incl. 1% fee)
      net_cost_M        — buy_cost_M - proceeds_M  (negative = net gain)
      balance_after_M   — bank balance after all transfers
      affordable        — True if balance_after_M >= 0
    """
    rec_ids     = {r["rider_id"] for r in team["team"]}
    to_sell_ids = current_ids - rec_ids
    to_buy_ids  = rec_ids - current_ids

    to_sell = [preds_by_id[rid] for rid in to_sell_ids if rid in preds_by_id]
    to_buy  = [r for r in team["team"] if r["rider_id"] in to_buy_ids]

    proceeds_M    = sum(r["price"] for r in to_sell)
    face_buy_M    = sum(r["price"] for r in to_buy)            # sticker price
    fee_M         = face_buy_M * TRANSFER_FEE                  # 1% gebyr
    buy_cost_M    = face_buy_M + fee_M                         # total outlay
    balance_after = bank_M + proceeds_M - buy_cost_M

    return {
        "to_sell": [
            {"rider_id": r["rider_id"],
             "full_name": r.get("full_name", r["rider_id"]),
             "price_M": round(r["price"], 2)}
            for r in sorted(to_sell, key=lambda x: x["price"], reverse=True)
        ],
        "to_buy": [
            {"rider_id": r["rider_id"],
             "full_name": r.get("full_name", r["rider_id"]),
             "price_M": round(r["price"], 2)}
            for r in sorted(to_buy, key=lambda x: x["price"], reverse=True)
        ],
        "n_transfers":     len(to_buy_ids),
        "proceeds_M":      round(proceeds_M, 2),
        "face_buy_M":      round(face_buy_M, 2),   # købers listepris
        "fee_M":           round(fee_M, 2),         # 1% transfergebyr
        "buy_cost_M":      round(buy_cost_M, 2),    # total (inkl. gebyr)
        "net_cost_M":      round(buy_cost_M - proceeds_M, 2),
        "balance_after_M": round(balance_after, 2),
        "affordable":      balance_after >= -0.01,
    }


# ---------------------------------------------------------------------------
# Main public functions
# ---------------------------------------------------------------------------

def make_three_teams(
    predictions: list[dict],
    current_team_data: dict | None = None,
    transfer_budget_M: float | None = None,
    force_top_n: int = 3,
    budget_rider_boost: float = 1.15,
    attack_out_n: int = 2,
) -> list[dict]:
    """
    Generate three optimised team suggestions.

    force_top_n (default 3):
      Force the top-N predicted riders (by expected_pts, i.e. the VeloScore
      consensus picks) into SAFE and VALUE teams.  Prevents the LP from
      "logically but unintuively" swapping an obvious top pick for cheaper
      mid-tier riders.

      ATTACK gets only the top-1 pick forced in; the top attack_out_n picks
      are EXCLUDED, producing a genuinely contrarian team.

      Set force_top_n=0 to disable (pure LP, no constraints on top picks).

    budget_rider_boost (default 1.15):
      Expected-pts multiplier applied to cheap riders (≤4M) in the VALUE
      team to favour budget picks.  Set via strategy.get_budget_boost().

    attack_out_n (default 2):
      How many consensus picks to force OUT of the ATTACK team.
      Set via strategy.get_attack_out_n().

    current_team_data (optional):
      {"rider_ids": [...], "bank_M": 5.0}
      When provided:
        - budget = bank_M + current team total value  (≈ 50M)
        - riders NOT in current team get 1% price surcharge (transfer fee)
        - each team receives a transfer_analysis showing what to buy/sell

    transfer_budget_M (optional): override budget in millions (legacy param)
    """
    current_ids     = set()
    bank_M          = 0.0
    current_total_M = 0.0

    if current_team_data:
        current_ids = set(current_team_data.get("rider_ids", []))
        bank_M      = float(current_team_data.get("bank_M", 0.0))
        by_id       = {p["rider_id"]: p for p in predictions}
        current_total_M = sum(
            by_id[rid]["price"] for rid in current_ids if rid in by_id
        )
        effective_budget = (bank_M + current_total_M) * 1_000_000

        # Adjust prices: 1% surcharge for riders not already on the team
        base_preds = []
        for p in predictions:
            adj = dict(p)
            if p["rider_id"] not in current_ids:
                adj["price"] = round(p["price"] * (1 + TRANSFER_FEE), 4)
            base_preds.append(adj)
    else:
        effective_budget = (transfer_budget_M * 1_000_000) if transfer_budget_M else BUDGET
        base_preds       = predictions

    # Build the consensus forced-in list (top N by expected_pts = VeloScore picks)
    top_n = [p["rider_id"] for p in predictions[:force_top_n]] if force_top_n > 0 else []

    # ── Team 1: SAFE — top-N locked in, then pure EV maximisation ─────────
    team1 = _solve(base_preds, budget=effective_budget, label="safe",
                   forced_in=top_n)

    # ── Team 2: VALUE — top-N locked in, boosted budget riders ───────────
    value_preds = []
    for p in base_preds:
        adj = dict(p)
        real_price = next(
            (rp["price"] for rp in predictions if rp["rider_id"] == p["rider_id"]),
            p["price"]
        )
        if real_price <= BUDGET_PRICE_THRESHOLD / 1_000_000 and budget_rider_boost != 1.0:
            adj["expected_pts"] = int(p["expected_pts"] * budget_rider_boost)
        value_preds.append(adj)
    team2 = _solve(value_preds, budget=effective_budget, label="value",
                   forced_in=top_n)

    # ── Team 3: ATTACK — contrarian: only top-1 in, top attack_out_n OUT ─
    # This forces genuinely different wildcards instead of just boosting variance
    # on a team that still has the obvious top picks.
    attack_forced_in  = top_n[:1]                  # lock in the #1 consensus pick only
    attack_forced_out = top_n[1 : 1 + attack_out_n]  # explicitly exclude top 2+
    attack_preds = []
    for p in base_preds:
        adj = dict(p)
        adj["expected_pts"] = int(p["expected_pts"] + p.get("variance", 0) * 0.0003)
        attack_preds.append(adj)
    team3 = _solve(attack_preds, budget=effective_budget, label="attack",
                   forced_in=attack_forced_in, forced_out=attack_forced_out)

    teams = [t for t in [team1, team2, team3] if t is not None]

    # Restore real expected_pts and prices (undo fee / boost adjustments)
    real_pts    = {p["rider_id"]: p["expected_pts"] for p in predictions}
    real_prices = {p["rider_id"]: p["price"]        for p in predictions}
    for team in teams:
        for r in team["team"]:
            r["expected_pts"] = real_pts.get(r["rider_id"],    r["expected_pts"])
            r["price"]        = real_prices.get(r["rider_id"], r["price"])
        total = sum(r["expected_pts"] for r in team["team"])
        cap   = next((r["expected_pts"] for r in team["team"] if r["is_captain"]), 0)
        team["expected_pts"] = total + cap
        team["total_cost_M"] = round(sum(r["price"] for r in team["team"]), 2)

    # De-duplicate (uses base_preds so fee-adjusted budget is respected)
    _deduplicate(teams, base_preds, effective_budget)

    # Restore prices again after deduplication (deduplicate may add new riders via _solve)
    for team in teams:
        for r in team["team"]:
            r["price"] = real_prices.get(r["rider_id"], r["price"])
        team["total_cost_M"] = round(sum(r["price"] for r in team["team"]), 2)

    # Compute budget_left and transfer analysis, then full assessment
    preds_by_id = {p["rider_id"]: p for p in predictions}
    for team in teams:
        if current_ids:
            ta = _transfer_analysis(team, current_ids, preds_by_id, bank_M)
            team["transfer_analysis"] = ta
            team["budget_left_M"]     = ta["balance_after_M"]
        else:
            team["budget_left_M"] = round(BUDGET / 1_000_000 - team["total_cost_M"], 2)
        team["assessment"] = _assess(team, predictions)

    return teams


def make_best_team(predictions: list[dict]) -> dict | None:
    """
    Unconstrained 'best possible' team — full 50M budget, no transfer fees.
    Shown as the 4th recommendation card on the website.
    """
    team = _solve(predictions, budget=BUDGET, label="best")
    if team:
        team["budget_left_M"] = round(BUDGET / 1_000_000 - team["total_cost_M"], 2)
        team["assessment"]    = _assess(team, predictions)
    return team


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _deduplicate(teams: list[dict], predictions: list[dict], budget: float):
    """Ensure the three teams are not identical."""
    seen_ids: list[frozenset] = []
    for team in teams:
        ids = frozenset(p["rider_id"] for p in team["team"])
        if ids in seen_ids:
            most_exp = max(team["team"], key=lambda x: x["price"])
            rerun = _solve(
                predictions, budget=budget,
                forced_out=[most_exp["rider_id"]],
                label=team["label"] + "_alt"
            )
            if rerun:
                team.update(rerun)
                team["label"] = team["label"].replace("_alt", "")
        seen_ids.append(frozenset(p["rider_id"] for p in team["team"]))


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------

def _assess(team: dict, all_predictions: list[dict]) -> dict:
    """Generate qualitative + quantitative assessment of a team."""
    riders  = team["team"]
    captain = next((r for r in riders if r["is_captain"]), riders[0])

    prices    = [r["price"] for r in riders]
    avg_price = sum(prices) / len(prices)
    n_premium = sum(1 for p in prices if p >= 8.0)
    n_budget  = sum(1 for p in prices if p <= 4.0)

    top_riders = sorted(all_predictions, key=lambda x: x["expected_pts"], reverse=True)
    top15_ids  = {p["rider_id"] for p in top_riders[:15]}
    est_top15  = sum(1 for r in riders if r["rider_id"] in top15_ids)

    from .scoring import etapebonus
    est_etapebonus = etapebonus(est_top15)

    total_variance = sum(r.get("variance", 0) for r in riders)
    if total_variance > 1_500_000:
        risk = "Høj"
    elif total_variance > 800_000:
        risk = "Middel"
    else:
        risk = "Lav"

    teams_rep = list({r["team"] for r in riders})

    ta = team.get("transfer_analysis", {})

    return {
        "captain_name":       captain["full_name"],
        "captain_reasoning":  captain.get("reasoning", ""),
        "expected_pts_total": team["expected_pts"],
        "est_etapebonus":     est_etapebonus,
        "est_top15_count":    est_top15,
        "total_cost_M":       team["total_cost_M"],
        "budget_left_M":      team.get("budget_left_M",
                                       round(BUDGET / 1_000_000 - team["total_cost_M"], 2)),
        "avg_price_M":        round(avg_price, 1),
        "n_premium_riders":   n_premium,
        "n_budget_riders":    n_budget,
        "risk_profile":       risk,
        "teams_represented":  teams_rep,
        "n_teams":            len(teams_rep),
        # Transfer details (populated when current_team_data is provided)
        "n_transfers":        ta.get("n_transfers", 0),
        "affordable":         ta.get("affordable", True),
        "balance_after_M":    ta.get("balance_after_M",
                                     team.get("budget_left_M", 0)),
    }
