#!/usr/bin/env python3
"""
Etapedata til de selvstændige HTML-udkast.

Udkastfilerne skal kunne åbnes hvor som helst — også sendt direkte i en
samtale — så dataene lægges ind i selve filen i stedet for at blive hentet.
Begge udkastbyggere bruger det samme udtræk herfra, så de ikke kan komme til
at vise hver sine tal.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Kun det udkastene rent faktisk viser — filerne skal kunne sendes som én fil.
KEEP = ["stage", "name", "category", "pcs_type", "profile_score", "vmeters",
        "climbs", "summit_finish", "intermediate_sprints",
        "winner_incl_team_bonus_kr"]


def payload(race: str = "vuelta2026") -> dict:
    src = json.loads((ROOT / f"web/data/{race}_stage_points.json").read_text())
    shp_path = ROOT / f"web/data/{race}_stage_shapes.json"
    shapes = json.loads(shp_path.read_text()) if shp_path.exists() else {"stages": {}, "max_vmeters": 0}

    stages = []
    for s in src["stages"]:
        row = {k: s.get(k) for k in KEEP}
        row["top5_kr"] = s["total_finish_kr"][4]
        row["top10_kr"] = s["total_finish_kr"][9]
        row["during_kr"] = s["total_during_kr"][0]
        sh = shapes["stages"].get(str(row["stage"]))
        row["shape"] = sh["shape"] if sh else None
        stages.append(row)

    return {
        "max_vmeters": shapes.get("max_vmeters", 0),
        "race": src.get("race"),
        "title": src.get("title"),
        "pcs_slug": src.get("pcs_slug"),
        "rest_after": src.get("rest_after", []),
        "stages": stages,
    }


def render(tmpl: Path, out: Path, race: str = "vuelta2026") -> dict:
    data = payload(race)
    out.write_text(tmpl.read_text().replace("__STAGES__", json.dumps(data, ensure_ascii=False)))
    return data
