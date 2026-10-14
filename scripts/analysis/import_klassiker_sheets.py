#!/usr/bin/env python3
"""
Importerer to slags input til Klassiker Manager-siden:

  startlists — forventede startlister (flueben) fra fantasytool, i samme form
               som Kalender-fanen: en navnekolonne, evt. en kategorikolonne,
               og én kolonne pr. løb med ✅ / ❌ (eller 1/0, x, ja/nej).
               Kan være .xlsx (vælg ark med --sheet), .csv eller tab-separeret
               tekst kopieret fra Excel.

               FLETTES med det der allerede ligger: fantasytool sletter løb
               efterhånden som de er kørt, så en tom celle betyder "ingen ny
               viden" og overskriver aldrig et tidligere flueben.

  plan       — et holdudkast runde for runde (Planlægning-fanen): én kolonne
               pr. løb, 12 rytternavne under hver. Løb uden udfyldt hold får
               holdet fra løbet før.

Løbskolonnerne genkendes på navnet (fx "omloop het nieuwsblad", "la fleche
wallone") ud fra løbene i data/klassiker/config.json.

Brug:
    python scripts/analysis/import_klassiker_sheets.py startlists <fil> [--sheet Kalender] [--year 2027]
    python scripts/analysis/import_klassiker_sheets.py plan <fil> [--sheet Planlægning] [--year 2026]
Derefter: python scripts/build/build_klassiker_web_data.py
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xlsx_stdlib import load  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "data/klassiker/config.json"
SRC = ROOT / "data/sources/classics"

YES = {"✅", "1", "x", "ja", "yes", "true", "✓", "✔"}
# Ord der kan stå i en overskrift uden at pege på ét løb ("Player Race Participation").
GENERIC = {"race", "classic", "gold", "het", "de", "la", "van", "door", "grand", "prix", "gp"}
NO = {"❌", "0", "nej", "no", "false", "✗", "✘", "-"}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def race_matcher(races):
    """-> funktion(kolonneoverskrift) -> slug | None.

    Et løb genkendes på de ord i dets slug som intet andet løb har ("omloop",
    "fleche", "harelbeke" …). "vlaanderen" deles af Dwars og Flandern og
    tæller derfor ikke; "ronde" og "dwars" gør."""
    toks = {r["slug"]: set(r["slug"].split("-")) | {norm(r["short"])} for r in races}
    uniq = {}
    for slug, t in toks.items():
        others = set().union(*(v for k, v in toks.items() if k != slug))
        uniq[slug] = {w for w in t if w not in others and len(w) >= 2 and w not in GENERIC}

    def match(header):
        words = set(norm(header).split())
        hits = [slug for slug, u in uniq.items()
                if any(w in words or any(x.startswith(w[:6]) for x in words if len(w) >= 6) for w in u)]
        return hits[0] if len(hits) == 1 else None
    return match


def read_table(path, sheet=None):
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        sheets = load(p)
        if sheet:
            d = dict(sheets)
            if sheet not in d:
                sys.exit(f"arket '{sheet}' findes ikke — der er: {', '.join(n for n, _ in sheets)}")
            return d[sheet]
        return sheets[0][1]
    text = p.read_text(encoding="utf-8-sig")
    dialect = "excel-tab" if "\t" in text.splitlines()[0] else "excel"
    if dialect == "excel" and ";" in text.splitlines()[0] and "," not in text.splitlines()[0]:
        return [r for r in csv.reader(io.StringIO(text), delimiter=";")]
    return [r for r in csv.reader(io.StringIO(text), dialect=dialect)]


def header_row(rows, match, min_races=3):
    for i, r in enumerate(rows[:10]):
        cols = {j: match(c) for j, c in enumerate(r) if c not in (None, "")}
        cols = {j: s for j, s in cols.items() if s}
        if len(cols) >= min_races:
            return i, cols
    sys.exit("fandt ingen overskriftsrække med løbsnavne")


def import_startlists(args, cfg):
    races = cfg["races"]
    match = race_matcher(races)
    rows = read_table(args.file, args.sheet)
    hi, cols = header_row(rows, match)
    # Navnekolonnen: den første kolonne med "T. Pogačar"-agtige værdier.
    body = [r for r in rows[hi + 1:] if any(str(x or "").strip() for x in r)]

    def looks_name(v):
        return bool(re.match(r"^\s*[A-ZÀ-Þ][a-zà-ÿ]?\.", str(v or "")))
    ncols = max(len(r) for r in body)
    name_col = max(range(ncols), key=lambda j: sum(looks_name(r[j]) for r in body if len(r) > j))
    cat_col = next((j for j in range(ncols)
                    if sum(bool(re.fullmatch(r"(kat\.?\s*)?[1-4]", str(r[j] or "").strip(), re.I))
                           for r in body if len(r) > j) > len(body) / 3), None)

    # Fantasytool-arket kan være forskudt en kolonne mellem overskrift og
    # data ("fiksfakserier"): test om fluebenene står én til højre for
    # overskriften, og ret i så fald.
    def marks(j):
        return sum(1 for r in body if len(r) > j and str(r[j] or "").strip() in YES | NO)
    shift = 1 if sum(marks(j + 1) for j in cols) > sum(marks(j) for j in cols) * 1.2 else 0

    out = SRC / f"startlists_{args.year}.json"
    old = json.loads(out.read_text()) if out.exists() else {"riders": {}}
    riders = old.get("riders", {})
    new_flags = changed = 0
    for r in body:
        name = str(r[name_col] or "").strip() if len(r) > name_col else ""
        if not name:
            continue
        rec = riders.setdefault(name, {"flags": {}})
        if cat_col is not None and len(r) > cat_col:
            c = re.sub(r"\D", "", str(r[cat_col] or ""))
            if c in "1234" and c:
                rec["cat"] = int(c)
        for j, slug in cols.items():
            v = str(r[j + shift] or "").strip().lower() if len(r) > j + shift else ""
            v = {"✅": "✅", "❌": "❌"}.get(v, v)
            if v in YES or v == "✅":
                f = 1
            elif v in NO or v == "❌":
                f = 0
            else:
                continue                      # tom celle: behold det vi vidste
            if rec["flags"].get(slug) != f:
                changed += rec["flags"].get(slug) is not None
                new_flags += rec["flags"].get(slug) is None
                rec["flags"][slug] = f
    out.write_text(json.dumps({
        "year": args.year,
        "updated": dt.date.today().isoformat(),
        "source": "Forventede startlister (fantasytool), importeret med import_klassiker_sheets.py",
        "races": [r["slug"] for r in races],
        "riders": dict(sorted(riders.items())),
    }, ensure_ascii=False, indent=1))
    print(f"Skrev {out.relative_to(ROOT)}: {len(riders)} ryttere, {new_flags} nye flueben, "
          f"{changed} ændrede; løb fundet: {len(cols)}/{len(races)}"
          + (" (data forskudt én kolonne — rettet)" if shift else ""))


def import_plan(args, cfg):
    races = cfg["races"]
    match = race_matcher(races)
    rows = read_table(args.file, args.sheet or "Planlægning")
    hi, cols = header_row(rows, match)
    rounds, prev = {}, None
    by_slug = {s: j for j, s in cols.items()}
    for r in races:
        j = by_slug.get(r["slug"])
        team = []
        if j is not None:
            for row in rows[hi + 1:hi + 1 + 12]:
                v = str(row[j] or "").strip() if len(row) > j else ""
                if v and not re.fullmatch(r"[\d.,]+", v):
                    team.append(v)
        if len(team) < 12 and prev:
            print(f"  {r['short']}: kun {len(team)} navne — bruger holdet fra løbet før")
            team = list(prev)
        rounds[r["slug"]] = team
        prev = team
    out = SRC / f"plan_{args.year}.json"
    out.write_text(json.dumps({
        "year": args.year,
        "label": args.label,
        "source": "Holdudkast fra Planlægning-fanen — et UDKAST, ikke nødvendigvis det hold der blev kørt med.",
        "rounds": rounds,
    }, ensure_ascii=False, indent=1))
    print(f"Skrev {out.relative_to(ROOT)}: {sum(1 for t in rounds.values() if len(t) == 12)} fulde runder")


def main():
    cfg = json.loads(CONFIG.read_text())
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["startlists", "plan"])
    ap.add_argument("file")
    ap.add_argument("--sheet")
    ap.add_argument("--year", type=int, default=cfg["data_year"])
    ap.add_argument("--label", default="Dit udkast (Planlægning-fanen)")
    args = ap.parse_args()
    (import_startlists if args.what == "startlists" else import_plan)(args, cfg)


if __name__ == "__main__":
    main()
