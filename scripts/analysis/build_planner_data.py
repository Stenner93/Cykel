#!/usr/bin/env python3
"""
Datasæt til ruteplanlægger + gebyr/vækst-regner (skitse).

Gebyrregnestykket kræver kun to ting ud over pointskemaet:
  1. alle ryttere med deres pris (gebyret er en procentdel af købsprisen)
  2. dit nuværende hold (så et salg kan stilles op mod et køb)

Ingen modelforudsigelser indgår — værktøjet skal regne, ikke vælge.

I et rigtigt løb kommer begge dele fra det daglige Holdet-snapshot. Her
bygges de af Vuelta 2026-data, så skitsen kan afprøves på virkelige tal.

Brug:
    python scripts/analysis/build_planner_data.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from names import index, resolve  # noqa: E402  — delt navnematchning

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web/data"
ME = "Anders"


def main():
    scores = json.loads((WEB / "vuelta2026_scores.json").read_text())
    teams = json.loads((WEB / "vuelta2026_teams.json").read_text())
    rules = json.loads((ROOT / "data/scoring_rules.json").read_text())

    riders = sorted(
        ({"name": r["name"], "team": r.get("team", ""), "price_m": r.get("price")}
         for r in scores["riders"] if r.get("price")),
        key=lambda r: -r["price_m"])

    mine = teams["teams"][ME]["current"]
    price = {r["name"]: r["price_m"] for r in riders}
    idx = index(price)
    roster, missing = [], []
    for n in mine["riders"]:
        hit = resolve(n, idx)
        if hit is None:
            missing.append(n)
        else:
            roster.append({"name": hit, "price_m": price[hit]})
    if missing:
        raise SystemExit(f"Ryttere uden pris — navnene matcher ikke: {missing}")

    out = {
        "source": "Skitsedata: priser og hold som de stod ved Vuelta 2026's afslutning. "
                  "I drift kommer begge dele fra det daglige Holdet-snapshot.",
        "rules": {
            "transfer_fee_pct": rules["transfer_fee_pct"],
            "budget": rules["budget"],
            "team_size": rules["team_size"],
            "max_per_team": rules["max_per_team"],
        },
        "my_team": {
            "riders": roster,
            "captain": mine.get("captain"),
            "bank_m": mine.get("bank_M", 0.0),
            "value_m": round(sum(r["price_m"] for r in roster), 3),
        },
        "riders": riders,
    }
    (WEB / "vuelta2026_planner.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"Skrev web/data/vuelta2026_planner.json — {len(riders)} ryttere, "
          f"holdværdi {out['my_team']['value_m']:.2f}M + bank {out['my_team']['bank_m']:.2f}M")


if __name__ == "__main__":
    main()
