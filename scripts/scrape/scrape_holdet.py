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

ROOT = Path(__file__).parent.parent.parent
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
    "team_time_trial":       "ttt",   # holdtidskørsel — distinct from individual TT
}

# Holdet cartridge slug → PCS race base path
# Used to look up PCS stage types (which distinguish p4/p5) when Holdet
# only returns a generic "mountain" type without knowing summit finish vs. not.
CARTRIDGE_TO_PCS_RACE: dict[str, str] = {
    "giro-d-italia-2026":           "giro-d-italia/2026",
    "criterium-du-dauphine-2026":   "criterium-du-dauphine/2026",
    "tour-de-france-2026":          "tour-de-france/2026",
    "vuelta-a-espana-2026":         "vuelta-a-espana/2026",
    # 2025 historical races (used as training data for 2026 calibration)
    "giro-d-italia-2025":           "giro-d-italia/2025",
    "tour-de-france-2025":          "tour-de-france/2025",
    "vuelta-2025":                  "vuelta-a-espana/2025",
}

# Per-game raw action cache directories (created on demand)
CARTRIDGE_RAW_CACHE: dict[str, Path] = {
    "giro-d-italia-2026":           CACHE_DIR / "giro2026_raw",
    "criterium-du-dauphine-2026":   CACHE_DIR / "dauphine2026_raw",
    "tour-de-france-2026":          CACHE_DIR / "tdf2026_raw",
    "vuelta-a-espana-2026":         CACHE_DIR / "vuelta2026_raw",
    # 2025 historical
    "giro-d-italia-2025":           CACHE_DIR / "giro2025_raw",
    "tour-de-france-2025":          CACHE_DIR / "tdf2025_raw",
    "vuelta-2025":                  CACHE_DIR / "vuelta2025_raw",
}

# Game IDs for known races (populated once discovered via --discover)
# 2025 IDs: run `python scrape_holdet.py --discover` to find and add them here.
KNOWN_GAME_IDS: dict[str, int] = {
    "giro-d-italia-2026":           612,
    "criterium-du-dauphine-2026":   622,
    "tour-de-france-2026":          618,
    # 2025 historical
    "giro-d-italia-2025":           550,
    "tour-de-france-2025":          563,
    "vuelta-2025":                  572,
}

# Holdet ruleId → sprint/KOM/team-bonus category
RULE_SPRINT    = 874    # sprint pts (amount = number of pts scored)
RULE_KOM       = 875    # KOM pts
RULE_TEAM_BEST = {885: 1, 886: 2, 887: 3}   # team rank → rule id mapping

def _load_pcs_stage_type_cache() -> dict:
    """Load PCS stage types cache (same file as scrape_pcs.py uses)."""
    p = CACHE_DIR / "pcs_stage_types.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}

def pcs_override_stage_type(cartridge: str, stage_num: int) -> str | None:
    """
    Return the PCS-derived stage type for a given stage, if available.
    PCS distinguishes p4 (hilly) from p5 (mountain summit) — Holdet does not.
    Returns None if no PCS data is cached for this race/stage.
    """
    pcs_race = CARTRIDGE_TO_PCS_RACE.get(cartridge)
    if not pcs_race:
        return None
    cache = _load_pcs_stage_type_cache()
    stage_map = cache.get(pcs_race, {})
    return stage_map.get(stage_num) or stage_map.get(str(stage_num))


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
        # 2026
        "tour-de-france-2026",
        "giro-d-italia-2026",
        "vuelta-a-espana-2026",
        "criterium-du-dauphine-2026",
        "tour-de-suisse-2026",
        # 2025 historical — full names
        "tour-de-france-2025",
        "giro-d-italia-2025",
        "vuelta-a-espana-2025",
        "criterium-du-dauphine-2025",
        # 2025 historical — short names (som de fremgår i URL'erne)
        "vuelta-2025",
        "giro-2025",
        "tdf-2025",
        # 2025 historical — andre varianter
        "tourspillet-2025",
        "girospillet-2025",
        "vueltaspillet-2025",
        "le-tour-2025",
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
                        # Ownership: popularity is a 0-1 fraction; popularityChange
                        # is Holdet's own recent delta (also a fraction).
                        "popularity":       row.get("popularity") or 0,
                        "popularityChange": row.get("popularityChange") or 0,
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


def detect_next_stage(
    game_id: int,
    cartridge: str = DEFAULT_CARTRIDGE,
) -> tuple[int | None, str | None]:
    """
    Auto-detect which stage to predict next, based on Holdet's live schedule.

    Logic:
      • Find the last "finished" event → that was the most recent stage.
      • The next entry in the schedule is the upcoming stage to predict.
      • If no stage has finished yet (race not started) → predict stage 1.
      • If all stages are finished (race over) → return (None, None).

    Stage type priority:
      1. PCS cache (distinguishes p4 hilly from p5 mountain summit finish)
      2. Holdet's own stageType (if provided)
      3. Fallback: "sprint"

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

    next_eid    = events[next_idx]
    holdet_type = event_info.get(next_eid, {}).get("stageType", "")
    stage_num   = next_idx + 1   # 0-based index → 1-based stage number

    # 1. Try PCS override (more granular: distinguishes p4 vs p5)
    pcs_type = pcs_override_stage_type(cartridge, stage_num)
    if pcs_type:
        stage_type = pcs_type
        source = f"PCS (p-klasse cache)"
    elif holdet_type:
        stage_type = HOLDET_STAGE_TYPE_MAP.get(holdet_type.lower(), "sprint")
        source = f"Holdet stageType='{holdet_type}'"
    else:
        stage_type = "sprint"
        source = "fallback"

    print(f"  Auto-detekteret: etape {stage_num}, type '{stage_type}' ({source})")
    return stage_num, stage_type


# ── Stage fantasy-actions parser ──────────────────────────────────────────────

def fetch_my_team(
    game_id: int,
    team_id: int,
    player_by_id: dict[int, dict],
    person_by_id: dict[int, dict],
    person_to_rid: dict[int, str],
) -> dict | None:
    """
    Fetch the user's current team from Holdet.dk and return a dict ready to
    write to current_team.json:
      {"bank_M": float, "riders": [full_name, ...]}

    team_id is the participantId (hold-ID) from holdet.dk.
    Returns None if the team cannot be fetched.
    """
    # PUBLIC round-lineup endpoint (no login needed):
    #   GET /api/fantasyteams/{id}/rounds/{round_num}/lineup
    # Holdet only exposes a round's lineup once that round is LOCKED, so we
    # use the most recently completed round: find the last round whose start
    # is in the past, then step back one (the in-progress round isn't served).
    from datetime import datetime, timezone

    def _parse_dt(s):
        try:
            return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        except Exception:
            return None

    try:
        rounds = HTTP.get(f"{BASE}/api/games/{game_id}/rounds").json().get("items", [])
    except Exception as exc:
        print(f"  [WARN] Kunne ikke hente runder for game {game_id}: {exc}")
        return None
    if not rounds:
        print("  [WARN] Ingen runder fundet — kan ikke bestemme lineup-runde")
        return None

    now = datetime.now(timezone.utc)
    started = 1
    for r in rounds:
        start = _parse_dt(r.get("start") or r.get("startDate") or r.get("from"))
        num = r.get("number")
        if start is not None and start <= now and isinstance(num, int):
            started = num
    round_num = max(1, started - 1)
    print(f"  [info] seneste startede runde={started} → henter sidst afsluttede runde {round_num}")

    lineup_url = f"{BASE}/api/fantasyteams/{team_id}/rounds/{round_num}/lineup"
    try:
        payload = HTTP.get(lineup_url).json()
    except Exception as exc:
        print(f"  [WARN] Kunne ikke hente lineup ({lineup_url}): {exc}")
        return None
    items = payload.get("items", payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not items:
        print(f"  [WARN] Tom lineup for runde {round_num} (team {team_id})")
        return None

    # Active riders have to==null (not transferred out); captain has role==captain.
    rider_names: list[str] = []
    captain = None
    spent_kr = 0.0
    for it in items:
        if not isinstance(it, dict) or it.get("to") is not None:
            continue
        pid = it.get("playerId")
        if pid is None and isinstance(it.get("player"), dict):
            pid = it["player"].get("id")
        if pid is None:
            continue
        player = player_by_id.get(pid, {})
        person = person_by_id.get(player.get("personId"), {})
        name = person.get("fullName") or player.get("name", "")
        if not name:
            print(f"  [WARN] playerId {pid} ikke i spillerlisten — springes over")
            continue
        rider_names.append(name)
        spent_kr += player.get("startPrice") or player.get("price") or 0
        if it.get("role") == "captain":
            captain = name

    if not rider_names:
        print(f"  [WARN] Ingen aktive ryttere kunne mappes for team {team_id}")
        return None

    # Bank = starting budget (50M) minus what the team cost. The lineup
    # endpoint carries no bank field, so approximate from Holdet start prices:
    # stable over the race (unlike current prices, which rise and would make
    # the bank shrink artificially). Exact if the team was bought at the
    # start; a rough estimate after transfers. Clamp to >= 0.
    START_BUDGET_M = 50.0
    bank_M = round(max(0.0, START_BUDGET_M - spent_kr / 1_000_000), 2)

    cap = f", kaptajn: {captain}" if captain else ""
    print(f"  [info] {len(rider_names)} ryttere hentet (runde {round_num}, "
          f"kostede ~{spent_kr/1_000_000:.1f}M → bank ~{bank_M:.1f}M{cap})")
    return {"bank_M": bank_M, "riders": rider_names}


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


# ── Sprint / KOM / team-bonus standings ──────────────────────────────────────

def _fetch_and_cache_actions(
    game_id: int,
    event_id: int,
    stage_num: int,
    raw_dir: Path,
) -> list[dict]:
    """Load cached stage actions, or fetch + cache from Holdet API."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"actions_s{stage_num:02d}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    actions = fetch_fantasy_actions(game_id, event_id)
    path.write_text(json.dumps(actions, ensure_ascii=False), encoding="utf-8")
    return actions


def compute_sprint_kom_standings(
    all_actions: list[list[dict]],     # list of per-stage action lists
    person_to_rid: dict[int, str],
) -> dict[str, dict]:
    """
    Aggregate sprint (rule 874) and KOM (rule 875) points across all completed
    stages and return classification standings.

    Returns:
      {rider_id: {"sprint_pts": N, "sprint_rank": R,
                  "kom_pts": N,    "kom_rank": R}}

    Only riders with at least 1 point appear in the output.
    """
    sprint: dict[str, int] = {}
    kom:    dict[str, int] = {}

    for stage_actions in all_actions:
        for item in stage_actions:
            pid  = item.get("personId")
            rid  = person_to_rid.get(pid)
            if not rid:
                continue
            rule = item.get("ruleId")
            amt  = item.get("amount", 0) or 0
            if rule == RULE_SPRINT and amt > 0:
                sprint[rid] = sprint.get(rid, 0) + amt
            elif rule == RULE_KOM and amt > 0:
                kom[rid] = kom.get(rid, 0) + amt

    result: dict[str, dict] = {}
    for rank, (rid, pts) in enumerate(
            sorted(sprint.items(), key=lambda x: x[1], reverse=True), 1):
        result.setdefault(rid, {}).update(sprint_pts=pts, sprint_rank=rank)
    for rank, (rid, pts) in enumerate(
            sorted(kom.items(), key=lambda x: x[1], reverse=True), 1):
        result.setdefault(rid, {}).update(kom_pts=pts, kom_rank=rank)
    return result


def compute_team_bonus_expectations(
    all_actions: list[list[dict]],
    person_to_rid: dict[int, str],
    rid_to_team: dict[str, str],
) -> dict[str, int]:
    """
    Look at the team bonus (rules 885/886/887) from recent stages and
    estimate the expected team bonus per rider for the next stage.

    Method:
      For each of the last N stages, identify which professional team placed
      1st / 2nd / 3rd.  Compute a per-team expected bonus as the average
      across those stages.  Return {rider_id: expected_bonus_kr} for all
      riders whose team has a non-zero expectation.

    Returns e.g. {"jonas_vingegaard": 42000, "wout_van_aert": 42000, ...}
    (all riders on the same team get the same expected bonus).
    """
    BONUS = {1: 60_000, 2: 30_000, 3: 20_000}
    n     = len(all_actions)
    if n == 0:
        return {}

    team_bonus_sum: dict[str, float] = {}   # team_code → cumulative expected bonus

    for stage_actions in all_actions:
        # Identify which pro team placed 1st / 2nd / 3rd this stage
        bonuses: dict[int, set[int]] = {885: set(), 886: set(), 887: set()}
        for item in stage_actions:
            rule = item.get("ruleId")
            if rule in bonuses:
                bonuses[rule].add(item.get("personId"))

        for rule_id, rank in RULE_TEAM_BEST.items():
            teams: set[str] = set()
            for pid in bonuses[rule_id]:
                rid  = person_to_rid.get(pid, "")
                team = rid_to_team.get(rid, "")
                if team:
                    teams.add(team)
            for team in teams:
                team_bonus_sum[team] = team_bonus_sum.get(team, 0) + BONUS[rank]

    # Average over N stages to get expected bonus per stage
    team_expected: dict[str, int] = {
        team: round(total / n)
        for team, total in team_bonus_sum.items()
        if total / n >= 1_000   # ignore negligible expectations
    }
    return team_expected  # {team_code: expected_kr_per_rider_per_stage}


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
    # Holdet: "Rasmus Søjberg Pedersen" → riders.json: mads_pedersen
    # Holdet stores the civil name; the rider is known professionally as Mads Pedersen (Lidl-Trek)
    "rasmus sojberg pedersen":          "mads_pedersen",
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
    parser.add_argument("--my-team-id",   type=int, default=None,
                        help="Dit hold-ID fra holdet.dk (f.eks. 7145433) — henter dit "
                             "aktuelle hold og skriver til data/current_team.json")
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
                # Ownership % comes from the statistics rows (person), NOT the
                # players API (which has no popularity → was always 0).
                "own_pct":        round((person.get("popularity") or 0) * 100, 2),
                "own_pct_change": round((person.get("popularityChange") or 0) * 100, 2),
                "is_out":     p.get("isOut", False),
                "is_injured": person.get("isInjured", False),
            }
        else:
            unmatched.append(full_name)

    matched_n = len(holdet_map)
    print(f"    Matchet: {matched_n}/{len(player_by_id)}  |  Ikke matchet: {len(unmatched)}")
    # [DIAG] popularity coverage — why do so few riders get own_pct?
    pop_pos = sum(1 for v in holdet_map.values() if (v.get("own_pct") or 0) > 0)
    print(f"    [DIAG] own_pct>0: {pop_pos}/{matched_n}")
    for probe in ("tadej_pogacar", "remco_evenepoel", "jonas_vingegaard"):
        v = holdet_map.get(probe)
        print(f"    [DIAG] {probe}: {'ikke i map' if v is None else f'own_pct={v.get(\"own_pct\")}'}")
    # How many persons in the stats page carry popularity at all?
    pop_persons = sum(1 for p in person_by_id.values() if (p.get("popularity") or 0) > 0)
    print(f"    [DIAG] personer i statistik m. popularity>0: {pop_persons}/{len(person_by_id)}")
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

    # ── Sprint/KOM + team-bonus from recent stages ───────────────────────────
    # Fetch the last up-to-5 completed stages' actions (cached per game)
    raw_dir     = CARTRIDGE_RAW_CACHE.get(cartridge, CACHE_DIR / "holdet_raw")
    n_to_fetch  = min(5, len([eid for eid in events
                               if event_info.get(eid, {}).get("status") == "finished"]))
    finished    = [eid for eid in events
                   if event_info.get(eid, {}).get("status") == "finished"]
    recent_events = finished[-n_to_fetch:]   # last N completed events

    all_stage_actions: list[list[dict]] = []
    if recent_events:
        print(f"  Henter actions for seneste {len(recent_events)} etaper "
              f"(sprint/KOM/hold-bonus)…")
        for eid in recent_events:
            sn = events.index(eid) + 1   # 1-based stage number
            try:
                acts = _fetch_and_cache_actions(game_id, eid, sn, raw_dir)
                all_stage_actions.append(acts)
            except Exception as exc:
                print(f"    [WARN] etape {sn} actions fejlede: {exc}")

    # Build rid → team lookup
    id_to_rider = {r["id"]: r for r in riders}
    rid_to_team = {rid: id_to_rider[rid].get("team", "") for rid in id_to_rider}

    # Sprint/KOM standings
    sprint_kom = compute_sprint_kom_standings(all_stage_actions, person_to_rid)

    sk_path = CACHE_DIR / "holdet_sprint_kom.json"
    sk_path.write_text(json.dumps(sprint_kom, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    top_sprint = sorted(
        [(rid, d) for rid, d in sprint_kom.items() if "sprint_rank" in d],
        key=lambda x: x[1]["sprint_rank"]
    )[:5]
    top_kom = sorted(
        [(rid, d) for rid, d in sprint_kom.items() if "kom_rank" in d],
        key=lambda x: x[1]["kom_rank"]
    )[:5]
    id2name = {r["id"]: r["full_name"] for r in riders}
    spr_str = ", ".join(f"{id2name.get(rid, rid)} ({d['sprint_pts']}pt)"
                        for rid, d in top_sprint)
    kom_str = ", ".join(f"{id2name.get(rid, rid)} ({d['kom_pts']}pt)"
                        for rid, d in top_kom)
    print(f"  Sprint-klassement (top 5): {spr_str or '(ingen data)'}")
    print(f"  Bjerg-klassement  (top 5): {kom_str or '(ingen data)'}")

    # Team bonus expectations
    team_expected = compute_team_bonus_expectations(
        all_stage_actions, person_to_rid, rid_to_team
    )
    # Expand to per-rider expectations
    rider_team_bonus: dict[str, int] = {}
    for r in riders:
        team = r.get("team", "")
        if team in team_expected:
            rider_team_bonus[r["id"]] = team_expected[team]

    tb_path = CACHE_DIR / "holdet_team_bonus.json"
    tb_path.write_text(json.dumps(rider_team_bonus, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    top_teams = sorted(team_expected.items(), key=lambda x: x[1], reverse=True)[:5]
    teams_str = ", ".join(f"{t}: {v//1000}k" for t, v in top_teams)
    print(f"  Forventet holdbonus (top 5 hold): {teams_str or '(ingen data)'}")

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

    # ── Fetch my team and write current_team.json ─────────────────────────────
    if args.my_team_id:
        print(f"\n  Henter dit hold (ID={args.my_team_id})…")
        my_team = fetch_my_team(
            game_id, args.my_team_id, player_by_id, person_by_id, person_to_rid
        )
        if my_team:
            team_path = DATA / "current_team.json"
            existing = {}
            if team_path.exists():
                try:
                    existing = json.loads(team_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            note = existing.get("_note", "Opdateres automatisk via scrape_holdet.py --my-team-id.")
            out = {
                "_note": note,
                "bank_M":  my_team["bank_M"],
                "riders":  my_team["riders"],
            }
            team_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  Gemt: {team_path}")
            print(f"  Ryttere ({len(my_team['riders'])}): {', '.join(my_team['riders'])}")
            print(f"  Bank: {my_team['bank_M']:.2f}M")
        else:
            print("  [WARN] Kunne ikke hente hold — current_team.json uændret")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n  === Klar ===")
    print(f"  Kør nu:  python run_daily.py --stage N --type TYPE")
    print(f"  GC og trøjer er nu tilgængelige i predictions.")


if __name__ == "__main__":
    main()
