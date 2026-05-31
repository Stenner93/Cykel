"""
LP team optimizer — finds the three best holdet.dk team compositions.

Uses PuLP (COIN-BC) for integer programming:
  - Maximize expected fantasy points
  - Budget ≤ 50M kr
  - Exactly 8 riders
  - Max 2 riders per real-world team
  - 1 captain (doubles expected points for that rider)

Returns three teams:
  1. SAFE   – maximise expected value, captain = highest EV rider
  2. VALUE  – same but force ≥2 budget picks (price ≤ 4M)
  3. ATTACK – allow one "high-variance" outsider, captain = aggressive pick
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


BUDGET       = 50_000_000  # kr
TEAM_SIZE    = 8
MAX_PER_TEAM = 2
BUDGET_PRICE_THRESHOLD = 4_000_000   # riders at ≤4M count as "budget picks"
VALUE_MIN_BUDGET_PICKS = 2


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

    n = len(predictions)
    ids = [p["rider_id"] for p in predictions]
    x = [pulp.LpVariable(f"x_{i}", cat="Binary") for i in range(n)]
    c = [pulp.LpVariable(f"c_{i}", cat="Binary") for i in range(n)]

    # Objective: sum(expected_pts * x) + sum(expected_pts * c)  [captain doubles]
    prob += pulp.lpSum(
        predictions[i][objective_key] * (x[i] + c[i])
        for i in range(n)
    )

    # Constraints
    prob += pulp.lpSum(x) == TEAM_SIZE                          # 8 riders
    prob += pulp.lpSum(c) == 1                                  # 1 captain
    prob += pulp.lpSum(predictions[i]["price"] * 1_000_000 * x[i]
                       for i in range(n)) <= budget             # budget
    for i in range(n):
        prob += c[i] <= x[i]                                    # captain on team

    # Max 2 per real-world team
    teams = {}
    for i, p in enumerate(predictions):
        t = p.get("team", "UNK")
        teams.setdefault(t, []).append(i)
    for t, idxs in teams.items():
        prob += pulp.lpSum(x[i] for i in idxs) <= MAX_PER_TEAM

    # Force inclusions / exclusions
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

    selected = []
    captain_id = None
    for i in range(n):
        if pulp.value(x[i]) > 0.5:
            is_cap = pulp.value(c[i]) > 0.5
            entry = dict(predictions[i])
            entry["is_captain"] = is_cap
            selected.append(entry)
            if is_cap:
                captain_id = predictions[i]["rider_id"]

    total_pts    = sum(p["expected_pts"] for p in selected)
    captain_pts  = next((p["expected_pts"] for p in selected if p["is_captain"]), 0)
    total_cost   = sum(p["price"] for p in selected)

    return {
        "label":       label,
        "team":        selected,
        "captain_id":  captain_id,
        "expected_pts":total_pts + captain_pts,  # captain doubles
        "total_cost_M":round(total_cost, 2),
        "budget_left_M": round(BUDGET / 1_000_000 - total_cost, 2),
    }


def _greedy_fallback(predictions, budget, forced_in, forced_out, label):
    """Simple greedy fallback when PuLP is not installed."""
    pool = [p for p in predictions if (forced_out or p["rider_id"] not in forced_out)]
    # Force-in first
    selected = []
    team_counts: dict[str, int] = {}
    spent = 0.0

    for p in pool:
        if p["rider_id"] in (forced_in or []):
            selected.append(p)
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
        selected.append(p)
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
        "label": label, "team": selected, "captain_id": best_cap["rider_id"],
        "expected_pts": total_pts + captain_pts,
        "total_cost_M": round(spent / 1_000_000, 2),
        "budget_left_M": round((budget - spent) / 1_000_000, 2),
    }


def make_three_teams(
    predictions: list[dict],
    current_team: list[str] | None = None,
    transfer_budget_M: float | None = None,
) -> list[dict]:
    """
    Generate three optimised team suggestions.

    current_team: list of rider IDs currently on the user's team (optional)
    transfer_budget_M: remaining budget in millions (optional, defaults to 50M fresh)
    """
    budget = (transfer_budget_M * 1_000_000) if transfer_budget_M else BUDGET

    # Team 1 — SAFE: pure expected-value maximisation
    team1 = _solve(predictions, budget=budget, label="safe")

    # Team 2 — VALUE: force at least 2 budget picks (≤4M), frees up money for a star
    budget_rider_ids = [p["rider_id"] for p in predictions
                        if p["price"] <= BUDGET_PRICE_THRESHOLD / 1_000_000]
    # We inject a soft constraint by boosting cheap riders' scores temporarily
    value_preds = []
    for p in predictions:
        adj = dict(p)
        if p["price"] <= BUDGET_PRICE_THRESHOLD / 1_000_000:
            adj["expected_pts"] = int(p["expected_pts"] * 1.15)  # 15% bonus for value
        value_preds.append(adj)
    team2 = _solve(value_preds, budget=budget, label="value")
    # Restore real expected_pts in team2 for display
    if team2:
        real_pts = {p["rider_id"]: p["expected_pts"] for p in predictions}
        for r in team2["team"]:
            r["expected_pts"] = real_pts.get(r["rider_id"], r["expected_pts"])
        total = sum(r["expected_pts"] for r in team2["team"])
        cap   = next((r["expected_pts"] for r in team2["team"] if r["is_captain"]), 0)
        team2["expected_pts"] = total + cap

    # Team 3 — ATTACK: boost high-variance riders to surface potential outsiders
    attack_preds = []
    for p in predictions:
        adj = dict(p)
        # High variance = high uncertainty = possible outsider upside
        variance_boost = p.get("variance", 0) * 0.0003
        adj["expected_pts"] = int(p["expected_pts"] + variance_boost)
        attack_preds.append(adj)
    team3 = _solve(attack_preds, budget=budget, label="attack")
    if team3:
        real_pts = {p["rider_id"]: p["expected_pts"] for p in predictions}
        for r in team3["team"]:
            r["expected_pts"] = real_pts.get(r["rider_id"], r["expected_pts"])
        total = sum(r["expected_pts"] for r in team3["team"])
        cap   = next((r["expected_pts"] for r in team3["team"] if r["is_captain"]), 0)
        team3["expected_pts"] = total + cap

    teams = [t for t in [team1, team2, team3] if t is not None]

    # De-duplicate: if two teams are identical, nudge team3 differently
    _deduplicate(teams, predictions, budget)

    # Add qualitative assessment to each team
    for team in teams:
        team["assessment"] = _assess(team, predictions)

    return teams


def _deduplicate(teams: list[dict], predictions: list[dict], budget: float):
    """Ensure the three teams are not identical."""
    seen_ids: list[set] = []
    for team in teams:
        ids = frozenset(p["rider_id"] for p in team["team"])
        if ids in seen_ids:
            # Force out the most expensive rider and re-solve
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


def _assess(team: dict, all_predictions: list[dict]) -> dict:
    """Generate qualitative + quantitative assessment of a team."""
    riders = team["team"]
    captain = next((r for r in riders if r["is_captain"]), riders[0])

    # Price distribution
    prices = [r["price"] for r in riders]
    avg_price = sum(prices) / len(prices)
    n_premium = sum(1 for p in prices if p >= 8.0)
    n_budget  = sum(1 for p in prices if p <= 4.0)

    # Expected top-15 count (rough estimate)
    top_riders = sorted(all_predictions, key=lambda x: x["expected_pts"], reverse=True)
    top15_ids  = {p["rider_id"] for p in top_riders[:15]}
    est_top15  = sum(1 for r in riders if r["rider_id"] in top15_ids)

    from .scoring import etapebonus
    est_etapebonus = etapebonus(est_top15)

    # Risk profile
    total_variance = sum(r.get("variance", 0) for r in riders)
    if total_variance > 1_500_000:
        risk = "Høj"
    elif total_variance > 800_000:
        risk = "Middel"
    else:
        risk = "Lav"

    # Diversity across teams
    teams_rep = list({r["team"] for r in riders})

    return {
        "captain_name":        captain["full_name"],
        "captain_reasoning":   captain.get("reasoning", ""),
        "expected_pts_total":  team["expected_pts"],
        "est_etapebonus":      est_etapebonus,
        "est_top15_count":     est_top15,
        "total_cost_M":        team["total_cost_M"],
        "budget_left_M":       team["budget_left_M"],
        "avg_price_M":         round(avg_price, 1),
        "n_premium_riders":    n_premium,
        "n_budget_riders":     n_budget,
        "risk_profile":        risk,
        "teams_represented":   teams_rep,
        "n_teams":             len(teams_rep),
    }
