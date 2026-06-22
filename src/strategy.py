"""
Stage-type strategy rules for team optimization.

Each stage type has different optimal parameters for the LP optimizer:
  force_top_n       — how many consensus top picks to lock into SAFE + VALUE teams
  budget_rider_boost — multiplier on expected_pts for cheap riders (≤4M) in VALUE team
  attack_out_n       — how many consensus picks to force OUT of ATTACK (contrarian)
  rationale          — human-readable explanation
"""
from __future__ import annotations

STAGE_STRATEGY: dict[str, dict] = {
    "sprint": {
        "force_top_n":        3,
        "budget_rider_boost": 1.10,
        "attack_out_n":       2,
        "rationale": (
            "Sprint (TdF 2026): 7 flade etaper med 70 pt til vinderen + 2 bonusspurter á 20 pt "
            "— maks. 530K på én etape mod 380K på en bjergetape. "
            "Kaptajnvalget er rekordvigtigt: lås de 3 topsprintere ind i SIKKER+VÆRDI. "
            "ANGREB udelader #2+#3 → mørke heste med udbryderpotentiale."
        ),
    },
    "mountain": {
        "force_top_n":        2,
        "budget_rider_boost": 1.20,
        "attack_out_n":       2,
        "rationale": (
            "Bjerg: kun top-2 GC-favoritter er sikre. "
            "20 % boost på budgetryttere fanger udbryderspecialister. "
            "ANGREB udelader de 2 topfavoritter → ren udbryderstrategi."
        ),
    },
    "hilly": {
        "force_top_n":        2,
        "budget_rider_boost": 1.15,
        "attack_out_n":       2,
        "rationale": (
            "Bakket: to puncheurs låses ind. "
            "Bugetboost 15 % for at fange udbrydere. "
            "ANGREB = fuld udbryderstrategi uden favoritter."
        ),
    },
    "tt": {
        "force_top_n":        2,
        "budget_rider_boost": 1.00,
        "attack_out_n":       1,
        "rationale": (
            "Enkeltstart: to TT-specialister låses ind (forudsigelig disciplin). "
            "Ingen budgetboost — budgetryttere er sjældent top i TT. "
            "ANGREB udelader kun #2 (kontrarist mod den klare favorit)."
        ),
    },
    "cobbled": {
        "force_top_n":        2,
        "budget_rider_boost": 1.15,
        "attack_out_n":       2,
        "rationale": (
            "Brosten: to brostenspecialister låses ind. "
            "15 % budgetboost for at fange sprinterfolk og opportunister."
        ),
    },
}

# Fallback for unknown stage types
_DEFAULT_STRATEGY = STAGE_STRATEGY["hilly"]


def get_strategy(stage_type: str) -> dict:
    """Return full strategy dict for the given stage type."""
    return STAGE_STRATEGY.get(stage_type.lower(), _DEFAULT_STRATEGY)


def get_force_top_n(stage_type: str) -> int:
    """How many consensus top picks to lock into SAFE + VALUE."""
    return get_strategy(stage_type)["force_top_n"]


def get_budget_boost(stage_type: str) -> float:
    """Expected-pts multiplier for budget riders (≤4M) in the VALUE team."""
    return get_strategy(stage_type)["budget_rider_boost"]


def get_attack_out_n(stage_type: str) -> int:
    """How many top picks to force OUT of ATTACK (contrarian setup)."""
    return get_strategy(stage_type)["attack_out_n"]


def describe(stage_type: str) -> str:
    """Return human-readable rationale for the stage strategy."""
    return get_strategy(stage_type)["rationale"]
