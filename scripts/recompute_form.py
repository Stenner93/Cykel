"""
Re-annotate stage types in cached results and recompute form scores.

Fixes two problems without re-scraping PCS:
  1. Riders whose stage_type was None because the stage-types cache was
     populated AFTER those riders were scraped — their form_by_type was
     computed with all-zero type-specific scores.
  2. All riders benefit from the updated scoring parameters in scrape_pcs.py
     (42-day recency half-life, 140-point win bonus, 25.0 not-found default).

Limitation: only the 15 stored results per rider are available (the full
result set used at original scrape time isn't persisted).  For riders with
few results (e.g. Pogacar: 6) this gives an exact match.  For riders with
many results (e.g. Denz: 38) the top-5 best are very likely in the stored
15 — the error is small and far better than all-zero type scores.

Usage:
    python recompute_form.py            # fix all riders
    python recompute_form.py --dry-run  # show diffs without saving
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent / "scrape"))
import scrape_pcs as sp

ROOT      = Path(__file__).parent.parent
DATA      = ROOT / "data"
CACHE_DIR = DATA / "cache"
FORM_PATH = CACHE_DIR / "pcs_form.json"
ST_PATH   = CACHE_DIR / "pcs_stage_types.json"


def _reannotate(results: list[dict], st_cache: dict) -> int:
    """Fill in stage_type for results where it is currently None."""
    fixed = 0
    for r in results:
        if r.get("stage_type") is not None:
            continue
        rb = r.get("race_base", "")
        sn = r.get("stage_num")
        if not rb or sn is None:
            continue
        stage_map = st_cache.get(rb, {})
        stype = stage_map.get(sn) or stage_map.get(str(sn))
        if stype:
            r["stage_type"] = stype
            fixed += 1
    return fixed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Vis ændringer uden at gemme")
    args = parser.parse_args()

    form_cache = json.loads(FORM_PATH.read_text(encoding="utf-8"))
    st_cache   = json.loads(ST_PATH.read_text(encoding="utf-8"))

    n_annotation_fixed = 0   # riders where at least one stage_type was filled in
    n_score_changed    = 0   # riders where computed scores actually changed
    n_total            = 0

    for rider_id, data in form_cache.items():
        if data.get("not_found"):
            continue

        results = data.get("results", [])
        if not results:
            continue

        n_total += 1
        old_scores = dict(data.get("form_by_type", {}))

        # Step 1 — re-annotate stage types using latest cache
        fixed = _reannotate(results, st_cache)
        if fixed:
            n_annotation_fixed += 1

        # Step 2 — recompute form_by_type with updated scoring params
        new_scores = sp.compute_all_form_scores(results)
        data["form_by_type"] = new_scores
        data["form_score"]   = new_scores["overall"]   # backward-compat key

        if new_scores != old_scores:
            n_score_changed += 1
            if args.dry_run:
                name = data.get("name", rider_id)
                print(f"  {name:<35} annotation_fixes={fixed}")
                for k in ["overall", "mountain", "hilly", "sprint", "tt", "cobbled"]:
                    old_v = old_scores.get(k, 0.0)
                    new_v = new_scores.get(k, 0.0)
                    if abs(new_v - old_v) > 0.1:
                        arrow = "▲" if new_v > old_v else "▼"
                        print(f"      {k:<10} {old_v:>5.1f} → {new_v:>5.1f}  {arrow}")

    print(f"\nResultat:")
    print(f"  Ryttere gennemgået:         {n_total}")
    print(f"  Stage-type annoteringer:    {n_annotation_fixed} ryttere fik nye stage-typer")
    print(f"  Form-scores ændret:         {n_score_changed} ryttere")

    if args.dry_run:
        print("\n  [dry-run] Ingen ændringer gemt.")
        return

    FORM_PATH.write_text(json.dumps(form_cache, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(f"\n  Gemt: {FORM_PATH}")


if __name__ == "__main__":
    main()
