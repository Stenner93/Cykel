#!/usr/bin/env python3
"""
Kategorierne direkte fra Holdet, når næste Klassiker Manager åbner.

Holdets spillerliste (games/<id>/players) har hver rytters position, og i
Manager-spillene hedder positionerne "Kategori 1" … "Kategori 4". Det giver
samme fil som importen fra dine noter, så resten af kæden er uændret:
    data/sources/classics/categories_<år>.json

Brug — enten fra en allerede hentet spillerliste (snapshot_holdet_teams.py):
    python scripts/analysis/import_holdet_categories.py --players data/sources/<løb>/holdet/reference/players.json --year 2027
eller direkte fra Holdet med spillets id (står i config'en, når det kendes):
    python scripts/analysis/import_holdet_categories.py --game-id 7xx --year 2027
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = "https://www.holdet.dk/api/season"


def fetch(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--players", help="sti til en hentet players.json")
    src.add_argument("--game-id", type=int, help="Holdets spil-id")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--out", help="default data/sources/classics/categories_<år>.json")
    args = ap.parse_args()

    d = json.loads(Path(args.players).read_text()) if args.players else fetch(f"{API}/games/{args.game_id}/players")
    emb = d["_embedded"]
    cat_of = {}
    for pid, pos in emb["positions"].items():
        m = re.search(r"(\d)", pos.get("title") or pos.get("name") or "")
        if m:
            cat_of[int(pid)] = int(m.group(1))
    persons, teams = emb["persons"], emb.get("teams", {})

    def short(p):
        first, last = (p.get("firstName") or "").strip(), (p.get("lastName") or "").strip()
        return f"{first[:1]}. {last}" if first else last

    names = collections.Counter(short(persons[str(it["personId"])]) for it in d["items"])
    cats, team_of, ids = {}, {}, {}
    for it in d["items"]:
        c = cat_of.get(it["positionId"])
        if not c:
            continue
        p = persons[str(it["personId"])]
        n = short(p)
        if names[n] > 1:                          # to "J. Andersen" — skriv fornavnet ud
            n = f"{p.get('firstName', '').strip()} {p.get('lastName', '').strip()}"
        cats[n] = c
        ids[n] = it["id"]
        t = teams.get(str(it.get("teamId")), {})
        if t.get("abbreviation"):
            team_of[n] = t["abbreviation"]

    out = Path(args.out) if args.out else ROOT / f"data/sources/classics/categories_{args.year}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "source": f"Holdet.dk spillerliste ({'fil' if args.players else 'spil ' + str(args.game_id)}) — navn, hold og kategori.",
        "categories": dict(sorted(cats.items())),
        "teams": dict(sorted(team_of.items())),
        "holdet_player_ids": dict(sorted(ids.items())),
    }, ensure_ascii=False, indent=1))
    c = collections.Counter(cats.values())
    print(f"Skrev {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out} — {len(cats)} ryttere "
          f"(Kat1 {c[1]}, Kat2 {c[2]}, Kat3 {c[3]}, Kat4 {c[4]})")


if __name__ == "__main__":
    main()
