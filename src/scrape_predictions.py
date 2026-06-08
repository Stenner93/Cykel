"""
Scrape stage win predictions from cycling web sources as VeloScore fallback.

Priority order (highest quality first):
  1. spilxperten.com  — actual Bet365 decimal odds → true win probability
  2. TV2 Axelgaard    — ⭐⭐⭐⭐⭐ ratings → approximate probability
  3. IDLProCycling    — Top/Outsiders/Long shots tiers → approximate probability

Usage:
    from src.scrape_predictions import get_stage_predictions
    odds = get_stage_predictions(
        cartridge="criterium-du-dauphine-2026",
        stage_num=2,
    )
    # Returns {rider_name_lower: win_probability} or {}

The returned dict keys match the `odds_data` format expected by predictor.predict_all():
  "dorian godon" → 0.18,  "wout van aert" → 0.15, ...
"""
from __future__ import annotations

import re
import time
import unicodedata
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}
TIMEOUT  = 10
DELAY    = 0.5   # seconds between requests

# ── Race slug mappings ──────────────────────────────────────────────────────────

# spilxperten.com/cykling/{slug}/
SPILXPERTEN_SLUG: dict[str, str] = {
    "criterium-du-dauphine-2026":   "criterium-du-dauphine",
    "tour-de-france-2026":          "tour-de-france",
    "giro-d-italia-2026":           "giro-d-italia",
    "vuelta-a-espana-2026":         "vuelta-a-espana",
}

# sport.tv2.dk/cykling/YYYY-MM-DD-axelgaards-optakt-til-N-etape-af-{slug}
TV2_RACE_SLUG: dict[str, str] = {
    "criterium-du-dauphine-2026":   "tour-auvergne-rhone-alpes",
    "tour-de-france-2026":          "tour-de-france",
    "giro-d-italia-2026":           "giro-d-italia",
    "vuelta-a-espana-2026":         "vuelta-a-espana",
}

# idlprocycling.com/cycling/{slug}-stage-N-preview-...
IDL_RACE_SLUG: dict[str, str] = {
    "criterium-du-dauphine-2026":   "tour-auvergne-rhone-alpes-2026",
    "tour-de-france-2026":          "tour-de-france-2026",
    "giro-d-italia-2026":           "giro-d-italia-2026",
}


# ── Utilities ───────────────────────────────────────────────────────────────────

def _norm(name: str) -> str:
    """Normalise rider name: lowercase, strip accents, strip extra spaces."""
    s = name.strip().lower()
    s = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    # Collapse whitespace
    return re.sub(r"\s+", " ", s)


def _get(url: str) -> requests.Response | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        # Force UTF-8 so emoji (e.g. ⭐ = E2 AD 90) isn't mis-decoded as ISO-8859-1
        r.encoding = "utf-8"
        return r
    except Exception:
        return None


def _normalize_probs(raw: dict[str, float]) -> dict[str, float]:
    """
    Remove bookmaker overround: divide each raw probability by the market total.
    e.g. raw = {"dorian godon": 0.182, "wout van aert": 0.154, ...}
    If total < 1.0 (gap-filled market), just return raw.
    """
    total = sum(raw.values())
    if total <= 0:
        return {}
    if total <= 1.0:
        return raw
    return {k: v / total for k, v in raw.items()}


def _stars_to_probs(star_dict: dict[str, int], max_stars: int = 5) -> dict[str, float]:
    """
    Convert star ratings to relative win probability.
    Stars → raw weight: 5→25, 4→12, 3→5, 2→2, 1→0.5
    Normalize so highest-ranked rider gets meaningful signal.
    """
    WEIGHTS = {5: 25.0, 4: 12.0, 3: 5.0, 2: 2.0, 1: 0.5}
    raw: dict[str, float] = {}
    for name, stars in star_dict.items():
        w = WEIGHTS.get(min(stars, 5), 0.5)
        raw[name] = w
    total = sum(raw.values()) or 1.0
    return {k: v / total for k, v in raw.items()}


# ── Source 1: Spilxperten ───────────────────────────────────────────────────────

def scrape_spilxperten(cartridge: str, stage_num: int) -> dict[str, float]:
    """
    Fetch stage N winner odds from spilxperten.com.
    Returns {rider_name_lower: win_probability (overround-removed)}.
    """
    slug = SPILXPERTEN_SLUG.get(cartridge)
    if not slug:
        return {}

    url  = f"https://www.spilxperten.com/cykling/{slug}/"
    resp = _get(url)
    if not resp:
        print(f"    [spilxperten] HTTP error fetching {url}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find the heading for this stage's market:
    # "Odds på ... {stage_num}. etape"
    target_heading = None
    for tag in soup.find_all(["h2", "h3", "h4", "strong", "b"]):
        text = tag.get_text(" ", strip=True)
        if re.search(rf"\b{stage_num}\. etape\b", text, re.IGNORECASE):
            target_heading = tag
            break

    if not target_heading:
        print(f"    [spilxperten] Ingen heading fundet for etape {stage_num}")
        return {}

    # Walk forward from heading to find rider/odds rows
    raw: dict[str, float] = {}
    node = target_heading.parent or target_heading

    # Junk keywords that indicate disclaimer/footer text — skip these rows
    JUNK_KEYWORDS = re.compile(
        r"reklame|bookmaker|opdateret|bemærk|cookies|ansvarligt|gambling|"
        r"18\+|vinder udbyder|odds kan|alle links",
        re.IGNORECASE,
    )

    # The structure is: heading → table rows with (rider, bookmaker, odds)
    for sibling in node.find_all_next(["tr", "li", "div", "p"]):
        text = sibling.get_text(" ", strip=True)

        # Skip obviously long/garbage rows
        if len(text) > 120 or JUNK_KEYWORDS.search(text):
            continue

        # Stop if we hit the next stage's market heading
        if re.search(r"\b\d+\. etape\b", text, re.IGNORECASE):
            heading_tags = sibling.find(["h2", "h3", "h4", "strong", "b"])
            if heading_tags:
                break

        # Look for rows with exactly ONE decimal odds number
        numbers = re.findall(r"\b(\d+\.\d+)\b", text)
        if len(numbers) != 1:
            continue

        odds_val = float(numbers[0])
        if odds_val < 1.01 or odds_val > 500:
            continue

        # Remove the odds value and bookmaker keywords to isolate rider name
        name_text = text.replace(numbers[0], "")
        name_text = re.sub(
            r"\b(bet365|unibet|betsson|betfair|william\s*hill|888sport|"
            r"betsafe|nordicbet|bet-at-home|pinnacle|1xbet)\b",
            "", name_text, flags=re.IGNORECASE,
        )
        name_text = re.sub(r"\s+", " ", name_text).strip()

        # Name must be 3–50 chars and look like a person's name (2–5 words)
        if not name_text or len(name_text) < 4 or len(name_text) > 50:
            continue

        words = name_text.split()
        name_words = [w for w in words if re.match(r"[A-Za-zÀ-ÿ'\-]{2,}", w)]
        if len(name_words) < 2 or len(name_words) > 5:
            continue

        rider_name = _norm(" ".join(name_words))
        raw[rider_name] = 1.0 / odds_val

    if not raw:
        print(f"    [spilxperten] Ingen odds parset for etape {stage_num}")
        return {}

    result = _normalize_probs(raw)
    print(f"    [spilxperten] {len(result)} ryttere med odds til etape {stage_num} "
          f"(top: {max(result, key=result.get)!r} {max(result.values()):.1%})")
    return result


# ── Source 2: TV2 Axelgaard ────────────────────────────────────────────────────

def scrape_tv2_axelgaard(cartridge: str, stage_num: int) -> dict[str, float]:
    """
    Fetch Axelgaard's optakt from TV2 Sport.
    URL pattern: sport.tv2.dk/cykling/YYYY-MM-DD-axelgaards-optakt-til-N-etape-af-{race}
    The article is published 0–2 days before the stage — try multiple dates.
    Returns {rider_name_lower: win_probability}.
    """
    race_slug = TV2_RACE_SLUG.get(cartridge)
    if not race_slug:
        return {}

    today = date.today()
    resp  = None
    tried = []

    # Try: today, yesterday, day before yesterday, 3 days ago
    for delta in range(0, 4):
        d   = today - timedelta(days=delta)
        url = (
            f"https://sport.tv2.dk/cykling/"
            f"{d.isoformat()}-axelgaards-optakt-til-{stage_num}"
            f"-etape-af-{race_slug}"
        )
        tried.append(url)
        r = _get(url)
        time.sleep(DELAY)
        if r:
            resp = r
            print(f"    [tv2] Fandt artikel: {url}")
            break

    if not resp:
        print(f"    [tv2] Ingen artikel fundet for etape {stage_num} (prøvede {len(tried)} datoer)")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    star_dict: dict[str, int] = {}

    # Count ⭐ (U+2B50) or ★ (U+2605) — ignore variation selectors (U+FE0F)
    STAR_CODEPOINTS = frozenset([0x2B50, 0x2605, 0x2606, 0x2B51, 0x1F31F])

    def _count_stars(s: str) -> int:
        return sum(1 for c in s if ord(c) in STAR_CODEPOINTS)

    def _strip_stars(s: str) -> str:
        return "".join(
            c for c in s if ord(c) not in STAR_CODEPOINTS and ord(c) != 0xFE0F
        ).strip()

    # Scan all text lines — TV2 uses lines like "⭐⭐⭐⭐⭐ Rider Name"
    full_text = soup.get_text("\n", strip=True)
    lines = full_text.split("\n")
    for line in lines:
        line = line.strip()
        stars = _count_stars(line)
        if stars == 0 or stars > 7:
            continue

        name_part = _strip_stars(line)
        name_part = re.sub(r"\(.*?\)", "", name_part).strip()
        name_part = re.sub(r"\s+", " ", name_part).strip()

        if not name_part or len(name_part) < 4:
            continue

        # Split on commas — allows multi-rider lines like "Van Aert, Fisher-Black"
        for raw_name in re.split(r"[,/]", name_part):
            raw_name = raw_name.strip(" .:–-")
            if len(raw_name) < 4 or len(raw_name) > 60:
                continue
            norm = _norm(raw_name)
            words = norm.split()
            if len(words) < 2 or len(words) > 5:
                continue
            if all(re.match(r"[a-z\-']{2,}", w) for w in words):
                star_dict[norm] = max(star_dict.get(norm, 0), stars)

    if not star_dict:
        print(f"    [tv2] Ingen stjerne-ratings fundet i artiklen")
        return {}

    result = _stars_to_probs(star_dict)
    print(f"    [tv2] {len(result)} ryttere med stjerne-ratings til etape {stage_num} "
          f"(top: {max(result, key=result.get)!r} {max(result.values()):.1%})")
    return result


# ── Source 3: IDLProCycling ─────────────────────────────────────────────────────

def scrape_idl(cartridge: str, stage_num: int) -> dict[str, float]:
    """
    Scrape tier favourites from idlprocycling.com stage preview.
    Uses Google-search approach since IDL URLs aren't predictable.
    Returns {rider_name_lower: win_probability}.
    """
    race_slug = IDL_RACE_SLUG.get(cartridge)
    if not race_slug:
        return {}

    # IDL URL pattern: /cycling/{race-slug}-stage-{N}-preview-...
    # Try a search-style fetch via Google to find the article URL
    search_url = (
        f"https://www.idlprocycling.com/cycling/"
        f"{race_slug}-stage-{stage_num}-preview"
    )
    resp = _get(search_url)
    time.sleep(DELAY)

    # That won't work (slug is too vague) — try via Google
    if not resp or resp.status_code != 200:
        search_url = (
            f"https://www.google.com/search?q=site:idlprocycling.com+"
            f"{race_slug}+stage+{stage_num}+preview"
        )
        resp = _get(search_url)
        time.sleep(DELAY)
        if not resp:
            return {}

        # Extract IDL URL from search results
        soup = BeautifulSoup(resp.text, "html.parser")
        idl_url = None
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "idlprocycling.com/cycling/" in href and f"stage-{stage_num}" in href:
                # Clean up Google redirect
                m = re.search(r"https?://www\.idlprocycling\.com[^\s&\"]+", href)
                if m:
                    idl_url = m.group(0)
                    break

        if not idl_url:
            print(f"    [idl] Ingen artikel-URL fundet for etape {stage_num}")
            return {}

        resp = _get(idl_url)
        time.sleep(DELAY)
        if not resp:
            return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text("\n", strip=True)

    # IDL uses tier keywords: "Top favorites:", "Outsiders:", "Long shots:"
    TIER_MAP = {
        "top": 4,       # map to star equivalent
        "favorite": 4,
        "favourit": 4,
        "outsider": 2,
        "long shot": 1,
        "longshot": 1,
        "dark horse": 1,
    }

    star_dict: dict[str, int] = {}
    current_tier = 2    # default tier

    lines = text.split("\n")
    for line in lines:
        line_l = line.strip().lower()

        # Detect tier heading
        for kw, tier in TIER_MAP.items():
            if kw in line_l and len(line_l) < 60:
                current_tier = tier
                break

        # Extract names — lines with team in parentheses
        if "(" in line and ")" in line and len(line.strip()) > 10:
            # Pattern: "Name (Team)"
            name_part = re.sub(r"\(.*?\)", "", line).strip()
            name_part = re.sub(r"[-–•*#\d\.]+", "", name_part).strip()
            if len(name_part) > 4:
                norm = _norm(name_part)
                words = norm.split()
                if len(words) >= 2 and all(re.match(r"[a-z\-']{2,}", w) for w in words):
                    star_dict[norm] = max(star_dict.get(norm, 0), current_tier)

    if not star_dict:
        return {}

    result = _stars_to_probs(star_dict)
    print(f"    [idl] {len(result)} ryttere med tier-ratings til etape {stage_num}")
    return result


# ── Main entry point ───────────────────────────────────────────────────────────

def get_stage_predictions(
    cartridge: str,
    stage_num: int,
    verbose: bool = True,
) -> tuple[dict[str, float], str]:
    """
    Try all sources in priority order and return win probabilities + source name.

    Returns:
        (odds_dict, source_name)
        odds_dict: {rider_name_lower: win_probability} — empty if all fail
        source_name: e.g. "spilxperten.com", "TV2 Axelgaard", "IDLProCycling", or ""
        Keys are lowercase full names with spaces (matching predictor.py's name_lower).
    """
    if verbose:
        print(f"  [predictions] Henter forudsigelser for etape {stage_num} ({cartridge})…")

    # 1. Spilxperten (best — actual decimal odds)
    result = scrape_spilxperten(cartridge, stage_num)
    if result:
        print(f"    OK Kilde: spilxperten.com ({len(result)} ryttere)")
        return result, "spilxperten.com"
    time.sleep(DELAY)

    # 2. TV2 Axelgaard (star ratings)
    result = scrape_tv2_axelgaard(cartridge, stage_num)
    if result:
        print(f"    OK Kilde: TV2 Axelgaard ({len(result)} ryttere)")
        return result, "TV2 Axelgaard"
    time.sleep(DELAY)

    # 3. IDLProCycling (tier ratings)
    result = scrape_idl(cartridge, stage_num)
    if result:
        print(f"    OK Kilde: IDLProCycling ({len(result)} ryttere)")
        return result, "IDLProCycling"

    print(f"    [!] Ingen forudsigelsesdata fundet til etape {stage_num}")
    return {}, ""
