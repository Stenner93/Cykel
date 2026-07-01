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
import unicodedata
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent.parent
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

# Chars not handled by NFKD decomposition — two variants for æ
# Primary:  æ → "ae"  (e.g. Wærenskjold → waerenskjold)
# Alt:      æ → "a"   (e.g. Træen → traen, because CO treats "æe" as "ae" not "aee")
_SPECIAL = {
    ord('ø'): 'o',  ord('Ø'): 'o',
    ord('æ'): 'ae', ord('Æ'): 'ae',
    ord('ß'): 'ss',
    ord('ð'): 'd',  ord('Ð'): 'd',
    ord('þ'): 'th', ord('Þ'): 'th',
}
_SPECIAL_ALT = {**_SPECIAL, ord('æ'): 'a', ord('Æ'): 'a'}

# Common short-name aliases: "official" → "CO name"
FIRST_NAME_ALIASES: dict[str, str] = {
    "thomas": "tom",
    "tom":    "thomas",
    "mathieu": "mat",
    "alexander": "alex",
    "alex": "alexander",
}

OVERRIDES_PATH = CACHE_DIR / "co_url_overrides.json"


def _normalize(s: str, alt: bool = False) -> str:
    """Lowercase, transliterate special chars, strip accents."""
    table = _SPECIAL_ALT if alt else _SPECIAL
    s = s.translate(table)
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


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
    return [_normalize(w) for w in slug.split("-")]


def _name_variants(full_name: str) -> list[list[str]]:
    """
    Return sorted word-lists to try for CO slug matching.
    Multiple variants handle accents, apostrophes and first-name aliases.
    """
    norm = _normalize(full_name)
    variants: list[list[str]] = []

    # Variant A: replace apostrophes and hyphens with spaces
    va = re.sub(r"[''`\-]", " ", norm).split()
    variants.append(sorted(va))

    # Variant B: remove apostrophes entirely ("O'Connor" → "oconnor" as one word)
    vb = re.sub(r"[''`]", "", norm)
    vb = re.sub(r"-", " ", vb).split()
    if sorted(vb) != sorted(va):
        variants.append(sorted(vb))

    # Variant C: swap first name via alias (Thomas→Tom, etc.)
    if va and va[0] in FIRST_NAME_ALIASES:
        alias_words = [FIRST_NAME_ALIASES[va[0]]] + va[1:]
        variants.append(sorted(alias_words))

    # Variant D: æ→a instead of æ→ae (for e.g. Træen→traen where CO writes "ae" not "aee")
    norm_alt = _normalize(full_name, alt=True)
    vd = re.sub(r"[''`\-]", " ", norm_alt).split()
    if sorted(vd) not in variants:
        variants.append(sorted(vd))

    return variants


def match_riders(
    co_urls: list[str],
    riders: list[dict],
) -> tuple[dict[str, str], list[str]]:
    """
    Return ({rider_id: co_url}, unmatched_names).

    Matching strategy (tried in order per rider):
      1. Full name → sorted words, possibly in multiple normalized variants
         (handles accents, apostrophes like O'Connor→oconnor, Thomas→Tom)
      2. All-words-in-slug containment check
      3. Drop middle names: try first+last only
      4. Drop each middle word individually
      5. Drop last compound surname (Ion Izagirre Insausti → ion izagirre)
      6. Last-name unique match
    """
    # Build slug-word lookup (normalized)
    slug_map: dict[str, str] = {}   # " ".join(sorted normalized words) → url
    for url in co_urls:
        slug = _slug(url)
        key  = " ".join(sorted(_slug_to_words(slug)))
        slug_map[key] = url

    matched: dict[str, str] = {}
    unmatched: list[str]    = []

    for rider in riders:
        rid  = rider["id"]
        name = rider["full_name"]

        # --- Strategy 1: full-name variants ---
        found = False
        all_variants = _name_variants(name)
        for wlist in all_variants:
            key = " ".join(wlist)
            if key in slug_map:
                matched[rid] = slug_map[key]
                found = True
                break
        if found:
            continue

        # Use the base (variant A) words for further strategies
        words = re.sub(r"[''`\-]", " ", _normalize(name)).split()

        # --- Strategy 2: slug containment ---
        best = None
        for url in co_urls:
            sw = _slug_to_words(_slug(url))
            if all(w in sw for w in words):
                best = url
                break
        if best:
            matched[rid] = best
            continue

        # --- Strategies 3-6: name reduction (only for 3+ word names) ---
        if len(words) > 2:
            # 3. First + last only
            short_key = " ".join(sorted([words[0], words[-1]]))
            if short_key in slug_map:
                matched[rid] = slug_map[short_key]
                continue

            # 4. Drop each middle word
            dropped = False
            for drop_i in range(1, len(words) - 1):
                reduced = sorted(w for i, w in enumerate(words) if i != drop_i)
                if " ".join(reduced) in slug_map:
                    matched[rid] = slug_map[" ".join(reduced)]
                    dropped = True
                    break
            if dropped:
                continue

            # 5. Drop last compound surname
            abl_key = " ".join(sorted(words[:-1]))
            if abl_key in slug_map:
                matched[rid] = slug_map[abl_key]
                continue

        # 6. Last-name unique match
        last = _normalize(name.split()[-1])
        candidates = [u for u in co_urls if last in _slug(u)]
        if len(candidates) == 1:
            matched[rid] = candidates[0]
            continue

        unmatched.append(name)

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

    # Apply manual URL overrides for edge cases the algorithm can't match
    url_overrides: dict = {}
    if OVERRIDES_PATH.exists():
        url_overrides = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
    rider_ids_in_run = {r["id"] for r in riders}
    applied = 0
    for rid, url in url_overrides.items():
        if rid in rider_ids_in_run and rid not in matched_map and rid not in cache:
            # Convert /en/riders/ to /nl/renners/ so scrape_rider() works consistently
            nl_url = url.replace("/en/riders/", "/nl/renners/")
            matched_map[rid] = nl_url
            applied += 1
            print(f"  Override anvendt: {rid} → {nl_url.split('/')[-1]}")
    if applied:
        print(f"  Overrides i alt: {applied}")

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

    # Build lookup dict once so loop doesn't scan all riders on every iteration
    rider_by_id = {r["id"]: r for r in riders}

    done = 0
    errors = 0
    for i, (rid, url) in enumerate(to_scrape):
        rider = rider_by_id[rid]
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
