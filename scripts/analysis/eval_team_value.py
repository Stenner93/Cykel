#!/usr/bin/env python3
"""
Rekonstruktion af den FAKTISKE holdværdi, runde for runde — Vuelta 2026.

Hvorfor et nyt script: eval_managers.py laver en vækst-ATTRIBUTION (sum af de 8
rytteres tilnærmede `actual`-vækst, kaptajn dobbelt). Det måler kvaliteten af
ryttervalgene, men det er hverken Holdets tal eller Holdets regnestykke, og
rangeringen derfra matchede ikke den virkelige slutstilling. To grunde:

  1. `actual` i predictions-filen er afledt af prisforskelle mellem scrapes og
     afviger fra Holdets egne tal på 2.568 rytter-etape-rækker.
  2. Attributionen ignorerer transfergebyr, etapebonus, kaptajnbonus og
     bankrente — og især transfergebyret er stort nok til at vende rangeringen.

Her bruges i stedet Holdets egne fantasy-actions (ruleId + amount ×
unitPriceChange), der er præcis det spillet selv bogførte, koblet på
lineup-snapshots via personId — altså ingen navnematchning overhovedet.

Regnestykke pr. runde (jf. holdet.dk's officielle regler):
    rytterværdi  += Σ actions for hver ejet rytter
    bank         += kaptajnbonus (kaptajnens vækst, kun hvis positiv)
    bank         += etapebonus efter antal ejede ryttere i etapens top 15
    bank         -= 1% transfergebyr af de indkøbte rytteres værdi
    bank         += 0,5% bankrente
    holdværdi     = Σ rytterpriser + bank

Output: data/analysis/team_value_vuelta.json + printet sammendrag.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

RACES = {
    "vuelta2026": {
        "label": "Vuelta 2026",
        "holdet_dir": ROOT / "data/sources/vuelta2026/holdet",
        "rules": ROOT / "data/scoring_rules.json",
        "labels": {7271757: "os (Anders)", 7272262: "Kasper",
                   7285351: "optakt (TheFantasyTool)"},
        "out": ROOT / "data/analysis/team_value_vuelta.json",
    },
}

RULE_TOP15 = 1795          # markør-regel: rytteren var i etapens top 15


def load_actions(H):
    """-> ({stage: {personId: kr}}, {stage: set(personId i top15)})"""
    growth = collections.defaultdict(lambda: collections.defaultdict(int))
    top15 = collections.defaultdict(set)
    for f in sorted(glob.glob(str(H / "fantasy_actions" / "*.json"))):
        stage = int(re.search(r"stage_(\d+)_", f).group(1))
        for it in json.loads(Path(f).read_text()).get("items", []):
            growth[stage][it["personId"]] += it["amount"] * it["unitPriceChange"]
            if it["ruleId"] == RULE_TOP15:
                top15[stage].add(it["personId"])
    return growth, top15


def load_players(H):
    """-> (playerId -> personId, personId -> startPrice, personId -> slutpris, personId -> navn)"""
    ref = json.loads((H / "reference/players.json").read_text())
    persons = ref["_embedded"]["persons"]
    pl2pe, start, final, name = {}, {}, {}, {}
    for it in ref["items"]:
        pid, pe = it["id"], it["personId"]
        pl2pe[pid] = pe
        start[pe] = it["startPrice"]
        final[pe] = it["price"]
    for k, v in persons.items():
        name[int(k)] = " ".join(x for x in (v.get("firstName"), v.get("lastName")) if x).strip()
    return pl2pe, start, final, name


def team_rounds(H, tid, pl2pe):
    """-> {runde: {'roster': set(personId), 'captain': personId|None}}"""
    out = {}
    tdir = H / "teams" / str(tid)
    for rf in sorted(os.listdir(tdir)):
        rnd = int(rf.split("_")[1].split(".")[0])
        roster, captain = set(), None
        for it in json.loads((tdir / rf).read_text())["items"]:
            pe = pl2pe.get(it["playerId"])
            if pe is None:
                raise SystemExit(f"ukendt playerId {it['playerId']} i {rf} (hold {tid})")
            roster.add(pe)
            if it["role"] == "captain":
                captain = pe
        out[rnd] = {"roster": roster, "captain": captain}
    return out


def simulate(rounds, growth, top15, start, rules):
    """Kør Holdets regnestykke runde for runde."""
    etapebonus = {int(k): v for k, v in rules["etapebonus"].items()}
    fee_pct = rules["transfer_fee_pct"]
    interest = rules["bank_interest_pct"]

    price = dict(start)                       # personId -> aktuel værdi
    bank = 0.0
    prev_roster = None
    per_round, transfers = [], 0
    cum_fees = cum_cap = cum_stagebonus = 0.0

    for rnd in sorted(rounds):
        roster = rounds[rnd]["roster"]
        captain = rounds[rnd]["captain"]

        # Handel: salg giver rytterens aktuelle værdi i kontanter, køb koster
        # tilsvarende, og oveni betales 1% i gebyr af de KØBTE rytteres værdi.
        # Kontantbevægelsen skal med, ellers "forsvinder" der værdi hver gang
        # et hold skifter dyre ryttere ud med billige (holdværdi = summen af
        # rytterpriser PLUS bank, ikke rytterpriserne alene).
        fee = 0.0
        if prev_roster is not None:
            bought = roster - prev_roster
            sold = prev_roster - roster
            transfers += len(bought)
            proceeds = sum(price.get(pe, 0) for pe in sold)
            cost = sum(price.get(pe, 0) for pe in bought)
            fee = cost * fee_pct
            bank += proceeds - cost - fee
            cum_fees += fee

        g = growth.get(rnd, {})
        rider_growth = sum(g.get(pe, 0) for pe in roster)

        cap_growth = g.get(captain, 0) if captain else 0
        cap_bonus = max(0, cap_growth)        # "der gives kun positiv kaptajnbonus"
        bank += cap_bonus
        cum_cap += cap_bonus

        n_top15 = len(roster & top15.get(rnd, set()))
        sb = etapebonus.get(n_top15, 0)
        bank += sb
        cum_stagebonus += sb

        for pe, delta in g.items():           # opdatér alle priser i feltet
            price[pe] = price.get(pe, 0) + delta
        bank *= (1 + interest)

        value = sum(price.get(pe, 0) for pe in roster) + bank
        per_round.append({
            "round": rnd, "value_M": round(value / 1e6, 3),
            "rider_growth_M": round(rider_growth / 1e6, 3),
            "captain_bonus_M": round(cap_bonus / 1e6, 3),
            "stage_bonus_M": round(sb / 1e6, 3), "top15": n_top15,
            "fee_M": round(fee / 1e6, 3), "bank_M": round(bank / 1e6, 3),
        })
        prev_roster = roster

    return {
        "final_value_M": round(per_round[-1]["value_M"], 2),
        "transfers": transfers,
        "fees_M": round(cum_fees / 1e6, 2),
        "captain_bonus_M": round(cum_cap / 1e6, 2),
        "stage_bonus_M": round(cum_stagebonus / 1e6, 2),
        "bank_M": round(bank / 1e6, 2),
        "per_round": per_round,
        "curve_M": [r["value_M"] for r in per_round],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--race", default="vuelta2026", choices=sorted(RACES))
    args = ap.parse_args()
    cfg = RACES[args.race]
    H = cfg["holdet_dir"]

    rules = json.loads(cfg["rules"].read_text())
    growth, top15 = load_actions(H)
    pl2pe, start, final, name = load_players(H)

    # Kontrol: startpris + al bogført vækst skal ramme Holdets egen slutpris.
    recon = {pe: start[pe] + sum(growth[s].get(pe, 0) for s in growth) for pe in start}
    off = [(name.get(pe, pe), final[pe], recon[pe])
           for pe in start if abs(recon[pe] - final[pe]) > 1000]
    print(f"Kontrol: {len(start) - len(off)}/{len(start)} ryttere rammer Holdets slutpris eksakt")
    for nm, f, r in off[:5]:
        print(f"   afvigelse: {nm:26s} holdet={f/1000:8.0f}k  rekonstrueret={r/1000:8.0f}k")

    teams = {}
    for tid in sorted(int(t) for t in os.listdir(H / "teams")):
        teams[tid] = simulate(team_rounds(H, tid, pl2pe), growth, top15, start, rules)
        teams[tid]["label"] = cfg["labels"].get(tid, "top-10")

    ranking = sorted(teams.items(), key=lambda kv: -kv[1]["final_value_M"])
    out = {
        "note": "Faktisk holdværdi rekonstrueret af Holdets egne fantasy-actions "
                "(ruleId × amount × unitPriceChange) + officielle regler for "
                "transfergebyr, etapebonus, kaptajnbonus og bankrente. "
                "Dækker etape 1-20 (runde 21's lineup var ikke tilgængelig).",
        "ranking": [{"team_id": t, "label": v["label"], "final_value_M": v["final_value_M"],
                     "transfers": v["transfers"], "fees_M": v["fees_M"],
                     "captain_bonus_M": v["captain_bonus_M"],
                     "stage_bonus_M": v["stage_bonus_M"], "bank_M": v["bank_M"]}
                    for t, v in ranking],
        "curves_M": {str(t): v["curve_M"] for t, v in teams.items()},
        "per_round": {str(t): v["per_round"] for t, v in teams.items()},
    }
    cfg["out"].write_text(json.dumps(out, indent=2, ensure_ascii=False))

    print(f"\nFAKTISK HOLDVÆRDI — {cfg['label']} (Holdets egne tal, etape 1-20)\n")
    print(f"{'#':>2} {'hold':>9} {'label':26} {'værdi(M)':>9} {'transf':>7} "
          f"{'gebyr(M)':>9} {'kaptajn(M)':>11} {'etapebon(M)':>12}")
    for i, (t, v) in enumerate(ranking, 1):
        print(f"{i:>2} {t:>9} {v['label']:26} {v['final_value_M']:>9.2f} {v['transfers']:>7} "
              f"{v['fees_M']:>9.2f} {v['captain_bonus_M']:>11.2f} {v['stage_bonus_M']:>12.2f}")
    print(f"\nWrote {cfg['out'].relative_to(ROOT)}")


if __name__ == "__main__":
    main()
