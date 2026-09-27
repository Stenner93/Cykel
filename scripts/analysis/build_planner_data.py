#!/usr/bin/env python3
"""
Datasæt til ruteplanlægger + gebyr/vækst-regner (skitse).

Gebyrregnestykket kræver kun to ting ud over pointskemaet:
  1. alle ryttere med deres pris (gebyret er en procentdel af købsprisen)
  2. dit nuværende hold (så et salg kan stilles op mod et køb)

Dertil lægges rytterarketyper, som udelukkende bruges til at FILTRERE
tabellen — aldrig til at sortere, rangere eller anbefale. De udledes af
CyclingOracles ratings (SPR/HLL/MTN/GC/ITT) som percentiler inden for netop
dette løbs startliste: en rytter får en mærkat hvis han ligger i feltets
øverste 20 % på den dimension. Derfor betyder "spurter" her "blandt de 20 %
bedste spurtere i DETTE felt", ikke "spurter" i absolut forstand.

Bevidst er der INGEN "udbrudsrytter"-mærkat. CO rater evne, ikke rolle, og
hvem der kommer i udbrud afhænger af holdsituation og pris — ikke af en
rating. Vil man finde udbrudskandidater, kombinerer man klatrer-mærkatet med
prisfilteret i tabellen. Eddie Dunbar, der vandt E19 fra udbrud, er netop
"klatrer til 4,15M"; Jakob Omrzel, der vandt E12, havde ingen CO-mærkat
overhovedet — ingen udledning ville have fanget ham, og det skal værktøjet
ikke lade som om.

Ingen modelforudsigelser indgår — værktøjet skal regne og filtrere, ikke vælge.

I et rigtigt løb kommer begge dele fra det daglige Holdet-snapshot. Her
bygges de af Vuelta 2026-data, så skitsen kan afprøves på virkelige tal.

Brug:
    python scripts/analysis/build_planner_data.py
"""
from __future__ import annotations

import bisect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from names import index, resolve  # noqa: E402  — delt navnematchning

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web/data"
ME = "Anders"

# CyclingOracle-dimension -> mærkat. COB (brosten) er udeladt for en grand
# tour: den fyrer på 40 ryttere uden at sige noget om en Vuelta-etape. Den
# ligger i rådataene og kan tages i brug til Klassiker Manager.
CO_TAGS = {"SPR": "spurter", "HLL": "puncher", "MTN": "klatrer",
           "GC": "gc", "ITT": "tempo"}
TAG_PERCENTILE = 0.80


def archetypes(riders):
    """Sæt CO-mærkater på rytterne. Ændrer listen på plads.

    Returnerer navnene på dem uden CO-data, så de kan rapporteres frem for at
    forsvinde lydløst — det var den fejl der engang tabte feltets største
    vækster ud af samtlige hold.
    """
    db = json.loads((WEB / "rider_database.json").read_text())["riders"]
    db = db if isinstance(db, list) else list(db.values())
    by_name = {r["name"]: r for r in db}
    idx = index(by_name)

    co = {}
    for r in riders:
        hit = resolve(r["name"], idx)
        ratings = by_name[hit].get("co_ratings") if hit else None
        if ratings:
            co[r["name"]] = ratings

    # Percentiler inden for DETTE felt, ikke mod hele verden.
    ladder = {d: sorted(v[d] for v in co.values() if v.get(d) is not None)
              for d in CO_TAGS}

    def pct(dim, value):
        col = ladder[dim]
        return bisect.bisect_left(col, value) / len(col) if col else 0.0

    for r in riders:
        ratings = co.get(r["name"])
        if not ratings:
            r["tags"], r["co"] = [], None
            continue
        r["co"] = {d: ratings.get(d) for d in CO_TAGS}
        r["tags"] = [tag for d, tag in CO_TAGS.items()
                     if ratings.get(d) is not None and pct(d, ratings[d]) >= TAG_PERCENTILE]

    return [r["name"] for r in riders if not r.get("co")]


def main():
    scores = json.loads((WEB / "vuelta2026_scores.json").read_text())
    teams = json.loads((WEB / "vuelta2026_teams.json").read_text())
    rules = json.loads((ROOT / "data/scoring_rules.json").read_text())

    riders = sorted(
        ({"name": r["name"], "team": r.get("team", ""), "price_m": r.get("price")}
         for r in scores["riders"] if r.get("price")),
        key=lambda r: -r["price_m"])

    no_co = archetypes(riders)
    if no_co:
        print(f"  uden CO-rating ({len(no_co)}): {', '.join(no_co)}", file=sys.stderr)

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
        "archetypes": {
            "source": "CyclingOracle-ratings som percentiler inden for dette løbs startliste. "
                      "En mærkat betyder 'blandt feltets øverste 20 % på den dimension' — "
                      "ikke en absolut vurdering. Kun til filtrering.",
            "percentile": TAG_PERCENTILE,
            "dims": CO_TAGS,
            "without_data": no_co,
        },
        "riders": riders,
    }
    (WEB / "vuelta2026_planner.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    counts = {}
    for r in riders:
        for t in r["tags"]:
            counts[t] = counts.get(t, 0) + 1
    print(f"Skrev web/data/vuelta2026_planner.json — {len(riders)} ryttere, "
          f"holdværdi {out['my_team']['value_m']:.2f}M + bank {out['my_team']['bank_m']:.2f}M")
    print("  mærkater: " + ", ".join(f"{t} {n}" for t, n in sorted(counts.items(), key=lambda kv: -kv[1]))
          + f" · uden mærkat {sum(1 for r in riders if not r['tags'])}")


if __name__ == "__main__":
    main()
