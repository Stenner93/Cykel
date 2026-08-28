#!/usr/bin/env python3
"""One-off diagnostic probe: hunt for an authoritative Holdet "Værdi"/rank
source (team value incl. real transfer-fee accounting) to replace the
current-price-based fee ESTIMATE used in vuelta.html's Holdduel.

Kører KUN i GitHub Actions (CI kan nå holdet.dk; lokale/agent-miljøer
blokeres). Skriver INTET og committer INTET — ren læsning/diagnose.

Prøver en række kandidat-endpoints under nexus-app-fantasy-fargate.holdet.dk
(samme base som resten af scraperen) samt et forsøg på selve holdet.dk's
Next.js server action der driver leaderboard-siden (paginerings-konstanten
NEXT_ACTION, aldrig verificeret før). Printer status + et uddrag af JSON'en
for alt der svarer 200, og fremhæver nøgler der ligner "value"/"rank"/"points".
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape_holdet as h  # noqa: E402

GAME_ID = 628          # vuelta-2026 (fra KNOWN_GAME_IDS)
TEAMS = {"Anders": 7271757, "Kasper": 7272262}


def dump(label, resp):
    print(f"\n--- {label} ---")
    print(f"  status: {resp.status_code}")
    ct = resp.headers.get("Content-Type", "")
    print(f"  content-type: {ct}")
    if resp.status_code != 200 or "json" not in ct:
        print(f"  body[:300]: {resp.text[:300]!r}")
        return None
    try:
        data = resp.json()
    except Exception as exc:
        print(f"  [FEJL] kunne ikke parse JSON: {exc}")
        print(f"  body[:300]: {resp.text[:300]!r}")
        return None
    s = json.dumps(data, ensure_ascii=False)
    print(f"  json[:800]: {s[:800]}")
    interesting = [k for k in _all_keys(data) if any(w in k.lower() for w in ("value", "rank", "point", "budget", "fee"))]
    if interesting:
        print(f"  [SIGNAL] nøgler der ligner value/rank/point/fee: {sorted(set(interesting))}")
    return data


def _all_keys(obj, out=None):
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(k)
            _all_keys(v, out)
    elif isinstance(obj, list):
        for it in obj[:5]:
            _all_keys(it, out)
    return out


def try_get(url):
    try:
        return h.HTTP.get(url)
    except h.requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        print(f"\n--- GET {url} ---\n  HTTP {code} (raise_for_status)")
        return None
    except Exception as exc:
        print(f"\n--- GET {url} ---\n  [EXC] {exc}")
        return None


def main():
    print("=== 1) Team-detail endpoint (uden /rounds/.../lineup) ===")
    for name, tid in TEAMS.items():
        r = try_get(f"{h.BASE}/api/fantasyteams/{tid}")
        if r is not None:
            dump(f"fantasyteams/{tid} ({name})", r)

    print("\n=== 2) Leagues for game (find evt. default/global liga-id) ===")
    r = try_get(f"{h.BASE}/api/games/{GAME_ID}/leagues")
    leagues_data = dump(f"games/{GAME_ID}/leagues", r) if r is not None else None

    league_ids = []
    if isinstance(leagues_data, dict):
        items = leagues_data.get("items", leagues_data)
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and isinstance(it.get("id"), int):
                    league_ids.append(it["id"])
    print(f"  fundne liga-id'er: {league_ids[:10]}")

    print("\n=== 3) Leaderboard-varianter ===")
    candidates = [
        f"{h.BASE}/api/games/{GAME_ID}/leaderboard",
        f"{h.BASE}/api/games/{GAME_ID}/leaderboards",
        f"{h.BASE}/api/games/{GAME_ID}/rankings",
        f"{h.BASE}/api/games/{GAME_ID}/participants",
        f"{h.BASE}/api/fantasyteams?gameId={GAME_ID}",
    ]
    for lid in league_ids[:3]:
        candidates.append(f"{h.BASE}/api/leagues/{lid}/leaderboard")
        candidates.append(f"{h.BASE}/api/leagues/{lid}")
        candidates.append(f"{h.BASE}/api/games/{GAME_ID}/leagues/{lid}/leaderboard")
    for url in candidates:
        r = try_get(url)
        if r is not None:
            dump(url, r)

    print("\n=== 4) Round-specifik team-snapshot (uden /lineup) ===")
    for name, tid in TEAMS.items():
        r = try_get(f"{h.BASE}/api/fantasyteams/{tid}/rounds/1")
        if r is not None:
            dump(f"fantasyteams/{tid}/rounds/1 ({name})", r)

    print("\n[FÆRDIG] Se ovenfor for [SIGNAL]-linjer — de peger på det bedste kandidat-endpoint.")


if __name__ == "__main__":
    main()
