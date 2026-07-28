#!/usr/bin/env python3
"""
Post-race holdet.dk snapshot — RUN LOCALLY (needs holdet.dk network access).

Preserves everything we need for the manager-comparison analysis BEFORE holdet
takes the game API down after the race:

  1. Global reference   → data/sources/tdf2026/holdet/reference/
       cartridge.json, rounds.json, players.json, schedule.json
  2. Per-stage results  → data/sources/tdf2026/holdet/fantasy_actions/round_XX.json
       (actual per-rider holdet points/rules per stage — the real growth driver)
  3. Per-team lineups   → data/sources/tdf2026/holdet/teams/<teamId>/round_XX.json
       for our two teams + the top-N final managers (raw, parse later)

WHY RAW: the exact lineup value/growth fields are undocumented here, so we dump
the raw JSON verbatim. The analysis step parses it; nothing is lost.

USAGE
    python scripts/scrape/snapshot_holdet_teams.py                # auto top-10
    python scripts/scrape/snapshot_holdet_teams.py --top 25       # wider net
    python scripts/scrape/snapshot_holdet_teams.py \
        --team-ids 111111,222222,333333                           # manual list

If auto-discovery of the leaderboard fails (holdet changes the endpoint), the
script tells you how to read the team IDs off the standings page and pass them
with --team-ids. Our own teams are always included.

Safe to re-run: it skips files already downloaded (use --force to refetch).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

# ── Config (TdF 2026) ─────────────────────────────────────────────────────────
BASE      = "https://nexus-app-fantasy-fargate.holdet.dk"
CARTRIDGE = "tour-de-france-2026"
GAME_ID   = 618

OUR_TEAMS = {
    7145433: "os (Anders)",
    7132927: "Kasper",
}

ROOT = Path(__file__).resolve().parents[2]
OUT  = ROOT / "data/sources/tdf2026/holdet"

# ── HTTP with polite throttling + retry/backoff ───────────────────────────────
class Http:
    def __init__(self, delay: float = 0.8):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "tdf-manager-holdet-snapshot/1.0"
        self.delay = delay
        self._last = 0.0

    def get(self, url: str, tolerate: bool = False):
        for attempt in range(5):
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self.s.get(url, timeout=30)
                if r.status_code == 429:
                    time.sleep(2.0 ** attempt + 1)
                    continue
                if r.status_code >= 400:
                    if tolerate:
                        return None
                    r.raise_for_status()
                return r.json()
            except requests.RequestException as exc:
                if attempt == 4:
                    if tolerate:
                        return None
                    raise
                time.sleep(2.0 ** attempt + 1)
                print(f"    retry {attempt+1} for {url}: {exc}", file=sys.stderr)
        return None


HTTP = Http()


def dump(path: Path, obj, force: bool) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        return False
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


# ── Leaderboard discovery ─────────────────────────────────────────────────────
def _extract_team_ids(payload) -> list[int]:
    """Best-effort: pull participant/team IDs out of an unknown leaderboard shape."""
    ids: list[int] = []
    items = payload.get("items", payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return ids
    for it in items:
        if not isinstance(it, dict):
            continue
        for key in ("fantasyteamId", "teamId", "participantId", "id"):
            v = it.get(key)
            if isinstance(v, int):
                ids.append(v)
                break
        else:
            # nested {"team": {"id": ...}} / {"fantasyteam": {...}}
            for sub in ("team", "fantasyteam", "participant"):
                d = it.get(sub)
                if isinstance(d, dict) and isinstance(d.get("id"), int):
                    ids.append(d["id"])
                    break
    return ids


def discover_top_teams(league_id, n: int) -> list[int]:
    """Try several candidate leaderboard endpoints; return up to n team IDs."""
    candidates = [
        f"{BASE}/api/fantasyleagues/{league_id}/standings?take={n}",
        f"{BASE}/api/fantasyleagues/{league_id}/leaderboard?take={n}",
        f"{BASE}/api/fantasyleagues/{league_id}/teams?take={n}&sort=rank",
        f"{BASE}/api/games/{GAME_ID}/leaderboard?take={n}",
        f"{BASE}/api/games/{GAME_ID}/standings?take={n}",
    ]
    for url in candidates:
        if league_id is None and "fantasyleagues/None" in url:
            continue
        payload = HTTP.get(url, tolerate=True)
        if payload is None:
            continue
        ids = _extract_team_ids(payload)
        if ids:
            print(f"  leaderboard OK via {url}  →  {len(ids)} teams")
            return ids[:n]
    return []


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10, help="how many top managers (default 10)")
    ap.add_argument("--team-ids", default="", help="comma-separated team IDs (manual override)")
    ap.add_argument("--force", action="store_true", help="refetch even if file exists")
    args = ap.parse_args()

    ref = OUT / "reference"

    # 1. Reference data
    print("Henter reference-data …")
    cart = HTTP.get(f"{BASE}/api/cartridges/{CARTRIDGE}")
    league_id = (cart or {}).get("defaultFantasyLeagueId")
    dump(ref / "cartridge.json", cart, args.force)

    rounds = HTTP.get(f"{BASE}/api/games/{GAME_ID}/rounds")
    dump(ref / "rounds.json", rounds, args.force)
    round_nums = sorted(r["number"] for r in (rounds or {}).get("items", [])
                        if isinstance(r.get("number"), int))
    print(f"  runder: {round_nums}")

    players = HTTP.get(f"{BASE}/api/games/{GAME_ID}/players")
    dump(ref / "players.json", players, args.force)

    schedule = HTTP.get(f"{BASE}/api/schedules/{GAME_ID}", tolerate=True)
    if schedule:
        dump(ref / "schedule.json", schedule, args.force)
    # event IDs for per-stage results
    events = []
    if isinstance(schedule, dict):
        for e in schedule.get("events", schedule.get("items", [])):
            eid = e.get("id") if isinstance(e, dict) else None
            if isinstance(eid, int):
                events.append(eid)

    # 2. Per-stage fantasy-actions (actual per-rider points)
    print("Henter fantasy-actions (faktiske etape-point) …")
    for i, eid in enumerate(events, 1):
        payload = HTTP.get(f"{BASE}/api/games/{GAME_ID}/events/{eid}/fantasy-actions",
                           tolerate=True)
        if payload is not None:
            wrote = dump(OUT / "fantasy_actions" / f"event_{eid}.json", payload, args.force)
            print(f"  event {eid} ({i}/{len(events)}) {'gemt' if wrote else 'findes'}")

    # 3. Team list
    if args.team_ids.strip():
        top_ids = [int(x) for x in args.team_ids.split(",") if x.strip()]
        print(f"Bruger manuelle team-IDs: {top_ids}")
    else:
        print(f"Finder top {args.top} hold (league {league_id}) …")
        top_ids = discover_top_teams(league_id, args.top)
        if not top_ids:
            print("\n  [!] Kunne ikke auto-finde leaderboardet (holdet har nok ændret "
                  "endpointet).\n      Gå til slutstillingen på holdet.dk, åbn hvert af "
                  "top-holdene, og aflæs\n      hold-ID'et i URL'en "
                  "(…/fantasyteams/<ID> eller ?team=<ID>). Kør så:\n"
                  "        python scripts/scrape/snapshot_holdet_teams.py "
                  "--team-ids 111,222,333\n      (vores egne hold tages altid med.)\n")

    # our teams always included, first, without duplication
    team_ids: list[int] = list(OUR_TEAMS)
    for t in top_ids:
        if t not in team_ids:
            team_ids.append(t)

    # 4. Per-team round lineups (raw)
    print(f"Henter lineups for {len(team_ids)} hold × {len(round_nums)} runder …")
    manifest = {"game_id": GAME_ID, "cartridge": CARTRIDGE, "league_id": league_id,
                "our_teams": {str(k): v for k, v in OUR_TEAMS.items()},
                "team_ids": team_ids, "rounds": round_nums}
    for t in team_ids:
        label = OUR_TEAMS.get(t, "top-manager")
        got = 0
        for n in round_nums:
            payload = HTTP.get(f"{BASE}/api/fantasyteams/{t}/rounds/{n}/lineup",
                               tolerate=True)
            if payload is not None:
                dump(OUT / "teams" / str(t) / f"round_{n:02d}.json", payload, args.force)
                got += 1
        print(f"  team {t} ({label}): {got}/{len(round_nums)} runder")
    dump(OUT / "manifest.json", manifest, force=True)

    print(f"\nFærdig. Rå data i {OUT.relative_to(ROOT)}/")
    print("Commit + push, så laver Claude manager-sammenligningen (Task 2).")


if __name__ == "__main__":
    main()
