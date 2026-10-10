#!/usr/bin/env python3
"""
Klassikerresultater fra procyclingstats — fundamentet under Klassiker Manager.

Den historiske resultatfil (data/ml/historical_results.json) dækker kun
etapeløb. Til et klassikerspil skal vi vide hvem der kører hvilke endagsløb,
og hvor de plejer at ende — hele vejen ned, fordi Manager-formatet betaler
placeringer langt ned i feltet, ikke kun top-15.

Pr. løb og år hentes:
  - fuld resultatliste: placering (eller DNF/DNS/OTL), rytter-slug, navn, hold
  - løbsdata: dato, profilscore, højdemeter, distance, startlistekvalitet,
    og hvordan løbet blev vundet

Grupperingen i blokke følger spillets kalender og den måde du selv har
inddelt dem i dine noter: forårsløbene frem til Roubaix er "brosten"-blokken
(også selvom Strade Bianche er grus og Sanremo er flad), resten er
Ardennerne + Eschborn-Frankfurt.

Brug:
    python scripts/scrape/scrape_classics_results.py                # 2021-2026
    python scripts/scrape/scrape_classics_results.py --years 2026
    python scripts/scrape/scrape_classics_results.py --force        # hent igen

Allerede hentede løb springes over, så scriptet kan genoptages.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pcs_browser import BASE, pcs_page  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/sources/classics"

# Spillets 13 løb i kalenderrækkefølge (Klassiker Manager 2026).
RACES = [
    ("omloop-het-nieuwsblad", "Omloop Het Nieuwsblad", "brosten"),
    ("strade-bianche",        "Strade Bianche",        "brosten"),
    ("milano-sanremo",        "Milano-Sanremo",        "brosten"),
    ("classic-brugge-de-panne", "Classic Brugge-De Panne", "brosten"),
    ("e3-harelbeke",          "E3 Saxo Classic",       "brosten"),
    ("gent-wevelgem",         "Gent-Wevelgem",         "brosten"),
    ("dwars-door-vlaanderen", "Dwars door Vlaanderen", "brosten"),
    ("ronde-van-vlaanderen",  "Ronde van Vlaanderen",  "brosten"),
    ("paris-roubaix",         "Paris-Roubaix",         "brosten"),
    ("amstel-gold-race",      "Amstel Gold Race",      "ardenner"),
    ("la-fleche-wallonne",    "La Flèche Wallonne",    "ardenner"),
    ("liege-bastogne-liege",  "Liège-Bastogne-Liège",  "ardenner"),
    ("eschborn-frankfurt",    "Eschborn-Frankfurt",    "ardenner"),
]

META = {
    "date": r"Date\s*([0-9]{1,2} \w+ [0-9]{4})",
    "profile_score": r"Profile score:\s*(\d+)",
    "vmeters": r"Vertical meters:\s*(\d+)",
    "distance_km": r"Distance:\s*([\d.]+)\s*km",
    "startlist_quality": r"Startlist quality score:\s*(\d+)",
    "won_how": r"Won how:\s*([^\n]+?)\s*(?:Avg\.|Avg temperature|Classification|\n|$)",
}
NUMERIC = {"profile_score", "vmeters", "startlist_quality"}


def parse_page(page) -> dict | None:
    # KUN den første resultattabel. Flere løb har en ekstra tabel med én
    # række (bjergpræmie, mest angrebsivrige) lige under, og tog man alle
    # tabeller med, overskrev dens "1" den rigtige vinder.
    rows = page.evaluate("""() => {
        const t = document.querySelector('table.results');   // den første — og kun den
        if (!t) return [];
        return Array.from(t.querySelectorAll('tbody tr')).map(tr => {
            const a = tr.querySelector("a[href*='rider/']");
            const tm = tr.querySelector("a[href*='team/']");
            const td = tr.querySelector('td');
            return {rank: td ? td.innerText.trim() : '',
                    slug: a ? a.getAttribute('href').replace(/^.*rider\//,'') : null,
                    name: a ? a.innerText.trim() : null,
                    team: tm ? tm.innerText.trim() : null};
        });
    }""")
    rows = [r for r in rows if r.get("slug")]
    if not rows:
        return None
    text = page.inner_text("body")
    meta = {}
    for k, rx in META.items():
        m = re.search(rx, text)
        if m:
            v = m.group(1).strip()
            if k in NUMERIC:
                v = int(v)
            elif k == "distance_km":
                v = float(v)
            meta[k] = v
    out = []
    for r in rows:
        rk = r["rank"]
        out.append({
            "rank": int(rk) if rk.isdigit() else None,
            "status": None if rk.isdigit() else (rk or None),
            "rider_slug": r["slug"],
            "rider_name": r["name"],
            "team": r["team"],
        })
    return {"meta": meta, "results": out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2021-2026", help="fx 2026 eller 2021-2026")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--delay", type=float, default=1.0, help="sekunder mellem løb (vær pæn)")
    args = ap.parse_args()

    if "-" in args.years:
        a, b = args.years.split("-")
        years = list(range(int(a), int(b) + 1))
    else:
        years = [int(args.years)]

    raw = OUT / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    failed = []

    with pcs_page() as (page, ctx):
        for year in years:
            for slug, name, block in RACES:
                f = raw / f"{year}_{slug}.json"
                if f.exists() and not args.force:
                    continue
                url = f"{BASE}/race/{slug}/{year}/result"
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3000)
                    parsed = parse_page(page) if resp and resp.status == 200 else None
                except Exception as e:  # netværk, timeout
                    parsed, resp = None, None
                    print(f"  ! {year} {name}: {str(e)[:80]}")
                if not parsed:
                    failed.append(f"{year} {name}")
                    print(f"  — {year} {name}: intet resultat ({resp.status if resp else 'fejl'})")
                    continue
                parsed.update({"race": name, "race_slug": slug, "block": block, "year": year,
                               "source": url})
                f.write_text(json.dumps(parsed, ensure_ascii=False))
                fin = sum(1 for r in parsed["results"] if r["rank"])
                print(f"  ✓ {year} {name:26} {len(parsed['results']):>3} startende, {fin:>3} i mål"
                      f"  profil {parsed['meta'].get('profile_score','–')}")
                time.sleep(args.delay)

    # Saml alt i én fil — samme form som den historiske etapefil, så de kan
    # bruges side om side.
    rows, races = [], []
    for f in sorted(raw.glob("*.json")):
        d = json.loads(f.read_text())
        races.append({k: d[k] for k in ("race", "race_slug", "block", "year", "source")} | d["meta"]
                     | {"starters": len(d["results"]),
                        "finishers": sum(1 for r in d["results"] if r["rank"])})
        for r in d["results"]:
            rows.append({"race": d["race"], "race_slug": d["race_slug"], "block": d["block"],
                         "year": d["year"], **r})
    (OUT / "results.json").write_text(json.dumps(
        {"source": "procyclingstats.com — fulde resultatlister", "races": races, "results": rows},
        ensure_ascii=False))
    print(f"\n{len(races)} løb, {len(rows)} rytterplaceringer → data/sources/classics/results.json")
    if failed:
        print("Mangler: " + "; ".join(failed))


if __name__ == "__main__":
    main()
