#!/usr/bin/env python3
"""
Bygger den FAKTISKE pointoversigt: hvad hver måplacering rent faktisk gav på
hver etape, taget direkte fra Holdets egen bogføring (fantasy-actions).

Baggrund: den hidtidige oversigt var et TEORETISK skema — etapeplaceringens
grundværdi plus sprint-/KOM-point fra TheFantasyTools tabel. Det er et
maksimum for den placeringsafhængige del, og det ramte skævt i begge
retninger i virkeligheden:

  * Stefan Küng vandt 18. etape og fik 320k, ikke 260k som skemaet sagde.
    Forskellen er holdbonus (60k), som skemaet med vilje udelader.
  * Eddie Dunbar vandt 19. etape og fik 435k mod skemaets 380k. Han hentede
    130k i betingede bonusser (holdbonus for BÅDE 1.- og 3.-pladsen, fordi
    hans hold havde to ryttere fremme, plus 50k for mest angrebsivrige), men
    "kun" 105k af de 180k klassifikationspoint skemaet forudsætter — man skal
    være først ved hver eneste spurt og stigning for at nå skemaets tal.

Her regnes i stedet baglæns fra det spillet faktisk udbetalte: for hver etape
findes rytteren på hver placering 1-15 (reglerne StagePosition1..15), og alt
hvad netop den rytter fik den dag lægges sammen og brydes ned på kategorier.

Output: web/data/vuelta2026_stage_points_actual.json
"""
from __future__ import annotations

import collections
import glob
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
H = ROOT / "data/sources/vuelta2026/holdet"
OUT = ROOT / "web/data/vuelta2026_stage_points_actual.json"

PLACEMENT = {1750 + i: i + 1 for i in range(15)}      # ruleId -> placering 1..15

CATEGORIES = [
    ("placement", lambda r: r in PLACEMENT),
    ("gc",        lambda r: 1765 <= r <= 1774),
    ("sprint",    lambda r: r == 1775),
    ("kom",       lambda r: r == 1776),
    ("jersey",    lambda r: 1777 <= r <= 1780),
    ("team_bonus", lambda r: 1786 <= r <= 1788),
    ("combative", lambda r: r == 1807),
    ("penalty",   lambda r: r in (1796, 1805, 1806, 1809)),
]


def categorise(rule_id):
    for name, test in CATEGORIES:
        if test(rule_id):
            return name
    return "other"


def main():
    emb = json.loads((H / "reference/schedule.json").read_text())["_embedded"]["events"]
    ev2stage = {int(e): int(re.match(r"(\d+)\.", v["name"]).group(1)) for e, v in emb.items()}
    ev2name = {int(e): v["name"] for e, v in emb.items()}
    persons = json.loads((H / "reference/players.json").read_text())["_embedded"]["persons"]
    name = {int(k): " ".join(x for x in (v.get("firstName"), v.get("lastName")) if x).strip()
            for k, v in persons.items()}

    stages = []
    for f in sorted(glob.glob(str(H / "fantasy_actions" / "*.json"))):
        eid = int(re.search(r"event_(\d+)", f).group(1))
        items = json.loads(Path(f).read_text())["items"]

        by_person = collections.defaultdict(lambda: collections.defaultdict(int))
        pos_of = {}
        for it in items:
            kr = it["amount"] * it["unitPriceChange"]
            by_person[it["personId"]][it["ruleId"]] += kr
            if it["ruleId"] in PLACEMENT:
                pos_of[PLACEMENT[it["ruleId"]]] = it["personId"]

        rows = []
        for pos in range(1, 16):
            pe = pos_of.get(pos)
            if pe is None:
                rows.append(None)
                continue
            parts = collections.Counter()
            for rid, kr in by_person[pe].items():
                parts[categorise(rid)] += kr
            rows.append({
                "pos": pos, "rider": name.get(pe, str(pe)),
                "total_kr": sum(parts.values()),
                **{k: parts.get(k, 0) for k, _ in CATEGORIES},
            })
        stages.append({"stage": ev2stage[eid], "name": ev2name[eid], "positions": rows})

    stages.sort(key=lambda s: s["stage"])
    OUT.write_text(json.dumps({
        "source": "Holdet.dk's egen bogføring (fantasy-actions) for Vueltaspillet 2026 — "
                  "hvad rytteren på hver måplacering FAKTISK fik udbetalt den dag, "
                  "inkl. betingede bonusser. Alle tal i kroner.",
        "note": "Dækker de 20 etaper Holdet kørte (etape 3 indgik ikke i spillet). "
                "'placement' er etapeplaceringens grundværdi, 'gc' løbsplacering, "
                "'sprint'/'kom' klassifikationspoint (3.000 kr/point), 'jersey' trøjer, "
                "'team_bonus' holdbonus (et hold kan få flere på samme etape, hvis det har "
                "flere ryttere i top 3), 'combative' mest angrebsivrige, 'penalty' fradrag.",
        "stages": stages,
    }, ensure_ascii=False, indent=2))

    print(f"Skrev {OUT.relative_to(ROOT)} — {len(stages)} etaper\n")
    print(f"{'etape':>5} {'vinder':26} {'total':>8}  nedbrydning")
    for s in stages:
        w = s["positions"][0]
        if not w:
            continue
        parts = " ".join(f"{k}={v/1000:.0f}k" for k, _ in CATEGORIES
                         if (v := w[k]))
        print(f"{s['stage']:>5} {w['rider']:26} {w['total_kr']/1000:>7.0f}k  {parts}")


if __name__ == "__main__":
    main()
