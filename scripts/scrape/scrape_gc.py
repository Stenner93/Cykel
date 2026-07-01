"""
GC standings + jersey leader scraper for the current Grand Tour.

Fetches current GC standings and jersey leaders from ProCyclingStats and
matches them to rider IDs in riders.json.

Usage:
    python scrape_gc.py                        # scrape TdF 2026 by default
    python scrape_gc.py --race giro-d-italia/2026

Output:
    data/cache/gc_standings.json   — {rider_id: gc_rank}  (rank 1 = leader)
    data/cache/jerseys.json        — {rider_id: [jersey_list]}
                                      jersey codes: "yellow", "green", "polka", "white"

The data is consumed by run_daily.py which passes it to predict_all():
    current_gc      — activates GC bonus in predictor.py
    current_jerseys — activates jersey bonus in predictor.py

PCS GC page URL:
    https://www.procyclingstats.com/race/{race}/gc
    e.g. https://www.procyclingstats.com/race/tour-de-france/2026/gc

PCS jersey leaders page URL:
    https://www.procyclingstats.com/race/{race}/jerseys
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent.parent
DATA      = ROOT / "data"
CACHE_DIR = DATA / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

GC_CACHE_PATH      = CACHE_DIR / "gc_standings.json"
JERSEYS_CACHE_PATH = CACHE_DIR / "jerseys.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

DEFAULT_RACE = "tour-de-france/2026"

# Map PCS jersey class / colour hints to scoring_rules.json jersey keys.
# Must match keys in JERSEY dict in src/scoring.py:
#   "leader", "sprint", "mountain", "youth", "most_aggressive"
JERSEY_MAP = {
    # Yellow / Maillot jaune (GC leader) → "leader"
    "gc":        "leader",
    "yellow":    "leader",
    "leader":    "leader",
    # Green / Points → "sprint"
    "points":    "sprint",
    "green":     "sprint",
    "sprint":    "sprint",
    # Polka dot / KOM → "mountain"
    "kom":       "mountain",
    "polka":     "mountain",
    "mountain":  "mountain",
    "dotted":    "mountain",
    # White / Best young rider → "youth"
    "youth":     "youth",
    "young":     "youth",
    "white":     "youth",
}

DELAY = 0.5   # seconds between requests


# ---------------------------------------------------------------------------
# Name normalisation (mirrors scrape_pcs.py approach)
# ---------------------------------------------------------------------------

_CHAR_MAP = str.maketrans({
    "ø": "o", "Ø": "o",
    "æ": "ae", "Æ": "ae",
    "å": "a", "Å": "a",
    "ð": "d", "þ": "th",
    "ß": "ss",
})


def _norm(name: str) -> str:
    s = name.strip().lower()
    s = s.translate(_CHAR_MAP)
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _last(name: str) -> str:
    return _norm(name).split()[-1] if name.strip() else ""


# ---------------------------------------------------------------------------
# Rider name → rider_id matcher (same fuzzy logic as predict_all)
# ---------------------------------------------------------------------------

def _build_lookup(riders: list[dict]) -> dict[str, str]:
    """Return {normalised_name: rider_id} with full + last-name keys."""
    lut: dict[str, str] = {}
    for r in riders:
        rid   = r["id"]
        full  = _norm(r["full_name"])
        short = _norm(r.get("short_name", ""))
        lut[full]  = rid
        if short:
            lut[short] = rid
        # Also index by last name for single-word fallback
        last = full.split()[-1]
        if last not in lut:          # don't overwrite full-name match
            lut[last] = rid
    return lut


def _match_name(name: str, lut: dict[str, str], riders: list[dict]) -> str | None:
    """Return rider_id for `name`, or None if not matched."""
    nl = _norm(name)
    if nl in lut:
        return lut[nl]
    # Last-name-only fallback
    last = nl.split()[-1] if " " in nl or nl else nl
    first = nl.split()[0] if " " in nl else ""
    candidates = [(k, v) for k, v in lut.items() if k.split()[-1] == last]
    if len(candidates) == 1:
        return candidates[0][1]
    if len(candidates) > 1 and first:
        for k, v in candidates:
            if k.split()[0] == first:
                return v
        # Try first initial
        for k, v in candidates:
            kw = k.split()
            if kw and kw[0][:1] == first[:1]:
                return v
    return None


# ---------------------------------------------------------------------------
# PCS fetch helpers
# ---------------------------------------------------------------------------

def _fetch(url: str) -> BeautifulSoup | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 200:
            return BeautifulSoup(resp.text, "html.parser")
        print(f"  [WARNING] {url} returned HTTP {resp.status_code}")
        return None
    except Exception as exc:
        print(f"  [WARNING] Could not fetch {url}: {exc}")
        return None


# ---------------------------------------------------------------------------
# GC standings parser
# ---------------------------------------------------------------------------

def fetch_gc_standings(race: str, riders: list[dict]) -> dict[str, int]:
    """
    Fetch GC standings for `race` (e.g. 'tour-de-france/2026') and return
    {rider_id: gc_rank}.  Only includes riders present in riders.json.
    """
    url  = f"https://www.procyclingstats.com/race/{race}/gc"
    print(f"  Henter GC-stilling: {url}")
    soup = _fetch(url)
    if soup is None:
        return {}

    lut = _build_lookup(riders)

    gc: dict[str, int] = {}
    rank = 0

    # PCS GC table: look for rows with rider links
    rows = soup.select("table tbody tr, ul.list.sortable li")
    if not rows:
        # Fallback: any table row
        rows = soup.select("tr")

    for row in rows:
        # Try to find rank cell
        rank_cell = row.select_one("td.rankr, td:first-child, .rnk")
        name_cell = row.select_one("a[href*='/rider/']")

        if name_cell is None:
            continue

        # Extract rank
        if rank_cell and rank_cell.get_text(strip=True).isdigit():
            rank = int(rank_cell.get_text(strip=True))
        else:
            rank += 1  # fallback: auto-increment

        # Extract name from link text or href
        rider_name = name_cell.get_text(strip=True)
        if not rider_name:
            href = name_cell.get("href", "")
            # /rider/jonas-vingegaard → "Jonas Vingegaard"
            slug = href.split("/rider/")[-1].replace("-", " ").title()
            rider_name = slug

        rid = _match_name(rider_name, lut, riders)
        if rid and rank > 0:
            gc[rid] = rank

    print(f"  GC-stilling: {len(gc)} ryttere matchet")
    return gc


# ---------------------------------------------------------------------------
# Jersey leaders parser
# ---------------------------------------------------------------------------

def fetch_jerseys(race: str, riders: list[dict]) -> dict[str, list[str]]:
    """
    Fetch current jersey leaders and return {rider_id: [jersey_codes]}.
    A rider may hold multiple jerseys (e.g. GC + young rider).
    """
    url  = f"https://www.procyclingstats.com/race/{race}/jerseys"
    print(f"  Henter trøjeledere: {url}")
    soup = _fetch(url)
    if soup is None:
        # Fallback: try /gc page which sometimes lists jersey wearers
        return {}

    lut = _build_lookup(riders)
    jerseys: dict[str, list[str]] = {}

    # PCS jerseys page typically has a table per jersey with rider links
    # Try to find jersey sections by heading keywords
    for elem in soup.find_all(["h2", "h3", "th", "div"]):
        text_lower = elem.get_text(strip=True).lower()
        jersey_code = None
        for keyword, code in JERSEY_MAP.items():
            if keyword in text_lower:
                jersey_code = code
                break
        if jersey_code is None:
            continue

        # Find the first rider link after this heading
        next_link = elem.find_next("a", href=re.compile(r"/rider/"))
        if next_link is None:
            continue
        rider_name = next_link.get_text(strip=True)
        rid = _match_name(rider_name, lut, riders)
        if rid:
            jerseys.setdefault(rid, [])
            if jersey_code not in jerseys[rid]:
                jerseys[rid].append(jersey_code)

    if not jerseys:
        print("  [INFO] Ingen trøjedata fundet på jerseys-siden")

    print(f"  Trøjeledere: {jerseys}")
    return jerseys


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape GC standings + jerseys")
    parser.add_argument(
        "--race", default=DEFAULT_RACE,
        help=f"PCS race path (default: {DEFAULT_RACE})",
    )
    args = parser.parse_args()

    riders_path = DATA / "riders.json"
    if not riders_path.exists():
        print(f"  [ERROR] {riders_path} not found")
        sys.exit(1)
    riders = json.loads(riders_path.read_text(encoding="utf-8"))
    print(f"  Ryttere indlæst: {len(riders)}")

    gc_standings = fetch_gc_standings(args.race, riders)
    time.sleep(DELAY)
    jersey_leaders = fetch_jerseys(args.race, riders)

    GC_CACHE_PATH.write_text(
        json.dumps(gc_standings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    JERSEYS_CACHE_PATH.write_text(
        json.dumps(jersey_leaders, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n  Gemt:")
    print(f"    {GC_CACHE_PATH}  ({len(gc_standings)} ryttere)")
    print(f"    {JERSEYS_CACHE_PATH}  ({len(jersey_leaders)} trøjer)")

    # Show top 5 GC
    if gc_standings:
        top5 = sorted(gc_standings.items(), key=lambda x: x[1])[:5]
        id_to_name = {r["id"]: r["full_name"] for r in riders}
        print("\n  Top 5 GC:")
        for rid, rank in top5:
            print(f"    {rank}. {id_to_name.get(rid, rid)}")


if __name__ == "__main__":
    main()
