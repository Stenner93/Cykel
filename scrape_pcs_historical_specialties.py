"""
Fetch PCS specialty scores for historical riders missing from pcs_form.json.

Reads all rider slugs from data/ml/historical_results.json, skips those already
covered by pcs_form.json, and fetches only the specialty block (climber/sprint/tt/
hills/onedayraces/gc) from the PCS rider page.

Output: data/cache/pcs_historical_specialties.json
  { "wout-van-aert": {"climber": 3240, "sprint": 5820, ...}, ... }

Usage:
    python scrape_pcs_historical_specialties.py          # fetch missing
    python scrape_pcs_historical_specialties.py --reset  # clear cache and refetch
    python scrape_pcs_historical_specialties.py --test 10  # test first 10 slugs
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT  = Path(__file__).parent
DATA  = ROOT / "data"
CACHE = DATA / "cache"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

DELAY = 0.8

_SPEC_LABELS = {"climber", "sprint", "tt", "hills", "onedayraces", "gc"}


def _parse_int(s: str) -> int | None:
    s = s.replace(".", "").replace(",", "")
    try:
        return int(s)
    except ValueError:
        return None


def _parse_specialties(html: str) -> dict[str, int]:
    soup = BeautifulSoup(html, "html.parser")
    for ul in soup.find_all("ul"):
        tokens = ul.get_text(" ", strip=True).split()
        found: dict[str, int] = {}
        i = 0
        while i < len(tokens) - 1:
            n = _parse_int(tokens[i])
            if n is not None and tokens[i + 1].lower() in _SPEC_LABELS:
                found[tokens[i + 1].lower()] = n
                i += 2
            elif tokens[i].lower() in _SPEC_LABELS and i + 1 < len(tokens):
                n2 = _parse_int(tokens[i + 1])
                if n2 is not None:
                    found[tokens[i].lower()] = n2
                    i += 2
                else:
                    i += 1
            else:
                i += 1
        if len(found) >= 3:
            return found
    return {}


def _fetch_specialties(slug: str, session: requests.Session) -> dict[str, int]:
    base  = "https://www.procyclingstats.com/rider/"
    parts = slug.split("-")
    candidates = [slug]
    if len(parts) > 2:
        candidates.append("-".join(parts[:-1]))
    if len(parts) > 3:
        candidates.append("-".join(parts[:-2]))

    for candidate in candidates:
        url = base + candidate
        try:
            r = session.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200 and "rdrResults" in r.text:
                return _parse_specialties(r.text)
        except requests.RequestException:
            pass
        time.sleep(0.2)
    return {}


def _slugs_in_pcs_form() -> set[str]:
    path = CACHE / "pcs_form.json"
    if not path.exists():
        return set()
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = set()
    for d in raw.values():
        url = d.get("pcs_url", "")
        if "/rider/" in url:
            out.add(url.split("/rider/")[-1].strip("/"))
    return out


def _slugs_in_historical() -> set[str]:
    path = DATA / "ml" / "historical_results.json"
    if not path.exists():
        print(f"[!] Ikke fundet: {path}", file=sys.stderr)
        return set()
    records = json.loads(path.read_text(encoding="utf-8"))
    return {r["rider_slug"] for r in records if r.get("rider_slug")}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Ryd cache og hent alt igen")
    parser.add_argument("--test",  type=int, default=0, metavar="N", help="Kun N ryttere (test)")
    args = parser.parse_args()

    out_path = CACHE / "pcs_historical_specialties.json"
    cache: dict[str, dict] = {}
    if out_path.exists() and not args.reset:
        cache = json.loads(out_path.read_text(encoding="utf-8"))

    all_slugs   = _slugs_in_historical()
    form_slugs  = _slugs_in_pcs_form()
    already_cached = set(cache.keys())

    # Need: historical slugs not in pcs_form and not yet fetched
    todo = sorted(all_slugs - form_slugs - already_cached)

    if args.test:
        todo = todo[:args.test]

    print(f"Historiske ryttere i alt:      {len(all_slugs)}")
    print(f"  Dækket af pcs_form.json:     {len(form_slugs & all_slugs)}")
    print(f"  Allerede i historical cache: {len(already_cached)}")
    print(f"  Skal hentes nu:              {len(todo)}")
    print(f"  Estimeret tid:               ~{len(todo) * (DELAY + 0.3):.0f}s\n")

    if not todo:
        print("Intet at hente — alt er cached.")
        return

    session = requests.Session()
    n_ok, n_miss = 0, 0

    for i, slug in enumerate(todo, 1):
        spec = _fetch_specialties(slug, session)
        cache[slug] = spec
        if spec:
            n_ok += 1
            top = max(spec, key=spec.get)
            print(f"  [{i}/{len(todo)}] {slug}: {top}={spec[top]}")
        else:
            n_miss += 1
            print(f"  [{i}/{len(todo)}] {slug}: (ingen specialties fundet)")

        out_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(DELAY)

    print(f"\nFærdig. OK: {n_ok}  Manglede: {n_miss}")
    print(f"Gemt: {out_path}  ({len(cache)} slugs i alt)")


if __name__ == "__main__":
    main()
