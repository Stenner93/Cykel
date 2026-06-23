"""
Scrape historical Holdet.dk fantasy data for Grand Tours 2025.

Downloads ALL stage fantasy-actions (faktiske Holdet-point per rytter per etape)
for Giro 2025, TdF 2025 og Vuelta 2025 og gemmer dem som træningsdata til
kalibrering af point-forudsigelsesmodellen.

Output:
  data/cache/holdet_historical.json  — per race/stage/rider faktiske point
  data/cache/holdet_historical_meta.json — metadata om data-kvalitet

Brug:
    # Trin 1: Find game IDs for 2025-løbene (kør én gang):
    python scrape_holdet.py --discover

    # Trin 2: Opdater RACE_CONFIGS nedenfor med fundne IDs, derefter:
    python scrape_historical_holdet.py

    # Trin 3 (valgfrit): Kør kun ét løb:
    python scrape_historical_holdet.py --race giro-d-italia-2025

    # Trin 4: Brug dataene til kalibrering:
    python build_training_data.py  (opdateres automatisk med Holdet-data)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT      = Path(__file__).parent
DATA      = ROOT / "data"
CACHE_DIR = DATA / "cache"

# ── Race configuration ──────────────────────────────────────────────────────────
# Opdater game_id'erne efter `python scrape_holdet.py --discover`
# PCS-race bruges til stage-type lookup (skeleton ← pcs_stage_types.json)
RACE_CONFIGS: list[dict] = [
    # ── 2024 ────────────────────────────────────────────────────────────────────
    {
        "cartridge": "tour-de-france-2024",
        "game_id":   442,
        "pcs_race":  "tour-de-france/2024",
        "label":     "Tour de France 2024",
        "n_stages":  21,
    },
    {
        "cartridge": "vuelta-2024",
        "game_id":   441,
        "pcs_race":  "vuelta-a-espana/2024",
        "label":     "Vuelta a España 2024",
        "n_stages":  21,
    },
    # ── 2025 ────────────────────────────────────────────────────────────────────
    {
        "cartridge": "giro-d-italia-2025",
        "game_id":   550,
        "pcs_race":  "giro-d-italia/2025",
        "label":     "Giro d'Italia 2025",
        "n_stages":  21,
    },
    {
        "cartridge": "tour-de-france-2025",
        "game_id":   563,
        "pcs_race":  "tour-de-france/2025",
        "label":     "Tour de France 2025",
        "n_stages":  21,
    },
    {
        "cartridge": "vuelta-2025",
        "game_id":   572,
        "pcs_race":  "vuelta-a-espana/2025",
        "label":     "Vuelta a España 2025",
        "n_stages":  21,
    },
]

# Holdet scoring rule IDs (kopieret fra scrape_holdet.py)
RULE_SPRINT    = 874
RULE_KOM       = 875
RULE_TEAM_BEST = {885: 1, 886: 2, 887: 3}
HOLDET_STAGE_TYPE_MAP = {
    "flat": "sprint", "sprint": "sprint",
    "hilly": "hilly", "cobbled": "cobbled", "mountain": "mountain",
    "tt": "tt", "individual_time_trial": "tt", "team_time_trial": "ttt",
}


def _import_holdet():
    """Lazy-import scrape_holdet to reuse HTTP + helpers."""
    sys.path.insert(0, str(ROOT))
    import scrape_holdet as sh
    return sh


def load_pcs_stage_types() -> dict:
    p = CACHE_DIR / "pcs_stage_types.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def extract_points_from_actions(actions: list[dict]) -> dict[int, int]:
    """
    Sum all Holdet point-rules for each personId in the fantasy-actions list.
    Returns {personId: total_fantasy_pts}.
    """
    totals: dict[int, int] = {}
    for action in actions:
        pid  = action.get("personId")
        amt  = action.get("amount", 0)
        rule = action.get("ruleId")
        if pid is None or rule is None:
            continue
        totals[pid] = totals.get(pid, 0) + int(round(amt))
    return totals


def scrape_race(cfg: dict, sh, pcs_types: dict, out_dir: Path) -> dict:
    """
    Scrape one historical race. Returns {stage_num: {rider_id: pts, ...}}.
    """
    cartridge = cfg["cartridge"]
    game_id   = cfg["game_id"]
    label     = cfg["label"]
    pcs_race  = cfg["pcs_race"]

    print(f"\n{'='*60}")
    print(f"  {label}  (game_id={game_id})")
    print(f"{'='*60}")

    if game_id is None:
        print(f"  [SKIP] game_id ikke sat for {cartridge!r}")
        print(f"  Kør: python scrape_holdet.py --discover  og opdater RACE_CONFIGS")
        return {}

    # Auto-discover if missing
    if game_id is None:
        info = sh.discover_cartridge(cartridge)
        if not info:
            print(f"  [ERROR] Cartridge {cartridge!r} ikke fundet på Holdet.")
            return {}
        game_id = info["game_id"]
        print(f"  Auto-discovered: game_id={game_id}")

    # Fetch schedule
    print("  Henter etapeplan…")
    try:
        events, event_info = sh.fetch_schedule(game_id)
    except Exception as exc:
        print(f"  [ERROR] Kunne ikke hente etapeplan: {exc}")
        return {}

    DONE_STATUSES = {"finished", "closed", "past", "complete", "ended"}
    finished = [eid for eid in events
                if event_info.get(eid, {}).get("status") in DONE_STATUSES]

    # For archived races: if none marked "finished", treat all events as done
    if not finished and events:
        statuses = {event_info.get(eid, {}).get("status", "?") for eid in events[:5]}
        print(f"  [INFO] Ingen etaper med status 'finished' — fundne statuser: {statuses}")
        print(f"  [INFO] Behandler alle {len(events)} etaper som afsluttede (arkiveret løb)")
        finished = list(events)

    print(f"  {len(events)} etaper i alt, {len(finished)} afsluttede")

    # Fetch players (name + id mapping)
    print("  Henter spillerliste…")
    try:
        player_by_id, person_by_id = sh.fetch_player_info(game_id, cartridge)
    except Exception as exc:
        print(f"  [WARN] Spillerliste fejlede: {exc} — bruger tom mapping")
        player_by_id = {}
        person_by_id = {}

    # Build personId → rider_id mapping (best-effort via name matching)
    person_to_rid: dict[int, str] = {}
    for pid, pdata in person_by_id.items():
        full_name = pdata.get("fullName", "")
        # Holdet ID: lowercase, underscore, normalize accents
        import unicodedata
        normed = unicodedata.normalize("NFD", full_name)
        rid = "".join(c for c in normed if unicodedata.category(c) != "Mn")
        rid = rid.lower().replace(" ", "_").replace("-", "_")
        rid = rid.replace("'", "").replace(".", "")
        person_to_rid[pid] = rid

    # Stage type cache (from PCS — distinguishes hilly vs mountain summit)
    pcs_stage_map = pcs_types.get(pcs_race, {})

    # Cache directory for raw actions
    raw_dir = CACHE_DIR / f"{cartridge.replace('-', '_')}_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict] = {}  # stage_num_str → {rider_id: pts}
    stage_meta: dict[str, dict] = {}

    for eid in finished:
        sn       = events.index(eid) + 1
        sn_str   = str(sn)
        evt_data = event_info.get(eid, {})

        # Stage type: prefer PCS override, fall back to Holdet
        holdet_type  = evt_data.get("stageType", "hilly")
        stage_type   = (pcs_stage_map.get(sn) or pcs_stage_map.get(sn_str)
                        or HOLDET_STAGE_TYPE_MAP.get(holdet_type, "hilly"))

        print(f"  Etape {sn:2d}/{len(events)}  (event={eid}, {stage_type})  ", end="", flush=True)

        try:
            acts = sh._fetch_and_cache_actions(game_id, eid, sn, raw_dir)
        except Exception as exc:
            print(f"FEJL: {exc}")
            continue

        pts_by_person = extract_points_from_actions(acts)

        # Map to rider IDs
        stage_pts: dict[str, int] = {}
        for pid, pts in pts_by_person.items():
            rid = person_to_rid.get(pid)
            if rid:
                stage_pts[rid] = pts

        results[sn_str]   = stage_pts
        stage_meta[sn_str] = {
            "stage_type":   stage_type,
            "holdet_type":  holdet_type,
            "event_id":     eid,
            "n_riders":     len(stage_pts),
        }
        print(f"{len(stage_pts)} ryttere med point")
        time.sleep(0.3)

    return {"stages": results, "meta": stage_meta}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape historiske Holdet-data (Giro/TdF/Vuelta 2025) som træningsdata"
    )
    parser.add_argument("--race", default=None,
                        help="Scrape kun ét løb (cartridge slug, fx 'giro-d-italia-2025')")
    parser.add_argument("--out", default=str(CACHE_DIR / "holdet_historical.json"),
                        help="Output JSON-fil (default: data/cache/holdet_historical.json)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Sekunder mellem requests (default: 1.0)")
    args = parser.parse_args()

    sh = _import_holdet()
    sh.HTTP.delay = args.delay

    pcs_types = load_pcs_stage_types()

    configs = RACE_CONFIGS
    if args.race:
        configs = [c for c in RACE_CONFIGS if c["cartridge"] == args.race]
        if not configs:
            print(f"Ukendt race: {args.race!r}")
            print("Kendte races:", [c["cartridge"] for c in RACE_CONFIGS])
            sys.exit(1)

    # Load existing output (for incremental update)
    out_path = Path(args.out)
    out_data: dict = {}
    if out_path.exists():
        out_data = json.loads(out_path.read_text(encoding="utf-8"))
        print(f"Indlæste eksisterende data fra {out_path} ({len(out_data)} løb)")

    for cfg in configs:
        race_data = scrape_race(cfg, sh, pcs_types, out_path.parent)
        if race_data:
            out_data[cfg["cartridge"]] = {
                "label":    cfg["label"],
                "pcs_race": cfg["pcs_race"],
                **race_data,
            }

    out_path.write_text(json.dumps(out_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nGemt til: {out_path}")

    # Summary
    for cartridge, data in out_data.items():
        n_stages = len(data.get("stages", {}))
        n_riders = sum(len(v) for v in data.get("stages", {}).values())
        print(f"  {data.get('label', cartridge)}: {n_stages} etaper, {n_riders} rytter-etaper")

    print("\nNæste trin:")
    print("  python build_training_data.py  — inkorporer i ML-træning")
    print("  (eller brug holdet_historical.json direkte til kalibrering)")


if __name__ == "__main__":
    main()
