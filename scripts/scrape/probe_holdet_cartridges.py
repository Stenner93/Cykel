#!/usr/bin/env python3
"""
Engangs-probe, runde 4: bekræftet at https://www.holdet.dk + "/api/season/"-
præfiks virker for games/cartridges/schedules (se PR #162's kørsel). Denne
runde tjekker om det SAMME præfiks-mønster gælder de øvrige Holdet-endpoints
scrape_holdet.py / snapshot_holdet_teams.py bruger:
  - fantasyteams (holdopslag — kritisk for Holdduel)
  - fantasyteams/rounds/{n}/lineup (rytterudvalg pr. runde)
  - games/{id}/events/{eid}/fantasy-actions (DNF-detektion m.m.)
  - games/{id}/events/{eid}/scoring-summary
  - fantasyleagues/{id}/... (liga-standings)
  - games/{id}/rounds

Kør via .github/workflows/probe-holdet-cartridges.yml (workflow_dispatch) —
output læses i job-loggen, og både denne fil og workflow'en slettes bagefter.
"""
import requests

BASE = "https://www.holdet.dk"
MY_TEAM_ID = 7271757     # Anders (data/vuelta_teams.json)
OTHER_TEAM_ID = 7272262  # Kasper (data/vuelta_teams.json)
GAME_ID = 628             # vuelta-2026 (bekræftet stadig korrekt)

PATH_PAIRS = [
    # (uden /season/, med /season/)
    (f"/api/fantasyteams/{MY_TEAM_ID}",                     f"/api/season/fantasyteams/{MY_TEAM_ID}"),
    (f"/api/fantasyteams/{MY_TEAM_ID}/rounds/1/lineup",     f"/api/season/fantasyteams/{MY_TEAM_ID}/rounds/1/lineup"),
    (f"/api/games/{GAME_ID}/rounds",                        f"/api/season/games/{GAME_ID}/rounds"),
    (f"/api/games/{GAME_ID}/events/1/fantasy-actions",      f"/api/season/games/{GAME_ID}/events/1/fantasy-actions"),
    (f"/api/games/{GAME_ID}/events/1/scoring-summary",      f"/api/season/games/{GAME_ID}/events/1/scoring-summary"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CykelManagerBot/1.0)"}


def check(path: str) -> str:
    url = BASE + path
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        ctype = resp.headers.get("Content-Type", "")
        size = len(resp.content)
        is_json = "json" in ctype
        tag = "JSON" if is_json else "html/other"
        snippet = ""
        if is_json:
            snippet = f"  body: {resp.text[:160]}"
        return f"  {resp.status_code}  {tag:11s}  {size:>7} bytes  {path}{snippet}"
    except requests.exceptions.RequestException as exc:
        return f"  ERR   {path}  {exc.__class__.__name__}: {exc}"


print("=" * 60)
print("  Holdet endpoint-mønster probe (fantasyteams/actions/rounds)")
print("=" * 60)

for old_path, new_path in PATH_PAIRS:
    print()
    print(check(old_path))
    print(check(new_path))
