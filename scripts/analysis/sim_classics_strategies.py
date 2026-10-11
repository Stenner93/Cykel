#!/usr/bin/env python3
"""
Klassiker Manager spillet som man faktisk spiller det — med begrænset viden.

Spørgsmålet her er ikke "hvad var optimalt med facit i hånden", men: når
man før hvert løb kun ved det man realistisk kunne vide, hvilken MÅDE at
spille på gav så flest point? Det er dét der kan bruges i 2027.

Viden før løb i (runde i+1):
  - Startlister: kendt for de næste H løb (default 2 — "sjældent ved jeg
    mere end et par løb frem hvem der kører hvad"). Længere ude bruges
    rytterens vane: hvor ofte han har kørt netop det løb de seneste år.
  - Niveau: rytterens point pr. start i samme blok (brosten / Ardenner) de
    tre foregående sæsoner og i sæsonen indtil nu, plus hans historik i
    netop dette løb, trukket mod kategoriens gennemsnit når der er lidt data.
  - Kaptajn: den på holdet med den højeste forventning blandt dem der starter.
Point tælles bagefter med de RIGTIGE resultater.

Strategier (alle med 2/3/3/4 og 35 skifter fra runde 2):
  fast          — holdet fra Omloop står hele sæsonen
  to_blokke     — brostenshold, så ét stort skifte før Amstel
  fri           — skifter løbende når forventet gevinst over de næste W løb
                  er over en tærskel (λ point pr. skifte)
  kerne         — som fri, men Kat 1 rører man ikke: de to bedste for sæsonen
                  beholdes også de dage de ikke kører
  reserve       — som fri, men R skifter gemmes til Amstel-runden

Til sidst nogle konkrete regnestykker: hvad det koster at have en Kat 1-
stjerne der ikke kører, hvor mange "døde" pladser man typisk har, hvor stort
Ardenner-skiftet bør være, og hvad hvert løb er værd.

Resultaterne er støjfyldte: tre sæsoner, og én styrtet favorit flytter
hundreder af point. Forskelle under ~1000 point mellem strategierne skal
ikke tillægges meget.

Brug:
    python scripts/analysis/sim_classics_strategies.py
    python scripts/analysis/sim_classics_strategies.py <Cykelmanager_2026.xlsx>
        — regner også dit eget udkast fra Planlægning-arket igennem som
          reference. Det printes kun; intet fra arket gemmes.
"""
from __future__ import annotations

import collections
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_manager_format import (  # noqa: E402
    MONUMENT_BONUS, MONUMENTS, RACE_ORDER, SLOTS, TEAM_BONUS, TRANSFERS,
    category_lookup, classics_scale,
)

ROOT = Path(__file__).resolve().parents[2]
CLASSICS = ROOT / "data/sources/classics"
OUT = ROOT / "data/analysis/classics_strategies.json"

ORDER = [r[0] for r in RACE_ORDER]
R = len(ORDER)
AMSTEL = ORDER.index("amstel-gold-race")
BLOCK = ["brosten" if i < AMSTEL else "ardenner" for i in range(R)]
SHORT = ["Omloop", "Strade", "Sanremo", "Brugge", "E3", "Gent-W", "Dwars", "Flandern",
         "Roubaix", "Amstel", "Flèche", "Liège", "Eschborn"]

SEASONS = [(2026, 2026), (2025, 2025), (2024, 2025)]   # (resultatår, kategoriår)
HIST_YEARS = 3
KNOWN_AHEAD = 2


# ── Data ─────────────────────────────────────────────────────────────────
def load_points():
    """-> pts[year][slug] = [13 point], start[year][slug] = [13 bool], name[slug]"""
    res = json.loads((CLASSICS / "results.json").read_text())["results"]
    winner = {(x["year"], x["race_slug"]): x["team"] for x in res if x["rank"] == 1}
    pts = collections.defaultdict(lambda: collections.defaultdict(lambda: [0] * R))
    start = collections.defaultdict(lambda: collections.defaultdict(lambda: [False] * R))
    name = {}
    import re
    for x in res:
        if x["race_slug"] not in ORDER:
            continue
        i = ORDER.index(x["race_slug"])
        y, s = x["year"], x["rider_slug"]
        status = (x.get("status") or "")
        rank = x["rank"]
        rel = re.search(r"relegated from \d+ to (\d+)", status)
        if rel:
            rank, status = int(rel.group(1)), ""
        if status.upper() == "DNS":
            continue
        name[s] = x["rider_name"]
        start[y][s][i] = True
        p = classics_scale(rank)
        if x["race_slug"] in MONUMENTS and rank and rank <= 10:
            p += MONUMENT_BONUS[rank - 1]
        if x["team"] and x["team"] == winner.get((y, x["race_slug"])):
            p += TEAM_BONUS          # også udgåede — de startede
        pts[y][s][i] = p
    return pts, start, name


def season_pool(year, cat_year, pts, start, name):
    """Ryttere med kategori i spillet, som har kørt mindst ét af løbene i
    sæsonen eller de tre før. -> {slug: kat}"""
    cats_raw = json.loads((CLASSICS / f"categories_{cat_year}.json").read_text())["categories"]
    lookup = category_lookup(cats_raw)
    pool = {}
    for y in range(year - HIST_YEARS, year + 1):
        for s in start.get(y, {}):
            if s in pool:
                continue
            hit = lookup(name.get(s))
            if hit:
                pool[s] = hit[1]
    return pool


# ── Forventninger med den viden man havde ────────────────────────────────
def expectations(year, pool, pts, start, known_ahead=KNOWN_AHEAD):
    """-> E[i][slug][j]: forventede point i løb j, set fra før løb i.
    Og S[i][slug][j]: sandsynlighed for start i løb j, set fra før løb i."""
    hist = [y for y in range(year - HIST_YEARS, year)]
    w_year = {y: 0.6 ** (year - 1 - y) for y in hist}
    W_CUR, K_PRIOR, K_RACE = 1.5, 2.0, 2.0

    # Kategoriens snit pr. start i hver blok (fra historikken) som prior
    prior = {}
    for c in SLOTS:
        for b in ("brosten", "ardenner"):
            tot = n = 0
            for y in hist:
                for s, cc in pool.items():
                    if cc != c:
                        continue
                    for j in range(R):
                        if BLOCK[j] == b and start[y][s][j]:
                            tot += pts[y][s][j]
                            n += 1
            prior[c, b] = tot / n if n else 0

    # Vane: andel af aktive år hvor rytteren kørte løb j
    habit = {}
    for s in pool:
        act = [y for y in hist if any(start[y][s])]
        for j in range(R):
            k = sum(start[y][s][j] for y in act)
            habit[s, j] = (k + 0.25) / (len(act) + 1)

    E, S = [], []
    for i in range(R):
        Ei, Si = {}, {}
        for s, c in pool.items():
            blk = {}
            for b in ("brosten", "ardenner"):
                tot, wt = 0.0, 0.0
                for y in hist:
                    for j in range(R):
                        if BLOCK[j] == b and start[y][s][j]:
                            tot += w_year[y] * pts[y][s][j]
                            wt += w_year[y]
                for j in range(i):                      # sæsonen indtil nu
                    if BLOCK[j] == b and start[year][s][j]:
                        tot += W_CUR * pts[year][s][j]
                        wt += W_CUR
                blk[b] = (tot + K_PRIOR * prior[c, b]) / (wt + K_PRIOR)
            e_row, p_row = [0.0] * R, [0.0] * R
            for j in range(i, R):
                rt, rw = 0.0, 0.0
                for y in hist:
                    if start[y][s][j]:
                        rt += w_year[y] * pts[y][s][j]
                        rw += w_year[y]
                e_start = (K_RACE * blk[BLOCK[j]] + rt) / (K_RACE + rw)
                p = (1.0 if start[year][s][j] else 0.0) if j < i + known_ahead else habit[s, j]
                e_row[j], p_row[j] = e_start, p
            Ei[s], Si[s] = e_row, p_row
        E.append(Ei)
        S.append(Si)
    return E, S


# ── Strategier ───────────────────────────────────────────────────────────
class Season:
    def __init__(self, year, cat_year, pts, start, name, known_ahead=KNOWN_AHEAD):
        self.year = year
        self.pool = season_pool(year, cat_year, pts, start, name)
        self.pts = {s: pts[year][s] for s in self.pool}
        self.start = {s: start[year][s] for s in self.pool}
        self.name = name
        self.E, self.S = expectations(year, self.pool, pts, start, known_ahead)
        self.by_cat = collections.defaultdict(list)
        for s, c in self.pool.items():
            self.by_cat[c].append(s)

    def ev(self, i, s, j):
        return self.E[i][s][j] * self.S[i][s][j]

    def team_value(self, i, team, W, d=0.85):
        """Forventet værdi af holdet over løb i..i+W-1, kaptajn medregnet."""
        v = 0.0
        for j in range(i, min(R, i + W)):
            evs = [self.ev(i, s, j) for s in team]
            v += (d ** (j - i)) * (sum(evs) + max(evs))
        return v

    def initial(self, W):
        team = []
        for c, n in SLOTS.items():
            team += sorted(self.by_cat[c], key=lambda s: -sum(self.ev(0, s, j) for j in range(min(R, W))))[:n]
        return team

    def improve(self, i, team, W, lam, budget, locked=frozenset()):
        """Grådige 1-for-1 skifter i samme kategori, så længe gevinsten > λ."""
        team = list(team)
        made = 0
        while made < budget:
            base = self.team_value(i, team, W)
            best = (lam, None, None)
            for c, n in SLOTS.items():
                cands = sorted((s for s in self.by_cat[c] if s not in team),
                               key=lambda s: -sum(self.ev(i, s, j) for j in range(i, min(R, i + W))))[:25]
                for o in [s for s in team if self.pool[s] == c and s not in locked]:
                    for nw in cands:
                        t2 = [nw if x == o else x for x in team]
                        g = self.team_value(i, t2, W) - base
                        if g > best[0]:
                            best = (g, o, nw)
            if best[1] is None:
                break
            team = [best[2] if x == best[1] else x for x in team]
            made += 1
        return team, made

    def play(self, strategy, W=3, lam=100, reserve=0, budget=TRANSFERS):
        plan, used = [], 0
        team = self.initial(W if strategy not in ("fast", "to_blokke") else AMSTEL)
        locked = frozenset()
        if strategy in ("kerne", "kerne1"):
            # Kat 1-kernen: de(n) med højest forventning for hele sæsonen.
            # "kerne" låser begge Kat 1-pladser, "kerne1" kun den største stjerne.
            k1 = self.kat1_core()[:2 if strategy == "kerne" else 1]
            rest1 = [s for s in self.initial(W) if self.pool[s] == 1 and s not in k1]
            team = k1 + rest1[:2 - len(k1)] + [s for s in self.initial(W) if self.pool[s] != 1]
            locked = frozenset(k1)
        for i in range(R):
            if i > 0 and strategy != "fast":
                left = budget - used
                if strategy == "to_blokke":
                    if i == AMSTEL:
                        team, m = self.improve(i, team, R - i, 0, left)
                        used += m
                else:
                    if strategy == "reserve" and i < AMSTEL:
                        left = max(0, left - reserve)
                    w = (R - i) if (strategy == "reserve" and i == AMSTEL) else W
                    team, m = self.improve(i, team, w, lam, left, locked=locked)
                    used += m
            plan.append(list(team))
        return self.score(plan), used, plan

    def kat1_core(self):
        return sorted(self.by_cat[1], key=lambda s: -sum(self.ev(0, s, j) for j in range(R)))

    def captain(self, i, team):
        starters = [s for s in team if self.start[s][i]] or team
        return max(starters, key=lambda s: self.E[i][s][i])

    def score(self, plan):
        tot = 0
        for i, team in enumerate(plan):
            tot += sum(self.pts[s][i] for s in team) + self.pts[self.captain(i, team)][i]
        return tot

    def hindsight_captain(self, plan):
        return sum(sum(self.pts[s][i] for s in t) + max(self.pts[s][i] for s in t)
                   for i, t in enumerate(plan))


# ── Analyse ──────────────────────────────────────────────────────────────
GRID_W = (1, 2, 3, 4, 6)
GRID_L = (25, 50, 75, 100, 150, 200, 300)


def nm(se, s):
    from eval_manager_format import pcs_key
    k = pcs_key(se.name.get(s, s))
    return se.name.get(s, s).title() if not k else f"{k[0].upper()}. {k[1].title()}"


def analyse(se):
    out = {"year": se.year, "pool": len(se.pool)}
    # 1) Strategierne over et gitter af (W, λ) — så vi kan se hvor robuste de er
    grid = {}
    for strat in ("fri", "kerne", "kerne1"):
        for W in GRID_W:
            for lam in GRID_L:
                p, u, _ = se.play(strat, W, lam)
                grid[f"{strat} W{W} λ{lam}"] = (p, u)
    for R_ in (4, 6, 8, 10):
        for W in (2, 3, 4):
            for lam in (50, 100, 150):
                p, u, _ = se.play("reserve", W, lam, reserve=R_)
                grid[f"reserve{R_} W{W} λ{lam}"] = (p, u)
    out["grid"] = grid
    out["fast"] = se.play("fast")[0]
    out["to_blokke"] = se.play("to_blokke")[:2]
    return out


def details(se, W, lam, reserve):
    """Detaljer for én valgt måde at spille på."""
    d = {}
    for strat in ("fri", "kerne1", "kerne", "reserve"):
        p, u, plan = se.play(strat, W, lam, reserve=reserve)
        per_round = [0] + [len(set(plan[i]) - set(plan[i - 1])) for i in range(1, R)]
        dead = [sum(1 for s in t if not se.start[s][i]) for i, t in enumerate(plan)]
        k1_idle = sum(1 for i, t in enumerate(plan) for s in t
                      if se.pool[s] == 1 and not se.start[s][i])
        d[strat] = {"points": p, "transfers": u, "per_round": per_round,
                    "dead_slots_per_race": dead,
                    "kat1_idle_slots": k1_idle,
                    "hindsight_captain": se.hindsight_captain(plan),
                    "points_per_race": [sum(se.pts[s][i] for s in t) + se.pts[se.captain(i, t)][i]
                                        for i, t in enumerate(plan)],
                    "kat1_per_race": [[nm(se, s) for s in t if se.pool[s] == 1] for t in plan],
                    "captain_per_race": [nm(se, se.captain(i, t)) for i, t in enumerate(plan)],
                    "start_team": [f"{nm(se, s)} (K{se.pool[s]})" for s in plan[0]]}
    # Værdien af et skifte på marginen: 2 skifter mere eller mindre
    p33 = se.play("fri", W, lam, budget=33)[0]
    p37 = se.play("fri", W, lam, budget=37)[0]
    d["marginal_per_transfer"] = {"33": p33, "35": d["fri"]["points"], "37": p37,
                                  "pr_skifte": round((p37 - p33) / 4)}
    return d


def kat1_idle_cost(se):
    """Hvad var der at hente de dage en Kat 1-stjerne ikke kørte?

    For de to Kat 1-ryttere med højest forventning for hele sæsonen: i hvert
    løb hvor én af dem ikke startede, hvad scorede den bedste Kat 1-rytter
    der startede (og ikke allerede var på holdet)? Det kræver 2 skifter
    (ud og ind igen), så det skal sammenlignes med 2 × marginalværdien.
    """
    k1 = se.kat1_core()[:2]
    rows = []
    for i in range(R):
        idle = [s for s in k1 if not se.start[s][i]]
        if not idle:
            continue
        alts = sorted((s for s in se.by_cat[1] if s not in k1 and se.start[s][i]),
                      key=lambda s: -se.E[i][s][i])
        exp_best = alts[0] if alts else None
        act_best = max(alts, key=lambda s: se.pts[s][i]) if alts else None
        rows.append({"race": SHORT[i], "idle": [nm(se, s) for s in idle],
                     "forventet_bedste": nm(se, exp_best) if exp_best else None,
                     "forventet_bedste_fik": se.pts[exp_best][i] if exp_best else 0,
                     "forventet_point": round(se.E[i][exp_best][i]) if exp_best else 0,
                     "faktisk_bedste": f"{nm(se, act_best)} {se.pts[act_best][i]}" if act_best else None})
    return {"kerne": [nm(se, s) for s in k1],
            "kerne_starter": [sum(se.start[s]) for s in k1],
            "kerne_point": [sum(se.pts[s]) for s in k1],
            "idle_races": rows}


def ardennes_swap(se):
    """Hvor stort bør skiftet før Amstel være? Fra et brostenshold valgt
    ex ante: for k = 0..12 skifter ved Amstel (de k bedste, set fra før
    Amstel), hvor mange point gav Ardennerne?"""
    team = se.initial(AMSTEL)
    out = {}
    for k in range(0, 13):
        t, m = se.improve(AMSTEL, team, R - AMSTEL, 0, k)
        out[k] = sum(sum(se.pts[s][j] for s in t) + se.pts[se.captain(j, t)][j]
                     for j in range(AMSTEL, R))
    survivors = sum(1 for s in team if any(se.start[s][j] for j in range(AMSTEL, R)))
    return {"points_by_swaps": out, "brosten_riders_starting_ardennes": survivors}


def race_value(se):
    """Hvad er hvert løb værd: point til de bedste 2/3/3/4 der startede
    (bagklogskab) + kaptajn. Viser hvor de store point ligger."""
    vals = []
    for i in range(R):
        tot = 0
        caps = 0
        for c, n in SLOTS.items():
            best = sorted((se.pts[s][i] for s in se.by_cat[c]), reverse=True)[:n]
            tot += sum(best)
            caps = max(caps, best[0] if best else 0)
        vals.append({"race": SHORT[i], "max_points": tot + caps,
                     "monument": ORDER[i] in MONUMENTS})
    return vals


def score_draft(se, xlsx):
    """Dit eget 2026-udkast (Planlægning-arket) regnet igennem som reference.
    Det er et UDKAST, ikke det hold du faktisk kørte med. Læses kun lokalt;
    intet fra arket gemmes."""
    from xlsx_stdlib import load
    rows = dict(load(xlsx)).get("Planlægning")
    if not rows:
        return None
    cats_raw = json.loads((CLASSICS / "categories_2026.json").read_text())["categories"]
    lookup = category_lookup(cats_raw)
    by_note = {}
    for s in se.pool:
        hit = lookup(se.name.get(s))
        if hit:
            by_note[hit[0]] = s
    from eval_manager_format import notes_key, norm
    by_key = {notes_key(k): v for k, v in by_note.items()}
    plan, missing = [], set()
    for i in range(R):
        team = []
        for r in rows[1:13]:
            cell = str(r[i + 1] or "").strip() if len(r) > i + 1 else ""
            if not cell:
                continue
            s = by_note.get(cell) or by_key.get(notes_key(cell))
            if not s:      # stavefejl i udkastet, fx "Vaquelin" — match på initial + efternavnets start
                k = notes_key(cell)
                hits = [v for kk, v in by_key.items() if k and kk and kk[0] == k[0] and kk[1][:4] == k[1][:4]]
                s = hits[0] if len(hits) == 1 else None
            if s:
                team.append(s)
            else:
                missing.add(cell)
        plan.append(team)
    used = sum(len(set(plan[i]) - set(plan[i - 1])) for i in range(1, R))
    return {"points": se.score(plan), "transfers": used, "missing": sorted(missing),
            "dead_slots_per_race": [sum(1 for s in t if not se.start[s][i]) for i, t in enumerate(plan)],
            "points_per_race": [sum(se.pts[s][i] for s in t) + se.pts[se.captain(i, t)][i]
                                for i, t in enumerate(plan)]}


def main():
    draft_xlsx = sys.argv[1] if len(sys.argv) > 1 else None
    pts, start, name = load_points()
    seasons = {f"{y}" + ("" if cy == y else f" (kat. {cy})"): Season(y, cy, pts, start, name)
               for y, cy in SEASONS}
    res = {k: analyse(se) for k, se in seasons.items()}

    # Robust valg: de parametre der i SNIT over sæsonerne gav flest point —
    # ikke det bedste i hver sæson for sig (det ville være at snyde).
    keys = list(next(iter(res.values()))["grid"])
    avg = {k: sum(r["grid"][k][0] for r in res.values()) / len(res) for k in keys}
    best_by_strat = {}
    for k, v in avg.items():
        st = k.split()[0]
        st = "reserve" if st.startswith("reserve") else st
        if st not in best_by_strat or v > avg[best_by_strat[st]]:
            best_by_strat[st] = k
    pick = best_by_strat["fri"]
    W = int(pick.split()[1][1:])
    lam = int(pick.split()[2][1:])
    rsv_key = best_by_strat["reserve"]
    rsv = int(rsv_key.split()[0][7:])

    report = {"known_ahead": KNOWN_AHEAD, "races": SHORT, "seasons": {}}
    print(f"Viden: startlister {KNOWN_AHEAD} løb frem; niveau fra {HIST_YEARS} foregående sæsoner + sæsonen indtil nu\n")
    print("STRATEGIER (point med de rigtige resultater; samme parametre alle sæsoner)")
    head = f"  {'':34}" + "".join(f"{k:>16}" for k in res)
    print(head)
    rows = [("fast hold (ingen skifter)", lambda r: (r["fast"], 0)),
            ("to blokke (stort skifte før Amstel)", lambda r: tuple(r["to_blokke"]))]
    for st in ("fri", "kerne1", "kerne", "reserve"):
        k = best_by_strat[st]
        rows.append((f"{st:7} {k.split(' ', 1)[1] if st != 'reserve' else k}",
                     lambda r, k=k: tuple(r["grid"][k])))
    for label, f in rows:
        print(f"  {label:34}" + "".join(f"{f(r)[0]:>10} ({f(r)[1]:>2})" for r in res.values()))
    print("  (tal i parentes = brugte skifter)")

    print("\n  Hvor følsomt er 'fri' for vindue W og tærskel λ? (snit over sæsonerne)")
    print("    λ →  " + "".join(f"{l:>7}" for l in GRID_L))
    for Wx in GRID_W:
        print(f"    W={Wx}  " + "".join(f"{avg[f'fri W{Wx} λ{l}']:>7.0f}" for l in GRID_L))

    for key, se in seasons.items():
        det = details(se, W, lam, rsv)
        k1 = kat1_idle_cost(se)
        ard = ardennes_swap(se)
        rv = race_value(se)
        report["seasons"][key] = {"grid": res[key]["grid"], "fast": res[key]["fast"],
                                  "to_blokke": res[key]["to_blokke"], "details": det,
                                  "kat1_idle": k1, "ardennes_swap": ard, "race_value": rv}
        f = det["fri"]
        print(f"\n── {key}  (fri W{W} λ{lam}) ──")
        print(f"  Starthold: {', '.join(f['start_team'])}")
        print("  " + " ".join(f"{x:>8}" for x in SHORT))
        print("  " + " ".join(f"{x:>8}" for x in f["per_round"]) + "   skifter")
        print("  " + " ".join(f"{x:>8}" for x in f["dead_slots_per_race"]) + "   ryttere på holdet der ikke startede")
        print("  " + " ".join(f"{x:>8}" for x in f["points_per_race"]) + "   point")
        print("  " + " ".join(f"{x.split()[-1][:8]:>8}" for x in f["captain_per_race"]) + "   kaptajn")
        print(f"  Kaptajn: {f['points']} med forventet kaptajn, {f['hindsight_captain']} hvis den altid var ramt")
        kd, k1d = det["kerne"], det["kerne1"]
        print(f"  Tomme Kat1-pladser (Kat1-rytter på holdet der ikke startede): fri {f['kat1_idle_slots']}, "
              f"kerne1 {k1d['kat1_idle_slots']} ({k1d['points']} pt), kerne {kd['kat1_idle_slots']} ({kd['points']} pt)")
        print("  Fri: Kat1 pr. løb: " + " | ".join("+".join(x.split()[-1][:7] for x in k) for k in f["kat1_per_race"]))
        m = det["marginal_per_transfer"]
        print(f"  Ét skifte mere/mindre på marginen: ≈{m['pr_skifte']} pt (33: {m['33']}, 35: {m['35']}, 37: {m['37']})")
        print(f"  Kat1-kernen ex ante: {', '.join(k1['kerne'])} — starter {k1['kerne_starter']}, point {k1['kerne_point']}")
        for r_ in k1["idle_races"]:
            print(f"    {r_['race']:9} uden {', '.join(r_['idle']):26} bedste alternativ ex ante: "
                  f"{r_['forventet_bedste']} ({r_['forventet_point']} forventet, fik {r_['forventet_bedste_fik']}); "
                  f"faktisk bedste: {r_['faktisk_bedste']}")
        pb = ard["points_by_swaps"]
        print(f"  Ardenner-skiftet: {ard['brosten_riders_starting_ardennes']} af 12 brostensryttere startede i Ardennerne. "
              f"Point i Ardennerne ved k skifter: " + ", ".join(f"{k}:{v}" for k, v in pb.items() if k in (0, 2, 4, 6, 8, 10, 12)))
        print("  Løbenes værdi (bedste mulige hold + kaptajn): " +
              ", ".join(f"{v['race']} {v['max_points']}{'*' if v['monument'] else ''}" for v in rv))

    if draft_xlsx:
        d = score_draft(seasons["2026"], draft_xlsx)
        if d:
            print(f"\nDIT 2026-UDKAST (kun som reference — et udkast, ikke dit faktiske hold): "
                  f"{d['points']} pt, {d['transfers']} skifter; ukendte navne: {d['missing']}")
            print("  " + " ".join(f"{x:>8}" for x in d["points_per_race"]) + "   point")
            print("  " + " ".join(f"{x:>8}" for x in d["dead_slots_per_race"]) + "   ikke-startende")

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\nSkrev {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
