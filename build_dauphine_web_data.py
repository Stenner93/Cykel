"""
Build Critérium du Dauphiné 2026 web data for the dashboard.

Generates per-stage model predictions + actual results as the race progresses.
No Holdet team required — used purely for model testing and calibration.

Usage:
    python build_dauphine_web_data.py
    python build_dauphine_web_data.py --no-holdet   # use cached player data
    python build_dauphine_web_data.py --delay 0.5   # faster API calls

Output:
    web/data/dauphine2026_predictions.json   — per-stage predictions + actuals
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import scrape_holdet as _h
from src.predictor import predict_all
from src.optimizer import make_best_team

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA    = ROOT / "data"
WEB_DIR = ROOT / "web" / "data"
WEB_DIR.mkdir(parents=True, exist_ok=True)

DAUPHINÉ_GAME_ID   = 622
DAUPHINÉ_CARTRIDGE = "criterium-du-dauphine-2026"
DAUPHINÉ_PCS_RACE  = "criterium-du-dauphine/2026"
DAUPHINÉ_RAW_DIR   = DATA / "cache" / "dauphine2026_raw"
PLAYERS_CACHE      = DATA / "cache" / "dauphine2026_players.json"

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
    DAUPHINÉ_RAW_DIR.mkdir(parents=True, exist_ok=True)
    return DAUPHINÉ_RAW_DIR / name


def _load_raw(name: str):
    p = _raw(name)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _save_raw(name: str, data) -> None:
    _raw(name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ── Fetch helpers ───────────────────────────────────────────────────────────────

def fetch_summary(game_id: int, event_id: int) -> dict[int, int]:
    """
    Returns {personId: total_fantasy_points} for a completed stage.
    Tries the /scoring-summary endpoint; falls back to reconstructing from actions.
    """
    # Try summary endpoint
    try:
        resp = _h.HTTP.get(
            f"{_h.BASE}/api/games/{game_id}/events/{event_id}/scoring-summary"
        )
        data = resp.json()
        items = data if isinstance(data, list) else data.get("items", [])
        result: dict[int, int] = {}
        for item in items:
            pid = item.get("personId") or item.get("person_id")
            pts = item.get("score") or item.get("total") or item.get("points") or 0
            if pid is not None:
                result[int(pid)] = int(pts)
        if result:
            return result
    except Exception:
        pass

    # Fall back to per-rule actions → use cached summary_sXX.json if available
    actions_raw = _load_raw(f"actions_e{event_id}.json")
    if not actions_raw:
        actions_raw = _h.HTTP.get(
            f"{_h.BASE}/api/games/{game_id}/events/{event_id}/fantasy-actions"
        ).json().get("items", [])
        _save_raw(f"actions_e{event_id}.json", actions_raw)

    # The summary files from giro already have {personId: net_points}
    # For actions we only have ruleId+amount without point values — skip reconstruction
    return {}


# ── Rider pool ─────────────────────────────────────────────────────────────────

def build_riders(player_by_id: dict, person_by_id: dict) -> list[dict]:
    """
    Build a `riders` list (same format as riders.json) from Holdet API data.
    rider_id is the normalised full name → matches pcs_form.json + cyclingoracle.json keys.
    """
    riders: list[dict] = []
    seen_ids: set[str] = set()

    for pid_str, p in player_by_id.items():
        person_id = p.get("personId")
        if not person_id:
            continue
        person    = person_by_id.get(person_id, {})
        full_name = person.get("fullName", "")
        if not full_name:
            continue

        # Build ID: normalised name (matches existing caches)
        # Also try manual Holdet overrides first
        norm_name = _h._norm(full_name)
        if norm_name in _h.HOLDET_NAME_OVERRIDES:
            rider_id = _h.HOLDET_NAME_OVERRIDES[norm_name]
        else:
            rider_id = _to_rider_id(full_name)

        if rider_id in seen_ids:
            continue
        seen_ids.add(rider_id)

        price_M = round(p.get("price", 0) / 1_000_000, 2)
        tname   = person.get("teamName", "")

        riders.append({
            "id":         rider_id,
            "full_name":  full_name,
            "short_name": full_name.split()[-1],   # last name as short name
            "team":       team_abbrev(tname),
            "team_full":  tname,
            "price":      price_M,
            "holdet_person_id": person_id,
        })

    return sorted(riders, key=lambda r: r["full_name"])


# ── Main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Byg Dauphiné 2026 web-data")
    parser.add_argument("--delay",     type=float, default=1.0)
    parser.add_argument("--no-holdet", action="store_true",
                        help="Brug kun cached spiller-data (ingen nye API-kald)")
    args = parser.parse_args()

    _h.HTTP.delay = args.delay

    print("=" * 60)
    print("  Build Critérium du Dauphiné 2026 Web Data")
    print("=" * 60)

    # ── Fetch or load rider pool ─────────────────────────────────────────────
    if args.no_holdet and PLAYERS_CACHE.exists():
        riders = json.loads(PLAYERS_CACHE.read_text(encoding="utf-8"))
        print(f"  [cache] {len(riders)} ryttere fra dauphine2026_players.json")
    else:
        print("  Henter spillerliste fra Holdet…")
        player_by_id, person_by_id = _h.fetch_player_info(DAUPHINÉ_GAME_ID, DAUPHINÉ_CARTRIDGE)
        riders = build_riders(player_by_id, person_by_id)
        PLAYERS_CACHE.write_text(
            json.dumps(riders, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  {len(riders)} ryttere gemt til cache")

    # Build lookup: holdet_person_id → rider (for actuals)
    pid_to_rider: dict[int, dict] = {
        r["holdet_person_id"]: r for r in riders if r.get("holdet_person_id")
    }

    # ── Fetch schedule ───────────────────────────────────────────────────────
    print("  Henter etapeplan…")
    sched_cache = _load_raw("schedule.json")
    if sched_cache and args.no_holdet:
        events_raw = sched_cache.get("events", [])
        event_info_raw = sched_cache.get("event_info", {})
        events     = events_raw
        event_info = {int(k): v for k, v in event_info_raw.items()}
    else:
        events, event_info = _h.fetch_schedule(DAUPHINÉ_GAME_ID)
        _save_raw("schedule.json", {
            "events":     events,
            "event_info": {str(k): v for k, v in event_info.items()},
        })

    n_stages   = len(events)
    finished   = [eid for eid in events
                  if event_info.get(eid, {}).get("status") == "finished"]
    upcoming   = [eid for eid in events
                  if event_info.get(eid, {}).get("status") != "finished"]
    print(f"  {n_stages} etaper  —  {len(finished)} afsluttede, {len(upcoming)} kommende")

    # ── Load model data ──────────────────────────────────────────────────────
    print("  Indlæser modeldata…")

    def _load_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    co_raw   = _load_json(DATA / "cache" / "cyclingoracle.json")
    co_data  = {name: d.get("ratings", {}) for name, d in co_raw.items()} if co_raw else {}

    pcs_raw  = _load_json(DATA / "cache" / "pcs_form.json")
    pcs_form = {}
    for rid, entry in pcs_raw.items():
        if entry.get("not_found"):
            continue
        if "form_by_type" in entry:
            pcs_form[rid] = entry["form_by_type"]
        else:
            pcs_form[rid] = {"overall": entry.get("form_score", 50.0)}

    pcs_scores = _load_json(DATA / "cache" / "pcs_profile_scores.json")
    stage_scores = pcs_scores.get(DAUPHINÉ_PCS_RACE + "_scores", {})

    pcs_types_cache = _load_json(DATA / "cache" / "pcs_stage_types.json")
    stage_types = pcs_types_cache.get(DAUPHINÉ_PCS_RACE, {})

    print(f"  CyclingOracle: {len(co_data)}  PCS form: {len(pcs_form)}")
    print(f"  PCS stage types: {len(stage_types)} etaper  "
          f"profile scores: {len(stage_scores)} etaper")

    # ── Fetch actuals for completed stages ──────────────────────────────────
    print(f"  Henter actual-point for {len(finished)} afsluttede etaper…")

    # {stage_num: {rider_id: actual_pts}}
    actuals: dict[int, dict[str, int]] = {}

    for stage_num, eid in enumerate(events, start=1):
        if event_info.get(eid, {}).get("status") != "finished":
            continue

        cache_name = f"summary_s{stage_num:02d}.json"
        cached     = _load_raw(cache_name)

        if cached is None and not args.no_holdet:
            # Try /scoring-summary endpoint first
            summary_by_pid = fetch_summary(DAUPHINÉ_GAME_ID, eid)
            if not summary_by_pid:
                # Fall back: fetch actions and build summary from them
                actions = _h.HTTP.get(
                    f"{_h.BASE}/api/games/{DAUPHINÉ_GAME_ID}/events/{eid}/fantasy-actions"
                ).json().get("items", [])
                _save_raw(f"actions_e{eid}.json", actions)

                # We don't reconstruct points from rules here — save empty summary
                # to avoid re-fetching. Actuals will be filled from scoring-summary
                # if/when the endpoint becomes available.
                summary_by_pid = {}

            # Save as {str(personId): points}
            _save_raw(cache_name, {str(k): v for k, v in summary_by_pid.items()})
            cached = {str(k): v for k, v in summary_by_pid.items()}

        if cached:
            stage_actuals: dict[str, int] = {}
            for pid_str, pts in cached.items():
                rider = pid_to_rider.get(int(pid_str))
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
                f"{_h.BASE}/api/games/{DAUPHINÉ_GAME_ID}/events/{last_eid}/fantasy-actions"
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
        top3 = sorted(gc_standings.items(), key=lambda x: x[1])[:3]
        id_to_name = {r["id"]: r["full_name"] for r in riders}
        print(f"  GC top-3: {', '.join(f'{rnk}. {id_to_name.get(rid, rid)}'for rid, rnk in top3)}")

    # ── Run predictions per stage ────────────────────────────────────────────
    print(f"  Kører forudsigelser for {n_stages} etaper…")

    stages_pred: list[dict] = []

    for stage_num, eid in enumerate(events, start=1):
        info   = event_info.get(eid, {})
        status = info.get("status", "upcoming")

        # Stage type: PCS override > Holdet stageType > fallback
        stype = (
            _h.pcs_override_stage_type(DAUPHINÉ_CARTRIDGE, stage_num)
            or _h.HOLDET_STAGE_TYPE_MAP.get(info.get("stageType", ""), "")
            or "hilly"
        )

        profile_score = stage_scores.get(stage_num) or stage_scores.get(str(stage_num))

        print(f"    Etape {stage_num:2d} ({stype}, status={status})…")

        preds = predict_all(
            riders=riders,
            stage_type=stype,
            veloscore_data=None,        # no VeloScore for Dauphiné
            cyclingoracle_data=co_data,
            pcs_form_data=pcs_form,
            current_gc=gc_standings or None,
            current_jerseys=jersey_leaders or None,
            profile_score=profile_score,
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
                "disc_key": p.get("disc_key", "AVG"),
                "signals":  [
                    round(sigs.get("veloscore", 0), 3),
                    round(sigs.get("odds", 0), 3),
                    round(sigs.get("discipline", 0), 3),
                    round(sigs.get("form", 0), 3),
                ],
                "reason":   p.get("reasoning", ""),
                "actual":   actual,
                "in_opt":   rid in best_ids,
                "is_cap":   rid == cap_id,
            })

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
        })

    # ── Write output ─────────────────────────────────────────────────────────
    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "race":      "Critérium du Dauphiné 2026",
        "cartridge": DAUPHINÉ_CARTRIDGE,
        "n_stages":  n_stages,
        "stages":    stages_pred,
    }

    out_path = WEB_DIR / "dauphine2026_predictions.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=None), encoding="utf-8")
    print(f"\n  Gemt: {out_path}  ({n_stages} etaper)")
    print()
    print("  === Klar! ===")
    print("  Push web/data/ til GitHub for at opdatere dashboardet.")
    print("  Ny side: web/dauphine.html")


if __name__ == "__main__":
    main()
