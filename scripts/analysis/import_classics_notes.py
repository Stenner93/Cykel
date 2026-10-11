#!/usr/bin/env python3
"""
Importerer kategorierne fra dine Klassiker Manager-noter.

Spillets kategorier (Kat 1-4) er ikke til at hente fra Holdet længere — API'et
lukker efter sæsonen — men de står i dine regneark:
  - 2026: Master-arket (Player, Kat.)
  - 2025: Oversigten-arket (Player, forkortet navn, Team, Kat.)

Kun rytternavn, hold og kategori tages med; resten af regnearket (planer,
vurderinger, noter) bliver hos dig.

Brug:
    python scripts/analysis/import_classics_notes.py <sti/til/Cykelmanager_2026.xlsx>
    python scripts/analysis/import_classics_notes.py <sti/til/Cykelmanager.xlsx> --year 2025
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xlsx_stdlib import load  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

# år -> (ark, kolonne med det forkortede navn "T. Pogačar")
SHEETS = {
    2026: ("Master", "Player"),
    2025: ("Oversigten", 1),     # to kolonner hedder "Player"; den anden er den forkortede
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--year", type=int, default=2026, choices=sorted(SHEETS))
    args = ap.parse_args()

    sheet, name_col = SHEETS[args.year]
    rows = dict(load(args.xlsx))[sheet]
    head = rows[0]
    i_name = name_col if isinstance(name_col, int) else head.index(name_col)
    i_cat = head.index("Kat.")
    i_team = head.index("Team") if "Team" in head else None

    cats, teams, skipped = {}, {}, 0
    for r in rows[1:]:
        if len(r) <= max(i_name, i_cat):
            continue
        name, cat = str(r[i_name] or "").strip(), str(r[i_cat] or "").strip()
        cat = cat[3:].strip() if cat.startswith("Kat") else cat
        if not name or cat not in {"1", "2", "3", "4"}:
            skipped += 1
            continue
        cats[name] = int(cat)
        if i_team is not None and len(r) > i_team and r[i_team]:
            teams[name] = str(r[i_team]).strip()

    out = ROOT / f"data/sources/classics/categories_{args.year}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "source": f"Klassiker Manager {args.year} — {sheet}-arket i Anders' noter "
                  f"(navn{', hold' if teams else ''} + kategori).",
        "categories": dict(sorted(cats.items())),
    }
    if teams:
        data["teams"] = dict(sorted(teams.items()))
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    c = collections.Counter(cats.values())
    print(f"Skrev {out.relative_to(ROOT)} — {len(cats)} ryttere "
          f"(Kat1 {c[1]}, Kat2 {c[2]}, Kat3 {c[3]}, Kat4 {c[4]}); {skipped} uden kategori sprunget over")


if __name__ == "__main__":
    main()
