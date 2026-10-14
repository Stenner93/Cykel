#!/usr/bin/env python3
"""
Bygger datafilerne til Klassiker Manager-siderne (web/klassiker*.html).

Alt styres af data/klassiker/config.json. Til en ny sæson rettes config'en,
inputtet opdateres (se data/klassiker/README.md), og så køres dette script.

Input:
  data/klassiker/config.json                       sæson, løb, blokke, grupper, regler
  data/sources/classics/results.json              PCS-resultater (scrape_classics_results.py)
  data/sources/classics/categories_<år>.json      kategorier (import_classics_notes.py / Holdet)
  data/sources/classics/startlists_<år>.json      forventede startlister (import_klassiker_sheets.py)
  data/sources/classics/plan_<år>.json            holdudkast (import_klassiker_sheets.py plan)
  data/klassiker/optakt_<sæson>.json              optakter (valgfri — tom skal indtil da)
  data/klassiker/profile_trace_<år>.json         profilform + mørke billeder (trace_classics_profiles.py)
  data/klassiker/route_marks_<år>.json           stigninger/pavéer pr. løb (aflæst i hånden)
  web/data/klassiker/profiles/<år>/manifest.json  profilbilleder (fetch_classics_profiles.py)

Output:
  web/data/klassiker.json         sæson, løb, regler, plan, startlister, optakt, duel
  web/data/klassiker_riders.json  alle relevante ryttere med resultat pr. løb og år
  (Uden årstal i navnet, så siderne ikke skal rettes fra sæson til sæson.)

Point regnes i browseren ud fra placeringerne og reglerne i første fil, så
pointskalaen kun står ét sted (her, i "rules.scale").
"""
from __future__ import annotations

import collections
import datetime as dt
import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/analysis"))
from eval_manager_format import category_lookup, classics_scale, notes_key, norm, pcs_key  # noqa: E402

CONFIG = ROOT / "data/klassiker/config.json"
SRC = ROOT / "data/sources/classics"
WEB = ROOT / "web/data"

# Resultatkoder i rytterfilen: placering som tal, ellers
DNF, DNS = -1, -2          # DNF/OTL/DSQ = startede men kom ikke i mål; DNS = startede ikke


def first_existing(*paths):
    for p in paths:
        if p and p.exists():
            return p
    return None


def short_name(pcs_name):
    k = pcs_key(pcs_name)
    if not k:
        return pcs_name
    toks = (pcs_name or "").split()
    sur = " ".join(t for t in toks if t.isupper() or not any(c.isalpha() for c in t))
    first = next(t for t in toks if t not in sur.split())
    return f"{first[0]}. {sur.title()}"


def main():
    cfg = json.loads(CONFIG.read_text())
    season, data_year = cfg["season"], cfg["data_year"]
    years = cfg["history_years"]
    races = cfg["races"]
    slugs = [r["slug"] for r in races]
    R = len(races)

    res = json.loads((SRC / "results.json").read_text())
    meta = {(r["year"], r["race_slug"]): r for r in res["races"]}

    # ── Resultater pr. rytter pr. år ────────────────────────────────────
    codes = collections.defaultdict(dict)        # slug -> {år: [kode × R]}
    tb = collections.defaultdict(list)           # slug -> [[år, løbsindeks], …]
    pcs_name, team_of, last_year = {}, {}, {}
    winner_team = {(x["year"], x["race_slug"]): x["team"] for x in res["results"] if x["rank"] == 1}
    podium = collections.defaultdict(list)
    for x in res["results"]:
        if x["year"] not in years or x["race_slug"] not in slugs:
            continue
        i = slugs.index(x["race_slug"])
        s, y = x["rider_slug"], x["year"]
        status, rank = (x.get("status") or ""), x["rank"]
        rel = re.search(r"relegated from \d+ to (\d+)", status)
        if rel:
            rank, status = int(rel.group(1)), ""
        code = rank if rank else (DNS if status.upper() == "DNS" else DNF)
        codes[s].setdefault(y, [None] * R)[i] = code
        if code != DNS and x["team"] and x["team"] == winner_team.get((y, x["race_slug"])):
            tb[s].append([y, i])
        if y >= last_year.get(s, 0):
            last_year[s], team_of[s], pcs_name[s] = y, x["team"], x["rider_name"]
        if y == data_year and rank and rank <= 3:
            podium[x["race_slug"]].append((rank, x["rider_name"], x["team"]))

    # ── Kategorier → rytter-id ──────────────────────────────────────────
    cat_file = SRC / f"categories_{cfg['categories_year']}.json"
    cats = json.loads(cat_file.read_text())["categories"] if cat_file.exists() else {}
    lookup = category_lookup(cats)
    note_to_slug = {}
    for s, nm in pcs_name.items():
        hit = lookup(nm)
        if hit and (hit[0] not in note_to_slug or last_year[s] > last_year[note_to_slug[hit[0]]]):
            note_to_slug[hit[0]] = s

    recent = set(years[-3:])
    riders = {}
    for note, c in cats.items():
        s = note_to_slug.get(note)
        rid = s or f"n:{note}"
        riders[rid] = {"id": rid, "name": note, "cat": c}
    for s in codes:
        if s in riders:
            continue
        if not (set(codes[s]) & recent):
            continue
        riders[s] = {"id": s, "name": short_name(pcs_name[s]), "cat": None}
    for rid, r in riders.items():
        if rid in codes:
            r["team"] = team_of.get(rid)
            r["res"] = {str(y): v for y, v in sorted(codes[rid].items())}
            if tb.get(rid):
                r["tb"] = tb[rid]

    # Navn → id til startlister og plan (noternes "T. Pogačar"-form)
    by_name = {r["name"]: rid for rid, r in riders.items()}
    by_key = {}
    for nm_, rid in by_name.items():
        k = notes_key(nm_)
        if k:
            by_key.setdefault(k, rid)
    keys = {norm(n): rid for n, rid in by_name.items()}
    unknown = set()

    def resolve(name):
        if name in by_name:
            return by_name[name]
        k = notes_key(name)
        if k and k in by_key:
            return by_key[k]
        close = difflib.get_close_matches(norm(name), list(keys), n=1, cutoff=0.85)
        if close:
            return keys[close[0]]
        unknown.add(name)
        return None

    # ── Startlister ─────────────────────────────────────────────────────
    sl_file = first_existing(SRC / f"startlists_{season}.json", SRC / f"startlists_{data_year}.json")
    startlists = None
    if sl_file:
        sl = json.loads(sl_file.read_text())
        flags = {}
        for nm_, rec in sl["riders"].items():
            if norm(nm_) in ("", "unknown", "player"):
                continue
            rid = resolve(nm_)
            if not rid:
                rid = f"n:{nm_}"
                riders.setdefault(rid, {"id": rid, "name": nm_, "cat": rec.get("cat")})
            flags[rid] = [rec["flags"].get(s) for s in slugs]
        startlists = {"year": sl["year"], "updated": sl.get("updated"), "flags": flags}

    # ── Holdudkast → 12 pladser fordelt på kategorierne ─────────────────
    slot_cats = [int(c) for c, n in cfg["rules"]["slots"].items() for _ in range(n)]
    plan_file = first_existing(SRC / f"plan_{season}.json", SRC / f"plan_{data_year}.json")
    plan = None
    if plan_file:
        pl = json.loads(plan_file.read_text())
        rounds, prev, warn = [], None, []
        for i, s in enumerate(slugs):
            ids = [resolve(n) for n in pl["rounds"].get(s, [])]
            ids = [x for x in ids if x]
            row = [None] * len(slot_cats)
            if prev:                                   # bliver på sin plads
                for k, x in enumerate(prev):
                    if x in ids:
                        row[k] = x
            for x in ids:
                if x in row:
                    continue
                c = riders[x]["cat"]
                free = [k for k, sc in enumerate(slot_cats) if row[k] is None and sc == c]
                if not free:
                    warn.append(f"{races[i]['short']}: {riders[x]['name']} (Kat {c}) — ingen ledig plads")
                    continue
                row[free[0]] = x
            rounds.append(row)
            prev = row
        plan = {"label": pl.get("label"), "source": pl.get("source"), "year": pl.get("year"),
                "rounds": rounds, "captains": [None] * R, "warnings": warn}

    # ── Løb ─────────────────────────────────────────────────────────────
    prof_dir = WEB / "klassiker/profiles" / str(data_year)
    prof = json.loads((prof_dir / "manifest.json").read_text())["files"] if (prof_dir / "manifest.json").exists() else {}
    # Piktogrammet: profilens form (aflæst af billedet) + stigninger/pavéer
    # (aflæst i hånden). Mangler en af dem, viser siden kun billedet.
    trace_f = ROOT / f"data/klassiker/profile_trace_{data_year}.json"
    marks_f = ROOT / f"data/klassiker/route_marks_{data_year}.json"
    traces = json.loads(trace_f.read_text())["races"] if trace_f.exists() else {}
    marks = json.loads(marks_f.read_text())["races"] if marks_f.exists() else {}
    race_out = []
    for r in races:
        m = meta.get((data_year, r["slug"]), {})
        won = m.get("won_how")
        if won and won.lower().startswith("avg"):
            won = None
        race_out.append({
            **{k: r[k] for k in ("slug", "name", "short", "group")},
            "monument": bool(r.get("monument")),
            "date": m.get("date"), "distance_km": m.get("distance_km"),
            "profile_score": m.get("profile_score"), "vmeters": m.get("vmeters"),
            "startlist_quality": m.get("startlist_quality"), "won_how": won,
            "starters": m.get("starters"), "finishers": m.get("finishers"),
            "profile_img": f"data/klassiker/profiles/{data_year}/{prof[r['slug']]}" if r["slug"] in prof else None,
            "profile_dark": (f"data/klassiker/profiles/{data_year}/{r['slug']}_dark.png"
                             if (prof_dir / f"{r['slug']}_dark.png").exists() else None),
            "route": ({"shape": traces[r["slug"]]["shape"], "marks": marks.get(r["slug"], [])}
                      if r["slug"] in traces else None),
            "podium": [{"rank": a, "name": short_name(b), "team": c} for a, b, c in sorted(podium[r["slug"]])],
        })

    optakt_file = ROOT / f"data/klassiker/optakt_{season}.json"
    optakt = json.loads(optakt_file.read_text()) if optakt_file.exists() else {"races": {}}

    rules = dict(cfg["rules"])
    rules["scale"] = [classics_scale(p) for p in range(1, 76)]       # 1. … 75. plads
    rules["monuments"] = [r["slug"] for r in races if r.get("monument")]

    main_out = {
        "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "game": cfg["game"], "season": season, "data_year": data_year,
        "categories_year": cfg["categories_year"], "history_years": years,
        "holdet": cfg["holdet"],
        "rules": rules, "races": race_out, "blocks": cfg["blocks"], "groups": cfg["groups"],
        "plan": plan, "startlists": startlists,
        "optakt": {"sources": cfg.get("optakt_sources", []), **optakt},
        "duel": cfg["duel"],
    }
    WEB.mkdir(parents=True, exist_ok=True)
    (WEB / "klassiker.json").write_text(json.dumps(main_out, ensure_ascii=False, separators=(",", ":")))
    riders_out = {"years": years, "races": slugs,
                  "codes": {"DNF": DNF, "DNS": DNS},
                  "riders": sorted(riders.values(), key=lambda r: (r["cat"] or 9, r["name"]))}
    (WEB / "klassiker_riders.json").write_text(
        json.dumps(riders_out, ensure_ascii=False, separators=(",", ":")))

    n_cat = sum(1 for r in riders.values() if r["cat"])
    n_hist = sum(1 for r in riders.values() if r.get("res"))
    print(f"Skrev web/data/klassiker.json og klassiker_riders.json ({cfg['game']})")
    print(f"  {len(riders)} ryttere ({n_cat} med kategori, {n_hist} med historik), "
          f"{len(prof)} profiler, startlister: {'ja' if startlists else 'nej'}, plan: {'ja' if plan else 'nej'}")
    if plan and plan["warnings"]:
        print("  Planadvarsler: " + "; ".join(plan["warnings"]))
    if unknown:
        print(f"  Ukendte navne ({len(unknown)}): " + ", ".join(sorted(unknown)[:20]))


if __name__ == "__main__":
    main()
