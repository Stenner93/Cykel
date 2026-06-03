"""
Build Giro 2026 web data files for the dashboard.

Tab 1 (Pointmatrix): Holdet.dk actual points per rider per stage
Tab 2 (Predictions): Model predictions + actual comparison for VeloScore stages

Usage:
    python build_giro_web_data.py
    python build_giro_web_data.py --delay 0.5       # faster
    python build_giro_web_data.py --no-predictions  # skip model predictions
    python build_giro_web_data.py --no-holdet       # skip API, use cached data

Output:
    web/data/giro2026_scores.json        — points matrix (riders x stages)
    web/data/giro2026_predictions.json   — per-stage model predictions
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Reuse infrastructure from scrape_holdet.py
import scrape_holdet as _h

from src.scoring import STAGE_PTS, GC_PTS, JERSEY, SPT_PER_PT, LATE_MAX, LATE_PER_MIN, DNF_PEN
from src.predictor import predict_all
from src.optimizer import make_best_team

# Ensure UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA    = ROOT / "data"
WEB_DIR = ROOT / "web" / "data"
WEB_DIR.mkdir(parents=True, exist_ok=True)

GIRO_GAME_ID   = 612
GIRO_CARTRIDGE = "giro-d-italia-2026"
VELOSCORE_PATH = DATA / "giro2026" / "veloscore.json"
RAW_CACHE_DIR  = DATA / "cache" / "giro2026_raw"

# ── Rule labels (Danish) ────────────────────────────────────────────────────────
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
    888: "Etapeplacering",         # stage position (all finishers, amt = rank)
    889: "Sprint-point (klassement)",  # parallel to 874, for classification tracking
    890: "KOM-point (klassement)",     # parallel to 875, for classification tracking
    891: "GC-stilling",            # overall GC rank (amt = rank)
    892: "Pointklassement-rang",   # sprint/points classification rank
    893: "Bjergklassement-rang",   # mountain classification rank
    894: "Etapepræmie",            # stage prize / combativity award
    895: "Forsinkelse",
    904: "Særpræmie",              # special prize
    905: "Gruppetto",
    1044: "Angreb",
    1080: "DNF",
}


def _action_pts(rule_id: int, amount: float, has_gc_specific: bool) -> int:
    """Convert a Holdet rule action to fantasy points (best-effort)."""
    # Stage positions 1-15 (each rule = fixed position)
    if 849 <= rule_id <= 863:
        return STAGE_PTS.get(rule_id - 848, 0)
    # GC top-10 specific bonuses
    if 864 <= rule_id <= 873:
        return GC_PTS.get(rule_id - 863, 0)
    # Sprint points: amount = sprint points earned
    if rule_id == 874:
        return int(amount) * SPT_PER_PT
    # KOM points: amount = KOM points earned
    if rule_id == 875:
        return int(amount) * SPT_PER_PT
    # Jersey bonuses
    if rule_id == 876:  return JERSEY["leader"]
    if rule_id == 877:  return JERSEY["sprint"]
    if rule_id == 878:  return JERSEY["mountain"]
    if rule_id == 879:  return JERSEY["youth"]
    # Most aggressive
    if rule_id == 1044: return JERSEY.get("most_aggressive", 50_000)
    # DNF
    if rule_id == 1080: return DNF_PEN
    # Late arrival: amount = minutes late
    if rule_id == 895:
        return max(LATE_MAX, int(amount) * LATE_PER_MIN)
    # GC rank for all riders (amount = rank) — only add if no specific 864-873 fired
    if rule_id == 891 and not has_gc_specific:
        rank = int(amount)
        return GC_PTS.get(rank, 0)
    # Gruppetto: not sure of exact amount format, skip for now
    return 0


# ── Holdet API helpers ──────────────────────────────────────────────────────────

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
        data = resp.json()
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


# ── Raw cache helpers ───────────────────────────────────────────────────────────

def _cache_path(name: str) -> Path:
    RAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return RAW_CACHE_DIR / name


def _load_cache(name: str) -> dict | list | None:
    p = _cache_path(name)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _save_cache(name: str, data) -> None:
    _cache_path(name).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


# ── Main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Byg Giro 2026 web-data")
    parser.add_argument("--delay",           type=float, default=1.0,
                        help="Sekunder mellem requests (default: 1.0)")
    parser.add_argument("--no-predictions",  action="store_true",
                        help="Spring modelforudsigelser over")
    parser.add_argument("--no-holdet",       action="store_true",
                        help="Brug kun cached Holdet-data (ingen nye API-kald)")
    args = parser.parse_args()

    _h.HTTP.delay = args.delay

    print("=" * 60)
    print("  Build Giro 2026 Web Data")
    print("=" * 60)

    # ── Load riders.json ─────────────────────────────────────────────────────
    riders = json.loads((DATA / "riders.json").read_text(encoding="utf-8"))
    lut    = _h.build_rider_lookup(riders)
    id_to_rider = {r["id"]: r for r in riders}
    # Normalised fallback: strip accents from ID to handle mismatches (e.g. narváez ↔ narvaez)
    norm_id_to_rider = {
        _h._norm(r["id"].replace("_", " ")).replace(" ", "_"): r
        for r in riders
    }
    print(f"  Ryttere (riders.json): {len(riders)}")

    # ── Fetch or load player info ──────────────────────────────────────────────
    def _lookup_rider(rid: str) -> dict:
        """Look up rider by ID, with normalised fallback for accent mismatches."""
        r = id_to_rider.get(rid)
        if r:
            return r
        norm = _h._norm(rid.replace("_", " ")).replace(" ", "_")
        return norm_id_to_rider.get(norm, {})

    if args.no_holdet:
        # Load existing holdet_players.json
        hp_path = DATA / "cache" / "holdet_players.json"
        if not hp_path.exists():
            print("  [ERROR] --no-holdet: data/cache/holdet_players.json mangler")
            sys.exit(1)
        holdet_map = json.loads(hp_path.read_text(encoding="utf-8"))
        # Rebuild mappings
        person_to_rid: dict[int, str] = {}
        rid_to_info:   dict[str, dict] = {}
        for rid, h in holdet_map.items():
            pid = h["holdet_person_id"]
            person_to_rid[pid] = rid
            r_data = _lookup_rider(rid)
            rid_to_info[rid] = {
                "name":  r_data.get("full_name", h["holdet_name"]),
                "team":  r_data.get("team", ""),
                "price": round(h["price_M"], 2),
                "pid":   pid,
            }
        print(f"  [cache] {len(rid_to_info)} ryttere fra holdet_players.json")
        player_by_id = {}  # not needed for no-holdet path

    else:
        player_by_id, person_by_id = _h.fetch_player_info(GIRO_GAME_ID, GIRO_CARTRIDGE)

        # Build personId → rider_id mapping
        person_to_rid = {}
        rid_to_info   = {}
        player_id_to_person_id: dict[int, int] = {}

        for pid_str, p in player_by_id.items():
            person_id = p.get("personId")
            if person_id:
                player_id_to_person_id[int(pid_str)] = person_id

        for pid_str, p in player_by_id.items():
            person_id = p.get("personId")
            person    = person_by_id.get(person_id, {})
            full_name = person.get("fullName", "")
            if not full_name:
                continue
            rid = _h.match_name(full_name, lut)
            if rid:
                person_to_rid[person_id] = rid
                r_data = _lookup_rider(rid)
                rid_to_info[rid] = {
                    "name":  r_data.get("full_name", full_name),
                    "team":  r_data.get("team", person.get("teamName", "")),
                    "price": round(p.get("price", 0) / 1_000_000, 2),
                    "pid":   person_id,
                }
        print(f"  Matchet: {len(rid_to_info)}/{len(player_by_id)} spillere")

    # ── Fetch schedule ─────────────────────────────────────────────────────────
    if args.no_holdet:
        schedule_cache = _load_cache("schedule.json")
        if schedule_cache:
            events     = schedule_cache["events"]
            event_info = {int(k): v for k, v in schedule_cache["event_info"].items()}
        else:
            print("  [ERROR] --no-holdet: schedule cache mangler")
            sys.exit(1)
    else:
        print("  Henter etapeplan…")
        events, event_info = _h.fetch_schedule(GIRO_GAME_ID)
        _save_cache("schedule.json", {
            "events":     events,
            "event_info": {str(k): v for k, v in event_info.items()},
        })

    stages_meta: list[dict] = []
    for i, eid in enumerate(events):
        info      = event_info.get(eid, {})
        stage_num = i + 1

        # Priority: PCS cache (distinguishes p4-hilly from p5-mountain) > Holdet
        stype = _h.pcs_override_stage_type(GIRO_CARTRIDGE, stage_num)
        if not stype:
            stype = _h.HOLDET_STAGE_TYPE_MAP.get(info.get("stageType", ""), "hilly")

        stages_meta.append({
            "num":      stage_num,
            "event_id": eid,
            "name":     info.get("name", f"Etape {stage_num}"),
            "type":     stype,
            "status":   info.get("status", "unknown"),
        })

    finished   = [s for s in stages_meta if s["status"] == "finished"]
    print(f"  {len(stages_meta)} etaper total, {len(finished)} afsluttede")

    # ── Fetch per-stage data ───────────────────────────────────────────────────
    all_actions:   dict[int, list]           = {}
    all_summaries: dict[int, dict[int, int]] = {}
    summary_available = False

    for stage in finished:
        snum = stage["num"]
        eid  = stage["event_id"]

        act_cache_name = f"actions_s{snum:02d}.json"
        sum_cache_name = f"summary_s{snum:02d}.json"

        if args.no_holdet:
            cached_acts = _load_cache(act_cache_name)
            cached_sum  = _load_cache(sum_cache_name)
            if cached_acts is None:
                print(f"  [WARN] Etape {snum}: ingen actions-cache — spring over")
                continue
            actions = cached_acts
            summary = {int(k): v for k, v in (cached_sum or {}).items()}
        else:
            print(f"  Etape {snum:2d} (event {eid})…", end=" ", flush=True)
            actions = fetch_stage_actions(GIRO_GAME_ID, eid)
            summary = fetch_scoring_summary(GIRO_GAME_ID, eid)
            # Cache raw results
            _save_cache(act_cache_name, actions)
            _save_cache(sum_cache_name, {str(k): v for k, v in summary.items()})
            n_unique = len({a["personId"] for a in actions})
            print(f"{len(actions)} actions, {n_unique} ryttere, "
                  f"{len(summary)} summary-poster")

        all_actions[snum]   = actions
        all_summaries[snum] = summary
        if summary:
            summary_available = True

    if not summary_available:
        print("  [INFO] scoring-summary ikke tilgængeligt — beregner totaler fra actions")

    # ── Build score matrix ─────────────────────────────────────────────────────
    print("\n  Bygger pointmatrix…")

    # Index actions by (stage_num, person_id) for fast lookup
    act_idx: dict[tuple, list] = {}
    for snum, actions in all_actions.items():
        for a in actions:
            act_idx.setdefault((snum, a["personId"]), []).append(a)

    riders_out: list[dict] = []

    for rid, info in sorted(rid_to_info.items(), key=lambda x: x[1]["name"]):
        pid = info["pid"]
        pts_by_stage:   dict[str, int]  = {}
        rules_by_stage: dict[str, list] = {}

        for stage in stages_meta:
            snum = stage["num"]

            # Total points: prefer authoritative scoring-summary
            if snum in all_summaries and pid in all_summaries[snum]:
                total = all_summaries[snum][pid]
            elif snum in all_actions:
                # Compute from actions as fallback
                rider_acts    = act_idx.get((snum, pid), [])
                has_gc_spec   = any(864 <= a["ruleId"] <= 873 for a in rider_acts)
                total = sum(
                    _action_pts(a["ruleId"], a.get("amount", 1), has_gc_spec)
                    for a in rider_acts
                )
            else:
                total = 0

            if total:
                pts_by_stage[str(snum)] = total

            # Rule breakdown for tooltip
            rider_acts = act_idx.get((snum, pid), [])
            if rider_acts:
                rules_by_stage[str(snum)] = [
                    [a["ruleId"], a.get("amount", 1)]
                    for a in rider_acts
                ]

        riders_out.append({
            "id":    rid,
            "name":  info["name"],
            "team":  info["team"],
            "price": info["price"],
            "pid":   pid,
            "pts":   pts_by_stage,
            "rules": rules_by_stage,
        })

    scores_data = {
        "generated":      datetime.now(timezone.utc).isoformat(),
        "game_id":        GIRO_GAME_ID,
        "cartridge":      GIRO_CARTRIDGE,
        "summary_source": "scoring-summary" if summary_available else "computed",
        "stages":         stages_meta,
        "rule_labels":    {str(k): v for k, v in RULE_LABELS.items()},
        "riders":         riders_out,
    }

    scores_path = WEB_DIR / "giro2026_scores.json"
    scores_path.write_text(
        json.dumps(scores_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  Gemt: {scores_path}  ({len(riders_out)} ryttere)")

    if args.no_predictions:
        print("\n  (--no-predictions: springer forudsigelser over)")
        print("\n  === Klar! ===")
        return

    # ── Actual scores lookup for predictions ──────────────────────────────────
    actual_by_rid_stage: dict[str, dict[int, int]] = {}
    for snum, summary in all_summaries.items():
        for pid, total in summary.items():
            rid = person_to_rid.get(pid)
            if rid and total > 0:
                actual_by_rid_stage.setdefault(rid, {})[snum] = total

    # If no scoring-summary, build from computed totals
    if not summary_available:
        for r in riders_out:
            rid = r["id"]
            for skey, pts in r["pts"].items():
                if pts > 0:
                    actual_by_rid_stage.setdefault(rid, {})[int(skey)] = pts

    # ── Load model inputs ──────────────────────────────────────────────────────
    print("\n  Indlæser modeldata…")

    co_data: dict[str, dict] = {}
    co_path = DATA / "cache" / "cyclingoracle.json"
    if co_path.exists():
        raw = json.loads(co_path.read_text(encoding="utf-8"))
        for name, entry in raw.items():
            co_data[name] = entry.get("ratings", {})

    pcs_form: dict[str, dict] = {}
    pcs_path = DATA / "cache" / "pcs_form.json"
    if pcs_path.exists():
        raw = json.loads(pcs_path.read_text(encoding="utf-8"))
        for rider_id, entry in raw.items():
            if entry.get("not_found"):
                continue
            if "form_by_type" in entry:
                pcs_form[rider_id] = entry["form_by_type"]
            else:
                pcs_form[rider_id] = {"overall": entry.get("form_score", 50.0)}

    gc_standings: dict[str, int] = {}
    gc_path = DATA / "cache" / "gc_standings.json"
    if gc_path.exists():
        gc_standings = json.loads(gc_path.read_text(encoding="utf-8"))

    jerseys: dict[str, list] = {}
    jerseys_path = DATA / "cache" / "jerseys.json"
    if jerseys_path.exists():
        jerseys = json.loads(jerseys_path.read_text(encoding="utf-8"))

    print(f"  CyclingOracle: {len(co_data)}  PCS form: {len(pcs_form)}"
          f"  GC: {len(gc_standings)}  Trøjer: {len(jerseys)}")

    # ── Load VeloScore stages ──────────────────────────────────────────────────
    if not VELOSCORE_PATH.exists():
        print("  [ERROR] VeloScore data ikke fundet:", VELOSCORE_PATH)
        sys.exit(1)

    vs_all = json.loads(VELOSCORE_PATH.read_text(encoding="utf-8"))
    print(f"  VeloScore: {len(vs_all)} etaper")

    # ── Run predictions per stage ─────────────────────────────────────────────
    print(f"  Kører forudsigelser for {len(vs_all)} etaper…")

    stages_pred: list[dict] = []

    for entry in vs_all:
        snum    = entry["stage"]
        stype   = (_h.pcs_override_stage_type(GIRO_CARTRIDGE, snum)
                   or entry.get("stage_type", "hilly"))
        vs_data = entry.get("predictions", [])

        print(f"    Etape {snum:2d} ({stype}, {len(vs_data)} VS-ryttere)…")

        preds = predict_all(
            riders=riders,
            stage_type=stype,
            veloscore_data=vs_data,
            cyclingoracle_data=co_data,
            pcs_form_data=pcs_form,
            current_gc=gc_standings or None,
            current_jerseys=jerseys or None,
        )

        # Optimal team for this stage (unconstrained)
        best     = make_best_team(preds) if preds else None
        team_ids = {r["rider_id"] for r in (best["team"] if best else [])}
        cap_id   = next(
            (r["rider_id"] for r in (best["team"] if best else []) if r.get("is_captain")),
            None,
        )

        riders_pred: list[dict] = []
        for p in preds:
            rid    = p["rider_id"]
            actual = actual_by_rid_stage.get(rid, {}).get(snum)
            d: dict = {
                "id":       rid,
                "name":     p["full_name"],
                "team":     p["team"],
                "price":    p["price"],
                "exp":      p["expected_pts"],
                "var":      p["variance"],
                "form":     p["form_score"],
                "disc":     p["disc_raw"],
                "disc_key": p["disc_key"],
                "signals":  [
                    round(p["signal_scores"]["veloscore"], 3),
                    round(p["signal_scores"]["odds"],      3),
                    round(p["signal_scores"]["discipline"], 3),
                    round(p["signal_scores"]["form"],      3),
                ],
                "reason":   p["reasoning"],
            }
            if actual is not None:
                d["actual"] = actual
            if rid in team_ids:
                d["in_opt"] = True
            if rid == cap_id:
                d["is_cap"] = True
            riders_pred.append(d)

        stages_pred.append({
            "num":   snum,
            "type":  stype,
            "riders": riders_pred,
        })

    preds_data = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "stages":    stages_pred,
    }

    preds_path = WEB_DIR / "giro2026_predictions.json"
    preds_path.write_text(
        json.dumps(preds_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  Gemt: {preds_path}  ({len(stages_pred)} etaper)")
    print("\n  === Klar! ===")
    print("  Push web/data/ til GitHub for at opdatere dashboardet.")
    print("  Ny side: web/giro.html")


if __name__ == "__main__":
    main()
