#!/usr/bin/env python3
"""
Importerer kategorierne fra dine Klassiker Manager 2026-noter.

Spillets kategorier (Kat 1-4) er ikke til at hente fra Holdet længere — API'et
lukker efter sæsonen — men de står i Master-arket i dine noter. Kun
rytternavn og kategori tages med; resten af regnearket (planer, vurderinger)
bliver hos dig.

Brug:
    python scripts/analysis/import_classics_notes.py <sti/til/Cykelmanager_2026.xlsx>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xlsx_stdlib import load  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/sources/classics/categories_2026.json"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sheets = dict(load(sys.argv[1]))
    master = sheets["Master"]
    head = master[0]
    i_name, i_cat = head.index("Player"), head.index("Kat.")
    cats, skipped = {}, 0
    for r in master[1:]:
        if len(r) <= max(i_name, i_cat):
            continue
        name, cat = str(r[i_name]).strip(), str(r[i_cat]).strip()
        if not name or not cat.startswith("Kat"):
            skipped += 1
            continue
        cats[name] = int(cat[3:])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "Klassiker Manager 2026 — Master-arket i Anders' noter (navn + kategori).",
        "categories": dict(sorted(cats.items())),
    }, ensure_ascii=False, indent=1))
    from collections import Counter
    c = Counter(cats.values())
    print(f"Skrev {OUT.relative_to(ROOT)} — {len(cats)} ryttere "
          f"(Kat1 {c[1]}, Kat2 {c[2]}, Kat3 {c[3]}, Kat4 {c[4]}); {skipped} uden kategori sprunget over")


if __name__ == "__main__":
    main()
