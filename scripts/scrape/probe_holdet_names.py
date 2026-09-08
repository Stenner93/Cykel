#!/usr/bin/env python3
"""
Engangs-probe, runde 5: /api/season/ endpoints (games/cartridges/schedules/
fantasyteams/fantasy-actions) er nu bekræftet at virke (se PR #164). Men
build_vuelta_web_data.py's fulde kørsel viste at "Henter navne fra
statistik-siden" fejler: parse_stats_html() finder ingen "rows" i siden
længere, så alle personId→navn-opslag bliver tomme og hele
vuelta2026_scores.json ender med 0 ryttere.

Formål: finde ud af om statistik-siden stadig indeholder samme Next.js
"rows"-struktur (måske under et andet navn), eller om der findes et
rigtigt JSON-endpoint for rytternavne under /api/season/ i stedet
(langt mere robust end HTML-scraping).

Kør via .github/workflows/probe-holdet-names.yml (workflow_dispatch) —
output læses i job-loggen, og både denne fil og workflow'en slettes bagefter.
"""
import re
import requests

BASE = "https://www.holdet.dk"
CARTRIDGE = "vuelta-2026"
GAME_ID = 628
SAMPLE_PERSON_ID = 3405   # fra et tidligere probe-svar (games/628/players)
SAMPLE_PLAYER_ID = 54427  # samme rytter, playerId fra "items"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CykelManagerBot/1.0)"}


def get(path: str, base: str = BASE):
    url = base + path
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        return resp
    except requests.exceptions.RequestException as exc:
        print(f"  ERR   {path}  {exc.__class__.__name__}: {exc}")
        return None


print("=" * 60)
print("  Statistik-side probe")
print("=" * 60)

resp = get(f"/da/{CARTRIDGE}/cycling/statistics")
if resp is not None:
    html = resp.text
    print(f"  {resp.status_code}  {len(html)} bytes  content-type={resp.headers.get('Content-Type')}")
    has_next_f  = "__next_f" in html
    has_rows    = '"rows"' in html
    has_pid_key = "personId" in html
    has_name    = "fullName" in html
    print(f"  indeholder '__next_f': {has_next_f}")
    print(f"  indeholder rows-nøgle: {has_rows}")
    print(f"  indeholder 'personId': {has_pid_key}")
    print(f"  indeholder 'fullName': {has_name}")
    # dump a chunk around any personId occurrence, if present
    idx = html.find("personId")
    if idx == -1:
        idx = html.find(str(SAMPLE_PERSON_ID))
    if idx != -1:
        print(f"  snippet omkring 'personId': {html[max(0,idx-80):idx+200]!r}")
    else:
        print("  ingen 'personId'/personId-værdi fundet i HTML")

print()
print("=" * 60)
print("  Person/rider JSON-endpoint probe")
print("=" * 60)

candidates = [
    f"/api/season/persons/{SAMPLE_PERSON_ID}",
    f"/api/season/games/{GAME_ID}/persons",
    f"/api/season/games/{GAME_ID}/persons/{SAMPLE_PERSON_ID}",
    f"/api/season/players/{SAMPLE_PLAYER_ID}",
    f"/api/season/games/{GAME_ID}/players/{SAMPLE_PLAYER_ID}",
    f"/api/season/cartridges/{CARTRIDGE}/persons",
    f"/api/season/cartridges/{CARTRIDGE}/players",
]
for path in candidates:
    resp = get(path)
    if resp is None:
        continue
    ctype = resp.headers.get("Content-Type", "")
    size = len(resp.content)
    is_json = "json" in ctype
    tag = "JSON" if is_json else "html/other"
    line = f"  {resp.status_code}  {tag:11s}  {size:>7} bytes  {path}"
    if is_json:
        line += f"\n      body: {resp.text[:250]}"
    print(line)
