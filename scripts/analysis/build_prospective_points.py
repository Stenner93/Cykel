#!/usr/bin/env python3
"""
Prospektiv pointberegner: laver pointoversigten for et løb FØR det køres,
ud fra ruteinformation alene + Holdets regler.

Det er meningen med værktøjet: til næste Grand Tour findes der hverken
Holdet-facit eller optakter endnu, så skemaet skal kunne bygges af det man
kan vide på forhånd — hvad der står i løbsbogen og på procyclingstats:

  1. etapens officielle kategori (flad / middel / bjerg+enkeltstart), som
     afgør hvilken pointskala der bruges ved mål
  2. de kategoriserede stigninger i rutens rækkefølge, og om den sidste er
     en målstigning
  3. antal indlagte spurter

Alt andet følger af reglerne. Skalaerne herunder er reverse-engineeret af
Vuelta 2026 og verificeret mod facit — se validate_vuelta() nederst.

Brug:
    python scripts/analysis/build_prospective_points.py data/routes/vuelta2026.json
    python scripts/analysis/build_prospective_points.py data/routes/<næste>.json \
        --out web/data/<næste>_stage_points.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ── Pointskalaer (UCI/løbsreglement), verificeret mod Vuelta 2026 ────────────
# Spurtpoint ved mål — tre officielle skalaer efter etapens kategori.
SPRINT_FINISH = {
    "flad":   [50, 30, 20, 18, 16, 14, 12, 10, 8, 7, 6, 5, 4, 3, 2],
    "middel": [30, 25, 22, 19, 17, 15, 13, 11, 9, 7, 6, 5, 4, 3, 2],
    "bjerg":  [20, 17, 15, 13, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
}
# Enkeltstart bruger bjerg-skalaen og har ingen indlagte spurter/stigninger.
SPRINT_INTERMEDIATE = [20, 17, 15, 13, 10]

# Bjergpoint pr. stigningskategori (position 1, 2, 3 …).
KOM_SCALE = {
    "HC": [20, 15, 10, 6, 4, 2],
    "1":  [10, 6, 4, 2, 1],
    "2":  [5, 3, 1],
    "3":  [3, 2, 1],
}

N_FINISH = 15          # etapeplaceringen betaler ned til 15. plads
N_INTERMEDIATE = 5     # indlagte spurter/stigninger betaler ned til 5. plads


def pad(scale, n):
    return [scale[i] if i < len(scale) else 0 for i in range(n)]


def stage_schedule(stage, rules):
    """Ét etape-dict fra rutefilen -> pointskema i kroner."""
    per_point = rules["sprint_kom_per_point"]
    placement = [rules["stage_position"][str(i + 1)] for i in range(N_FINISH)]

    climbs = [c for c in stage.get("climbs", []) if c]
    summit = bool(stage.get("summit_finish"))
    n_sprints = stage.get("intermediate_sprints", 0)

    # Målstigningen betales efter måplacering; resten undervejs.
    finish_climb = climbs[-1] if (summit and climbs) else None
    during_climbs = climbs[:-1] if finish_climb else climbs

    sprint_finish = pad(SPRINT_FINISH[stage["category"]], N_FINISH)
    kom_finish = pad(KOM_SCALE.get(finish_climb, []), N_FINISH) if finish_climb \
        else [0] * N_FINISH

    sprint_during = [v * n_sprints for v in pad(SPRINT_INTERMEDIATE, N_INTERMEDIATE)]
    kom_during = [sum(pad(KOM_SCALE[c], N_INTERMEDIATE)[i] for c in during_climbs)
                  for i in range(N_INTERMEDIATE)]

    kr = lambda xs: [v * per_point for v in xs]
    total_finish = [placement[i] + (sprint_finish[i] + kom_finish[i]) * per_point
                    for i in range(N_FINISH)]
    total_during = [(sprint_during[i] + kom_during[i]) * per_point
                    for i in range(N_INTERMEDIATE)]
    total_combined = [total_finish[i] + (total_during[i] if i < N_INTERMEDIATE else 0)
                      for i in range(N_FINISH)]

    return {
        "stage": stage["stage"],
        "name": stage.get("name", ""),
        "category": stage["category"],
        "climbs": "-".join(climbs) if climbs else "—",
        "summit_finish": summit,
        "placement_kr": placement,
        "points_finish_kr": kr(sprint_finish),
        "kom_finish_kr": kr(kom_finish),
        "points_during_kr": kr(sprint_during),
        "kom_during_kr": kr(kom_during),
        "total_finish_kr": total_finish,
        "total_during_kr": total_during,
        "total_combined_kr": total_combined,
        # Holdbonussen er reelt garanteret for vinderen (hans eget hold vinder
        # den), så den hører med i et realistisk bud på hvad en sejr giver.
        "winner_incl_team_bonus_kr": total_combined[0] + rules["team_bonus"]["1"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("route", help="rutefil, fx data/routes/vuelta2026.json")
    ap.add_argument("--out", help="output (default: web/data/<race>_stage_points.json)")
    ap.add_argument("--validate", metavar="FACIT",
                    help="sammenlign mod en eksisterende pointoversigt (facit)")
    ap.add_argument("--compare-actual", metavar="FIL",
                    help="sammenlign forudsagt sejrsværdi mod hvad vinderen faktisk fik")
    ap.add_argument("--compare-out", metavar="FIL",
                    help="skriv sammenligningen som JSON")
    args = ap.parse_args()

    route = json.loads(Path(args.route).read_text())
    rules = json.loads((ROOT / "data/scoring_rules.json").read_text())
    stages = [stage_schedule(s, rules) for s in route["stages"]]

    out = Path(args.out) if args.out else ROOT / f"web/data/{route['race']}_stage_points.json"
    out.write_text(json.dumps({
        "source": f"Beregnet på forhånd af {Path(args.route).name} "
                  "(etapekategori, stigninger, indlagte spurter) + holdet.dk's regler. "
                  "Ingen resultatdata indgår.",
        "note": "Alle tal i kroner. total_finish = etapeplacering + spurt- og bjergpoint ved "
                "mål. total_during = point ved indlagte spurter/stigninger (efter placering "
                "DER, ikke ved mål). total_combined = summen, altså et maksimum der "
                "forudsætter man er først overalt. Holdbonus, trøjer, klassement og mest "
                "angrebsivrige er betingede og indgår ikke — undtagen i "
                "winner_incl_team_bonus_kr, hvor vinderens garanterede holdbonus er lagt til.",
        "stages": stages,
    }, ensure_ascii=False, indent=2))
    try:
        shown = out.relative_to(ROOT)
    except ValueError:
        shown = out
    print(f"Skrev {shown} — {len(stages)} etaper")

    if args.validate:
        facit = {s["stage"]: s for s in json.loads(Path(args.validate).read_text())["stages"]}
        keys = ["points_finish_kr", "kom_finish_kr", "points_during_kr",
                "kom_during_kr", "total_finish_kr", "total_combined_kr"]
        ok = miss = 0
        for s in stages:
            f = facit.get(s["stage"])
            if not f:
                continue
            for k in keys:
                a = [v or 0 for v in s[k]]
                b = [v or 0 for v in f[k]]
                n = min(len(a), len(b))
                if a[:n] == b[:n]:
                    ok += 1
                else:
                    miss += 1
                    print(f"  AFVIGER E{s['stage']} {k}\n     beregnet {a[:n]}\n     facit    {b[:n]}")
        print(f"\nValidering mod {Path(args.validate).name}: {ok}/{ok + miss} kolonner matcher")

    if args.compare_actual:
        compare_actual(stages, args.compare_actual, rules, args.compare_out)


def compare_actual(stages, actual_path, rules, out_path):
    """Hold den prospektive forudsigelse op mod hvad vinderen faktisk fik.

    Forudsigelsen for en etapesejr = måplacering + spurt/KOM ved mål +
    holdbonussen, der reelt er garanteret fordi vinderens eget hold tager den.
    Alt derudover er betinget (klassement, trøjer, angrebspræmie, ekstra
    holdbonus hvis holdet har flere i top 3, og point hentet undervejs) og kan
    per definition ikke udledes af ruten.
    """
    act = {s["stage"]: s for s in json.loads(Path(actual_path).read_text())["stages"]}
    pro = {s["stage"]: s for s in stages}
    rows, exact = [], 0
    for st in sorted(act):
        w = (act[st]["positions"] or [None])[0]
        p = pro.get(st)
        if not w or not p:
            continue
        pred = p["total_finish_kr"][0] + rules["team_bonus"]["1"]
        during = w["sprint"] + w["kom"] - (p["points_finish_kr"][0] + p["kom_finish_kr"][0])
        extra = {
            "klassement": w["gc"], "trøjer": w["jersey"], "angrebspræmie": w["combative"],
            "ekstra holdbonus": max(0, w["team_bonus"] - rules["team_bonus"]["1"]),
            "point undervejs": during,
        }
        exact += pred == w["total_kr"]
        rows.append({"stage": st, "winner": w["rider"], "predicted_kr": pred,
                     "actual_kr": w["total_kr"], "diff_kr": w["total_kr"] - pred,
                     "extras": {k: v for k, v in extra.items() if v}})
    out = {"note": "Forudsagt sejrsværdi (kun ruteinformation + regler) mod Holdets faktiske "
                   "udbetaling. Afvigelser er udelukkende betingede poster.",
           "exact": exact, "stages_compared": len(rows), "rows": rows}
    if out_path:
        Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nForudsagt sejrsværdi vs. faktisk: rammer eksakt på {exact}/{len(rows)} etaper")
    for r in rows:
        why = ", ".join(f"{k} {v//1000:+d}k" for k, v in r["extras"].items()) or "—"
        print(f"  E{r['stage']:>2} {r['winner'][:22]:22} {r['predicted_kr']//1000:>4}k → "
              f"{r['actual_kr']//1000:>4}k  ({r['diff_kr']//1000:+4}k)  {why}")
    return out


if __name__ == "__main__":
    main()
