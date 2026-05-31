"""
Data scrapers for:
  - CyclingOracle (discipline ratings per rider)
  - ProcyclingStats (form, race history)
  - Startlist.info (official startlists)
"""
from __future__ import annotations
import json
import re
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TdFManager/1.0)"}


# ---------------------------------------------------------------------------
# CyclingOracle
# ---------------------------------------------------------------------------

def _fetch_co_sitemap_rider_urls(max_pages: int = 3) -> list[str]:
    """Fetch rider URLs from CyclingOracle's sitemap."""
    urls = []
    for page in range(1, max_pages + 1):
        sitemap_url = (
            f"https://www.cyclingoracle.com/nl/sitemaps-1-section-riders-1-sitemap-p{page}.xml"
        )
        try:
            r = requests.get(sitemap_url, headers=HEADERS, timeout=10)
            soup = BeautifulSoup(r.text, "xml")
            for loc in soup.find_all("loc"):
                url = loc.text.strip()
                if "/renners/" in url:
                    urls.append(url)
        except Exception as e:
            print(f"CO sitemap page {page} error: {e}")
        time.sleep(0.5)
    return urls


def scrape_cyclingoracle_rider(url: str) -> dict[str, Any] | None:
    """Scrape discipline ratings for one rider from CyclingOracle."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")

        # Extract name from URL slug
        slug = url.rstrip("/").split("/")[-1]
        # Remove trailing numeric ID: "tadej-pogacar-1013" → "tadej-pogacar"
        name_slug = re.sub(r"-\d+$", "", slug)
        name = " ".join(p.capitalize() for p in name_slug.split("-"))

        ratings: dict[str, float] = {}

        # Parse rating values — they appear as text next to discipline codes
        text = soup.get_text(" ", strip=True)

        patterns = {
            "AVG": r"(?:AVG|Overall)[:\s]+(\d+)",
            "SPR": r"(?:SPR|Sprint)[:\s]+(\d+)",
            "FLT": r"(?:FLT|Flat)[:\s]+(\d+)",
            "COB": r"(?:COB|Cobble)[:\s]+(\d+)",
            "HLL": r"(?:HLL|Hill)[:\s]+(\d+)",
            "MTN": r"(?:MTN|Mountain)[:\s]+(\d+)",
            "GC":  r"(?:GC)[:\s]+(\d+)",
            "ITT": r"(?:ITT|Time trial)[:\s]+(\d+)",
            "PR":  r"(?:PR|Prologue)[:\s]+(\d+)",
        }
        for key, pattern in patterns.items():
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                ratings[key] = float(m.group(1))

        # If no ratings found via text, try to get from structured data attributes
        if not ratings:
            for tag in soup.find_all(True, {"data-value": True}):
                label = tag.get("data-label", "").upper()
                try:
                    val = float(tag["data-value"])
                    if label in ("AVG","SPR","FLT","COB","HLL","MTN","GC","ITT","PR"):
                        ratings[label] = val
                except (ValueError, KeyError):
                    pass

        if not ratings:
            return None

        return {
            "name":    name,
            "url":     url,
            "ratings": ratings,
        }
    except Exception as e:
        print(f"CO rider error {url}: {e}")
        return None


def scrape_cyclingoracle_all(
    rider_names: list[str] | None = None,
    cache_path: Path | None = None,
    delay: float = 0.8,
) -> dict[str, dict]:
    """
    Scrape all riders from CyclingOracle.
    Returns dict: rider_name_lower → {ratings: {SPR: 91, MTN: 32, ...}}
    Results are cached to avoid repeated scraping.
    """
    cp = cache_path or CACHE_DIR / "cyclingoracle.json"
    if cp.exists():
        print(f"Loading CyclingOracle cache from {cp}")
        return json.loads(cp.read_text(encoding="utf-8"))

    print("Fetching CyclingOracle rider URLs from sitemap…")
    urls = _fetch_co_sitemap_rider_urls(max_pages=3)
    print(f"Found {len(urls)} rider URLs")

    results: dict[str, dict] = {}
    for i, url in enumerate(urls):
        data = scrape_cyclingoracle_rider(url)
        if data and data.get("ratings"):
            results[data["name"].lower()] = data
        if i % 50 == 0:
            print(f"  {i}/{len(urls)} scraped…")
        time.sleep(delay)

    cp.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"CyclingOracle: saved {len(results)} riders to {cp}")
    return results


# ---------------------------------------------------------------------------
# ProcyclingStats (PCS) — form & recent results
# ---------------------------------------------------------------------------

def scrape_pcs_rider_form(rider_pcs_id: str) -> dict[str, Any] | None:
    """
    Scrape recent form from PCS.
    Returns a form score 0-100 based on results in the last 90 days.
    """
    url = f"https://www.procyclingstats.com/rider/{rider_pcs_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        recent_results = []
        # PCS shows recent race results in a table
        for row in soup.select("table.results tbody tr")[:20]:
            cols = row.find_all("td")
            if len(cols) >= 4:
                pos_text = cols[0].get_text(strip=True)
                try:
                    pos = int(pos_text)
                    recent_results.append(pos)
                except ValueError:
                    pass

        if not recent_results:
            return {"form": 50.0, "recent_results": []}

        # Form score: inversely weighted by recency and position
        # Top-5 = great, top-20 = decent, lower = poor
        form_pts = []
        for i, pos in enumerate(recent_results[:10]):
            recency_weight = 1.0 - i * 0.08  # more recent = higher weight
            if pos <= 3:
                score = 95
            elif pos <= 5:
                score = 85
            elif pos <= 10:
                score = 70
            elif pos <= 20:
                score = 55
            else:
                score = 30
            form_pts.append(score * recency_weight)

        form = sum(form_pts) / len(form_pts) if form_pts else 50.0
        return {
            "form": round(form, 1),
            "recent_results": recent_results[:10],
        }
    except Exception as e:
        print(f"PCS form error {rider_pcs_id}: {e}")
        return None


def scrape_pcs_startlist(race_url: str) -> list[dict]:
    """
    Scrape startlist from PCS race page.
    race_url example: 'https://www.procyclingstats.com/race/tour-de-france/2026/startlist'
    Returns list of {rider_name, team, pcs_id}
    """
    try:
        r = requests.get(race_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        riders = []
        for a in soup.select("li.team a.blue"):
            href = a.get("href", "")
            pcs_id = href.strip("/").split("/")[-1]
            riders.append({
                "name":   a.get_text(strip=True),
                "pcs_id": pcs_id,
                "url":    f"https://www.procyclingstats.com{href}",
            })
        return riders
    except Exception as e:
        print(f"PCS startlist error {race_url}: {e}")
        return []


# ---------------------------------------------------------------------------
# Odds API
# ---------------------------------------------------------------------------

def fetch_stage_odds(api_key: str, sport: str = "cycling") -> dict[str, float]:
    """
    Fetch win odds from The Odds API (free tier: 500 req/month).
    Returns dict: rider_name_lower → implied_probability

    Sign up free at: https://the-odds-api.com/
    """
    url = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
    try:
        r = requests.get(
            f"https://api.the-odds-api.com/v4/sports/cycling_tour_de_france/odds",
            params={
                "apiKey": api_key,
                "regions": "eu",
                "markets": "h2h",
                "oddsFormat": "decimal",
            },
            timeout=10,
        )
        data = r.json()
        result: dict[str, float] = {}
        for event in data:
            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market["key"] != "h2h":
                        continue
                    for outcome in market.get("outcomes", []):
                        name  = outcome["name"].lower()
                        price = float(outcome["price"])
                        prob  = 1.0 / price
                        # Take the highest probability across bookmakers
                        result[name] = max(result.get(name, 0), prob)
        # Normalise
        total = sum(result.values())
        if total > 0:
            result = {k: v / total for k, v in result.items()}
        return result
    except Exception as e:
        print(f"Odds API error: {e}")
        return {}
