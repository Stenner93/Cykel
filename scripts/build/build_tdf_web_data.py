"""
Build Tour de France 2026 web data for the dashboard.

Generates per-stage model predictions + actual results as the race progresses.
Combines riders.json (TdF rider pool) with Holdet API prices.

Usage:
    python build_tdf_web_data.py
    python build_tdf_web_data.py --no-holdet   # use cached player data
    python build_tdf_web_data.py --delay 0.5   # faster API calls

Output:
    web/data/tdf2026_predictions.json   — per-stage predictions + actuals
    web/data/tdf2026_scores.json        — score matrix for all riders
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent.parent / "scrape"))

import scrape_holdet as _h
from src.predictor import predict_all
from src.optimizer import make_best_team
from src.ml_signal import compute_ml_scores, compute_holdet_raw_scores, compute_placement_scores
from src.scoring import STAGE_PTS, GC_PTS, JERSEY, SPT_PER_PT, LATE_MAX, LATE_PER_MIN, DNF_PEN
from src.scrape_predictions import get_stage_predictions
from scrape_pcs import check_dns

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA    = ROOT / "data"
WEB_DIR = ROOT / "web" / "data"
WEB_DIR.mkdir(parents=True, exist_ok=True)

TDF_GAME_ID   = 618
TDF_CARTRIDGE = "tour-de-france-2026"
TDF_PCS_RACE  = "tour-de-france/2026"
TDF_RAW_DIR   = DATA / "cache" / "tdf2026_raw"
PLAYERS_CACHE = DATA / "cache" / "tdf2026_players.json"

# ── Rule labels (Danish) — same rule IDs as Giro (same Holdet platform) ───────
RULE_LABELS: dict[int, str] = {
    849: "Etapesejr",  850: "2. plads",  851: "3. plads",
    852: "4. plads",   853: "5. plads",  854: "6. plads",
    855: "7. plads",   856: "8. plads",  857: "9. plads",
    858: "10. plads",  859: "11. plads", 860: "12. plads",
    861: "13. plads",  862: "14. plads", 863: "15. plads",
    864: "GC #1",  865: "GC #2",  866: "GC #3",  867: "GC #4",
    868: "GC #5",  869: "GC #6",  870: "GC #7",  871: "GC #8",
    872: "GC #9",  873: "GC #10",
    874: "Sprint-point",
    875: "KOM-point",
    876: "Ledertrøje",
    877: "Pointtrøje",
    878: "Bjergtrøje",
    879: "Ungdomstrøje",
    885: "Holdbonus (1. bedste hold)",
    886: "Holdbonus (2. bedste hold)",
    887: "Holdbonus (3. bedste hold)",
    888: "Etapeplacering",
    889: "Sprint-point (klassement)",
    890: "KOM-point (klassement)",
    891: "GC-stilling",
    892: "Pointklassement-rang",
    893: "Bjergklassement-rang",
    894: "Etapepræmie",
    895: "Forsinkelse",
    904: "Særpræmie",
    905: "Gruppetto",
    1044: "Angreb",
    1080: "DNF",
}

# ── TdF expected-points ceiling for a stage winner ────────────────────────────
#
# Tour de France scoring (same scale as Giro):
#   Stage win:    560 000 kr  (sprint/hilly)
#   Mountain win: 630 000 kr  (hardest stages, major GC bonus)
#   TT win:       380 000 kr  (smaller field spread)
#
# These match the Giro 2026 defaults — TdF is the same Holdet scoring platform.
TDF_WINNER_POINTS: dict[str, int] = {
    "sprint":   560_000,
    "mountain": 630_000,
    "hilly":    500_000,
    "tt":       380_000,
    "ttt":      380_000,
    "cobbled":  500_000,
    "gc":       630_000,
}

# ── Known WorldTour team abbreviations ─────────────────────────────────────────
_TEAM_MAP: list[tuple[str, str]] = [
    # (substring to look for in lowercased team name, abbreviation)
    ("visma",          "VLB"),
    ("lease a bike",   "VLB"),
    ("uae team",       "UAE"),
    ("ineos",          "IGD"),
    ("lidl",           "L-T"),
    ("trek",           "L-T"),
    ("soudal",         "SQ"),
    ("quick-step",     "SQ"),
    ("groupama",       "GFD"),
    ("fdj",            "GFD"),
    ("dsm",            "DSM"),
    ("red bull",       "RBH"),
    ("bora",           "BORA"),
    ("ef education",   "EF"),
    ("decathlon",      "DAL"),
    ("ag2r",           "DAL"),
    ("intermarche",    "INT"),
    ("intermarché",    "INT"),
    ("cofidis",        "COF"),
    ("arkea",          "ARK"),
    ("arke",           "ARK"),
    ("uno-x",          "UXM"),
    ("lotto",          "LOT"),
    ("astana",         "XDA"),
    ("q36",            "Q36"),
    ("tudor",          "TDR"),
    ("jayco",          "JAL"),
    ("bahrain",        "TVM"),
    ("movistar",       "MOV"),
    ("israel",         "IPT"),
    ("alpecin",        "ALP"),
    ("human powered",  "HPH"),
]


def team_abbrev(name: str) -> str:
    """Convert a full team name to a short abbreviation."""
    if not name:
        return ""
    low = name.lower()
    for key, abbr in _TEAM_MAP:
        if key in low:
            return abbr
    # Fallback: first letters of words (max 4 chars)
    words = [w for w in name.split() if len(w) > 2 and w[0].isupper()]
    return "".join(w[0] for w in words[:4]) or name[:4].upper()


def _to_rider_id(full_name: str) -> str:
    """Normalise a full name to a rider_id key matching riders.json convention."""
    s = full_name.strip().lower()
    s = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    return "_".join(s.split())


# ── Raw cache helpers ───────────────────────────────────────────────────────────

def _raw(name: str) -> Path:
    TDF_RAW_DIR.mkdir(parents=True, exist_ok=True)
    return TDF_RAW_DIR / name


def _load_raw(name: str):
    p = _raw(name)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _save_raw(name: str, data) -> None:
    _raw(name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ── Fetch helpers ───────────────────────────────────────────────────────────────

def fetch_stage_actions(game_id: int, event_id: int) -> list[dict]:
    """GET /api/games/{gameId}/events/{eventId}/fantasy-actions"""
    return _h.HTTP.get(
        f"{_h.BASE}/api/games/{game_id}/events/{event_id}/fantasy-actions"
    ).json().get("items", [])


def fetch_scoring_summary(game_id: int, event_id: int) -> dict[int, int]:
    """
    GET /api/games/{gameId}/events/{eventId}/scoring-summary
    Returns {personId: total_score} or {} if endpoint unavailable.
    """
    try:
        resp = _h.HTTP.get(
            f"{_h.BASE}/api/games/{game_id}/events/{event_id}/scoring-summary"
        )
        data  = resp.json()
        items = data if isinstance(data, list) else data.get("items", [])
        result: dict[int, int] = {}
        for item in items:
            pid = (
                item.get("personId")
                or item.get("person_id")
                or item.get("playerId")
            )
            score = (
                item.get("score")
                or item.get("total")
                or item.get("points")
                or item.get("fantasyPoints")
                or 0
            )
            if pid is not None:
                result[int(pid)] = int(score)
        return result
    except Exception as exc:
        print(f"    [INFO] scoring-summary unavailable for event {event_id}: {exc}")
        return {}


def _action_pts(rule_id: int, amount: float, has_gc_specific: bool) -> int:
    """Convert a Holdet rule action to fantasy points (fallback reconstruction)."""
    if 849 <= rule_id <= 863:
        return STAGE_PTS.get(rule_id - 848, 0)
    if 864 <= rule_id <= 873:
        return GC_PTS.get(rule_id - 863, 0)
    if rule_id == 874:
        return int(amount) * SPT_PER_PT
    if rule_id == 875:
        return int(amount) * SPT_PER_PT
    if rule_id == 876: return JERSEY["leader"]
    if rule_id == 877: return JERSEY["sprint"]
    if rule_id == 878: return JERSEY["mountain"]
    if rule_id == 879: return JERSEY["youth"]
    if rule_id == 1044: return JERSEY.get("most_aggressive", 50_000)
    if rule_id == 1080: return DNF_PEN
    if rule_id == 895:
        return max(LATE_MAX, int(amount) * LATE_PER_MIN)
    if rule_id == 891 and not has_gc_specific:
        return GC_PTS.get(int(amount), 0)
    return 0


# ── Rider pool ─────────────────────────────────────────────────────────────────

def build_riders(
    player_by_id: dict,
    person_by_id: dict,
    riders_json: list[dict],
) -> list[dict]:
    """
    Build a `riders` list from riders.json (base data) supplemented with
    prices and holdet_person_id from the Holdet API.

    riders.json already has the correct TdF rider pool and rider IDs.
    Holdet API provides current prices and person IDs for actuals mapping.
    """
    # Build lookup: rider_id → riders.json entry
    base_by_id: dict[str, dict] = {r["id"]: r for r in riders_json}

    # Build lookup: normalised full name → rider_id (from riders.json)
    name_lut: dict[str, str] = {}
    for r in riders_json:
        norm = _to_rider_id(r["full_name"])
        name_lut[norm] = r["id"]
        # Also map via last name for fuzzy fallback
        last = norm.split("_")[-1] if "_" in norm else norm
        if last not in name_lut:
            name_lut[last] = r["id"]

    # Build Holdet name → person_id + price + team mapping
    holdet_prices: dict[str, tuple[int, float]] = {}  # rider_id → (person_id, price_M)
    holdet_pid_to_rid: dict[int, str] = {}
    holdet_team: dict[str, str] = {}  # rider_id → Holdet teamName (authoritative)

    for pid_str, p in player_by_id.items():
        person_id = p.get("personId")
        if not person_id:
            continue
        person    = person_by_id.get(person_id, {})
        full_name = person.get("fullName", "")
        if not full_name:
            continue

        # Match to riders.json via name normalisation + Holdet overrides
        norm_name = _h._norm(full_name)
        if norm_name in _h.HOLDET_NAME_OVERRIDES:
            rid = _h.HOLDET_NAME_OVERRIDES[norm_name]
        else:
            rid = _to_rider_id(full_name)
            if rid not in base_by_id:
                # Try last-name lookup
                last = rid.split("_")[-1] if "_" in rid else rid
                rid = name_lut.get(rid) or name_lut.get(last) or rid

        price_M = round(p.get("price", 0) / 1_000_000, 2)
        holdet_prices[rid] = (int(person_id), price_M)
        holdet_pid_to_rid[int(person_id)] = rid
        holdet_team[rid] = person.get("teamName", "")

    # Merge: start from riders.json, overlay Holdet price + person_id.
    # Only keep riders actually confirmed in Holdet's TdF game — riders.json
    # is a cross-race pool (built for Giro/Dauphiné) and contains many names
    # that aren't part of this race. Showing them in predictions would mean
    # ranking riders who may not even start, and — worse — some of the
    # biggest TdF stars (Pogačar, Evenepoel, Roglič, van der Poel, ...)
    # simply aren't in riders.json yet, so they'd be silently excluded from
    # the very top of the field while padding the bottom with irrelevant
    # leftover names. Holdet's player list is the authoritative TdF roster.
    riders: list[dict] = []
    seen_ids: set[str] = set()

    for base in riders_json:
        rid = base["id"]
        if rid in seen_ids:
            continue

        person_id, price_M = holdet_prices.get(rid, (None, base.get("price", 0.0)))
        if person_id is None:
            continue   # not in Holdet's TdF game — skip
        seen_ids.add(rid)

        full_name  = base["full_name"]
        short_name = base.get("short_name", full_name.split()[-1])
        # Always recompute team abbreviation from Holdet's authoritative
        # teamName — riders.json's own "team" field uses an inconsistent
        # abbreviation scheme from earlier races (e.g. "TV|L" vs "VLB" for
        # the same Visma squad), which would silently split teammates into
        # different "teams" for TTT clustering and team-bonus calculations.
        team_full  = holdet_team.get(rid) or base.get("team_full", "")
        team       = team_abbrev(team_full) if team_full else base.get("team", "")

        riders.append({
            "id":               rid,
            "full_name":        full_name,
            "short_name":       short_name,
            "team":             team,
            "team_full":        team_full,
            "price":            price_M,
            "holdet_person_id": person_id,
        })

    # Also add any Holdet riders not in riders.json (late call-ups etc.)
    for rid, (person_id, price_M) in holdet_prices.items():
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        person    = person_by_id.get(person_id, {})
        full_name = person.get("fullName", rid.replace("_", " ").title())
        tname     = person.get("teamName", "")
        riders.append({
            "id":               rid,
            "full_name":        full_name,
            "short_name":       full_name.split()[-1],
            "team":             team_abbrev(tname),
            "team_full":        tname,
            "price":            price_M,
            "holdet_person_id": person_id,
        })

    return sorted(riders, key=lambda r: r["full_name"])


# ── Main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Byg Tour de France 2026 web-data")
    parser.add_argument("--delay",     type=float, default=1.0)
    parser.add_argument("--no-holdet", action="store_true",
                        help="Brug kun cached spiller-data (ingen nye API-kald)")
    args = parser.parse_args()

    _h.HTTP.delay = args.delay

    print("=" * 60)
    print("  Build Tour de France 2026 Web Data")
    print("=" * 60)

    # ── Load base riders from riders.json ────────────────────────────────────
    riders_json_path = DATA / "riders.json"
    if riders_json_path.exists():
        riders_json: list[dict] = json.loads(riders_json_path.read_text(encoding="utf-8"))
        print(f"  riders.json: {len(riders_json)} ryttere")
    else:
        riders_json = []
        print("  [ADVARSEL] data/riders.json mangler — kun Holdet-ryttere bruges")

    # ── Fetch or load rider pool (with prices from Holdet) ───────────────────
    if args.no_holdet and PLAYERS_CACHE.exists():
        riders = json.loads(PLAYERS_CACHE.read_text(encoding="utf-8"))
        print(f"  [cache] {len(riders)} ryttere fra tdf2026_players.json")
    else:
        print("  Henter spillerliste fra Holdet…")
        try:
            player_by_id, person_by_id = _h.fetch_player_info(TDF_GAME_ID, TDF_CARTRIDGE)
            riders = build_riders(player_by_id, person_by_id, riders_json)
            PLAYERS_CACHE.write_text(
                json.dumps(riders, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"  {len(riders)} ryttere gemt til cache")
        except Exception as e:
            if PLAYERS_CACHE.exists():
                print(f"  ⚠️  Holdet player-fetch fejl ({e}) — bruger cached spillerliste")
                riders = json.loads(PLAYERS_CACHE.read_text(encoding="utf-8"))
                print(f"  [cache] {len(riders)} ryttere fra tdf2026_players.json")
            else:
                print(f"  ❌  Holdet player-fetch fejl og ingen cache — afbryder ({e})")
                return

    # Build lookup: holdet_person_id → rider (for actuals)
    pid_to_rider: dict[int, dict] = {
        r["holdet_person_id"]: r for r in riders if r.get("holdet_person_id")
    }

    # ── Fetch schedule ───────────────────────────────────────────────────────
    print("  Henter etapeplan…")
    sched_cache = _load_raw("schedule.json")
    if sched_cache and args.no_holdet:
        events_raw     = sched_cache.get("events", [])
        event_info_raw = sched_cache.get("event_info", {})
        events         = events_raw
        event_info     = {int(k): v for k, v in event_info_raw.items()}
    else:
        try:
            events, event_info = _h.fetch_schedule(TDF_GAME_ID)
            _save_raw("schedule.json", {
                "events":     events,
                "event_info": {str(k): v for k, v in event_info.items()},
            })
        except Exception as e:
            if sched_cache:
                print(f"  ⚠️  Holdet schedule 403/fejl ({e}) — bruger cached etapeplan")
                events_raw     = sched_cache.get("events", [])
                event_info_raw = sched_cache.get("event_info", {})
                events         = events_raw
                event_info     = {int(k): v for k, v in event_info_raw.items()}
            else:
                print(f"  ❌  Holdet schedule fejl og ingen cache — afbryder ({e})")
                return

    n_stages = len(events)
    finished = [eid for eid in events
                if event_info.get(eid, {}).get("status") == "finished"]
    upcoming = [eid for eid in events
                if event_info.get(eid, {}).get("status") != "finished"]
    print(f"  {n_stages} etaper  —  {len(finished)} afsluttede, {len(upcoming)} kommende")

    if n_stages != 21:
        print(f"  [INFO] Forventede 21 etaper, fik {n_stages} — tjek Holdet schedule")

    # ── Load model data ──────────────────────────────────────────────────────
    print("  Indlæser modeldata…")

    def _load_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    co_raw  = _load_json(DATA / "cache" / "cyclingoracle.json")
    co_data = {name: d.get("ratings", {}) for name, d in co_raw.items()} if co_raw else {}

    pcs_raw  = _load_json(DATA / "cache" / "pcs_form.json")
    pcs_form: dict[str, dict] = {}
    pcs_form_long: dict[str, dict] = {}     # multi-season form (slow decay)
    pcs_specialties: dict[str, dict] = {}   # {rider_id: {climber, sprint, tt, hills, ...}}
    for rid, entry in pcs_raw.items():
        if entry.get("not_found"):
            continue
        if "form_by_type" in entry:
            pcs_form[rid] = entry["form_by_type"]
        else:
            pcs_form[rid] = {"overall": entry.get("form_score", 50.0)}
        if entry.get("form_long_by_type"):
            pcs_form_long[rid] = entry["form_long_by_type"]
        if "pcs_specialties" in entry and entry["pcs_specialties"]:
            pcs_specialties[rid] = entry["pcs_specialties"]

    pcs_scores    = _load_json(DATA / "cache" / "pcs_profile_scores.json")
    stage_scores  = pcs_scores.get(TDF_PCS_RACE + "_scores", {})
    stage_finish_alts = pcs_scores.get(TDF_PCS_RACE + "_finish_alt", {})

    pcs_types_cache = _load_json(DATA / "cache" / "pcs_stage_types.json")
    stage_types     = pcs_types_cache.get(TDF_PCS_RACE, {})
    stage_types_meta = pcs_types_cache.get(TDF_PCS_RACE + "_meta", {})

    # ── Load rider context (status from previous stage) ──────────────────────
    context_path = DATA / "stage_context.json"
    all_stage_context: dict[int, dict] = {}
    if context_path.exists():
        raw_ctx  = json.loads(context_path.read_text(encoding="utf-8"))
        race_ctx = raw_ctx.get(TDF_CARTRIDGE, {})
        # {stage_num_str: {"riders": {rider_id: {status, note}}}}
        for snum_str, sdata in race_ctx.items():
            if snum_str.startswith("_"):
                continue
            try:
                all_stage_context[int(snum_str)] = sdata.get("riders", {})
            except ValueError:
                pass
        n_ctx = sum(len(v) for v in all_stage_context.values())
        print(f"  Rytterkontekst: {n_ctx} noter fordelt over "
              f"{len(all_stage_context)} etaper")
    else:
        print("  Rytterkontekst: ingen (data/stage_context.json mangler)")

    # ── DNS check via PCS startlist (kun når løbet er i gang) ───────────────
    dns_ids: set[str] = set()
    if finished and not args.no_holdet:
        print("  Tjekker DNS mod PCS startliste…")
        dns_list = check_dns(riders, TDF_PCS_RACE)
        dns_ids  = set(dns_list)
        if dns_ids:
            id_to_name = {r["id"]: r["full_name"] for r in riders}
            print(f"  DNS ryttere ({len(dns_ids)}): "
                  + ", ".join(id_to_name.get(r, r) for r in sorted(dns_ids)))
        else:
            print("  Alle ryttere ser ud til at starte")
    elif not finished:
        print("  DNS-check springes over (løbet ikke startet endnu)")

    # ── Load PCS 12-month rankings + ML signal ───────────────────────────────
    pcs_rankings_raw = _load_json(DATA / "cache" / "pcs_rankings.json")
    pcs_rank_data: dict[str, float] = {}
    pcs_n_results_data: dict[str, int] = {}
    for rid, entry in pcs_raw.items():
        pcs_url = entry.get("pcs_url", "")
        if pcs_url and "/rider/" in pcs_url:
            slug = pcs_url.split("/rider/")[-1].strip("/")
            rank_entry = pcs_rankings_raw.get(slug, {})
            if rank_entry.get("pts", 0) > 0:
                pcs_rank_data[rid] = float(rank_entry["pts"])
        pcs_n_results_data[rid] = entry.get("n_results", 0)

    gt_results_raw = _load_json(DATA / "cache" / "gt_stage_results.json")

    print(f"  CyclingOracle: {len(co_data)}  PCS form: {len(pcs_form)}"
          f"  PCS specialties: {len(pcs_specialties)}")
    print(f"  PCS stage types: {len(stage_types)} etaper  "
          f"profile scores: {len(stage_scores)} etaper")
    print(f"  PCS ranking: {len(pcs_rank_data)} ryttere")

    # ── Fetch actuals + actions for completed stages ─────────────────────────
    print(f"  Henter actual-point for {len(finished)} afsluttede etaper…")

    # {stage_num: {rider_id: actual_pts}}
    actuals: dict[int, dict[str, int]] = {}
    # {stage_num: list[action]}  — for rule breakdown in matrix
    all_actions: dict[int, list] = {}
    # {stage_num: {personId: total}}  — from scoring-summary or reconstructed
    all_summaries: dict[int, dict[int, int]] = {}
    summary_available = False

    for stage_num, eid in enumerate(events, start=1):
        if event_info.get(eid, {}).get("status") != "finished":
            continue

        # ── Actions ──────────────────────────────────────────────────────────
        act_cache = _load_raw(f"actions_e{eid}.json")
        if act_cache is None and not args.no_holdet:
            act_cache = fetch_stage_actions(TDF_GAME_ID, eid)
            _save_raw(f"actions_e{eid}.json", act_cache)
        if act_cache:
            all_actions[stage_num] = act_cache

        # ── Scoring summary ───────────────────────────────────────────────────
        sum_cache_name = f"summary_s{stage_num:02d}.json"
        cached_sum     = _load_raw(sum_cache_name)

        if cached_sum is None and not args.no_holdet:
            summary_by_pid = fetch_scoring_summary(TDF_GAME_ID, eid)

            if not summary_by_pid and act_cache:
                # Reconstruct from actions as fallback
                print(f"    Etape {stage_num}: rekonstruerer point fra actions…")
                for action in act_cache:
                    pid = action.get("personId")
                    if pid is None:
                        continue
                    has_gc = any(864 <= a["ruleId"] <= 873
                                 for a in act_cache if a.get("personId") == pid)
                    pts = _action_pts(action["ruleId"], action.get("amount", 1), has_gc)
                    summary_by_pid[pid] = summary_by_pid.get(pid, 0) + pts

            _save_raw(sum_cache_name, {str(k): v for k, v in summary_by_pid.items()})
            cached_sum = {str(k): v for k, v in summary_by_pid.items()}

        if cached_sum:
            summary: dict[int, int] = {int(k): v for k, v in cached_sum.items()}
            all_summaries[stage_num] = summary
            if summary:
                summary_available = True

            # Build rider_id → pts lookup for predictions actuals
            stage_actuals: dict[str, int] = {}
            for pid_int, pts in summary.items():
                rider = pid_to_rider.get(pid_int)
                if rider:
                    stage_actuals[rider["id"]] = int(pts)
            actuals[stage_num] = stage_actuals

    # ── GC + jerseys from last completed stage ───────────────────────────────
    gc_standings:   dict[str, int]       = {}
    jersey_leaders: dict[str, list[str]] = {}

    if finished:
        last_eid   = finished[-1]
        last_stage = events.index(last_eid) + 1
        gc_cache   = _load_raw(f"gc_s{last_stage:02d}.json")

        if gc_cache is None and not args.no_holdet:
            actions = _h.HTTP.get(
                f"{_h.BASE}/api/games/{TDF_GAME_ID}/events/{last_eid}/fantasy-actions"
            ).json().get("items", [])
            gc_by_person, jerseys_by_person = _h.extract_gc_and_jerseys(actions)
            gc_cache = {
                "gc":     {str(k): v for k, v in gc_by_person.items()},
                "jersey": {str(k): v for k, v in jerseys_by_person.items()},
            }
            _save_raw(f"gc_s{last_stage:02d}.json", gc_cache)

        if gc_cache:
            for pid_str, rank in gc_cache.get("gc", {}).items():
                r = pid_to_rider.get(int(pid_str))
                if r:
                    gc_standings[r["id"]] = int(rank)
            for pid_str, jlist in gc_cache.get("jersey", {}).items():
                r = pid_to_rider.get(int(pid_str))
                if r:
                    jersey_leaders[r["id"]] = jlist

    if gc_standings:
        top3       = sorted(gc_standings.items(), key=lambda x: x[1])[:3]
        id_to_name = {r["id"]: r["full_name"] for r in riders}
        print(f"  GC top-3: {', '.join(f'{rnk}. {id_to_name.get(rid, rid)}' for rid, rnk in top3)}")
    elif co_data:
        # Race not started: estimate pre-race GC from CO_GC ratings.
        # This ensures _expected_gc_bonus fires for all upcoming stages,
        # adding the daily GC classification bonus (100k for leader, etc.)
        field_ids = {r["id"] for r in riders}
        gc_rated = [
            (rid, 0.5*co_data[rid].get("MTN",0) + 0.3*co_data[rid].get("GC",0) + 0.2*co_data[rid].get("HLL",0))
            for rid in field_ids
            if rid in co_data and co_data[rid].get("GC", 0) > 72
        ]
        gc_rated.sort(key=lambda x: -x[1])
        gc_standings = {rid: rank + 1 for rank, (rid, _) in enumerate(gc_rated[:20])}
        id_to_name   = {r["id"]: r["full_name"] for r in riders}
        top3_str = ', '.join(
            f"{rank+1}. {id_to_name.get(rid, rid)} ({score:.1f})"
            for rank, (rid, score) in enumerate(gc_rated[:3])
        )
        print(f"  Pre-race GC estimate (MTN-blend): {top3_str}")

    # ── Run predictions per stage ────────────────────────────────────────────
    print(f"  Kører forudsigelser for {n_stages} etaper…")

    stages_pred: list[dict] = []

    for stage_num, eid in enumerate(events, start=1):
        info   = event_info.get(eid, {})
        status = info.get("status", "upcoming")

        # Stage type: PCS override > Holdet stageType > fallback
        stype = (
            _h.pcs_override_stage_type(TDF_CARTRIDGE, stage_num)
            or _h.HOLDET_STAGE_TYPE_MAP.get(info.get("stageType", ""), "")
            or "hilly"
        )

        profile_score = stage_scores.get(stage_num) or stage_scores.get(str(stage_num))

        _smeta     = stage_types_meta.get(str(stage_num), {})
        p_class    = int(_smeta.get("p_class") or 0) or -1
        finish_alt = int(stage_finish_alts.get(stage_num)
                         or stage_finish_alts.get(str(stage_num)) or 0) or -1

        print(f"    Etape {stage_num:2d} ({stype}, p{p_class}, status={status})…")

        # For upcoming stages: fetch web predictions as odds signal
        odds_data:   dict | None = None
        odds_source: str         = ""
        if status != "finished":
            odds_data, odds_source = get_stage_predictions(
                cartridge=TDF_CARTRIDGE,
                stage_num=stage_num,
                verbose=True,
            )
            if not odds_data:
                odds_data = None

        # Build context with auto-DNS from startlist check
        stage_ctx = dict(all_stage_context.get(stage_num) or {})
        for dns_id in dns_ids:
            if dns_id not in stage_ctx:
                stage_ctx[dns_id] = {"status": "dns", "note": "Ikke på PCS startliste"}

        stage_ml = compute_ml_scores(
            riders=riders,
            stage_type=stype,
            stage_num=stage_num,
            profile_score=profile_score,
            gt_results=gt_results_raw or None,
            pcs_form_raw=pcs_raw or None,
            pcs_rankings=pcs_rankings_raw or None,
            co_data=co_data or None,
            pcs_specialty_data=pcs_specialties or None,
            startlist_quality=1.0,   # TdF: top-tier field (~1000 PCS score → 1.0 normalised)
        )
        stage_holdet_raw = compute_holdet_raw_scores(
            riders=riders,
            stage_type=stype,
            stage_num=stage_num,
            gt_results=gt_results_raw or None,
            pcs_form_raw=pcs_raw or None,
            co_data=co_data or None,
            pcs_specialty_data=pcs_specialties or None,
        )
        stage_placement = compute_placement_scores(
            riders=riders,
            stage_type=stype,
            stage_num=stage_num,
            gt_results=gt_results_raw or None,
            pcs_form_raw=pcs_raw or None,
            co_data=co_data or None,
            pcs_specialty_data=pcs_specialties or None,
            startlist_quality=1.0,
            profile_score=float(profile_score or 100),
            p_class=p_class,
            finish_alt=float(finish_alt),
        )

        preds = predict_all(
            riders=riders,
            stage_type=stype,
            veloscore_data=None,        # no VeloScore integration for TdF web build
            odds_data=odds_data,
            cyclingoracle_data=co_data,
            pcs_form_data=pcs_form,
            pcs_form_long_data=pcs_form_long,
            current_gc=gc_standings or None,
            current_jerseys=jersey_leaders or None,
            profile_score=profile_score,
            winner_pts_override=TDF_WINNER_POINTS,
            rider_context=stage_ctx or None,
            pcs_specialty_data=pcs_specialties or None,
            ml_prob_data=stage_ml or None,
            pcs_rank_data=pcs_rank_data or None,
            pcs_n_results_data=pcs_n_results_data or None,
            holdet_raw_data=stage_holdet_raw or None,
            placement_data=stage_placement or None,
        )

        stage_actuals = actuals.get(stage_num, {})

        # Best team (unconstrained)
        best   = make_best_team(preds) if preds else None
        cap_id = next(
            (r["rider_id"] for r in (best["team"] if best else []) if r.get("is_captain")),
            None,
        )
        best_ids = {r["rider_id"] for r in (best["team"] if best else [])}

        riders_out: list[dict] = []
        for p in preds:
            rid    = p["rider_id"]
            actual = stage_actuals.get(rid)
            sigs   = p.get("signal_scores", {})
            riders_out.append({
                "id":       rid,
                "name":     p["full_name"],
                "team":     p.get("team", ""),
                "price":    p.get("price", 0),
                "exp":      p.get("expected_pts", 0),
                "var":      p.get("variance", 0),
                "form":     round(p.get("form_score", 0), 1),
                "disc":     round(p.get("disc_raw", 0) or 0, 1),
                "disc_co":  round(p.get("disc_co_raw", 0) or 0, 1),
                "disc_key": p.get("disc_key", "AVG"),
                "signals":  [
                    round(sigs.get("veloscore") or 0, 3),
                    round(sigs.get("odds") or 0, 3),
                    round(sigs.get("discipline") or 0, 3),
                    round(sigs.get("form") or 0, 3),
                    round(sigs.get("ml") or 0, 3),
                    round(sigs.get("pcs_rank") or 0, 3),
                ],
                "reason":   p.get("reasoning", ""),
                "actual":   actual,
                "in_opt":   rid in best_ids,
                "is_cap":   rid == cap_id,
                "ctx_status": p.get("context_status", "normal"),
                "ctx_note":   p.get("context_note", ""),
                "ctx_mult":   p.get("context_mult", 1.0),
                # holdet_est i tusinder (displayformat: 202 → 202k)
                # Beregnet fra holdet ML-model når tilgængeligt, ellers fra exp
                "holdet_est": round(p.get("expected_pts", 0) / 1000, 1) if p.get("expected_pts") else None,
                "holdet_raw_pred": round(p["holdet_raw_pred"], 2) if p.get("holdet_raw_pred") is not None else None,
                "placement_pred":  round(p["placement_pred"], 4) if p.get("placement_pred") is not None else None,
                "ml_source":       p.get("ml_source_used"),
                "expected_pts":    p.get("expected_pts"),
            })

        # Top odds for display in dashboard sources tab
        odds_top = []
        if odds_data:
            odds_top = [
                {"name": name.title(), "prob": round(prob, 4)}
                for name, prob in sorted(odds_data.items(), key=lambda x: -x[1])[:15]
            ]

        stages_pred.append({
            "num":           stage_num,
            "type":          stype,
            "event_id":      eid,
            "status":        status,
            "name":          info.get("name", f"Etape {stage_num}"),
            "profile_score": profile_score,
            "riders":        riders_out,
            "best_team":     sorted(best_ids),
            "cap_id":        cap_id,
            "odds_source":   odds_source,
            "odds_top":      odds_top,
        })

    # ── Build score matrix ────────────────────────────────────────────────────
    print(f"  Bygger pointmatrix ({len(all_summaries)} etaper med data)…")

    act_idx: dict[tuple, list] = {}
    for snum, acts in all_actions.items():
        for a in acts:
            act_idx.setdefault((snum, a["personId"]), []).append(a)

    stages_meta_matrix = []
    for stage_num, eid in enumerate(events, start=1):
        info  = event_info.get(eid, {})
        stype = (
            _h.pcs_override_stage_type(TDF_CARTRIDGE, stage_num)
            or _h.HOLDET_STAGE_TYPE_MAP.get(info.get("stageType", ""), "")
            or "hilly"
        )
        stages_meta_matrix.append({
            "num":    stage_num,
            "name":   info.get("name", f"Etape {stage_num}"),
            "type":   stype,
            "status": info.get("status", "none"),
        })

    riders_matrix: list[dict] = []
    for rider in sorted(riders, key=lambda r: r["full_name"]):
        pid = rider.get("holdet_person_id")
        if not pid:
            continue
        pts_by_stage:   dict[str, int]  = {}
        rules_by_stage: dict[str, list] = {}

        for stage in stages_meta_matrix:
            snum = stage["num"]
            if snum in all_summaries and pid in all_summaries[snum]:
                total = all_summaries[snum][pid]
            elif snum in all_actions:
                rider_acts = act_idx.get((snum, pid), [])
                has_gc     = any(864 <= a["ruleId"] <= 873 for a in rider_acts)
                total      = sum(_action_pts(a["ruleId"], a.get("amount", 1), has_gc)
                                 for a in rider_acts)
            else:
                total = 0

            if total:
                pts_by_stage[str(snum)] = total
            rider_acts = act_idx.get((snum, pid), [])
            if rider_acts:
                rules_by_stage[str(snum)] = [
                    [a["ruleId"], a.get("amount", 1)] for a in rider_acts
                ]

        riders_matrix.append({
            "id":    rider["id"],
            "name":  rider["full_name"],
            "team":  rider["team"],
            "price": rider["price"],
            "pid":   pid,
            "pts":   pts_by_stage,
            "rules": rules_by_stage,
        })

    scores_out = {
        "generated":      datetime.now(timezone.utc).isoformat(),
        "game_id":        TDF_GAME_ID,
        "cartridge":      TDF_CARTRIDGE,
        "summary_source": "scoring-summary" if summary_available else "computed",
        "stages":         stages_meta_matrix,
        "rule_labels":    {str(k): v for k, v in RULE_LABELS.items()},
        "riders":         riders_matrix,
    }
    scores_path = WEB_DIR / "tdf2026_scores.json"
    scores_path.write_text(
        json.dumps(scores_out, ensure_ascii=False, indent=None), encoding="utf-8"
    )
    print(f"  Gemt: {scores_path}  ({len(riders_matrix)} ryttere)")

    # ── Write output ─────────────────────────────────────────────────────────
    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "race":      "Tour de France 2026",
        "cartridge": TDF_CARTRIDGE,
        "n_stages":  n_stages,
        "stages":    stages_pred,
    }

    out_path = WEB_DIR / "tdf2026_predictions.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=None), encoding="utf-8")
    print(f"\n  Gemt: {out_path}  ({n_stages} etaper)")
    print()
    print("  === Klar! ===")
    print("  Push web/data/ til GitHub for at opdatere dashboardet.")
    print("  Ny side: web/tdf.html")


if __name__ == "__main__":
    main()
