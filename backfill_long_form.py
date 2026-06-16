"""
One-off backfill: add form_long_by_type to existing pcs_form.json entries
that don't have it yet (added after the multi-season form feature shipped).

Usage:
    python backfill_long_form.py
    python backfill_long_form.py --limit 20   # test on a subset first
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
import scrape_pcs as sp

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CACHE_PATH = sp.CACHE_PATH


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=0.5)
    args = parser.parse_args()

    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    todo = [
        rid for rid, e in cache.items()
        if not e.get("not_found") and not e.get("form_long_by_type") and e.get("pcs_url")
    ]
    if args.limit:
        todo = todo[: args.limit]

    print(f"Skal opdateres: {len(todo)} ryttere")
    stage_types_disk = sp._load_stage_types_cache()
    session = requests.Session()

    ok = 0
    fail = 0
    for i, rid in enumerate(todo, 1):
        entry = cache[rid]
        url = entry["pcs_url"]
        try:
            prev_html = sp._fetch_prev_season_html(url, session)
            if prev_html:
                prev_results = sp._parse_results_table(prev_html, entry.get("name", rid))
                prev_results = sp.annotate_stage_types(prev_results, session, stage_types_disk)

                # Current-season results aren't stored in full (only top 15
                # cached) — re-fetch current page too for a complete combined set.
                cur_html, _ = sp._fetch_rider_page(sp._rider_to_pcs_slug({"id": rid, "full_name": entry.get("name", rid)}), session)
                cur_results = sp._parse_results_table(cur_html, entry.get("name", rid)) if cur_html else []
                cur_results = sp.annotate_stage_types(cur_results, session, stage_types_disk)

                combined = cur_results + prev_results
                cache[rid]["form_long_by_type"] = sp.compute_all_form_scores_long(combined)
                ok += 1
            else:
                cache[rid]["form_long_by_type"] = {}
                fail += 1
        except Exception as e:
            print(f"  [ERR] {rid}: {e}")
            cache[rid]["form_long_by_type"] = {}
            fail += 1

        if i % 15 == 0 or i == len(todo):
            print(f"  {i:>4}/{len(todo)}  ok={ok}  fail={fail}  senest: {rid}")
            CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            sp._save_stage_types_cache(stage_types_disk)

        time.sleep(args.delay)

    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    sp._save_stage_types_cache(stage_types_disk)
    print(f"\nFaerdig: {ok} opdateret, {fail} fejlede/ingen data")


if __name__ == "__main__":
    main()
