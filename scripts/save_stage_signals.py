"""
Gem pre-stage signaler for TdF 2026 inden hver etape starter.

Formål: Samle træningsdata til fremtidig ML-model ved at gemme
        hvad modellen FORUDSAGDE + hvad der SKETE efterfølgende.

Kør INDEN hver etape (f.eks. morgen på etapedagen):
    python save_stage_signals.py --stage 5

Kør EFTER etapen (for at hente faktiske Holdet-point):
    python save_stage_signals.py --stage 5 --fetch-results

Data gemmes i:
    data/training/tdf2026_stage_{N}_pre.json   — signaler før etapen
    data/training/tdf2026_stage_{N}_post.json  — faktiske point efter

Samlet output (til ML-træning) efter løbet:
    python save_stage_signals.py --compile
    → data/training/tdf2026_training.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA     = ROOT / "data"
TRAIN    = DATA / "training"
PRED_FILE = ROOT / "web" / "data" / "tdf2026_predictions.json"

TRAIN.mkdir(parents=True, exist_ok=True)

CARTRIDGE = "tour-de-france-2026"


def save_pre_stage(stage_num: int) -> None:
    """Gem signaler fra predictions.json for en given etape."""
    with open(PRED_FILE, encoding="utf-8") as f:
        pred = json.load(f)

    stage = next((s for s in pred["stages"] if s["num"] == stage_num), None)
    if stage is None:
        print(f"[FEJL] Etape {stage_num} ikke fundet i {PRED_FILE}")
        sys.exit(1)

    out = {
        "race":       "tour-de-france-2026",
        "stage_num":  stage_num,
        "stage_type": stage["type"],
        "saved_at":   datetime.utcnow().isoformat(),
        "riders": []
    }

    for r in stage["riders"]:
        sigs = r.get("signals") or []
        sigs = (list(sigs) + [None] * 6)[:6]
        out["riders"].append({
            "id":          r["id"],
            "name":        r.get("name", ""),
            "team":        r.get("team", ""),
            "price":       r.get("price"),
            "exp":         r.get("exp"),
            "disc":        r.get("disc"),
            "disc_co":     r.get("disc_co"),
            "signals": {
                "veloscore": sigs[0],
                "odds":      sigs[1],
                "disc":      sigs[2],
                "form":      sigs[3],
                "ml":        sigs[4],
                "pcs_rank":  sigs[5],
            }
        })

    out_path = TRAIN / f"tdf2026_stage_{stage_num:02d}_pre.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Gemt {len(out['riders'])} ryttere → {out_path}")
    print(f"Kør efter etapen: python save_stage_signals.py --stage {stage_num} --fetch-results")


def fetch_post_stage(stage_num: int, game_id: int) -> None:
    """Hent faktiske Holdet-point efter etapen og gem dem."""
    sys.path.insert(0, str(ROOT))
    import scrape_holdet as sh

    pre_path = TRAIN / f"tdf2026_stage_{stage_num:02d}_pre.json"
    if not pre_path.exists():
        print(f"[FEJL] Ingen pre-stage data — kør først: python save_stage_signals.py --stage {stage_num}")
        sys.exit(1)

    raw_dir = DATA / "cache" / "tdf2026_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    print(f"Henter etapeplan for game_id={game_id}…")
    events, event_info = sh.fetch_schedule(game_id)
    if stage_num > len(events):
        print(f"[FEJL] Kun {len(events)} etaper i planen.")
        sys.exit(1)

    eid = events[stage_num - 1]
    status = event_info.get(eid, {}).get("status", "?")
    if status != "finished":
        print(f"[ADVARSEL] Etape {stage_num} har status={status!r} — måske ikke afsluttet endnu?")

    print(f"Henter fantasy-actions for event {eid}…")
    acts = sh._fetch_and_cache_actions(game_id, eid, stage_num, raw_dir)

    pts_by_person: dict[int, int] = {}
    for a in acts:
        pid = a.get("personId")
        amt = a.get("amount", 0)
        if pid is not None:
            pts_by_person[pid] = pts_by_person.get(pid, 0) + int(round(amt))

    # Load player → person mapping
    players = sh.fetch_players_api(game_id)
    person_to_player: dict[int, int] = {
        v.get("personId"): pid for pid, v in players.items()
        if v.get("personId")
    }

    # Also load rider name mapping from statistics page
    _, person_by_id = sh.fetch_player_info(game_id, CARTRIDGE)
    import unicodedata
    def norm_name(full_name: str) -> str:
        n = unicodedata.normalize("NFD", full_name)
        n = "".join(c for c in n if unicodedata.category(c) != "Mn")
        return n.lower().replace(" ", "_").replace("-", "_").replace("'", "").replace(".", "")

    person_to_rid = {pid: norm_name(d.get("fullName", "")) for pid, d in person_by_id.items()}

    # Build rid → actual_pts
    rid_to_pts: dict[str, int] = {}
    for pid, pts in pts_by_person.items():
        rid = person_to_rid.get(pid)
        if rid:
            rid_to_pts[rid] = pts

    out = {
        "race":       "tour-de-france-2026",
        "stage_num":  stage_num,
        "game_id":    game_id,
        "event_id":   eid,
        "fetched_at": datetime.utcnow().isoformat(),
        "pts_by_rider": rid_to_pts,
    }

    out_path = TRAIN / f"tdf2026_stage_{stage_num:02d}_post.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Gemt {len(rid_to_pts)} ryttere med point → {out_path}")


def compile_training_data() -> None:
    """Byg en samlet CSV med alle etaper hvor vi har både pre og post data."""
    rows = []
    for pre_path in sorted(TRAIN.glob("tdf2026_stage_*_pre.json")):
        stage_num = int(pre_path.stem.split("_")[2])
        post_path = TRAIN / f"tdf2026_stage_{stage_num:02d}_post.json"
        if not post_path.exists():
            print(f"  Etape {stage_num}: ingen post-data endnu, springer over")
            continue

        pre  = json.loads(pre_path.read_text(encoding="utf-8"))
        post = json.loads(post_path.read_text(encoding="utf-8"))
        pts  = post["pts_by_rider"]
        stype = pre["stage_type"]

        for r in pre["riders"]:
            rid = r["id"]
            actual_pts = pts.get(rid)
            if actual_pts is None:
                continue
            s = r["signals"]
            rows.append({
                "race":        "tdf2026",
                "stage_num":   stage_num,
                "stage_type":  stype,
                "rider_id":    rid,
                "rider_name":  r["name"],
                "team":        r["team"],
                "price":       r.get("price"),
                "exp":         r.get("exp"),
                "sig_veloscore": s.get("veloscore"),
                "sig_odds":      s.get("odds"),
                "sig_disc":      s.get("disc"),
                "sig_form":      s.get("form"),
                "sig_ml":        s.get("ml"),
                "sig_pcs_rank":  s.get("pcs_rank"),
                "actual_pts":  actual_pts,
            })

    if not rows:
        print("Ingen komplette etaper endnu (kræver både pre og post data).")
        return

    out_path = TRAIN / "tdf2026_training.csv"
    fieldnames = list(rows[0].keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"Gemt {len(rows)} træningsrækker → {out_path}")
    print(f"Etaper med data: {sorted({r['stage_num'] for r in rows})}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gem pre/post signaler for TdF 2026 etaper")
    parser.add_argument("--stage", type=int, help="Etapenummer (1–21)")
    parser.add_argument("--fetch-results", action="store_true",
                        help="Hent faktiske Holdet-point efter etapen")
    parser.add_argument("--game-id", type=int, default=None,
                        help="Holdet game ID for TdF 2026 (kræves til --fetch-results)")
    parser.add_argument("--compile", action="store_true",
                        help="Kompiler alle etaper til training CSV")
    args = parser.parse_args()

    if args.compile:
        compile_training_data()
    elif args.stage and args.fetch_results:
        if not args.game_id:
            print("[FEJL] --game-id kræves med --fetch-results")
            print("Find game ID: python scrape_holdet.py --discover")
            sys.exit(1)
        fetch_post_stage(args.stage, args.game_id)
    elif args.stage:
        save_pre_stage(args.stage)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
