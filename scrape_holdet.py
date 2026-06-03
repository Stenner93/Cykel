"""
Holdet.dk scraper for TdF Manager — TdF 2026.

Fetches current player prices, GC standings, and jersey leaders from
Holdet.dk's API and saves them to the tdf-manager cache.

Data pulled:
  1. Player prices + popularity → data/cache/holdet_players.json
  2. GC standings (from latest stage actions) → data/cache/gc_standings.json
  3. Jersey leaders                           → data/cache/jerseys.json
  4. (optional) Update riders.json prices + own_pct in-place

Usage:
    python scrape_holdet.py                   # TdF 2026, full update
    python scrape_holdet.py --discover        # list available cycling cartridges
    python scrape_holdet.py --update-riders   # also patch riders.json prices
    python scrape_holdet.py --cartridge giro-d-italia-2026 --game-id 612
                                              # use Giro IDs (testing / backfill)

When TdF 2026 is live on Holdet, run --discover once to get the correct
cartridge slug + game ID, then save them as DEFAULT_CARTRIDGE / DEFAULT_GAME_ID.

Jersey codes saved match scoring_rules.json:
  "leader"  = yellow / GC leader
  "sprint"  = green / points leader
  "mountain"= polka dot / KOM leader
  "youth"   = white / young rider
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Optional

import requests

# Ensure UTF-8 on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT      = Path(__file__).parent
DATA      = ROOT / "data"
CACHE_DIR = DATA / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── Default config ──────────────────────────────────────────────────────────────
# Update these once TdF 2026 goes live on Holdet.dk.
# Run `python scrape_holdet.py --discover` to find the correct values.
DEFAULT_CARTRIDGE = "tour-de-france-2026"   # update if slug differs
DEFAULT_GAME_ID   = None                    # will be auto-discovered if None
DEFAULT_LEAGUE_ID = None                    # global league, optional

BASE = "https://nexus-app-fantasy-fargate.holdet.dk"

# For leaderboard pagination (Next.js Server Action ID — may change on deploy)
NEXT_ACTION = "7ef01b31de58b5ab9cbb41e2d8a4b09018750b08f2"

# Holdet ruleId → scoring_rules.json jersey key
HOLDET_RULE_TO_JERSEY: dict[int, str] = {
    876: "leader",    # GC leader / gul trøje
    877: "sprint",    # Points leader / grøn trøje
    878: "mountain",  # KOM leader / prikket trøje
    879: "youth",     # Young rider / hvid trøje
}

# Holdet stageType → our stage_type
HOLDET_STAGE_TYPE_MAP: dict[str, str] = {
    "flat":       "sprint",
    "sprint":     "sprint",
    "hilly":      "hilly",
    "cobbled":    "cobbled",
    "mountain":   "mountain",
    "tt":         "tt",
    "individual_time_trial": "tt",
    "team_time_trial":       "tt",
}


# ── Rate-limited HTTP ─────────────────────────────────────────────────────────

class RateLimitedSession:
    MAX_RETRIES  = 4
    RETRY_CODES  = {429, 500, 502, 503, 504}
    BACKOFF_BASE = 2.0

    def __init__(self, delay: float = 1.0):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "tdf-manager-holdet/1.0"
        self.delay   = delay
        self._last   = 0.0

    def _throttle(self):
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        for attempt in range(self.MAX_RETRIES + 1):
            self._throttle()
            try:
                r = self.session.request(method, url, timeout=30, **kw)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.MAX_RETRIES:
                    raise
                wait = self.BACKOFF_BASE ** attempt + random.uniform(0, 1)
                print(f"    [retry {attempt+1}] {exc}  (wait {wait:.1f}s)")
                time.sleep(wait)
                continue
            if r.status_code in self.RETRY_CODES and attempt < self.MAX_RETRIES:
                ra   = r.headers.get("Retry-After")
                wait = float(ra) if ra else self.BACKOFF_BASE ** attempt + random.uniform(0, 1)
                print(f"    [retry {attempt+1}] HTTP {r.status_code}  (wait {wait:.1f}s)")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r
        raise RuntimeError(f"Failed after {self.MAX_RETRIES} retries: {url}")

    def get(self, url: str, **kw) -> requests.Response:
        return self._request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self._request("POST", url, **kw)


HTTP = RateLimitedSession(delay=1.0)


# ── Discovery helpers ──────────────────────────────────────────────────────────

def discover_cartridge(cartridge_slug: str) -> dict:
    """
    Fetch /api/cartridges/{slug} and return gameId + defaultFantasyLeagueId.
    Returns empty dict if 404 or non-JSON response (game not live yet).
    """
    url = f"{BASE}/api/cartridges/{cartridge_slug}"
    try:
        resp = HTTP.session.get(url, timeout=15)  # bypass rate-limiter for discovery
        if resp.status_code in (404, 400, 410):
            return {}
        resp.raise_for_status()
        if not resp.content or "application/json" not in resp.headers.get("Content-Type", ""):
            return {}
        data = resp.json()
        return {
            "game_id":   data.get("gameId"),
            "league_id": data.get("defaultFantasyLeagueId"),
            "name":      data.get("name", cartridge_slug),
        }
    except (requests.HTTPError, requests.JSONDecodeError, ValueError):
        return {}
    except Exception:
        return {}


def list_cycling_cartridges() -> list[dict]:
    """
    Try a range of known/guessed slugs and return those that exist.
    """
    candidates = [
        "tour-de-france-2026",
        "tdf-2026",
        "le-tour-de-france-2026",
        "tour-de-france-2026-manager",
        "giro-d-italia-2026",
        "vuelta-a-espana-2026",
        "criterium-du-dauphine-2026",
        "tour-de-suisse-2026",
    ]
    found = []
    for slug in candidates:
        info = discover_cartridge(slug)
        if info:
            found.append({"slug": slug, **info})
            print(f"  FOUND: {slug!r}  gameId={info['game_id']}  leagueId={info['league_id']}  ({info['name']})")
        else:
            print(f"  miss:  {slug!r}")
    return found


# ── Player data ────────────────────────────────────────────────────────────────

def fetch_players_api(game_id: int) -> dict[int, dict]:
    """Fetch /api/games/{gameId}/players → {playerId: player_dict}."""
    items = HTTP.get(f"{BASE}/api/games/{game_id}/players").json()["items"]
    return {p["id"]: p for p in items}


def parse_stats_html(html: str) -> dict[int, dict]:
    """
    Parse player names + team info from statistics HTML.
    Next.js embeds data in self.__next_f.push([id, "json-string"]) tags.
    Returns {personId: {fullName, teamName, ...}}
    """
    decoder = json.JSONDecoder()
    result: dict[int, dict] = {}

    for m in re.finditer(r"self\.__next_f\.push\(", html):
        pos = m.end()
        try:
            arr, _ = decoder.raw_decode(html, pos)
        except json.JSONDecodeError:
            continue
        if not isinstance(arr, list) or len(arr) < 2 or not isinstance(arr[1], str):
            continue
        inner = arr[1]
        if '"rows"' not in inner:
            continue
        try:
            rows_idx  = inner.index('"rows":[')
            arr_start = inner.index("[", rows_idx)
            rows_list, _ = decoder.raw_decode(inner, arr_start)
        except (ValueError, json.JSONDecodeError):
            continue
        for row in rows_list:
            try:
                person = row.get("person", {})
                team   = row.get("team", {})
                pid    = person.get("id")
                if pid:
                    result[pid] = {
                        "fullName":      person.get("fullName", ""),
                        "teamName":      team.get("name", ""),
                        "teamSlug":      team.get("slug", ""),
                        "playerId":      row.get("id"),
                        "isInjured":     row.get("isInjured", False),
                        "hasSuspension": row.get("hasSuspension", False),
                        "isActive":      row.get("isActive", True),
                    }
            except (AttributeError, TypeError):
                continue
        if result:
            return result
    return result


def fetch_player_info(game_id: int, cartridge: str) -> tuple[dict[int, dict], dict[int, dict]]:
    """
    Returns:
      player_by_id  — {playerId:  {personId, startPrice, price, popularity, ...}}
      person_by_id  — {personId:  {fullName, teamName, ...}}
    """
    print("  Henter spillerliste…")
    player_by_id = fetch_players_api(game_id)
    print(f"    {len(player_by_id)} spillere")

    print("  Henter navne fra statistik-siden…")
    stats_url = f"{BASE}/da/{cartridge}/cycling/statistics"
    html = HTTP.get(stats_url).text
    person_by_id = parse_stats_html(html)
    if not person_by_id:
        print("  [WARNING] Kunne ikke parse statistik-HTML — navne er tomme")
    else:
        print(f"    {len(person_by_id)} navne parset")
    return player_by_id, person_by_id


# ── Schedule + stage data ──────────────────────────────────────────────────────

def fetch_schedule(game_id: int) -> tuple[list[int], dict[int, dict]]:
    """
    Returns:
      events      — ordered list of eventIds (index 0 = stage 1)
      event_info  — {eventId: {stageType, name, status}}
    """
    data = HTTP.get(f"{BASE}/api/schedules/{game_id}").json()
    events = data["events"]
    embedded = data.get("_embedded", {}).get("events", {})
    event_info = {
        int(eid): {
            "stageType": ev.get("stageType", ""),
            "name":      ev.get("name", ""),
            "status":    ev.get("status", ""),
        }
        for eid, ev in embedded.items()
    }
    return events, event_info


def last_completed_event(events: list[int], event_info: dict[int, dict]) -> int | None:
    """Return the eventId of the most recently finished stage."""
    finished = [
        eid for eid in events
        if event_info.get(eid, {}).get("status", "") == "finished"
    ]
    return finished[-1] if finished else None


def detect_next_stage(game_id: int) -> tuple[int | None, str | None]:
    """
    Auto-detect which stage to predict next, based on Holdet's live schedule.

    Logic:
      • Find the last "finished" event → that was the most recent stage.
      • The next entry in the schedule is the upcoming stage to predict.
      • If no stage has finished yet (race not started) → predict stage 1.
      • If all stages are finished (race over) → return (None, None).

    Returns:
      (stage_num, stage_type)  — 1-indexed stage number + our internal type string
      (None, None)             — if the race is over or schedule is empty
    """
    try:
        events, event_info = fetch_schedule(game_id)
    except Exception as exc:
        print(f"  [WARN] detect_next_stage: could not fetch schedule — {exc}")
        return None, None

    if not events:
        return None, None

    # Index of last finished event (or -1 if none finished yet)
    last_finished_idx = -1
    for i, eid in enumerate(events):
        if event_info.get(eid, {}).get("status", "") == "finished":
            last_finished_idx = i

    next_idx = last_finished_idx + 1  # 0 if race hasn't started

    if next_idx >= len(events):
        # All stages done — race is over
        return None, None

    next_eid   = events[next_idx]
    holdet_type = event_info.get(next_eid, {}).get("stageType", "")
    stage_type  = HOLDET_STAGE_TYPE_MAP.get(holdet_type.lower(), "sprint")
    stage_num   = next_idx + 1  # convert 0-based index → 1-based stage number

    print(f"  Auto-detekteret: etape {stage_num}, type '{stage_type}' "
          f"(Holdet stageType='{holdet_type}')")
    return stage_num, stage_type


# ── Stage fantasy-actions parser ──────────────────────────────────────────────

def fetch_fantasy_actions(game_id: int, event_id: int) -> list[dict]:
    """Fetch /api/games/{gameId}/events/{eventId}/fantasy-actions."""
    items = HTTP.get(
        f"{BASE}/api/games/{game_id}/events/{event_id}/fantasy-actions"
    ).json().get("items", [])
    return items


def extract_gc_and_jerseys(
    actions: list[dict],
) -> tuple[dict[int, int], dict[int, list[str]]]:
    """
    From a list of fantasy-action items for one stage, extract:
      gc_by_person     — {personId: gc_rank}  (rule 891, amount = rank)
      jerseys_by_person— {personId: [jersey_keys]}  (rules 876-879)
    """
    gc_by_person:      dict[int, int]       = {}
    jerseys_by_person: dict[int, list[str]] = {}

    for a in actions:
        pid    = a["personId"]
        rule   = a["ruleId"]
        amount = a.get("amount", 1)

        # GC rank for all riders (rule 891: amount = rank)
        if rule == 891:
            gc_by_person[pid] = int(amount)

        # Jersey leaders (rules 876-879: amount = 1)
        if rule in HOLDET_RULE_TO_JERSEY:
            jcode = HOLDET_RULE_TO_JERSEY[rule]
            jerseys_by_person.setdefault(pid, [])
            if jcode not in jerseys_by_person[pid]:
                jerseys_by_person[pid].append(jcode)

    return gc_by_person, jerseys_by_person


# ── Name matching ──────────────────────────────────────────────────────────────

_CHAR_MAP = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae",
    "å": "a", "Å": "a", "ð": "d",  "þ": "th", "ß": "ss",
})

# Manual overrides: normalised Holdet fullName → rider_id in riders.json.
# Needed when Holdet uses full legal name (extra surname particles) that
# differ from the shorter name used in riders.json.
# Format: _norm(holdet_full_name): rider_id
HOLDET_NAME_OVERRIDES: dict[str, str] = {
    # Holdet: "Jhonatan Manuel Narvaez Prado"  → riders.json: jhonatan_narváez
    "jhonatan manuel narvaez prado":    "jhonatan_narváez",
    # Holdet: "Enric Mas Nicolau"              → riders.json: enric_mas
    "enric mas nicolau":                "enric_mas",
    # Holdet: "David De La Cruz"               → riders.json: david_de_la_cruz_melgarejo
    "david de la cruz":                 "david_de_la_cruz_melgarejo",
    # Holdet: "Christopher Juul Jensen"         → riders.json: christopher_juul_jensen
    "christopher juul jensen":          "christopher_juul_jensen",
    # Add TdF-specific overrides here as needed
}


def _norm(name: str) -> str:
    s = name.strip().lower().translate(_CHAR_MAP)
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _last(name: str) -> str:
    parts = _norm(name).split()
    return parts[-1] if parts else ""


def build_rider_lookup(riders: list[dict]) -> dict[str, str]:
    """Return {normalised_full_name: rider_id} with last-name fallback keys."""
    lut: dict[str, str] = {}
    for r in riders:
        rid  = r["id"]
        full = _norm(r["full_name"])
        lut[full] = rid
        # Last-name key (only if not already taken by a full name)
        last = full.split()[-1] if " " in full else full
        if last not in lut:
            lut[last] = rid
    return lut


def match_name(name: str, lut: dict[str, str]) -> str | None:
    """Fuzzy match a Holdet full name to a rider_id."""
    nl = _norm(name)
    # Check manual overrides first (handles extra surname particles etc.)
    if nl in HOLDET_NAME_OVERRIDES:
        return HOLDET_NAME_OVERRIDES[nl]
    if nl in lut:
        return lut[nl]
    last  = _last(name)
    first = nl.split()[0] if " " in nl else ""
    candidates = [(k, v) for k, v in lut.items() if k.split()[-1] == last]
    if len(candidates) == 1:
        return candidates[0][1]
    if candidates and first:
        for k, v in candidates:
            if k.split()[0] == first:
                return v
        for k, v in candidates:
            kw = k.split()
            if kw and kw[0][:1] == first[:1]:
                return v
    return None


# ── Sync to riders.json ────────────────────────────────────────────────────────

def update_riders_json(
    riders: list[dict],
    holdet_map: dict[str, dict],   # rider_id → {price_M, own_pct, holdet_name}
    riders_path: Path,
) -> int:
    """
    Update price and own_pct in riders.json from Holdet data.
    Only overwrites if a match was found.
    Returns count of updated riders.
    """
    updated = 0
    for r in riders:
        rid = r["id"]
        if rid in holdet_map:
            h = holdet_map[rid]
            r["price"]   = round(h["price_M"] * 2) / 2   # round to nearest 0.5M
            r["own_pct"] = round(h["own_pct"] * 100, 1)  # store as percentage
            updated += 1
    riders_path.write_text(
        json.dumps(riders, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return updated


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Holdet.dk scraper — henter priser, GC-stilling og trøjer til TdF Manager"
    )
    parser.add_argument("--discover",      action="store_true",
                        help="List tilgængelige cycling-cartridges og afslut")
    parser.add_argument("--cartridge",     default=DEFAULT_CARTRIDGE,
                        help=f"Cartridge slug (default: {DEFAULT_CARTRIDGE})")
    parser.add_argument("--game-id",       type=int, default=DEFAULT_GAME_ID,
                        help="Game ID (hentes automatisk fra cartridge hvis ukendt)")
    parser.add_argument("--league-id",     type=int, default=DEFAULT_LEAGUE_ID,
                        help="League ID (bruges ikke til pris/GC, kun til leaderboard)")
    parser.add_argument("--update-riders", action="store_true",
                        help="Opdater priser + popularitet i data/riders.json")
    parser.add_argument("--delay",         type=float, default=1.0,
                        help="Sekunder mellem requests (default: 1.0)")
    args = parser.parse_args()

    HTTP.delay = args.delay

    print("=" * 60)
    print("  Holdet.dk Scraper — TdF Manager")
    print("=" * 60)

    # ── Discovery mode ─────────────────────────────────────────────────────────
    if args.discover:
        print("\n  Søger efter tilgængelige cycling-cartridges…")
        found = list_cycling_cartridges()
        if not found:
            print("\n  Ingen kendte cartridges fundet.")
            print("  Prøv: python scrape_holdet.py --cartridge {slug} --game-id {id}")
        else:
            print(f"\n  Fundet {len(found)} cartridge(s).")
            print("  Opdater DEFAULT_CARTRIDGE og DEFAULT_GAME_ID øverst i scrape_holdet.py.")
        return

    # ── Auto-discover game ID ──────────────────────────────────────────────────
    game_id  = args.game_id
    if game_id is None:
        print(f"\n  Auto-discover: henter {args.cartridge!r}…")
        info = discover_cartridge(args.cartridge)
        if not info:
            print(f"  [ERROR] Cartridge {args.cartridge!r} ikke fundet.")
            print("  Kør: python scrape_holdet.py --discover  for at finde korrekt slug.")
            sys.exit(1)
        game_id = info["game_id"]
        print(f"  game_id={game_id}  leagueId={info.get('league_id')}  ({info.get('name')})")

    cartridge = args.cartridge
    print(f"\n  Cartridge: {cartridge!r}  game_id={game_id}")

    # ── Load riders ────────────────────────────────────────────────────────────
    riders_path = DATA / "riders.json"
    riders = json.loads(riders_path.read_text(encoding="utf-8"))
    print(f"  Ryttere (riders.json): {len(riders)}")
    lut = build_rider_lookup(riders)

    # ── Fetch player data ──────────────────────────────────────────────────────
    player_by_id, person_by_id = fetch_player_info(game_id, cartridge)

    # ── Fetch schedule → find last completed stage ─────────────────────────────
    print("  Henter etapeplan…")
    events, event_info = fetch_schedule(game_id)
    n_stages    = len(events)
    last_event  = last_completed_event(events, event_info)
    print(f"  {n_stages} etaper  —  senest afsluttet: eventId={last_event}")

    # ── Fetch GC + jerseys from latest stage ──────────────────────────────────
    gc_by_person:      dict[int, int]       = {}
    jerseys_by_person: dict[int, list[str]] = {}

    if last_event:
        print(f"  Henter fantasy-actions for etape-event {last_event}…")
        actions = fetch_fantasy_actions(game_id, last_event)
        gc_by_person, jerseys_by_person = extract_gc_and_jerseys(actions)
        print(f"    GC data: {len(gc_by_person)} ryttere  |  Trøjer: {len(jerseys_by_person)} ryttere")
    else:
        print("  Ingen afsluttede etaper endnu — GC/trøje-data er tomt")

    # ── Match Holdet personIds + names to rider IDs ───────────────────────────
    print("  Matcher Holdet-navne til riders.json…")

    # Build personId → rider_id map through player_by_id + person_by_id
    person_to_rid: dict[int, str] = {}
    holdet_map:    dict[str, dict] = {}    # rider_id → Holdet data
    unmatched:     list[str] = []

    for pid, p in player_by_id.items():
        person_id = p.get("personId")
        person    = person_by_id.get(person_id, {})
        full_name = person.get("fullName", "")
        if not full_name:
            continue

        rid = match_name(full_name, lut)
        if rid:
            person_to_rid[person_id] = rid
            holdet_map[rid] = {
                "holdet_name": full_name,
                "holdet_player_id": pid,
                "holdet_person_id": person_id,
                "price_M":    round(p.get("price", 0) / 1_000_000, 2),
                "start_price_M": round(p.get("startPrice", 0) / 1_000_000, 2),
                "own_pct":    round(p.get("popularity", 0) * 100, 2),  # as %
                "is_out":     p.get("isOut", False),
                "is_injured": person.get("isInjured", False),
            }
        else:
            unmatched.append(full_name)

    matched_n = len(holdet_map)
    print(f"    Matchet: {matched_n}/{len(player_by_id)}  |  Ikke matchet: {len(unmatched)}")
    if unmatched:
        # Show unmatched (sorted for readability)
        for nm in sorted(unmatched)[:20]:
            print(f"    [ikke matchet] {nm}")
        if len(unmatched) > 20:
            print(f"    ... og {len(unmatched) - 20} til")

    # ── Map GC/jerseys: personId → rider_id ──────────────────────────────────
    gc_standings:   dict[str, int]       = {}
    jersey_leaders: dict[str, list[str]] = {}

    for person_id, gc_rank in gc_by_person.items():
        rid = person_to_rid.get(person_id)
        if rid:
            gc_standings[rid] = gc_rank

    for person_id, jersey_list in jerseys_by_person.items():
        rid = person_to_rid.get(person_id)
        if rid:
            jersey_leaders[rid] = jersey_list

    # ── Save cache files ──────────────────────────────────────────────────────
    # holdet_players.json
    holdet_cache = CACHE_DIR / "holdet_players.json"
    holdet_cache.write_text(
        json.dumps(holdet_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  Gemt: {holdet_cache}  ({len(holdet_map)} ryttere)")

    # gc_standings.json
    gc_path = CACHE_DIR / "gc_standings.json"
    gc_path.write_text(
        json.dumps(gc_standings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    top5_gc = sorted(gc_standings.items(), key=lambda x: x[1])[:5]
    id2name = {r["id"]: r["full_name"] for r in riders}
    top5_str = ", ".join(f"{rk}. {id2name.get(rid, rid)}" for rid, rk in top5_gc)
    print(f"  Gemt: {gc_path}  ({len(gc_standings)} ryttere)  top5: {top5_str or '(ingen)'}")

    # jerseys.json
    jerseys_path = CACHE_DIR / "jerseys.json"
    jerseys_path.write_text(
        json.dumps(jersey_leaders, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    jersey_str = "  ".join(
        f"{id2name.get(rid, rid)}: {'+'.join(jl)}"
        for rid, jl in jersey_leaders.items()
    )
    print(f"  Gemt: {jerseys_path}  —  {jersey_str or '(ingen trøjer fundet)'}")

    # ── Optionally update riders.json ─────────────────────────────────────────
    if args.update_riders:
        n = update_riders_json(riders, holdet_map, riders_path)
        print(f"\n  riders.json opdateret: {n} ryttere fik ny pris/popularitet")
        # Show price changes
        changes = []
        for r in riders:
            rid = r["id"]
            if rid in holdet_map:
                new_p = round(holdet_map[rid]["price_M"] * 2) / 2
                if abs(new_p - r["price"]) > 0.05:
                    changes.append(f"  {r['full_name']}: {r['price']:.1f}M → {new_p:.1f}M")
        if changes:
            print(f"  Prisændringer ({len(changes)}):")
            for c in changes[:15]:
                print(c)
            if len(changes) > 15:
                print(f"  ... og {len(changes) - 15} til")
    else:
        print("\n  (Kør med --update-riders for at skrive priser tilbage til riders.json)")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n  === Klar ===")
    print(f"  Kør nu:  python run_daily.py --stage N --type TYPE")
    print(f"  GC og trøjer er nu tilgængelige i predictions.")


if __name__ == "__main__":
    main()
