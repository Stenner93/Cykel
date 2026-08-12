#!/usr/bin/env python3
"""Validerings-probe: bekræft Vuelta-spillet på Holdet før pipelinen wires.

Kører KUN i GitHub Actions (CI kan nå holdet.dk; lokale/agent-miljøer blokeres).
Rapporterer:
  - hvilket cartridge-slug der er live + game-id + liga-id
  - hvor mange ryttere (startliste) der kan hentes
  - hvor stor CO-dækning vi har for de ryttere (fra data/cache/cyclingoracle.json)
Skriver INTET og committer INTET — ren læsning.
"""
import sys, json, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape_holdet as h  # noqa: E402

CANDIDATES = ["vuelta-2026", "vuelta-a-espana-2026", "vueltaspillet-2026", "la-vuelta-2026"]

def norm(s): return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()

def main():
    print("=== 1) Discover cartridge ===")
    found = None
    for slug in CANDIDATES:
        info = h.discover_cartridge(slug)
        print(f"  {slug!r:26} -> {info or 'miss'}")
        if info and info.get("game_id") and found is None:
            found = (slug, info)
    if not found:
        print("\n[RESULTAT] INTET Vuelta-cartridge fundet live endnu.")
        sys.exit(1)

    slug, info = found
    gid = info["game_id"]
    print(f"\n=== 2) Bruger slug={slug!r}  game_id={gid}  league_id={info.get('league_id')} ===")

    try:
        player_by_id, person_by_id = h.fetch_player_info(gid, slug)
    except Exception as exc:
        print(f"  [FEJL] kunne ikke hente spillere: {exc}")
        sys.exit(2)

    names = [p.get("fullName", "") for p in person_by_id.values() if p.get("fullName")]
    teams = sorted({p.get("teamName", "") for p in person_by_id.values() if p.get("teamName")})
    print(f"  ryttere hentet: {len(player_by_id)}   navne: {len(names)}   hold: {len(teams)}")
    print("  eksempel-ryttere:", ", ".join(names[:12]))
    print("  eksempel-hold:", ", ".join(teams[:8]))

    print("\n=== 3) CO-dækning for startlisten ===")
    co_path = ROOT / "data" / "cache" / "cyclingoracle.json"
    co = json.load(open(co_path, encoding="utf-8"))
    co_names = {norm(v.get("name", "")) for v in co.values()}
    covered = [n for n in names if norm(n) in co_names]
    missing = [n for n in names if norm(n) not in co_names]
    pct = 100 * len(covered) / len(names) if names else 0
    print(f"  CO-dækning: {len(covered)}/{len(names)} ({pct:.0f}%)")
    print(f"  mangler CO ({len(missing)}):", ", ".join(missing[:25]))

    print("\n[RESULTAT] OK — Vuelta-spillet er live og kan hentes.")
    print(f"[CONFIG]  DEFAULT_CARTRIDGE = {slug!r}   game_id (auto-discovered) = {gid}")

if __name__ == "__main__":
    main()
