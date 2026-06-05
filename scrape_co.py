"""
Targeted CyclingOracle scraper — only fetches riders in riders.json.

Usage:
    python scrape_co.py

Saves: data/cache/cyclingoracle.json
Time:  ~3-4 minutes for ~196 riders (0.8 s delay)

The cache is keyed by rider_id from riders.json (not just name), making
lookup in predictor.py reliable even when names differ slightly.
"""
from __future__ import annotations
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT     = Path(__file__).parent
DATA     = ROOT / "data"
CACHE_DIR = DATA / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_PATH = CACHE_DIR / "cyclingoracle.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}
DELAY = 0.8   # seconds between requests

RATING_KEYS = ["AVG", "SPR", "FLT", "COB", "HLL", "MTN", "GC", "ITT", "PR"]
RATING_PAT  = re.compile(
    r"(AVG|SPR|FLT|COB|HLL|MTN|GC|ITT|PR)\s+(\d+)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Step 1: Collect all CyclingOracle rider URLs
# ---------------------------------------------------------------------------

def fetch_all_co_urls() -> list[str]:
    urls = []
    for page in range(1, 4):
        sitemap = (
            f"https://www.cyclingoracle.com/nl/"
            f"sitemaps-1-section-riders-1-sitemap-p{page}.xml"
        )
        try:
            r = requests.get(sitemap, headers=HEADERS, timeout=30)
            soup = BeautifulSoup(r.text, "xml")
            locs = [l.text for l in soup.find_all("loc") if "/renners/" in l.text]
            urls.extend(locs)
            print(f"  Sitemap p{page}: {len(locs)} rider URLs")
        except Exception as e:
            print(f"  Sitemap p{page} error: {e}")
        time.sleep(0.4)
    return urls


# ---------------------------------------------------------------------------
# Step 2: Match sitemap URLs to riders.json
# ---------------------------------------------------------------------------

def _slug(url: str) -> str:
    """Extract name slug (without trailing numeric ID) from CO URL."""
    slug = url.rstrip("/").split("/")[-1]
    return re.sub(r"-\d+$", "", slug)   # "jonas-vingegaard-38195" → "jonas-vingegaard"


def _slug_to_words(slug: str) -> list[str]:
    return slug.lower().replace("-", " ").split()


def match_riders(
    co_urls: list[str],
    riders: list[dict],
) -> dict[str, str]:
    """
    Return {rider_id: co_url} for the best URL match per rider.

    Matching strategy (in order):
      1. All words in rider full_name (lowercase) are all in the slug words
      2. Last-name exact match (for short slugs)
    """
    # Build slug-word lookup
    slug_map: dict[str, str] = {}   # slug_words_key → url
    for url in co_urls:
        slug = _slug(url)
        key  = " ".join(sorted(_slug_to_words(slug)))
        slug_map[key] = url

    matched: dict[str, str] = {}
    unmatched: list[str]    = []

    for rider in riders:
        rid   = rider["id"]
        fname = rider["full_name"].lower()
        words = fname.split()
        key   = " ".join(sorted(words))

        if key in slug_map:
            matched[rid] = slug_map[key]
            continue

        # Fallback: find URLs where ALL name words appear in the slug words
        best = None
        for url in co_urls:
            sw = _slug_to_words(_slug(url))
            if all(w in sw for w in words):
                best = url
                break
        if best:
            matched[rid] = best
            continue

        # Last-name only match (single result only)
        last = words[-1]
        candidates = [u for u in co_urls if last in _slug(u)]
        if len(candidates) == 1:
            matched[rid] = candidates[0]
            continue

        unmatched.append(rider["full_name"])

    return matched, unmatched


# ---------------------------------------------------------------------------
# Step 3: Scrape each matched rider
# ---------------------------------------------------------------------------

def scrape_rider(url: str) -> dict | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" ", strip=True)

        ratings: dict[str, float] = {}
        for m in RATING_PAT.finditer(text):
            key = m.group(1).upper()
            if key in RATING_KEYS:
                # Keep the LAST occurrence in case duplicates
                ratings[key] = float(m.group(2))

        # Fallback: data-value attributes
        if not ratings:
            for tag in soup.find_all(True, {"data-value": True}):
                label = tag.get("data-label", "").upper()
                if label in RATING_KEYS:
                    try:
                        ratings[label] = float(tag["data-value"])
                    except (ValueError, KeyError):
                        pass

        return ratings or None
    except Exception as e:
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="CyclingOracle scraper")
    parser.add_argument("--riders-file", default=None,
                        help="Alternativ JSON-fil med rytterliste "
                             "(f.eks. data/cache/dauphine2026_players.json). "
                             "Henter kun ryttere der mangler i cachen.")
    args = parser.parse_args()

    # Load or resume cache
    cache: dict = {}
    if CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        print(f"Resuming from existing cache: {len(cache)} riders already scraped")

    if args.riders_file:
        alt_path = Path(args.riders_file)
        alt_data = json.loads(alt_path.read_text(encoding="utf-8"))
        if isinstance(alt_data, list):
            riders = [{"id": r["id"], "full_name": r["full_name"]} for r in alt_data
                      if r.get("id") and r.get("full_name")]
        else:
            riders = [{"id": k, "full_name": v.get("holdet_name", k)}
                      for k, v in alt_data.items()]
        # Only riders missing from cache
        riders = [r for r in riders if r["id"] not in cache]
        print(f"Ryttere fra {alt_path.name} manglende i cache: {len(riders)}")
    else:
        riders = json.loads((DATA / "riders.json").read_text(encoding="utf-8"))

    print(f"\nHenter CyclingOracle sitemap URLs ({3} sider)...")
    co_urls = fetch_all_co_urls()
    print(f"Total CO rider URLs: {len(co_urls)}")

    print(f"\nMatcher {len(riders)} ryttere mod CO URLs...")
    matched_map, unmatched = match_riders(co_urls, riders)
    print(f"  Matchede:    {len(matched_map)}")
    print(f"  Ikke matchede: {len(unmatched)}")
    if unmatched:
        print("  Ikke matchede ryttere:")
        for name in sorted(unmatched):
            print(f"    - {name}")

    # Determine which still need scraping
    to_scrape = [
        (rid, url)
        for rid, url in matched_map.items()
        if rid not in cache
    ]
    print(f"\nSkal scrapes: {len(to_scrape)} ryttere "
          f"({len(matched_map) - len(to_scrape)} i cache)")

    if not to_scrape:
        print("Alle ryttere allerede i cache. Intet at gore.")
        return

    estimated = len(to_scrape) * DELAY
    print(f"Estimeret tid: ~{estimated:.0f}s ({estimated/60:.1f} min)")
    print()

    done = 0
    errors = 0
    for i, (rid, url) in enumerate(to_scrape):
        rider = next(r for r in riders if r["id"] == rid)
        ratings = scrape_rider(url)
        if ratings:
            cache[rid] = {
                "name":    rider["full_name"],
                "url":     url,
                "ratings": ratings,
            }
            done += 1
        else:
            errors += 1

        if (i + 1) % 20 == 0 or (i + 1) == len(to_scrape):
            print(f"  {i+1:>4}/{len(to_scrape)}  ok={done}  fejl={errors}  "
                  f"senest: {rider['full_name']}")
            # Save progress
            CACHE_PATH.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        time.sleep(DELAY)

    # Final save
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nFaerdig! {done} ryttere gemt til {CACHE_PATH}")
    print(f"Fejl/ingen data: {errors}")

    # Quick spot-check
    print("\nSpot-check (top picks):")
    for name in ["jonathan_milan", "paul_magnier", "jonas_vingegaard", "dylan_groenewegen"]:
        if name in cache:
            print(f"  {cache[name]['name']}: {cache[name]['ratings']}")
        else:
            print(f"  {name}: ikke fundet i cache")


if __name__ == "__main__":
    main()
