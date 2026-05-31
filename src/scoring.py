"""
Holdet.dk scoring engine — mirrors the official rules exactly.
"""
from __future__ import annotations
import json
from pathlib import Path

RULES = json.loads((Path(__file__).parent.parent / "data" / "scoring_rules.json").read_text())

STAGE_PTS   = {int(k): v for k, v in RULES["stage_position"].items()}
GC_PTS      = {int(k): v for k, v in RULES["gc_standing"].items()}
TEAM_BONUS  = {int(k): v for k, v in RULES["team_bonus"].items()}
JERSEY      = RULES["jersey_bonus"]
SPT_PER_PT  = RULES["sprint_kom_per_point"]
LATE_PER_MIN = RULES["late_arrival_per_minute"]
LATE_MAX    = RULES["late_arrival_max"]
DNF_PEN     = RULES["dnf"]
DNS_PEN     = RULES["dns_per_remaining_stage"]


def stage_score(
    position: int | None,
    gc_rank: int | None,
    sprint_pts: int = 0,
    kom_pts: int = 0,
    jerseys: list[str] | None = None,
    team_position: int | None = None,
    minutes_behind: float = 0.0,
    dnf: bool = False,
    dns: bool = False,
) -> int:
    """
    Calculate fantasy points for a single rider on a single stage.
    Returns the rider's value change (not including captain bonus or etapebonus).
    """
    if dns:
        return DNS_PEN

    total = 0

    if dnf:
        # DNF: sprint/KOM points still count, but no stage/GC/jersey/team bonus
        total += sprint_pts * SPT_PER_PT
        total += kom_pts * SPT_PER_PT
        total += DNF_PEN
        return total

    # Stage position
    if position and position in STAGE_PTS:
        total += STAGE_PTS[position]

    # GC standing after the stage
    if gc_rank and gc_rank in GC_PTS:
        total += GC_PTS[gc_rank]

    # Sprint + KOM points
    total += sprint_pts * SPT_PER_PT
    total += kom_pts * SPT_PER_PT

    # Jersey bonuses (leader, sprint, mountain, youth, most_aggressive)
    for jersey in (jerseys or []):
        total += JERSEY.get(jersey, 0)

    # Team holdbonus
    if team_position and team_position in TEAM_BONUS:
        total += TEAM_BONUS[team_position]

    # Late arrival penalty
    if minutes_behind > 0:
        penalty = max(LATE_MAX, LATE_PER_MIN * int(minutes_behind))
        total += penalty

    return total


def captain_bonus(rider_score: int) -> int:
    """Additional bank bonus for captain — equals rider's positive growth."""
    return max(0, rider_score)


def etapebonus(riders_in_top15: int) -> int:
    """Bank bonus based on how many of your 8 riders finished in top 15."""
    eb = RULES["etapebonus"]
    return eb.get(str(riders_in_top15), 0)


def team_total_score(
    rider_scores: list[int],
    captain_index: int,
    top15_count: int,
) -> int:
    """
    Full team score for one stage including captain bonus and etapebonus.
    rider_scores: list of 8 individual rider scores
    captain_index: index in rider_scores of the captain
    top15_count: how many of the 8 riders finished in top 15
    """
    base = sum(rider_scores)
    cap  = captain_bonus(rider_scores[captain_index])
    eb   = etapebonus(top15_count)
    return base + cap + eb
