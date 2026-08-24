"""
Lokal hjælper til at hente CyclingOracle-ratings for de 28 ryttere der
mangler i cachen (bl.a. Finn Fisher-Black, Mario Aparicio, Eddie Dunbar).

HVORFOR denne fil findes: GitHub Actions' IP bliver i øjeblikket afvist med
HTTP 403 af CyclingOracles sitemap-endpoint (formentlig en rate-limit/WAF-
regel udløst efter en kode-ændring der midlertidigt hentede flere sider ad
gangen). Dit hjemme-netværk rammer højst sandsynligt IKKE den samme
blokering. Denne fil er selvstændig (ingen andre projekt-filer nødvendige)
så du kan køre den direkte på din egen computer.

BRUG:
  1. Installér de to nødvendige pakker (kun disse — ikke hele projektet):
       pip install requests beautifulsoup4 lxml
  2. Kør scriptet:
       python co_local_helper.py
  3. Når det er færdigt, skriver det en fil "co_local_result.json" i samme
     mappe. Send den fil (eller indholdet) tilbage i chatten — så merger og
     committer jeg resultatet ind i data/cache/cyclingoracle.json.

Tager ca. 30-60 sekunder (28 ryttere × ~0,8-1,2 sek pr. opslag).
"""
from __future__ import annotations
import json
import re
import time
import unicodedata

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# De 28 ryttere der mangler CO-ratings pr. 2026-08-24 (fra vuelta2026 startlisten).
MISSING_RIDERS = [
    {"id": "lennard_kamna", "full_name": "Lennard Kämna"},
    {"id": "finn_fisher-black", "full_name": "Finn Fisher-Black"},
    {"id": "jasha_sutterlin", "full_name": "Jasha Sütterlin"},
    {"id": "embret_svestad_bårdseng", "full_name": "Embret Svestad-Bårdseng"},
    {"id": "jose_luis_faura", "full_name": "Jose Luis Faura"},
    {"id": "sergio_geovani_chumil", "full_name": "Sergio Geovani Chumil"},
    {"id": "sinuhe_fernandez", "full_name": "Sinuhe Fernandez"},
    {"id": "mario_aparicio", "full_name": "Mario Aparicio"},
    {"id": "cesar_macias", "full_name": "César Macías"},
    {"id": "eddie_dunbar", "full_name": "Eddie Dunbar"},
    {"id": "pau_marti", "full_name": "Pau Marti"},
    {"id": "hamish_mckenzie", "full_name": "Hamish McKenzie"},
    {"id": "oscar_chamberlain", "full_name": "Oscar Chamberlain"},
    {"id": "steffen_de_schuyteneer", "full_name": "Steffen De Schuyteneer"},
    {"id": "alessandro_romele", "full_name": "Alessandro Romele"},
    {"id": "sente_sentjens", "full_name": "Sente Sentjens"},
    {"id": "floris_van_tricht", "full_name": "Floris Van Tricht"},
    {"id": "clement_alleno", "full_name": "Clément Alleno"},
    {"id": "martin_tjøtta", "full_name": "Martin Tjøtta"},
    {"id": "lindsay_de_vylder", "full_name": "Lindsay De Vylder"},
    {"id": "asbjørn_hellemose", "full_name": "Asbjørn Hellemose"},
    {"id": "jakob_omrzel", "full_name": "Jakob Omrzel"},
    {"id": "juan_felipe_rodriguez", "full_name": "Juan Felipe Rodriguez"},
    {"id": "oliver_peace", "full_name": "Oliver Peace"},
    {"id": "henri-francois_renard-haquin", "full_name": "Henri-Francois Renard-Haquin"},
    {"id": "mattia_gaffuri", "full_name": "Mattia Gaffuri"},
    {"id": "moritz_kretschy", "full_name": "Moritz Kretschy"},
    {"id": "simon_dalby", "full_name": "Simon Dalby"},
]

# --- Navnematch (samme logik som scripts/scrape/scrape_co.py) ---------------

_SPECIAL = {
    ord('ø'): 'o', ord('Ø'): 'o',
    ord('æ'): 'ae', ord('Æ'): 'ae',
    ord('ß'): 'ss',
    ord('ð'): 'd', ord('Ð'): 'd',
    ord('þ'): 'th', ord('Þ'): 'th',
}
_SPECIAL_ALT = {**_SPECIAL, ord('æ'): 'a', ord('Æ'): 'a'}
FIRST_NAME_ALIASES = {
    "thomas": "tom", "tom": "thomas",
    "mathieu": "mat",
    "alexander": "alex", "alex": "alexander",
    "eddie": "edward", "edward": "eddie",
}
RATING_KEYS = ["AVG", "SPR", "FLT", "COB", "HLL", "MTN", "GC", "ITT", "PR"]
RATING_PAT = re.compile(r"(AVG|SPR|FLT|COB|HLL|MTN|GC|ITT|PR)\s+(\d+)", re.IGNORECASE)


def _normalize(s, alt=False):
    table = _SPECIAL_ALT if alt else _SPECIAL
    s = s.translate(table)
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _slug(url):
    slug = url.rstrip("/").split("/")[-1]
    return re.sub(r"-\d+$", "", slug)


def _slug_to_words(slug):
    return [_normalize(w) for w in slug.split("-")]


def _name_variants(full_name):
    norm = _normalize(full_name)
    variants = []
    va = re.sub(r"['’`\-]", " ", norm).split()
    variants.append(sorted(va))
    vb = re.sub(r"['’`]", "", norm)
    vb = re.sub(r"-", " ", vb).split()
    if sorted(vb) != sorted(va):
        variants.append(sorted(vb))
    if va and va[0] in FIRST_NAME_ALIASES:
        variants.append(sorted([FIRST_NAME_ALIASES[va[0]]] + va[1:]))
    norm_alt = _normalize(full_name, alt=True)
    vd = re.sub(r"['’`\-]", " ", norm_alt).split()
    if sorted(vd) not in variants:
        variants.append(sorted(vd))
    return variants


def fetch_all_co_urls(max_pages=8):
    urls = []
    for page in range(1, max_pages + 1):
        sitemap = (
            f"https://www.cyclingoracle.com/nl/"
            f"sitemaps-1-section-riders-1-sitemap-p{page}.xml"
        )
        try:
            r = requests.get(sitemap, headers=HEADERS, timeout=30)
            soup = BeautifulSoup(r.text, "xml")
            locs = [l.text for l in soup.find_all("loc") if "/renners/" in l.text]
            print(f"  Sitemap p{page}: HTTP {r.status_code}, {len(locs)} rytter-URLs")
            urls.extend(locs)
        except Exception as e:
            print(f"  Sitemap p{page} fejl: {e}")
        time.sleep(0.4)
    return urls


def match_riders(co_urls, riders):
    slug_map = {}
    for url in co_urls:
        slug = _slug(url)
        key = " ".join(sorted(_slug_to_words(slug)))
        slug_map[key] = url

    matched, unmatched = {}, []
    for rider in riders:
        rid, name = rider["id"], rider["full_name"]
        found = False
        for wlist in _name_variants(name):
            key = " ".join(wlist)
            if key in slug_map:
                matched[rid] = slug_map[key]
                found = True
                break
        if found:
            continue
        words = re.sub(r"['’`\-]", " ", _normalize(name)).split()
        best = None
        for url in co_urls:
            sw = _slug_to_words(_slug(url))
            if all(w in sw for w in words):
                best = url
                break
        if best:
            matched[rid] = best
            continue

        if len(words) > 2:
            short_key = " ".join(sorted([words[0], words[-1]]))
            if short_key in slug_map:
                matched[rid] = slug_map[short_key]
                continue
            dropped = False
            for drop_i in range(1, len(words) - 1):
                reduced = sorted(w for i, w in enumerate(words) if i != drop_i)
                if " ".join(reduced) in slug_map:
                    matched[rid] = slug_map[" ".join(reduced)]
                    dropped = True
                    break
            if dropped:
                continue
            abl_key = " ".join(sorted(words[:-1]))
            if abl_key in slug_map:
                matched[rid] = slug_map[abl_key]
                continue

        last = _normalize(name.split()[-1])
        candidates = [u for u in co_urls if last in _slug(u)]
        if len(candidates) == 1:
            matched[rid] = candidates[0]
            continue

        unmatched.append(name)
    return matched, unmatched


def _parse_ratings(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    ratings = {}
    for m in RATING_PAT.finditer(text):
        key = m.group(1).upper()
        if key in RATING_KEYS:
            ratings[key] = float(m.group(2))
    if not ratings:
        for tag in soup.find_all(True, {"data-value": True}):
            label = tag.get("data-label", "").upper()
            if label in RATING_KEYS:
                try:
                    ratings[label] = float(tag["data-value"])
                except (ValueError, KeyError):
                    pass
    return ratings


def scrape_rider(url):
    candidates = [url]
    if "/nl/renners/" in url:
        candidates.append(url.replace("/nl/renners/", "/en/riders/"))
    elif "/en/riders/" in url:
        candidates.append(url.replace("/en/riders/", "/nl/renners/"))
    for attempt_url in candidates:
        try:
            r = requests.get(attempt_url, headers=HEADERS, timeout=15)
        except Exception as e:
            print(f"    [fejl] {attempt_url}: {e}")
            time.sleep(0.4)
            continue
        if r.status_code != 200:
            print(f"    [fejl] {attempt_url}: HTTP {r.status_code}")
            time.sleep(0.4)
            continue
        ratings = _parse_ratings(r.text)
        if ratings:
            return ratings
        time.sleep(0.4)
    return None


def main():
    print("=" * 60)
    print("  CyclingOracle — lokal hjælper (kør fra din egen computer)")
    print("=" * 60)
    print(f"\nHenter CyclingOracle sitemap URLs...")
    co_urls = fetch_all_co_urls()
    print(f"Total CO rider URLs: {len(co_urls)}")
    if not co_urls:
        print("\n[STOP] Ingen sitemap-URLs hentet — enten er CyclingOracle nede,")
        print("       eller dit netværk rammer samme blokering som GitHub Actions.")
        print("       Prøv evt. igen om et par timer.")
        return

    print(f"\nMatcher {len(MISSING_RIDERS)} ryttere mod CO URLs...")
    matched, unmatched = match_riders(co_urls, MISSING_RIDERS)
    print(f"  Matchede:    {len(matched)}")
    print(f"  Ikke matchede: {len(unmatched)}")
    if unmatched:
        for name in unmatched:
            print(f"    - {name} (ingen CO-profil fundet — helt normalt for nogle)")

    print(f"\nHenter ratings for {len(matched)} matchede ryttere...")
    name_by_id = {r["id"]: r["full_name"] for r in MISSING_RIDERS}
    result = {}
    for i, (rid, url) in enumerate(matched.items(), 1):
        print(f"  [{i}/{len(matched)}] {name_by_id[rid]}...", end=" ")
        ratings = scrape_rider(url)
        if ratings:
            result[rid] = {"name": name_by_id[rid], "url": url, "ratings": ratings}
            print(f"OK ({len(ratings)} værdier)")
        else:
            print("ingen ratings fundet")
        time.sleep(0.8)

    out_path = "co_local_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 60}")
    print(f"  Færdig! {len(result)}/{len(MISSING_RIDERS)} ryttere hentet.")
    print(f"  Gemt til: {out_path}")
    print(f"  Send filen (eller indholdet) tilbage i chatten, så committer")
    print(f"  jeg resultatet ind i data/cache/cyclingoracle.json.")
    print("=" * 60)


if __name__ == "__main__":
    main()
