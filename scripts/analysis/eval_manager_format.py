#!/usr/bin/env python3
"""
Manager-formatet regnet igennem — forarbejde til Klassiker Manager 2027.

To kilder:
  1. Vuelta Manager 2026 (Holdets eget pointfacit, ruleset "Cycling classic
     2025"). Det er SAMME regelsæt-familie som Klassiker Manager, så det
     viser hvordan pointskalaen faktisk fordeler sig.
  2. Klassikerne 2026, simuleret: de rigtige PCS-resultater for spillets 13
     løb, dine kategorier fra Master-arket, og Manager-formatets
     placeringsskala fra punkt 1. Det giver et bud på hvad hver rytter
     ville have scoret, og dermed hvad en plads i hver kategori er værd.

Holdets sammensætning og transferloft er dine oplysninger: 2 / 3 / 3 / 4
ryttere fra Kategori 1-4, præcis, og 35 udskiftninger fra runde 2 (det
første hold er gratis). Én runde = ét løb.

Klassikerspillets pointsystem (fra spillets regelside, givet af Anders):
  - Placering: 350, 300, 250, 210, 180, 160, 150, 140, 130, 120 for top-10,
    så 100, 95, 90, 85, 80 for 11.-15., derefter -2 pr. plads til 30. (50),
    -1 pr. plads til 50. (30), 25 for 51.-55., 20 for 56.-60., 15 for
    61.-65., og så fortløbende 10, 9, 8 … fra 66. til 1 point på 75.;
    76. og ned giver 0.
  - Monumentbonus til top-10 i Sanremo, Flandern, Roubaix og Liège:
    150, 125, 100, 85, 75, 65, 55, 45, 35, 25.
  - Holdbonus: 25 point til vinderens hold.

Bekræftet af Anders:
  - Holdbonus til alle fra vinderens hold, vinderen inklusive.
  - Kaptajnen fordobler sine point. Kaptajnen kan skiftes frit mellem ens
    egne ryttere; at skifte selve rytteren ud koster en kontrakt som alle andre.
  - DNF/DNS giver 0.
  - Kategorierne er faste hele sæsonen.
  - Kalenderen antages at være de samme 13 løb i 2027.

Stadig antaget:
  - Holdbonussen gives kun til ryttere der startede løbet.
  - Kaptajnens fordobling gælder også monument- og holdbonus.

Alle "optimale" hold her er bagklogskab: de kender resultaterne på forhånd.
De siger ikke hvad man burde have valgt, kun hvor meget der var at hente,
og hvor det lå.

Brug:
    python scripts/analysis/eval_manager_format.py
"""
from __future__ import annotations

import collections
import glob
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MGR = ROOT / "data/sources/vueltamanager2026/holdet"
CLASSICS = ROOT / "data/sources/classics"
OUT = ROOT / "data/analysis/manager_format.json"

SLOTS = {1: 2, 2: 3, 3: 3, 4: 4}      # ryttere pr. kategori — præcis
TRANSFERS = 35                          # fra runde 2; første hold er gratis
CAT_OF_POS = {265: 1, 266: 2, 267: 3, 268: 4}


# ── Klassikerspillets pointsystem ────────────────────────────────────────
TOP10 = [350, 300, 250, 210, 180, 160, 150, 140, 130, 120]
P11_15 = [100, 95, 90, 85, 80]
MONUMENTS = {"milano-sanremo", "ronde-van-vlaanderen", "paris-roubaix", "liege-bastogne-liege"}
MONUMENT_BONUS = [150, 125, 100, 85, 75, 65, 55, 45, 35, 25]
TEAM_BONUS = 25


def classics_scale(pos):
    """Placeringspoint i Klassiker Manager. None (DNF/DNS) giver 0."""
    if not pos:
        return 0
    if pos <= 10:
        return TOP10[pos - 1]
    if pos <= 15:
        return P11_15[pos - 11]
    if pos <= 30:
        return 78 - 2 * (pos - 16)      # 16.=78 … 30.=50
    if pos <= 50:
        return 49 - (pos - 31)          # 31.=49 … 50.=30
    if pos <= 55:
        return 25
    if pos <= 60:
        return 20
    if pos <= 65:
        return 15
    # Fortløbende nedad fra 66.: 10, 9, 8 … 1 point på 75.-pladsen, 0 derefter.
    return max(0, 10 - (pos - 66))


# ── Vuelta Managers pointskala, læst af Holdets egen bogføring ───────────
def manager_scale():
    """-> (funktion placering -> point, regelnavn -> point)"""
    names, pts = {}, {}
    for f in glob.glob(str(MGR / "fantasy_actions/*.json")):
        d = json.loads(Path(f).read_text())
        names.update({int(k): v["name"] for k, v in d["_embedded"]["rules"].items()})
        for x in d["items"]:
            if x["unitPointsChange"]:
                pts[names.get(x["ruleId"], str(x["ruleId"]))] = x["unitPointsChange"]
    single = {int(m.group(1)): v for k, v in pts.items()
              if (m := re.fullmatch(r"StagePosition(\d+)", k))}
    bands = [(int(m.group(1)), int(m.group(2)), v) for k, v in pts.items()
             if (m := re.fullmatch(r"StagePosition(\d+)to(\d+)", k))]

    def f(pos):
        if pos is None:
            return 0
        if pos in single:
            return single[pos]
        for a, b, v in bands:
            if a <= pos <= b:
                return v
        return 0
    return f, pts


# ── 1. Vuelta Manager: hvor kom pointene fra ─────────────────────────────
def vuelta_manager():
    names = {}
    by_rule = collections.Counter()
    by_band = collections.Counter()
    for f in sorted(glob.glob(str(MGR / "fantasy_actions/*.json"))):
        d = json.loads(Path(f).read_text())
        names.update({int(k): v["name"] for k, v in d["_embedded"]["rules"].items()})
        for x in d["items"]:
            p = x["amount"] * x["unitPointsChange"]
            if not p:
                continue
            n = names.get(x["ruleId"], "")
            by_rule[re.sub(r"\d+(to\d+)?$", "", n)] += p
            m = re.match(r"StagePosition(\d+)", n)
            if m:
                q = int(m.group(1))
                band = ("1-3" if q <= 3 else "4-10" if q <= 10 else "11-15" if q <= 15
                        else "16-30" if q <= 30 else "31-75" if q <= 75 else "76+")
                by_band[band] += p

    pl = json.loads((MGR / "reference/players.json").read_text())
    emb = pl["_embedded"]
    nm = {int(k): f"{v.get('firstName','')} {v.get('lastName','')}".strip()
          for k, v in emb["persons"].items()}
    per_cat = collections.defaultdict(list)
    for r in pl["items"]:
        per_cat[CAT_OF_POS[r["positionId"]]].append(
            {"name": nm.get(r["personId"], "?"), "points": r["points"],
             "popularity": round(r["popularity"], 4)})
    cats = {}
    for c, rs in sorted(per_cat.items()):
        rs.sort(key=lambda r: -r["points"])
        n = SLOTS[c]
        cats[c] = {
            "riders": len(rs),
            "slots": n,
            "best_slot_avg": round(sum(r["points"] for r in rs[:n]) / n),
            "median_rider": rs[len(rs) // 2]["points"],
            "top": rs[:5],
        }
    # Lavt ejede ryttere der scorede godt — de afgørende valg
    sleepers = sorted((r | {"cat": c} for c, rs in per_cat.items() for r in rs
                       if r["popularity"] < 0.05), key=lambda r: -r["points"])[:8]
    tot = sum(by_rule.values())
    stage_tot = sum(by_band.values())
    return {
        "total_points": tot,
        "by_source": {k: round(v / tot, 4) for k, v in by_rule.most_common()},
        "stage_points_by_band": {b: round(by_band[b] / stage_tot, 4)
                                 for b in ["1-3", "4-10", "11-15", "16-30", "31-75", "76+"]},
        "categories": cats,
        "low_owned_high_scorers": sleepers,
    }


# ── 2. Klassikerne 2026 simuleret ────────────────────────────────────────
def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).replace("ł", "l").replace("Ł", "L")
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


def pcs_key(name):
    """'VAN DER POEL Mathieu' -> ('m', 'van der poel'). Efternavnet står med versaler."""
    toks = (name or "").split()
    sur = [t for t in toks if t.isupper() or not any(ch.isalpha() for ch in t)]
    first = [t for t in toks if t not in sur]
    if not sur or not first:
        return None
    return norm(first[0])[:1], norm(" ".join(sur))


def notes_key(name):
    """'T. L. Andresen' -> ('t', 'andresen'); 'M. Van Der Poel' -> ('m', 'van der poel')."""
    toks = (name or "").replace(".", ". ").split()
    ini = [t for t in toks if t.endswith(".")]
    sur = [t for t in toks if not t.endswith(".")]
    if not ini or not sur:
        return None
    return norm(ini[0])[:1], norm(" ".join(sur))


def classics_2026(scale):
    res = json.loads((CLASSICS / "results.json").read_text())
    cats_raw = json.loads((CLASSICS / "categories_2026.json").read_text())["categories"]
    races = sorted([r for r in res["races"] if r["year"] == 2026],
                   key=lambda r: [x[0] for x in RACE_ORDER].index(r["race_slug"]))
    order = [r["race_slug"] for r in races]

    # Kategori pr. rytter: dine noter skriver "T. Pogačar", PCS "POGAČAR Tadej".
    # Først på fornavnsinitial + hele efternavnet; dernæst på initial + SIDSTE
    # efternavnsled, fordi noterne skriver sammensatte efternavne som en
    # ekstra initial ("I. G. Cortina" = García Cortina, "S. K. Andersen" =
    # Kragh Andersen). Den anden nøgle bruges kun hvis den er entydig.
    by_key, by_last, clash = {}, {}, set()
    for n, c in cats_raw.items():
        k = notes_key(n)
        if not k:
            continue
        by_key[k] = (n, c)
        lk = (k[0], k[1].split()[-1])
        if lk in by_last and by_last[lk][0] != n:
            clash.add(lk)
        by_last[lk] = (n, c)
    for lk in clash:
        by_last.pop(lk, None)

    def lookup(pcs_name):
        k = pcs_key(pcs_name)
        if not k:
            return None
        return by_key.get(k) or by_last.get((k[0], k[1].split()[-1]))
    pts = collections.defaultdict(lambda: [0] * len(order))   # rider_slug -> pr. løb
    starts = collections.Counter()
    meta, unmatched = {}, set()
    band = collections.Counter()
    source = collections.Counter()   # placering / monument / hold

    # Vinderens hold pr. løb — til holdbonussen.
    winner_team = {}
    for x in res["results"]:
        if x["year"] == 2026 and x["rank"] == 1:
            winner_team[x["race_slug"]] = x["team"]

    for x in res["results"]:
        if x["year"] != 2026:
            continue
        hit = lookup(x["rider_name"])
        if not hit:
            if x["rank"] and x["rank"] <= 30:
                unmatched.add(x["rider_name"])
            continue
        i = order.index(x["race_slug"])
        place = scale(x["rank"])
        mon = (MONUMENT_BONUS[x["rank"] - 1]
               if x["race_slug"] in MONUMENTS and x["rank"] and x["rank"] <= 10 else 0)
        team = TEAM_BONUS if x["team"] and x["team"] == winner_team.get(x["race_slug"]) else 0
        pts[x["rider_slug"]][i] = place + mon + team
        source["placering"] += place
        source["monumentbonus"] += mon
        source["holdbonus"] += team
        starts[x["rider_slug"]] += 1
        meta[x["rider_slug"]] = {"name": hit[0], "cat": hit[1]}
        if x["rank"]:
            q = x["rank"]
            band["1-3" if q <= 3 else "4-10" if q <= 10 else "11-15" if q <= 15
                 else "16-30" if q <= 30 else "31-65" if q <= 65 else "66-75"] += place

    riders = {s: {**meta[s], "points": pts[s], "total": sum(pts[s]), "starts": starts[s]}
              for s in meta}
    by_cat = collections.defaultdict(list)
    for s, r in riders.items():
        by_cat[r["cat"]].append(s)
    for c in by_cat:
        by_cat[c].sort(key=lambda s: -riders[s]["total"])

    R = len(order)

    def best(c, lo, hi, n):
        return sorted(by_cat[c], key=lambda s: -sum(riders[s]["points"][lo:hi]))[:n]

    def score(team_per_race, captain=True):
        tot = cap = 0
        for i, team in enumerate(team_per_race):
            p = [riders[s]["points"][i] for s in team]
            tot += sum(p)
            if captain and p:
                cap += max(p)
        return tot + cap, cap

    # a) Intet skifte: bedste 2/3/3/4 for hele sæsonen
    static = [s for c, n in SLOTS.items() for s in best(c, 0, R, n)]
    static_pts, static_cap = score([static] * R)

    # b) Ubegrænset: bedste hold til hvert enkelt løb
    per_race = [[s for c, n in SLOTS.items() for s in best(c, i, i + 1, n)] for i in range(R)]
    unl_pts, unl_cap = score(per_race)
    unl_tr = sum(len(set(per_race[i]) - set(per_race[i - 1])) for i in range(1, R))

    # c) To blokke: brosten (til og med Roubaix) og Ardennerne
    split = 1 + max(i for i, r in enumerate(races) if r["block"] == "brosten")
    b1 = [s for c, n in SLOTS.items() for s in best(c, 0, split, n)]
    b2 = [s for c, n in SLOTS.items() for s in best(c, split, R, n)]
    blk_pts, _ = score([b1] * split + [b2] * (R - split))
    blk_tr = len(set(b2) - set(b1))

    # d) 35 skifter, fordelt grådigt efter gevinst pr. skifte (bagklogskab
    #    med et vindue på W løb frem). Tærsklen λ søges så loftet holdes.
    def greedy(W, lam):
        team = {c: best(c, 0, W, n) for c, n in SLOTS.items()}
        plan, used = [], 0
        for i in range(R):
            if i > 0:
                for c, n in SLOTS.items():
                    win = lambda s: sum(riders[s]["points"][i:i + W])
                    tgt = best(c, i, i + W, n)
                    outs = sorted([s for s in team[c] if s not in tgt], key=win)
                    ins = sorted([s for s in tgt if s not in team[c]], key=win, reverse=True)
                    for o, nw in zip(outs, ins):
                        if used >= TRANSFERS or win(nw) - win(o) < lam:
                            break
                        team[c] = [nw if s == o else s for s in team[c]]
                        used += 1
            plan.append([s for c in SLOTS for s in team[c]])
        return score(plan)[0], used, plan

    best_g = (0, 0, None, None, None)
    for W in (1, 2, 3, 4):
        for lam in range(0, 400, 10):
            p, u, plan = greedy(W, lam)
            if u <= TRANSFERS and p > best_g[0]:
                best_g = (p, u, W, lam, plan)
    g_pts, g_used, g_W, g_lam, g_plan = best_g
    used_per_round = [0] + [len(set(g_plan[i]) - set(g_plan[i - 1])) for i in range(1, R)]

    # d2) Hvor gør et skifte mest gavn? Kategori for kategori: hvor mange
    #     point giver de første k skifter i DEN kategori, hvis man bruger dem
    #     bedst muligt? Det er den MARGINALE værdi — hvad skifte nr. 1, 5, 10
    #     er værd — og det er den man skal sammenligne på tværs, ikke et
    #     gennemsnit. (Kaptajnen er udeladt her; den går på tværs af
    #     kategorierne.)
    def cat_points(plan):
        return sum(sum(riders[s]["points"][i] for s in plan[i]) for i in range(R))

    def cat_greedy(c, n, k, W, lam, start):
        team = list(start)
        plan, used = [], 0
        for i in range(R):
            if i > 0 and used < k:
                win = lambda s: sum(riders[s]["points"][i:i + W])
                tgt = best(c, i, i + W, n)
                outs = sorted([s for s in team if s not in tgt], key=win)
                ins = sorted([s for s in tgt if s not in team], key=win, reverse=True)
                for o, nw in zip(outs, ins):
                    if used >= k or win(nw) - win(o) < lam:
                        break
                    team = [nw if s == o else s for s in team]
                    used += 1
            plan.append(list(team))
        return cat_points(plan), used

    KS = [0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40]
    curves = {}
    for c, n in SLOTS.items():
        starts_opts = [best(c, 0, R, n)] + [best(c, 0, W, n) for W in (1, 2, 3, 4)]
        curve = {}
        prev = 0
        for k in KS:
            top = cat_points([best(c, 0, R, n)] * R) if k == 0 else prev
            if k:
                for st in starts_opts:
                    for W in (1, 2, 3, 4):
                        for lam in (0, 20, 40, 60, 100, 150, 200, 300):
                            pp, u = cat_greedy(c, n, k, W, lam, st)
                            if u <= k and pp > top:
                                top = pp
            curve[k] = top
            prev = top
        curves[c] = curve

    per_cat_value = {}
    for c, n in SLOTS.items():
        cv = curves[c]
        races_best = [best(c, i, i + 1, n) for i in range(R)]
        free_pts = sum(sum(riders[s]["points"][i] for s in races_best[i]) for i in range(R))
        need = sum(len(set(races_best[i]) - set(races_best[i - 1])) for i in range(1, R))
        g_tr = sum(len(set(x for x in g_plan[i] if riders[x]["cat"] == c)
                       - set(x for x in g_plan[i - 1] if riders[x]["cat"] == c)) for i in range(1, R))
        # Kat 4-tesen: de n med flest starter blandt dem der faktisk scorer
        many = sorted([s for s in by_cat[c] if riders[s]["total"] > 0],
                      key=lambda s: (-riders[s]["starts"], -riders[s]["total"]))[:n]
        per_cat_value[c] = {
            "kurve": cv,
            "de_første_3": cv[3] - cv[0],
            "de_første_5": cv[5] - cv[0],
            "skifte_6_til_10_pr_stk": round((cv[10] - cv[5]) / 5),
            "skifte_11_til_20_pr_stk": round((cv[20] - cv[10]) / 10),
            "fast_bedste_hold": cv[0],
            "bedste_hvert_løb": free_pts,
            "skifter_det_kræver": need,
            "gennemsnit_pr_skifte": round((free_pts - cv[0]) / max(1, need)),
            "skifter_i_35_planen": g_tr,
            "flest_starter_hold": sum(sum(riders[s]["points"]) for s in many),
            "flest_starter_navne": [f"{riders[s]['name']} ({riders[s]['starts']})" for s in many],
        }

    # Eksempel: det faste hold i Kat 1 og Kat 4, løb for løb. Det er her
    # forskellen kan ses med det blotte øje.
    examples = {}
    for c in (1, 4):
        fixed = best(c, 0, R, SLOTS[c])
        rows = []
        for s in fixed:
            rows.append({"name": riders[s]["name"], "points": riders[s]["points"],
                         "starts": riders[s]["starts"]})
        best_avail = [max((riders[x]["points"][i] for x in by_cat[c]), default=0) for i in range(R)]
        examples[c] = {"fixed": rows, "best_available_per_race": best_avail}

    # e) Kategoriernes værdi: hvad gav de bedste n pr. kategori, og hvor
    #    afhængigt var det af antal starter?
    cats = {}
    for c, n in SLOTS.items():
        top = by_cat[c][:max(n, 5)]
        cats[c] = {
            "riders": len(by_cat[c]),
            "slots": n,
            "best_slot_avg": round(sum(riders[s]["total"] for s in by_cat[c][:n]) / n),
            "top": [{"name": riders[s]["name"], "total": riders[s]["total"],
                     "starts": riders[s]["starts"]} for s in top],
        }
    # Kat 4-tesen fra dine noter: "Kat4 skal bare være nogle med mange løb".
    k4 = [riders[s] for s in by_cat[4] if riders[s]["total"] > 0]
    k4_many = [r for r in k4 if r["starts"] >= 8]
    k4_best = sorted(k4, key=lambda r: -r["total"])[:20]

    bt = sum(band.values()) or 1
    return {
        "races": [{"race": r["race"], "block": r["block"], "date": r.get("date"),
                   "profile_score": r.get("profile_score")} for r in races],
        "matched_riders": len(riders),
        "unmatched_top30": sorted(unmatched),
        "points_by_band": {b: round(band[b] / bt, 4)
                           for b in ["1-3", "4-10", "11-15", "16-30", "31-65", "66-75"]},
        "points_by_source": {k: v for k, v in source.most_common()},
        "categories": cats,
        "kat4_thesis": {
            "share_of_kat4_points_from_riders_with_8plus_starts":
                round(sum(r["total"] for r in k4_many) / max(1, sum(r["total"] for r in k4)), 3),
            "top20_kat4_median_starts": sorted(r["starts"] for r in k4_best)[len(k4_best) // 2]
                if k4_best else None,
        },
        "transfer_value_by_category": per_cat_value,
        "examples": examples,
        "scenarios": {
            "ingen_skifter": {"points": static_pts, "captain_points": static_cap, "transfers": 0,
                              "team": [riders[s]["name"] for s in static]},
            "to_blokke": {"points": blk_pts, "transfers": blk_tr},
            "35_skifter_graadigt": {"points": g_pts, "transfers": g_used, "window": g_W,
                                    "min_gain_per_transfer": g_lam,
                                    "transfers_per_round": used_per_round},
            "ubegraenset": {"points": unl_pts, "captain_points": unl_cap, "transfers": unl_tr},
        },
    }


RACE_ORDER = [
    ("omloop-het-nieuwsblad",), ("strade-bianche",), ("milano-sanremo",),
    ("classic-brugge-de-panne",), ("e3-harelbeke",), ("gent-wevelgem",),
    ("dwars-door-vlaanderen",), ("ronde-van-vlaanderen",), ("paris-roubaix",),
    ("amstel-gold-race",), ("la-fleche-wallonne",), ("liege-bastogne-liege",),
    ("eschborn-frankfurt",),
]


def main():
    scale, raw_scale = manager_scale()
    vm = vuelta_manager()
    cl = classics_2026(classics_scale)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "assumptions": [
            "Holdbonus gives kun til ryttere fra vinderens hold der startede løbet.",
            "Kaptajnens fordobling gælder også monument- og holdbonus.",
            "Alle 'optimale' hold er bagklogskab — de kender resultaterne på forhånd.",
        ],
        "rules": {"slots": SLOTS, "transfers": TRANSFERS,
                  "classics_scale_sample": {p: classics_scale(p) for p in
                                            (1, 2, 3, 5, 10, 11, 15, 16, 20, 30, 31, 50, 51, 56, 61, 66, 70, 75, 76, 120)},
                  "monuments": sorted(MONUMENTS), "monument_bonus": MONUMENT_BONUS,
                  "team_bonus": TEAM_BONUS},
        "vuelta_manager_2026": vm,
        "classics_2026_simulated": cl,
    }, ensure_ascii=False, indent=1))

    # ── Udskrift ─────────────────────────────────────────────────────────
    print("VUELTA MANAGER 2026 — Holdets egne point")
    print("  Etapepoint fordelt på placering:",
          " · ".join(f"{k}: {v*100:.0f}%" for k, v in vm["stage_points_by_band"].items()))
    for c, d in vm["categories"].items():
        print(f"  Kat {c}: {d['riders']:>3} ryttere, {d['slots']} pladser, snit for de bedste "
              f"{d['slots']}: {d['best_slot_avg']} pt (median-rytter {d['median_rider']})")

    print("\nKLASSIKERNE 2026 SIMULERET (PCS-resultater × klassikerspillets pointsystem × dine kategorier)")
    print(f"  {cl['matched_riders']} ryttere koblet; ukoblede i top-30: {len(cl['unmatched_top30'])}")
    print("  Placeringspoint fordelt:",
          " · ".join(f"{k}: {v*100:.0f}%" for k, v in cl["points_by_band"].items()))
    src = cl["points_by_source"]; st = sum(src.values()) or 1
    print("  Pointkilder:", " · ".join(f"{k} {v} ({v*100/st:.0f}%)" for k, v in src.items()))
    for c, d in cl["categories"].items():
        tops = ", ".join(f"{t['name']} {t['total']} ({t['starts']} løb)" for t in d["top"][:d["slots"]])
        print(f"  Kat {c}: snit for de bedste {d['slots']}: {d['best_slot_avg']} pt — {tops}")
    k4 = cl["kat4_thesis"]
    print(f"  Kat 4: {k4['share_of_kat4_points_from_riders_with_8plus_starts']*100:.0f}% af pointene "
          f"kom fra ryttere med 8+ starter; de 20 bedste havde median {k4['top20_kat4_median_starts']} starter")
    print("\n  Hvad de første skifter er værd, kategori for kategori (uden kaptajn):")
    print(f"    {'':6} {'fast hold':>9} {'+3 skifter':>11} {'+5 skifter':>11} {'nr. 6-10':>10} {'nr. 11-20':>10}")
    for c, v in cl["transfer_value_by_category"].items():
        print(f"    Kat {c}: {v['fast_bedste_hold']:>9} {v['de_første_3']:>+11} {v['de_første_5']:>+11}"
              f" {v['skifte_6_til_10_pr_stk']:>+7}/stk {v['skifte_11_til_20_pr_stk']:>+7}/stk")
    for c, v in cl["transfer_value_by_category"].items():
        print(f"    Kat {c}: flest-starter-holdet gav {v['flest_starter_hold']} mod {v['fast_bedste_hold']} for det bedste faste")
    races_short = [r["race"].split()[0][:6] for r in cl["races"]]
    for c, ex in cl["examples"].items():
        print(f"\n  Det faste Kat {c}-hold, løb for løb:")
        print("    " + " ".join(f"{x:>6}" for x in races_short))
        for r in ex["fixed"]:
            print("    " + " ".join(f"{p:>6}" for p in r["points"]) + f"   {r['name']} ({r['starts']} starter)")
        print("    " + " ".join(f"{p:>6}" for p in ex["best_available_per_race"]) + "   ← bedste i kategorien det løb")
    print("\n  Hvad var der at hente (bagklogskab, kaptajn medregnet):")
    for k, v in cl["scenarios"].items():
        extra = ""
        if k == "35_skifter_graadigt":
            extra = f"  vindue {v['window']} løb, skifter pr. runde {v['transfers_per_round']}"
        print(f"    {k:22} {v['points']:>6} pt   {v['transfers']:>3} skifter{extra}")
    print(f"\nSkrev {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
