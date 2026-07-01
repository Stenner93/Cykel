"""
Scraper til historiske etaperesultater fra ProCyclingStats.

Henter alle placeringer for TdF, Giro, Vuelta + 7 et-ugersløb 2021-2025.
Output: data/ml/historical_results.json

Format:
  [
    {
      "race":         "tour-de-france",
      "year":         2024,
      "stage":        3,
      "stage_type":   "hilly",
      "profile_score": 185,
      "rider_slug":   "tadej-pogacar",
      "position":     1,
      "pcs_pts":      0,
      "dnf":          false
    }, ...
  ]

Usage:
    python scrape_pcs_history.py               # alle løb 2021-2025
    python scrape_pcs_history.py --years 2024 2025
    python scrape_pcs_history.py --races tdf   # kun TdF
    python scrape_pcs_history.py --reset       # ryd cache og hent alt
"""
from __future__ import annotations
import argparse
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent.parent
ML_DIR    = ROOT / "data" / "ml"
ML_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = ROOT / "data" / "cache"
OUT_PATH  = ML_DIR / "historical_results.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
DELAY = 0.6

RACES = {
    # Grand Tours
    "tdf":       "tour-de-france",
    "giro":      "giro-d-italia",
    "vuelta":    "vuelta-a-espana",
    # Et-ugersløb (GT-opvarmning — bruges som placement model træningsdata)
    "pn":        "paris-nice",
    "tirreno":   "tirreno-adriatico",
    "catalunya": "volta-a-catalunya",
    "basque":    "itzulia-basque-country",
    "romandie":  "tour-de-romandie",
    "dauphine":  "criterium-du-dauphine",
    "suisse":    "tour-de-suisse",
}

# Fallback startlist quality scores (PCS scale, 0-1000+) used when scraping
# fails. TdF attracts the deepest WorldTour field, followed by Giro and Vuelta.
DEFAULT_RACE_QUALITY: dict[str, float] = {
    "tour-de-france":           1000.0,
    "giro-d-italia":             950.0,
    "vuelta-a-espana":           900.0,
    "criterium-du-dauphine":     870.0,
    "itzulia-basque-country":    830.0,
    "tour-de-suisse":            810.0,
    "tirreno-adriatico":         800.0,
    "volta-a-catalunya":         770.0,
    "tour-de-romandie":          750.0,
    "paris-nice":                730.0,
}

YEARS = [2021, 2022, 2023, 2024, 2025]

# PCS profile class → stage_type (reuse from scrape_pcs.py)
PCS_PROFILE_TO_TYPE = {
    "1": "sprint", "2": "sprint",
    "3": "hilly",  "4": "hilly",
    "5": "mountain",
}


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


def fetch_stage_list(session, race_slug: str, year: int) -> list[dict]:
    """
    Fetch stage metadata (num, type, profile_score, name) for a race year.
    Returns list of {stage, name, stage_type, profile_score}.
    """
    url  = f"https://www.procyclingstats.com/race/{race_slug}/{year}/stages"
    html = _get(session, url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    stage_href_re = re.compile(r"/stage-(\d+)$")
    profile_re    = re.compile(r"\bp([1-9])\b")
    stages = []

    for row in soup.find_all("tr"):
        a = row.find("a", href=stage_href_re)
        if not a:
            continue
        sn         = int(stage_href_re.search(a["href"]).group(1))
        stage_name = a.get_text(" ", strip=True)

        profiles = []
        for span in row.find_all("span", class_=True):
            for cls in span.get("class", []):
                m = profile_re.fullmatch(cls)
                if m:
                    profiles.append(m.group(1))

        if profiles:
            p = profiles[0]
            if re.search(r"\b(ITT|TT)\b", stage_name, re.I):
                stype = "tt"
            elif re.search(r"\bTTT\b", stage_name, re.I):
                stype = "ttt"
            else:
                stype = PCS_PROFILE_TO_TYPE.get(p, "hilly")
        else:
            stype = "hilly"

        stages.append({"stage": sn, "name": stage_name, "stage_type": stype,
                       "profile_class": profiles[0] if profiles else "?"})

    time.sleep(DELAY)
    return stages


def fetch_stage_results(session, race_slug: str, year: int, stage_num: int) -> list[dict]:
    """
    Fetch all finisher positions for a single stage.
    Returns list of {rider_slug, position, pcs_pts, dnf}.
    """
    url  = f"https://www.procyclingstats.com/race/{race_slug}/{year}/stage-{stage_num}"
    html = _get(session, url)
    if not html:
        return []

    soup   = BeautifulSoup(html, "html.parser")
    # Try both table ids PCS uses
    table  = soup.find("table", {"class": lambda c: c and "results" in c.lower()})
    if not table:
        table = soup.find("div", id="result-cont")

    results = []

    # Parse via text: each row is "pos | rider-link | time | pcs_pts"
    for row in (soup.find_all("tr") if not table else table.find_all("tr")):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        pos_text = cells[0].get_text(strip=True)
        # Position: numeric OR "DNF" / "DNS" / "OTL" / "DSQ"
        dnf  = False
        pos  = None
        if re.match(r"^\d+$", pos_text):
            pos = int(pos_text)
        elif pos_text.upper() in ("DNF", "DNS", "OTL", "DSQ", "AB"):
            dnf  = True
            pos  = 999
        else:
            continue

        # Rider slug from link
        rider_link = None
        for cell in cells:
            a = cell.find("a", href=True)
            if a and a["href"].startswith("rider/"):
                rider_link = a["href"].split("/")[1].split("?")[0]
                break
        if not rider_link:
            continue

        # PCS points (last numeric column)
        pcs_pts = 0
        for cell in reversed(cells):
            t = cell.get_text(strip=True)
            if re.match(r"^\d+$", t):
                pcs_pts = int(t)
                break

        results.append({
            "rider_slug": rider_link,
            "position":   pos,
            "pcs_pts":    pcs_pts,
            "dnf":        dnf,
        })

    time.sleep(DELAY)
    return results


def fetch_profile_score(session, race_slug: str, year: int, stage_num: int) -> int | None:
    """Fetch PCS ProfileScore for a single stage page."""
    url  = f"https://www.procyclingstats.com/race/{race_slug}/{year}/stage-{stage_num}"
    html = _get(session, url)
    if not html:
        return None
    lines = BeautifulSoup(html, "html.parser").get_text("\n", strip=True).split("\n")
    for i, line in enumerate(lines):
        if line.strip().lower() == "profilescore:" and i + 1 < len(lines):
            m = re.match(r"(\d+)", lines[i + 1].strip())
            if m:
                return int(m.group(1))
    return None


def scrape_race_quality(session, race_slug: str, year: int) -> float | None:
    """
    Fetch the PCS startlist quality score for a race-year from the overview page.

    PCS shows "Startlist quality: XXX" in the race sidebar at:
        https://www.procyclingstats.com/race/{race_slug}/{year}

    Returns the numeric quality score (0-1000+ scale) or None if not found.
    Falls back to DEFAULT_RACE_QUALITY if the race_slug is known.
    """
    url  = f"https://www.procyclingstats.com/race/{race_slug}/{year}"
    html = _get(session, url)
    if not html:
        return None

    # Search for "startlist quality" (case-insensitive) in page text.
    # PCS renders it as plain text like "Startlist quality\n935" or
    # as adjacent elements; we search the raw HTML for the numeric value
    # that follows the label.
    m = re.search(
        r"startlist\s+quality[^0-9]*?(\d+(?:\.\d+)?)",
        html,
        re.IGNORECASE,
    )
    if m:
        return float(m.group(1))

    # Also try the BeautifulSoup text extraction (handles split elements)
    soup  = BeautifulSoup(html, "html.parser")
    text  = soup.get_text("\n", strip=True)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if "startlist quality" in line.lower():
            # The number may be on the same line or the next one
            combined = " ".join(lines[i : i + 3])
            m2 = re.search(r"(\d+(?:\.\d+)?)", combined)
            if m2:
                return float(m2.group(1))

    return None


def scrape_race_year(
    session, race_key: str, race_slug: str, year: int,
    existing: set[tuple],
) -> list[dict]:
    """Scrape all stages for one race-year. Skip stages already in existing."""
    print(f"\n  {race_key.upper()} {year}", flush=True)
    stages = fetch_stage_list(session, race_slug, year)
    if not stages:
        print(f"    Ingen etaper fundet (løb ikke afholdt / URL fejl)")
        return []

    # Fetch startlist quality for this race-year (one request per race-year).
    # Falls back to DEFAULT_RACE_QUALITY if scraping fails; defaults to 1000.0
    # as a conservative estimate (treat unknown fields as max quality).
    quality = scrape_race_quality(session, race_slug, year)
    if quality is None:
        quality = DEFAULT_RACE_QUALITY.get(race_slug, 1000.0)
        print(f"    Startlist quality: {quality:.0f} (fallback)", flush=True)
    else:
        print(f"    Startlist quality: {quality:.0f}", flush=True)

    records = []
    for st in stages:
        sn  = st["stage"]
        key = (race_key, year, sn)
        if key in existing:
            print(f"    Etape {sn:>2}: cache hit", flush=True)
            continue

        print(f"    Etape {sn:>2} ({st['stage_type']:<8}) ...", end=" ", flush=True)
        ps = fetch_profile_score(session, race_slug, year, sn)
        results = fetch_stage_results(session, race_slug, year, sn)

        if not results:
            print("ingen resultater")
            continue

        for r in results:
            records.append({
                "race":              race_key,
                "year":              year,
                "stage":             sn,
                "stage_type":        st["stage_type"],
                "profile_score":     ps,
                "startlist_quality": quality,   # PCS field-strength score (0-1000+)
                "rider_slug":        r["rider_slug"],
                "position":          r["position"],
                "pcs_pts":           r["pcs_pts"],
                "dnf":               r["dnf"],
            })
        print(f"{len(results)} ryttere")

    return records


def main() -> None:
    global DELAY
    parser = argparse.ArgumentParser()
    parser.add_argument("--years",  nargs="+", type=int, default=YEARS)
    parser.add_argument("--races",  nargs="+", default=list(RACES.keys()),
                        choices=list(RACES.keys()),
                        help="Løb at hente (default: alle). Eks: --races tdf giro pn dauphine")
    parser.add_argument("--reset",  action="store_true",
                        help="Ryd cache og hent alt forfra")
    parser.add_argument("--delay",  type=float, default=DELAY)
    args = parser.parse_args()

    DELAY = args.delay

    # Load existing records
    if OUT_PATH.exists() and not args.reset:
        existing_records = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    else:
        existing_records = []

    existing_keys = {(r["race"], r["year"], r["stage"]) for r in existing_records}
    print(f"Eksisterende records: {len(existing_records):,}  "
          f"(løb-år-etape kombinationer: {len(existing_keys)})")

    session = requests.Session()
    new_records: list[dict] = []

    for race_key in args.races:
        race_slug = RACES[race_key]
        for year in sorted(args.years):
            new = scrape_race_year(session, race_key, race_slug, year, existing_keys)
            new_records.extend(new)
            # Incremental save every race-year
            if new:
                all_records = existing_records + new_records
                OUT_PATH.write_text(
                    json.dumps(all_records, ensure_ascii=False),
                    encoding="utf-8"
                )

    all_records = existing_records + new_records
    OUT_PATH.write_text(
        json.dumps(all_records, ensure_ascii=False, indent=1),
        encoding="utf-8"
    )
    print(f"\nFærdig: {len(new_records):,} nye records  →  {len(all_records):,} total")
    print(f"Gemt: {OUT_PATH}")


if __name__ == "__main__":
    main()
