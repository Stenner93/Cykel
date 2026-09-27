#!/usr/bin/env python3
"""
Bygger en selvstændig HTML-fil med layout-udkast til ruteplanlæggeren.

Filen skal kunne åbnes hvor som helst — også sendt direkte i en samtale —
så etapedataene lægges ind i selve filen i stedet for at blive hentet.
Skabelonen ligger i scripts/analysis/layout_drafts.tmpl.html og har ét
pladsholder-mærke, __STAGES__, hvor JSON'en skydes ind.

Brug:
    python scripts/analysis/build_layout_drafts.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMPL = Path(__file__).resolve().parent / "layout_drafts.tmpl.html"
SRC = ROOT / "web/data/vuelta2026_stage_points.json"
OUT = ROOT / "web/planlaegger-layouts.html"

# Kun det layoutene rent faktisk viser — filen skal være lille nok til at
# kunne sendes som én fil.
KEEP = ["stage", "name", "category", "pcs_type", "profile_score", "vmeters",
        "climbs", "summit_finish", "intermediate_sprints",
        "winner_incl_team_bonus_kr"]


def main():
    src = json.loads(SRC.read_text())
    stages = []
    for s in src["stages"]:
        row = {k: s.get(k) for k in KEEP}
        row["top5_kr"] = s["total_finish_kr"][4]
        row["top10_kr"] = s["total_finish_kr"][9]
        row["during_kr"] = s["total_during_kr"][0]
        stages.append(row)

    payload = {
        "race": src.get("race"),
        "title": src.get("title"),
        "pcs_slug": src.get("pcs_slug"),
        "rest_after": src.get("rest_after", []),
        "stages": stages,
    }
    html = TMPL.read_text().replace("__STAGES__", json.dumps(payload, ensure_ascii=False))
    OUT.write_text(html)
    print(f"Skrev {OUT.relative_to(ROOT)} — {len(stages)} etaper, {len(html)//1024} kB")


if __name__ == "__main__":
    main()
