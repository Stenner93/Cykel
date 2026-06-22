"""
Scrape PCS 12-month individual ranking (top 300) for quality signal.

Saves {pcs_slug: {"rank": N, "pts": M, "name": "..."}} to
data/cache/pcs_rankings.json.

Pogacar ~rank 1-3, Vingegaard ~rank 2-5, Fred Wright ~rank 100.
This gives a quality baseline independent of recent form.

Usage:
    python scrape_pcs_rankings.py
"""
from __future__ import annotations
import json
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT      = Path(__file__).parent
CACHE_DIR = ROOT / "data" / "cache"
OUT_PATH  = CACHE_DIR / "pcs_rankings.json"

# 100 riders per page, 3 pages → top 300
BASE_URL = "https://www.procyclingstats.com/rankings/me/individual"
OFFSETS  = [0, 100, 200]
DELAY    = 1.5  # seconds between requests


def _fetch_page(session: requests.Session, offset: int) -> str:
    url    = f"{BASE_URL}?offset={offset}"
    resp   = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def _parse_page(html: str) -> list[dict]:
    """
    Parse one ranking page.

    Table columns (typical):  rank | ↑↓ | rider name | nationality | team | points
    The rider <td> contains an <a href="rider/{slug}">Name</a> link.
    We extract rank, pcs_slug, name, and pts.
    """
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []

    # Find the ranking table — look for a table that has rider links
    table = None
    for t in soup.find_all("table"):
        if t.find("a", href=lambda h: h and h.startswith("rider/")):
            table = t
            break

    if table is None:
        print("  [WARN] Ingen rangeringstabel fundet på siden")
        return entries

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue

        # Find rider link in any cell
        rider_link = None
        for cell in cells:
            link = cell.find("a", href=lambda h: h and h.startswith("rider/"))
            if link:
                rider_link = link
                break

        if rider_link is None:
            continue

        # Extract slug from href, e.g. "rider/tadej-pogacar" → "tadej-pogacar"
        href = rider_link.get("href", "")
        slug = href.split("rider/")[-1].strip("/")
        name = rider_link.get_text(strip=True)

        if not slug or not name:
            continue

        # Extract rank from first non-empty numeric cell
        rank = None
        for cell in cells:
            txt = cell.get_text(strip=True)
            if txt.isdigit():
                rank = int(txt)
                break

        if rank is None:
            continue

        # Extract points: last numeric-looking cell (may contain commas/dots)
        pts = 0.0
        for cell in reversed(cells):
            txt = cell.get_text(strip=True).replace(",", "").replace(".", "")
            if txt.isdigit():
                try:
                    pts = float(cell.get_text(strip=True).replace(",", ""))
                except ValueError:
                    pass
                break

        entries.append({"rank": rank, "name": name, "slug": slug, "pts": pts})

    return entries


def scrape_rankings() -> dict[str, dict]:
    """
    Scrape PCS 12-month individual ranking (top 300).
    Returns {pcs_slug: {"rank": N, "pts": M, "name": "..."}}.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (compatible; CyckelFetcher/1.0; +https://github.com)"
    )

    result: dict[str, dict] = {}

    for i, offset in enumerate(OFFSETS):
        print(f"  Henter PCS rangering side {i+1}/{len(OFFSETS)} (offset={offset})...")
        try:
            html    = _fetch_page(session, offset)
            entries = _parse_page(html)
            print(f"    → {len(entries)} ryttere fundet")
            for e in entries:
                result[e["slug"]] = {
                    "rank": e["rank"],
                    "pts":  e["pts"],
                    "name": e["name"],
                }
        except Exception as exc:
            print(f"  [WARN] Side {i+1} fejlede: {exc}")

        if i < len(OFFSETS) - 1:
            time.sleep(DELAY)

    return result


def main() -> None:
    print("PCS 12-måneders rangering — henter top 300...")
    rankings = scrape_rankings()

    if not rankings:
        print("  [WARN] Ingen data hentet — pcs_rankings.json opdateres ikke")
        return

    OUT_PATH.write_text(
        json.dumps(rankings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  ✓ Gemt {len(rankings)} ryttere til {OUT_PATH}")

    # Show top 10
    top10 = sorted(rankings.items(), key=lambda x: x[1]["rank"])[:10]
    print("  Top 10:")
    for slug, info in top10:
        print(f"    #{info['rank']:>3}  {info['name']:<30}  {info['pts']:>8.0f} pts  ({slug})")


if __name__ == "__main__":
    main()
