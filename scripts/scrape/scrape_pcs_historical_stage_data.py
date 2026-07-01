"""
Fetch p_class (PCS stage profile classification 1-5) and finish altitude
for all historical race/year combinations in data/ml/historical_results.json.

These two values cannot be derived reliably from existing data:
  - p_class distinguishes p4 (mountain, valley finish) from p5 (summit finish)
    and p1 (flat sprint) from p2 (rolling sprint) — distinctions that
    strongly affect who wins but are collapsed in our 4-category stage_type.
  - finish_altitude (metres) is a continuous proxy for summit vs valley finish.

Run this once to populate the cache, then rebuild training data and retrain
the placement model.

Output: data/cache/pcs_historical_stage_data.json
  {
    "tdf/2021": {"1": {"p_class": 3, "finish_alt": 150}, ...},
    "giro/2022": {...},
    ...
  }

Usage:
    python scrape_pcs_historical_stage_data.py            # full run
    python scrape_pcs_historical_stage_data.py --no-alts  # p_class only (~50 req)
    python scrape_pcs_historical_stage_data.py --reset    # clear cache and refetch
    python scrape_pcs_historical_stage_data.py --test 3   # first 3 races only
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT  = Path(__file__).parent
DATA  = ROOT / "data"
CACHE = DATA / "cache"
ML    = DATA / "ml"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
DELAY = 0.6

# Maps historical race keys → PCS race slugs (same as scrape_pcs_history.py)
RACE_SLUGS: dict[str, str] = {
    "tdf":       "tour-de-france",
    "giro":      "giro-d-italia",
    "vuelta":    "vuelta-a-espana",
    "pn":        "paris-nice",
    "tirreno":   "tirreno-adriatico",
    "catalunya": "volta-a-catalunya",
    "basque":    "itzulia-basque-country",
    "romandie":  "tour-de-romandie",
    "dauphine":  "criterium-du-dauphine",
    "suisse":    "tour-de-suisse",
}

PCS_PROFILE_TO_CLASS: dict[str, int] = {
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5,
}

_ALT_LABELS = {"finish:", "finish altitude:", "arrival:", "arrivée:", "arrivo:"}


def _get(session: requests.Session, url: str, retries: int = 3) -> str | None:
    for attempt in range(retries):
        try:
            r = session.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(DELAY * (attempt + 1))
    return None


def _fetch_p_class(session: requests.Session, race_slug: str, year: int) -> dict[int, int]:
    """Fetch p_class (1-5) for each stage of a race from the stages overview page."""
    url  = f"https://www.procyclingstats.com/race/{race_slug}/{year}/stages"
    html = _get(session, url)
    if not html:
        return {}

    soup          = BeautifulSoup(html, "html.parser")
    stage_href_re = re.compile(r"/stage-(\d+)$")
    profile_re    = re.compile(r"\bp([1-9])\b")
    result: dict[int, int] = {}

    for row in soup.find_all("tr"):
        a = row.find("a", href=stage_href_re)
        if not a:
            continue
        sn = int(stage_href_re.search(a["href"]).group(1))
        for span in row.find_all("span", class_=True):
            for cls in span.get("class", []):
                m = profile_re.fullmatch(cls)
                if m:
                    result[sn] = int(m.group(1))
                    break
            if sn in result:
                break

    time.sleep(DELAY)
    return result


def _fetch_finish_alt(session: requests.Session, race_slug: str, year: int, stage: int) -> int | None:
    """Fetch finish altitude (metres) for a single stage page."""
    url  = f"https://www.procyclingstats.com/race/{race_slug}/{year}/stage-{stage}"
    html = _get(session, url)
    if not html:
        return None

    lines = BeautifulSoup(html, "html.parser").get_text("\n", strip=True).split("\n")
    for i, line in enumerate(lines):
        if line.strip().lower() in _ALT_LABELS and i + 1 < len(lines):
            m = re.match(r"(\d+)", lines[i + 1].strip())
            if m:
                return int(m.group(1))
    return None


def _race_year_combos() -> list[tuple[str, int]]:
    """Return sorted list of (race_key, year) from historical_results.json."""
    path = ML / "historical_results.json"
    if not path.exists():
        print(f"[!] {path} mangler — kør scrape_pcs_history.py først", file=sys.stderr)
        return []
    records = json.loads(path.read_text(encoding="utf-8"))
    seen: set[tuple[str, int]] = set()
    result = []
    for r in records:
        key = (r["race"], r["year"])
        if key not in seen and r["race"] in RACE_SLUGS:
            seen.add(key)
            result.append(key)
    return sorted(result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-alts", action="store_true",
                        help="Spring finish-altitude over — hent kun p_class (~50 req)")
    parser.add_argument("--reset", action="store_true",
                        help="Ryd cache og hent alt forfra")
    parser.add_argument("--test", type=int, default=0, metavar="N",
                        help="Kun de første N løb (test)")
    args = parser.parse_args()

    out_path = CACHE / "pcs_historical_stage_data.json"
    cache: dict[str, dict] = {}
    if out_path.exists() and not args.reset:
        cache = json.loads(out_path.read_text(encoding="utf-8"))

    combos = _race_year_combos()
    if args.test:
        combos = combos[:args.test]

    # Separate already-done from todo
    todo = [(r, y) for r, y in combos if f"{r}/{y}" not in cache]

    n_pclass_req = len(todo)
    # Estimate stage count from historical data for alt requests
    path = ML / "historical_results.json"
    records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    stages_per_race: dict[str, int] = {}
    for r in records:
        key = f"{r['race']}/{r['year']}"
        stages_per_race[key] = max(stages_per_race.get(key, 0), r["stage"])
    n_alt_req = 0 if args.no_alts else sum(
        stages_per_race.get(f"{r}/{y}", 20) for r, y in todo
    )

    print(f"Historiske løb i alt:    {len(combos)}")
    print(f"  Allerede cached:       {len(combos) - len(todo)}")
    print(f"  Skal hentes:           {len(todo)}")
    print(f"  Etapeliste-requests:   {n_pclass_req}")
    print(f"  Individuel stage req:  {'0 (--no-alts)' if args.no_alts else n_alt_req}")
    est_secs = n_pclass_req * (DELAY + 0.2) + n_alt_req * (DELAY + 0.2)
    print(f"  Estimeret tid:         ~{est_secs:.0f}s ({est_secs/60:.1f} min)\n")

    if not todo:
        print("Intet at hente — alt er cached.")
        return

    session = requests.Session()

    for i, (race_key, year) in enumerate(todo, 1):
        race_slug = RACE_SLUGS[race_key]
        cache_key = f"{race_key}/{year}"
        print(f"[{i}/{len(todo)}] {cache_key} ({race_slug}/{year})…")

        p_classes = _fetch_p_class(session, race_slug, year)
        if not p_classes:
            print(f"  [!] Ingen etaper fundet — sidesprunget")
            cache[cache_key] = {}
            continue

        stage_data: dict[str, dict] = {}
        for sn, pc in sorted(p_classes.items()):
            stage_data[str(sn)] = {"p_class": pc, "finish_alt": None}
        print(f"  p_class: {len(p_classes)} etaper (p1={sum(1 for v in p_classes.values() if v==1)}, "
              f"p2={sum(1 for v in p_classes.values() if v==2)}, "
              f"p3={sum(1 for v in p_classes.values() if v==3)}, "
              f"p4={sum(1 for v in p_classes.values() if v==4)}, "
              f"p5={sum(1 for v in p_classes.values() if v==5)})")

        if not args.no_alts:
            n_found = 0
            for sn in sorted(p_classes.keys()):
                fa = _fetch_finish_alt(session, race_slug, year, sn)
                stage_data[str(sn)]["finish_alt"] = fa
                if fa is not None:
                    n_found += 1
                time.sleep(DELAY)
            print(f"  finish_alt: {n_found}/{len(p_classes)} etaper med data")

        cache[cache_key] = stage_data
        out_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nFærdig. {len(cache)} løb i cache.")
    print(f"Gemt: {out_path}")
    print("\nNæste skridt:")
    print("  1. python build_placement_training_data.py")
    print("  2. python train_placement_model.py")
    print("  3. python build_tdf_web_data.py")


if __name__ == "__main__":
    main()
