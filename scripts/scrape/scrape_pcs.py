"""
ProCyclingStats form scraper — recent results for all riders in riders.json.

Computes a form score (0-100) based on results from the last 90 days using:
  - Position-based points (1st=100, 2nd=70, 3rd=50, ...) per result
  - PCS points (encodes position x race importance) as supplementary signal
  - Exponential recency decay: results 30 days ago carry ~50% weight

Output: data/cache/pcs_form.json
  {
    "rider_id": {
      "name": "...",
      "pcs_url": "...",
      "form_score": 42.7,      # 0-100
      "n_results": 8,          # number of results used
      "last_result_date": "31.05",
      "results": [             # raw recent results for debugging
        {"date": "31.05", "result": "104", "race": "...", "pcs_pts": 0},
        ...
      ]
    }
  }

Usage:
    python scrape_pcs.py              # scrape all riders
    python scrape_pcs.py --reset      # clear cache and re-scrape all
    python scrape_pcs.py --test       # test 5 riders only

URL construction:
    rider_id uses underscores: "jonas_vingegaard"
    PCS URL uses hyphens:      "jonas-vingegaard"
    Accented names are pre-stripped in riders.json IDs already.
    Fallback: try lastname-only or search if direct URL 404s.
"""
from __future__ import annotations
import argparse
import json
import math
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.parent.parent
DATA      = ROOT / "data"
CACHE_DIR = DATA / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_PATH = CACHE_DIR / "pcs_form.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

DELAY          = 0.8   # seconds between requests
FORM_DAYS      = 180   # look back this many days (was 90 — extended for better form baseline)
TODAY          = date.today()                   # reference date
CURRENT_SEASON = TODAY.year                     # used to interpret "DD.MM" dates without year

STAGE_TYPES_CACHE_PATH   = CACHE_DIR / "pcs_stage_types.json"
PROFILE_SCORES_CACHE_PATH = CACHE_DIR / "pcs_profile_scores.json"


def _load_profile_scores_cache() -> dict:
    if PROFILE_SCORES_CACHE_PATH.exists():
        return json.loads(PROFILE_SCORES_CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_profile_scores_cache(cache: dict) -> None:
    PROFILE_SCORES_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _parse_stage_page(html: str) -> tuple[int | None, int | None, int | None]:
    """
    Extract ProfileScore, Vertical meters, and finish altitude from a PCS stage page.

    Returns (profile_score, vert_meters, finish_alt).

    NOTE: PCS renders label/value pairs as separate <li> elements, so adjacent
    text matching via regex is unreliable.  We use BeautifulSoup.get_text() to
    get a flat line-by-line representation and match label lines followed by
    value lines.

    Finish altitude: PCS shows "Finish:" or "Departure:" on the race-info block,
    followed by a value like "1640 m" or just "1640".  We also try
    "finish altitude:", "arrival:", and "arrivée:" as fallbacks.
    """
    soup  = BeautifulSoup(html, "html.parser")
    lines = soup.get_text("\n", strip=True).split("\n")

    profile_score: int | None = None
    vert_meters:   int | None = None
    finish_alt:    int | None = None

    _ALT_LABELS = {"finish:", "finish altitude:", "arrival:", "arrivée:", "arrivo:"}

    for i, line in enumerate(lines):
        low = line.strip().lower()
        if low == "profilescore:" and i + 1 < len(lines):
            m = re.match(r"(\d+)", lines[i + 1].strip())
            if m:
                profile_score = int(m.group(1))
        elif low == "vertical meters:" and i + 1 < len(lines):
            m = re.match(r"(\d+)", lines[i + 1].strip())
            if m:
                vert_meters = int(m.group(1))
        elif finish_alt is None and low in _ALT_LABELS and i + 1 < len(lines):
            # Value may be "1640 m", "1640m", or just "1640"
            m = re.match(r"(\d+)", lines[i + 1].strip())
            if m:
                finish_alt = int(m.group(1))

    return profile_score, vert_meters, finish_alt


# Keep backward-compat alias used by callers outside this module
def _parse_profile_and_vmeters(html: str) -> tuple[int | None, int | None]:
    ps, vm, _ = _parse_stage_page(html)
    return ps, vm


def fetch_race_profile_scores(
    race_base: str,
    stage_nums: list[int],
    session: requests.Session,
    disk_cache: dict,
) -> dict[int, int]:
    """
    Fetch PCS ProfileScore (+ vertical meters + finish altitude) for each
    stage from individual stage pages.  The overview page does NOT include
    these stats — only individual stage pages do.

    Returns {stage_num: profile_score}.

    Side-effect caching (all keyed under race_base):
      "{race_base}_scores"      → {stage_num: profile_score}
      "{race_base}_vmeters"     → {stage_num: vertical_meters}
      "{race_base}_finish_alt"  → {stage_num: finish_altitude_m}

    Profile score guide (procyclingstats.com/info/profile-score-explained):
      0–40:   flat / sprint stage
      40–100: slightly rolling
      100–200: clearly hilly
      200–350: mountain stage (flat/downhill finish)
      350+:   high mountain / summit finish
    """
    cache_key  = race_base + "_scores"
    vkey       = race_base + "_vmeters"
    falt_key   = race_base + "_finish_alt"
    if cache_key in disk_cache:
        return {int(k): v for k, v in disk_cache[cache_key].items()}

    scores:      dict[int, int] = {}
    vmeters:     dict[int, int] = {}
    finish_alts: dict[int, int] = {}
    print(f"  Henter profile scores for {len(stage_nums)} etaper "
          f"({race_base})…", flush=True)

    for sn in sorted(stage_nums):
        url = f"https://www.procyclingstats.com/race/{race_base}/stage-{sn}"
        try:
            r = session.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                ps, vm, fa = _parse_stage_page(r.text)
                if ps is not None:
                    scores[sn] = ps
                if vm is not None:
                    vmeters[sn] = vm
                if fa is not None:
                    finish_alts[sn] = fa
        except requests.RequestException:
            pass
        time.sleep(0.35)

    disk_cache[cache_key] = scores
    disk_cache[vkey]      = vmeters
    disk_cache[falt_key]  = finish_alts
    return scores

# PCS profile class → our stage_type
#
# p1 = flat (score ~0-40)         → sprint
# p2 = rolling / slightly hilly   → sprint
#      (p2 stages end in bunch sprints; classifying as "hilly" gave sprinters
#       too low sprint form)
# p3 = clearly hilly (score ~100-200) → hilly  (puncheur / short climbs)
# p4 = mountain WITHOUT summit finish → hilly
#      (high-category climbs but the finish is in a valley or on a descent;
#       breakaway specialists and puncheurs win these, not pure climbers.
#       Giro 2026 examples: stage 2 Narváez score=108, stage 4 Narváez score=89)
# p5 = mountain WITH summit finish    → mountain
#      (race ends at the top of a categorised climb; GC riders dominate)
# TT override: stage name contains "ITT" or "TT" → "tt" regardless of profile
PCS_PROFILE_TO_TYPE: dict[str, str] = {
    "1": "sprint",
    "2": "sprint",
    "3": "hilly",
    "4": "hilly",    # was "mountain" — p4 has flat/downhill finish, not a summit
    "5": "mountain",
}

ALL_STAGE_TYPES = ["sprint", "mountain", "hilly", "tt", "ttt", "cobbled"]

# ---------------------------------------------------------------------------
# Position-based score (for a single result)
# ---------------------------------------------------------------------------
POSITION_SCORE = {
    1: 140, 2: 70, 3: 50, 4: 38, 5: 28,
    6: 22,  7: 18, 8: 15, 9: 13, 10: 11,
}

def _pos_score(pos: int) -> float:
    if pos <= 10:
        return POSITION_SCORE.get(pos, 11)
    if pos <= 20:
        return max(0.0, 11 - (pos - 10) * 0.5)   # 10.5, 10.0 ... 6.5
    if pos <= 50:
        return max(0.0, 5 - (pos - 20) * 0.1)    # 5.0 ... 2.0
    return 0.0


# ---------------------------------------------------------------------------
# Recency weight: half-life ~60 days (short-term "current form" signal)
# Tuned for pre-GT preparation races (Romandie, Dauphiné) which happen
# 4-6 weeks before a Grand Tour and are the most relevant form signal.
# Previous 42-day half-life cut Pogacar's weight by ~50% if he skipped a
# GT (last results from Romandie, ~50 days ago), unfairly ranking him below
# riders with many fresh results from a race he didn't enter. 60-day half-
# life retains ~57% weight at 50 days (vs. ~30% with 42-day) and still
# decays fast enough to reflect genuine form differences over a season.
# ---------------------------------------------------------------------------
def _recency_weight(days_ago: int) -> float:
    return math.exp(-days_ago / 60.0)


# ---------------------------------------------------------------------------
# Long-term recency weight: half-life ~125 days (~4 months) for the
# multi-season "underlying ability" signal — slow enough that a strong
# result from last season still counts meaningfully, but a result from
# 2+ years ago is mostly faded out.
# ---------------------------------------------------------------------------
LONG_FORM_DAYS = 540   # ~18 months lookback

def _recency_weight_long(days_ago: int) -> float:
    return math.exp(-days_ago / 125.0)


# ---------------------------------------------------------------------------
# Accent stripping (for URL matching)
# ---------------------------------------------------------------------------

# Scandinavian and other characters that don't decompose via NFD but have
# standard ASCII/Latin equivalents used in URLs.
_CHAR_MAP = str.maketrans({
    "ø": "o", "Ø": "o",
    "æ": "ae", "Æ": "ae",
    "å": "a", "Å": "a",
    "ð": "d", "þ": "th",
    "ß": "ss",
})


def _strip_accents(s: str) -> str:
    # First apply manual char map (handles ø, æ, å etc.)
    s = s.translate(_CHAR_MAP)
    # Then strip combining diacritics (handles é, ñ, ú etc.)
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _to_pcs_slug(full_name: str) -> str:
    """Convert 'Jhonatan Narváez' -> 'jhonatan-narvaez'."""
    stripped = _strip_accents(full_name.lower())
    # Replace apostrophes/curly quotes with hyphen (e.g. O'Connor → o-connor)
    cleaned  = re.sub(r"[''`]", "-", stripped)
    cleaned  = re.sub(r"[^a-z0-9\- ]", "", cleaned)
    cleaned  = re.sub(r"-+", "-", cleaned)   # collapse multiple hyphens
    return re.sub(r"\s+", "-", cleaned.strip())


# ---------------------------------------------------------------------------
# Manual overrides: rider_id → PCS slug
# (used when auto-generated slug fails)
# ---------------------------------------------------------------------------
PCS_SLUG_OVERRIDES: dict[str, str] = {
    # Riders needing extra surname particle for PCS URL
    # (auto-slug from full_name would drop the second surname)
    "igor_arrieta":            "igor-arrieta-lizarraga",
    "santiago_buitrago":       "santiago-buitrago-sanchez",
    "michael_valgren":         "michael-valgren-andersen",
    # Scandinavian names: ø is mapped to 'o' by _to_pcs_slug but PCS uses 'oe'
    "rasmus_søjberg_pedersen": "rasmus-soejberg-pedersen",
}


def _rider_to_pcs_slug(rider: dict) -> str:
    """Use manual override if available, else derive from full_name."""
    if rider["id"] in PCS_SLUG_OVERRIDES:
        return PCS_SLUG_OVERRIDES[rider["id"]]
    return _to_pcs_slug(rider["full_name"])


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------

_SPEC_LABELS = {"climber", "sprint", "tt", "hills", "onedayraces", "gc"}


def _parse_int(s: str) -> int | None:
    """Parse integer allowing European thousands separators (6.142 or 6,142 → 6142)."""
    clean = s.replace(",", "").replace(".", "")
    return int(clean) if clean.isdigit() else None


def _parse_specialties(html: str) -> dict[str, int]:
    """
    Extract PCS specialty points from a rider page.
    Returns e.g. {"climber": 6142, "sprint": 273, "tt": 525, "hills": 2240,
                  "onedayraces": 1836, "gc": 584}

    Handles both orderings found on PCS:
      "6142 climber"   (number-first)
      "climber 6142"   (label-first)
    and European thousands separators: "6.142" or "6,142".
    """
    soup = BeautifulSoup(html, "html.parser")
    for ul in soup.find_all("ul"):
        tokens = ul.get_text(" ", strip=True).split()
        found: dict[str, int] = {}
        i = 0
        while i < len(tokens) - 1:
            # number-first: "6142 climber" or "6.142 climber"
            n = _parse_int(tokens[i])
            if n is not None and tokens[i + 1].lower() in _SPEC_LABELS:
                found[tokens[i + 1].lower()] = n
                i += 2
            # label-first: "climber 6142" or "Climber 6.142"
            elif tokens[i].lower() in _SPEC_LABELS and i + 1 < len(tokens):
                n2 = _parse_int(tokens[i + 1])
                if n2 is not None:
                    found[tokens[i].lower()] = n2
                    i += 2
                else:
                    i += 1
            else:
                i += 1
        if len(found) >= 3:   # require at least 3 labels to avoid false matches
            return found
    return {}


def _parse_results_table(html: str, rider_name: str) -> list[dict]:
    """
    Parse the rdrResults table in a PCS rider page.

    Returns list of dicts:
      {date, result, race, pcs_pts, stage_href, stage_num, race_base}
    stage_href: e.g. "race/giro-d-italia/2026/stage-7"
    race_base:  e.g. "giro-d-italia/2026"  (for stage-type lookup)
    stage_num:  int or None
    """
    soup  = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="rdrResults")
    if not table:
        return []

    rows        = table.find("tbody").find_all("tr")
    results     = []
    cur_race    = ""
    cur_race_base = ""  # e.g. "giro-d-italia/2026"

    _stage_num_re  = re.compile(r"/stage-(\d+)$")
    _race_base_re  = re.compile(r"^race/([\w-]+/\d{4})/")

    for row in rows:
        row_class = row.get("class", [])

        if "main" in row_class:
            cells = row.find_all("td")
            if len(cells) >= 5:
                race_td = cells[4]
                cur_race = race_td.get_text(" ", strip=True)
                # Extract race base URL from any link in the row
                a_tag = race_td.find("a", href=True)
                if a_tag:
                    m = _race_base_re.search(a_tag["href"])
                    cur_race_base = m.group(1) if m else ""
            continue

        if "stage" not in row_class:
            continue

        cells = row.find_all("td")
        if len(cells) < 9:
            continue

        date_str = cells[0].get_text(strip=True)
        result   = cells[1].get_text(strip=True)
        race_td  = cells[4]
        pcs_pts  = cells[7].get_text(strip=True)

        race_text = race_td.get_text(" ", strip=True)

        # Skip if no date or non-numeric result
        if not date_str:
            continue
        if not re.match(r"^\d+$", result):
            continue
        if "classification" in race_text.lower():
            continue

        try:
            pcs_pts_int = int(pcs_pts) if pcs_pts and pcs_pts.isdigit() else 0
        except ValueError:
            pcs_pts_int = 0

        # Extract stage href + number
        a_tag     = race_td.find("a", href=True)
        stage_href = a_tag["href"] if a_tag else ""
        sn_match  = _stage_num_re.search(stage_href)
        stage_num = int(sn_match.group(1)) if sn_match else None

        results.append({
            "date":       date_str,
            "result":     int(result),
            "race":       race_text or cur_race,
            "pcs_pts":    pcs_pts_int,
            "stage_href": stage_href,
            "stage_num":  stage_num,
            "race_base":  cur_race_base,
            "stage_type": None,   # filled in later by annotate_stage_types()
        })

    return results


def _parse_date(date_str: str) -> date | None:
    """Parse 'DD.MM' or 'DD.MM.YYYY' to date object."""
    try:
        if re.match(r"^\d{1,2}\.\d{1,2}$", date_str):
            day, month = date_str.split(".")
            return date(CURRENT_SEASON, int(month), int(day))
        elif re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}$", date_str):
            day, month, year = date_str.split(".")
            return date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        pass
    return None


# ---------------------------------------------------------------------------
# Race stage-type lookup (fetched once per race, cached on disk)
# ---------------------------------------------------------------------------

def _load_stage_types_cache() -> dict:
    if STAGE_TYPES_CACHE_PATH.exists():
        return json.loads(STAGE_TYPES_CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_stage_types_cache(cache: dict) -> None:
    STAGE_TYPES_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def fetch_race_stage_types(
    race_base: str,              # e.g. "giro-d-italia/2026"
    session: requests.Session,
    disk_cache: dict,
) -> dict[int, str]:
    """
    Fetch stage profiles for a race and return {stage_num: stage_type}.

    Also writes enriched metadata (p_class, stage_type) into
    disk_cache[race_base + "_meta"] for inspection / future use.

    Results are cached in disk_cache[race_base] to avoid re-fetching.
    """
    if race_base in disk_cache:
        return disk_cache[race_base]

    url = f"https://www.procyclingstats.com/race/{race_base}/stages"
    try:
        r = session.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            disk_cache[race_base] = {}
            return {}
    except requests.RequestException:
        disk_cache[race_base] = {}
        return {}

    soup          = BeautifulSoup(r.text, "html.parser")
    stage_map:  dict[int, str]  = {}
    stage_meta: dict[int, dict] = {}
    stage_href_re = re.compile(r"/stage-(\d+)$")
    profile_re    = re.compile(r"\bp([1-9])\b")

    for row in soup.find_all("tr"):
        a = row.find("a", href=stage_href_re)
        if not a:
            continue
        sn         = int(stage_href_re.search(a["href"]).group(1))
        stage_name = a.get_text(" ", strip=True)

        # Detect PCS profile class (p1–p5) from span CSS class
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
            else:
                stype = PCS_PROFILE_TO_TYPE.get(p, "hilly")
        else:
            p     = "?"
            stype = "hilly"   # safe fallback

        stage_map[sn]  = stype
        stage_meta[sn] = {"p_class": p, "stage_type": stype, "name": stage_name}

    disk_cache[race_base]            = stage_map
    disk_cache[race_base + "_meta"]  = stage_meta   # richer data for inspection
    time.sleep(0.4)
    return stage_map


def annotate_stage_types(
    results: list[dict],
    session: requests.Session,
    disk_cache: dict,
) -> list[dict]:
    """
    Fill in result["stage_type"] for each result that has a race_base + stage_num.
    Fetches race stage pages as needed (cached).
    """
    # Gather unique races needed
    races_needed = {r["race_base"] for r in results
                    if r.get("race_base") and r.get("stage_num") is not None
                    and r["race_base"] not in disk_cache}
    for rb in races_needed:
        fetch_race_stage_types(rb, session, disk_cache)

    for r in results:
        rb = r.get("race_base", "")
        sn = r.get("stage_num")
        if rb and sn is not None and rb in disk_cache:
            sub = disk_cache[rb]
            # disk_cache loaded fresh this run has int keys; disk_cache
            # round-tripped through JSON (_load_stage_types_cache) has
            # string keys — JSON object keys are always strings. Try both
            # so a cache hit from THIS run and a cache hit loaded from disk
            # both resolve correctly (a string-only lookup here used to
            # silently return None for every already-cached race).
            r["stage_type"] = sub.get(sn, sub.get(str(sn)))

    return results


# ---------------------------------------------------------------------------
# Form score calculation
# ---------------------------------------------------------------------------

def compute_form_score(
    results: list[dict],
    stage_type_filter: str | None = None,
    lookback_days: int = FORM_DAYS,
    recency_fn=_recency_weight,
) -> float:
    """
    Compute form score 0-100 from parsed results.

    If stage_type_filter is given (e.g. "sprint"), only results whose
    stage_type matches are used.  This lets a sprint specialist score
    high on sprint stages even if they finish last on mountain stages.

    lookback_days/recency_fn let callers compute a slower-decaying,
    longer-window variant (see compute_all_form_scores_long) without
    duplicating this function.

    Strategy: average of top-5 recency-weighted position scores.
    """
    if not results:
        return 0.0

    cutoff = TODAY - timedelta(days=lookback_days)
    weighted_scores: list[float] = []

    for r in results:
        # Stage type filter
        if stage_type_filter is not None:
            if r.get("stage_type") != stage_type_filter:
                continue

        d = _parse_date(r["date"])
        if d is None or d < cutoff:
            continue

        days_ago = (TODAY - d).days
        w        = recency_fn(days_ago)

        pos_s = _pos_score(r["result"])
        pcs_s = math.log1p(r["pcs_pts"]) * 2.4 if r["pcs_pts"] > 0 else 0.0

        weighted_scores.append(w * (pos_s + pcs_s))

    if not weighted_scores:
        return 0.0

    top5 = sorted(weighted_scores, reverse=True)[:5]
    return min(100.0, round(sum(top5) / len(top5), 1))


def compute_all_form_scores(results: list[dict]) -> dict[str, float]:
    """
    Compute overall + per-stage-type SHORT-TERM form scores (current form,
    29-day half-life, 180-day lookback).
    Returns {"overall": x, "sprint": x, "mountain": x, "hilly": x, "tt": x, "cobbled": x}.
    """
    scores = {"overall": compute_form_score(results, stage_type_filter=None)}
    for stype in ALL_STAGE_TYPES:
        scores[stype] = compute_form_score(results, stage_type_filter=stype)
    return scores


def compute_all_form_scores_long(results: list[dict]) -> dict[str, float]:
    """
    Compute overall + per-stage-type LONG-TERM form scores — multi-season
    "underlying ability" signal (125-day half-life, 540-day/~18mo lookback).

    Distinct from compute_all_form_scores: this answers "how good is this
    rider at this discipline over the last season-and-a-half", as opposed
    to "are they hot right now". A rider who's had a quiet last 2 months
    but a strong climbing record over the past year should still show up
    as a credible climbing threat — the short-term signal alone would
    miss that.
    """
    scores = {"overall": compute_form_score(
        results, stage_type_filter=None,
        lookback_days=LONG_FORM_DAYS, recency_fn=_recency_weight_long,
    )}
    for stype in ALL_STAGE_TYPES:
        scores[stype] = compute_form_score(
            results, stage_type_filter=stype,
            lookback_days=LONG_FORM_DAYS, recency_fn=_recency_weight_long,
        )
    return scores


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _fetch_rider_page(slug: str, session: requests.Session) -> tuple[str | None, str]:
    """
    Try to fetch PCS rider page. Returns (html, url_used).

    Fallback chain:
      1. Full slug as-is
      2. Drop last hyphen-word (handles "de-la-cruz-melgarejo" → "de-la-cruz")
      3. Drop last two words
    """
    base = "https://www.procyclingstats.com/rider/"

    # Build candidate slugs
    parts      = slug.split("-")
    candidates = [slug]
    if len(parts) > 2:
        candidates.append("-".join(parts[:-1]))   # drop last word
    if len(parts) > 3:
        candidates.append("-".join(parts[:-2]))   # drop last two words

    for candidate in candidates:
        url = base + candidate
        try:
            r = session.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200 and "rdrResults" in r.text:
                return r.text, url
        except requests.RequestException:
            pass
        time.sleep(0.2)

    return None, ""


def _fetch_prev_season_html(rider_url: str, session: requests.Session) -> str | None:
    """
    Fetch a rider's PREVIOUS season results page for long-term form.

    PCS rider pages take the season as a path segment:
        procyclingstats.com/rider/{slug}/{year}
    The default (year-less) page only shows the CURRENT season — last
    year's results aren't in there, so this is a separate fetch.
    """
    prev_year = TODAY.year - 1
    url = rider_url.rstrip("/") + f"/{prev_year}"
    try:
        r = session.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200 and "rdrResults" in r.text:
            return r.text
    except requests.RequestException:
        pass
    return None


# ---------------------------------------------------------------------------
# Main scraping loop
# ---------------------------------------------------------------------------

def scrape_all(
    riders: list[dict],
    reset: bool = False,
    test: bool = False,
    with_history: bool = True,
) -> dict:
    # Load existing caches
    if CACHE_PATH.exists() and not reset:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    else:
        cache = {}

    stage_types_disk = _load_stage_types_cache()

    todo = [r for r in riders if r["id"] not in cache]
    if test:
        todo = todo[:5]

    n_cached  = len(riders) - len([r for r in riders if r["id"] not in cache])
    n_total   = len(todo)
    n_ok      = 0
    n_missing = 0

    print(f"  Skal hentes: {n_total} ryttere ({n_cached} allerede i cache)")
    print(f"  Estimeret tid: ~{n_total * (DELAY + 0.3):.0f}s\n")

    session = requests.Session()

    for i, rider in enumerate(todo, 1):
        slug = _rider_to_pcs_slug(rider)
        html, url_used = _fetch_rider_page(slug, session)

        if html:
            results      = _parse_results_table(html, rider["full_name"])
            specialties  = _parse_specialties(html)
            # Annotate each result with stage_type (fetches race pages as needed)
            results = annotate_stage_types(results, session, stage_types_disk)

            cutoff = TODAY - timedelta(days=FORM_DAYS)
            recent = [
                r for r in results
                if (d := _parse_date(r["date"])) is not None and d >= cutoff
            ]

            form_scores = compute_all_form_scores(results)
            last_date   = recent[0]["date"] if recent else ""

            # ── Long-term form: fetch last season too, blend in for the
            # multi-season "underlying ability" signal (see compute_all_
            # form_scores_long). A quiet current season shouldn't erase a
            # rider's known climbing/sprint pedigree from the year before.
            form_scores_long: dict[str, float] = {}
            if with_history:
                prev_html = _fetch_prev_season_html(url_used, session)
                if prev_html:
                    prev_results = _parse_results_table(prev_html, rider["full_name"])
                    prev_results = annotate_stage_types(prev_results, session, stage_types_disk)
                    combined = results + prev_results
                    form_scores_long = compute_all_form_scores_long(combined)
                time.sleep(0.3)

            cache[rider["id"]] = {
                "name":             rider["full_name"],
                "pcs_url":          url_used,
                "form_score":       form_scores["overall"],   # backward compat key
                "form_by_type":     form_scores,              # sprint/mountain/hilly/tt/cobbled
                "form_long_by_type": form_scores_long,         # multi-season, slow decay
                "pcs_specialties":  specialties,              # {"climber":882, "sprint":273, ...}
                "n_results":        len(recent),
                "last_result_date": last_date,
                "results":          recent[:15],
            }
            n_ok += 1
        else:
            cache[rider["id"]] = {
                "name":         rider["full_name"],
                "pcs_url":      "",
                "form_score":   10.0,
                "form_by_type": {"overall": 10.0, **{t: 0.0 for t in ALL_STAGE_TYPES}},
                "form_long_by_type": {},
                "n_results":    0,
                "last_result_date": "",
                "results":      [],
                "not_found":    True,
            }
            n_missing += 1

        if i % 20 == 0 or i == n_total:
            print(f"  {i:>4}/{n_total}  ok={n_ok}  niet_gevonden={n_missing}"
                  f"  senest: {rider['full_name']}")
            CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
            _save_stage_types_cache(stage_types_disk)

        time.sleep(DELAY)

    # Final save
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    _save_stage_types_cache(stage_types_disk)
    return cache


# ---------------------------------------------------------------------------
# PCS startlist — DNS detection
# ---------------------------------------------------------------------------

def fetch_pcs_startlist(pcs_race: str, session: requests.Session | None = None) -> list[str]:
    """
    Fetch the startlist from PCS and return a list of rider slugs.
    pcs_race: e.g. "tour-auvergne-rhone-alpes/2026"
    Returns list of PCS slugs like ["dorian-godon", "wout-van-aert", ...]
    """
    url = f"https://www.procyclingstats.com/race/{pcs_race}/startlist"
    sess = session or requests.Session()
    try:
        r = sess.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        slugs: list[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("rider/"):
                slug = href.split("/")[1].split("?")[0]
                if slug and slug not in slugs:
                    slugs.append(slug)
        return slugs
    except Exception:
        return []


def check_dns(
    riders: list[dict],
    pcs_race: str,
    session: requests.Session | None = None,
) -> list[str]:
    """
    Compare our rider list against the PCS startlist.
    Returns list of rider IDs that appear to be DNS (not on PCS startlist).
    Only flags riders where we HAVE a known PCS slug (to avoid false positives).
    """
    startlist_slugs = set(fetch_pcs_startlist(pcs_race, session))
    if not startlist_slugs:
        return []   # couldn't fetch — don't flag anyone

    # Load existing cache to get known PCS URLs → slugs
    cache: dict = {}
    if CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))

    dns: list[str] = []
    for rider in riders:
        entry = cache.get(rider["id"], {})
        pcs_url = entry.get("pcs_url", "")
        if not pcs_url:
            continue   # no known PCS URL → can't check
        slug = pcs_url.rstrip("/").split("/")[-1]
        if slug and slug not in startlist_slugs:
            dns.append(rider["id"])

    return dns


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PCS form scraper")
    parser.add_argument("--reset",          action="store_true",
                        help="Ryd cache og hent alt")
    parser.add_argument("--test",           action="store_true",
                        help="Test kun 5 ryttere")
    parser.add_argument("--race",           default="tour-de-france/2026",
                        help="PCS race base path til profile-score-hentning "
                             "(default: tour-de-france/2026)")
    parser.add_argument("--profile-scores", action="store_true",
                        help="Hent ProfileScore per etape fra PCS (gemmes i "
                             "data/cache/pcs_profile_scores.json)")
    parser.add_argument("--riders-file",    default=None,
                        help="Alternativ JSON-fil med rytterliste (f.eks. "
                             "data/cache/dauphine2026_players.json). "
                             "Henter kun ryttere der mangler i cachen.")
    parser.add_argument("--no-history",     action="store_true",
                        help="Spring multi-saeson langtids-form over (kun "
                             "denne saesons data, hurtigere men mister "
                             "langsigtet form-signal)")
    args = parser.parse_args()

    print("-" * 60)
    print("  PCS Form Scraper")
    print("-" * 60)

    if args.riders_file:
        # Load alternate riders list (e.g. Dauphiné players)
        alt_path = Path(args.riders_file)
        alt_data = json.loads(alt_path.read_text(encoding="utf-8"))
        # Support both list-of-dicts (dauphine players) and plain list
        if isinstance(alt_data, list):
            riders = [{"id": r["id"], "full_name": r["full_name"]} for r in alt_data
                      if r.get("id") and r.get("full_name")]
        else:
            riders = [{"id": k, "full_name": v.get("holdet_name", k)}
                      for k, v in alt_data.items()]
        print(f"  Ryttere fra {alt_path.name}: {len(riders)}")
        # Only scrape riders missing from cache
        existing_cache = {}
        if CACHE_PATH.exists():
            existing_cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        riders = [r for r in riders if r["id"] not in existing_cache]
        print(f"  Mangler i cache: {len(riders)} ryttere")
    else:
        riders = json.loads((DATA / "riders.json").read_text(encoding="utf-8"))
        print(f"  Ryttere i riders.json: {len(riders)}")

    cache = scrape_all(riders, reset=args.reset, test=args.test,
                       with_history=not args.no_history)

    # ── Profile scores (optional) ──────────────────────────────────────────────
    if args.profile_scores:
        print(f"\n  Henter profile scores for {args.race}…")
        session      = requests.Session()
        ps_cache     = _load_profile_scores_cache()
        stage_types  = _load_stage_types_cache().get(args.race, {})

        # Fetch stage types first if not cached
        if not stage_types:
            st_disk = _load_stage_types_cache()
            stage_types = fetch_race_stage_types(args.race, session, st_disk)
            _save_stage_types_cache(st_disk)

        # Always re-fetch live — clear old cache entries so fetch_race_profile_scores
        # doesn't short-circuit and return stale data
        ps_cache.pop(args.race + "_scores",  None)
        ps_cache.pop(args.race + "_vmeters", None)

        stage_nums   = sorted(int(k) for k in stage_types.keys())
        scores       = fetch_race_profile_scores(args.race, stage_nums, session, ps_cache)
        _save_profile_scores_cache(ps_cache)
        print(f"  Profile scores gemt: {len(scores)} etaper")
        for sn in sorted(scores.keys()):
            print(f"    Stage {sn:>2}: {scores[sn]:>4}  ({stage_types.get(sn, '?')})")

    # --- Summary ---
    found     = [v for v in cache.values() if not v.get("not_found")]
    not_found = [v for v in cache.values() if v.get("not_found")]

    print(f"\n  Faerdig! {len(found)} ryttere med PCS-data, {len(not_found)} ikke fundet")
    print(f"  Gemt til {CACHE_PATH}")

    # Spot-check
    check_ids = ["jonas_vingegaard", "jonathan_milan", "remco_evenepoel",
                 "tadej_pogacar", "wout_van_aert"]
    print("\n  Spot-check:")
    for rid in check_ids:
        if rid in cache:
            d = cache[rid]
            print(f"    {d['name']:<30}  form={d['form_score']:>5.1f}  "
                  f"resultater={d['n_results']}  senest={d['last_result_date']}")


if __name__ == "__main__":
    main()
